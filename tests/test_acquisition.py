"""Active comparison scoring tests."""

from __future__ import annotations

from semantic_duel_ranker.acquisition import AcquisitionPolicy
from semantic_duel_ranker.rank_model import BradleyTerryModel


def test_acquisition_returns_unique_pair_and_penalizes_repeat(simple_items) -> None:
    model = BradleyTerryModel(
        simple_items,
        metadata_prior={item.id: 0.0 for item in simple_items},
    )
    state = model.fit([])
    policy = AcquisitionPolicy(top_k=2, seed=42)
    first = policy.select(
        state=state,
        model=model,
        observations=[],
        next_step=1,
    )
    assert len(first.item_ids) == 2
    assert len(set(first.item_ids)) == 2
    assert first.diagnostics["previous_comparisons"] == 0
