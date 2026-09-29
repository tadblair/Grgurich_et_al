"""second_turn_toward_goal: did the rat's 2nd turn head toward the goal?

Uses zone-trace-derived columns (coord_turn_2, turn_2_goal_side) to
determine whether the rat's 2nd turn was goal-directed, regardless of
which side of the maze it occurred on:

- Goal side (turn_2_goal_side == True): True when coord_turn_2 matches
  correct_route[1].
- Non-goal side (turn_2_goal_side == False): True when coord_turn_2
  is the *mirror* of correct_route[1] — the geometrically goal-directed
  response from the non-goal-side perimeter intersection.

Returns a nullable bool: NA when coord_turn_2, turn_2_goal_side, or
correct_route is missing.
"""

from __future__ import annotations

import pandas as pd


def compute_second_turn_toward_goal(trials: pd.DataFrame) -> pd.Series:
    ct2 = trials["coord_turn_2"].astype("string")
    cr = trials["correct_route"].astype("string")
    gs = trials["turn_2_goal_side"]

    cr1 = cr.str[1]
    valid = ct2.notna() & cr1.notna() & gs.notna()

    second_match = ct2 == cr1
    # Goal side: toward goal = correct 2nd turn
    # Non-goal side: toward goal = mirror of correct 2nd turn (i.e. NOT matching)
    toward_goal = gs == second_match

    return toward_goal.where(valid, other=pd.NA).astype("boolean")
