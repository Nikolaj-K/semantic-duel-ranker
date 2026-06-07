"""Loader normalization and checked-in sample tests."""

from __future__ import annotations

import json
from pathlib import Path

from semantic_duel_ranker.input_loader import load_rank_items
from semantic_duel_ranker.tweet_loader import (
    inspect_tweet_dataset,
    load_normalized_tweets,
)


def test_checked_in_sample_is_complete_and_diverse() -> None:
    path = Path("test_data/sample_10_rank_items.jsonl")
    items = load_rank_items(path)
    assert len(items) == 10
    assert len({item.id for item in items}) == 10
    assert all(item.language == "en" for item in items)
    assert sum(bool(item.media) for item in items) >= 4
    likes = [item.metrics.likes or 0 for item in items]
    assert min(likes) == 0
    assert max(likes) >= 1_000
    assert all(item.source and item.source.row_number is not None for item in items)


def test_tweet_loader_inspects_and_deduplicates(tmp_path: Path) -> None:
    raw_path = tmp_path / "tweets.json"
    records = [
        _tweet_record("1", lang="en", media=True),
        _tweet_record("1", lang="en", media=True),
        _tweet_record("2", lang="fr", media=False),
    ]
    raw_path.write_text(json.dumps(records), encoding="utf-8")

    inspection = inspect_tweet_dataset(raw_path)
    assert inspection.records == 3
    assert inspection.unique_ids == 2
    assert inspection.duplicate_rows == 1
    assert inspection.english_records == 2
    assert inspection.media_records == 2

    items = load_normalized_tweets(raw_path)
    assert [item.id for item in items] == ["1"]
    assert items[0].metrics.likes == 4
    assert items[0].author and items[0].author.username == "tester"
    assert items[0].media[0].width == 640


def _tweet_record(item_id: str, *, lang: str, media: bool) -> dict[str, object]:
    raw_media = [
        {
            "type": "photo",
            "media_url_https": "https://example.test/photo.jpg",
            "display_url": "pic.example/1",
            "expanded_url": "https://example.test/post/photo/1",
            "original_info": {"width": 640, "height": 480},
        }
    ]
    return {
        "type": "tweet",
        "id": item_id,
        "url": f"https://example.test/{item_id}",
        "twitterUrl": f"https://twitter.test/{item_id}",
        "text": "A sufficiently detailed test post for normalization.",
        "retweetCount": 2,
        "replyCount": 1,
        "likeCount": 4,
        "quoteCount": 0,
        "viewCount": 50,
        "bookmarkCount": 1,
        "createdAt": "Mon Feb 26 23:55:51 +0000 2024",
        "lang": lang,
        "isReply": False,
        "isQuote": False,
        "isRetweet": False,
        "author": {
            "userName": "tester",
            "name": "Test Author",
            "followers": 12,
            "following": 3,
            "description": "Testing.",
            "location": "Local",
            "isVerified": False,
        },
        "extendedEntities": {"media": raw_media if media else []},
    }
