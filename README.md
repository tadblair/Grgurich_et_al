# Grgurich et al., *Path Integration Promotes Flexible Navigational Decision Making*

Repository containing data and analysis code for Grgurich et al. 2026, *Path Integration Promotes Flexible Navigational Decision Making*. The repository holds the raw behavioral records of the 47 rats used in the study, the data cleaning and feature extraction pipelines, statistics analysis and figures. 

<!-- DOI badge once the article and the archived release have one -->

## Citation

Grgurich R, Wang S, Wang Y, Pimenta J, Delafraz K, Blair HT. Path Integration Promotes Flexible
Navigational Decision Making. bioRxiv. 2026. doi:10.1101/2026.MM.DD.NNNNNN

`CITATION.cff` has the same reference in machine-readable form.

## Setup

Python 3.11 or later (the release was built with 3.12.11). The Figure 2, 3 and 4 notebooks
also need R with the afex and emmeans packages; the pipeline and the other notebooks do not.

On macOS or Linux, one script builds the environment and reports what it found:

```bash
git clone https://github.com/tadblair/Grgurich_et_al.git
cd Grgurich_et_al
./setup.sh
source .venv/bin/activate
```

`setup.sh` creates `.venv` with the newest Python 3.11+ on your PATH (3.12 preferred), installs
the versions pinned in `requirements.txt` and the `corner_maze` package from `src/`, then checks
for R, afex and emmeans and says which notebooks will run. It is safe to re-run.

