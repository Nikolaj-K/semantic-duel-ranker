"""
What: Strict provider-facing schema and defensive comparison-result parser.
Used by: LM Studio and mock providers.
Deps: Python >= 3.11.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from semantic_duel_ranker.models import AdjacentConfidence, ComparisonResult

MARGINS = {"negligible", "slight", "moderate", "strong", "decisive"}


def comparison_response_schema(
    expected_item_ids: Sequence[str],
) -> dict[str, object]:
    item_count = len(expected_item_ids)
    return {
        "type": "object",
        "properties": {
            "ranking": {
                "type": "array",
                "items": {"type": "string", "enum": list(expected_item_ids)},
                "minItems": item_count,
                "maxItems": item_count,
                "uniqueItems": True,
            },
            "confidence": {
                "type": ["number", "null"],
                "minimum": 0.5,
                "maximum": 1.0,
            },
            "adjacent_confidences": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "better": {
                            "type": "string",
                            "enum": list(expected_item_ids),
                        },
                        "worse": {
                            "type": "string",
                            "enum": list(expected_item_ids),
                        },
                        "confidence": {
                            "type": "number",
                            "minimum": 0.5,
                            "maximum": 1.0,
                        },
                    },
                    "required": ["better", "worse", "confidence"],
                    "additionalProperties": False,
                },
                "maxItems": max(0, item_count - 1),
            },
            "margin": {"type": "string", "enum": sorted(MARGINS)},
            "criterion_scores": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "criterion": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 80,
                        },
                        "scores": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "item_id": {
                                        "type": "string",
                                        "enum": list(expected_item_ids),
                                    },
                                    "score": {
                                        "type": "number",
                                        "minimum": 0,
                                        "maximum": 10,
                                    },
                                },
                                "required": ["item_id", "score"],
                                "additionalProperties": False,
                            },
                            "minItems": item_count,
                            "maxItems": item_count,
                        },
                    },
                    "required": ["criterion", "scores"],
                    "additionalProperties": False,
                },
                "minItems": 1,
                "maxItems": 12,
            },
            "justification": {
                "type": "string",
                "minLength": 1,
                "maxLength": 1600,
            },
        },
        "required": [
            "ranking",
            "confidence",
            "adjacent_confidences",
            "margin",
            "criterion_scores",
            "justification",
        ],
        "additionalProperties": False,
    }


def parse_comparison_response(
    payload: object,
    *,
    expected_item_ids: Sequence[str],
) -> ComparisonResult:
    if not isinstance(payload, Mapping):
        raise ValueError("Comparison response must be a JSON object.")
    expected = tuple(expected_item_ids)
    ranking = _string_tuple(payload.get("ranking"), "ranking")
    if len(ranking) != len(expected) or set(ranking) != set(expected):
        raise ValueError(
            "ranking must contain every compared item ID exactly once; "
            f"expected={expected}, received={ranking}"
        )

    confidence = _optional_probability(payload.get("confidence"), "confidence")
    raw_adjacent = payload.get("adjacent_confidences")
    if not isinstance(raw_adjacent, Sequence) or isinstance(raw_adjacent, (str, bytes)):
        raise ValueError("adjacent_confidences must be an array.")
    adjacent: list[AdjacentConfidence] = []
    for entry in raw_adjacent:
        if not isinstance(entry, Mapping):
            raise ValueError("Each adjacent confidence must be an object.")
        adjacent.append(
            AdjacentConfidence(
                better=_required_string(entry.get("better"), "better"),
                worse=_required_string(entry.get("worse"), "worse"),
                confidence=_probability(entry.get("confidence"), "confidence"),
            )
        )

    if len(expected) == 2:
        if confidence is None:
            raise ValueError("Pair comparisons require confidence.")
        if adjacent:
            raise ValueError("Pair comparisons must use an empty adjacent_confidences.")
    else:
        if confidence is not None:
            raise ValueError(
                "Tuple comparisons use adjacent_confidences, not confidence."
            )
        expected_edges = list(zip(ranking, ranking[1:], strict=False))
        received_edges = [(entry.better, entry.worse) for entry in adjacent]
        if received_edges != expected_edges:
            raise ValueError(
                "adjacent_confidences must follow consecutive ranking edges; "
                f"expected={expected_edges}, received={received_edges}"
            )

    margin = _required_string(payload.get("margin"), "margin")
    if margin not in MARGINS:
        raise ValueError(f"margin must be one of {sorted(MARGINS)}.")
    criterion_scores = _parse_criterion_scores(
        payload.get("criterion_scores"),
        expected_item_ids=expected,
    )
    justification = _required_string(payload.get("justification"), "justification")
    diagnostic_flags = _comparison_diagnostics(
        ranking=ranking,
        confidence=confidence,
        adjacent=adjacent,
        margin=margin,
        criterion_scores=criterion_scores,
    )
    return ComparisonResult(
        ranking=ranking,
        confidence=confidence,
        adjacent_confidences=tuple(adjacent),
        margin=margin,
        criterion_scores=criterion_scores,
        justification=justification,
        diagnostic_flags=diagnostic_flags,
    )


def parse_assistant_json(text: str) -> object:
    """Parse strict JSON with small recovery for common fenced responses."""
    stripped = text.strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError as original_error:
        if stripped.startswith("```") and stripped.endswith("```"):
            lines = stripped.splitlines()
            stripped = "\n".join(lines[1:-1]).strip()
            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                pass
        object_start = stripped.find("{")
        object_end = stripped.rfind("}")
        if object_start >= 0 and object_end > object_start:
            try:
                return json.loads(stripped[object_start : object_end + 1])
            except json.JSONDecodeError:
                pass
        raise ValueError(
            f"Assistant message is not valid JSON: {original_error}"
        ) from original_error


def _parse_criterion_scores(
    payload: object,
    *,
    expected_item_ids: tuple[str, ...],
) -> dict[str, dict[str, float]]:
    if not isinstance(payload, Sequence) or isinstance(payload, (str, bytes)):
        raise ValueError("criterion_scores must be an array.")
    parsed: dict[str, dict[str, float]] = {}
    for criterion_entry in payload:
        if not isinstance(criterion_entry, Mapping):
            raise ValueError("Each criterion score entry must be an object.")
        criterion = _required_string(
            criterion_entry.get("criterion"), "criterion_scores.criterion"
        )
        if criterion in parsed:
            raise ValueError(f"Duplicate criterion {criterion!r}.")
        raw_scores = criterion_entry.get("scores")
        if not isinstance(raw_scores, Sequence) or isinstance(raw_scores, (str, bytes)):
            raise ValueError(f"Scores for {criterion!r} must be an array.")
        scores: dict[str, float] = {}
        for score_entry in raw_scores:
            if not isinstance(score_entry, Mapping):
                raise ValueError("Each item score must be an object.")
            item_id = _required_string(score_entry.get("item_id"), "item_id")
            score = score_entry.get("score")
            if (
                not isinstance(score, (int, float))
                or isinstance(score, bool)
                or not 0 <= float(score) <= 10
            ):
                raise ValueError("criterion score must be between 0 and 10.")
            if item_id in scores:
                raise ValueError(f"Duplicate score for item {item_id!r}.")
            scores[item_id] = float(score)
        if set(scores) != set(expected_item_ids):
            raise ValueError(f"Criterion {criterion!r} must score every compared item.")
        parsed[criterion] = scores
    if not parsed:
        raise ValueError("At least one criterion score is required.")
    return parsed


def _comparison_diagnostics(
    *,
    ranking: tuple[str, ...],
    confidence: float | None,
    adjacent: list[AdjacentConfidence],
    margin: str,
    criterion_scores: dict[str, dict[str, float]],
) -> tuple[str, ...]:
    flags: list[str] = []
    reported_confidences = (
        [confidence]
        if confidence is not None
        else [entry.confidence for entry in adjacent]
    )
    if margin == "negligible" and any(
        value is not None and value >= 0.75 for value in reported_confidences
    ):
        flags.append("high_confidence_negligible_margin")
    elif margin == "slight" and any(
        value is not None and value >= 0.9 for value in reported_confidences
    ):
        flags.append("very_high_confidence_slight_margin")

    means = {
        item_id: sum(scores[item_id] for scores in criterion_scores.values())
        / len(criterion_scores)
        for item_id in ranking
    }
    for better, worse in zip(ranking, ranking[1:], strict=False):
        if means[better] + 0.10 < means[worse]:
            flags.append("criterion_scores_disagree_with_ranking")
            break
    return tuple(flags)


def _string_tuple(payload: object, label: str) -> tuple[str, ...]:
    if not isinstance(payload, Sequence) or isinstance(payload, (str, bytes)):
        raise ValueError(f"{label} must be an array.")
    values = tuple(_required_string(value, label) for value in payload)
    if len(set(values)) != len(values):
        raise ValueError(f"{label} values must be unique.")
    return values


def _required_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string.")
    return value.strip()


def _optional_probability(value: object, label: str) -> float | None:
    if value is None:
        return None
    return _probability(value, label)


def _probability(value: object, label: str) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not 0.5 <= float(value) <= 1.0
    ):
        raise ValueError(f"{label} must be between 0.5 and 1.0.")
    return float(value)
