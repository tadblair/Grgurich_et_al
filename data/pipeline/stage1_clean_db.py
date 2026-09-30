"""
Stage 1: Clean MazeControl.db into MazeControl-clean.db.

Inputs:
  data/raw/MazeControl.db
  config/subject_exceptions.yaml  (per-subject session rules)

Outputs:
  data/processed/MazeControl-clean.db
  data/processed/cleaning_review.txt
  data/processed/build_log.txt  (appended)

Run:
  python data/pipeline/stage1_clean_db.py
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

import yaml

from corner_maze.common.paths import (
    BUILD_LOG,
    CLEAN_DB,
    CLEANING_REVIEW,
    PROCESSED_DIR,
    RAW_DB,
    SUBJECT_EXCEPTIONS,
)

MIN_VALID_SESSION_DURATION_S = 15 * 60  # a complete session runs 16 or more trials, well over 15 min


# ── Config loading ───────────────────────────────────────────────────


def load_subject_exceptions() -> dict:
    with open(SUBJECT_EXCEPTIONS) as f:
        raw = yaml.safe_load(f)
    # Keys are subject_ids (ints); normalize in case YAML reads them as ints already
    return {int(k): v for k, v in raw.items()} if raw else {}


# ── Step 1: Copy raw DB ─────────────────────────────────────────────


def copy_raw_db() -> None:
    """Copy raw MazeControl.db to the processed location."""
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(RAW_DB)) as src:
        with sqlite3.connect(str(CLEAN_DB)) as dst:
            src.backup(dst)
    print(f"  Copied {RAW_DB} → {CLEAN_DB}")


# ── Step 3: Drop bad-start sessions ─────────────────────────────────


def restore_sessions(conn: sqlite3.Connection, exceptions: dict) -> dict:
    """Un-X sessions that the YAML says should be kept.

    Must run BEFORE drop_bad_sessions so the restored sessions survive
    the general X/NULL filter. The restore clears the X marker by
    leaving session_number as-is (it stays 'X' in the cleaned DB, but
    the session is excluded from the drop query by session_id).

    Returns summary of restores.
    """
    cur = conn.cursor()
    restore_ids = []

    for subject_id, entry in exceptions.items():
        for exc in entry.get("exceptions", []):
            if exc.get("action") != "restore":
                continue
            sid = exc.get("session_id")
            if sid is None:
                continue
            # Verify it actually exists and is X'd or NULL-duration
            cur.execute(
                "SELECT session_id, session_number, session_duration FROM session WHERE session_id = ?",
                (sid,),
            )
            row = cur.fetchone()
            if row is None:
                print(f"  WARNING: restore target session_id {sid} not found")
                continue
            restore_ids.append(sid)
            print(f"  Restoring session {sid} (subject {subject_id}): "
                  f"session_number='{row[1]}', will be kept despite X/NULL marker")

    return {"n_restored": len(restore_ids), "restored_ids": restore_ids}


def drop_bad_sessions(conn: sqlite3.Connection, restored_ids: list[int]) -> dict:
    """Remove sessions with NULL duration or session_number = 'X'.

    Sessions in restored_ids are excluded from the drop — they were
    explicitly marked for retention by the YAML restore action.
    """
    cur = conn.cursor()

    # Count totals before filtering out restores
    cur.execute("SELECT COUNT(*) FROM session WHERE session_number = 'X'")
    n_x_total = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM session WHERE session_duration IS NULL")
    n_null_total = cur.fetchone()[0]

    # Find sessions to drop, excluding restored ones
    cur.execute("""
        SELECT session_id FROM session
        WHERE session_duration IS NULL OR session_number = 'X'
    """)
    candidate_ids = [row[0] for row in cur.fetchall()]
    restore_set = set(restored_ids)
    drop_ids = [sid for sid in candidate_ids if sid not in restore_set]

    n_saved = len(candidate_ids) - len(drop_ids)
    if n_saved > 0:
        print(f"  ({n_saved} session(s) saved from drop by 'restore' action)")

    if drop_ids:
        ph = ",".join("?" for _ in drop_ids)
        cur.execute(f"DELETE FROM trial WHERE session_id IN ({ph})", drop_ids)
        cur.execute(f"DELETE FROM session_event WHERE session_id IN ({ph})", drop_ids)
        cur.execute(f"DELETE FROM session WHERE session_id IN ({ph})", drop_ids)

    conn.commit()

    return {
        "n_x_sessions": n_x_total,
        "n_null_duration": n_null_total,
        "n_restored": n_saved,
        "total_dropped": len(drop_ids),
    }


# ── Step 4: Identify and merge duplicate sessions ────────────────────


def find_merge_pairs(conn: sqlite3.Connection) -> list[tuple[dict, dict]]:
    """Find session pairs with the same (subject_id, session_number).

    Returns a list of (kept_row, deleted_row) tuples, where kept_row
    is the one with the lower session_id (earlier recording).
    """
    cur = conn.cursor()

    cur.execute("""
        SELECT t1.session_id, t1.subject_id, t1.session_number,
               t2.session_id AS session_id_2
        FROM session t1
        JOIN session t2 ON t1.session_number = t2.session_number
                       AND t1.subject_id = t2.subject_id
        WHERE t1.session_id < t2.session_id
        ORDER BY t1.session_id
    """)
    pairs_raw = cur.fetchall()

    pairs = []
    for row in pairs_raw:
        kept_id, _, _, deleted_id = row
        # Fetch full rows
        cur.execute("SELECT * FROM session WHERE session_id = ?", (kept_id,))
        kept = _session_row_to_dict(cur, cur.fetchone())
        cur.execute("SELECT * FROM session WHERE session_id = ?", (deleted_id,))
        deleted = _session_row_to_dict(cur, cur.fetchone())
        pairs.append((kept, deleted))

    return pairs


def _session_row_to_dict(cur: sqlite3.Cursor, row: tuple) -> dict:
    """Convert a session row tuple to a dict keyed by column name."""
    cols = [desc[0] for desc in cur.description]
    return dict(zip(cols, row))


def merge_session_pair(
    conn: sqlite3.Connection, kept: dict, deleted: dict
) -> None:
    """Merge two session rows into one, keeping the earlier session_id.

    Reassigns trial and session_event rows from the deleted session to
    the kept session, updates the kept session's totals, and deletes the
    duplicate session row.
    """
    cur = conn.cursor()
    kept_id = kept["session_id"]
    deleted_id = deleted["session_id"]

    # Reassign trials and events. Trials from the deleted session need their
    # trial_number offset by max(kept.trial_number) so the merged session has
    # a unique 1..N sequence — otherwise stage3c's (session_id, trial_number)
    # joins collide and assign wrong phase bounds to half the trials.
    cur.execute(
        "SELECT COALESCE(MAX(trial_number), 0) FROM trial WHERE session_id = ?",
        (kept_id,),
    )
    tn_offset = cur.fetchone()[0]
    cur.execute(
        "UPDATE trial SET session_id = ?, trial_number = trial_number + ? WHERE session_id = ?",
        (kept_id, tn_offset, deleted_id),
    )
    cur.execute(
        "UPDATE session_event SET session_id = ? WHERE session_id = ?", (kept_id, deleted_id)
    )

    # Compute merged totals (matches legacy logic)
    merged_duration = (kept["session_duration"] or 0) + (deleted["session_duration"] or 0)
    merged_trials = (kept["total_trials"] or 0) + (deleted["total_trials"] or 0)
    merged_perfect = (kept["total_perfect"] or 0) + (deleted["total_perfect"] or 0)
    merged_errors = (kept["total_errors"] or 0) + (deleted["total_errors"] or 0)

    kept_score = float(kept["score"]) if kept["score"] else 0
    deleted_score = float(deleted["score"]) if deleted["score"] else 0
    merged_score = (kept_score + deleted_score) / 2

    kept_avg_err = kept["average_errors"] if kept["average_errors"] else 0
    deleted_avg_err = deleted["average_errors"] if deleted["average_errors"] else 0
    merged_avg_errors = (kept_avg_err + deleted_avg_err) / 2

    cur.execute(
        """UPDATE session
           SET session_duration = ?,
               total_trials = ?,
               total_perfect = ?,
               score = ?,
               total_errors = ?,
               average_errors = ?
           WHERE session_id = ?""",
        (
            merged_duration,
            merged_trials,
            merged_perfect,
            str(merged_score),
            merged_errors,
            merged_avg_errors,
            kept_id,
        ),
    )

    # Delete the duplicate session row
    cur.execute("DELETE FROM session WHERE session_id = ?", (deleted_id,))


# ── Step 5: Create session_segments table ────────────────────────────


def create_session_segments(
    conn: sqlite3.Connection, merge_pairs: list[tuple[dict, dict]]
) -> int:
    """Create and populate the session_segments table.

    Every session gets at least one row (segment_idx=0). Merged sessions
    get one row per original recording in chronological order.

    Returns the total number of segment rows inserted.
    """
    cur = conn.cursor()

    cur.execute("DROP TABLE IF EXISTS session_segments")
    cur.execute("""
        CREATE TABLE session_segments (
            session_id              INTEGER NOT NULL,
            segment_idx             INTEGER NOT NULL,
            original_session_id     INTEGER NOT NULL,
            session_number          TEXT,
            date                    TEXT,
            time                    TEXT,
            session_duration        REAL,
            coordinate_history_file TEXT,
            PRIMARY KEY (session_id, segment_idx)
        )
    """)

    # Build a set of canonical session_ids that were involved in merges
    merged_canonical = {}
    for kept, deleted in merge_pairs:
        canonical_id = kept["session_id"]
        merged_canonical[canonical_id] = (kept, deleted)

    # Insert rows for all sessions currently in the cleaned DB
    cur.execute("""
        SELECT session_id, session_number, date, time,
               session_duration, coordinate_history_file
        FROM session
        ORDER BY session_id
    """)
    all_sessions = cur.fetchall()

    n_inserted = 0
    for sess_id, sess_num, date, time_, dur, coord_file in all_sessions:
        if sess_id in merged_canonical:
            # Merged session: insert one row per original recording
            kept, deleted = merged_canonical[sess_id]
            cur.execute(
                """INSERT INTO session_segments
                   (session_id, segment_idx, original_session_id,
                    session_number, date, time, session_duration,
                    coordinate_history_file)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    sess_id, 0, kept["session_id"],
                    kept["session_number"], kept["date"], kept["time"],
                    kept["session_duration"], kept["coordinate_history_file"],
                ),
            )
            cur.execute(
                """INSERT INTO session_segments
                   (session_id, segment_idx, original_session_id,
                    session_number, date, time, session_duration,
                    coordinate_history_file)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    sess_id, 1, deleted["session_id"],
                    deleted["session_number"], deleted["date"], deleted["time"],
                    deleted["session_duration"], deleted["coordinate_history_file"],
                ),
            )
            n_inserted += 2
        else:
            # Non-merged session: one segment row
            cur.execute(
                """INSERT INTO session_segments
                   (session_id, segment_idx, original_session_id,
                    session_number, date, time, session_duration,
                    coordinate_history_file)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (sess_id, 0, sess_id, sess_num, date, time_, dur, coord_file),
            )
            n_inserted += 1

    conn.commit()
    return n_inserted


