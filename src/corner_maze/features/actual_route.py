"""Actual-route feature: two-turn sequence taken by the subject.

Concatenates turn_1 and turn_2 into a two-char string. Returns None
for trials with missing turns (empty strings).
"""

from __future__ import annotations

import pandas as pd

ROUTE_CATEGORIES = ["LL", "LR", "RL", "RR"]


def compute_actual_route(trials: pd.DataFrame) -> pd.Categorical:
    """Return a categorical Series of actual routes (turn_1 + turn_2).

    Rows where either turn is empty get NaN.
    """
    t1 = trials["turn_1"].astype(str)
    t2 = trials["turn_2"].astype(str)
    routes = (t1 + t2).where((t1 != "") & (t2 != ""), other=None)
    return pd.Categorical(routes, categories=ROUTE_CATEGORIES)
