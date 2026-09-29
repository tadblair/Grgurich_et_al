"""
Stage 3d: Reconstruct turn choices from trial coordinate traces.

Adds to trials.parquet:
  coord_turn_1 — L/R at 1st choice (center intersection) from coordinates
  coord_turn_2 — L/R at 2nd choice (perimeter intersection) from coordinates
  coord_route  — concatenation of coord_turn_1 + coord_turn_2
  turn_2_goal_side — bool: did the 2nd turn occur at the goal-side intersection?
  goal_side_turn — L/R at the goal-side perimeter, even after backtracking
  second_turn_toward_goal — bool: did the 2nd turn head toward the goal?

Run:
  python data/pipeline/stage3d_turn_trajectory.py
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from corner_maze.common.paths import BUILD_LOG, COORDINATES, PHASES, TRIALS
from corner_maze.features.second_turn_toward_goal import (
    compute_second_turn_toward_goal,
)
from corner_maze.features.turn_trajectory import (
    detect_goal_side_turn,
    detect_turns,
    goal_side_arm,
)


def main() -> None:
    print("Stage 3d — Reconstruct turn trajectories from coordinates")
    print()

    trials = pd.read_parquet(TRIALS)
    phases = pd.read_parquet(PHASES)
    coords = pd.read_parquet(
        COORDINATES, columns=["session_id", "t_ms", "zone"]
    )

    # Training trials only (start_arm != "None")
    training = trials[trials["start_arm"] != "None"].copy()

    # Trial windows from phases
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

    # Group coords by session
    coords = coords.sort_values(["session_id", "t_ms"]).reset_index(drop=True)
    session_slices: dict[int, tuple[np.ndarray, np.ndarray]] = {}
    for sid, grp in coords.groupby("session_id", sort=False):
        session_slices[int(sid)] = (
            grp["t_ms"].to_numpy(),
            grp["zone"].to_numpy(),
        )
    del coords

    turn1_map: dict[int, str | None] = {}
    turn2_map: dict[int, str | None] = {}
    turn2_arm_map: dict[int, int | None] = {}
    gs_turn_map: dict[int, str | None] = {}
    n_empty = 0

    for row in training.itertuples(index=False):
        sid = int(row.session_id)
        tid = int(row.trial_id)
        t0, t1 = int(row.t_start_ms), int(row.t_end_ms)
        start_arm = row.start_arm
        cr = row.correct_route

        t_arr, z_arr = session_slices.get(sid, (None, None))
        if t_arr is None:
            n_empty += 1
            continue

        lo = np.searchsorted(t_arr, t0, side="left")
        hi = np.searchsorted(t_arr, t1, side="right")
        if hi <= lo:
            n_empty += 1
            continue

        trial_zones = z_arr[lo:hi]
        t1_val, t2_val, t2_arm = detect_turns(trial_zones, start_arm)
        turn1_map[tid] = t1_val
        turn2_map[tid] = t2_val
        turn2_arm_map[tid] = t2_arm

        # Goal-side turn: scan full trace for eventual goal-side perimeter entry
        if isinstance(cr, str) and len(cr) >= 1:
            gs_turn_map[tid] = detect_goal_side_turn(trial_zones, start_arm, cr[0])
        else:
            gs_turn_map[tid] = None

    # Add columns to trials
    for col in ["coord_turn_1", "coord_turn_2", "coord_route",
                "turn_2_goal_side", "goal_side_turn",
                # Drop old columns if present from prior version
                "goal_side_turn_correct"]:
        if col in trials.columns:
            trials = trials.drop(columns=col)

    trials["coord_turn_1"] = trials["trial_id"].map(turn1_map)
    trials["coord_turn_2"] = trials["trial_id"].map(turn2_map)

    has_both = trials["coord_turn_1"].notna() & trials["coord_turn_2"].notna()
    trials["coord_route"] = pd.Categorical(
        (trials["coord_turn_1"].astype(str) + trials["coord_turn_2"].astype(str)).where(
            has_both, other=None
        ),
        categories=["LL", "LR", "RL", "RR"],
    )

    # turn_2_goal_side: compare the arm where turn 2 occurred to the goal-side arm
    def _is_goal_side(row):
        tid = row["trial_id"]
        t2_arm = turn2_arm_map.get(tid)
        if t2_arm is None:
            return pd.NA
        cr = row["correct_route"]
        sa = row["start_arm"]
        if not isinstance(cr, str) or len(cr) < 1:
            return pd.NA
        gs_arm = goal_side_arm(sa, cr[0])
        if gs_arm is None:
            return pd.NA
        return t2_arm == gs_arm

    trials["turn_2_goal_side"] = trials.apply(_is_goal_side, axis=1).astype("boolean")
    trials["goal_side_turn"] = trials["trial_id"].map(gs_turn_map)

    if "second_turn_toward_goal" in trials.columns:
        trials = trials.drop(columns="second_turn_toward_goal")
    trials["second_turn_toward_goal"] = compute_second_turn_toward_goal(trials)

    trials.to_parquet(TRIALS, engine="pyarrow", index=False, compression="zstd")

    # ── Summary ──────────────────────────────────────────────────────
    n_training = len(training)
    n_t1 = trials["coord_turn_1"].notna().sum()
    n_t2 = trials["coord_turn_2"].notna().sum()
    n_route = trials["coord_route"].notna().sum()

    print(f"  training trials: {n_training}")
    print(f"    no coord window: {n_empty}")
    print(f"    coord_turn_1 detected: {n_t1}")
    print(f"    coord_turn_2 detected: {n_t2}")
    print(f"    coord_route complete: {n_route}")
    print()

    # Agreement with actual_route
    both_valid = trials["coord_route"].notna() & trials["actual_route"].notna()
    if both_valid.any():
        match = (trials.loc[both_valid, "coord_route"].astype(str)
                 == trials.loc[both_valid, "actual_route"].astype(str))
        n_match = int(match.sum())
        n_both = int(both_valid.sum())
        print(f"  agreement: coord_route == actual_route: "
              f"{n_match} / {n_both} ({100 * n_match / n_both:.1f}%)")

        if n_match < n_both:
            disagree = trials[both_valid & ~match]
            print(f"  disagreements: {n_both - n_match}")
            cols = ["trial_id", "session_id", "start_arm", "actual_route", "coord_route"]
            print(disagree[cols].head(10).to_string())

    print()
    print("  coord_route distribution:")
    print(trials["coord_route"].value_counts(dropna=False).to_string())

    # turn_2_goal_side summary
    t2gs = trials["turn_2_goal_side"]
    n_gs_true = int(t2gs.dropna().sum())
    n_gs_false = int((~t2gs.dropna().astype(bool)).sum()) if t2gs.notna().any() else 0
    n_gs_total = int(t2gs.notna().sum())
    print()
    print(f"  turn_2_goal_side: True={n_gs_true}, False={n_gs_false}, "
          f"NA={len(trials) - n_gs_total}")

    # goal_side_turn summary
    n_gs_turn = int(trials["goal_side_turn"].notna().sum())
    print(f"  goal_side_turn detected: {n_gs_turn}")

    # second_turn_toward_goal summary
    stg = trials["second_turn_toward_goal"]
    n_stg = int(stg.notna().sum())
    n_stg_true = int(stg.dropna().sum())
    print(f"  second_turn_toward_goal: {n_stg_true} / {n_stg} "
          f"({100 * n_stg_true / n_stg:.1f}%)" if n_stg else "")

    print()
    print(f"{'=' * 60}")
    print("Stage 3d Summary")
    print(f"{'=' * 60}")
    print(f"  trials enriched: {len(trials)}")
    print(f"  coord_route detected: {n_route}")
    print(f"  turn_2_goal_side non-null: {n_gs_total}")
    print(f"  goal_side_turn detected: {n_gs_turn}")

    with open(BUILD_LOG, "a") as f:
        f.write(f"\nStage 3d — Turn trajectories — {datetime.now().isoformat()}\n")
        f.write(f"{'─' * 60}\n")
        f.write(f"  training trials: {n_training}\n")
        f.write(f"  coord_turn_1: {n_t1}\n")
        f.write(f"  coord_turn_2: {n_t2}\n")
        f.write(f"  coord_route: {n_route}\n")
        f.write(f"  turn_2_goal_side: True={n_gs_true}, False={n_gs_false}\n")
        f.write(f"  goal_side_turn: {n_gs_turn}\n")
        if both_valid.any():
            f.write(f"  agreement: {n_match}/{n_both} ({100 * n_match / n_both:.1f}%)\n")
        f.write("\n")

    print("\nDone.")


if __name__ == "__main__":
    main()
