"""Post-hoc goal-well visit detection from coordinate traces.

Replicates the live-logging logic in maze-control's main_2c2s.py
(see docs/posthoc_zone_spec.md). Given a trial's per-frame (t_ms, zone)
stream and its reward well, yields an ordered list of well visits —
error visits (with 10 ms dwell + 2 s debounce) and the terminal reward
visit (250 ms dwell).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

GOAL_WELLS = {1, 5, 17, 21}
ZONE_TO_NAME = {1: "SW", 5: "NW", 17: "SE", 21: "NE"}
NAME_TO_ZONE = {v: k for k, v in ZONE_TO_NAME.items()}
GOAL_LOCATION_TO_ZONE = {
    "Southwest": 1,
    "Northwest": 5,
    "Southeast": 17,
    "Northeast": 21,
}

PASS_IN_ERROR_MS = 10
PASS_OUT_ERROR_MS = 2000
PASS_REWARD_MS = 250


@dataclass
class WellVisit:
    visit_idx: int
    well_zone: int
    t_entry_ms: int
    t_exit_ms: int | None
    dwell_ms: int | None
    is_reward: bool


def detect_visits(
    t_ms: np.ndarray, zones: np.ndarray, trigger_zone: int
) -> list[WellVisit]:
    """Detect goal-well visits in one trial.

    Parameters
    ----------
    t_ms     : int array of per-frame timestamps (ms from session start)
    zones    : int array of per-frame zone IDs
    trigger_zone : the rewarded well for this trial (1, 5, 17, or 21)

    Returns a list of WellVisit in temporal order. Trial ends on the
    first reward-well visit that satisfies the dwell threshold.
    """
    if trigger_zone not in GOAL_WELLS:
        raise ValueError(f"trigger_zone {trigger_zone} not a goal well")
    error_zones = GOAL_WELLS - {trigger_zone}

    visits: list[WellVisit] = []
    last_error_register_ts = int(t_ms[0]) if len(t_ms) else 0
    in_error_registered = False
    run_zone: int | None = None
    run_start_ts: int = 0

    def close_last_visit(exit_ts: int) -> None:
        if visits and visits[-1].t_exit_ms is None:
            v = visits[-1]
            v.t_exit_ms = exit_ts
            v.dwell_ms = exit_ts - v.t_entry_ms

    for ts, z in zip(t_ms.tolist(), zones.tolist()):
        if z != run_zone:
            # Zone just changed: close any open visit record
            close_last_visit(ts)
            run_zone = z
            run_start_ts = ts
            if z not in error_zones:
                in_error_registered = False

        dwell = ts - run_start_ts

        if z == trigger_zone and dwell >= PASS_REWARD_MS:
            # Reward visit: record and terminate
            if not visits or visits[-1].t_entry_ms != run_start_ts:
                visits.append(
                    WellVisit(
                        visit_idx=len(visits),
                        well_zone=z,
                        t_entry_ms=run_start_ts,
                        t_exit_ms=ts,
                        dwell_ms=ts - run_start_ts,
                        is_reward=True,
                    )
                )
            else:
                v = visits[-1]
                v.is_reward = True
                v.t_exit_ms = ts
                v.dwell_ms = ts - run_start_ts
            return visits

        if (
            z in error_zones
            and not in_error_registered
            and dwell >= PASS_IN_ERROR_MS
            and (ts - last_error_register_ts) >= PASS_OUT_ERROR_MS
        ):
            visits.append(
                WellVisit(
                    visit_idx=len(visits),
                    well_zone=z,
                    t_entry_ms=run_start_ts,
                    t_exit_ms=None,
                    dwell_ms=None,
                    is_reward=False,
                )
            )
            in_error_registered = True
            last_error_register_ts = ts

    # Trial ended without explicit reward trigger. If the last run is in the
    # trigger zone, the trial ending IS proof that MazeControl detected the
    # dwell — record it as the reward visit regardless of measured duration.
    if run_zone == trigger_zone:
        if visits and visits[-1].t_entry_ms == run_start_ts:
            v = visits[-1]
            v.is_reward = True
            v.t_exit_ms = int(t_ms[-1])
            v.dwell_ms = int(t_ms[-1]) - v.t_entry_ms
        else:
            visits.append(
                WellVisit(
                    visit_idx=len(visits),
                    well_zone=trigger_zone,
                    t_entry_ms=run_start_ts,
                    t_exit_ms=int(t_ms[-1]),
                    dwell_ms=int(t_ms[-1]) - run_start_ts,
                    is_reward=True,
                )
            )
        return visits

    # Trial ended without reaching reward zone; close any trailing open visit
    if len(t_ms):
        close_last_visit(int(t_ms[-1]))
    return visits