# ── Step 6: Apply YAML-driven exception drops ────────────────────────


def apply_exceptions(conn: sqlite3.Connection, exceptions: dict) -> dict:
    """Apply per-subject session exceptions from the YAML.

    Only processes entries with action='drop'. 'flag' entries are handled
    in the review step. 'note' entries are documentation only.

    Returns summary of actions taken.
    """
    cur = conn.cursor()
    n_dropped = 0

    for subject_id, entry in exceptions.items():
        for exc in entry.get("exceptions", []):
            if exc.get("action") != "drop":
                continue
            sid = exc.get("session_id")
            if sid is None:
                continue

            # Verify the session exists before dropping
            cur.execute("SELECT session_id FROM session WHERE session_id = ?", (sid,))
            if cur.fetchone() is None:
                print(f"  WARNING: session_id {sid} (subject {subject_id}) not found — "
                      f"may have been removed by an earlier step")
                continue

            cur.execute("DELETE FROM trial WHERE session_id = ?", (sid,))
            cur.execute("DELETE FROM session_event WHERE session_id = ?", (sid,))
            cur.execute("DELETE FROM session WHERE session_id = ?", (sid,))
            # Also remove from session_segments if it was already inserted
            cur.execute("DELETE FROM session_segments WHERE session_id = ? OR original_session_id = ?",
                        (sid, sid))
            n_dropped += 1
            print(f"  Dropped session {sid} (subject {subject_id}): {exc.get('reason', '').strip()[:80]}")

    conn.commit()
    return {"n_exception_drops": n_dropped}


