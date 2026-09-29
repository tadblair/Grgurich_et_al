# Environment

The release was built and checked with the versions below on macOS (2026-09-28). The
pipeline is Python only. Figures 2, 3 and 4 fit their mixed ANOVAs with the R package afex,
called from Python through rpy2; Figure 5 and Supplementary Figure 1 are Python only.

## Python

Python 3.12.11. Exact package versions are pinned in `requirements.txt`:

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install -e .                   # the corner_maze package in src/
```

`pip install -e .` alone (no requirements file) is enough to run the pipeline; the notebooks
need the full set.

## R (Figures 2–4 only)

R 4.5.2 with afex 1.5.0 and emmeans 2.0.2:

```r
install.packages(c("afex", "emmeans"))
```

rpy2 locates R through `R_HOME` or the `R` executable on `PATH`. If `import rpy2.robjects`
fails, set `R_HOME` to the value printed by `R RHOME`. The notebooks call afex only in the
cells marked as such; every other statistic is computed in Python.

## Fonts

The figures use Helvetica. On macOS the notebooks' style cells load the system `Helvetica.ttc`
(including its bold face, which matplotlib otherwise silently drops). Elsewhere matplotlib
falls back to DejaVu Sans: numbers, layout logic and panel content are unchanged, but glyph
widths differ, so the SVG output will not be byte-identical to the committed files. `verify.py`
therefore compares printed numbers everywhere and SVGs only when Helvetica is available.

## Run times on the build machine

| Step | Time |
|---|---|
| `data/pipeline/run_pipeline.py` (stages 1, 2b, 2c–3e) | 23 s |
| `figures/notebooks/figure2.ipynb` | 7 s |
| `figures/notebooks/figure3.ipynb` | 6 s |
| `figures/notebooks/figure4.ipynb` | 15 s |
| `figures/notebooks/figure5.ipynb` | 20 s |
| `figures/notebooks/supp_figure1.ipynb` | 2 s |

## Jupyter

The notebooks were executed with `jupyter nbconvert --execute --inplace` from the
`figures/notebooks/` directory; they locate the repository root two levels up from their own
directory, so run them from there (or open them in Jupyter with that working directory).
