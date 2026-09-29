"""Per-trial zone-sequence tracker.

Run-length encodes a trial's per-frame zone trace into an ordered list
of zone runs — one entry per contiguous stay in a single zone, in the
order the subject moved through them. Zone 0 (unclassified) is kept as
an ordinary zone; nothing is dropped, merged, or thresholded.

This is the raw substrate for later segmented-latency analysis. It
carries no analysis labels (no roles, no directness) — only the
sequence and the times.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class ZoneRun:
    sequence_number: int  # 0-based run index within the trial
    zone: int             # zone id (0-21)
    t_in_ms: int          # entry timestamp (ms from session start)
    t_out_ms: int         # exit timestamp (= next run's t_in; last run = last frame)
    t_total_ms: int       # time in zone = t_out_ms - t_in_ms


def build_zone_runs(t_ms: np.ndarray, zones: np.ndarray) -> list[ZoneRun]:
    """Run-length encode a trial's (t_ms, zone) trace.

    A new run starts whenever the zone value changes. Each run's
    ``t_out_ms`` is the entry time of the next run (so consecutive runs
    are contiguous); the final run closes at the last frame's timestamp.

    Parameters
    ----------
    t_ms : int array of per-frame timestamps (ms), non-decreasing
    zones : int array of per-frame zone IDs (0-21), same length as t_ms
    """
    n = len(zones)
    if n == 0:
        return []

    zones = np.asarray(zones)
    t_ms = np.asarray(t_ms)

    # Indices where a new run starts (zone differs from previous frame).
    change = np.flatnonzero(zones[1:] != zones[:-1]) + 1
    starts = np.concatenate(([0], change))
    # t_out for run k = t_in of run k+1; last run closes at the final frame.
    next_starts = np.concatenate((starts[1:], [n - 1]))

    run_zones = zones[starts]
    t_in = t_ms[starts]
    t_out = t_ms[next_starts]

    return [
        ZoneRun(
            sequence_number=k,
            zone=int(run_zones[k]),
            t_in_ms=int(t_in[k]),
            t_out_ms=int(t_out[k]),
            t_total_ms=int(t_out[k]) - int(t_in[k]),
        )
        for k in range(len(starts))
    ]
