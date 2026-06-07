"""
What: Orchestrate active selection, provider comparison, model updates, and audit.
Used by: CLI.
Deps: package runtime dependencies.
"""

from __future__ import annotations

import datetime as dt
import time
from collections.abc import Sequence

from semantic_duel_ranker.acquisition import AcquisitionPolicy
from semantic_duel_ranker.artifacts import ArtifactStore
from semantic_duel_ranker.config import RunConfig
from semantic_duel_ranker.logging_utils import RunClock, RunLogger, format_duration
from semantic_duel_ranker.metrics_prior import compute_metadata_prior
from semantic_duel_ranker.models import PreferenceObservation, RankItem
from semantic_duel_ranker.observations import build_observation
from semantic_duel_ranker.prompt_builder import build_comparison_prompt
from semantic_duel_ranker.providers import ComparisonProvider
from semantic_duel_ranker.rank_model import BradleyTerryModel
from semantic_duel_ranker.visualization import TerminalVisualizer, ranking_movement


def run_ranking(
    *,
    items: list[RankItem],
    config: RunConfig,
    provider: ComparisonProvider,
    store: ArtifactStore,
    existing_observations: list[PreferenceObservation] | None = None,
    attempts_completed: int = 0,
    provider_errors: int = 0,
) -> None:
    config.validate(len(items))
    observations = list(existing_observations or [])
    prior = (
        compute_metadata_prior(items)
        if config.use_metadata_prior
        else {item.id: 0.0 for item in items}
    )
    model = BradleyTerryModel(
        items,
        metadata_prior=prior,
        prior_strength=config.prior_strength,
        regularization=config.regularization,
    )
    state = model.fit(observations, step=attempts_completed)
    policy = AcquisitionPolicy(
        top_k=config.top_k,
        tuple_size=config.tuple_size,
        seed=config.seed,
    )
    logger = RunLogger()
    visualizer = TerminalVisualizer(
        items=items,
        top_k=config.top_k,
        matrix_window=config.matrix_window,
        console=logger.console,
    )
    clock = RunClock.start_now()
    successful_call_durations = [
        observation.provider_elapsed_seconds
        for observation in observations
        if observation.provider_elapsed_seconds is not None
    ]
    attempted_call_durations: list[float] = []

    visualizer.show_reader_introduction(
        provider=config.provider,
        model=provider.model_label(),
        objective=config.objective,
        comparison_budget=config.budget,
        tuple_size=config.tuple_size,
        top_k=config.top_k,
        resumed=attempts_completed > 0,
    )
    logger.event(
        "Run started" if attempts_completed == 0 else "Run resumed",
        clock=clock,
        provider=config.provider,
        model=provider.model_label(),
        items=len(items),
        top_k=config.top_k,
        budget=config.budget,
        tuple_size=config.tuple_size,
        observations=len(observations),
        run_dir=store.run_dir,
    )
    visualizer.show_model_explanation()
    visualizer.show_item_previews(config.preview_tweets)
    visualizer.show_item_key()
    visualizer.show_state(state)
    previous_order = tuple(entry.item_id for entry in state.entries)

    for step in range(attempts_completed + 1, config.budget + 1):
        step_started = time.perf_counter()
        comparison = policy.select(
            state=state,
            model=model,
            observations=observations,
            next_step=step,
        )
        compared_items = [
            _item_by_id(items, item_id) for item_id in comparison.item_ids
        ]
        visualizer.show_selection(comparison, step=step, budget=config.budget)
        logger.event(
            "Comparison selected",
            clock=clock,
            step=step,
            budget=config.budget,
            items=comparison.item_ids,
            reason=comparison.reason,
            score=comparison.acquisition_score,
        )
        for key, value in comparison.diagnostics.items():
            logger.detail(key, value, style="cyan")
        prompt = build_comparison_prompt(
            items=compared_items,
            objective=config.objective,
        )
        visualizer.show_provider_request(
            provider=config.provider,
            model=provider.model_label(),
        )
        call_result = provider.compare(
            items=compared_items,
            prompt=prompt,
            artifact_dir=store.provider_dir(step),
        )
        attempted_call_durations.append(call_result.elapsed_seconds)
        attempts_completed = step
        if call_result.error or call_result.comparison is None:
            provider_errors += 1
            error = call_result.error or "provider returned no comparison"
            store.append_provider_error(
                step=step,
                item_ids=comparison.item_ids,
                error=error,
                model=call_result.model,
                response_format=call_result.response_format,
            )
            store.write_progress(
                attempts_completed=attempts_completed,
                successful_observations=len(observations),
                provider_errors=provider_errors,
                status="running",
                **_progress_timing(observations),
            )
            logger.event(
                "Provider failed; continuing",
                clock=clock,
                step=step,
                budget=config.budget,
                step_elapsed=time.perf_counter() - step_started,
                style="bright_yellow",
                error=error,
                eta=_eta_minutes(
                    successful_call_durations or attempted_call_durations,
                    config.budget - step,
                ),
            )
            continue

        result = call_result.comparison
        repeated = bool(comparison.diagnostics.get("previous_comparisons", 0))
        observation = build_observation(
            step=step,
            comparison=comparison,
            result=result,
            provider=config.provider,
            model=call_result.model,
            response_format=call_result.response_format,
            usage=call_result.usage,
            provider_elapsed_seconds=call_result.elapsed_seconds,
            repeated_pair=repeated,
        )
        store.append_observation(observation)
        observations.append(observation)
        successful_call_durations.append(call_result.elapsed_seconds)
        state = model.fit(observations, step=step)
        current_order = tuple(entry.item_id for entry in state.entries)
        meaningful, movement_details = ranking_movement(
            previous_order,
            current_order,
            top_k=config.top_k,
        )
        store.write_snapshot(
            state=state,
            items=items,
            keep_step_snapshot=(step % config.snapshot_every == 0),
        )
        store.write_progress(
            attempts_completed=attempts_completed,
            successful_observations=len(observations),
            provider_errors=provider_errors,
            status="running",
            **_progress_timing(observations),
        )
        logger.event(
            "Comparison incorporated",
            clock=clock,
            step=step,
            budget=config.budget,
            step_elapsed=time.perf_counter() - step_started,
            style="bright_green",
            ranking=" > ".join(result.ranking),
            confidence=result.confidence,
            margin=result.margin,
            changed=meaningful,
            movement=movement_details,
            provider_seconds=call_result.elapsed_seconds,
            tokens=_usage_label(call_result.usage),
            effective_output_tps=_effective_output_tps(
                call_result.usage,
                call_result.elapsed_seconds,
            ),
            eta=_eta_minutes(successful_call_durations, config.budget - step),
        )
        visualizer.show_timing_checkpoint(
            successful=len(observations),
            budget=config.budget,
        )
        logger.event(
            "Successful comparison timing",
            clock=clock,
            step=step,
            budget=config.budget,
            style="bright_magenta",
            current_time=_current_time_label(),
            successful_calls=f"{len(observations)}/{config.budget}",
            attempted_calls=f"{attempts_completed}/{config.budget}",
            call_duration=format_duration(call_result.elapsed_seconds),
            average_successful_call=format_duration(
                sum(successful_call_durations) / len(successful_call_durations)
            ),
            run_elapsed=clock.elapsed_label(),
            eta=_eta_minutes(successful_call_durations, config.budget - step),
        )
        visualizer.show_result(result)
        visualizer.show_state(state, previous_order=previous_order)
        previous_order = current_order

    store.finalize(
        state=state,
        items=items,
        config=config,
        observations=observations,
        attempts_completed=attempts_completed,
        provider_errors=provider_errors,
    )
    visualizer.show_completion(
        attempts=attempts_completed,
        successful=len(observations),
    )
    logger.event(
        "Run finished",
        clock=clock,
        style="bright_green",
        attempts=attempts_completed,
        observations=len(observations),
        provider_errors=provider_errors,
        top_k=tuple(entry.item_id for entry in state.entries[: config.top_k]),
        summary=store.run_dir / "summary.md",
    )


