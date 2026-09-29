"""
Stage 2d: Export normalized tables from cleaned DB to parquets.

Inputs:
  data/processed/MazeControl-clean.db
  data/interim/sample_rates.parquet       (from Stage 2b-auto)
  data/interim/sample_rates_merged.parquet (from Stage 2b-merge)

Outputs:
  data/processed/subjects.parquet
  data/processed/sessions.parquet
  data/processed/trials.parquet
  data/processed/session_segments.parquet
  data/processed/build_log.txt  (appended)

Run:
  python data/pipeline/stage2d_export.py
"""

from __future__ import annotations

from datetime import datetime

import duckdb
import pandas as pd

from corner_maze.common.paths import (
    BUILD_LOG,
    CLEAN_DB,
    INTERIM_DIR,
    PROCESSED_DIR,
    SESSION_SEGMENTS,
    SESSIONS,
    SUBJECTS,
    TRIALS,
)

SAMPLE_RATES_AUTO = INTERIM_DIR / "sample_rates.parquet"
SAMPLE_RATES_MERGED = INTERIM_DIR / "sample_rates_merged.parquet"


def main() -> None:
    print("Stage 2d — Export normalized tables")
    print()

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    # Combine sample rate files
    sr_parts = []
    for path in [SAMPLE_RATES_AUTO, SAMPLE_RATES_MERGED]:
        if path.exists():
            sr_parts.append(pd.read_parquet(str(path)))
    if sr_parts:
        sr_combined = pd.concat(sr_parts, ignore_index=True)
        sr_path = INTERIM_DIR / "sample_rates_combined.parquet"
        sr_combined.to_parquet(str(sr_path), engine="pyarrow", index=False)
        print(f"  Combined sample rates: {len(sr_combined)} sessions")
    else:
        sr_path = None
        print("  WARNING: no sample rate files found")

    con = duckdb.connect()
    con.execute("INSTALL sqlite; LOAD sqlite;")
    con.execute(f"ATTACH '{CLEAN_DB}' AS clean (TYPE sqlite)")

    # ── subjects.parquet ─────────────────────────────────────────────

    con.execute(f"""
        COPY (
            SELECT
                subject_id::SMALLINT AS subject_id,
                name,
                sex,
                cue_goal_orientation,
                TRY_STRPTIME(date_of_birth, '%Y-%m-%d')::DATE AS date_of_birth
            FROM clean.subjects
            ORDER BY subject_id
        ) TO '{SUBJECTS}' (FORMAT PARQUET, COMPRESSION ZSTD)
    """)
    n_subjects = con.execute(f"SELECT COUNT(*) FROM '{SUBJECTS}'").fetchone()[0]
    print(f"  subjects.parquet: {n_subjects} rows")

    # ── sessions.parquet ─────────────────────────────────────────────

    sr_join = ""
    sr_cols = "NULL::TINYINT AS sample_rate_hz,"
    if sr_path and sr_path.exists():
        sr_join = f"LEFT JOIN '{sr_path}' sr USING (session_id)"
        sr_cols = "sr.sample_rate_hz::TINYINT AS sample_rate_hz,"

    con.execute(f"""
        COPY (
            SELECT
                s.session_id,
                s.subject_id::SMALLINT AS subject_id,
                s.session_number,
                s.session_type,
                TRY_STRPTIME(s.date, '%-m/%-d/%Y')::DATE AS date,
                s.time,
                s.session_duration::REAL AS session_duration,
                s.total_trials::SMALLINT AS total_trials,
                s.total_perfect::SMALLINT AS total_perfect,
                s.total_errors::SMALLINT AS total_errors,
                s.total_switches::SMALLINT AS total_switches,
                s.score,
                s.average_errors::REAL AS average_errors,
                s.average_latency::REAL AS average_latency,
                s.seed,
                s.reward_volume::SMALLINT AS reward_volume,
                s.delay::SMALLINT AS delay,
                s.cue_conf,
                {sr_cols}
                (seg.n_segments > 1) AS is_merged,
                seg.n_segments::TINYINT AS n_segments
            FROM clean.session s
            {sr_join}
            LEFT JOIN (
                SELECT session_id, COUNT(*) AS n_segments
                FROM clean.session_segments
                GROUP BY session_id
            ) seg USING (session_id)
            ORDER BY s.session_id
        ) TO '{SESSIONS}' (FORMAT PARQUET, COMPRESSION ZSTD)
    """)
    n_sessions = con.execute(f"SELECT COUNT(*) FROM '{SESSIONS}'").fetchone()[0]
    n_with_rate = con.execute(
        f"SELECT COUNT(*) FROM '{SESSIONS}' WHERE sample_rate_hz IS NOT NULL"
    ).fetchone()[0]
    print(f"  sessions.parquet: {n_sessions} rows ({n_with_rate} with sample_rate_hz)")

    # ── trials.parquet ───────────────────────────────────────────────

    # SQLite's flexible typing means time_duration is declared INTEGER
    # but contains floats. Use sqlite_all_varchar to bypass DuckDB's
    # strict type checking, then cast explicitly.
    con.execute("SET sqlite_all_varchar=true")
    con.execute(f"DETACH clean")
    con.execute(f"ATTACH '{CLEAN_DB}' AS clean (TYPE sqlite)")
    con.execute(f"""
        COPY (
            SELECT
                trial_id::INTEGER AS trial_id,
                session_id::INTEGER AS session_id,
                trial_number::SMALLINT AS trial_number,
                start_arm,
                goal_location,
                cue_orientation,
                TRY_CAST(cue_on AS TINYINT) AS cue_on,
                TRY_CAST(time_duration AS REAL) AS time_duration,
                TRY_CAST(errors AS SMALLINT) AS errors,
                turn_1,
                turn_2,
                TRY_CAST(perseveration_score AS SMALLINT) AS perseveration_score,
                TRY_CAST(first_error AS SMALLINT) AS first_error,
                goal_zones_visited
            FROM clean.trial
            ORDER BY session_id, trial_number
        ) TO '{TRIALS}' (FORMAT PARQUET, COMPRESSION ZSTD)
    """)
    # Reset for any subsequent queries
    con.execute("SET sqlite_all_varchar=false")
    con.execute(f"DETACH clean")
    con.execute(f"ATTACH '{CLEAN_DB}' AS clean (TYPE sqlite)")
    n_trials = con.execute(f"SELECT COUNT(*) FROM '{TRIALS}'").fetchone()[0]
    print(f"  trials.parquet: {n_trials} rows")

    # ── session_segments.parquet ─────────────────────────────────────

    con.execute(f"""
        COPY (
            SELECT
                session_id,
                segment_idx::TINYINT AS segment_idx,
                original_session_id,
                session_number,
                date,
                time,
                session_duration::REAL AS session_duration,
                coordinate_history_file
            FROM clean.session_segments
            ORDER BY session_id, segment_idx
        ) TO '{SESSION_SEGMENTS}' (FORMAT PARQUET, COMPRESSION ZSTD)
    """)
    n_segments = con.execute(f"SELECT COUNT(*) FROM '{SESSION_SEGMENTS}'").fetchone()[0]
    print(f"  session_segments.parquet: {n_segments} rows")

    # ── Validation ───────────────────────────────────────────────────

    print("\n  Validating foreign keys...")

    # Every trial.session_id exists in sessions
    orphan_trials = con.execute(f"""
        SELECT COUNT(*) FROM '{TRIALS}' t
        LEFT JOIN '{SESSIONS}' s ON t.session_id = s.session_id
        WHERE s.session_id IS NULL
    """).fetchone()[0]
    assert orphan_trials == 0, f"{orphan_trials} orphan trials"

    # Every session.subject_id exists in subjects
    orphan_sessions = con.execute(f"""
        SELECT COUNT(*) FROM '{SESSIONS}' s
        LEFT JOIN '{SUBJECTS}' sub ON s.subject_id = sub.subject_id
        WHERE sub.subject_id IS NULL
    """).fetchone()[0]
    assert orphan_sessions == 0, f"{orphan_sessions} orphan sessions"

    # Every session has at least one segment
    sessions_no_seg = con.execute(f"""
        SELECT COUNT(*) FROM '{SESSIONS}' s
        LEFT JOIN '{SESSION_SEGMENTS}' seg ON s.session_id = seg.session_id
        WHERE seg.session_id IS NULL
    """).fetchone()[0]
    assert sessions_no_seg == 0, f"{sessions_no_seg} sessions with no segment"

    print("  All validation checks passed")

    # ── Summary ──────────────────────────────────────────────────────

    sizes = {
        "subjects": SUBJECTS.stat().st_size / 1e3,
        "sessions": SESSIONS.stat().st_size / 1e3,
        "trials": TRIALS.stat().st_size / 1e3,
        "session_segments": SESSION_SEGMENTS.stat().st_size / 1e3,
    }

    summary = {
        "subjects": n_subjects,
        "sessions": n_sessions,
        "sessions_with_sample_rate": n_with_rate,
        "trials": n_trials,
        "session_segments": n_segments,
    }

    print(f"\n{'=' * 60}")
    print("Stage 2d Summary")
    print(f"{'=' * 60}")
    for key, val in summary.items():
        print(f"  {key}: {val}")
    print(f"\n  File sizes:")
    for name, kb in sizes.items():
        print(f"    {name}.parquet: {kb:.1f} KB")

    with open(BUILD_LOG, "a") as f:
        f.write(f"\nStage 2d — Export normalized tables — {datetime.now().isoformat()}\n")
        f.write(f"{'─' * 60}\n")
        for key, val in summary.items():
            f.write(f"  {key}: {val}\n")
        f.write("\n")

    con.close()
    print(f"\nDone.")


if __name__ == "__main__":
    main()
