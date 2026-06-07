"""Strict pair and tuple response contract tests."""

from __future__ import annotations

import pytest

from semantic_duel_ranker.schema import parse_assistant_json, parse_comparison_response


def test_parse_pair_response() -> None:
    payload = _payload(["alpha", "beta"])
    result = parse_comparison_response(
        payload,
        expected_item_ids=["alpha", "beta"],
    )
    assert result.ranking == ("alpha", "beta")
    assert result.confidence == 0.75
    assert not result.adjacent_confidences


def test_parser_flags_confidence_margin_and_score_disagreement() -> None:
    payload = _payload(["alpha", "beta"])
    payload["confidence"] = 0.9
    payload["margin"] = "negligible"
    payload["criterion_scores"] = [
        {
            "criterion": "clarity",
            "scores": [
                {"item_id": "alpha", "score": 5},
                {"item_id": "beta", "score": 9},
            ],
        }
    ]
    result = parse_comparison_response(
        payload,
        expected_item_ids=["alpha", "beta"],
    )
    assert "high_confidence_negligible_margin" in result.diagnostic_flags
    assert "criterion_scores_disagree_with_ranking" in result.diagnostic_flags


def test_parse_tuple_response() -> None:
    payload = _payload(["alpha", "beta", "gamma"])
    payload["confidence"] = None
    payload["adjacent_confidences"] = [
        {"better": "alpha", "worse": "beta", "confidence": 0.7},
        {"better": "beta", "worse": "gamma", "confidence": 0.8},
    ]
    result = parse_comparison_response(
        payload,
        expected_item_ids=["alpha", "beta", "gamma"],
    )
    assert len(result.adjacent_confidences) == 2


def test_parser_rejects_missing_or_foreign_ids() -> None:
    payload = _payload(["alpha", "foreign"])
    with pytest.raises(ValueError, match="every compared item"):
        parse_comparison_response(
            payload,
            expected_item_ids=["alpha", "beta"],
        )


def test_parse_assistant_json_recovers_fenced_object() -> None:
    assert parse_assistant_json('```json\n{"ok": true}\n```') == {"ok": True}


def _payload(item_ids: list[str]) -> dict[str, object]:
    return {
        "ranking": item_ids,
        "confidence": 0.75,
        "adjacent_confidences": [],
        "margin": "moderate",
        "criterion_scores": [
            {
                "criterion": "clarity",
                "scores": [
                    {"item_id": item_id, "score": 8 - index}
                    for index, item_id in enumerate(item_ids)
                ],
            }
        ],
        "justification": "The first item is clearer.",
    }
