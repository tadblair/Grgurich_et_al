"""Post-hoc goal-well visit detection from coordinate traces.

Replays the live error-counting loop of the maze-control software
(maze-control/Ubuntu/main_2c2s.py, ``run_action_vector``, the
``action_vector[0] == 4`` block) over a trial's stored per-frame
(t_ms, zone) stream, so that the visits it yields are the ones the rig
counted in ``trial.errors`` and listed in ``trial.goal_zones_visited``.

The rig polls the tracker's current zone every ~10 ms of wall-clock time:

* A wrong-well entry registers once the rat has been in the well for
  ``pass_time_in_error_zone`` = 10 ms *and* ``pass_time_out_of_error_zone``
  = 2 s have passed since the rat was last in a registered wrong well.
  The zone updates once per video frame (33 ms at 30 Hz, 67 ms at 15 Hz),
  so a single frame in a wrong well always satisfies the 10 ms criterion:
  here a wrong-well run registers on its first frame.
* While the rat stays in a wrong well it has registered, the rig resets
  its "time out of error zone" clock on every poll, so the 2 s debounce
  runs from the rat's *exit* from that well, not from the registration.
  Here ``last_error_ts`` is advanced on every frame of a registered run.
* The trial ends when the rat has been in the rewarded well for
  ``pass_time_reward_zone`` = 250 ms.

An earlier version of this module measured the debounce from the
registration and required a second frame in the well. Those two departures
from the rig made it disagree with ``trial.errors`` on 5.5 % of training
trials (over-counting a well left and re-entered within 2 s of exit but
not of registration; missing single-frame grazes). With the rules as the
rig has them the reconstruction matches ``trial.errors`` on 99.9 % of
training trials; the remainder are long trials the experimenter paused.
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

PASS_OUT_ERROR_MS = 2000   # rig: pass_time_out_of_error_zone, measured from the exit
PASS_REWARD_MS = 250       # rig: pass_time_reward_zone


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
    # The rig starts its "time out of error zone" clock when the trial loop
    # starts, so the first registration needs 2 s from the trial's first frame.
    last_error_ts = int(t_ms[0]) if len(t_ms) else 0
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

        if z in error_zones:
            if in_error_registered:
                # Rig: time_out_of_error_zone = time.time() on every poll while
                # the rat stays in the registered well, so the debounce runs
                # from the exit.
                last_error_ts = ts
            elif ts - last_error_ts >= PASS_OUT_ERROR_MS:
                # Registers on the first frame of the run (or, if the debounce
                # was still running on entry, on the first frame after it
                # has elapsed while the rat is still in the well, as the rig does).
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
                last_error_ts = ts

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
