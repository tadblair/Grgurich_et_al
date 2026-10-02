"""
Stage 2c: Build phases.parquet from session_event in the cleaned DB.

Reads Pretrial, Trial Start, Trial End, and ITI events, applies the
phase boundary formulas with named offset constants, and writes one
row per phase span.

Stitched sessions (recorded in two parts; see session_segments): Stage 1
re-keys the second recording's events to the merged session_id but leaves
them on that recording's own clock, while Stage 2b shifted its coordinates
by max(t_ms of the first recording) + one sample interval. The same shift
is applied here to every event from the second 'Presession' marker on, so
the windows land on the right frames.

Inputs:
  data/processed/MazeControl-clean.db  (session_event table)
  data/processed/coordinates.parquet   (for merged-session seam locations)

Outputs:
  data/processed/phases.parquet
  data/processed/build_log.txt  (appended)

Run:
  python data/pipeline/stage2c_phases.py
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from corner_maze.common.paths import BUILD_LOG, CLEAN_DB, COORDINATES, PHASES, PROCESSED_DIR
from corner_maze.features.well_visits import GOAL_LOCATION_TO_ZONE

# Phase boundary offset constants (see plan §7.7, Appendix B)
PRETRIAL_LOOKBACK_MS = 10_000   # 10 s pre-dwell context window
GOAL_REACH_OFFSET_MS = 1_250    # 1.0 + 0.25 s pipeline delays (fallback)
REWARD_DWELL_MS = 250           # 250 ms dwell required for reward trigger

# Event types we use for phase construction
KNOWN_EVENTS = {"Pretrial", "Trial Start", "Trial End", "ITI"}

# One sample interval per tracking rate, as stage2b_merge.stitch_segments uses it
SAMPLE_INTERVAL_MS = {30: 33, 15: 67, 10: 100}

PHASE_SCHEMA = pa.schema([
    ("session_id", pa.int32()),
    ("phase", pa.dictionary(pa.int8(), pa.string())),
    ("phase_idx", pa.int16()),
    ("trial_number", pa.int16()),
    ("t_start_ms", pa.int32()),
    ("t_end_ms", pa.int32()),
    ("duration_ms", pa.int32()),
    ("crosses_seam", pa.bool_()),
])


def find_reward_entry_ms(
    t_ms: np.ndarray,
    zones: np.ndarray,
    trigger_zone: int,
    db_trial_end_t: int,
    dwell_ms: int = REWARD_DWELL_MS,
    search_window_ms: int = 2000,
    forward_buffer_ms: int = 2000,
) -> int | None:
    """Find when the rat entered the reward zone for its terminal dwell.

    Searches the window [db_trial_end_t - search_window_ms,
    db_trial_end_t + forward_buffer_ms] for the last contiguous run of
    zone == trigger_zone lasting >= dwell_ms. Returns the t_ms of the
    first frame in that run, or None if not found.

    The forward extension is necessary because MazeControl's hardware
    Trial End event fires ~1 s *after* the reward-block delivery, so the
    rat's actual reward-zone dwell can start before db_trial_end_t and
    continue past it. Without the forward buffer, the run gets clipped
    at db_trial_end_t and may fall below dwell_ms, causing the function
    to return None and forcing a phases.parquet fallback that's offset
    too early.
    """
    lo = np.searchsorted(t_ms, db_trial_end_t - search_window_ms, side="left")
    hi = np.searchsorted(t_ms, db_trial_end_t + forward_buffer_ms, side="right")
    if hi <= lo:
        return None

    t_win = t_ms[lo:hi]
    z_win = zones[lo:hi]
    in_zone = z_win == trigger_zone

    # Walk backward through frames to find the last qualifying run
    i = len(t_win) - 1
    while i >= 0:
        if not in_zone[i]:
            i -= 1
            continue
        # Found end of a run in trigger zone; walk backward to find start
        run_end_idx = i
        while i >= 0 and in_zone[i]:
            i -= 1
        run_start_idx = i + 1
        run_duration = int(t_win[run_end_idx]) - int(t_win[run_start_idx])
        if run_duration >= dwell_ms:
            return int(t_win[run_start_idx])
    return None


def get_seam_timestamps() -> dict[int, int]:
    """Get the seam timestamp for each merged session from coordinates.parquet.

    The seam is where source_segment changes from 0 to 1. Returns
    {session_id: seam_t_ms} for merged sessions only.
    """
    import duckdb
    con = duckdb.connect()
    # Find the first row where source_segment=1 for each session that has one
    result = con.execute(f"""
        SELECT session_id, MIN(t_ms) AS seam_t_ms
        FROM '{COORDINATES}'
        WHERE source_segment = 1
        GROUP BY session_id
    """).fetchall()
    con.close()
    return {row[0]: row[1] for row in result}


def get_segment_offsets() -> dict[int, int]:
    """The shift Stage 2b added to each stitched session's second recording.

    stage2b_merge.stitch_segments puts the second recording's frames at
    time_stamp + max(t_ms of the first recording) + one sample interval. The
    rate is recovered from the first recording's median frame interval, which
    equals the stored sample rate for every stitched session.
    Returns {session_id: offset_ms} for stitched sessions only.
    """
    import duckdb
    con = duckdb.connect()
    rows = con.execute(f"""
        WITH seg0 AS (
            SELECT session_id, t_ms,
                   t_ms - LAG(t_ms) OVER (PARTITION BY session_id ORDER BY t_ms) AS dt
            FROM '{COORDINATES}'
            WHERE source_segment = 0
              AND session_id IN (SELECT DISTINCT session_id FROM '{COORDINATES}'
                                 WHERE source_segment = 1)
        )
        SELECT session_id, MAX(t_ms) AS max0, MEDIAN(dt) AS median_dt
        FROM seg0 GROUP BY session_id
    """).fetchall()
    con.close()
    offsets = {}
    for sid, max0, median_dt in rows:
        rate_hz = int(round(1000 / float(median_dt)))
        offsets[int(sid)] = int(max0) + SAMPLE_INTERVAL_MS.get(rate_hz, 33)
    return offsets


def shift_second_recording_events(
    events: pd.DataFrame, offsets: dict[int, int]
) -> tuple[pd.DataFrame, dict[int, int]]:
    """Add each stitched session's offset to the events of its second recording.

    `events` must hold every event type in session_event_id order. The rig
    writes one 'Presession' event at the start of each recording, so within a
    stitched session the second 'Presession' row is where the second
    recording's events begin. A stitched session without exactly two such
    rows is left unshifted with a warning.
    Returns (shifted events, {session_id: n_events_shifted}).
    """
    events = events.copy()
    shifted: dict[int, int] = {}
    for sid, offset in sorted(offsets.items()):
        in_session = events["session_id"] == sid
        is_presession = events.loc[in_session, "action_vector_type"] == "Presession"
        if int(is_presession.sum()) != 2:
            print(f"  WARNING: session {sid} is stitched but has {int(is_presession.sum())} "
                  f"Presession events; its second recording's events were not shifted")
            continue
        second = is_presession.cumsum() >= 2
        idx = second[second].index
        events.loc[idx, "time_stamp"] = events.loc[idx, "time_stamp"] + offset
        shifted[sid] = len(idx)
    return events, shifted


def build_phases_for_session(
    events: pd.DataFrame,
    session_id: int,
    seam_t_ms: int | None = None,
    trial_trigger_zones: dict[int, int] | None = None,
    session_coords: tuple[np.ndarray, np.ndarray] | None = None,
) -> tuple[list[dict], int, int]:
    """Build phase rows for one session from its event list.

    Events should be pre-filtered to KNOWN_EVENTS and sorted by time_stamp.
    Returns (phases, n_coord_based, n_fallback).
    """
    # Group events by type in order
    pretrials = events[events.action_vector_type == "Pretrial"].reset_index(drop=True)
    trial_starts = events[events.action_vector_type == "Trial Start"].reset_index(drop=True)
    trial_ends = events[events.action_vector_type == "Trial End"].reset_index(drop=True)

    n_trials = min(len(pretrials), len(trial_starts), len(trial_ends))
    if n_trials == 0:
        return [], 0, 0

    phases = []
    phase_idx = 0
    n_coord_based = 0
    n_fallback = 0

    for n in range(n_trials):
        pretrial_t = int(pretrials.iloc[n].time_stamp)
        trial_start_t = int(trial_starts.iloc[n].time_stamp)
        trial_end_t = int(trial_ends.iloc[n].time_stamp)
        trial_num = n + 1

        # Determine trial end boundary: coordinate-based or fallback
        trigger = (
            trial_trigger_zones.get(trial_num)
            if trial_trigger_zones is not None
            else None
        )
        entry_ms = None
        if trigger is not None and session_coords is not None:
            t_arr, z_arr = session_coords
            entry_ms = find_reward_entry_ms(
                t_arr, z_arr, trigger, db_trial_end_t=trial_end_t,
            )
        if entry_ms is not None:
            tr_end = entry_ms + REWARD_DWELL_MS
            n_coord_based += 1
        else:
            tr_end = trial_end_t - GOAL_REACH_OFFSET_MS
            n_fallback += 1

        # Pretrial N
        pt_start = pretrial_t - PRETRIAL_LOOKBACK_MS
        pt_end = trial_start_t
        if pt_end > pt_start:
            crosses = _crosses_seam(pt_start, pt_end, seam_t_ms)
            phases.append({
                "session_id": session_id,
                "phase": "pretrial",
                "phase_idx": phase_idx,
                "trial_number": trial_num,
                "t_start_ms": pt_start,
                "t_end_ms": pt_end,
                "duration_ms": pt_end - pt_start,
                "crosses_seam": crosses,
            })
            phase_idx += 1

        # Trial N
        tr_start = trial_start_t
        if tr_end > tr_start:
            crosses = _crosses_seam(tr_start, tr_end, seam_t_ms)
            phases.append({
                "session_id": session_id,
                "phase": "trial",
                "phase_idx": phase_idx,
                "trial_number": trial_num,
                "t_start_ms": tr_start,
                "t_end_ms": tr_end,
                "duration_ms": tr_end - tr_start,
                "crosses_seam": crosses,
            })
            phase_idx += 1

        # ITI N (only if there's a next trial)
        if n + 1 < n_trials:
            next_pretrial_t = int(pretrials.iloc[n + 1].time_stamp)
            iti_start = tr_end
            iti_end = next_pretrial_t - PRETRIAL_LOOKBACK_MS
            if iti_end > iti_start:
                crosses = _crosses_seam(iti_start, iti_end, seam_t_ms)
                phases.append({
                    "session_id": session_id,
                    "phase": "iti",
                    "phase_idx": phase_idx,
                    "trial_number": trial_num,
                    "t_start_ms": iti_start,
                    "t_end_ms": iti_end,
                    "duration_ms": iti_end - iti_start,
                    "crosses_seam": crosses,
                })
                phase_idx += 1

    return phases, n_coord_based, n_fallback


def _crosses_seam(t_start: int, t_end: int, seam_t_ms: int | None) -> bool:
    if seam_t_ms is None:
        return False
    return t_start < seam_t_ms <= t_end


def main() -> None:
    print("Stage 2c — Build phases.parquet")
    print()

    # Get seam locations for merged sessions
    print("  Finding seam timestamps for merged sessions...")
    seams = get_seam_timestamps()
    print(f"  Merged sessions with seams: {len(seams)}")
    for sid, t in sorted(seams.items()):
        print(f"    session {sid}: seam at t_ms={t:,}")
    offsets = get_segment_offsets()

    # Read all events from cleaned DB
    conn = sqlite3.connect(str(CLEAN_DB))
    all_events = pd.read_sql_query("""
        SELECT session_id, action_vector_type, time_stamp, zone,
               x_coordinate, y_coordinate
        FROM session_event
        ORDER BY session_id, session_event_id
    """, conn)

    # Get session list
    session_ids = pd.read_sql_query(
        "SELECT session_id FROM session", conn
    ).session_id.tolist()

    # Get per-trial trigger zones for coordinate-based boundary detection
    trial_goals = pd.read_sql_query("""
        SELECT session_id, trial_number, goal_location
        FROM trial
    """, conn)
    conn.close()

    # Build {session_id: {trial_number: trigger_zone}}
    trial_trigger_map: dict[int, dict[int, int]] = {}
    for _, row in trial_goals.iterrows():
        tz = GOAL_LOCATION_TO_ZONE.get(row["goal_location"])
        if tz is not None:
            trial_trigger_map.setdefault(int(row["session_id"]), {})[
                int(row["trial_number"])
            ] = tz

    # Load coordinates grouped by session for boundary detection
    print("  Loading coordinates for boundary detection...")
    coords = pd.read_parquet(COORDINATES, columns=["session_id", "t_ms", "zone"])
    coords = coords.sort_values(["session_id", "t_ms"]).reset_index(drop=True)
    session_coords: dict[int, tuple[np.ndarray, np.ndarray]] = {}
    for sid, grp in coords.groupby("session_id", sort=False):
        session_coords[int(sid)] = (
            grp["t_ms"].to_numpy(),
            grp["zone"].to_numpy(),
        )
    del coords

    print(f"  Total events: {len(all_events):,}")
    print(f"  Sessions: {len(session_ids)}")

    # Put the second recording of each stitched session on the stitched clock
    all_events, n_shifted = shift_second_recording_events(all_events, offsets)
    for sid, n in sorted(n_shifted.items()):
        print(f"  Stitched session {sid}: {n} second-recording events shifted by "
              f"+{offsets[sid]:,} ms (coordinate seam at {seams[sid]:,} ms)")

    # Filter to known event types
    known_events = all_events[all_events.action_vector_type.isin(KNOWN_EVENTS)].copy()
    n_dropped = len(all_events) - len(known_events)
    print(f"  Events after filtering to {KNOWN_EVENTS}: {len(known_events):,} "
          f"({n_dropped:,} dropped)")
    print()

    # Build phases per session
    all_phases = []
    n_sessions_with_phases = 0
    n_sessions_no_phases = 0
    n_validation_issues = 0
    total_coord_based = 0
    total_fallback = 0

    for sid in session_ids:
        sess_events = known_events[known_events.session_id == sid].copy()
        if sess_events.empty:
            n_sessions_no_phases += 1
            continue

        seam = seams.get(sid)
        triggers = trial_trigger_map.get(sid)
        sc = session_coords.get(sid)
        phases, n_cb, n_fb = build_phases_for_session(
            sess_events, sid,
            seam_t_ms=seam,
            trial_trigger_zones=triggers,
            session_coords=sc,
        )
        total_coord_based += n_cb
        total_fallback += n_fb

        if not phases:
            n_sessions_no_phases += 1
            continue

        # Validate: all durations positive
        for p in phases:
            if p["duration_ms"] <= 0:
                print(f"  WARNING: negative/zero duration in session {sid}, "
                      f"phase {p['phase']} #{p['trial_number']}: {p['duration_ms']}ms")
                n_validation_issues += 1

        all_phases.extend(phases)
        n_sessions_with_phases += 1

    print(f"  Sessions with phases: {n_sessions_with_phases}")
    print(f"  Sessions without phases: {n_sessions_no_phases} "
          f"(Exposure sessions or sessions with no trial events)")
    print(f"  Trial boundaries — coord-based: {total_coord_based}, "
          f"fallback: {total_fallback}")
    print(f"  Validation issues: {n_validation_issues}")
    print(f"  Total phase rows: {len(all_phases):,}")

    # Convert to DataFrame and write
    phase_df = pd.DataFrame(all_phases)

    # Cast types
    phase_df["session_id"] = phase_df["session_id"].astype(np.int32)
    phase_df["phase_idx"] = phase_df["phase_idx"].astype(np.int16)
    phase_df["trial_number"] = phase_df["trial_number"].astype(np.int16)
    phase_df["t_start_ms"] = phase_df["t_start_ms"].astype(np.int32)
    phase_df["t_end_ms"] = phase_df["t_end_ms"].astype(np.int32)
    phase_df["duration_ms"] = phase_df["duration_ms"].astype(np.int32)

    # Sort
    phase_df = phase_df.sort_values(["session_id", "t_start_ms"]).reset_index(drop=True)

    # Write
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pandas(phase_df, schema=PHASE_SCHEMA, preserve_index=False)
    pq.write_table(table, str(PHASES), compression="zstd")
    size_mb = PHASES.stat().st_size / 1e6

    # Count by phase type
    phase_counts = phase_df["phase"].value_counts().to_dict()
    n_crosses_seam = int(phase_df["crosses_seam"].sum())

    print(f"\n  Written: {PHASES} ({len(phase_df):,} rows, {size_mb:.1f} MB)")
    print(f"  Phase counts: {phase_counts}")
    print(f"  Phases crossing seam: {n_crosses_seam}")

    summary = {
        "sessions_with_phases": n_sessions_with_phases,
        "sessions_without_phases": n_sessions_no_phases,
        "total_phases": len(phase_df),
        "phase_counts": phase_counts,
        "crosses_seam": n_crosses_seam,
        "trial_boundaries_coord_based": total_coord_based,
        "trial_boundaries_fallback": total_fallback,
        "second_recording_events_shifted": n_shifted,
        "validation_issues": n_validation_issues,
        "file_size_mb": round(size_mb, 1),
    }

    print(f"\n{'=' * 60}")
    print("Stage 2c Summary")
    print(f"{'=' * 60}")
    for key, val in summary.items():
        print(f"  {key}: {val}")

    with open(BUILD_LOG, "a") as f:
        f.write(f"\nStage 2c — Build phases.parquet — {datetime.now().isoformat()}\n")
        f.write(f"{'─' * 60}\n")
        for key, val in summary.items():
            f.write(f"  {key}: {val}\n")
        f.write("\n")

    print(f"\nDone.")


if __name__ == "__main__":
    main()
