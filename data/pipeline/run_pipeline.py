"""
Run the pipeline stages in dependency order.

Every stage is a script in this directory, run as a subprocess of the same interpreter from
the repository root. After each stage, every output file it was expected to write is checked
to have been modified during this run, so a stage that silently did nothing is a hard error.

Usage:
  python data/pipeline/run_pipeline.py                  # every stage, 1 -> 3e
  python data/pipeline/run_pipeline.py --from 2c        # 2c onward
  python data/pipeline/run_pipeline.py --only 3b,3d     # just these (in pipeline order)
  python data/pipeline/run_pipeline.py --list           # show the stage table

Stage keys: 1, 2b, 2c, 2d, 2e, 2f, 3a, 3b, 3c, 3d, 3e.

Order and dependencies:
  1   clean DB                    raw/MazeControl.db -> processed/MazeControl-clean.db
  2b  assemble tracking           tracking/<RAT>.parquet -> processed/coordinates.parquet,
                                  interim/sample_rates.parquet
  2c  phases                      clean DB + coordinates -> phases.parquet
  2d  export                      clean DB + interim sample rates -> subjects, sessions,
                                  trials, session_segments
  2e  exposure rewards            coordinates + clean DB + sessions -> exposure_rewards
  2f  exposure phases             exposure_rewards + phases -> phases.parquet (appended)
  3a  subject/session features    subjects, sessions (in place)
  3b  trial routes                trials (in place)
  3c  well visits                 trials + phases + coordinates -> trial_well_visits, trials
  3d  turn trajectories           trials + phases + coordinates -> trials (in place)
  3e  zone sequence               trials + phases + coordinates -> trial_zone_sequence
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from corner_maze.common.paths import (
    BUILD_LOG,
    CLEAN_DB,
    CLEANING_REVIEW,
    COORDINATES,
    EXPOSURE_REWARDS,
    INTERIM_DIR,
    PHASES,
    PROCESSED_DIR,
    REPO_ROOT,
    SESSION_SEGMENTS,
    SESSIONS,
    SUBJECTS,
    TRIAL_WELL_VISITS,
    TRIAL_ZONE_SEQUENCE,
    TRIALS,
)

HERE = Path(__file__).resolve().parent

# key, script, outputs that must be (re)written by the stage
STAGES: list[tuple[str, str, list[Path]]] = [
    ("1",        "stage1_clean_db.py",           [CLEAN_DB, CLEANING_REVIEW]),
    ("2b",       "stage2b_assemble.py",          [COORDINATES, INTERIM_DIR / "sample_rates.parquet"]),
    ("2c",       "stage2c_phases.py",            [PHASES]),
    ("2d",       "stage2d_export.py",            [SUBJECTS, SESSIONS, TRIALS, SESSION_SEGMENTS]),
    ("2e",       "stage2e_exposure_rewards.py",  [EXPOSURE_REWARDS]),
    ("2f",       "stage2f_exposure_phases.py",   [PHASES]),
    ("3a",       "stage3a_session_features.py",  [SUBJECTS, SESSIONS]),
    ("3b",       "stage3b_trial_routes.py",      [TRIALS]),
    ("3c",       "stage3c_well_visits.py",       [TRIAL_WELL_VISITS, TRIALS]),
    ("3d",       "stage3d_turn_trajectory.py",   [TRIALS]),
    ("3e",       "stage3e_zone_sequence.py",     [TRIAL_ZONE_SEQUENCE]),
]
KEYS = [k for k, _, _ in STAGES]
ALIASES: dict[str, list[str]] = {}


def _expand(csv: str | None) -> set[str]:
    out: set[str] = set()
    for tok in (csv or "").split(","):
        tok = tok.strip()
        if not tok:
            continue
        if tok in ALIASES:
            out.update(ALIASES[tok])
        elif tok in KEYS:
            out.add(tok)
        else:
            sys.exit(f"unknown stage key {tok!r}; valid: {', '.join(KEYS)}")
    return out


def _fmt(seconds: float) -> str:
    return f"{seconds:6.1f}s" if seconds < 600 else f"{seconds / 60:5.1f}m"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--skip", help="comma-separated stage keys to skip (e.g. 2a,2b)")
    ap.add_argument("--from", dest="from_", help="start at this stage key")
    ap.add_argument("--only", help="run only these comma-separated stage keys")
    ap.add_argument("--list", action="store_true", help="print the stage table and exit")
    args = ap.parse_args(argv)

    if args.list:
        for k, script, outs in STAGES:
            print(f"  {k:9s} {script:32s} -> {', '.join(p.name for p in outs)}")
        return 0

    selected = list(STAGES)
    if args.from_:
        if args.from_ not in KEYS:
            sys.exit(f"unknown --from key {args.from_!r}")
        selected = selected[KEYS.index(args.from_):]
    if args.only:
        only = _expand(args.only)
        selected = [s for s in selected if s[0] in only]
    skip = _expand(args.skip)
    selected = [s for s in selected if s[0] not in skip]
    if not selected:
        sys.exit("nothing to run")

    run_start = time.time()
    stamp = datetime.now().isoformat(timespec="seconds")
    plan = " ".join(k for k, _, _ in selected)
    print(f"Pipeline run {stamp}: {plan}")
    print(f"  interpreter: {sys.executable}")
    print(f"  skipped: {', '.join(sorted(skip)) or 'none'}")
    print()
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    with open(BUILD_LOG, "a") as f:
        f.write(f"\n{'=' * 60}\nPIPELINE RUN — {stamp} — stages: {plan}\n{'=' * 60}\n")

    durations: list[tuple[str, float]] = []
    for key, script, outputs in selected:
        cmd = [sys.executable, str(HERE / script)]
        print(f"{'─' * 60}\n▶ Stage {key}: {script}\n{'─' * 60}", flush=True)
        t0 = time.time()
        proc = subprocess.run(cmd, cwd=REPO_ROOT)
        dt = time.time() - t0
        durations.append((key, dt))
        if proc.returncode != 0:
            msg = f"Stage {key} ({script}) failed with exit code {proc.returncode} after {_fmt(dt)}"
            with open(BUILD_LOG, "a") as f:
                f.write(f"\nPIPELINE RUN ABORTED — {msg}\n")
            print(f"\n✖ {msg}", file=sys.stderr)
            return proc.returncode
        stale = [p for p in outputs if not p.exists() or p.stat().st_mtime < t0 - 1]
        if stale:
            names = ", ".join(str(p.relative_to(REPO_ROOT)) for p in stale)
            msg = f"Stage {key} finished but did not write: {names}"
            with open(BUILD_LOG, "a") as f:
                f.write(f"\nPIPELINE RUN ABORTED — {msg}\n")
            print(f"\n✖ {msg}", file=sys.stderr)
            return 2
        print(f"✔ Stage {key} done in {_fmt(dt)}\n", flush=True)

    total = time.time() - run_start
    print(f"{'=' * 60}\nPipeline run complete in {_fmt(total)}")
    for key, dt in durations:
        print(f"  {key:9s} {_fmt(dt)}")
    with open(BUILD_LOG, "a") as f:
        f.write(f"PIPELINE RUN COMPLETE — {datetime.now().isoformat(timespec='seconds')} — {_fmt(total).strip()}\n")
        for key, dt in durations:
            f.write(f"  {key:9s} {_fmt(dt).strip()}\n")
        f.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
