"""
Stage 3a: Enrich subjects.parquet and sessions.parquet with derived
group/phase columns.

Adds to subjects.parquet:
  training_group        — PI+VC / PI+VC_f1 / PI / VC / VC_DREADDs
  novel_route_probe     — PI_PI / PI_PI+VC / VC_VC / VC_PI+VC / null
  reversal_probe        — PI^PI / PI^PI+VC / VC^VC / VC^PI+VC / null
  approach_to_goal      — toward / away / null (PI has no cue)

Adds to sessions.parquet:
  session_experiment_phase — Acquisition / Exposure / Novel Route /
                             Reversal / Rotation / No Cue
  session_order            — per-subject chronological rank (1 = first
                             session, by date + time); use this, never a
                             sort on the string `session_number`

Run:
  python data/pipeline/stage3a_session_features.py
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from corner_maze.common.paths import BUILD_LOG, SESSIONS, SUBJECTS
from corner_maze.features.approach_to_goal import compute_approach_to_goal

ACQUISITION_GROUP = {
    "Fixed Cue 1": "PI+VC",
    "Fixed Cue 1 Twist": "PI+VC_f1",
    "Dark Train": "PI",
    "Rotate Train": "VC",
}

NOVEL_ROUTE_PROBE = {
    "Dark Detour No Cue": "PI_PI",
    "Dark Detour": "PI_PI+VC",
    "Rotate Detour Moving": "VC_VC",
    "Rotate Detour": "VC_PI+VC",
}

# Reversal probe depends on training_group for "Fixed Cue Switch"
REVERSAL_PROBE_SIMPLE = {
    "Dark Reverse": "PI^PI",
    "Rotate Reverse": "VC^VC",
}

FIXED_CUE_SWITCH_BY_GROUP = {
    "PI": "PI^PI+VC",
    "VC": "VC^PI+VC",
    "VC_DREADDs": "VC^PI+VC",
    # PI+VC and PI+VC_f1: Fixed Cue Switch IS their expected reversal,
    # not a crossed probe → null.
}

SESSION_EXPERIMENT_PHASE = {
    "Fixed Cue 1": "Acquisition",
    "Fixed Cue 1 Twist": "Acquisition",
    "Dark Train": "Acquisition",
    "Rotate Train": "Acquisition",
    "Exposure": "Exposure",
    "Fixed Cue 2a": "Novel Route",
    "Fixed Cue Novel Route Twist": "Novel Route",
    "Dark Detour": "Novel Route",
    "Dark Detour No Cue": "Novel Route",
    "Rotate Detour": "Novel Route",
    "Rotate Detour Moving": "Novel Route",
    "Fixed Cue Switch": "Reversal",
    "Fixed Cue Switch Twist": "Reversal",
    "Dark Reverse": "Reversal",
    "Rotate Reverse": "Reversal",
    "Fixed Cue Rotate": "Rotation",
    "Fixed Cue Rotate Twist": "Rotation",
    "Fixed No Cue": "No Cue",
    "Fixed No Cue Twist": "No Cue",
}


def derive_training_group(
    name: str, session_types: set[str], acq_counts: dict[str, int]
) -> str:
    if name.startswith(("V", "PG")):
        return "VC_DREADDs"
    acq = session_types & ACQUISITION_GROUP.keys()
    if not acq:
        raise ValueError(f"{name}: no acquisition session_type found")
    # If a subject has multiple acquisition types (e.g. CM032/CM033 switched
    # protocols mid-study), pick the one with most sessions.
    best = max(acq, key=lambda t: acq_counts.get(t, 0))
    return ACQUISITION_GROUP[best]


def derive_novel_route_probe(name: str, session_types: set[str]) -> str | None:
    probes = {NOVEL_ROUTE_PROBE[t] for t in session_types if t in NOVEL_ROUTE_PROBE}
    if len(probes) > 1:
        raise ValueError(f"{name}: multiple novel_route_probe values {probes}")
    return next(iter(probes), None)


def derive_reversal_probe(
    name: str, session_types: set[str], training_group: str
) -> str | None:
    probes: set[str] = set()
    for t in session_types:
        if t in REVERSAL_PROBE_SIMPLE:
            probes.add(REVERSAL_PROBE_SIMPLE[t])
        elif t == "Fixed Cue Switch":
            mapped = FIXED_CUE_SWITCH_BY_GROUP.get(training_group)
            if mapped is not None:
                probes.add(mapped)
    if len(probes) > 1:
        raise ValueError(f"{name}: multiple reversal_probe values {probes}")
    return next(iter(probes), None)


def main() -> None:
    print("Stage 3a — Enrich subjects/sessions with derived features")
    print()

    subjects = pd.read_parquet(SUBJECTS)
    sessions = pd.read_parquet(SESSIONS)

    # Drop prior derived columns if rerunning on an already-enriched file
    for col in [
        "training_group",
        "novel_route_probe",
        "reversal_probe",
        "approach_to_goal",
    ]:
        if col in subjects.columns:
            subjects = subjects.drop(columns=col)
    for col in ["session_experiment_phase", "session_order"]:
        if col in sessions.columns:
            sessions = sessions.drop(columns=col)

    # Build per-subject session_type sets + per-type counts
    by_subj = sessions.groupby("subject_id")["session_type"].apply(set)
    counts = sessions.groupby(["subject_id", "session_type"]).size()

    training_groups: list[str] = []
    novel_probes: list[str | None] = []
    reversal_probes: list[str | None] = []
    for _, sub in subjects.iterrows():
        sid = sub["subject_id"]
        types = by_subj.get(sid, set())
        subj_counts = counts.loc[sid].to_dict() if sid in counts.index.get_level_values(0) else {}
        tg = derive_training_group(sub["name"], types, subj_counts)
        nrp = derive_novel_route_probe(sub["name"], types)
        rp = derive_reversal_probe(sub["name"], types, tg)
        training_groups.append(tg)
        novel_probes.append(nrp)
        reversal_probes.append(rp)

    subjects["training_group"] = pd.Categorical(
        training_groups,
        categories=["PI+VC", "PI+VC_f1", "PI", "VC", "VC_DREADDs"],
    )
    subjects["novel_route_probe"] = pd.Categorical(
        novel_probes,
        categories=["PI_PI", "PI_PI+VC", "VC_VC", "VC_PI+VC"],
    )
    subjects["reversal_probe"] = pd.Categorical(
        reversal_probes,
        categories=["PI^PI", "PI^PI+VC", "VC^VC", "VC^PI+VC"],
    )
    subjects["approach_to_goal"] = compute_approach_to_goal(subjects)

    # Map session_type → session_experiment_phase
    unmapped = set(sessions["session_type"]) - SESSION_EXPERIMENT_PHASE.keys()
    if unmapped:
        raise ValueError(f"Unmapped session_types: {unmapped}")
    sessions["session_experiment_phase"] = pd.Categorical(
        sessions["session_type"].map(SESSION_EXPERIMENT_PHASE),
        categories=["Acquisition", "Exposure", "Novel Route",
                    "Reversal", "Rotation", "No Cue"],
    )

    # Chronological rank within subject. session_number is a string ('1e', '2e',
    # '1', ..., '10') and sorts lexically, so downstream code should order on this.
    when = pd.to_datetime(sessions["date"].astype(str) + " " + sessions["time"].astype(str))
    order = (sessions.assign(_when=when)
             .sort_values(["subject_id", "_when", "session_id"])
             .groupby("subject_id").cumcount() + 1)
    sessions["session_order"] = order.reindex(sessions.index).astype("int16")

    subjects.to_parquet(SUBJECTS, engine="pyarrow", index=False, compression="zstd")
    sessions.to_parquet(SESSIONS, engine="pyarrow", index=False, compression="zstd")

    # ── Summary ──────────────────────────────────────────────────────

    print("  subjects.parquet:")
    print(f"    training_group:\n{subjects['training_group'].value_counts().to_string()}")
    print()
    print(f"    novel_route_probe:\n{subjects['novel_route_probe'].value_counts(dropna=False).to_string()}")
    print()
    print(f"    reversal_probe:\n{subjects['reversal_probe'].value_counts(dropna=False).to_string()}")
    print()
    print(f"    approach_to_goal:\n{subjects['approach_to_goal'].value_counts(dropna=False).to_string()}")
    print()
    print("  sessions.parquet:")
    print(f"    session_experiment_phase:\n{sessions['session_experiment_phase'].value_counts().to_string()}")
    print()

    print(f"{'=' * 60}")
    print("Stage 3a Summary")
    print(f"{'=' * 60}")
    print(f"  subjects enriched: {len(subjects)}")
    print(f"  sessions enriched: {len(sessions)}")

    with open(BUILD_LOG, "a") as f:
        f.write(f"\nStage 3a — Enrich subjects/sessions — {datetime.now().isoformat()}\n")
        f.write(f"{'─' * 60}\n")
        f.write(f"  subjects: {len(subjects)}\n")
        f.write(f"  sessions: {len(sessions)}\n")
        f.write(f"  training_group:\n")
        for k, v in subjects["training_group"].value_counts().items():
            f.write(f"    {k}: {v}\n")
        f.write(f"  session_experiment_phase:\n")
        for k, v in sessions["session_experiment_phase"].value_counts().items():
            f.write(f"    {k}: {v}\n")
        f.write("\n")

    print("\nDone.")


if __name__ == "__main__":
    main()
