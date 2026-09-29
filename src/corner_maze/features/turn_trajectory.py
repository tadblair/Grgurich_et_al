"""Reconstruct turn choices from per-trial zone sequences.

Detects the 1st turn (at center intersection, zone 11) and 2nd turn
(at a perimeter intersection) by walking the coordinate zone trace.
See docs/maze_behavior_spec.md for full turn detection rules.
"""

from __future__ import annotations

import numpy as np

START_ARM_TO_ZONE = {"North": 12, "South": 10, "East": 15, "West": 7}

# Arms: {inner_zone: (inner, outer)}
ARM_ZONES = {
    12: (12, 13),  # North
    10: (10, 9),   # South
    15: (15, 19),  # East
    7:  (7, 3),    # West
}

# 1st turn targets: start_arm_inner -> {target_inner: "L"/"R"}
TURN1_TARGETS: dict[int, dict[int, str]] = {
    12: {7: "R", 15: "L"},   # North start
    10: {15: "R", 7: "L"},   # South start
    15: {12: "R", 10: "L"},  # East start
    7:  {10: "R", 12: "L"},  # West start
}

# 2nd turn targets: traversed_arm_inner -> {perimeter_zone: "L"/"R"}
TURN2_TARGETS: dict[int, dict[int, str]] = {
    12: {16: "R", 8: "L"},   # traversing North arm
    10: {6: "R", 14: "L"},   # traversing South arm
    15: {18: "R", 20: "L"},  # traversing East arm
    7:  {4: "R", 2: "L"},    # traversing West arm
}


def detect_turns(
    zones: np.ndarray, start_arm: str
) -> tuple[str | None, str | None, int | None]:
    """Detect 1st and 2nd turns from a trial zone sequence.

    Parameters
    ----------
    zones : int array of per-frame zone IDs for one trial
    start_arm : "North", "South", "East", or "West"

    Returns (turn_1, turn_2, turn_2_arm):
      turn_1, turn_2 : "L", "R", or None if not detected
      turn_2_arm : inner zone of the arm where the 2nd turn occurred,
                   or None if not detected
    """
    start_zone = START_ARM_TO_ZONE.get(start_arm)
    if start_zone is None:
        return None, None, None

    t1_targets = TURN1_TARGETS[start_zone]
    turn_1 = None
    turn_1_arm: int | None = None  # inner zone of the arm entered at turn 1

    # Walk zone sequence, tracking zone transitions
    prev_zone = None
    for z in zones.tolist():
        if z == prev_zone or z == 0:
            prev_zone = z if z != 0 else prev_zone
            continue

        if turn_1 is None:
            # Looking for 1st turn: entering a perpendicular inner arm zone
            if z in t1_targets:
                turn_1 = t1_targets[z]
                turn_1_arm = z
                prev_zone = z
                continue
        else:
            # Looking for 2nd turn: entering a perimeter segment zone
            t2_targets = TURN2_TARGETS[turn_1_arm]
            if z in t2_targets:
                turn_2 = t2_targets[z]
                return turn_1, turn_2, turn_1_arm

        prev_zone = z

    return turn_1, None, None


def goal_side_arm(start_arm: str, correct_first_turn: str) -> int | None:
    """Return the inner zone of the arm the rat should enter at choice 1.

    This is the arm on the goal side of the maze for this trial.
    """
    start_zone = START_ARM_TO_ZONE.get(start_arm)
    if start_zone is None:
        return None
    targets = TURN1_TARGETS[start_zone]
    for zone, direction in targets.items():
        if direction == correct_first_turn:
            return zone
    return None


def detect_goal_side_turn(
    zones: np.ndarray,
    start_arm: str,
    correct_first_turn: str,
) -> str | None:
    """Detect the turn the rat made at the goal-side perimeter intersection.

    Scans the entire zone trace for the first time the rat reaches the
    goal-side perimeter — even after wrong turns and backtracking. This
    captures the goal-side choice regardless of whether it was the 2nd
    turn or a later one.

    Returns "L", "R", or None if the goal-side perimeter was never reached.
    """
    gs_arm = goal_side_arm(start_arm, correct_first_turn)
    if gs_arm is None:
        return None

    t2_targets = TURN2_TARGETS[gs_arm]
    reached_goal_arm = False
    prev_zone = None

    for z in zones.tolist():
        if z == prev_zone or z == 0:
            prev_zone = z if z != 0 else prev_zone
            continue

        if z == gs_arm:
            reached_goal_arm = True
        elif reached_goal_arm and z in t2_targets:
            return t2_targets[z]

        prev_zone = z

    return None
