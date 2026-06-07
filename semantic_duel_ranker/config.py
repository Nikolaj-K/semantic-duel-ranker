"""
What: Runtime configuration and validated defaults for ranking runs.
Used by: CLI and run loop.
Deps: Python >= 3.11.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path

DEFAULT_OBJECTIVE = (
    "Rank these items by expected project value, where project value combines "
    "useful engagement potential, informativeness, clarity, originality, and "
    "topical relevance. Engagement metrics are context, not the sole target."
)

CONFIG_KEY_MAP = {
    "rank": {
        "input_path": "input_path",
        "loader": "loader",
        "provider": "provider",
        "top_k": "top_k",
        "budget": "budget",
        "tuple_size": "tuple_size",
        "objective": "objective",
        "seed": "seed",
        "use_metadata_prior": "use_metadata_prior",
        "prior_strength": "prior_strength",
        "regularization": "regularization",
        "matrix_window": "matrix_window",
        "snapshot_every": "snapshot_every",
        "preview_tweets": "preview_tweets",
    },
    "lmstudio": {
        "base_url": "lmstudio_base_url",
        "api_key": "lmstudio_api_key",
        "model": "model",
        "temperature": "temperature",
        "max_output_tokens": "max_output_tokens",
        "response_format": "lmstudio_response_format",
        "retries": "lmstudio_retries",
        "timeout_seconds": "timeout_seconds",
        "allow_non_loopback": "allow_non_loopback_lmstudio",
    },
    "mock": {
        "noise": "mock_noise",
    },
    "output": {
        "run_root": "run_root",
    },
}

ENVIRONMENT_KEY_MAP = {
    "SEMANTIC_DUEL_LMSTUDIO_BASE_URL": "lmstudio_base_url",
    "SEMANTIC_DUEL_LMSTUDIO_API_KEY": "lmstudio_api_key",
    "SEMANTIC_DUEL_MODEL": "model",
    "SEMANTIC_DUEL_RUN_ROOT": "run_root",
}


@dataclass(frozen=True)
class RunConfig:
    input_path: Path
    loader: str = "generic"
    provider: str = "lmstudio"
    top_k: int = 5
    budget: int = 25
    tuple_size: int = 2
    objective: str = DEFAULT_OBJECTIVE
    model: str | None = None
    temperature: float = 0.1
    max_output_tokens: int = 1200
    lmstudio_base_url: str = "http://127.0.0.1:1234/v1"
    lmstudio_api_key: str = "lm-studio"
    lmstudio_response_format: str = "auto"
    lmstudio_retries: int = 1
    allow_non_loopback_lmstudio: bool = False
    mock_noise: float = 0.35
    timeout_seconds: int = 900
    run_root: Path = Path("runs")
    seed: int = 42
    use_metadata_prior: bool = True
    prior_strength: float = 0.35
    regularization: float = 1.0
    matrix_window: int = 20
    snapshot_every: int = 1
    preview_tweets: int = 0

    def validate(self, item_count: int | None = None) -> None:
        if self.loader not in {"generic", "tweet"}:
            raise ValueError("loader must be generic or tweet.")
        if self.provider not in {"lmstudio", "mock"}:
            raise ValueError("provider must be lmstudio or mock.")
        if self.top_k <= 0:
            raise ValueError("top_k must be positive.")
        if self.budget <= 0:
            raise ValueError("budget must be positive.")
        if not 2 <= self.tuple_size <= 5:
            raise ValueError("tuple_size must be between 2 and 5.")
        if not 0.0 <= self.temperature <= 2.0:
            raise ValueError("temperature must be between 0 and 2.")
        if not 0.0 <= self.mock_noise <= 1.0:
            raise ValueError("mock_noise must be between 0 and 1.")
        if self.max_output_tokens <= 0 or self.timeout_seconds <= 0:
            raise ValueError("token and timeout limits must be positive.")
        if self.lmstudio_retries < 0:
            raise ValueError("lmstudio_retries must be non-negative.")
        if self.lmstudio_response_format not in {
            "auto",
            "json-schema",
            "json-object",
            "prompt-only",
        }:
            raise ValueError("Unsupported LM Studio response format.")
        if self.regularization <= 0:
            raise ValueError("regularization must be positive.")
        if self.prior_strength < 0:
            raise ValueError("prior_strength must be non-negative.")
        if self.matrix_window <= 0:
            raise ValueError("matrix_window must be positive.")
        if self.snapshot_every <= 0:
            raise ValueError("snapshot_every must be positive.")
        if self.preview_tweets < 0:
            raise ValueError("preview_tweets must be non-negative.")
        if item_count is not None:
            if item_count < 2:
                raise ValueError("At least two items are required.")
            if self.top_k > item_count:
                raise ValueError("top_k cannot exceed the number of items.")
            if self.tuple_size > item_count:
                raise ValueError("tuple_size cannot exceed the number of items.")

    def to_dict(self, *, include_secrets: bool = False) -> dict[str, object]:
        payload = asdict(self)
        if not include_secrets:
            payload.pop("lmstudio_api_key", None)
        payload["input_path"] = str(self.input_path)
        payload["run_root"] = str(self.run_root)
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> RunConfig:
        values = dict(payload)
        values["input_path"] = Path(str(values["input_path"]))
        values["run_root"] = Path(str(values.get("run_root", "runs")))
        return cls(**values)  # type: ignore[arg-type]


def load_config_overrides(path: Path | None) -> dict[str, object]:
    """Load a public-shaped TOML config and apply supported environment values."""

    values: dict[str, object] = {}
    if path is not None:
        expanded = path.expanduser()
        if not expanded.exists():
            raise ValueError(
                f"Config file not found: {expanded}. Copy config.example.toml to "
                "config.toml or pass an existing path with --config."
            )
        try:
            with expanded.open("rb") as handle:
                payload = tomllib.load(handle)
        except tomllib.TOMLDecodeError as exc:
            raise ValueError(f"Invalid TOML in {expanded}: {exc}") from exc
        unknown_sections = sorted(set(payload) - set(CONFIG_KEY_MAP))
        if unknown_sections:
            raise ValueError(
                f"Unknown config section(s) in {expanded}: "
                f"{', '.join(unknown_sections)}. Supported sections: "
                f"{', '.join(CONFIG_KEY_MAP)}."
            )
        for section_name, key_map in CONFIG_KEY_MAP.items():
            section = payload.get(section_name, {})
            if not isinstance(section, Mapping):
                raise ValueError(
                    f"Config section [{section_name}] in {expanded} must be a table."
                )
            unknown_keys = sorted(set(section) - set(key_map))
            if unknown_keys:
                raise ValueError(
                    f"Unknown key(s) in [{section_name}] in {expanded}: "
                    f"{', '.join(unknown_keys)}."
                )
            for source_key, target_key in key_map.items():
                if source_key in section:
                    values[target_key] = section[source_key]
        config_dir = expanded.resolve().parent
        for path_key in ("input_path", "run_root"):
            raw_value = values.get(path_key)
            if raw_value is not None:
                candidate = Path(str(raw_value)).expanduser()
                values[path_key] = (
                    candidate
                    if candidate.is_absolute()
                    else (config_dir / candidate).resolve()
                )

    for environment_key, config_key in ENVIRONMENT_KEY_MAP.items():
        environment_value = os.environ.get(environment_key)
        if environment_value:
            values[config_key] = (
                Path(environment_value).expanduser()
                if config_key == "run_root"
                else environment_value
            )
    return values
