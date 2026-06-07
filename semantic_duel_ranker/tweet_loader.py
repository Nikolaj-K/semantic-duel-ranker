"""
What: Inspect and normalize tweet-scraper JSON into generic RankItem objects.
Run: `semantic-duel-ranker inspect-tweets --input RAW.json`
     `semantic-duel-ranker extract-sample --input RAW.json --output sample.jsonl`
Deps: Python >= 3.11.

The source file is loaded without modification. Normalization retains only data
that can support ranking, debugging, or source traceability.
"""

from __future__ import annotations

import json
import math
import random
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from html import unescape
from pathlib import Path

from semantic_duel_ranker.models import (
    AuthorInfo,
    ItemMetrics,
    MediaItem,
    RankItem,
    SourceInfo,
)

DEFAULT_TWEET_SAMPLE_IDS = (
    "1762195954617274802",
    "1762144506948800609",
    "1762559991204999421",
    "1762153797265092923",
    "1762553764622356765",
    "1762147081685250382",
    "1762247208173191590",
    "1762176034319814705",
    "1762131845972431325",
    "1761171439972159866",
)


@dataclass(frozen=True)
class TweetDatasetInspection:
    records: int
    unique_ids: int
    duplicate_rows: int
    english_records: int
    nonempty_text: int
    author_records: int
    media_records: int
    media_items: int
    missing_view_count: int
    top_level_fields: dict[str, int]
    languages: dict[str, int]

    def to_dict(self) -> dict[str, object]:
        return {
            "records": self.records,
            "unique_ids": self.unique_ids,
            "duplicate_rows": self.duplicate_rows,
            "english_records": self.english_records,
            "nonempty_text": self.nonempty_text,
            "author_records": self.author_records,
            "media_records": self.media_records,
            "media_items": self.media_items,
            "missing_view_count": self.missing_view_count,
            "top_level_fields": self.top_level_fields,
            "languages": self.languages,
        }


def load_raw_tweets(path: Path) -> list[dict[str, object]]:
    with path.open(encoding="utf-8") as handle:
        records_as_loaded = json.load(handle)
    if not isinstance(records_as_loaded, list) or not all(
        isinstance(record, dict) for record in records_as_loaded
    ):
        raise ValueError(f"Expected a JSON array of objects: {path}")
    return records_as_loaded


def inspect_tweet_dataset(path: Path) -> TweetDatasetInspection:
    records_as_loaded = load_raw_tweets(path)
    fields: Counter[str] = Counter()
    languages: Counter[str] = Counter()
    ids: list[str] = []
    nonempty_text = 0
    author_records = 0
    media_records = 0
    media_items = 0
    missing_view_count = 0
    for record in records_as_loaded:
        fields.update(record.keys())
        languages[str(record.get("lang"))] += 1
        ids.append(str(record.get("id")))
        nonempty_text += int(bool(str(record.get("text") or "").strip()))
        author_records += int(isinstance(record.get("author"), dict))
        media = _raw_media(record)
        media_records += int(bool(media))
        media_items += len(media)
        missing_view_count += int(record.get("viewCount") is None)
    return TweetDatasetInspection(
        records=len(records_as_loaded),
        unique_ids=len(set(ids)),
        duplicate_rows=len(ids) - len(set(ids)),
        english_records=languages["en"],
        nonempty_text=nonempty_text,
        author_records=author_records,
        media_records=media_records,
        media_items=media_items,
        missing_view_count=missing_view_count,
        top_level_fields=dict(fields.most_common()),
        languages=dict(languages.most_common()),
    )


def normalize_tweet_record(
    record: Mapping[str, object],
    *,
    source_path: str,
    row_number: int,
) -> RankItem:
    item_id = str(record.get("id") or "").strip()
    text = unescape(str(record.get("text") or "")).strip()
    author = record.get("author")
    author_payload = author if isinstance(author, Mapping) else {}
    return RankItem(
        id=item_id,
        text=text,
        created_at=_string(record.get("createdAt")),
        language=_string(record.get("lang")),
        url=_string(record.get("url") or record.get("twitterUrl")),
        metrics=ItemMetrics(
            likes=_integer(record.get("likeCount")),
            replies=_integer(record.get("replyCount")),
            reposts=_integer(record.get("retweetCount")),
            quotes=_integer(record.get("quoteCount")),
            views=_integer(record.get("viewCount")),
            bookmarks=_integer(record.get("bookmarkCount")),
        ),
        author=AuthorInfo(
            username=_string(author_payload.get("userName")),
            name=_string(author_payload.get("name")),
            followers=_integer(author_payload.get("followers")),
            following=_integer(author_payload.get("following")),
            description=_string(author_payload.get("description")),
            location=_string(author_payload.get("location")),
            verified=_boolean(author_payload.get("isVerified")),
        ),
        media=tuple(_normalize_media(entry) for entry in _raw_media(record)),
        metadata={
            "is_reply": bool(record.get("isReply", False)),
            "is_quote": bool(record.get("isQuote", False)),
            "is_repost": bool(record.get("isRetweet", False)),
        },
        source=SourceInfo(
            path=source_path,
            row_number=row_number,
            source_type="tweet_scraper_json",
        ),
    )


