"""
Stage 3c: Detect goal-well visits per trial from coordinate traces.

Produces trial_well_visits.parquet (long format, one row per visit).
On trials.parquet:
  - overwrites goal_zones_visited with the reconstructed zone-ID list
    string (e.g. "[21, 5, 1]")
  - adds first_choice_latency_s (float32): seconds from trial start to
    first well visit; NaN if no visit was registered.

Only runs on training trials (goal_location != "Any"). Exposure trials
are handled separately by Stage 2e.

Output schema — trial_well_visits.parquet:
  trial_id     int32   FK to trials
  session_id   int32   FK to sessions
  visit_idx    int16   0-based visit index within trial
  well_zone    int8    zone ID (1, 5, 17, or 21)
  well_name    string  "SW", "NW", "SE", "NE"
  t_entry_ms   int32   first frame in the run that registered
  t_exit_ms    int32   frame the run ended (or trial end)
  dwell_ms     int32   t_exit_ms - t_entry_ms
  is_reward    bool    True for the terminal reward visit

Run:
  python data/pipeline/stage3c_well_visits.py
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from corner_maze.common.paths import (
    BUILD_LOG,
    COORDINATES,
    PHASES,
    TRIAL_WELL_VISITS,
    TRIALS,
)
from corner_maze.features.well_visits import (
    GOAL_LOCATION_TO_ZONE,
    ZONE_TO_NAME,
    detect_visits,
)


def main() -> None:
    print("Stage 3c — Detect goal-well visits per trial")
    print()

    trials = pd.read_parquet(TRIALS)
    phases = pd.read_parquet(PHASES)
    coords = pd.read_parquet(
        COORDINATES, columns=["session_id", "t_ms", "zone"]
    )

    # Training trials only (goal_location maps to a single well).
    training = trials[trials["goal_location"].isin(GOAL_LOCATION_TO_ZONE)].copy()
    training["trigger_zone"] = training["goal_location"].map(
        GOAL_LOCATION_TO_ZONE
    )

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

    visit_rows: list[dict] = []
    reconstructed: dict[int, str] = {}
    first_choice_latency: dict[int, float] = {}
    n_empty = 0
    n_no_reward = 0

    for row in training.itertuples(index=False):
        sid = int(row.session_id)
        tid = int(row.trial_id)
        trigger = int(row.trigger_zone)
        t0, t1 = int(row.t_start_ms), int(row.t_end_ms)

        t_arr, z_arr = session_slices.get(sid, (None, None))
        if t_arr is None:
            n_empty += 1
            reconstructed[tid] = "[]"
            continue
        lo = np.searchsorted(t_arr, t0, side="left")
        hi = np.searchsorted(t_arr, t1, side="right")
        if hi <= lo:
            n_empty += 1
            reconstructed[tid] = "[]"
            continue

        visits = detect_visits(t_arr[lo:hi], z_arr[lo:hi], trigger)
        if not visits or not visits[-1].is_reward:
            n_no_reward += 1

        seq = [v.well_zone for v in visits]
        reconstructed[tid] = "[" + ", ".join(str(z) for z in seq) + "]"
        if visits:
            first_choice_latency[tid] = (visits[0].t_entry_ms - t0) / 1000.0

        for v in visits:
            visit_rows.append(
                {
                    "trial_id": tid,
                    "session_id": sid,
                    "visit_idx": v.visit_idx,
                    "well_zone": v.well_zone,
                    "well_name": ZONE_TO_NAME[v.well_zone],
                    "t_entry_ms": v.t_entry_ms,
                    "t_exit_ms": v.t_exit_ms if v.t_exit_ms is not None else t1,
                    "dwell_ms": (
                        v.dwell_ms
                        if v.dwell_ms is not None
                        else t1 - v.t_entry_ms
                    ),
                    "is_reward": v.is_reward,
                }
            )

    visits_df = pd.DataFrame(visit_rows).astype(
        {
            "trial_id": "int32",
            "session_id": "int32",
            "visit_idx": "int16",
            "well_zone": "int8",
            "t_entry_ms": "int32",
            "t_exit_ms": "int32",
            "dwell_ms": "int32",
            "is_reward": "bool",
        }
    )
    visits_df.to_parquet(
        TRIAL_WELL_VISITS, engine="pyarrow", index=False, compression="zstd"
    )

    # Overwrite goal_zones_visited and add first_choice_latency_s on trials.
    trials["goal_zones_visited"] = trials["trial_id"].map(reconstructed).fillna(
        trials["goal_zones_visited"]
    )
    if "first_choice_latency_s" in trials.columns:
        trials = trials.drop(columns="first_choice_latency_s")
    trials["first_choice_latency_s"] = (
        trials["trial_id"].map(first_choice_latency).astype("float32")
    )
    trials.to_parquet(TRIALS, engine="pyarrow", index=False, compression="zstd")

    # ── Summary ──────────────────────────────────────────────────────
    n_trials = len(training)
    n_visits = len(visits_df)
    n_reward = int(visits_df["is_reward"].sum())

    print(f"  training trials processed: {n_trials}")
    print(f"    trials with no coord window: {n_empty}")
    print(f"    trials without reward visit: {n_no_reward}")
    print(f"  total visits: {n_visits}")
    print(f"    reward visits: {n_reward}")
    print(f"    error visits:  {n_visits - n_reward}")
    print()
    print("  visits per well:")
    print(visits_df["well_name"].value_counts().to_string())
    print()
    lat = trials["first_choice_latency_s"].dropna()
    print(f"  first_choice_latency_s: n={len(lat)}, "
          f"median={lat.median():.2f}s, mean={lat.mean():.2f}s, "
          f"p95={lat.quantile(0.95):.2f}s")

    print()
    print(f"{'=' * 60}")
    print("Stage 3c Summary")
    print(f"{'=' * 60}")
    print(f"  trials processed: {n_trials}")
    print(f"  visits written:   {n_visits}")

    with open(BUILD_LOG, "a") as f:
        f.write(f"\nStage 3c — Well visits — {datetime.now().isoformat()}\n")
        f.write(f"{'─' * 60}\n")
        f.write(f"  trials processed: {n_trials}\n")
        f.write(f"  visits written:   {n_visits}\n")
        f.write(f"  reward visits:    {n_reward}\n")
        f.write(f"  error visits:     {n_visits - n_reward}\n")
        f.write(f"  no-coord trials:  {n_empty}\n")
        f.write(f"  no-reward trials: {n_no_reward}\n")
        f.write("\n")

    print("\nDone.")


if __name__ == "__main__":
    main()
