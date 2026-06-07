"""Provider safety and mock contract tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from semantic_duel_ranker.prompt_builder import build_comparison_prompt
from semantic_duel_ranker.providers import (
    MockProvider,
    ProviderError,
    validate_lmstudio_base_url,
)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:1234/v1",
        "http://localhost:1234/v1",
        "http://[::1]:1234/v1",
    ],
)
def test_loopback_urls_are_allowed(url: str) -> None:
    validate_lmstudio_base_url(url, allow_non_loopback=False)


def test_non_loopback_url_requires_opt_in() -> None:
    with pytest.raises(ProviderError, match="Refusing non-loopback"):
        validate_lmstudio_base_url(
            "https://example.com/v1",
            allow_non_loopback=False,
        )


def test_mock_provider_writes_auditable_response(simple_items, tmp_path: Path) -> None:
    items = simple_items[:2]
    prompt = build_comparison_prompt(items=items, objective="Rank for test value.")
    result = MockProvider(seed=7).compare(
        items=items,
        prompt=prompt,
        artifact_dir=tmp_path,
    )
    assert result.error is None
    assert result.comparison
    assert set(result.comparison.ranking) == {"alpha", "beta"}
    assert (tmp_path / "request.json").exists()
    assert (tmp_path / "response.json").exists()
    assert (tmp_path / "assistant_message.txt").exists()