def load_normalized_tweets(
    path: Path,
    *,
    english_only: bool = True,
    deduplicate: bool = True,
    limit: int | None = None,
) -> list[RankItem]:
    records_as_loaded = load_raw_tweets(path)
    items: list[RankItem] = []
    seen: set[str] = set()
    source_path = _portable_source_path(path)
    for row_number, record in enumerate(records_as_loaded):
        if english_only and record.get("lang") != "en":
            continue
        item = normalize_tweet_record(
            record,
            source_path=source_path,
            row_number=row_number,
        )
        if not item.text.strip() or (deduplicate and item.id in seen):
            continue
        seen.add(item.id)
        items.append(item)
        if limit is not None and len(items) >= limit:
            break
    return items


def select_stratified_sample(
    items: list[RankItem],
    *,
    count: int = 10,
    seed: int = 42,
) -> list[RankItem]:
    """
    Select readable examples across engagement strata.

    The selection gives preference to self-contained, non-reply posts while
    preserving at least two media examples and a broad engagement range.
    """
    if count < 2:
        raise ValueError("Sample count must be at least two.")
    eligible = [item for item in items if _sample_quality(item) > 0]
    if len(eligible) < count:
        raise ValueError(f"Only {len(eligible)} eligible items for sample of {count}.")
    ordered = sorted(eligible, key=engagement_proxy)
    rng = random.Random(seed)
    stratum_count = min(count, 5)
    strata = [
        ordered[
            round(start * len(ordered) / stratum_count) : round(
                (start + 1) * len(ordered) / stratum_count
            )
        ]
        for start in range(stratum_count)
    ]
    quotas = [count // stratum_count] * stratum_count
    for index in range(count % stratum_count):
        quotas[-(index + 1)] += 1

    selected: list[RankItem] = []
    for stratum, quota in zip(strata, quotas, strict=True):
        shuffled = list(stratum)
        rng.shuffle(shuffled)
        ranked = sorted(
            shuffled,
            key=lambda item: (
                _sample_quality(item),
                int(bool(item.media)),
                -abs(len(item.text) - 220),
            ),
            reverse=True,
        )
        selected.extend(ranked[:quota])

    # Media is useful to verify that metadata survives normalization. Replace
    # the lowest-quality non-media examples when the strata did not yield two.
    media_needed = max(0, 2 - sum(bool(item.media) for item in selected))
    if media_needed:
        replacements = sorted(
            (item for item in eligible if item.media and item not in selected),
            key=lambda item: (_sample_quality(item), engagement_proxy(item)),
            reverse=True,
        )[:media_needed]
        replaceable = sorted(
            (item for item in selected if not item.media),
            key=_sample_quality,
        )[:media_needed]
        for old, new in zip(replaceable, replacements, strict=True):
            selected[selected.index(old)] = new
    return sorted(selected, key=engagement_proxy, reverse=True)


def select_curated_sample(items: list[RankItem]) -> list[RankItem]:
    """Return the reviewed ten-item fixture in a stable pedagogical order."""
    by_id = {item.id: item for item in items}
    missing = [item_id for item_id in DEFAULT_TWEET_SAMPLE_IDS if item_id not in by_id]
    if missing:
        raise ValueError(
            "The source dataset does not contain curated sample IDs: "
            + ", ".join(missing)
        )
    return [by_id[item_id] for item_id in DEFAULT_TWEET_SAMPLE_IDS]


def engagement_proxy(item: RankItem) -> float:
    """Heavy-tailed but interpretable proxy used only for sample stratification."""
    metrics = item.metrics
    likes = metrics.likes or 0
    replies = metrics.replies or 0
    reposts = metrics.reposts or 0
    quotes = metrics.quotes or 0
    bookmarks = metrics.bookmarks or 0
    views = metrics.views or 0
    return (
        math.log1p(likes)
        + 1.2 * math.log1p(replies)
        + 1.3 * math.log1p(reposts)
        + 1.2 * math.log1p(quotes)
        + 0.8 * math.log1p(bookmarks)
        + 0.15 * math.log1p(views)
    )


def _sample_quality(item: RankItem) -> float:
    text = " ".join(item.text.split())
    if len(text) < 45 or len(text) > 900:
        return 0.0
    is_reply = bool(item.metadata.get("is_reply"))
    starts_with_mention = text.startswith("@")
    quality = 4.0
    quality -= 2.0 * is_reply
    quality -= 2.0 * starts_with_mention
    quality += min(len(text), 300) / 300
    quality += 0.5 * bool(item.media)
    return quality


def _raw_media(record: Mapping[str, object]) -> list[Mapping[str, object]]:
    extended = record.get("extendedEntities")
    if not isinstance(extended, Mapping):
        return []
    media = extended.get("media")
    if not isinstance(media, list):
        return []
    return [entry for entry in media if isinstance(entry, Mapping)]


def _normalize_media(payload: Mapping[str, object]) -> MediaItem:
    original_info = payload.get("original_info")
    dimensions = original_info if isinstance(original_info, Mapping) else {}
    return MediaItem(
        kind=_string(payload.get("type")) or "unknown",
        url=_string(payload.get("media_url_https")),
        display_url=_string(payload.get("display_url")),
        expanded_url=_string(payload.get("expanded_url")),
        width=_integer(dimensions.get("width")),
        height=_integer(dimensions.get("height")),
        alt_text=_string(payload.get("ext_alt_text")),
    )


def _portable_source_path(path: Path) -> str:
    resolved = path.resolve()
    cwd = Path.cwd().resolve()
    try:
        return resolved.relative_to(cwd).as_posix()
    except ValueError:
        return str(resolved)


def _string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _integer(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    return None


def _boolean(value: object) -> bool | None:
    return value if isinstance(value, bool) else None
