"""
What: Regularized Bradley-Terry fitting and ranking-state construction.
Used by: active acquisition, visualization, artifacts, and tests.
Deps: NumPy and SciPy.

For evidence winner i > loser j:
    P(i beats j) = sigmoid(theta_i - theta_j)

The optimization minimizes weighted negative log likelihood plus a Gaussian
penalty toward the optional metadata prior.
"""

from __future__ import annotations

import math
from collections import defaultdict

import numpy as np
from scipy.optimize import minimize

from semantic_duel_ranker.models import (
    PairwiseEvidence,
    PreferenceObservation,
    RankingEntry,
    RankingState,
    RankItem,
)


class BradleyTerryModel:
    def __init__(
        self,
        items: list[RankItem],
        *,
        metadata_prior: dict[str, float] | None = None,
        prior_strength: float = 0.35,
        regularization: float = 1.0,
    ) -> None:
        if len(items) < 2:
            raise ValueError("Bradley-Terry requires at least two items.")
        self.items = items
        self.item_ids = [item.id for item in items]
        self.index_by_id = {
            item_id: index for index, item_id in enumerate(self.item_ids)
        }
        self.metadata_prior = metadata_prior or {
            item_id: 0.0 for item_id in self.item_ids
        }
        self.prior_strength = prior_strength
        self.regularization = regularization
        self._scores = np.array(
            [
                prior_strength * self.metadata_prior.get(item_id, 0.0)
                for item_id in self.item_ids
            ],
            dtype=float,
        )
        self._uncertainties = np.full(
            len(items), 1.0 / math.sqrt(regularization), dtype=float
        )

    def fit(
        self,
        observations: list[PreferenceObservation],
        *,
        step: int | None = None,
    ) -> RankingState:
        evidence = [
            pair for observation in observations for pair in observation.evidence
        ]
        if evidence:
            result = minimize(
                fun=self._objective,
                x0=self._scores,
                args=(evidence,),
                jac=True,
                method="L-BFGS-B",
            )
            if not result.success:
                raise RuntimeError(
                    f"Bradley-Terry optimization failed: {result.message}"
                )
            self._scores = np.asarray(result.x, dtype=float)
            self._scores -= float(np.mean(self._scores))
            self._uncertainties = self._estimate_uncertainties(evidence)
        return self.state(
            observations,
            step=len(observations) if step is None else step,
        )

    def probability(self, first_id: str, second_id: str) -> float:
        difference = (
            self._scores[self.index_by_id[first_id]]
            - self._scores[self.index_by_id[second_id]]
        )
        return float(_sigmoid(difference))

    def metadata_probability(self, first_id: str, second_id: str) -> float:
        difference = self.prior_strength * (
            self.metadata_prior.get(first_id, 0.0)
            - self.metadata_prior.get(second_id, 0.0)
        )
        return float(_sigmoid(difference))

    def state(
        self,
        observations: list[PreferenceObservation],
        *,
        step: int,
    ) -> RankingState:
        comparisons: defaultdict[str, int] = defaultdict(int)
        wins: defaultdict[str, float] = defaultdict(float)
        losses: defaultdict[str, float] = defaultdict(float)
        last_justification: dict[str, str] = {}
        for observation in observations:
            ordering = " > ".join(observation.ranking)
            for item_id in observation.item_ids:
                comparisons[item_id] += 1
                last_justification[item_id] = f"{ordering}: {observation.justification}"
            for evidence in observation.evidence:
                wins[evidence.winner_id] += evidence.weight
                losses[evidence.loser_id] += evidence.weight

        order = sorted(
            self.item_ids,
            key=lambda item_id: self._scores[self.index_by_id[item_id]],
            reverse=True,
        )
        pairwise = {
            row_id: {
                column_id: (
                    0.5 if row_id == column_id else self.probability(row_id, column_id)
                )
                for column_id in order
            }
            for row_id in order
        }
        flags = self._diagnostic_flags(order)
        entries = tuple(
            RankingEntry(
                rank=rank,
                item_id=item_id,
                score=float(self._scores[self.index_by_id[item_id]]),
                uncertainty=float(self._uncertainties[self.index_by_id[item_id]]),
                metadata_prior=float(self.metadata_prior.get(item_id, 0.0)),
                comparisons=comparisons[item_id],
                wins=wins[item_id],
                losses=losses[item_id],
                last_justification=last_justification.get(item_id),
                diagnostic_flags=tuple(flags.get(item_id, [])),
            )
            for rank, item_id in enumerate(order, start=1)
        )
        warnings = detect_inconsistency_warnings(observations)
        return RankingState(
            step=step,
            entries=entries,
            score_by_id={
                item_id: float(self._scores[self.index_by_id[item_id]])
                for item_id in self.item_ids
            },
            uncertainty_by_id={
                item_id: float(self._uncertainties[self.index_by_id[item_id]])
                for item_id in self.item_ids
            },
            pairwise_probabilities=pairwise,
            warnings=warnings,
        )

    def _objective(
        self,
        scores: np.ndarray,
        evidence: list[PairwiseEvidence],
    ) -> tuple[float, np.ndarray]:
        prior = np.array(
            [
                self.prior_strength * self.metadata_prior.get(item_id, 0.0)
                for item_id in self.item_ids
            ],
            dtype=float,
        )
        difference_from_prior = scores - prior
        loss = (
            0.5
            * self.regularization
            * float(difference_from_prior @ difference_from_prior)
        )
        gradient = self.regularization * difference_from_prior
        for pair in evidence:
            winner = self.index_by_id[pair.winner_id]
            loser = self.index_by_id[pair.loser_id]
            difference = scores[winner] - scores[loser]
            loss += pair.weight * float(np.logaddexp(0.0, -difference))
            error = pair.weight * (_sigmoid(difference) - 1.0)
            gradient[winner] += error
            gradient[loser] -= error
        return loss, gradient

    def _estimate_uncertainties(self, evidence: list[PairwiseEvidence]) -> np.ndarray:
        count = len(self.item_ids)
        hessian = self.regularization * np.eye(count)
        for pair in evidence:
            winner = self.index_by_id[pair.winner_id]
            loser = self.index_by_id[pair.loser_id]
            probability = self.probability(pair.winner_id, pair.loser_id)
            curvature = pair.weight * probability * (1.0 - probability)
            hessian[winner, winner] += curvature
            hessian[loser, loser] += curvature
            hessian[winner, loser] -= curvature
            hessian[loser, winner] -= curvature
        covariance = np.linalg.pinv(hessian)
        diagonal = np.clip(np.diag(covariance), 0.0, None)
        return np.sqrt(diagonal)

    def _diagnostic_flags(self, order: list[str]) -> dict[str, list[str]]:
        model_rank = {item_id: rank for rank, item_id in enumerate(order)}
        prior_order = sorted(
            self.item_ids,
            key=lambda item_id: self.metadata_prior.get(item_id, 0.0),
            reverse=True,
        )
        prior_rank = {item_id: rank for rank, item_id in enumerate(prior_order)}
        threshold = max(2, len(order) // 3)
        flags: dict[str, list[str]] = defaultdict(list)
        for item_id in order:
            if abs(model_rank[item_id] - prior_rank[item_id]) >= threshold:
                flags[item_id].append("metadata_disagreement")
        return flags


def detect_inconsistency_warnings(
    observations: list[PreferenceObservation],
) -> tuple[str, ...]:
    wins: defaultdict[tuple[str, str], float] = defaultdict(float)
    for observation in observations:
        for pair in observation.evidence:
            wins[(pair.winner_id, pair.loser_id)] += pair.weight
    warnings: list[str] = []
    pairs = {tuple(sorted(pair)) for pair in wins}
    contradictory = 0
    for first, second in pairs:
        if wins[(first, second)] > 0 and wins[(second, first)] > 0:
            contradictory += 1
    if contradictory:
        warnings.append(f"{contradictory} pair(s) have contradictory observations")

    item_ids = sorted({item_id for pair in wins for item_id in pair})
    cycles = 0
    for first_index, first in enumerate(item_ids):
        for second_index in range(first_index + 1, len(item_ids)):
            second = item_ids[second_index]
            for third in item_ids[second_index + 1 :]:
                if _majority_cycle(first, second, third, wins):
                    cycles += 1
    if cycles:
        warnings.append(f"{cycles} majority three-cycle(s) detected")
    diagnostic_counts: defaultdict[str, int] = defaultdict(int)
    for observation in observations:
        for flag in observation.diagnostic_flags:
            diagnostic_counts[flag] += 1
    warnings.extend(
        f"{count} observation(s): {flag.replace('_', ' ')}"
        for flag, count in sorted(diagnostic_counts.items())
    )
    return tuple(warnings)


def _majority_cycle(
    first: str,
    second: str,
    third: str,
    wins: dict[tuple[str, str], float],
) -> bool:
    def beats(left: str, right: str) -> bool:
        return wins[(left, right)] > wins[(right, left)]

    return (beats(first, second) and beats(second, third) and beats(third, first)) or (
        beats(second, first) and beats(third, second) and beats(first, third)
    )


def _sigmoid(value: float | np.ndarray) -> float | np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(value, -40.0, 40.0)))
