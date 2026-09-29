"""Approach-to-goal-from-cue feature.

Per-subject classification of whether the rewarded goal lies in the
same hemifield as the visual cue ("toward") or the opposite hemifield
("away"). Dark-trained (PI) subjects have no cue, so the value is
None.

Ported from legacy/scripts/build_pandas_table.py::approach_to_goal_from_cue.
"""

from __future__ import annotations

import pandas as pd

APPROACH_CATEGORIES = ["toward", "away"]

# cue_goal_orientation → approach label. Goals sharing the cue's
# hemifield (N/NE, N/NW) are "toward"; opposite-hemifield goals
# (N/SE, N/SW) are "away".
APPROACH_BY_ORIENTATION: dict[str, str] = {
    "N/NE": "toward",
    "N/NW": "toward",
    "N/SE": "away",
    "N/SW": "away",
}


def compute_approach_to_goal(subjects: pd.DataFrame) -> pd.Categorical:
    """Return a categorical Series of approach labels per subject.

    PI (dark-trained) subjects get NaN since they have no visual cue.
    """
    values: list[str | None] = []
    for _, row in subjects.iterrows():
        if row["training_group"] == "PI":
            values.append(None)
        else:
            values.append(APPROACH_BY_ORIENTATION.get(row["cue_goal_orientation"]))
    return pd.Categorical(values, categories=APPROACH_CATEGORIES)
