"""Shared compact fixtures for ranking tests."""

from __future__ import annotations

import pytest

from semantic_duel_ranker.models import ItemMetrics, RankItem


@pytest.fixture
def simple_items() -> list[RankItem]:
    return [
        RankItem(
            id="alpha",
            text="A clear and informative explanation with a concrete useful result.",
            metrics=ItemMetrics(likes=30, views=500),
        ),
        RankItem(
            id="beta",
            text="A reasonable summary with some useful context.",
            metrics=ItemMetrics(likes=10, views=200),
        ),
        RankItem(
            id="gamma",
            text="A vague announcement without enough supporting detail.",
            metrics=ItemMetrics(likes=2, views=20),
        ),
    ]