# ── Step 7: Surface anomalies for review ─────────────────────────────


def surface_anomalies(
    conn: sqlite3.Connection, exceptions: dict
) -> list[dict]:
    """Find sessions that may need review.

    Current heuristic: sessions shorter than MIN_VALID_SESSION_DURATION_S
    that aren't already covered by a YAML entry. Also includes any
    sessions with action='flag' from the YAML.

    Returns a list of anomaly dicts and writes cleaning_review.txt.
    """
    cur = conn.cursor()
    anomalies = []

    # Short-duration sessions
    cur.execute(
        """SELECT s.session_id, s.subject_id, sub.name,
                  s.session_number, s.session_type, s.session_duration,
                  s.total_trials, s.date
           FROM session s
           JOIN subjects sub USING (subject_id)
           WHERE s.session_duration < ?
           ORDER BY s.session_duration""",
        (MIN_VALID_SESSION_DURATION_S,),
    )
    for row in cur.fetchall():
        sid, subj_id, name, sess_num, sess_type, dur, trials, date = row
        # Check if this session is already covered by a YAML entry
        if _session_in_exceptions(subj_id, sid, exceptions):
            continue
        anomalies.append({
            "session_id": sid,
            "subject_id": subj_id,
            "name": name,
            "session_number": sess_num,
            "session_type": sess_type,
            "duration_s": dur,
            "total_trials": trials,
            "date": date,
            "source": "short_duration",
        })

    # Flagged sessions from YAML
    for subject_id, entry in exceptions.items():
        for exc in entry.get("exceptions", []):
            if exc.get("action") != "flag":
                continue
            sid = exc.get("session_id")
            if sid is None:
                continue
            cur.execute(
                """SELECT s.session_id, s.subject_id, sub.name,
                          s.session_number, s.session_type, s.session_duration,
                          s.total_trials, s.date
                   FROM session s
                   JOIN subjects sub USING (subject_id)
                   WHERE s.session_id = ?""",
                (sid,),
            )
            row = cur.fetchone()
            if row is None:
                continue
            sid_, subj_id, name, sess_num, sess_type, dur, trials, date = row
            anomalies.append({
                "session_id": sid_,
                "subject_id": subj_id,
                "name": name,
                "session_number": sess_num,
                "session_type": sess_type,
                "duration_s": dur,
                "total_trials": trials,
                "date": date,
                "source": "yaml_flag",
                "reason": exc.get("reason", "").strip(),
            })

    # Write cleaning_review.txt
    with open(CLEANING_REVIEW, "w") as f:
        f.write(f"Cleaning Review — {datetime.now().isoformat()}\n")
        f.write(f"{'=' * 60}\n\n")
        if not anomalies:
            f.write("No anomalies found.\n")
        else:
            f.write(f"{len(anomalies)} session(s) flagged for review:\n\n")
            for a in anomalies:
                f.write(f"  session_id:    {a['session_id']}\n")
                f.write(f"  subject:       {a['subject_id']} ({a['name']})\n")
                f.write(f"  session:       {a['session_number']} — {a['session_type']}\n")
                f.write(f"  date:          {a['date']}\n")
                f.write(f"  duration:      {a['duration_s']:.0f}s "
                        f"({a['duration_s']/60:.1f} min)\n")
                f.write(f"  total_trials:  {a['total_trials']}\n")
                f.write(f"  source:        {a['source']}\n")
                if a.get("reason"):
                    f.write(f"  reason:        {a['reason'][:120]}\n")
                f.write("\n")

    return anomalies


