"""
Stage 2b (release): assemble coordinates.parquet from the per-rat tracking files.

The development repository builds the tracking from the rig's per-frame CSVs in three
stages (2b-auto, 2b-merge, 2b-final: reading, interpolating corrupt rows, stitching split
recordings, assigning zones). This repository ships their result as one file per rat in
data/tracking/. This stage puts those files back together in the single file the later
stages read, and derives each session's sample rate the way stage 2b-auto did (median
frame interval 33 / 67 / 100 ms -> 30 / 15 / 10 Hz).

Inputs:
  data/tracking/<RAT>.parquet   session_id, frame_idx, t_ms, x, y, zone, source_segment

Outputs:
  data/processed/coordinates.parquet   all rats, sorted by session_id, t_ms (not committed)
  data/interim/sample_rates.parquet    session_id, sample_rate_hz, n_samples, median_dt_ms
  data/processed/build_log.txt         (appended)

Run:
  python data/pipeline/stage2b_assemble.py
"""

from __future__ import annotations

from datetime import datetime

import duckdb

from corner_maze.common.paths import BUILD_LOG, COORDINATES, INTERIM_DIR, PROCESSED_DIR, TRACKING_DIR

EXPECTED_DT_MS = {33: 30, 67: 15, 100: 10}  # median dt (ms) -> sample rate (Hz), as in stage 2b-auto
SAMPLE_RATE_TOLERANCE_MS = 5


def rate_from_median_dt(median_dt: float) -> int:
    for expected_dt, rate in EXPECTED_DT_MS.items():
        if abs(median_dt - expected_dt) <= SAMPLE_RATE_TOLERANCE_MS:
            return rate
    raise ValueError(f"Unexpected median dt={median_dt:.1f}ms")


def main() -> None:
    print("Stage 2b (release) — Assemble coordinates.parquet from data/tracking/")
    files = sorted(TRACKING_DIR.glob("*.parquet"))
    if not files:
        raise SystemExit(f"no tracking files in {TRACKING_DIR}")
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    INTERIM_DIR.mkdir(parents=True, exist_ok=True)
    print(f"  {len(files)} rats: {files[0].stem} … {files[-1].stem}")

    con = duckdb.connect()
    glob = str(TRACKING_DIR / "*.parquet")
    con.execute(f"""
        COPY (SELECT * FROM read_parquet('{glob}') ORDER BY session_id, t_ms)
        TO '{COORDINATES}' (FORMAT PARQUET, COMPRESSION ZSTD)
    """)
    n_rows, n_sessions = con.execute(
        f"SELECT COUNT(*), COUNT(DISTINCT session_id) FROM '{COORDINATES}'"
    ).fetchone()
    print(f"  Written {COORDINATES.name}: {n_rows:,} rows, {n_sessions} sessions, "
          f"{COORDINATES.stat().st_size / 1e6:.1f} MB")

    sr = con.execute(f"""
        SELECT session_id, median(dt) AS median_dt_ms, COUNT(*) + 1 AS n_samples
        FROM (
            SELECT session_id, t_ms - lag(t_ms) OVER (PARTITION BY session_id ORDER BY t_ms) AS dt
            FROM '{COORDINATES}'
        )
        WHERE dt IS NOT NULL
        GROUP BY session_id
        ORDER BY session_id
    """).df()
    sr["sample_rate_hz"] = sr["median_dt_ms"].map(rate_from_median_dt)
    sr["median_dt_ms"] = sr["median_dt_ms"].astype(float).round(1)
    sr = sr[["session_id", "sample_rate_hz", "n_samples", "median_dt_ms"]].astype(
        {"session_id": "int64", "sample_rate_hz": "int64", "n_samples": "int64"}
    )
    out = INTERIM_DIR / "sample_rates.parquet"
    sr.to_parquet(str(out), engine="pyarrow", index=False)
    rates = sr["sample_rate_hz"].value_counts().sort_index().to_dict()
    print(f"  Written {out.name}: {len(sr)} sessions; sessions per rate (Hz): {rates}")

    with open(BUILD_LOG, "a") as f:
        f.write(f"\n[{datetime.now().isoformat(timespec='seconds')}] Stage 2b (release) assemble: "
                f"{len(files)} tracking files -> coordinates.parquet ({n_rows:,} rows, {n_sessions} sessions); "
                f"sample_rates.parquet ({len(sr)} sessions)\n")
    print("\nDone.")


if __name__ == "__main__":
    main()
