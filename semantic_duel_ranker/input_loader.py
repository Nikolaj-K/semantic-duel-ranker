"""
What: Load generic normalized RankItem data from JSONL, JSON, or CSV.
Run: Used through `semantic-duel-ranker rank --loader generic --input PATH`.
Deps: Python >= 3.11.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable, Mapping
from pathlib import Path

from semantic_duel_ranker.models import RankItem


def load_rank_items(path: Path, *, limit: int | None = None) -> list[RankItem]:
    """Load normalized items and reject duplicate IDs."""
    assert limit is None or limit >= 0
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        raw_items_as_loaded = list(_read_jsonl(path))
    elif suffix == ".json":
        raw_items_as_loaded = _read_json(path)
    elif suffix == ".csv":
        raw_items_as_loaded = list(_read_csv(path))
    else:
        raise ValueError(f"Unsupported input format {suffix!r}: {path}")

    selected = raw_items_as_loaded[:limit] if limit is not None else raw_items_as_loaded
    items = [RankItem.from_dict(payload) for payload in selected]
    duplicate_ids = _duplicates(item.id for item in items)
    if duplicate_ids:
        raise ValueError(f"Duplicate item IDs: {', '.join(sorted(duplicate_ids))}")
    if len(items) < 2:
        raise ValueError("At least two rank items are required.")
    return items


def write_rank_items_jsonl(path: Path, items: Iterable[RankItem]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for item in items:
            handle.write(json.dumps(item.to_dict(), ensure_ascii=False, sort_keys=True))
            handle.write("\n")


def _read_jsonl(path: Path) -> Iterable[Mapping[str, object]]:
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            payload = json.loads(stripped)
            if not isinstance(payload, dict):
                raise ValueError(f"{path}:{line_number} must contain a JSON object.")
            yield payload


def _read_json(path: Path) -> list[Mapping[str, object]]:
    with path.open(encoding="utf-8") as handle:
        payload_as_loaded = json.load(handle)
    if isinstance(payload_as_loaded, dict):
        raw_items = payload_as_loaded.get("items")
    else:
        raw_items = payload_as_loaded
    if not isinstance(raw_items, list) or not all(
        isinstance(item, dict) for item in raw_items
    ):
        raise ValueError(
            f"{path} must be a JSON array or an object with an items array."
        )
    return raw_items


def _read_csv(path: Path) -> Iterable[Mapping[str, object]]:
    with path.open(encoding="utf-8", newline="") as handle:
        yield from csv.DictReader(handle)


def _duplicates(values: Iterable[str]) -> set[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return duplicates
