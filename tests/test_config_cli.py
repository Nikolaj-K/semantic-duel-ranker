"""Configuration validation and CLI override regression tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from semantic_duel_ranker.cli import _config_from_args, build_parser, main
from semantic_duel_ranker.config import RunConfig, load_config_overrides


def test_mock_noise_round_trips_and_resume_preserves_it() -> None:
    parser = build_parser()
    initial_args = parser.parse_args(
        [
            "rank",
            "--input",
            "items.jsonl",
            "--provider",
            "mock",
            "--mock-noise",
            "0.12",
            "--preview-tweets",
            "3",
        ]
    )
    initial = _config_from_args(initial_args)
    restored = RunConfig.from_dict(initial.to_dict())

    resume_args = parser.parse_args(
        ["rank", "--resume", "runs/example", "--budget", "40"]
    )
    resumed = _config_from_args(resume_args, base=restored)

    assert initial.mock_noise == pytest.approx(0.12)
    assert restored.mock_noise == pytest.approx(0.12)
    assert resumed.mock_noise == pytest.approx(0.12)
    assert initial.preview_tweets == 3
    assert restored.preview_tweets == 3
    assert resumed.preview_tweets == 3


def test_legacy_config_uses_default_mock_noise() -> None:
    config = RunConfig(input_path=Path("items.jsonl"))
    legacy_payload = config.to_dict()
    legacy_payload.pop("mock_noise")

    restored = RunConfig.from_dict(legacy_payload)

    assert restored.mock_noise == pytest.approx(0.35)


def test_toml_config_resolves_paths_and_environment_overrides(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[rank]
input_path = "data/items.jsonl"
provider = "mock"
budget = 7

[lmstudio]
base_url = "http://127.0.0.1:9999/v1"

[output]
run_root = "output"
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv(
        "SEMANTIC_DUEL_LMSTUDIO_BASE_URL",
        "http://127.0.0.1:1234/v1",
    )

    values = load_config_overrides(config_path)

    assert values["input_path"] == (tmp_path / "data/items.jsonl").resolve()
    assert values["run_root"] == (tmp_path / "output").resolve()
    assert values["budget"] == 7
    assert values["lmstudio_base_url"] == "http://127.0.0.1:1234/v1"


def test_toml_config_rejects_unknown_key(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "[rank]\nunknown_setting = true\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=r"Unknown key.*unknown_setting"):
        load_config_overrides(config_path)


def test_rank_can_take_input_from_config(tmp_path: Path) -> None:
    sample_path = Path("test_data/sample_10_rank_items.jsonl").resolve()
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
[rank]
input_path = "{sample_path}"
provider = "mock"
top_k = 3
budget = 1

[output]
run_root = "{tmp_path / "runs"}"
""".strip(),
        encoding="utf-8",
    )

    exit_code = main(["rank", "--config", str(config_path), "--limit", "3"])

    assert exit_code == 0
    assert len(list((tmp_path / "runs").iterdir())) == 1


def test_mock_provider_uses_mock_model_label_despite_lmstudio_config(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[rank]
input_path = "items.jsonl"
provider = "mock"

[lmstudio]
model = "unused-real-model"
""".strip(),
        encoding="utf-8",
    )
    args = build_parser().parse_args(["rank", "--config", str(config_path)])

    config = _config_from_args(args)

    assert config.model == "mock-hidden-score"


def test_invalid_limit_is_rejected_before_loading_input(capsys) -> None:
    exit_code = main(
        [
            "rank",
            "--input",
            "missing.jsonl",
            "--provider",
            "mock",
            "--limit",
            "0",
        ]
    )

    assert exit_code == 2
    assert "--limit must be at least one" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("temperature", -0.1),
        ("mock_noise", 1.1),
        ("matrix_window", 0),
        ("snapshot_every", 0),
        ("preview_tweets", -1),
    ],
)
def test_config_rejects_invalid_numeric_ranges(field: str, value: float) -> None:
    values = RunConfig(input_path=Path("items.jsonl")).to_dict()
    values[field] = value
    config = RunConfig.from_dict(values)

    with pytest.raises(ValueError):
        config.validate(item_count=10)
