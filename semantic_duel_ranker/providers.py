"""
What: Local LM Studio and deterministic mock comparison providers.
Used by: run loop and CLI diagnostics.
Deps: requests; Python >= 3.11.

Provider output is untrusted. Every successful response is parsed and validated
against the exact item IDs before it becomes ranking evidence.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

import requests

from semantic_duel_ranker.models import ComparisonResult, RankItem
from semantic_duel_ranker.schema import (
    comparison_response_schema,
    parse_assistant_json,
    parse_comparison_response,
)


@dataclass(frozen=True)
class ProviderCallResult:
    comparison: ComparisonResult | None
    error: str | None
    model: str
    response_format: str | None
    usage: dict[str, int]
    elapsed_seconds: float


class ComparisonProvider(Protocol):
    name: str

    def model_label(self) -> str: ...

    def compare(
        self,
        *,
        items: list[RankItem],
        prompt: str,
        artifact_dir: Path,
    ) -> ProviderCallResult: ...


class ProviderError(ValueError):
    pass


class LMStudioProvider:
    name = "lmstudio"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str | None,
        temperature: float,
        max_output_tokens: int,
        retries: int,
        response_format: str,
        timeout_seconds: int,
        allow_non_loopback: bool,
    ) -> None:
        self.base_url = normalize_base_url(base_url)
        self.api_key = api_key
        self.explicit_model = model
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens
        self.retries = retries
        self.response_format = response_format
        self.timeout_seconds = timeout_seconds
        self.allow_non_loopback = allow_non_loopback
        self._inferred_model: str | None = None
        self._working_response_format: str | None = None

    def require_ready(self) -> None:
        validate_lmstudio_base_url(
            self.base_url,
            allow_non_loopback=self.allow_non_loopback,
        )

    def model_label(self) -> str:
        return self.explicit_model or self._inferred_model or "(lmstudio auto)"

    def endpoint(self, suffix: str) -> str:
        return f"{self.base_url}/{suffix.lstrip('/')}"

    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def list_model_ids(self) -> list[str]:
        self.require_ready()
        try:
            response = requests.get(
                self.endpoint("models"),
                headers=self.headers(),
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.exceptions.RequestException as exc:
            raise ProviderError(f"lmstudio_models_unreachable: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise ProviderError(f"lmstudio_models_invalid_json: {exc}") from exc
        raw_models = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(raw_models, list):
            raise ProviderError("lmstudio_models_invalid_shape: expected data list")
        return [
            str(model["id"])
            for model in raw_models
            if isinstance(model, dict)
            and isinstance(model.get("id"), str)
            and model["id"]
        ]

    def selected_model(self) -> str:
        if self.explicit_model:
            return self.explicit_model
        if self._inferred_model:
            return self._inferred_model
        model_ids = self.list_model_ids()
        chat_models = [model_id for model_id in model_ids if _is_chat_model(model_id)]
        if len(chat_models) == 1:
            self._inferred_model = chat_models[0]
            return self._inferred_model
        if not chat_models:
            raise ProviderError(
                "lmstudio_no_chat_model: load a chat model or pass --model. "
                f"Available: {', '.join(model_ids) or '(none)'}"
            )
        raise ProviderError(
            "lmstudio_ambiguous_model: pass --model explicitly. "
            f"Available chat models: {', '.join(chat_models)}"
        )

    def compare(
        self,
        *,
        items: list[RankItem],
        prompt: str,
        artifact_dir: Path,
    ) -> ProviderCallResult:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        started = time.perf_counter()
        try:
            self.require_ready()
            model = self.selected_model()
        except ProviderError as exc:
            _write_error(artifact_dir, str(exc))
            return ProviderCallResult(
                comparison=None,
                error=str(exc),
                model=self.model_label(),
                response_format=None,
                usage={},
                elapsed_seconds=time.perf_counter() - started,
            )

        schema = comparison_response_schema([item.id for item in items])
        _write_json(artifact_dir / "response_schema.json", schema)
        last_error = "lmstudio_request_not_attempted"
        for response_format in self._formats_to_try():
            for retry_index in range(self.retries + 1):
                attempt = retry_index + 1
                result, compatibility_error = self._call_once(
                    items=items,
                    prompt=prompt,
                    schema=schema,
                    model=model,
                    response_format=response_format,
                    artifact_dir=artifact_dir,
                    attempt=attempt,
                    started=started,
                )
                if result.error is None:
                    self._working_response_format = response_format
                    return result
                last_error = result.error
                if compatibility_error:
                    break
                if retry_index >= self.retries:
                    _write_error(artifact_dir, last_error)
                    return result
        _write_error(artifact_dir, last_error)
        return ProviderCallResult(
            comparison=None,
            error=last_error,
            model=model,
            response_format=None,
            usage={},
            elapsed_seconds=time.perf_counter() - started,
        )

    def _call_once(
        self,
        *,
        items: list[RankItem],
        prompt: str,
        schema: dict[str, object],
        model: str,
        response_format: str,
        artifact_dir: Path,
        attempt: int,
        started: float,
    ) -> tuple[ProviderCallResult, bool]:
        body = self._request_body(
            prompt=prompt,
            schema=schema,
            model=model,
            response_format=response_format,
        )
        _write_json(artifact_dir / "request.json", body)
        _write_json(artifact_dir / f"request_attempt_{attempt:02d}.json", body)
        try:
            response = requests.post(
                self.endpoint("chat/completions"),
                headers=self.headers(),
                data=json.dumps(body, ensure_ascii=False),
                timeout=self.timeout_seconds,
            )
        except requests.exceptions.RequestException as exc:
            error = f"lmstudio_request_failed: {exc}"
            _write_error(artifact_dir, error, attempt=attempt)
            return (
                ProviderCallResult(
                    comparison=None,
                    error=error,
                    model=model,
                    response_format=response_format,
                    usage={},
                    elapsed_seconds=time.perf_counter() - started,
                ),
                False,
            )
        response_text = response.text
        (artifact_dir / "response_text.txt").write_text(response_text, encoding="utf-8")
        (artifact_dir / f"response_text_attempt_{attempt:02d}.txt").write_text(
            response_text, encoding="utf-8"
        )
        try:
            response_payload = response.json()
            _write_json(artifact_dir / "response.json", response_payload)
            _write_json(
                artifact_dir / f"response_attempt_{attempt:02d}.json",
                response_payload,
            )
        except json.JSONDecodeError:
            response_payload = None
        usage = _normalize_usage(response_payload)
        if response.status_code >= 400:
            error = (
                f"lmstudio_http_{response.status_code}: " f"{_compact(response_text)}"
            )
            _write_error(artifact_dir, error, attempt=attempt)
            compatibility = _is_format_compatibility_error(
                response.status_code,
                response_text,
                response_format=response_format,
            )
            return (
                ProviderCallResult(
                    comparison=None,
                    error=error,
                    model=model,
                    response_format=response_format,
                    usage=usage,
                    elapsed_seconds=time.perf_counter() - started,
                ),
                compatibility,
            )
        try:
            assistant_message = _extract_assistant_message(response_payload)
            (artifact_dir / "assistant_message.txt").write_text(
                assistant_message, encoding="utf-8"
            )
            (artifact_dir / f"assistant_message_attempt_{attempt:02d}.txt").write_text(
                assistant_message, encoding="utf-8"
            )
            payload = parse_assistant_json(assistant_message)
            comparison = parse_comparison_response(
                payload,
                expected_item_ids=[item.id for item in items],
            )
        except ValueError as exc:
            error = f"lmstudio_response_parse_failed: {exc}"
            _write_error(artifact_dir, error, attempt=attempt)
            return (
                ProviderCallResult(
                    comparison=None,
                    error=error,
                    model=model,
                    response_format=response_format,
                    usage=usage,
                    elapsed_seconds=time.perf_counter() - started,
                ),
                False,
            )
        return (
            ProviderCallResult(
                comparison=comparison,
                error=None,
                model=model,
                response_format=response_format,
                usage=usage,
                elapsed_seconds=time.perf_counter() - started,
            ),
            False,
        )

    def _formats_to_try(self) -> list[str]:
        if self.response_format != "auto":
            return [self.response_format]
        if self._working_response_format:
            return [self._working_response_format]
        return ["json-schema", "json-object", "prompt-only"]

    def _request_body(
        self,
        *,
        prompt: str,
        schema: dict[str, object],
        model: str,
        response_format: str,
    ) -> dict[str, object]:
        body: dict[str, object] = {
            "model": model,
            "temperature": self.temperature,
            "max_tokens": self.max_output_tokens,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        }
        if response_format == "json-schema":
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "semantic_duel_comparison",
                    "strict": True,
                    "schema": schema,
                },
            }
        elif response_format == "json-object":
            body["response_format"] = {"type": "json_object"}
        elif response_format != "prompt-only":
            raise ValueError(f"Unknown response format: {response_format}")
        return body


class MockProvider:
    name = "mock"

    def __init__(self, *, seed: int = 42, noise: float = 0.35) -> None:
        self.seed = seed
        self.noise = noise

    def model_label(self) -> str:
        return f"mock-hidden-score-seed-{self.seed}"

    def compare(
        self,
        *,
        items: list[RankItem],
        prompt: str,
        artifact_dir: Path,
    ) -> ProviderCallResult:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        started = time.perf_counter()
        _write_json(
            artifact_dir / "request.json",
            {
                "provider": "mock",
                "model": self.model_label(),
                "prompt": prompt,
                "item_ids": [item.id for item in items],
                "noise": self.noise,
            },
        )
        schema = comparison_response_schema([item.id for item in items])
        _write_json(artifact_dir / "response_schema.json", schema)
        step_rng = random.Random(
            f"{self.seed}:{artifact_dir.name}:{','.join(item.id for item in items)}"
        )
        criterion_scores = {
            criterion: {
                item.id: score
                for item, score in zip(
                    items,
                    self._criterion_values(items, criterion),
                    strict=True,
                )
            }
            for criterion in (
                "engagement_potential",
                "informativeness",
                "clarity",
                "originality",
                "topical_relevance",
            )
        }
        hidden = {
            item.id: sum(scores[item.id] for scores in criterion_scores.values()) / 5
            + step_rng.gauss(0.0, self.noise)
            for item in items
        }
        ranking = tuple(sorted(hidden, key=hidden.get, reverse=True))  # type: ignore[arg-type]
        gaps = [
            max(0.0, hidden[ranking[index]] - hidden[ranking[index + 1]])
            for index in range(len(ranking) - 1)
        ]
        adjacent = [
            {
                "better": ranking[index],
                "worse": ranking[index + 1],
                "confidence": _gap_confidence(gap),
            }
            for index, gap in enumerate(gaps)
        ]
        payload = {
            "ranking": list(ranking),
            "confidence": _gap_confidence(gaps[0]) if len(items) == 2 else None,
            "adjacent_confidences": [] if len(items) == 2 else adjacent,
            "margin": _margin_from_gap(min(gaps)),
            "criterion_scores": [
                {
                    "criterion": criterion,
                    "scores": [
                        {"item_id": item.id, "score": round(scores[item.id], 2)}
                        for item in items
                    ],
                }
                for criterion, scores in criterion_scores.items()
            ],
            "justification": (
                f"{ranking[0]} leads on the mock objective score "
                f"({hidden[ranking[0]]:.2f} vs {hidden[ranking[1]]:.2f}); "
                "metrics contribute but text-derived criteria also affect the result."
            ),
        }
        _write_json(artifact_dir / "response.json", payload)
        response_text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        (artifact_dir / "response_text.txt").write_text(response_text, encoding="utf-8")
        (artifact_dir / "assistant_message.txt").write_text(
            response_text, encoding="utf-8"
        )
        comparison = parse_comparison_response(
            payload,
            expected_item_ids=[item.id for item in items],
        )
        return ProviderCallResult(
            comparison=comparison,
            error=None,
            model=self.model_label(),
            response_format="mock-json",
            usage={},
            elapsed_seconds=time.perf_counter() - started,
        )

    def _criterion_values(
        self,
        items: list[RankItem],
        criterion: str,
    ) -> list[float]:
        values: list[float] = []
        for item in items:
            text = " ".join(item.text.split())
            metrics = item.metrics
            if criterion == "engagement_potential":
                raw = math.log1p(
                    (metrics.likes or 0)
                    + 2 * (metrics.reposts or 0)
                    + (metrics.replies or 0)
                )
                value = min(10.0, 2.0 + 1.2 * raw)
            elif criterion == "informativeness":
                value = min(10.0, 2.5 + len(text) / 65)
            elif criterion == "clarity":
                sentence_penalty = max(0.0, len(text) / 280 - 1)
                value = 7.5 - sentence_penalty - 1.5 * text.startswith("@")
            elif criterion == "originality":
                digest = hashlib.sha256(item.id.encode()).digest()[0]
                value = 3.5 + 5.0 * digest / 255
            else:
                value = 5.5 + 0.5 * bool(item.media)
            values.append(float(min(10.0, max(0.0, value))))
        return values


def normalize_base_url(base_url: str) -> str:
    stripped = base_url.strip().rstrip("/")
    if not stripped:
        raise ProviderError("LM Studio base URL is empty.")
    return stripped


def validate_lmstudio_base_url(
    base_url: str,
    *,
    allow_non_loopback: bool,
) -> None:
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ProviderError(f"Invalid LM Studio base URL: {base_url}")
    if allow_non_loopback:
        return
    host = parsed.hostname
    if not host:
        raise ProviderError(f"Invalid LM Studio base URL host: {base_url}")
    if host == "localhost":
        return
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ProviderError(
            f"Refusing non-loopback LM Studio host {host!r}; "
            "pass --allow-non-loopback-lmstudio to opt in."
        ) from exc
    if not address.is_loopback:
        raise ProviderError(
            f"Refusing non-loopback LM Studio host {host!r}; "
            "pass --allow-non-loopback-lmstudio to opt in."
        )


def _extract_assistant_message(response_payload: object) -> str:
    if not isinstance(response_payload, dict):
        raise ValueError("LM Studio response must be an object.")
    choices = response_payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("LM Studio choices must be a non-empty list.")
    first = choices[0]
    message = first.get("message") if isinstance(first, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str):
        raise ValueError("LM Studio assistant content must be a string.")
    return content.strip()


def _normalize_usage(response_payload: object) -> dict[str, int]:
    if not isinstance(response_payload, dict):
        return {}
    usage = response_payload.get("usage")
    if not isinstance(usage, dict):
        return {}
    mapping = {
        "prompt_tokens": "input_tokens",
        "completion_tokens": "output_tokens",
        "total_tokens": "total_tokens",
    }
    return {
        normalized: usage[raw]
        for raw, normalized in mapping.items()
        if isinstance(usage.get(raw), int)
    }


def _is_chat_model(model_id: str) -> bool:
    lowered = model_id.lower()
    return not any(
        marker in lowered for marker in ("embed", "embedding", "nomic", "bge-", "e5-")
    )


def _is_format_compatibility_error(
    status_code: int,
    response_text: str,
    *,
    response_format: str,
) -> bool:
    if response_format == "prompt-only" or status_code not in {400, 422}:
        return False
    lowered = response_text.lower()
    return any(
        marker in lowered
        for marker in ("response_format", "json_schema", "json_object", "structured")
    )


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_error(path: Path, error: str, *, attempt: int | None = None) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "provider_error.txt").write_text(error + "\n", encoding="utf-8")
    if attempt is not None:
        (path / f"provider_error_attempt_{attempt:02d}.txt").write_text(
            error + "\n", encoding="utf-8"
        )


def _compact(text: str, max_chars: int = 500) -> str:
    return " ".join(text.split())[:max_chars]


def _gap_confidence(gap: float) -> float:
    return round(min(0.96, 0.5 + 0.35 * (1.0 - math.exp(-gap / 2))), 3)


def _margin_from_gap(gap: float) -> str:
    if gap < 0.2:
        return "negligible"
    if gap < 0.6:
        return "slight"
    if gap < 1.4:
        return "moderate"
    if gap < 2.5:
        return "strong"
    return "decisive"
