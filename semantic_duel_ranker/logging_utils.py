"""
What: High-signal colored operational logs for long ranking runs.
Used by: run loop and CLI.
Deps: Rich.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass

from rich.console import Console
from rich.text import Text


@dataclass(frozen=True)
class RunClock:
    started: float

    @classmethod
    def start_now(cls) -> RunClock:
        return cls(started=time.perf_counter())

    def elapsed_seconds(self) -> float:
        return time.perf_counter() - self.started

    def elapsed_label(self) -> str:
        return format_duration(self.elapsed_seconds())


class RunLogger:
    def __init__(self, *, console: Console | None = None) -> None:
        self.console = console or Console(
            file=sys.stderr,
            highlight=False,
        )

    def event(
        self,
        event: str,
        *,
        clock: RunClock | None = None,
        step: int | None = None,
        budget: int | None = None,
        step_elapsed: float | None = None,
        style: str = "bright_cyan",
        **fields: object,
    ) -> None:
        prefix_parts: list[str] = []
        if step is not None and budget is not None:
            width = len(str(budget))
            prefix_parts.append(f"{step:0{width}d}/{budget}")
        if clock is not None:
            prefix_parts.append(clock.elapsed_label())
        if step_elapsed is not None:
            prefix_parts.append(f"+{format_duration(step_elapsed)}")
        prefix = "[semantic-duel]"
        if prefix_parts:
            prefix += f" [{', '.join(prefix_parts)}]"
        line = Text()
        line.append(prefix + " ", style="bold white")
        line.append(event, style=f"bold {style}")
        visible = {key: value for key, value in fields.items() if value is not None}
        for key, value in visible.items():
            line.append(" | ", style="dim white")
            line.append(f"{key}=", style="bold white")
            line.append(_format_value(value), style=_field_style(key))
        self.console.print(line)

    def detail(self, label: str, value: object, *, style: str = "white") -> None:
        text = Text("    ")
        text.append(f"{label}: ", style="bold white")
        text.append(_format_value(value), style=style)
        self.console.print(text)


def format_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _format_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".")
    if isinstance(value, (tuple, list)):
        return ", ".join(str(item) for item in value)
    return str(value)


def _field_style(key: str) -> str:
    if key in {"error", "warnings"}:
        return "bold bright_yellow"
    if key in {
        "elapsed",
        "remaining_min",
        "eta",
        "provider_seconds",
        "effective_output_tps",
        "tokens",
        "call_duration",
        "average_successful_call",
        "run_elapsed",
    }:
        return "bright_cyan"
    if key in {
        "ranking",
        "top_k",
        "changed",
        "confidence",
        "margin",
        "score",
        "successful_calls",
        "attempted_calls",
    }:
        return "bold bright_white"
    if key == "current_time":
        return "bright_magenta"
    if key in {"provider", "model", "response_format", "run_dir", "reason"}:
        return "cyan"
    return "white"