def _item_by_id(items: Sequence[RankItem], item_id: str) -> RankItem:
    for item in items:
        if item.id == item_id:
            return item
    raise KeyError(item_id)


def _eta_minutes(durations: list[float], remaining: int) -> str:
    if not durations or remaining <= 0:
        return "0.00 min"
    seconds = sum(durations) / len(durations) * remaining
    return f"{seconds / 60:.2f} min"


def _usage_label(usage: dict[str, int]) -> str | None:
    if not usage:
        return None
    parts = []
    if "input_tokens" in usage:
        parts.append(f"in={usage['input_tokens']}")
    if "output_tokens" in usage:
        parts.append(f"out={usage['output_tokens']}")
    if "total_tokens" in usage:
        parts.append(f"total={usage['total_tokens']}")
    return ", ".join(parts)


def _effective_output_tps(
    usage: dict[str, int],
    elapsed_seconds: float,
) -> float | None:
    output_tokens = usage.get("output_tokens")
    if output_tokens is None or elapsed_seconds <= 0:
        return None
    return output_tokens / elapsed_seconds


def _current_time_label() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _progress_timing(
    observations: list[PreferenceObservation],
) -> dict[str, float | str | None]:
    timed = [
        observation
        for observation in observations
        if observation.provider_elapsed_seconds is not None
    ]
    durations = [
        observation.provider_elapsed_seconds
        for observation in timed
        if observation.provider_elapsed_seconds is not None
    ]
    total = sum(durations)
    return {
        "successful_provider_seconds_total": total,
        "average_successful_provider_seconds": (
            total / len(durations) if durations else None
        ),
        "last_successful_provider_seconds": durations[-1] if durations else None,
        "last_success_at": timed[-1].created_at if timed else None,
    }
