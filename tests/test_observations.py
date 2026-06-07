"""Confidence weighting and tuple decomposition tests."""

from __future__ import annotations

from semantic_duel_ranker.models import (
    AdjacentConfidence,
    ComparisonResult,
    PreferenceObservation,
)
from semantic_duel_ranker.observations import confidence_weight, decompose_ranking


def test_tuple_decomposition_uses_weakest_adjacent_confidence() -> None:
    result = ComparisonResult(
        ranking=("alpha", "beta", "gamma"),
        confidence=None,
        adjacent_confidences=(
            AdjacentConfidence("alpha", "beta", 0.9),
            AdjacentConfidence("beta", "gamma", 0.6),
        ),
        margin="moderate",
        criterion_scores={"clarity": {"alpha": 9, "beta": 7, "gamma": 4}},
        justification="Ordered by clarity.",
    )
    evidence = decompose_ranking(result)
    assert [(pair.winner_id, pair.loser_id) for pair in evidence] == [
        ("alpha", "beta"),
        ("alpha", "gamma"),
        ("beta", "gamma"),
    ]
    assert evidence[1].confidence == 0.6


def test_confidence_weight_is_bounded() -> None:
    assert confidence_weight(0.5) == 0.75
    assert confidence_weight(0.75) == 1.0
    assert confidence_weight(1.0) == 1.25


def test_legacy_observation_without_provider_timing_still_loads() -> None:
    payload = {
        "observation_id": "obs-1",
        "step": 1,
        "created_at": "2026-06-06T00:00:00+00:00",
        "item_ids": ["alpha", "beta"],
        "ranking": ["alpha", "beta"],
        "evidence": [
            {
                "winner_id": "alpha",
                "loser_id": "beta",
                "weight": 1.0,
                "confidence": 0.75,
            }
        ],
        "confidence": 0.75,
        "adjacent_confidences": [],
        "margin": "slight",
        "criterion_scores": {},
        "justification": "Alpha is clearer.",
        "provider": "mock",
        "model": "mock",
        "response_format": None,
        "acquisition_reason": "test",
        "acquisition_score": 1.0,
        "usage": {},
    }

    observation = PreferenceObservation.from_dict(payload)

    assert observation.provider_elapsed_seconds is None
