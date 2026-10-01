#!/usr/bin/env bash
# Set up this repository on macOS or Linux: a Python virtual environment in .venv with the
# pinned packages, then a check for R with afex and emmeans (needed by the Figure 2-4 notebooks
# only). Safe to re-run. On Windows follow the manual steps in README.md.
#
#   ./setup.sh
#   source .venv/bin/activate

set -euo pipefail
cd "$(dirname "$0")"

# ── Python ≥ 3.11; prefer 3.12, the version the release was built with ─────────────────────
PY=""
for cand in python3.12 python3.11 python3.13 python3; do
    if command -v "$cand" >/dev/null 2>&1 &&
       "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
        PY="$cand"; break
    fi
done
if [ -z "$PY" ]; then
    echo "No Python 3.11 or later found on PATH. Install one (3.12 recommended) and re-run." >&2
    exit 1
fi
echo "Python: $($PY --version) ($(command -v "$PY"))"

# ── Virtual environment and packages ────────────────────────────────────────────────────────
if [ ! -x .venv/bin/python ]; then
    "$PY" -m venv .venv
    echo "Created .venv"
else
    echo "Reusing .venv ($(.venv/bin/python --version))"
fi
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -r requirements.txt
.venv/bin/python -m pip install --quiet -e .
echo "Installed requirements.txt and the corner_maze package"

# ── R with afex and emmeans (Figures 2-4) ───────────────────────────────────────────────────
R_OK=0
if command -v Rscript >/dev/null 2>&1; then
    echo "R: $(Rscript -e 'cat(R.version.string)' 2>/dev/null)"
    if Rscript -e 'suppressMessages({library(afex); library(emmeans)})' >/dev/null 2>&1; then
        echo "R packages afex and emmeans: found"
        R_OK=1
    else
        echo "R packages afex and emmeans: missing. In R run:  install.packages(c(\"afex\", \"emmeans\"))"
    fi
else
    echo "R: not found on PATH (needed for the Figure 2, 3 and 4 notebooks; install R, then afex and emmeans)"
fi

if [ "$R_OK" = 1 ]; then
    # rpy2 must be able to load this R. A "Trying to import in ABI mode" message here is
    # harmless (see README.md, Known issues); a failure means R_HOME needs setting.
    if .venv/bin/python -c 'import rpy2.robjects' >/dev/null 2>&1; then
        echo "rpy2 can load R"
    else
        echo "rpy2 could not load R. Set R_HOME to the output of 'R RHOME' and re-run." >&2
        R_OK=0
    fi
fi

# ── Summary ─────────────────────────────────────────────────────────────────────────────────
echo
echo "Ready. Activate the environment with:  source .venv/bin/activate"
echo "  pipeline (data/pipeline/run_pipeline.py): yes"
echo "  Figure 5 and Supplementary Figure 1 notebooks: yes"
if [ "$R_OK" = 1 ]; then
    echo "  Figure 2, 3 and 4 notebooks: yes"
else
    echo "  Figure 2, 3 and 4 notebooks: not until R with afex and emmeans is installed"
fi
