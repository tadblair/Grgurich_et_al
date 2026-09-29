"""Canonical filesystem paths for the corner_maze pipeline.

Every stage and helper imports paths from here. No hardcoded paths
should appear anywhere else in the package — if you need a new
directory or file, add it as a constant in this module first.

Paths are resolved relative to the repository root, which is
located by walking upward from this file until a directory
containing 'pyproject.toml' is found.
"""

from __future__ import annotations

from pathlib import Path


def _find_repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        if (parent / "pyproject.toml").exists():
            return parent
    # Fallback: assume src/corner_maze/common/paths.py → repo root is 3 up.
    return here.parents[3]


REPO_ROOT = _find_repo_root()

# Top-level directories
DATA_DIR = REPO_ROOT / "data"
CONFIG_DIR = REPO_ROOT / "config"
DOCS_DIR = REPO_ROOT / "docs"

# Data subdirectories
RAW_DIR = DATA_DIR / "raw"
INTERIM_DIR = DATA_DIR / "interim"
PROCESSED_DIR = DATA_DIR / "processed"
TRACKING_DIR = DATA_DIR / "tracking"   # per-rat coordinate files (release input)

# Specific source files
RAW_DB = RAW_DIR / "MazeControl.db"

# Pipeline output files (created by the build stages)
CLEAN_DB = PROCESSED_DIR / "MazeControl-clean.db"
COORDINATES = PROCESSED_DIR / "coordinates.parquet"
PHASES = PROCESSED_DIR / "phases.parquet"
SUBJECTS = PROCESSED_DIR / "subjects.parquet"
SESSIONS = PROCESSED_DIR / "sessions.parquet"
TRIALS = PROCESSED_DIR / "trials.parquet"
SESSION_SEGMENTS = PROCESSED_DIR / "session_segments.parquet"
EXPOSURE_REWARDS = PROCESSED_DIR / "exposure_rewards.parquet"
TRIAL_WELL_VISITS = PROCESSED_DIR / "trial_well_visits.parquet"
TRIAL_ZONE_SEQUENCE = PROCESSED_DIR / "trial_zone_sequence.parquet"

# Config files
PIPELINE_CONFIG = CONFIG_DIR / "pipeline.yaml"
SUBJECT_EXCEPTIONS = CONFIG_DIR / "subject_exceptions.yaml"

# Build log
BUILD_LOG = PROCESSED_DIR / "build_log.txt"
CLEANING_REVIEW = PROCESSED_DIR / "cleaning_review.txt"
