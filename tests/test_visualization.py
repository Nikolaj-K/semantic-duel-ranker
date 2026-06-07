"""Terminal preview rendering tests."""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from semantic_duel_ranker.visualization import TerminalVisualizer


def test_tweet_preview_uses_matrix_labels_and_full_text(simple_items) -> None:
    output = StringIO()
    console = Console(
        file=output,
        color_system=None,
        width=100,
    )
    visualizer = TerminalVisualizer(
        items=simple_items,
        top_k=2,
        matrix_window=3,
        console=console,
    )

    visualizer.show_item_previews(2)

    rendered = output.getvalue()
    assert "first 2 of 3" in rendered
    assert "1. I01" in rendered
    assert "2. I02" in rendered
    assert simple_items[0].text in rendered
    assert simple_items[1].text in rendered
    assert simple_items[2].text not in rendered


def test_reader_introduction_explains_text_heavy_log(simple_items) -> None:
    output = StringIO()
    console = Console(file=output, color_system=None, width=100)
    visualizer = TerminalVisualizer(
        items=simple_items,
        top_k=2,
        matrix_window=3,
        console=console,
    )

    visualizer.show_reader_introduction(
        provider="lmstudio",
        model="gemma-4-12b-it",
        objective="Rank by useful project value.",
        comparison_budget=4,
        tuple_size=2,
        top_k=2,
        resumed=False,
    )

    rendered = output.getvalue()
    normalized = " ".join(rendered.split())
    assert "intentionally text-heavy" in rendered
    assert "Fan House" in rendered
    assert "comparison budget is 4" in normalized
    assert "up to 4 judgments" in normalized
    assert "configured top-K is 2" in normalized
