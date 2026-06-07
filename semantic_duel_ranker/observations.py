"""
What: Convert validated tuple rankings into bounded pairwise evidence.
Used by: run loop and tests.
Deps: Python >= 3.11.
"""

from __future__ import annotations

import datetime as dt
from uuid import uuid4

from semantic_duel_ranker.models import (
    ComparisonResult,
    ComparisonTuple,
    PairwiseEvidence,
    PreferenceObservation,
)


def build_observation(
    *,
    step: int,
    comparison: ComparisonTuple,
    result: ComparisonResult,
    provider: str,
    model: str,
    response_format: str | None,
    usage: dict[str, int],
    provider_elapsed_seconds: float | None,
    repeated_pair: bool,
) -> PreferenceObservation:
    evidence = decompose_ranking(result)
    return PreferenceObservation(
        observation_id=f"obs-{step:06d}-{uuid4().hex[:8]}",
        step=step,
        created_at=dt.datetime.now(dt.UTC).isoformat(),
        item_ids=comparison.item_ids,
        ranking=result.ranking,
        evidence=evidence,
        confidence=result.confidence,
        adjacent_confidences=result.adjacent_confidences,
        margin=result.margin,
        criterion_scores=result.criterion_scores,
        justification=result.justification,
        provider=provider,
        model=model,
        response_format=response_format,
        acquisition_reason=comparison.reason,
        acquisition_score=comparison.acquisition_score,
        usage=usage,
        provider_elapsed_seconds=provider_elapsed_seconds,
        repeated_pair=repeated_pair,
        diagnostic_flags=result.diagnostic_flags,
    )


def decompose_ranking(result: ComparisonResult) -> tuple[PairwiseEvidence, ...]:
    adjacent_confidence = {
        (entry.better, entry.worse): entry.confidence
        for entry in result.adjacent_confidences
    }
    evidence: list[PairwiseEvidence] = []
    for better_index, winner in enumerate(result.ranking):
        for loser_index in range(better_index + 1, len(result.ranking)):
            loser = result.ranking[loser_index]
            confidence = result.confidence
            if len(result.ranking) > 2:
                path_confidences = [
                    adjacent_confidence[
                        (result.ranking[index], result.ranking[index + 1])
                    ]
                    for index in range(better_index, loser_index)
                ]
                confidence = min(path_confidences)
            evidence.append(
                PairwiseEvidence(
                    winner_id=winner,
                    loser_id=loser,
                    weight=confidence_weight(confidence),
                    confidence=confidence,
                )
            )
    return tuple(evidence)


def confidence_weight(confidence: float | None) -> float:
    """Map self-reported confidence to a deliberately narrow evidence range."""
    if confidence is None:
        return 1.0
    bounded = min(1.0, max(0.5, confidence))
    return 0.75 + bounded - 0.5