The same by hand, which is also the Windows route:

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install -e .
```

For R, install it from <https://cran.r-project.org> and then, in R:

```r
install.packages(c("afex", "emmeans"))
```

`environment.md` lists the exact versions the release was verified with and how rpy2 finds R.

## Figures and statistics

One notebook per figure. Each reads its rats from `config/subjects.yaml` and its data from
`data/processed/`, prints every statistic the paper cites for that figure, and writes the
figure as SVG.

| Figure | Notebook | Writes | Needs R |
|---|---|---|---|
| 2, acquisition | `figures/notebooks/figure2.ipynb` | `figures/images/figure2.svg` | yes |
| 3, Novel Route probe | `figure3.ipynb` | `figure3.svg`, `figure3_median.svg` (median variant of the latency panels) | yes |
| 4, Reversal probe | `figure4.ipynb` | `figure4.svg` | yes |
| 5, individual differences | `figure5.ipynb` | `figure5.svg` | no |
| S1, response convergence | `supp_figure1.ipynb` | `supp_figure1.svg` | no |

Supplementary Figure 1 is not in the submitted manuscript. It shows the convergence of
responses over acquisition and justifies the inclusion criterion below.

The notebooks are committed executed, so their printed output can be read on GitHub without
running anything. Each runs in seconds. To run one:

```bash
cd figures/notebooks
python -m jupyter nbconvert --to notebook --execute --inplace figure2.ipynb
```

or start Jupyter from `figures/notebooks/` and open it there; the notebooks find the
repository root two levels up from their own directory. Three schematic panels in Figure 2
and one each in Figures 3 and 4 (`figures/images/fig_*.svg`) were drawn by hand; the
notebooks splice them in.

To check the printed statistics against the manuscript:

```bash
python figures/scripts/verify.py --stats      # no R needed
```

This looks for every statistic the manuscript cites, 61 test statistics and 115 descriptive
values (`figures/scripts/expected_stats.json`, extracted from the manuscript text), in the
committed notebooks' output at the manuscript's precision. `verify.py --figures` goes further:
it re-executes the notebooks in a temporary directory and compares their printed output and
SVGs with the committed ones.

**Subjects.** `config/subjects.yaml` lists the 47 rats by figure and group:

| Group | n | Used in |
|---|---|---|
| PI+VC | 17 | Figures 2–5 |
| PI | 12 | Figures 2–5 (split by Novel Route condition in Figures 3 and 4) |
| VC | 12 | Figures 2–5 (split by probe condition in Figures 3 and 4) |
| PI+VC_f1 | 6 | Figure 2G |

A rat is in the cohort if it made at least 24 of 32 correct trials on its final acquisition
session, the single-session score whose chance probability is below 0.01 under a binomial
with p = 0.5.

## Rebuilding the tables

Everything in `data/processed/` is derived from `data/raw/` and `data/tracking/` by the
pipeline. A full run takes well under a minute on a laptop:

```bash
python data/pipeline/run_pipeline.py            # all stages, in order
python data/pipeline/run_pipeline.py --list     # the stage table
python data/pipeline/run_pipeline.py --only 3c  # one stage; also --from and --skip
```

Each stage runs as its own process and the run refuses to finish if a stage did not rewrite
its outputs. To prove the committed tables are what the pipeline produces:

```bash
python figures/scripts/verify.py --rebuild      # rebuild in a temporary directory, compare
```

Every table must match exactly: same rows, same values, same dtypes.

### What the pipeline does

Three steps take the rig's records to the tables the notebooks read; the notebooks are the
fourth.

**1. Data cleaning** (Stage 1). The raw database holds every session the rig logged for the
47 rats, including ones the experimenter voided at the time (`session_number = 'X'`, or a
null duration after an aborted start) and three sessions recorded in two parts. Stage 1
copies the database, drops the voided sessions, merges the split recordings, and writes a
review file listing every session it thinks worth a look. The raw database is never edited.
Per-session notes and overrides live in `config/subject_exceptions.yaml`.

**2. Structuring** (Stages 2b–2f). The per-rat tracking files are assembled into one frame
table; the event log is turned into the time window of every trial, inter-trial interval and
exposure phase (`phases.parquet`); and the cleaned database is exported as tidy tables
(`subjects`, `sessions`, `trials`, `session_segments`).

**3. Feature extraction** (Stages 3a–3e). The measures the paper analyses are computed and
added to those tables: training group and probe conditions per rat; experiment phase and
chronological order per session; and, per trial, the correct and actual two-turn route, the
turns and route read from the tracking, the latency to the first well, every well visit, and
the run-length-encoded sequence of maze zones.

**4. Statistical analysis** (the notebooks). Cohort selection, the tests, and the figures.

| Stage | Script | Reads | Writes |
|---|---|---|---|
| 1 | `stage1_clean_db.py` | `raw/MazeControl.db`, `config/subject_exceptions.yaml` | `processed/MazeControl-clean.db`, `cleaning_review.txt` |
| 2b | `stage2b_assemble.py` | `tracking/*.parquet` | `processed/coordinates.parquet` (not committed, 200 MB), `interim/sample_rates.parquet` |
| 2c | `stage2c_phases.py` | clean DB, coordinates | `phases.parquet` |
| 2d | `stage2d_export.py` | clean DB, sample rates | `subjects`, `sessions`, `trials`, `session_segments` |
| 2e, 2f | `stage2e_exposure_rewards.py`, `stage2f_exposure_phases.py` | coordinates, clean DB | `exposure_rewards.parquet`; exposure phases appended to `phases.parquet` |
| 3a | `stage3a_session_features.py` | subjects, sessions | group, probe conditions, approach-to-goal, experiment phase, session order |
| 3b | `stage3b_trial_routes.py` | trials | `correct_route`, `actual_route` |
| 3c | `stage3c_well_visits.py` | trials, phases, coordinates | `trial_well_visits.parquet`; `first_choice_latency_s` |
| 3d | `stage3d_turn_trajectory.py` | trials, phases, coordinates | `coord_turn_1`, `coord_turn_2`, `coord_route`, `second_turn_toward_goal` |
| 3e | `stage3e_zone_sequence.py` | trials, phases, coordinates | `trial_zone_sequence.parquet` |

The scripts are the ones that built the tables in the paper, with two changes for this
repository: `stage2b_assemble.py` stands in for the stages that read the rig's per-frame CSVs
(their result, `data/tracking/`, ships instead), and Stage 1 no longer filters subjects,
because the database here holds only the 47 paper rats.

### Where to look

| To see | Look at |
|---|---|
| every table and column, with the units and how each was derived | `data/README.md` |
| how a session was cleaned, merged or flagged | `data/pipeline/stage1_clean_db.py`, `config/subject_exceptions.yaml`, `data/processed/cleaning_review.txt` |
| how a per-trial measure is computed | `src/corner_maze/features/` (`correct_route.py`, `actual_route.py`, `well_visits.py`, `turn_trajectory.py`, `zone_sequence.py`, …), called from the Stage 3 scripts |
| the maze zone map (zone id from x, y) | `src/corner_maze/common/zones.py` |
| where every file lives | `src/corner_maze/common/paths.py` |
| which rats are in which figure and group | `config/subjects.yaml` |
| what each stage printed when the committed tables were built | `data/processed/build_log.txt` |

## Known issues

- **One session has no tracking:** CM008 Exposure session 1e (`session_id` 1456). No figure
  uses it.
- **rpy2 and R versions.** If the rpy2 wheel was built against a different R than the one
  installed, importing it prints "Error importing in API mode … Trying to import in ABI
  mode." That is harmless: the numbers are the same. `verify.py` ignores those lines.
- **Fonts.** The figures use Helvetica. Without it matplotlib falls back to DejaVu Sans, the
  numbers are unchanged, and the SVGs differ in glyph widths; `verify.py` then compares the
  printed numbers and reports, but does not fail on, the SVG difference.
- **Re-executed notebooks print absolute paths** in their "Saved SVG" lines. The committed
  notebooks have them relative to the repository root.

## Videos

`videos/` holds Videos S1–S3 with their legends and the sessions they were cut from.

## Licenses

Code: MIT (`LICENSE`). Data and videos: CC BY 4.0 (`data/LICENSE`). Cite the article when you
use either; `CITATION.cff` has the reference in machine-readable form.

## Contact

Ryan Grgurich (first author) or Hugh T. Blair (corresponding author, tadblair@g.ucla.edu),
UCLA Psychology Department. Issues on this repository are welcome.
