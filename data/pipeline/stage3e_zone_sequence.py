"""
Stage 3e: Per-trial zone-sequence tracker from coordinate traces.

Produces trial_zone_sequence.parquet (long format, one row per zone
run). For each training trial, run-length encodes the per-frame zone
trace over the whole trial window [t_start, t_end] into the ordered
sequence of zones the subject moved through, with entry/exit/total
times. Zone 0 (unclassified) is kept as an ordinary zone.

This is the raw substrate for later segmented-latency analysis; it
carries no analysis variables.

Output schema — trial_zone_sequence.parquet:
  trial_id         int32   FK to trials
  session_id       int32   FK to sessions
  sequence_number  int16   0-based run index within the trial
  zone             int8    zone ID (0-21)
  t_in_ms          int32   entry time (ms from session start)
  t_out_ms         int32   exit time (next run's t_in; last run = last frame)
  t_total_ms       int32   t_out_ms - t_in_ms

Run:
  python data/pipeline/stage3e_zone_sequence.py
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from corner_maze.common.paths import (
    BUILD_LOG,
    COORDINATES,
    PHASES,
    TRIAL_ZONE_SEQUENCE,
    TRIALS,
)
from corner_maze.features.well_visits import GOAL_LOCATION_TO_ZONE
from corner_maze.features.zone_sequence import build_zone_runs


def main() -> None:
    print("Stage 3e — Per-trial zone-sequence tracker")
    print()

    trials = pd.read_parquet(TRIALS)
    phases = pd.read_parquet(PHASES)
    coords = pd.read_parquet(
        COORDINATES, columns=["session_id", "t_ms", "zone"]
    )

    # Training trials only (goal_location maps to a single well); excludes
    # exposure trials, which also have no "trial" phase.
    training = trials[trials["goal_location"].isin(GOAL_LOCATION_TO_ZONE)].copy()

    # Trial windows from phases (phase == "trial").
    trial_phases = phases[phases["phase"] == "trial"][
        ["session_id", "trial_number", "t_start_ms", "t_end_ms"]
    ]
    training = training.merge(
        trial_phases, on=["session_id", "trial_number"], how="left"
    )
    missing = training["t_start_ms"].isna().sum()
    if missing:
        print(f"  warning: {missing} training trials have no phase window")
        training = training.dropna(subset=["t_start_ms", "t_end_ms"])

    training["t_start_ms"] = training["t_start_ms"].astype("int32")
    training["t_end_ms"] = training["t_end_ms"].astype("int32")

    # Group coords by session for fast per-trial slicing.
    coords = coords.sort_values(["session_id", "t_ms"]).reset_index(drop=True)
    session_slices: dict[int, tuple[np.ndarray, np.ndarray]] = {}
    for sid, grp in coords.groupby("session_id", sort=False):
        session_slices[int(sid)] = (
            grp["t_ms"].to_numpy(),
            grp["zone"].to_numpy(),
        )
    del coords

    run_rows: list[dict] = []
    n_empty = 0

    for row in training.itertuples(index=False):
        sid = int(row.session_id)
        tid = int(row.trial_id)
        t0, t1 = int(row.t_start_ms), int(row.t_end_ms)

        t_arr, z_arr = session_slices.get(sid, (None, None))
        if t_arr is None:
            n_empty += 1
            continue
        lo = np.searchsorted(t_arr, t0, side="left")
        hi = np.searchsorted(t_arr, t1, side="right")
        if hi <= lo:
            n_empty += 1
            continue

        for run in build_zone_runs(t_arr[lo:hi], z_arr[lo:hi]):
            run_rows.append(
                {
                    "trial_id": tid,
                    "session_id": sid,
                    "sequence_number": run.sequence_number,
                    "zone": run.zone,
                    "t_in_ms": run.t_in_ms,
                    "t_out_ms": run.t_out_ms,
                    "t_total_ms": run.t_total_ms,
                }
            )

    runs_df = pd.DataFrame(run_rows).astype(
        {
            "trial_id": "int32",
            "session_id": "int32",
            "sequence_number": "int16",
            "zone": "int8",
            "t_in_ms": "int32",
            "t_out_ms": "int32",
            "t_total_ms": "int32",
        }
    )
    runs_df.to_parquet(
        TRIAL_ZONE_SEQUENCE, engine="pyarrow", index=False, compression="zstd"
    )

    # ── Summary ──────────────────────────────────────────────────────
    n_processed = len(training) - n_empty
    n_rows = len(runs_df)
    runs_per_trial = runs_df.groupby("trial_id").size()

    print(f"  training trials processed: {n_processed}")
    print(f"    trials with no coord window: {n_empty}")
    print(f"  total zone runs: {n_rows}")
    if len(runs_per_trial):
        print(f"  runs per trial: median={int(runs_per_trial.median())}, "
              f"mean={runs_per_trial.mean():.1f}, max={int(runs_per_trial.max())}")
        zero_share = (runs_df["zone"] == 0).mean()
        print(f"  zone-0 runs: {int((runs_df['zone'] == 0).sum())} "
              f"({100 * zero_share:.1f}% of runs)")

    print()
    print(f"{'=' * 60}")
    print("Stage 3e Summary")
    print(f"{'=' * 60}")
    print(f"  trials processed: {n_processed}")
    print(f"  zone runs written: {n_rows}")

    with open(BUILD_LOG, "a") as f:
        f.write(f"\nStage 3e — Zone sequence — {datetime.now().isoformat()}\n")
        f.write(f"{'─' * 60}\n")
        f.write(f"  trials processed: {n_processed}\n")
        f.write(f"  zone runs written: {n_rows}\n")
        f.write(f"  no-coord trials:  {n_empty}\n")
        f.write("\n")

    print("\nDone.")


if __name__ == "__main__":
    main()