def _session_in_exceptions(subject_id: int, session_id: int, exceptions: dict) -> bool:
    """Check if a session is already documented in the YAML."""
    entry = exceptions.get(subject_id)
    if entry is None:
        return False
    for exc in entry.get("exceptions", []):
        if exc.get("session_id") == session_id:
            return True
    return False


# ── Step 8: Validation ───────────────────────────────────────────────


def validate(conn: sqlite3.Connection, restored_ids: list[int] | None = None) -> None:
    """Assert post-cleaning invariants."""
    cur = conn.cursor()
    restored_ids = restored_ids or []

    # No NULL durations remain (excluding restored sessions)
    if restored_ids:
        ph = ",".join("?" for _ in restored_ids)
        cur.execute(
            f"SELECT COUNT(*) FROM session WHERE session_duration IS NULL "
            f"AND session_id NOT IN ({ph})", restored_ids
        )
    else:
        cur.execute("SELECT COUNT(*) FROM session WHERE session_duration IS NULL")
    n = cur.fetchone()[0]
    assert n == 0, f"Found {n} sessions with NULL duration after cleaning"

    # No X session_numbers remain (excluding restored sessions)
    if restored_ids:
        ph = ",".join("?" for _ in restored_ids)
        cur.execute(
            f"SELECT COUNT(*) FROM session WHERE session_number = 'X' "
            f"AND session_id NOT IN ({ph})", restored_ids
        )
    else:
        cur.execute("SELECT COUNT(*) FROM session WHERE session_number = 'X'")
    n = cur.fetchone()[0]
    assert n == 0, f"Found {n} sessions with session_number='X' after cleaning"

    # Every trial.session_id resolves
    cur.execute("""
        SELECT COUNT(*) FROM trial t
        LEFT JOIN session s ON t.session_id = s.session_id
        WHERE s.session_id IS NULL
    """)
    n = cur.fetchone()[0]
    assert n == 0, f"Found {n} orphan trial rows"

    # Every session_event.session_id resolves
    cur.execute("""
        SELECT COUNT(*) FROM session_event e
        LEFT JOIN session s ON e.session_id = s.session_id
        WHERE s.session_id IS NULL
    """)
    n = cur.fetchone()[0]
    assert n == 0, f"Found {n} orphan session_event rows"

    # Every session.subject_id resolves
    cur.execute("""
        SELECT COUNT(*) FROM session s
        LEFT JOIN subjects sub ON s.subject_id = sub.subject_id
        WHERE sub.subject_id IS NULL
    """)
    n = cur.fetchone()[0]
    assert n == 0, f"Found {n} sessions with missing subject"

    # Every session has at least one segment row
    cur.execute("""
        SELECT COUNT(*) FROM session s
        LEFT JOIN session_segments seg ON s.session_id = seg.session_id
        WHERE seg.session_id IS NULL
    """)
    n = cur.fetchone()[0]
    assert n == 0, f"Found {n} sessions with no segment row"

    print("  All validation checks passed")


