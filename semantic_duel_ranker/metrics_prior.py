"""
What: Compute a robust optional metadata prior from item engagement metrics.
Used by: rank model initialization and disagreement diagnostics.
Deps: NumPy.

The prior is intentionally weak. It supplies context before comparisons but does
not create synthetic wins or override provider evidence.
"""

from __future__ import annotations

import numpy as np

from semantic_duel_ranker.models import RankItem

METRIC_WEIGHTS = {
    "likes": 0.30,
    "replies": 0.10,
    "reposts": 0.20,
    "quotes": 0.10,
    "views": 0.10,
    "bookmarks": 0.20,
}


def compute_metadata_prior(items: list[RankItem]) -> dict[str, float]:
    if not items:
        return {}
    columns: dict[str, np.ndarray] = {}
    for metric_name in METRIC_WEIGHTS:
        values = np.array(
            [float(getattr(item.metrics, metric_name) or 0) for item in items],
            dtype=float,
        )
        columns[metric_name] = _robust_standardize(np.log1p(values))

    combined = np.zeros(len(items), dtype=float)
    available_weight = 0.0
    for metric_name, weight in METRIC_WEIGHTS.items():
        combined += weight * columns[metric_name]
        available_weight += weight
    if available_weight:
        combined /= available_weight
    combined = np.tanh(combined / 2.0)
    combined -= float(np.mean(combined))
    return {item.id: float(value) for item, value in zip(items, combined, strict=True)}


def _robust_standardize(values: np.ndarray) -> np.ndarray:
    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median)))
    if mad < 1e-9:
        std = float(np.std(values))
        if std < 1e-9:
            return np.zeros_like(values)
        return (values - median) / std
    return (values - median) / (1.4826 * mad)
