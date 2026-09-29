"""
Stage 3b: Add route-related features to trials.parquet.

Features added:
  correct_route — two-turn sequence (LL/LR/RL/RR) for a direct run
                  from start_arm to goal_location. NaN for exposure
                  trials (no defined start_arm/goal).
  actual_route  — two-turn sequence actually taken (turn_1 + turn_2).
                  The rig-logged turn_1/turn_2 columns are kept. NaN
                  when either turn is missing.
  second_turn_toward_goal
                — bool. Did the rat's 2nd turn head toward the goal
                  corner (goal-side or mirrored non-goal-side)?
                  NaN when either route is missing. See
                  features/second_turn_toward_goal.py.

Run:
  python data/pipeline/stage3b_trial_routes.py
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from corner_maze.common.paths import BUILD_LOG, SESSIONS, SUBJECTS, TRIALS
from corner_maze.features.actual_route import compute_actual_route
from corner_maze.features.correct_route import compute_correct_route


def validate_twist_constant_first_turn(
    trials: pd.DataFrame, sessions: pd.DataFrame, subjects: pd.DataFrame
) -> dict[str, str]:
    """For Fixed Cue 1 Twist sessions, the 1st turn of correct_route
    must be constant per subject. Returns {subject_name: turn}."""
    twist = sessions[sessions["session_type"] == "Fixed Cue 1 Twist"]
    if twist.empty:
        return {}
    t = trials.merge(twist[["session_id", "subject_id"]], on="session_id")
    t = t.merge(subjects[["subject_id", "name"]], on="subject_id")
    t = t[t["correct_route"].notna()]
    t["first_turn"] = t["correct_route"].astype(str).str[0]

    result: dict[str, str] = {}
    for name, grp in t.groupby("name"):
        turns = set(grp["first_turn"])
        if len(turns) > 1:
            raise ValueError(
                f"Twist subject {name}: non-constant first turn {turns}"
            )
        result[name] = next(iter(turns))
    return result


def main() -> None:
    print("Stage 3b — Add route features to trials.parquet")
    print()

    trials = pd.read_parquet(TRIALS)
    sessions = pd.read_parquet(SESSIONS)
    subjects = pd.read_parquet(SUBJECTS)

    # correct_route and actual_route are recomputed every run; the rig's
    # turn_1/turn_2 stay on the table (rig-logged, like `errors`).
    if "correct_route" in trials.columns:
        trials = trials.drop(columns="correct_route")
    trials["correct_route"] = compute_correct_route(trials)

    if "turn_1" in trials.columns:
        if "actual_route" in trials.columns:
            trials = trials.drop(columns="actual_route")
        trials["actual_route"] = compute_actual_route(trials)

    # second_turn_toward_goal is computed in stage3d (needs coord_turn_2 + turn_2_goal_side)
    if "second_turn_toward_goal" in trials.columns:
        trials = trials.drop(columns="second_turn_toward_goal")

    # Validation: every training trial (start_arm != "None") must map.
    training_mask = trials["start_arm"] != "None"
    missing = trials[training_mask & trials["correct_route"].isna()]
    if not missing.empty:
        bad = missing[["start_arm", "goal_location"]].drop_duplicates()
        raise ValueError(
            f"Unmapped (start_arm, goal_location) pairs:\n{bad.to_string()}"
        )

    twist_first_turns = validate_twist_constant_first_turn(
        trials, sessions, subjects
    )

    trials.to_parquet(TRIALS, engine="pyarrow", index=False, compression="zstd")

    # ── Summary ──────────────────────────────────────────────────────
    n_total = len(trials)
    n_routed = trials["correct_route"].notna().sum()
    n_null = n_total - n_routed

    print(f"  trials: {n_total}")
    print(f"    with correct_route: {n_routed}")
    print(f"    null (exposure):    {n_null}")
    print()
    print("  correct_route distribution:")
    print(trials["correct_route"].value_counts(dropna=False).to_string())
    print()
    print("  actual_route distribution:")
    print(trials["actual_route"].value_counts(dropna=False).to_string())
    print()
    n_match = (
        (trials["actual_route"] == trials["correct_route"])
        & trials["correct_route"].notna()
    ).sum()
    print(f"  actual == correct: {n_match} / {n_routed} training trials "
          f"({100 * n_match / n_routed:.1f}%)")
    print()
    print(f"  Twist subjects validated: {len(twist_first_turns)}")
    for name, turn in sorted(twist_first_turns.items()):
        print(f"    {name}: first turn always {turn}")

    print()
    print(f"{'=' * 60}")
    print("Stage 3b Summary")
    print(f"{'=' * 60}")
    print(f"  trials enriched: {n_total}")

    with open(BUILD_LOG, "a") as f:
        f.write(f"\nStage 3b — Trial routes — {datetime.now().isoformat()}\n")
        f.write(f"{'─' * 60}\n")
        f.write(f"  trials: {n_total}\n")
        f.write(f"  correct_route non-null: {n_routed}\n")
        f.write(f"  correct_route null (exposure): {n_null}\n")
        f.write(f"  twist subjects validated: {len(twist_first_turns)}\n")
        for k, v in trials["correct_route"].value_counts(dropna=False).items():
            f.write(f"    {k}: {v}\n")
        f.write("\n")

    print("\nDone.")


if __name__ == "__main__":
    main()
