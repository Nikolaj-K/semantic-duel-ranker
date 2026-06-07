"""
What: Create, update, resume, and finalize auditable ranking run directories.
Used by: run loop and CLI.
Deps: Python >= 3.11.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
from pathlib import Path

from semantic_duel_ranker.config import RunConfig
from semantic_duel_ranker.input_loader import load_rank_items, write_rank_items_jsonl
from semantic_duel_ranker.models import PreferenceObservation, RankingState, RankItem


class ArtifactStore:
    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir
        self.rankings_dir = run_dir / "rankings"
        self.matrices_dir = run_dir / "matrices"
        self.llm_dir = run_dir / "llm"

    @classmethod
    def create(
        cls,
        *,
        run_root: Path,
        config: RunConfig,
        items: list[RankItem],
    ) -> ArtifactStore:
        run_id = dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H-%M-%S-%fZ")
        store = cls(run_root / run_id)
        store.run_dir.mkdir(parents=True, exist_ok=False)
        store.rankings_dir.mkdir()
        store.matrices_dir.mkdir()
        store.llm_dir.mkdir()
        _write_json(store.run_dir / "config.json", config.to_dict())
        write_rank_items_jsonl(store.run_dir / "items.jsonl", items)
        store.write_progress(
            attempts_completed=0,
            successful_observations=0,
            provider_errors=0,
            status="running",
        )
        return store

    @classmethod
    def resume(cls, run_dir: Path) -> ArtifactStore:
        store = cls(run_dir)
        for required in ("config.json", "items.jsonl"):
            if not (run_dir / required).exists():
                raise ValueError(f"Cannot resume; missing {run_dir / required}")
        store.rankings_dir.mkdir(exist_ok=True)
        store.matrices_dir.mkdir(exist_ok=True)
        store.llm_dir.mkdir(exist_ok=True)
        return store

    def provider_dir(self, step: int) -> Path:
        path = self.llm_dir / f"{step:06d}"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def write_config(self, config: RunConfig) -> None:
        _write_json_atomic(self.run_dir / "config.json", config.to_dict())

    def append_observation(self, observation: PreferenceObservation) -> None:
        _append_jsonl(
            self.run_dir / "observations.jsonl",
            observation.to_dict(),
        )

    def append_provider_error(
        self,
        *,
        step: int,
        item_ids: tuple[str, ...],
        error: str,
        model: str,
        response_format: str | None,
    ) -> None:
        _append_jsonl(
            self.run_dir / "provider_errors.jsonl",
            {
                "step": step,
                "created_at": dt.datetime.now(dt.UTC).isoformat(),
                "item_ids": list(item_ids),
                "error": error,
                "model": model,
                "response_format": response_format,
            },
        )

    def write_snapshot(
        self,
        *,
        state: RankingState,
        items: list[RankItem],
        keep_step_snapshot: bool,
    ) -> None:
        rows = _ranking_rows(state, items)
        current_path = self.rankings_dir / "current_ranking.csv"
        _write_csv(current_path, rows)
        if keep_step_snapshot:
            _write_csv(
                self.rankings_dir / f"ranking_step_{state.step:06d}.csv",
                rows,
            )
            _write_json(
                self.matrices_dir / f"matrix_step_{state.step:06d}.json",
                {
                    "step": state.step,
                    "order": [entry.item_id for entry in state.entries],
                    "probabilities": state.pairwise_probabilities,
                },
            )

    def write_progress(
        self,
        *,
        attempts_completed: int,
        successful_observations: int,
        provider_errors: int,
        status: str,
        successful_provider_seconds_total: float = 0.0,
        average_successful_provider_seconds: float | None = None,
        last_successful_provider_seconds: float | None = None,
        last_success_at: str | None = None,
    ) -> None:
        _write_json_atomic(
            self.run_dir / "run_state.json",
            {
                "updated_at": dt.datetime.now(dt.UTC).isoformat(),
                "attempts_completed": attempts_completed,
                "successful_observations": successful_observations,
                "provider_errors": provider_errors,
                "successful_provider_seconds_total": round(
                    successful_provider_seconds_total, 6
                ),
                "average_successful_provider_seconds": (
                    round(average_successful_provider_seconds, 6)
                    if average_successful_provider_seconds is not None
                    else None
                ),
                "last_successful_provider_seconds": (
                    round(last_successful_provider_seconds, 6)
                    if last_successful_provider_seconds is not None
                    else None
                ),
                "last_success_at": last_success_at,
                "status": status,
            },
        )

    def finalize(
        self,
        *,
        state: RankingState,
        items: list[RankItem],
        config: RunConfig,
        observations: list[PreferenceObservation],
        attempts_completed: int,
        provider_errors: int,
    ) -> None:
        rows = _ranking_rows(state, items)
        _write_csv(self.rankings_dir / "final_ranking.csv", rows)
        with (self.rankings_dir / "final_ranking.jsonl").open(
            "w", encoding="utf-8"
        ) as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
                handle.write("\n")
        (self.run_dir / "summary.md").write_text(
            _summary_markdown(
                state=state,
                rows=rows,
                config=config,
                observations=observations,
                attempts_completed=attempts_completed,
                provider_errors=provider_errors,
            ),
            encoding="utf-8",
        )
        self.write_progress(
            attempts_completed=attempts_completed,
            successful_observations=len(observations),
            provider_errors=provider_errors,
            status="complete",
            **_observation_timing(observations),
        )

    def load_items(self) -> list[RankItem]:
        return load_rank_items(self.run_dir / "items.jsonl")

    def load_observations(self) -> list[PreferenceObservation]:
        path = self.run_dir / "observations.jsonl"
        if not path.exists():
            return []
        observations: list[PreferenceObservation] = []
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    observations.append(
                        PreferenceObservation.from_dict(json.loads(line))
                    )
        return observations

    def load_progress(self) -> dict[str, object]:
        path = self.run_dir / "run_state.json"
        if not path.exists():
            return {
                "attempts_completed": 0,
                "successful_observations": 0,
                "provider_errors": 0,
                "status": "unknown",
            }
        with path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            raise ValueError(f"Invalid run state: {path}")
        return payload


def _ranking_rows(
    state: RankingState,
    items: list[RankItem],
) -> list[dict[str, object]]:
    by_id = {item.id: item for item in items}
    rows: list[dict[str, object]] = []
    for entry in state.entries:
        item = by_id[entry.item_id]
        rows.append(
            {
                "rank": entry.rank,
                "item_id": entry.item_id,
                "text_preview": item.preview(240),
                "score": round(entry.score, 8),
                "uncertainty": round(entry.uncertainty, 8),
                "metadata_prior": round(entry.metadata_prior, 8),
                "comparisons": entry.comparisons,
                "wins": round(entry.wins, 4),
                "losses": round(entry.losses, 4),
                "likes": item.metrics.likes,
                "replies": item.metrics.replies,
                "reposts": item.metrics.reposts,
                "quotes": item.metrics.quotes,
                "views": item.metrics.views,
                "bookmarks": item.metrics.bookmarks,
                "justification": entry.last_justification or "",
                "diagnostic_flags": ";".join(entry.diagnostic_flags),
            }
        )
    return rows


def _summary_markdown(
    *,
    state: RankingState,
    rows: list[dict[str, object]],
    config: RunConfig,
    observations: list[PreferenceObservation],
    attempts_completed: int,
    provider_errors: int,
) -> str:
    models_used = sorted({observation.model for observation in observations})
    lines = [
        "# Semantic Duel Ranking Summary",
        "",
        "## Run",
        "",
        f"- provider: {config.provider}",
        f"- requested_model: {config.model}",
        f"- models_used: {', '.join(models_used) if models_used else '(none)'}",
        f"- objective: {config.objective}",
        f"- items: {len(rows)}",
        f"- top_k: {config.top_k}",
        f"- budget: {config.budget}",
        f"- tuple_size: {config.tuple_size}",
        f"- attempts_completed: {attempts_completed}",
        f"- successful_observations: {len(observations)}",
        f"- provider_errors: {provider_errors}",
        f"- metadata_prior_enabled: {config.use_metadata_prior}",
        "",
        "## Model",
        "",
        "`P(i beats j) = sigmoid(theta_i - theta_j)`",
        "",
        "Provider rankings are decomposed into weighted pairwise evidence. "
        "Confidence only changes evidence weight within a bounded range.",
        "",
        "## Final Ranking",
        "",
        "| Rank | Item | Score | Uncertainty | Comparisons | Preview |",
        "|---:|---|---:|---:|---:|---|",
    ]
    for row in rows:
        preview = str(row["text_preview"]).replace("|", "\\|")
        lines.append(
            f"| {row['rank']} | {row['item_id']} | {row['score']:.4f} | "
            f"{row['uncertainty']:.4f} | {row['comparisons']} | {preview} |"
        )
    lines.extend(["", "## Diagnostics", ""])
    if state.warnings:
        lines.extend(f"- {warning}" for warning in state.warnings)
    else:
        lines.append("- No aggregate inconsistency warning was triggered.")
    lines.extend(["", "## Observation Justifications", ""])
    if observations:
        for observation in observations:
            ranking = " > ".join(observation.ranking)
            timing = (
                f" Provider time: {observation.provider_elapsed_seconds:.2f}s."
                if observation.provider_elapsed_seconds is not None
                else ""
            )
            output_tokens = observation.usage.get("output_tokens")
            throughput = (
                output_tokens / observation.provider_elapsed_seconds
                if output_tokens is not None
                and observation.provider_elapsed_seconds is not None
                and observation.provider_elapsed_seconds > 0
                else None
            )
            throughput_text = (
                f" Effective output rate: {throughput:.2f} tokens/s."
                if throughput is not None
                else ""
            )
            flags = (
                f" Flags: {', '.join(observation.diagnostic_flags)}."
                if observation.diagnostic_flags
                else ""
            )
            lines.append(
                f"- Step {observation.step} (`{ranking}`, {observation.margin}): "
                f"{observation.justification}{timing}{throughput_text}{flags}"
            )
    else:
        lines.append("- No successful observations.")
    return "\n".join(lines) + "\n"


def _observation_timing(
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


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        raise ValueError("Cannot write an empty ranking.")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _append_jsonl(path: Path, payload: dict[str, object]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        handle.write("\n")


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_json_atomic(path: Path, payload: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    _write_json(temporary, payload)
    temporary.replace(path)
