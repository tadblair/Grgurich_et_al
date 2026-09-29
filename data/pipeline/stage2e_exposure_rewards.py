"""
Stage 2e: Reconstruct reward events for exposure sessions.

Exposure sessions (1e and 2e) lack standard trial structure. Rats freely
explore and collect rewards from 4 corner wells under a cycle-alternation
rule: all 4 wells start available, each visited well is consumed, and the
set resets once all 4 have been visited.

The logged session_event timestamps are delayed by a Gaussian pause
(mean=45s, sd=10s) from the maze control software, so the logged time is
when reward was dispensed, not when the rat arrived. This stage
reconstructs actual reward times by forward-scanning the coordinate zone
data for well-zone entries.

Session variants:
  1e: 32 rewards — all non-presession events are reward events
  2e: 33 rewards — first 6 non-presession events are barrier-drop
      acclimation, rewards start at event index 7 (0-based, including
      the presession row)

Inputs:
  data/processed/coordinates.parquet
  data/processed/MazeControl-clean.db  (session_event table)
  data/processed/sessions.parquet

Outputs:
  data/processed/exposure_rewards.parquet

Run:
  python data/pipeline/stage2e_exposure_rewards.py
"""

from __future__ import annotations

from datetime import datetime

import duckdb
import numpy as np
import pandas as pd

from corner_maze.common.paths import (
    BUILD_LOG,
    CLEAN_DB,
    COORDINATES,
    EXPOSURE_REWARDS,
    PROCESSED_DIR,
    SESSIONS,
)

# Zone -> well index; well index -> name
ZONE_TO_WELL = {1: 0, 5: 1, 17: 2, 21: 3}  # SW, NW, SE, NE
WELL_ZONES = frozenset(ZONE_TO_WELL)
WELL_NAMES = ["SW", "NW", "SE", "NE"]

# Expected reward counts by session number
EXPECTED_REWARDS = {"1e": 32, "2e": 33}

# Minimum inter-reward gap in milliseconds (empirical estimate)
MIN_REWARD_GAP_MS = 12_000


