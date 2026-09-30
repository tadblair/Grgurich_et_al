# Grgurich et al., *Path Integration Promotes Flexible Navigational Decision Making*

Data cleaning, structuring, feature extraction and statistical analysis code for Path Integration Promotes Flexible Navigational Decision Making (Grgurich R, Wang S, Wang Y, Pimenta J, Delafraz K,
Blair HT; *Hippocampus*).

## What is here

| Layer | Where | Contents |
|---|---|---|
| Raw | `data/raw/`, `data/tracking/` | the maze-control database for the 47 rats (587 sessions as logged) and the per-frame tracking (47 files, 37 MB, 38,217,231 frames) |
| Processed | `data/processed/` | the cleaned database and the tables the notebooks read: 556 sessions, 16,398 trials, every derived measure; rebuilt by `data/pipeline/run_pipeline.py` in about 23 s |
| Figures | `figures/` | the notebooks that produce Figures 2–5 (and a response-convergence figure not in the submitted manuscript), their SVG output, and the verification scripts |
| Videos | `videos/` | Videos S1–S3 |

`data/README.md` is the data dictionary. `environment.md` lists versions and install steps.

## Quick start

```bash
git clone https://github.com/tadblair/Grgurich_et_al.git
cd Grgurich_et_al
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

Figures 2–4 also need R with the afex package (see `environment.md`). Then:

```bash
python figures/scripts/verify.py --stats     # every statistic the paper cites is in the notebooks' output (seconds)
python data/pipeline/run_pipeline.py         # rebuild data/processed/ from data/raw/ and data/tracking/ (about 23 s)
python figures/scripts/verify.py --rebuild   # rebuild into a temporary directory and compare with the committed tables
cd figures/notebooks
python -m jupyter nbconvert --to notebook --execute --inplace figure2.ipynb   # likewise figure3, figure4, figure5, supp_figure1
```

## Figures

| Figure | Notebook | Writes | Needs R |
|---|---|---|---|
| 2, acquisition | `figures/notebooks/figure2.ipynb` | `figures/images/figure2.svg` | yes |
| 3, Novel Route probe | `figures/notebooks/figure3.ipynb` | `figure3.svg` and `figure3_median.svg` (median variant of the latency panels) | yes |
| 4, Reversal probe | `figures/notebooks/figure4.ipynb` | `figure4.svg` | yes |
| 5, individual differences | `figures/notebooks/figure5.ipynb` | `figure5.svg` | no |
| response convergence (not in the submitted manuscript) | `figures/notebooks/supp_figure1.ipynb` | `supp_figure1.svg` | no |

Each notebook selects the cohort from `config/subjects.yaml`, reads `data/processed/`,
prints every statistic the paper cites, and writes its figure. The five schematic panels the
notebooks splice into the figures (`figures/images/fig_*.svg`) were drawn by hand. The
notebooks ship executed, so their printed output can be read on GitHub without running them.

## Cohort

| Group | n | Used in |
|---|---|---|
| PI+VC | 17 | Figures 2–5 |
| PI | 12 | Figures 2–5 |
| VC | 12 | Figures 2–5 |
| PI+VC_f1 | 6 | Figure 2G (the "F1" group) |

A rat is in the cohort if it made at least 24 of 32 correct trials on its final acquisition
session (chance probability ≤ 0.01 under a binomial with p = 0.5).

## The pipeline

`python data/pipeline/run_pipeline.py` runs the stages below in order, each as a separate
process, and refuses to finish if a stage did not rewrite its outputs. `--from`, `--only`
and `--skip` select stages; `--list` prints the table.

| Stage | Script | Reads | Writes |
|---|---|---|---|
| 1 | `stage1_clean_db.py` | `data/raw/MazeControl.db`, `config/` | `processed/MazeControl-clean.db` (voided sessions dropped, split recordings merged, `session_segments` added), `cleaning_review.txt` |
| 2b | `stage2b_assemble.py` | `data/tracking/*.parquet` | `processed/coordinates.parquet` (all rats, not committed), `interim/sample_rates.parquet` |
| 2c | `stage2c_phases.py` | clean DB, coordinates | `phases.parquet`: the time window of every trial and inter-trial interval |
| 2d | `stage2d_export.py` | clean DB, sample rates | `subjects`, `sessions`, `trials`, `session_segments` |
| 2e, 2f | `stage2e_exposure_rewards.py`, `stage2f_exposure_phases.py` | coordinates, clean DB | `exposure_rewards.parquet`; exposure phases appended to `phases.parquet` |
| 3a | `stage3a_session_features.py` | subjects, sessions | training group, probe conditions, approach-to-goal, experiment phase, session order |
| 3b | `stage3b_trial_routes.py` | trials | `correct_route`, `actual_route` |
| 3c | `stage3c_well_visits.py` | trials, phases, coordinates | `trial_well_visits.parquet`; first-choice latency, reconstructed goal-zone visits |
| 3d | `stage3d_turn_trajectory.py` | trials, phases, coordinates | turns and routes read from the tracking |
| 3e | `stage3e_zone_sequence.py` | trials, phases, coordinates | `trial_zone_sequence.parquet`: the run-length-encoded zone trace of every trial |

The scripts are the ones that built the tables in the paper, with two changes for this
repository: `stage2b_assemble.py` stands in for the stages that read the rig's CSVs, and
Stage 1 no longer filters subjects, because the database here holds only the 47 paper rats.

## Verification

`figures/scripts/verify.py` runs three checks and exits non-zero if any fails:

- `--rebuild`: run the pipeline in a temporary directory and compare every table with the
  committed `data/processed/` (exact equality).
- `--stats`: `check_manuscript_stats.py` against `expected_stats.json`, the 61 test
  statistics and 115 descriptive values the manuscript cites, extracted from its text.
  Each must appear in the notebooks' printed output at the manuscript's precision.
- `--figures`: execute copies of the notebooks against the committed data and compare their
  printed output and SVGs with the committed ones.

## Videos

`videos/` holds Videos S1–S3 with their legends and the sessions they were cut from.

## Licenses

Code: MIT (`LICENSE`). Data and videos: CC BY 4.0 (`data/LICENSE`). Cite the article when you
use either; `CITATION.cff` has the reference in machine-readable form.

## Contact

Ryan Grgurich (first author) or Hugh T. Blair (corresponding author, tadblair@g.ucla.edu),
UCLA Psychology Department. Issues on this repository are welcome.

Built 2026-09-29 from the Blair Lab development repository.
