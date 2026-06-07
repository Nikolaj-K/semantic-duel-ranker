"""Bradley-Terry recovery and uncertainty tests."""

from __future__ import annotations

import datetime as dt

from semantic_duel_ranker.models import PairwiseEvidence, PreferenceObservation
from semantic_duel_ranker.rank_model import BradleyTerryModel


def test_model_recovers_consistent_order(simple_items) -> None:
    observations = [
        _observation(step, winner, loser)
        for step, (winner, loser) in enumerate(
            [
                ("alpha", "beta"),
                ("alpha", "gamma"),
                ("beta", "gamma"),
                ("alpha", "beta"),
                ("beta", "gamma"),
            ],
            start=1,
        )
    ]
    model = BradleyTerryModel(
        simple_items,
        metadata_prior={item.id: 0.0 for item in simple_items},
        regularization=0.5,
    )
    initial = model.fit([])
    fitted = model.fit(observations)
    assert [entry.item_id for entry in fitted.entries] == [
        "alpha",
        "beta",
        "gamma",
    ]
    assert fitted.entries[0].score > fitted.entries[1].score
    assert fitted.entries[1].score > fitted.entries[2].score
    assert max(entry.uncertainty for entry in fitted.entries) < max(
        entry.uncertainty for entry in initial.entries
    )


def _observation(step: int, winner: str, loser: str) -> PreferenceObservation:
    return PreferenceObservation(
        observation_id=f"obs-{step}",
        step=step,
        created_at=dt.datetime.now(dt.UTC).isoformat(),
        item_ids=(winner, loser),
        ranking=(winner, loser),
        evidence=(PairwiseEvidence(winner, loser, 1.0, 0.75),),
        confidence=0.75,
        adjacent_confidences=(),
        margin="moderate",
        criterion_scores={"clarity": {winner: 8, loser: 6}},
        justification="Test evidence.",
        provider="mock",
        model="mock",
        response_format="mock-json",
        acquisition_reason="test",
        acquisition_score=1.0,
    )
