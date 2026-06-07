"""
What: Construct comparison prompts without provider-specific transport details.
Used by: run loop.
Deps: Python >= 3.11.
"""

from __future__ import annotations

import json

from semantic_duel_ranker.models import RankItem

CRITERIA = (
    "engagement_potential",
    "informativeness",
    "clarity",
    "originality",
    "topical_relevance",
)


def build_comparison_prompt(
    *,
    items: list[RankItem],
    objective: str,
) -> str:
    item_payloads = [_prompt_item(item) for item in items]
    tuple_instructions = (
        'Set "confidence" to a number from 0.5 to 1.0 and return an empty '
        '"adjacent_confidences" array.'
        if len(items) == 2
        else (
            'Set "confidence" to null. Return one adjacent_confidences entry for '
            "each consecutive edge in the ranking, in ranking order."
        )
    )
    return f"""You are comparing short text items for an active preference ranking system.

OBJECTIVE
{objective.strip()}

DECISION RULES
- Judge the items against the objective, not against political agreement or author fame.
- Engagement metrics are noisy context, not the target and not proof of quality.
- Preserve a distinction between informative value, clarity, originality, relevance,
  and likely useful engagement.
- Media content has not been downloaded or inspected. A media entry only means that
  attached media exists; do not infer its unseen contents.
- Return a complete strict ordering with no ties.
- Confidence is weak self-reported evidence. Use 0.5 for a near tie and reserve values
  near 1.0 for very clear differences.
- {tuple_instructions}
- Score every item from 0 to 10 on each requested criterion.
- Keep the justification concrete and concise, naming the decisive tradeoffs.

REQUESTED CRITERIA
{json.dumps(CRITERIA)}

ITEMS
{json.dumps(item_payloads, ensure_ascii=False, indent=2)}

Return only JSON matching the supplied schema."""


def _prompt_item(item: RankItem) -> dict[str, object]:
    author: dict[str, object] | None = None
    if item.author:
        author = {
            "username": item.author.username,
            "name": item.author.name,
            "followers": item.author.followers,
            "description": item.author.description,
        }
    return {
        "id": item.id,
        "text": item.text,
        "created_at": item.created_at,
        "language": item.language,
        "metrics": item.metrics.available(),
        "author": author,
        "media": [
            {
                "kind": media.kind,
                "width": media.width,
                "height": media.height,
                "alt_text": media.alt_text,
            }
            for media in item.media
        ],
        "metadata": item.metadata,
    }
