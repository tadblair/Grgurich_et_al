"""Verify that this repository reproduces itself.

Usage (from the repository root, with the environment in environment.md):
    python figures/scripts/verify.py              # all three checks
    python figures/scripts/verify.py --rebuild    # 1 only
    python figures/scripts/verify.py --stats      # 2 only
    python figures/scripts/verify.py --figures    # 3 only
    python figures/scripts/verify.py --keep       # leave the temporary directories in place

1. Rebuild. Copy the inputs (data/raw, data/tracking, data/pipeline, src, config) to a temporary
   directory, run data/pipeline/run_pipeline.py there, and compare every table in data/processed/
   with the committed one. Exact equality is required: same rows, same values, same dtypes.
2. Statistics. Run figures/scripts/check_manuscript_stats.py against figures/scripts/expected_stats.json:
   every test statistic and descriptive value the manuscript cites must appear in the committed
   notebooks' printed output.
3. Figures. Execute copies of the notebooks in a temporary directory against the committed
   data/processed/ and compare their printed output (sorted lines) and their SVGs (element ids
   normalised) with the committed ones. Needs R with afex for figure2–4 (environment.md); SVG
   identity additionally needs Helvetica, so it is reported but not fatal without it.

Exit status 0 if every requested check passed, 1 otherwise.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
NOTEBOOKS = ["figure2", "figure3", "figure4", "figure5", "supp_figure1"]
PROCESSED_TABLES = ["subjects", "sessions", "trials", "session_segments", "phases",
                    "exposure_rewards", "trial_well_visits", "trial_zone_sequence"]
DB_TABLES = ["subjects", "session", "trial", "session_event", "session_segments"]


def _fmt(seconds: float) -> str:
    return f"{seconds:.0f} s" if seconds < 600 else f"{seconds / 60:.1f} min"


def _mirror(src: Path, dst: Path, *, link: bool) -> None:
    """Copy (or symlink, for large read-only inputs) src into dst."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if link:
        os.symlink(src, dst)
    elif src.is_dir():
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", ".ipynb_checkpoints"))
    else:
        shutil.copy2(src, dst)


def frames_equal(a: pd.DataFrame, b: pd.DataFrame) -> str | None:
    """None if identical, else a one-line reason."""
    if a.shape != b.shape:
        return f"shape {a.shape} vs {b.shape}"
    if list(a.columns) != list(b.columns):
        return f"columns differ: {sorted(set(a.columns) ^ set(b.columns))}"
    try:
        pd.testing.assert_frame_equal(a.reset_index(drop=True), b.reset_index(drop=True))
        return None
    except AssertionError as e:
        return str(e).splitlines()[0][:160]


