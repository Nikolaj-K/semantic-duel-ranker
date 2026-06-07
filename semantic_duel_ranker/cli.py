#!/usr/bin/env python
"""
What: Command-line entrypoint for dataset inspection, sampling, and ranking.
Run:
    semantic-duel-ranker inspect-tweets --input RAW.json
    semantic-duel-ranker extract-sample --input RAW.json --output sample.jsonl
    semantic-duel-ranker rank --provider mock --input sample.jsonl
    semantic-duel-ranker list-models
Deps: Install this project with `python -m pip install -e .`.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

from semantic_duel_ranker.artifacts import ArtifactStore
from semantic_duel_ranker.config import RunConfig, load_config_overrides
from semantic_duel_ranker.input_loader import load_rank_items, write_rank_items_jsonl
from semantic_duel_ranker.providers import LMStudioProvider, MockProvider, ProviderError
from semantic_duel_ranker.run_loop import run_ranking
from semantic_duel_ranker.tweet_loader import (
    inspect_tweet_dataset,
    load_normalized_tweets,
    select_curated_sample,
    select_stratified_sample,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="semantic-duel-ranker",
        description="Local-first active preference ranking for short text items.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser(
        "inspect-tweets",
        help="Inspect a tweet-scraper JSON array without dumping its records.",
    )
    inspect_parser.add_argument("--input", required=True, type=Path)

    extract_parser = subparsers.add_parser(
        "extract-sample",
        help="Normalize and extract a small tweet development fixture.",
    )
    extract_parser.add_argument("--input", required=True, type=Path)
    extract_parser.add_argument("--output", required=True, type=Path)
    extract_parser.add_argument("--count", type=int, default=10)
    extract_parser.add_argument("--seed", type=int, default=42)
    extract_parser.add_argument(
        "--strategy",
        choices=("curated", "stratified"),
        default="curated",
        help="Use reviewed IDs for the supplied test dataset or general stratification.",
    )

    models_parser = subparsers.add_parser(
        "list-models",
        help="List model IDs from the configured local LM Studio endpoint.",
    )
    models_parser.add_argument(
        "--config",
        type=Path,
        help="Optional TOML configuration file. CLI flags override its values.",
    )
    _add_lmstudio_arguments(models_parser, nullable_defaults=True)

    rank_parser = subparsers.add_parser(
        "rank",
        help="Run or resume active preference ranking.",
    )
    rank_parser.add_argument("--input", type=Path)
    rank_parser.add_argument("--resume", type=Path)
    rank_parser.add_argument(
        "--config",
        type=Path,
        help="Optional TOML configuration file. CLI flags override its values.",
    )
    rank_parser.add_argument("--loader", choices=("generic", "tweet"))
    rank_parser.add_argument("--provider", choices=("lmstudio", "mock"))
    rank_parser.add_argument("--limit", type=int)
    rank_parser.add_argument("--top-k", type=int)
    rank_parser.add_argument("--budget", type=int)
    rank_parser.add_argument("--tuple-size", type=int)
    rank_parser.add_argument(
        "--objective",
        help="Objective text or path to a UTF-8 text file.",
    )
    rank_parser.add_argument("--run-dir", type=Path)
    rank_parser.add_argument("--seed", type=int)
    rank_parser.add_argument("--prior-strength", type=float)
    rank_parser.add_argument("--regularization", type=float)
    rank_parser.add_argument("--matrix-window", type=int)
    rank_parser.add_argument("--snapshot-every", type=int)
    rank_parser.add_argument(
        "--preview-tweets",
        type=int,
        help=(
            "Print the first N loaded items in full, with the same labels used "
            "in ranking tables and probability matrices."
        ),
    )
    rank_parser.add_argument(
        "--no-metadata-prior",
        action="store_true",
        help="Initialize all item priors at zero.",
    )
    rank_parser.add_argument(
        "--mock-noise",
        type=float,
        default=None,
        help="Standard deviation of mock comparison noise.",
    )
    _add_lmstudio_arguments(rank_parser, nullable_defaults=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "inspect-tweets":
            return _inspect_tweets(args)
        if args.command == "extract-sample":
            return _extract_sample(args)
        if args.command == "list-models":
            return _list_models(args)
        if args.command == "rank":
            return _rank(args)
    except (OSError, ValueError, ProviderError, RuntimeError) as exc:
        print(f"semantic-duel-ranker: {exc}", file=sys.stderr)
        return 2
    raise AssertionError(f"Unhandled command: {args.command}")


def _inspect_tweets(args: argparse.Namespace) -> int:
    inspection = inspect_tweet_dataset(args.input)
    print(json.dumps(inspection.to_dict(), indent=2, ensure_ascii=False))
    return 0


def _extract_sample(args: argparse.Namespace) -> int:
    if args.count < 2:
        raise ValueError("--count must be at least two.")
    items = load_normalized_tweets(args.input)
    if args.strategy == "curated":
        if args.count != 10:
            raise ValueError("The curated strategy contains exactly 10 reviewed items.")
        selected = select_curated_sample(items)
    else:
        selected = select_stratified_sample(
            items,
            count=args.count,
            seed=args.seed,
        )
    write_rank_items_jsonl(args.output, selected)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "items": len(selected),
                "ids": [item.id for item in selected],
                "media_items": sum(len(item.media) for item in selected),
            },
            indent=2,
        )
    )
    return 0


def _list_models(args: argparse.Namespace) -> int:
    config = _lmstudio_config_from_args(args)
    provider = _lmstudio_provider_from_config(config)
    model_ids = provider.list_model_ids()
    for model_id in model_ids:
        print(model_id)
    print(
        f"[semantic-duel] LM Studio models | base_url={provider.base_url} "
        f"| count={len(model_ids)}",
        file=sys.stderr,
    )
    return 0


def _rank(args: argparse.Namespace) -> int:
    if args.input and args.resume:
        raise ValueError("Specify only one of --input or --resume.")
    if args.resume and args.config:
        raise ValueError(
            "--config cannot be combined with --resume because the run already "
            "contains config.json. Use CLI flags to override resumable settings."
        )
    if args.limit is not None and args.limit < 1:
        raise ValueError("--limit must be at least one.")
    if args.resume:
        store = ArtifactStore.resume(args.resume)
        stored_config = _load_stored_config(store.run_dir / "config.json")
        config = _config_from_args(args, base=stored_config)
        items = store.load_items()
        config.validate(len(items))
        store.write_config(config)
        observations = store.load_observations()
        progress = store.load_progress()
        attempts_completed = int(progress.get("attempts_completed", 0))
        provider_errors = int(progress.get("provider_errors", 0))
        if config.budget <= attempts_completed:
            raise ValueError(
                f"Run already completed {attempts_completed} attempts; "
                "resume with a larger --budget."
            )
    else:
        config = _config_from_args(args)
        items = (
            load_normalized_tweets(
                config.input_path,
                limit=args.limit,
            )
            if config.loader == "tweet"
            else load_rank_items(config.input_path, limit=args.limit)
        )
        config.validate(len(items))
        store = ArtifactStore.create(
            run_root=config.run_root,
            config=config,
            items=items,
        )
        observations = []
        attempts_completed = 0
        provider_errors = 0

    provider = (
        MockProvider(seed=config.seed, noise=config.mock_noise)
        if config.provider == "mock"
        else _lmstudio_provider_from_config(config)
    )
    run_ranking(
        items=items,
        config=config,
        provider=provider,
        store=store,
        existing_observations=observations,
        attempts_completed=attempts_completed,
        provider_errors=provider_errors,
    )
    return 0


def _config_from_args(
    args: argparse.Namespace,
    *,
    base: RunConfig | None = None,
) -> RunConfig:
    configured_values: dict[str, object] = {}
    if base is None:
        configured_values = load_config_overrides(args.config)
        configured_input = configured_values.pop("input_path", None)
        input_path = args.input or configured_input
        if input_path is None:
            raise ValueError(
                "Missing input data. Pass --input PATH or set "
                "[rank].input_path in the TOML file supplied with --config."
            )
        defaults = RunConfig(
            input_path=Path(str(input_path)),
            **configured_values,  # type: ignore[arg-type]
        )
    else:
        environment_values = load_config_overrides(None)
        environment_values.pop("input_path", None)
        environment_values.pop("run_root", None)
        defaults = replace(base, **environment_values)
    provider = args.provider or defaults.provider
    if provider == "mock":
        model = "mock-hidden-score"
    elif args.model:
        model = args.model
    else:
        model = defaults.model
    objective = (
        _read_objective(args.objective) if args.objective else defaults.objective
    )
    return RunConfig(
        input_path=args.input or defaults.input_path,
        loader=args.loader or defaults.loader,
        provider=provider,
        top_k=args.top_k if args.top_k is not None else defaults.top_k,
        budget=args.budget if args.budget is not None else defaults.budget,
        tuple_size=(
            args.tuple_size if args.tuple_size is not None else defaults.tuple_size
        ),
        objective=objective,
        model=model,
        temperature=(
            args.temperature if args.temperature is not None else defaults.temperature
        ),
        max_output_tokens=(
            args.max_output_tokens
            if args.max_output_tokens is not None
            else defaults.max_output_tokens
        ),
        lmstudio_base_url=(
            args.lmstudio_base_url
            if args.lmstudio_base_url is not None
            else defaults.lmstudio_base_url
        ),
        lmstudio_api_key=(
            args.lmstudio_api_key
            if args.lmstudio_api_key is not None
            else defaults.lmstudio_api_key
        ),
        lmstudio_response_format=(
            args.lmstudio_response_format
            if args.lmstudio_response_format is not None
            else defaults.lmstudio_response_format
        ),
        lmstudio_retries=(
            args.lmstudio_retries
            if args.lmstudio_retries is not None
            else defaults.lmstudio_retries
        ),
        allow_non_loopback_lmstudio=(
            args.allow_non_loopback_lmstudio or defaults.allow_non_loopback_lmstudio
        ),
        mock_noise=(
            args.mock_noise if args.mock_noise is not None else defaults.mock_noise
        ),
        timeout_seconds=(
            args.timeout_seconds
            if args.timeout_seconds is not None
            else defaults.timeout_seconds
        ),
        run_root=args.run_dir or defaults.run_root,
        seed=args.seed if args.seed is not None else defaults.seed,
        use_metadata_prior=(
            False if args.no_metadata_prior else defaults.use_metadata_prior
        ),
        prior_strength=(
            args.prior_strength
            if args.prior_strength is not None
            else defaults.prior_strength
        ),
        regularization=(
            args.regularization
            if args.regularization is not None
            else defaults.regularization
        ),
        matrix_window=(
            args.matrix_window
            if args.matrix_window is not None
            else defaults.matrix_window
        ),
        snapshot_every=(
            args.snapshot_every
            if args.snapshot_every is not None
            else defaults.snapshot_every
        ),
        preview_tweets=(
            args.preview_tweets
            if args.preview_tweets is not None
            else defaults.preview_tweets
        ),
    )


def _add_lmstudio_arguments(
    parser: argparse.ArgumentParser,
    *,
    nullable_defaults: bool,
) -> None:
    default = None if nullable_defaults else "http://127.0.0.1:1234/v1"
    parser.add_argument(
        "--model", default=None if nullable_defaults else "gemma-4-12b-it"
    )
    parser.add_argument(
        "--temperature", type=float, default=None if nullable_defaults else 0.1
    )
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=None if nullable_defaults else 1200,
    )
    parser.add_argument("--lmstudio-base-url", default=default)
    parser.add_argument(
        "--lmstudio-api-key",
        default=None if nullable_defaults else "lm-studio",
    )
    parser.add_argument(
        "--lmstudio-retries",
        type=int,
        default=None if nullable_defaults else 1,
    )
    parser.add_argument(
        "--lmstudio-response-format",
        choices=("auto", "json-schema", "json-object", "prompt-only"),
        default=None if nullable_defaults else "auto",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=None if nullable_defaults else 900,
    )
    parser.add_argument(
        "--allow-non-loopback-lmstudio",
        action="store_true",
    )


def _lmstudio_config_from_args(args: argparse.Namespace) -> RunConfig:
    configured_values = load_config_overrides(args.config)
    configured_values.pop("input_path", None)
    defaults = RunConfig(
        input_path=Path("items.jsonl"),
        **configured_values,  # type: ignore[arg-type]
    )
    return RunConfig(
        **{
            **defaults.to_dict(include_secrets=True),
            "input_path": defaults.input_path,
            "run_root": defaults.run_root,
            "model": args.model or defaults.model,
            "temperature": (
                args.temperature
                if args.temperature is not None
                else defaults.temperature
            ),
            "max_output_tokens": (
                args.max_output_tokens
                if args.max_output_tokens is not None
                else defaults.max_output_tokens
            ),
            "lmstudio_base_url": (
                args.lmstudio_base_url
                if args.lmstudio_base_url is not None
                else defaults.lmstudio_base_url
            ),
            "lmstudio_api_key": (
                args.lmstudio_api_key
                if args.lmstudio_api_key is not None
                else defaults.lmstudio_api_key
            ),
            "lmstudio_retries": (
                args.lmstudio_retries
                if args.lmstudio_retries is not None
                else defaults.lmstudio_retries
            ),
            "lmstudio_response_format": (
                args.lmstudio_response_format
                if args.lmstudio_response_format is not None
                else defaults.lmstudio_response_format
            ),
            "timeout_seconds": (
                args.timeout_seconds
                if args.timeout_seconds is not None
                else defaults.timeout_seconds
            ),
            "allow_non_loopback_lmstudio": (
                args.allow_non_loopback_lmstudio or defaults.allow_non_loopback_lmstudio
            ),
        }
    )


def _lmstudio_provider_from_config(config: RunConfig) -> LMStudioProvider:
    return LMStudioProvider(
        base_url=config.lmstudio_base_url,
        api_key=config.lmstudio_api_key,
        model=config.model,
        temperature=config.temperature,
        max_output_tokens=config.max_output_tokens,
        retries=config.lmstudio_retries,
        response_format=config.lmstudio_response_format,
        timeout_seconds=config.timeout_seconds,
        allow_non_loopback=config.allow_non_loopback_lmstudio,
    )


def _read_objective(value: str) -> str:
    candidate = Path(value).expanduser()
    if candidate.exists():
        text = candidate.read_text(encoding="utf-8").strip()
    else:
        text = value.strip()
    if not text:
        raise ValueError("Ranking objective cannot be empty.")
    return text


def _load_stored_config(path: Path) -> RunConfig:
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"Stored config must be an object: {path}")
    return RunConfig.from_dict(payload)


if __name__ == "__main__":
    raise SystemExit(main())
