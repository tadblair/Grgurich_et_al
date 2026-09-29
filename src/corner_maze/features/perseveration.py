"""perseveration: on a Novel Route trial, did the rat repeat a previously
learned response?

During acquisition exactly two routes are reinforced — one from each trained
start arm (e.g. LL and RL). The Novel Route probe starts the rat from a third,
untrained arm, where the optimal route is a *different* turn sequence and
neither trained response reaches the goal. A rat that falls back on training
therefore executes one of those two previously learned responses.

    perseverative  <=>  executed route is one of the two trained responses

Chance is 50%: of the four possible turn sequences, two are trained.

Note on the 2S2C design: because the correct 2nd turn is held constant across
both trained start arms, the two trained responses always share their 2nd turn
(LL and RL both end in L). Testing "route in {LL, RL}" is therefore equivalent
to testing "2nd turn == the trained 2nd turn". `assert_trained_share_second_turn`
checks that this design invariant actually holds in the data.

Uses `coord_route` (the coordinate/zone-trace-derived route) rather than
`actual_route`: coord_route reflects the route the rat physically ran to its
first goal well, which is what perseveration is about.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

PHASE_1_LAST_TRIAL = 16
NOVEL = "novel"
FAMILIAR = "familiar"


def label_routes(trials: pd.DataFrame) -> pd.Series:
    """Label each probe trial 'novel' or 'familiar'.

    The novel route is the most frequent Phase-2 `correct_route` in a session
    (by design Phase 2 is 16 novel + 8 familiar, so the novel turn sequence is
    the unique modal one). `correct_route` is a turn sequence and therefore
    frame-invariant, which matters for the rotating-frame VC groups whose
    world-frame start arm is uniform.
    """
    cr = trials["correct_route"].astype("string")
    is_p2 = trials["trial_number"] > PHASE_1_LAST_TRIAL

    counts = (
        pd.DataFrame({"session_id": trials["session_id"], "correct_route": cr})
        .loc[is_p2]
        .groupby(["session_id", "correct_route"])
        .size()
        .reset_index(name="n")
    )
    novel_seq = (
        counts.sort_values("n", ascending=False)
        .groupby("session_id")["correct_route"]
        .first()
    )
    session_novel = trials["session_id"].map(novel_seq)
    return pd.Series(
        np.where(cr == session_novel, NOVEL, FAMILIAR), index=trials.index, dtype="object"
    )


def trained_responses(trials: pd.DataFrame, route_label: pd.Series | None = None) -> pd.Series:
    """session_id -> frozenset of the two routes reinforced during acquisition.

    Derived from the familiar trials' `correct_route`, which is by definition
    the response that was correct from each trained start arm.

    Raises ValueError if `trials` contains no familiar trials — e.g. when a
    novel-only subset is passed. Without them the trained responses cannot be
    derived, and every trial would silently score NA.
    """
    if route_label is None:
        route_label = label_routes(trials)
    fam = pd.DataFrame(
        {
            "session_id": trials["session_id"],
            "correct_route": trials["correct_route"].astype("string"),
        }
    ).loc[route_label == FAMILIAR]
    if fam.empty:
        raise ValueError(
            "no familiar trials found: cannot derive the trained responses. "
            "Pass the full probe session (novel + familiar), or supply `trained` "
            "computed from it."
        )
    return fam.groupby("session_id")["correct_route"].agg(
        lambda s: frozenset(s.dropna().tolist())
    )


def compute_perseveration(
    trials: pd.DataFrame, trained: pd.Series | None = None
) -> pd.Series:
    """Nullable bool per trial: did the rat execute one of the two trained responses?

    NA when the executed route or the session's trained responses are unknown.
    Meaningful only on novel-route trials (on familiar trials the trained
    response *is* correct, so this would trivially be True).
    """
    if trained is None:
        trained = trained_responses(trials)
    routes = trials["coord_route"].astype("string")
    sets = trials["session_id"].map(trained)
    out = [
        (route in tr) if (isinstance(tr, frozenset) and pd.notna(route)) else pd.NA
        for route, tr in zip(routes, sets, strict=True)
    ]
    return pd.Series(out, index=trials.index, dtype="boolean")


def trained_second_turn(trained: pd.Series) -> pd.Series:
    """session_id -> the single 2nd turn shared by both trained responses."""
    return trained.map(
        lambda s: next(iter({r[1] for r in s})) if len(s) and len({r[1] for r in s}) == 1 else pd.NA
    )


def assert_trained_share_second_turn(trained: pd.Series) -> None:
    """Design invariant: both trained responses share their 2nd turn.

    Raises AssertionError if any session violates it, which would mean the
    "repeat a trained response" and "repeat the trained 2nd turn" definitions
    of perseveration are no longer equivalent.
    """
    bad = {sid: set(s) for sid, s in trained.items() if len({r[1] for r in s}) != 1}
    assert not bad, f"sessions whose trained responses differ in 2nd turn: {bad}"
