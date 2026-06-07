"""
What: Select informative pair or tuple comparisons from the current ranking.
Used by: active run loop.
Deps: Python >= 3.11.

Base score:
    acquisition(i, j) = uncertainty * importance * novelty

Small boosts cover metadata disagreement and under-compared items. Periodic
diagnostic and consistency comparisons remain explicit in the selection reason.
"""

from __future__ import annotations

import math
import random
from collections import Counter
from dataclasses import dataclass
from itertools import combinations

from semantic_duel_ranker.models import (
    ComparisonTuple,
    PreferenceObservation,
    RankingState,
)
from semantic_duel_ranker.rank_model import BradleyTerryModel


@dataclass(frozen=True)
class PairCandidate:
    first_id: str
    second_id: str
    score: float
    uncertainty: float
    importance: float
    novelty: float
    disagreement: float
    comparison_count: int


class AcquisitionPolicy:
    def __init__(
        self,
        *,
        top_k: int,
        tuple_size: int = 2,
        seed: int = 42,
        diagnostic_every: int = 11,
        repeat_every: int = 9,
    ) -> None:
        self.top_k = top_k
        self.tuple_size = tuple_size
        self.rng = random.Random(seed)
        self.diagnostic_every = diagnostic_every
        self.repeat_every = repeat_every

    def select(
        self,
        *,
        state: RankingState,
        model: BradleyTerryModel,
        observations: list[PreferenceObservation],
        next_step: int,
    ) -> ComparisonTuple:
        candidates = self.score_candidates(
            state=state,
            model=model,
            observations=observations,
        )
        if not candidates:
            raise RuntimeError("No comparison candidates are available.")

        reason = "highest active-acquisition score"
        chosen = candidates[0]
        if next_step % self.repeat_every == 0:
            repeated = [
                candidate for candidate in candidates if candidate.comparison_count
            ]
            if repeated:
                chosen = max(repeated, key=lambda candidate: candidate.uncertainty)
                reason = "scheduled consistency repeat on an uncertain pair"
        elif next_step % self.diagnostic_every == 0:
            pool = candidates[: max(3, len(candidates) // 4)]
            chosen = self.rng.choice(pool)
            reason = "scheduled diagnostic exploration among useful pairs"

        item_ids = [chosen.first_id, chosen.second_id]
        if self.tuple_size > 2:
            item_ids.extend(
                self._extend_tuple(
                    selected=item_ids,
                    candidates=candidates,
                    count=self.tuple_size - 2,
                )
            )
            reason += f"; extended to a {self.tuple_size}-item tuple"
        return ComparisonTuple(
            item_ids=tuple(item_ids),
            reason=reason,
            acquisition_score=chosen.score,
            diagnostics={
                "uncertainty": chosen.uncertainty,
                "importance": chosen.importance,
                "novelty": chosen.novelty,
                "metadata_disagreement": chosen.disagreement,
                "previous_comparisons": float(chosen.comparison_count),
            },
        )

    def score_candidates(
        self,
        *,
        state: RankingState,
        model: BradleyTerryModel,
        observations: list[PreferenceObservation],
    ) -> list[PairCandidate]:
        rank_by_id = {entry.item_id: entry.rank for entry in state.entries}
        comparisons = _comparison_counts(observations)
        item_comparisons = Counter(
            item_id for observation in observations for item_id in observation.item_ids
        )
        candidates: list[PairCandidate] = []
        for first_id, second_id in combinations(rank_by_id, 2):
            probability = model.probability(first_id, second_id)
            uncertainty = 4.0 * probability * (1.0 - probability)
            rank_distance = abs(rank_by_id[first_id] - rank_by_id[second_id])
            adjacency = 1.0 / max(1, rank_distance)
            cutoff_distance = min(
                abs(rank_by_id[first_id] - self.top_k),
                abs(rank_by_id[second_id] - self.top_k),
            )
            cutoff_importance = math.exp(-cutoff_distance / 2.0)
            top_importance = math.exp(
                -min(rank_by_id[first_id], rank_by_id[second_id]) / max(2, self.top_k)
            )
            importance = (
                0.30
                + 0.35 * adjacency
                + 0.50 * cutoff_importance
                + 0.20 * top_importance
            )
            key = tuple(sorted((first_id, second_id)))
            comparison_count = comparisons[key]
            novelty = 1.0 / ((1.0 + comparison_count) ** 0.75)
            metadata_probability = model.metadata_probability(first_id, second_id)
            disagreement = abs(probability - metadata_probability)
            under_compared = 1.0 / (
                1.0 + min(item_comparisons[first_id], item_comparisons[second_id])
            )
            score = (
                uncertainty
                * importance
                * novelty
                * (1.0 + 0.75 * disagreement + 0.25 * under_compared)
            )
            candidates.append(
                PairCandidate(
                    first_id=first_id,
                    second_id=second_id,
                    score=score,
                    uncertainty=uncertainty,
                    importance=importance,
                    novelty=novelty,
                    disagreement=disagreement,
                    comparison_count=comparison_count,
                )
            )
        return sorted(candidates, key=lambda candidate: candidate.score, reverse=True)

    def _extend_tuple(
        self,
        *,
        selected: list[str],
        candidates: list[PairCandidate],
        count: int,
    ) -> list[str]:
        additional: list[str] = []
        for _ in range(count):
            possible: dict[str, float] = {}
            for candidate in candidates:
                for item_id, other_id in (
                    (candidate.first_id, candidate.second_id),
                    (candidate.second_id, candidate.first_id),
                ):
                    if (
                        item_id not in selected
                        and item_id not in additional
                        and other_id in selected + additional
                    ):
                        possible[item_id] = max(
                            possible.get(item_id, 0.0), candidate.score
                        )
            if not possible:
                break
            additional.append(max(possible, key=possible.get))  # type: ignore[arg-type]
        return additional


def _comparison_counts(
    observations: list[PreferenceObservation],
) -> Counter[tuple[str, str]]:
    counts: Counter[tuple[str, str]] = Counter()
    for observation in observations:
        for first_id, second_id in combinations(observation.item_ids, 2):
            counts[tuple(sorted((first_id, second_id)))] += 1
    return counts