def _load_session_events(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Load session_event rows for all exposure sessions."""
    return con.execute("""
        SELECT
            CAST(se.session_id AS INT) AS session_id,
            CAST(se.time_stamp AS DOUBLE) AS t_logged_ms,
            CAST(se.zone AS INT) AS logged_zone
        FROM clean.session_event se
        JOIN clean.session s USING (session_id)
        WHERE s.session_type = 'Exposure'
        ORDER BY CAST(se.session_id AS INT), CAST(se.session_event_id AS INT)
    """).fetchdf()


def _load_exposure_sessions(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Load session metadata for exposure sessions."""
    return con.execute(f"""
        SELECT session_id, session_number
        FROM '{SESSIONS}'
        WHERE session_number IN ('1e', '2e')
        ORDER BY session_id
    """).fetchdf()


def _reconstruct_session(
    zone: np.ndarray,
    t_ms: np.ndarray,
    n_rewards: int,
    logged_ts: np.ndarray,
    start_after_ms: float = 0.0,
) -> list[dict]:
    """Forward-scan zone data to reconstruct reward events for one session.

    Args:
        zone: Array of zone IDs from coordinates (int8).
        t_ms: Array of timestamps in ms from coordinates.
        n_rewards: Number of rewards to find.
        logged_ts: Array of logged reward timestamps from session_event
                   (already filtered to reward events only).
        start_after_ms: Earliest time a reward can fire (e.g. after
                        the last acclimation event in 2e sessions).

    Returns:
        List of dicts, one per reward, with keys:
          reward_idx, cycle, well_zone, well_name, t_entry_ms,
          t_logged_ms, dwell_ms, wells_available.
    """
    wells_remaining = {0, 1, 2, 3}
    cycle = 0
    cursor = 0
    prev_reward_ts = max(-np.inf, start_after_ms - MIN_REWARD_GAP_MS)
    results = []

    for k in range(n_rewards):
        well_idx = None
        well_entry_ts = None
        well_zone = None
        dwell_ms = None
        min_ts = prev_reward_ts + MIN_REWARD_GAP_MS

        for j in range(cursor, len(zone)):
            if t_ms[j] < min_ts:
                continue
            z = int(zone[j])
            if z in ZONE_TO_WELL:
                idx = ZONE_TO_WELL[z]
                if idx in wells_remaining:
                    well_idx = idx
                    well_entry_ts = float(t_ms[j])
                    well_zone = z
                    # Dwell: how long the rat stays in this zone
                    stay = j + 1
                    while stay < len(zone) and zone[stay] == z:
                        stay += 1
                    exit_ts = float(t_ms[min(stay, len(zone) - 1)])
                    dwell_ms = exit_ts - well_entry_ts
                    cursor = j + 1
                    break

        # Record availability BEFORE consuming this well
        avail = "".join(
            WELL_NAMES[w] + "," for w in range(4) if w in wells_remaining
        ).rstrip(",")

        t_logged = float(logged_ts[k]) if k < len(logged_ts) else None

        results.append({
            "reward_idx": k,
            "cycle": cycle,
            "well_zone": well_zone,
            "well_name": WELL_NAMES[well_idx] if well_idx is not None else None,
            "t_entry_ms": well_entry_ts,
            "t_logged_ms": t_logged,
            "dwell_ms": dwell_ms,
            "wells_available": avail,
        })

        if well_idx is not None:
            wells_remaining.discard(well_idx)
            prev_reward_ts = well_entry_ts

        # Reset cycle when all 4 wells consumed
        if not wells_remaining:
            wells_remaining = {0, 1, 2, 3}
            cycle += 1

    return results


def main() -> None:
    print("Stage 2e — Reconstruct exposure reward events")
    print()

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    # Connect to DB for session_event data
    con = duckdb.connect()
    con.execute("INSTALL sqlite; LOAD sqlite;")
    con.execute("SET sqlite_all_varchar=true")
    con.execute(f"ATTACH '{CLEAN_DB}' AS clean (TYPE sqlite)")

    events_df = _load_session_events(con)
    sessions_df = _load_exposure_sessions(con)

    # Load acclimation event timestamps for 2e sessions (barrier-drops).
    # The last acclimation timestamp marks the earliest a reward can fire.
    acclim_df = con.execute("""
        SELECT
            CAST(se.session_id AS INT) AS session_id,
            MAX(CAST(se.time_stamp AS DOUBLE)) AS last_acclim_ms
        FROM clean.session_event se
        JOIN clean.session s USING (session_id)
        WHERE s.session_type = 'Exposure'
          AND s.session_number = '2e'
          AND se.action_vector_type NOT IN ('Presession', 'Exp Trial')
        GROUP BY se.session_id
    """).fetchdf()
    acclim_end = dict(
        zip(acclim_df["session_id"], acclim_df["last_acclim_ms"])
    )

    # Load coordinates for all exposure sessions at once
    exposure_ids = sessions_df["session_id"].tolist()
    coords_df = con.execute(f"""
        SELECT session_id, t_ms, zone
        FROM '{COORDINATES}'
        WHERE session_id IN ({','.join(str(s) for s in exposure_ids)})
        ORDER BY session_id, frame_idx
    """).fetchdf()

    all_rows = []
    n_skipped = 0
    n_incomplete = 0

    for _, sess in sessions_df.iterrows():
        sid = int(sess["session_id"])
        snum = sess["session_number"]

        # Get coordinate data for this session
        cmask = coords_df["session_id"] == sid
        sc = coords_df.loc[cmask]
        if sc.empty:
            print(f"  SKIP session {sid} ({snum}): no coordinate data")
            n_skipped += 1
            continue

        zone = sc["zone"].values.astype(np.int8)
        t_ms = sc["t_ms"].values.astype(np.int64)

        # Get session_event data — filter out presession row (t=0) and
        # for 2e, skip the 6 barrier-drop acclimation events
        emask = events_df["session_id"] == sid
        se = events_df.loc[emask].copy()
        # Remove presession row (time_stamp ≈ 0)
        se = se[se["t_logged_ms"] > 1.0]

        if snum == "2e":
            # First 6 non-presession events are barrier-drop, skip them
            se = se.iloc[6:]

        n_rewards_expected = EXPECTED_REWARDS[snum]
        n_rewards_available = len(se)

        if n_rewards_available < n_rewards_expected:
            if n_rewards_available == 0:
                print(f"  SKIP session {sid} ({snum}): no reward events in DB")
                n_skipped += 1
                continue
            print(
                f"  WARN session {sid} ({snum}): {n_rewards_available} "
                f"reward events (expected {n_rewards_expected})"
            )
            n_incomplete += 1

        n_rewards = min(n_rewards_available, n_rewards_expected)
        logged_ts = se["t_logged_ms"].values

        # For 2e, rewards can't fire until after the last barrier-drop
        start_after = acclim_end.get(sid, 0.0) if snum == "2e" else 0.0
        rewards = _reconstruct_session(
            zone, t_ms, n_rewards, logged_ts, start_after_ms=start_after
        )

        for r in rewards:
            r["session_id"] = sid
            all_rows.append(r)

    if not all_rows:
        print("\n  ERROR: no reward events reconstructed")
        con.close()
        return

    # Build output DataFrame
    out = pd.DataFrame(all_rows)
    out = out[
        [
            "session_id",
            "reward_idx",
            "cycle",
            "well_zone",
            "well_name",
            "t_entry_ms",
            "t_logged_ms",
            "dwell_ms",
            "wells_available",
        ]
    ]

    # Downcast types
    out["session_id"] = out["session_id"].astype("int32")
    out["reward_idx"] = out["reward_idx"].astype("int16")
    out["cycle"] = out["cycle"].astype("int16")
    out["well_zone"] = out["well_zone"].astype("Int8")  # nullable
    out["t_entry_ms"] = out["t_entry_ms"].astype("Float64")  # nullable
    out["t_logged_ms"] = out["t_logged_ms"].astype("Float64")  # nullable
    out["dwell_ms"] = out["dwell_ms"].astype("Float64")  # nullable

    out.to_parquet(str(EXPOSURE_REWARDS), engine="pyarrow", index=False)

    # Validation
    n_sessions = out["session_id"].nunique()
    n_rewards_total = len(out)
    n_found = out["well_zone"].notna().sum()
    n_missing = out["well_zone"].isna().sum()

    # Summary
    print(f"\n{'=' * 60}")
    print("Stage 2e Summary")
    print(f"{'=' * 60}")
    print(f"  Sessions processed: {n_sessions}")
    print(f"  Sessions skipped:   {n_skipped}")
    print(f"  Sessions incomplete: {n_incomplete}")
    print(f"  Total rewards:      {n_rewards_total}")
    print(f"  Wells identified:   {n_found}")
    print(f"  Wells missing:      {n_missing}")
    print(f"  Output: {EXPOSURE_REWARDS}")
    print(f"  Size: {EXPOSURE_REWARDS.stat().st_size / 1e3:.1f} KB")

    # Per-session reward count distribution
    per_sess = out.groupby("session_id").size()
    print(f"\n  Rewards per session: min={per_sess.min()}, "
          f"median={per_sess.median():.0f}, max={per_sess.max()}")

    # Cycle count distribution
    max_cycles = out.groupby("session_id")["cycle"].max()
    print(f"  Cycles per session: min={max_cycles.min()}, "
          f"median={max_cycles.median():.0f}, max={max_cycles.max()}")

    with open(BUILD_LOG, "a") as f:
        f.write(
            f"\nStage 2e — Exposure reward reconstruction"
            f" — {datetime.now().isoformat()}\n"
        )
        f.write(f"{'─' * 60}\n")
        f.write(f"  sessions: {n_sessions}\n")
        f.write(f"  skipped: {n_skipped}\n")
        f.write(f"  incomplete: {n_incomplete}\n")
        f.write(f"  rewards: {n_rewards_total}\n")
        f.write(f"  wells_identified: {n_found}\n")
        f.write(f"  wells_missing: {n_missing}\n")
        f.write("\n")

    con.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
