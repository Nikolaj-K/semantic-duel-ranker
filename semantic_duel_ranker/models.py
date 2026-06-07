"""
What: Provider-neutral domain objects and JSON serialization helpers.
Used by: loaders, ranking, acquisition, providers, artifacts, and the CLI.
Deps: Python >= 3.11.

The core model deliberately avoids tweet-specific names. Domain loaders map
their source records into these stable objects.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field

JSONValue = None | bool | int | float | str | list["JSONValue"] | dict[str, "JSONValue"]


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError("Boolean values are not valid integer metrics.")
    if isinstance(value, (int, float)):
        return int(value)
    raise ValueError(
        f"Expected an integer-compatible value, got {type(value).__name__}."
    )


@dataclass(frozen=True)
class ItemMetrics:
    """Optional quantitative context attached to an item."""

    likes: int | None = None
    replies: int | None = None
    reposts: int | None = None
    quotes: int | None = None
    views: int | None = None
    bookmarks: int | None = None
    extra: dict[str, float] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: Mapping[str, object] | None) -> ItemMetrics:
        payload = payload or {}
        known = {"likes", "replies", "reposts", "quotes", "views", "bookmarks"}
        raw_extra = payload.get("extra", {})
        if not isinstance(raw_extra, Mapping):
            raise ValueError("metrics.extra must be an object.")
        extra = {str(key): float(value) for key, value in raw_extra.items()}
        extra.update(
            {
                str(key): float(value)
                for key, value in payload.items()
                if key not in known | {"extra"}
                and isinstance(value, (int, float))
                and not isinstance(value, bool)
            }
        )
        return cls(
            likes=_optional_int(payload.get("likes")),
            replies=_optional_int(payload.get("replies")),
            reposts=_optional_int(payload.get("reposts")),
            quotes=_optional_int(payload.get("quotes")),
            views=_optional_int(payload.get("views")),
            bookmarks=_optional_int(payload.get("bookmarks")),
            extra=extra,
        )

    def available(self) -> dict[str, int | float]:
        result: dict[str, int | float] = {}
        for key in ("likes", "replies", "reposts", "quotes", "views", "bookmarks"):
            value = getattr(self, key)
            if value is not None:
                result[key] = value
        result.update(self.extra)
        return result


@dataclass(frozen=True)
class AuthorInfo:
    """Small author context that can help interpret an item."""

    username: str | None = None
    name: str | None = None
    followers: int | None = None
    following: int | None = None
    description: str | None = None
    location: str | None = None
    verified: bool | None = None

    @classmethod
    def from_dict(cls, payload: Mapping[str, object] | None) -> AuthorInfo | None:
        if not payload:
            return None
        verified = payload.get("verified")
        if verified is not None and not isinstance(verified, bool):
            raise ValueError("author.verified must be boolean or null.")
        return cls(
            username=_optional_string(payload.get("username")),
            name=_optional_string(payload.get("name")),
            followers=_optional_int(payload.get("followers")),
            following=_optional_int(payload.get("following")),
            description=_optional_string(payload.get("description")),
            location=_optional_string(payload.get("location")),
            verified=verified,
        )


@dataclass(frozen=True)
class MediaItem:
    """Media metadata only; the MVP never downloads or interprets media."""

    kind: str
    url: str | None = None
    display_url: str | None = None
    expanded_url: str | None = None
    width: int | None = None
    height: int | None = None
    alt_text: str | None = None

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> MediaItem:
        kind = payload.get("kind")
        if not isinstance(kind, str) or not kind.strip():
            raise ValueError("media.kind must be a non-empty string.")
        return cls(
            kind=kind.strip(),
            url=_optional_string(payload.get("url")),
            display_url=_optional_string(payload.get("display_url")),
            expanded_url=_optional_string(payload.get("expanded_url")),
            width=_optional_int(payload.get("width")),
            height=_optional_int(payload.get("height")),
            alt_text=_optional_string(payload.get("alt_text")),
        )


@dataclass(frozen=True)
class SourceInfo:
    """Provenance needed to trace a normalized item back to its source."""

    path: str
    source_type: str
    row_number: int | None = None

    @classmethod
    def from_dict(cls, payload: Mapping[str, object] | None) -> SourceInfo | None:
        if not payload:
            return None
        path = payload.get("path")
        source_type = payload.get("source_type")
        if not isinstance(path, str) or not isinstance(source_type, str):
            raise ValueError("source.path and source.source_type must be strings.")
        return cls(
            path=path,
            source_type=source_type,
            row_number=_optional_int(payload.get("row_number")),
        )


@dataclass(frozen=True)
class RankItem:
    """Generic text item that can participate in preference comparisons."""

    id: str
    text: str
    created_at: str | None = None
    language: str | None = None
    url: str | None = None
    metrics: ItemMetrics = field(default_factory=ItemMetrics)
    author: AuthorInfo | None = None
    media: tuple[MediaItem, ...] = ()
    metadata: dict[str, JSONValue] = field(default_factory=dict)
    source: SourceInfo | None = None

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("RankItem.id must be non-empty.")
        if not self.text.strip():
            raise ValueError(f"RankItem {self.id!r} has empty text.")

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> RankItem:
        item_id = payload.get("id")
        text = payload.get("text")
        if not isinstance(item_id, (str, int)) or isinstance(item_id, bool):
            raise ValueError("item.id must be a string or integer.")
        if not isinstance(text, str):
            raise ValueError("item.text must be a string.")
        raw_media = payload.get("media", [])
        if not isinstance(raw_media, Sequence) or isinstance(raw_media, (str, bytes)):
            raise ValueError("item.media must be a list.")
        raw_metadata = payload.get("metadata", {})
        if not isinstance(raw_metadata, Mapping):
            raise ValueError("item.metadata must be an object.")
        return cls(
            id=str(item_id),
            text=text,
            created_at=_optional_string(
                payload.get("created_at", payload.get("createdAt"))
            ),
            language=_optional_string(payload.get("language", payload.get("lang"))),
            url=_optional_string(payload.get("url")),
            metrics=ItemMetrics.from_dict(
                payload.get("metrics")
                if isinstance(payload.get("metrics"), Mapping)
                else None
            ),
            author=AuthorInfo.from_dict(
                payload.get("author")
                if isinstance(payload.get("author"), Mapping)
                else None
            ),
            media=tuple(
                MediaItem.from_dict(entry)
                for entry in raw_media
                if isinstance(entry, Mapping)
            ),
            metadata=dict(raw_metadata),  # type: ignore[arg-type]
            source=SourceInfo.from_dict(
                payload.get("source")
                if isinstance(payload.get("source"), Mapping)
                else None
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    def preview(self, max_chars: int = 96) -> str:
        collapsed = " ".join(self.text.split())
        if len(collapsed) <= max_chars:
            return collapsed
        return collapsed[: max(1, max_chars - 1)].rstrip() + "…"


@dataclass(frozen=True)
class ComparisonTuple:
    """Selected item IDs and acquisition diagnostics for one provider call."""

    item_ids: tuple[str, ...]
    reason: str
    acquisition_score: float
    diagnostics: dict[str, float | str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if len(self.item_ids) < 2:
            raise ValueError("A comparison requires at least two items.")
        if len(set(self.item_ids)) != len(self.item_ids):
            raise ValueError("Comparison item IDs must be unique.")


@dataclass(frozen=True)
class AdjacentConfidence:
    better: str
    worse: str
    confidence: float


@dataclass(frozen=True)
class ComparisonResult:
    """Validated provider ranking for a pair or small tuple."""

    ranking: tuple[str, ...]
    confidence: float | None
    adjacent_confidences: tuple[AdjacentConfidence, ...]
    margin: str
    criterion_scores: dict[str, dict[str, float]]
    justification: str
    diagnostic_flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class PairwiseEvidence:
    winner_id: str
    loser_id: str
    weight: float
    confidence: float | None


@dataclass(frozen=True)
class PreferenceObservation:
    """Auditable record of one provider comparison and its derived evidence."""

    observation_id: str
    step: int
    created_at: str
    item_ids: tuple[str, ...]
    ranking: tuple[str, ...]
    evidence: tuple[PairwiseEvidence, ...]
    confidence: float | None
    adjacent_confidences: tuple[AdjacentConfidence, ...]
    margin: str
    criterion_scores: dict[str, dict[str, float]]
    justification: str
    provider: str
    model: str
    response_format: str | None
    acquisition_reason: str
    acquisition_score: float
    usage: dict[str, int] = field(default_factory=dict)
    provider_elapsed_seconds: float | None = None
    repeated_pair: bool = False
    diagnostic_flags: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> PreferenceObservation:
        return cls(
            observation_id=str(payload["observation_id"]),
            step=int(payload["step"]),
            created_at=str(payload["created_at"]),
            item_ids=tuple(str(value) for value in payload["item_ids"]),  # type: ignore[arg-type]
            ranking=tuple(str(value) for value in payload["ranking"]),  # type: ignore[arg-type]
            evidence=tuple(
                PairwiseEvidence(
                    winner_id=str(entry["winner_id"]),
                    loser_id=str(entry["loser_id"]),
                    weight=float(entry["weight"]),
                    confidence=(
                        float(entry["confidence"])
                        if entry.get("confidence") is not None
                        else None
                    ),
                )
                for entry in payload["evidence"]  # type: ignore[union-attr]
            ),
            confidence=(
                float(payload["confidence"])
                if payload.get("confidence") is not None
                else None
            ),
            adjacent_confidences=tuple(
                AdjacentConfidence(
                    better=str(entry["better"]),
                    worse=str(entry["worse"]),
                    confidence=float(entry["confidence"]),
                )
                for entry in payload.get("adjacent_confidences", [])  # type: ignore[union-attr]
            ),
            margin=str(payload["margin"]),
            criterion_scores={
                str(name): {str(key): float(value) for key, value in scores.items()}
                for name, scores in payload.get("criterion_scores", {}).items()  # type: ignore[union-attr]
            },
            justification=str(payload["justification"]),
            provider=str(payload["provider"]),
            model=str(payload["model"]),
            response_format=(
                str(payload["response_format"])
                if payload.get("response_format") is not None
                else None
            ),
            acquisition_reason=str(payload["acquisition_reason"]),
            acquisition_score=float(payload["acquisition_score"]),
            usage={
                str(key): int(value)
                for key, value in payload.get("usage", {}).items()  # type: ignore[union-attr]
            },
            provider_elapsed_seconds=(
                float(payload["provider_elapsed_seconds"])
                if payload.get("provider_elapsed_seconds") is not None
                else None
            ),
            repeated_pair=bool(payload.get("repeated_pair", False)),
            diagnostic_flags=tuple(
                str(value)
                for value in payload.get("diagnostic_flags", [])  # type: ignore[union-attr]
            ),
        )


@dataclass(frozen=True)
class RankingEntry:
    rank: int
    item_id: str
    score: float
    uncertainty: float
    metadata_prior: float
    comparisons: int
    wins: float
    losses: float
    last_justification: str | None = None
    diagnostic_flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class RankingState:
    step: int
    entries: tuple[RankingEntry, ...]
    score_by_id: dict[str, float]
    uncertainty_by_id: dict[str, float]
    pairwise_probabilities: dict[str, dict[str, float]]
    warnings: tuple[str, ...] = ()


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"Expected a string or null, got {type(value).__name__}.")
    stripped = value.strip()
    return stripped or None