# ── 1. rebuild ───────────────────────────────────────────────────────────────
def check_rebuild(keep: bool) -> bool:
    print("═" * 72 + "\n1. Rebuild data/processed/ in a temporary directory and compare\n" + "═" * 72)
    tmp = Path(tempfile.mkdtemp(prefix="verify_rebuild_")).resolve()
    try:
        for rel in ["pyproject.toml", "src", "config", "data/pipeline"]:
            _mirror(ROOT / rel, tmp / rel, link=False)
        for rel in ["data/raw", "data/tracking"]:
            _mirror(ROOT / rel, tmp / rel, link=True)
        (tmp / "data/interim").mkdir(parents=True)
        env = dict(os.environ, PYTHONPATH=str(tmp / "src"))
        t0 = time.time()
        proc = subprocess.run([sys.executable, str(tmp / "data/pipeline/run_pipeline.py")], cwd=tmp, env=env)
        print(f"\npipeline exit code {proc.returncode} after {_fmt(time.time() - t0)}")
        if proc.returncode != 0:
            return False
        ok = True
        for t in PROCESSED_TABLES:
            a = pd.read_parquet(tmp / "data/processed" / f"{t}.parquet")
            b = pd.read_parquet(ROOT / "data/processed" / f"{t}.parquet")
            why = frames_equal(a, b)
            print(f"  {t:22s} {a.shape!s:16s} {'identical' if why is None else 'MISMATCH: ' + why}")
            ok &= why is None
        new = sqlite3.connect(tmp / "data/processed/MazeControl-clean.db")
        old = sqlite3.connect(ROOT / "data/processed/MazeControl-clean.db")
        for t in DB_TABLES:
            order = {"subjects": "subject_id", "session": "session_id", "trial": "trial_id",
                     "session_event": "session_event_id", "session_segments": "session_id, segment_idx"}[t]
            a = pd.read_sql(f"SELECT * FROM {t} ORDER BY {order}", new)
            b = pd.read_sql(f"SELECT * FROM {t} ORDER BY {order}", old)
            why = frames_equal(a, b)
            print(f"  clean DB {t:13s} {a.shape!s:16s} {'identical' if why is None else 'MISMATCH: ' + why}")
            ok &= why is None
        return ok
    finally:
        if keep:
            print(f"kept {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)


# ── 2. statistics ────────────────────────────────────────────────────────────
def check_stats() -> bool:
    print("═" * 72 + "\n2. Manuscript statistics vs the committed notebooks' output\n" + "═" * 72)
    proc = subprocess.run([sys.executable, str(ROOT / "figures/scripts/check_manuscript_stats.py"),
                           "--from-json", str(ROOT / "figures/scripts/expected_stats.json")], cwd=ROOT)
    return proc.returncode == 0


# ── 3. figures ───────────────────────────────────────────────────────────────
_ID1 = re.compile(r"\b[mp][0-9a-f]{10}\b")
_ID2 = re.compile(r"\bC[0-9a-z]+_\d+_[0-9a-f]{10}\b")
_DATE = re.compile(r"<dc:date>.*?</dc:date>")


def svg_norm(path: Path) -> str:
    s = path.read_text()
    return _DATE.sub("", _ID2.sub("ID", _ID1.sub("ID", s)))


# Printed lines that depend on the machine, not the data: font substitution notices, library
# warnings, and rpy2 reporting that its compiled extension was built against a different R
# than the one installed (it then falls back to ABI mode; the numbers are the same).
_NOISE = ("fontTools", "Warning", "Error importing in API mode", "Trying to import in ABI mode")


def nb_lines(path: Path, strip: tuple[str, ...] = ()) -> collections.Counter:
    """Printed lines as a multiset; absolute path prefixes in `strip` are removed first so a
    PosixPath(...) echoed by a cell compares equal across directories."""
    d = json.loads(path.read_text())
    out: list[str] = []
    for c in d["cells"]:
        if c["cell_type"] != "code":
            continue
        for o in c.get("outputs", []):
            if o.get("output_type") == "stream":
                text = "".join(o.get("text", []))
            elif o.get("output_type") in ("execute_result", "display_data"):
                text = "".join(o.get("data", {}).get("text/plain", []))
            else:
                continue
            for l in text.splitlines():
                l = l.strip()
                for pre in strip:
                    l = l.replace(pre + "/", "").replace(pre, ".")
                if l and not any(n in l for n in _NOISE) and not l.startswith("Saved"):
                    out.append(l)
    return collections.Counter(out)


def helvetica_available() -> bool:
    from matplotlib import font_manager
    return any("Helvetica" in f.name for f in font_manager.fontManager.ttflist)


def check_figures(keep: bool) -> bool:
    print("═" * 72 + "\n3. Re-execute the notebooks in a temporary directory and compare\n" + "═" * 72)
    tmp = Path(tempfile.mkdtemp(prefix="verify_figures_")).resolve()
    try:
        (tmp / "figures").mkdir()
        _mirror(ROOT / "figures/notebooks", tmp / "figures/notebooks", link=False)
        _mirror(ROOT / "figures/images", tmp / "figures/images", link=False)
        _mirror(ROOT / "config", tmp / "config", link=True)
        (tmp / "data").mkdir()
        _mirror(ROOT / "data/processed", tmp / "data/processed", link=True)
        ok = True
        helv = helvetica_available()
        if not helv:
            print("  Helvetica not available: SVG differences are reported but not counted as failures")
        for nb in NOTEBOOKS:
            t0 = time.time()
            proc = subprocess.run([sys.executable, "-m", "jupyter", "nbconvert", "--to", "notebook", "--execute",
                                   "--inplace", "--ExecutePreprocessor.timeout=3600", f"{nb}.ipynb"],
                                  cwd=tmp / "figures/notebooks", capture_output=True, text=True)
            if proc.returncode != 0:
                print(f"  {nb}: execution FAILED after {_fmt(time.time() - t0)}\n{proc.stderr[-2000:]}")
                ok = False
                continue
            a = nb_lines(tmp / "figures/notebooks" / f"{nb}.ipynb", strip=(str(tmp), str(tmp).removeprefix("/private")))
            b = nb_lines(ROOT / "figures/notebooks" / f"{nb}.ipynb", strip=(str(ROOT),))
            diff = sum((a - b).values()) + sum((b - a).values())
            print(f"  {nb}: executed in {_fmt(time.time() - t0)}; printed lines {sum(a.values())} vs {sum(b.values())}, "
                  f"{'identical' if diff == 0 else f'{diff} lines differ'}")
            if diff:
                ok = False
                for l, n in list((a - b).items())[:5]:
                    print(f"      + {l[:120]}")
                for l, n in list((b - a).items())[:5]:
                    print(f"      - {l[:120]}")
        for svg in sorted((ROOT / "figures/images").glob("*.svg")):
            if svg.name.startswith("fig_"):
                continue
            other = tmp / "figures/images" / svg.name
            if not other.exists():
                print(f"  {svg.name}: not written by the re-executed notebooks")
                ok = False
                continue
            same = svg_norm(svg) == svg_norm(other)
            print(f"  {svg.name}: {'identical (ids normalised)' if same else 'differs'}")
            if not same and helv:
                ok = False
        return ok
    finally:
        if keep:
            print(f"kept {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--figures", action="store_true")
    ap.add_argument("--keep", action="store_true", help="keep the temporary directories")
    args = ap.parse_args(argv)
    if not (args.rebuild or args.stats or args.figures):
        args.rebuild = args.stats = args.figures = True
    results = {}
    if args.rebuild:
        results["rebuild"] = check_rebuild(args.keep)
    if args.stats:
        results["stats"] = check_stats()
    if args.figures:
        results["figures"] = check_figures(args.keep)
    print("\n" + "═" * 72)
    for k, v in results.items():
        print(f"  {k:9s} {'PASS' if v else 'FAIL'}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
