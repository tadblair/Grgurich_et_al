"""Correct-route feature: two-turn sequence for a direct run from
start_arm to goal_location.

Returns one of {"LL", "LR", "RL", "RR"} or None for trials without a
defined route (exposure rows with start_arm="None" / goal="Any").
"""

from __future__ import annotations

import pandas as pd

ROUTE_CATEGORIES = ["LL", "LR", "RL", "RR"]

# (start_arm, goal_location) -> two-turn sequence.
# Ported from legacy/scripts/build_pandas_table.py::correct_route.
CORRECT_ROUTE: dict[tuple[str, str], str] = {
    ("North", "Northeast"): "LL",
    ("North", "Southeast"): "LR",
    ("North", "Southwest"): "RL",
    ("North", "Northwest"): "RR",
    ("East",  "Northeast"): "RR",
    ("East",  "Southeast"): "LL",
    ("East",  "Southwest"): "LR",
    ("East",  "Northwest"): "RL",
    ("South", "Northeast"): "RL",
    ("South", "Southeast"): "RR",
    ("South", "Southwest"): "LL",
    ("South", "Northwest"): "LR",
    ("West",  "Northeast"): "LR",
    ("West",  "Southeast"): "RL",
    ("West",  "Southwest"): "RR",
    ("West",  "Northwest"): "LL",
}


def compute_correct_route(trials: pd.DataFrame) -> pd.Categorical:
    """Return a categorical Series of correct routes for each trial.

    Rows where start_arm is "None" or goal_location is "Any" (exposure
    trials) get NaN.
    """
    keys = list(zip(trials["start_arm"], trials["goal_location"]))
    values = [CORRECT_ROUTE.get(k) for k in keys]
    return pd.Categorical(values, categories=ROUTE_CATEGORIES)