# ── Logging ──────────────────────────────────────────────────────────


def write_build_log(summary: dict) -> None:
    """Append a structured summary to build_log.txt."""
    with open(BUILD_LOG, "a") as f:
        f.write(f"\nStage 1 — Clean MazeControl.db — {datetime.now().isoformat()}\n")
        f.write(f"{'─' * 60}\n")
        for key, val in summary.items():
            f.write(f"  {key}: {val}\n")
        f.write("\n")


# ── Main ─────────────────────────────────────────────────────────────


def main() -> None:
    print("Stage 1 — Clean MazeControl.db")
    print(f"  Source: {RAW_DB}")
    print(f"  Target: {CLEAN_DB}")
    print()

    exceptions = load_subject_exceptions()

    # Step 1
    copy_raw_db()

    conn = sqlite3.connect(str(CLEAN_DB))

    try:
        # Step 2: Restore sessions before the general drop
        print("\nChecking for sessions to restore...")
        restore_summary = restore_sessions(conn, exceptions)
        if restore_summary["n_restored"] > 0:
            print(f"  Restored: {restore_summary['n_restored']} session(s)")
        else:
            print(f"  None to restore")

        # Step 3
        print("\nDropping bad-start sessions...")
        drop_summary = drop_bad_sessions(conn, restore_summary.get("restored_ids", []))
        print(f"  X sessions dropped: {drop_summary['n_x_sessions']} total in DB, "
              f"{drop_summary['n_restored']} saved by restore")
        print(f"  NULL-duration dropped: {drop_summary['n_null_duration']}")
        print(f"  Net dropped: {drop_summary['total_dropped']}")

        # Step 4
        print("\nFinding merge pairs...")
        merge_pairs = find_merge_pairs(conn)
        print(f"  Merge pairs found: {len(merge_pairs)}")
        for kept, deleted in merge_pairs:
            print(f"    subject {kept['subject_id']}, session_number '{kept['session_number']}': "
                  f"keep {kept['session_id']}, delete {deleted['session_id']}")

        # Perform merges
        for kept, deleted in merge_pairs:
            merge_session_pair(conn, kept, deleted)
        conn.commit()
        print(f"  Merges complete")

        # Step 5
        print("\nCreating session_segments table...")
        n_segments = create_session_segments(conn, merge_pairs)
        print(f"  Segment rows inserted: {n_segments}")

        # Step 6
        print("\nApplying YAML exception drops...")
        exc_summary = apply_exceptions(conn, exceptions)
        print(f"  Sessions dropped by exceptions: {exc_summary['n_exception_drops']}")

        # Step 7
        print("\nSurfacing anomalies for review...")
        anomalies = surface_anomalies(conn, exceptions)
        print(f"  Anomalies for review: {len(anomalies)} (see {CLEANING_REVIEW})")

        # Step 8
        print("\nValidating...")
        validate(conn, restored_ids=restore_summary.get("restored_ids", []))

        # Final counts
        cur = conn.cursor()
        n_subjects = cur.execute("SELECT COUNT(*) FROM subjects").fetchone()[0]
        n_sessions = cur.execute("SELECT COUNT(*) FROM session").fetchone()[0]
        n_trials = cur.execute("SELECT COUNT(*) FROM trial").fetchone()[0]
        n_events = cur.execute("SELECT COUNT(*) FROM session_event").fetchone()[0]

        summary = {
            "subjects": n_subjects,
            "sessions": n_sessions,
            "trials": n_trials,
            "session_events": n_events,
            "merge_pairs": len(merge_pairs),
            "segments": n_segments,
            "x_sessions_dropped": drop_summary["n_x_sessions"],
            "null_duration_dropped": drop_summary["n_null_duration"],
            "exception_drops": exc_summary["n_exception_drops"],
            "anomalies_for_review": len(anomalies),
        }

        print(f"\n{'=' * 60}")
        print("Stage 1 Summary")
        print(f"{'=' * 60}")
        for key, val in summary.items():
            print(f"  {key}: {val}")

        write_build_log(summary)

    finally:
        conn.close()

    print(f"\nDone. Cleaned DB at {CLEAN_DB}")


if __name__ == "__main__":
    main()
