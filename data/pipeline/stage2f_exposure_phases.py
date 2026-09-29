"""
Stage 2f: Build phase spans for exposure sessions.

Appends presession, acclimation (2e only), and reward phases to the
existing phases.parquet (which contains training-session phases from
Stage 2c).

Phase structure:
  1e: presession (trial_number=0), reward 1..N
  2e: presession (trial_number=0), acclimation -6..-1, reward 1..N

Inputs:
  data/processed/phases.parquet         (training phases from Stage 2c)
  data/processed/exposure_rewards.parquet (from Stage 2e)
  data/processed/coordinates.parquet    (for session end times)
  data/processed/sessions.parquet       (exposure session list)
  data/processed/MazeControl-clean.db   (acclimation events for 2e)

Outputs:
  data/processed/phases.parquet  (appended with exposure phases)

Run:
  python data/pipeline/stage2f_exposure_phases.py
"""

from __future__ import annotations

from datetime import datetime

import duckdb
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from corner_maze.common.paths import (
    BUILD_LOG,
    CLEAN_DB,
    COORDINATES,
    EXPOSURE_REWARDS,
    PHASES,
    PROCESSED_DIR,
    SESSIONS,
)
from stage2c_phases import PHASE_SCHEMA


def _load_acclimation_events(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Load barrier-drop acclimation events for all 2e sessions.

    Returns DataFrame with session_id, event_order (0-5), t_ms.
    """
    df = con.execute("""
        SELECT
            CAST(se.session_id AS INT) AS session_id,
            CAST(se.time_stamp AS DOUBLE) AS t_ms,
            ROW_NUMBER() OVER (
                PARTITION BY se.session_id
                ORDER BY CAST(se.session_event_id AS INT)
            ) - 1 AS event_order
        FROM clean.session_event se
        JOIN clean.session s USING (session_id)
        WHERE s.session_type = 'Exposure'
          AND s.session_number = '2e'
          AND se.action_vector_type NOT IN ('Presession', 'Exp Trial')
        ORDER BY CAST(se.session_id AS INT), CAST(se.session_event_id AS INT)
    """).fetchdf()
    return df


def _get_session_end_times(
    con: duckdb.DuckDBPyConnection, session_ids: list[int]
) -> dict[int, int]:
    """Get MAX(t_ms) per session from coordinates.parquet."""
    id_list = ",".join(str(s) for s in session_ids)
    rows = con.execute(f"""
        SELECT session_id, MAX(t_ms) AS t_end
        FROM '{COORDINATES}'
        WHERE session_id IN ({id_list})
        GROUP BY session_id
    """).fetchall()
    return {int(r[0]): int(r[1]) for r in rows}


def _build_phases_1e(
    session_id: int,
    rewards: pd.DataFrame,
    session_end_ms: int,
) -> list[dict]:
    """Build phase rows for a 1e session."""
    rows = []
    phase_idx = 0
    t_entries = rewards["t_entry_ms"].values

    # Presession: [0, first reward)
    rows.append({
        "session_id": session_id,
        "phase": "presession",
        "phase_idx": phase_idx,
        "trial_number": 0,
        "t_start_ms": 0,
        "t_end_ms": int(t_entries[0]),
        "crosses_seam": False,
    })
    phase_idx += 1

    # Reward phases
    for i in range(len(t_entries)):
        t_start = int(t_entries[i])
        t_end = int(t_entries[i + 1]) if i + 1 < len(t_entries) else session_end_ms
        rows.append({
            "session_id": session_id,
            "phase": "reward",
            "phase_idx": phase_idx,
            "trial_number": i + 1,
            "t_start_ms": t_start,
            "t_end_ms": t_end,
            "crosses_seam": False,
        })
        phase_idx += 1

    return rows


def _build_phases_2e(
    session_id: int,
    acclimation: pd.DataFrame,
    rewards: pd.DataFrame,
    session_end_ms: int,
) -> list[dict]:
    """Build phase rows for a 2e session."""
    rows = []
    phase_idx = 0
    accl_ts = acclimation["t_ms"].values
    t_entries = rewards["t_entry_ms"].values

    # Presession: [0, first acclimation)
    rows.append({
        "session_id": session_id,
        "phase": "presession",
        "phase_idx": phase_idx,
        "trial_number": 0,
        "t_start_ms": 0,
        "t_end_ms": int(accl_ts[0]),
        "crosses_seam": False,
    })
    phase_idx += 1

    # Acclimation phases: trial_number -6..-1
    for i in range(len(accl_ts)):
        t_start = int(accl_ts[i])
        t_end = int(accl_ts[i + 1]) if i + 1 < len(accl_ts) else int(t_entries[0])
        rows.append({
            "session_id": session_id,
            "phase": "acclimation",
            "phase_idx": phase_idx,
            "trial_number": i - 6,  # -6, -5, -4, -3, -2, -1
            "t_start_ms": t_start,
            "t_end_ms": t_end,
            "crosses_seam": False,
        })
        phase_idx += 1

    # Reward phases
    for i in range(len(t_entries)):
        t_start = int(t_entries[i])
        t_end = int(t_entries[i + 1]) if i + 1 < len(t_entries) else session_end_ms
        rows.append({
            "session_id": session_id,
            "phase": "reward",
            "phase_idx": phase_idx,
            "trial_number": i + 1,
            "t_start_ms": t_start,
            "t_end_ms": t_end,
            "crosses_seam": False,
        })
        phase_idx += 1

    return rows


def main() -> None:
    print("Stage 2f — Build exposure session phases")
    print()

    con = duckdb.connect()
    con.execute("INSTALL sqlite; LOAD sqlite;")
    con.execute("SET sqlite_all_varchar=true")
    con.execute(f"ATTACH '{CLEAN_DB}' AS clean (TYPE sqlite)")

    # Load inputs
    sessions_df = con.execute(f"""
        SELECT session_id, session_number
        FROM '{SESSIONS}'
        WHERE session_number IN ('1e', '2e')
        ORDER BY session_id
    """).fetchdf()

    rewards_df = pd.read_parquet(str(EXPOSURE_REWARDS))
    acclimation_df = _load_acclimation_events(con)
    end_times = _get_session_end_times(
        con, sessions_df["session_id"].tolist()
    )

    # Read existing training phases
    existing = pd.read_parquet(str(PHASES))
    # Remove any prior exposure phases (idempotent re-run)
    exposure_ids = set(sessions_df["session_id"])
    existing = existing[~existing["session_id"].isin(exposure_ids)]

    all_rows = []
    n_skipped = 0

    for _, sess in sessions_df.iterrows():
        sid = int(sess["session_id"])
        snum = sess["session_number"]

        # Get rewards for this session
        sr = rewards_df[rewards_df["session_id"] == sid]
        if sr.empty or sr["t_entry_ms"].isna().all():
            print(f"  SKIP session {sid} ({snum}): no reward data")
            n_skipped += 1
            continue

        session_end_ms = end_times.get(sid)
        if session_end_ms is None:
            print(f"  SKIP session {sid} ({snum}): no coordinate data")
            n_skipped += 1
            continue

        if snum == "2e":
            sa = acclimation_df[acclimation_df["session_id"] == sid]
            if len(sa) < 6:
                print(
                    f"  WARN session {sid} (2e): {len(sa)} acclimation "
                    f"events (expected 6), building without acclimation"
                )
                # Fall back to 1e-style (presession + rewards only)
                phases = _build_phases_1e(sid, sr, session_end_ms)
            else:
                phases = _build_phases_2e(sid, sa, sr, session_end_ms)
        else:
            phases = _build_phases_1e(sid, sr, session_end_ms)

        all_rows.extend(phases)

    if not all_rows:
        print("\n  ERROR: no exposure phases built")
        con.close()
        return

    # Build DataFrame and compute duration_ms
    new_df = pd.DataFrame(all_rows)
    new_df["duration_ms"] = new_df["t_end_ms"] - new_df["t_start_ms"]

    # Validate: no negative durations
    bad = new_df[new_df["duration_ms"] <= 0]
    if len(bad) > 0:
        print(f"\n  WARNING: {len(bad)} phases with non-positive duration:")
        for _, r in bad.head(5).iterrows():
            print(f"    session {r['session_id']} phase_idx={r['phase_idx']} "
                  f"{r['phase']} trial={r['trial_number']} "
                  f"duration={r['duration_ms']}ms")

    # Combine with existing training phases
    combined = pd.concat([existing, new_df], ignore_index=True)
    combined = combined.sort_values(["session_id", "t_start_ms"]).reset_index(drop=True)

    # Cast types to match schema
    combined["session_id"] = combined["session_id"].astype("int32")
    combined["phase_idx"] = combined["phase_idx"].astype("int16")
    combined["trial_number"] = combined["trial_number"].astype("int16")
    combined["t_start_ms"] = combined["t_start_ms"].astype("int32")
    combined["t_end_ms"] = combined["t_end_ms"].astype("int32")
    combined["duration_ms"] = combined["duration_ms"].astype("int32")
    combined["crosses_seam"] = combined["crosses_seam"].astype(bool)

    # Write with schema from stage2c
    table = pa.Table.from_pandas(combined, schema=PHASE_SCHEMA, preserve_index=False)
    pq.write_table(table, str(PHASES), compression="zstd")

    # Summary
    n_new = len(new_df)
    n_sessions = new_df["session_id"].nunique()
    n_total = len(combined)
    size_kb = PHASES.stat().st_size / 1e3

    print(f"\n{'=' * 60}")
    print("Stage 2f Summary")
    print(f"{'=' * 60}")
    print(f"  Exposure sessions: {n_sessions}")
    print(f"  Skipped:           {n_skipped}")
    print(f"  New phase rows:    {n_new}")
    print(f"  Total phase rows:  {n_total} (was {len(existing)})")
    print(f"  Output: {PHASES}")
    print(f"  Size: {size_kb:.1f} KB")

    # Phase type breakdown
    new_counts = new_df["phase"].value_counts()
    print(f"\n  New phases by type:")
    for phase, count in new_counts.items():
        print(f"    {phase}: {count}")

    with open(BUILD_LOG, "a") as f:
        f.write(
            f"\nStage 2f — Exposure phases"
            f" — {datetime.now().isoformat()}\n"
        )
        f.write(f"{'─' * 60}\n")
        f.write(f"  exposure_sessions: {n_sessions}\n")
        f.write(f"  skipped: {n_skipped}\n")
        f.write(f"  new_phases: {n_new}\n")
        f.write(f"  total_phases: {n_total}\n")
        f.write("\n")

    con.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
