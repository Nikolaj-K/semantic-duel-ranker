"""End-to-end mock run and resume artifact tests."""

from __future__ import annotations

import json
from pathlib import Path

from semantic_duel_ranker.artifacts import ArtifactStore
from semantic_duel_ranker.config import RunConfig
from semantic_duel_ranker.providers import MockProvider
from semantic_duel_ranker.run_loop import run_ranking


def test_mock_run_writes_final_artifacts_and_can_resume(
    simple_items, tmp_path: Path
) -> None:
    config = RunConfig(
        input_path=Path("synthetic.jsonl"),
        provider="mock",
        top_k=2,
        budget=2,
        run_root=tmp_path,
        matrix_window=3,
    )
    store = ArtifactStore.create(
        run_root=tmp_path,
        config=config,
        items=simple_items,
    )
    run_ranking(
        items=simple_items,
        config=config,
        provider=MockProvider(seed=42, noise=0.0),
        store=store,
    )
    assert (store.rankings_dir / "final_ranking.csv").exists()
    assert (store.rankings_dir / "final_ranking.jsonl").exists()
    assert (store.run_dir / "summary.md").exists()
    assert len(store.load_observations()) == 2
    stored_config = json.loads((store.run_dir / "config.json").read_text())
    assert "lmstudio_api_key" not in stored_config

    progress = store.load_progress()
    resumed_config = RunConfig(
        input_path=config.input_path,
        provider="mock",
        top_k=2,
        budget=4,
        run_root=tmp_path,
        matrix_window=3,
    )
    run_ranking(
        items=store.load_items(),
        config=resumed_config,
        provider=MockProvider(seed=42, noise=0.0),
        store=ArtifactStore.resume(store.run_dir),
        existing_observations=store.load_observations(),
        attempts_completed=int(progress["attempts_completed"]),
        provider_errors=int(progress["provider_errors"]),
    )
    assert len(store.load_observations()) == 4
    final_state = json.loads((store.run_dir / "run_state.json").read_text())
    assert final_state["attempts_completed"] == 4
    assert final_state["successful_observations"] == 4
    assert final_state["successful_provider_seconds_total"] >= 0
    assert final_state["average_successful_provider_seconds"] is not None
    assert final_state["last_successful_provider_seconds"] is not None
    assert final_state["last_success_at"] is not None
    assert final_state["status"] == "complete"
