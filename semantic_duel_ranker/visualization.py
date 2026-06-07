"""
What: Rich terminal explanations, ranking tables, and probability matrices.
Used by: run loop.
Deps: Rich.
"""

from __future__ import annotations

from collections.abc import Sequence

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from semantic_duel_ranker.models import (
    ComparisonResult,
    ComparisonTuple,
    RankingState,
    RankItem,
)


class TerminalVisualizer:
    def __init__(
        self,
        *,
        items: list[RankItem],
        top_k: int,
        matrix_window: int,
        console: Console,
    ) -> None:
        self.items = {item.id: item for item in items}
        self.labels = {
            item.id: f"I{index:02d}" for index, item in enumerate(items, start=1)
        }
        self.top_k = top_k
        self.matrix_window = matrix_window
        self.console = console

    def show_reader_introduction(
        self,
        *,
        provider: str,
        model: str,
        objective: str,
        comparison_budget: int,
        tuple_size: int,
        top_k: int,
        resumed: bool,
    ) -> None:
        status = "resumed" if resumed else "new"
        self._narrate(
            "READER NOTE FOR FAN HOUSE",
            (
                "This log is intentionally text-heavy so Fan House can understand "
                "the experiment without needing to inspect the code. Blank lines and "
                "short explanations separate the stages of the run.\n\n"
                f"This is a {status} ranking run over {len(self.items)} loaded items. "
                f"The judge is the {provider} provider using {model}. The comparison "
                f"budget is {comparison_budget}, meaning the program may ask the "
                f"provider for up to {_count_phrase(comparison_budget, 'judgment')}. "
                "Failed attempts still count toward this budget. "
                f"Each judgment contains {tuple_size} items. The configured top-K is "
                f"{top_k}, so the first {top_k} positions receive extra attention "
                "during active selection.\n\n"
                f"Ranking objective: {objective}"
            ),
            style="bold bright_magenta",
        )

    def show_model_explanation(self) -> None:
        self._narrate(
            "1. HOW THE RANKING METHOD WORKS",
            (
                "The program does not ask the LLM to sort every tweet at once. It "
                "selects a small comparison, asks the judge which item is better, "
                "then updates a global ranking from all judgments collected so far.\n\n"
                "The box below gives the mathematical summary. A larger fitted score "
                "means an item is currently preferred. Uncertainty is larger when the "
                "available comparisons do not yet determine an item's position well."
            ),
        )
        body = Text()
        body.append("Bradley-Terry model\n", style="bold bright_white")
        body.append("P(i beats j) = sigmoid(theta_i - theta_j)\n", style="cyan")
        body.append(
            "Each provider ranking becomes weighted pairwise evidence. "
            "The fitted theta score is regularized toward a weak metadata prior. "
            "Uncertainty comes from the inverse observed-information matrix.\n\n",
            style="white",
        )
        body.append("Acquisition\n", style="bold bright_white")
        body.append(
            "score(i,j) = uncertainty × importance × novelty\n",
            style="cyan",
        )
        body.append(
            "Importance favors adjacent items and the top-K cutoff; novelty "
            "penalizes repeats. Metadata disagreement and under-compared items "
            "receive small explicit boosts.",
            style="white",
        )
        self.console.print(
            Panel(body, title="How the numbers work", border_style="bright_cyan")
        )

    def show_selection(
        self,
        comparison: ComparisonTuple,
        *,
        step: int,
        budget: int,
    ) -> None:
        self._narrate(
            f"5. COMPARISON {step} OF {budget}: ITEMS CHOSEN FOR THE NEXT DUEL",
            (
                "The active-selection policy chose the items below because comparing "
                "them is expected to reduce useful ranking uncertainty. This is not "
                "yet the LLM's judgment, and the acquisition score is not an item "
                "quality score. It only measures how useful this particular comparison "
                "should be for the ranking process.\n\n"
                "Uncertainty is high for close or poorly measured pairs. Importance "
                "favors neighboring ranks and the top-K boundary. Novelty reduces the "
                "priority of repeated pairs. Metadata disagreement adds a small boost "
                "when the learned ranking conflicts with the weak starting prior."
            ),
            style="bold bright_magenta",
        )
        table = Table(
            title="Selected comparison",
            border_style="cyan",
            header_style="bold bright_white",
        )
        table.add_column("Label", style="bold cyan", no_wrap=True)
        table.add_column("Text", style="white")
        table.add_column("Metrics", style="bright_black")
        for item_id in comparison.item_ids:
            item = self.items[item_id]
            table.add_row(
                self.labels[item_id],
                item.preview(105),
                _metric_summary(item),
            )
        self.console.print(table)
        diagnostics = " | ".join(
            f"{key}={value:.3f}" if isinstance(value, float) else f"{key}={value}"
            for key, value in comparison.diagnostics.items()
        )
        self.console.print(
            f"[bold white]Reason:[/] {comparison.reason}\n"
            f"[bold white]Acquisition:[/] {comparison.acquisition_score:.4f}"
            + (f" | {diagnostics}" if diagnostics else "")
        )

    def show_provider_request(self, *, provider: str, model: str) -> None:
        if provider == "mock":
            explanation = (
                f"The deterministic mock judge {model} is now synthesizing a response. "
                "No LLM is called in mock mode, so this step should return almost "
                "immediately. Mock output is useful for checking the workflow and log "
                "format, but it is not a real semantic judgment."
            )
        else:
            explanation = (
                f"The selected item texts and metadata are now being sent to {model}. "
                "The judge must return a strict ranking, criterion scores, confidence, "
                "a margin label, and a concise written justification. For a local LLM, "
                "this is normally the slowest part of the run."
            )
        self._narrate(
            "THE JUDGE IS NOW WORKING",
            explanation,
            style="bold bright_yellow",
        )

    def show_timing_checkpoint(self, *, successful: int, budget: int) -> None:
        self._narrate(
            "TIMING CHECKPOINT",
            (
                f"Successful comparison {successful} of the {budget}-call budget has "
                "finished. The colored timing line below records the local clock time, "
                "successful and attempted call counts, this call's duration, the "
                "average successful-call duration so far, total elapsed run time, and "
                "the estimated time remaining."
            ),
            style="bold bright_magenta",
        )

    def show_result(self, result: ComparisonResult) -> None:
        ranking = " > ".join(self.labels[item_id] for item_id in result.ranking)
        self._narrate(
            "6. THE LLM JUDGMENT",
            (
                "The provider returned a valid structured answer. In the ranking below, "
                "the item on the left of '>' was preferred. Confidence is the model's "
                "self-reported certainty and only changes evidence weight within a "
                "deliberately narrow range. The margin describes how large the model "
                "believes the qualitative difference is."
            ),
            style="bold bright_green",
        )
        confidence = (
            f"{result.confidence:.3f}"
            if result.confidence is not None
            else ", ".join(
                f"{self.labels[entry.better]}>{self.labels[entry.worse]}"
                f"={entry.confidence:.3f}"
                for entry in result.adjacent_confidences
            )
        )
        body = Text()
        body.append("Ranking: ", style="bold white")
        body.append(ranking + "\n", style="bold bright_green")
        body.append("Confidence: ", style="bold white")
        body.append(confidence + "\n", style="bright_cyan")
        body.append("Margin: ", style="bold white")
        body.append(result.margin + "\n\n", style="bright_cyan")
        body.append(result.justification, style="white")
        self.console.print(
            Panel(body, title="Provider judgment", border_style="bright_green")
        )
        if result.diagnostic_flags:
            self.console.print(
                Panel(
                    "\n".join(
                        f"• {flag.replace('_', ' ')}"
                        for flag in result.diagnostic_flags
                    ),
                    title="Provider diagnostics",
                    border_style="bright_yellow",
                )
            )
        self.console.print()
        self.console.print(
            "[bold bright_cyan]How to read the criterion table:[/] "
            "Each value is the LLM's 0-to-10 assessment for that item. These values "
            "explain the judgment, but the final ranking model learns from the ordered "
            "preference rather than treating the criterion scores as exact measurements. "
            "Engagement potential estimates useful attention; informativeness measures "
            "substantive content; clarity measures ease of understanding; originality "
            "measures novelty; and topical relevance measures fit with the objective."
        )
        self.console.print()
        criteria = Table(
            title="Criterion scores",
            border_style="bright_black",
            header_style="bold bright_white",
        )
        criteria.add_column("Criterion")
        for item_id in result.ranking:
            criteria.add_column(self.labels[item_id], justify="right")
        for criterion, scores in result.criterion_scores.items():
            criteria.add_row(
                criterion,
                *(f"{scores[item_id]:.1f}" for item_id in result.ranking),
            )
        self.console.print(criteria)

    def show_state(
        self,
        state: RankingState,
        *,
        previous_order: Sequence[str] | None = None,
    ) -> None:
        if previous_order is None:
            explanation = (
                "This is the baseline ranking before the next comparison. It starts "
                "from a deliberately weak metadata prior, so it should not be treated "
                "as the final result. 'Score' is the fitted preference strength; "
                "'Unc.' is uncertainty; 'Prior' is the small metadata-based starting "
                "signal; 'Cmp' counts comparisons; 'W/L' is weighted evidence; and "
                "'Flags' marks diagnostics."
            )
            title = "4. BASELINE RANKING BEFORE LLM EVIDENCE"
        else:
            explanation = (
                "The new LLM judgment has now been incorporated into the global model. "
                "The table shows the complete updated order. 'Move' reports position "
                "change since the previous step: positive means the item moved up, "
                "negative means it moved down, and the dot means no movement."
            )
            title = f"7. UPDATED GLOBAL RANKING AFTER COMPARISON {state.step}"
        self._narrate(title, explanation)
        table = Table(
            title=f"Current ranking after step {state.step}",
            border_style="bright_cyan",
            header_style="bold bright_white",
        )
        table.add_column("Rank", justify="right")
        table.add_column("Item", style="bold cyan")
        table.add_column("Score", justify="right")
        table.add_column("Unc.", justify="right")
        table.add_column("Prior", justify="right")
        table.add_column("Cmp", justify="right")
        table.add_column("W/L", justify="right")
        table.add_column("Move", justify="right")
        table.add_column("Flags", style="bright_yellow")
        previous_rank = (
            {item_id: rank for rank, item_id in enumerate(previous_order, start=1)}
            if previous_order
            else {}
        )
        visible_entries = state.entries[: max(self.matrix_window, self.top_k)]
        for entry in visible_entries:
            movement = ""
            if entry.item_id in previous_rank:
                difference = previous_rank[entry.item_id] - entry.rank
                movement = f"{difference:+d}" if difference else "·"
            rank_style = "bold bright_green" if entry.rank <= self.top_k else "white"
            table.add_row(
                Text(str(entry.rank), style=rank_style),
                self.labels[entry.item_id],
                f"{entry.score:+.3f}",
                f"{entry.uncertainty:.3f}",
                f"{entry.metadata_prior:+.3f}",
                str(entry.comparisons),
                f"{entry.wins:.1f}/{entry.losses:.1f}",
                movement,
                ", ".join(entry.diagnostic_flags),
            )
        self.console.print(table)
        self.show_uncertain_pairs(state)
        self.show_probability_matrix(state)
        if state.warnings:
            self.console.print(
                Panel(
                    "\n".join(f"• {warning}" for warning in state.warnings),
                    title="Diagnostics",
                    border_style="bright_yellow",
                )
            )

    def show_item_key(self) -> None:
        self._narrate(
            "3. STABLE LABELS USED THROUGHOUT THE LOG",
            (
                "Every loaded item receives one short label based on input order. "
                "For example, I01 always refers to the first loaded item. These labels "
                "are reused in previews, duel tables, rankings, criterion scores, and "
                "the probability matrix so the reader can trace an item consistently."
            ),
        )
        table = Table(
            title="Item key",
            border_style="bright_black",
            header_style="bold white",
        )
        table.add_column("Label", style="bold cyan", no_wrap=True)
        table.add_column("Item ID", style="white", no_wrap=True)
        table.add_column("Preview", style="white")
        for item_id, item in self.items.items():
            table.add_row(self.labels[item_id], item_id, item.preview(58))
        self.console.print(table)

    def show_item_previews(self, count: int) -> None:
        if count <= 0:
            self._narrate(
                "2. LOADED ITEM PREVIEWS",
                (
                    "Full tweet previews were not requested for this run. The compact "
                    "item key in the next section still identifies every loaded item. "
                    "Use --preview-tweets N to print the first N items in full."
                ),
            )
            return
        preview_items = list(self.items.values())[:count]
        self._narrate(
            "2. LOADED ITEM PREVIEWS",
            (
                f"The following panels show the first {len(preview_items)} of "
                f"{len(self.items)} loaded items in input order. Each panel contains "
                "the stable matrix label, original item ID, author, engagement metrics, "
                "URL, and complete text. Media is counted but its visual contents are "
                "not downloaded or judged."
            ),
        )
        for index, item in enumerate(preview_items, start=1):
            label = self.labels[item.id]
            username = (
                f"@{item.author.username}"
                if item.author and item.author.username
                else "(unknown author)"
            )
            body = Text()
            body.append("ID: ", style="bold white")
            body.append(item.id + "\n", style="cyan")
            body.append("Author: ", style="bold white")
            body.append(username + "\n", style="bright_magenta")
            body.append("Metrics: ", style="bold white")
            body.append(_metric_summary(item) + "\n", style="bright_cyan")
            body.append("URL: ", style="bold white")
            body.append((item.url or "(none)") + "\n\n", style="blue")
            body.append(item.text, style="bright_white")
            self.console.print(
                Panel(
                    body,
                    title=f"{index}. {label}",
                    border_style="bright_magenta",
                )
            )

    def show_uncertain_pairs(self, state: RankingState, count: int = 5) -> None:
        self.console.print()
        self.console.print(
            "[bold bright_cyan]What remains uncertain:[/] "
            "The next table lists neighboring items whose current ordering is least "
            "certain. A win probability near 0.500 means the model sees the pair as "
            "close; a larger score gap means the current ordering is more separated."
        )
        self.console.print()
        adjacent: list[tuple[float, str, str, float]] = []
        entries = list(state.entries)
        for first, second in zip(entries, entries[1:], strict=False):
            probability = state.pairwise_probabilities[first.item_id][second.item_id]
            adjacent.append(
                (abs(probability - 0.5), first.item_id, second.item_id, probability)
            )
        table = Table(
            title="Most uncertain adjacent pairs",
            border_style="bright_black",
            header_style="bold white",
        )
        table.add_column("Pair")
        table.add_column("P(first wins)", justify="right")
        table.add_column("Score gap", justify="right")
        for _, first_id, second_id, probability in sorted(adjacent)[:count]:
            gap = state.score_by_id[first_id] - state.score_by_id[second_id]
            table.add_row(
                f"{self.labels[first_id]} >? {self.labels[second_id]}",
                f"{probability:.3f}",
                f"{gap:+.3f}",
            )
        self.console.print(table)

    def show_probability_matrix(self, state: RankingState) -> None:
        self.console.print()
        self.console.print(
            "[bold bright_cyan]How to read the matrix:[/] "
            "Choose a row item and a column item. The cell estimates the probability "
            "that the row item would beat the column item in a future duel. The matrix "
            "is sorted by the current global ranking, and the diagonal is blank because "
            "an item is not compared with itself."
        )
        self.console.print()
        entries = list(state.entries[: self.matrix_window])
        table = Table(
            title=(
                "Pairwise probability matrix " "(cell = P(row item beats column item))"
            ),
            border_style="bright_black",
            header_style="bold bright_white",
            pad_edge=False,
        )
        table.add_column("", style="bold cyan", no_wrap=True)
        for entry in entries:
            table.add_column(
                self.labels[entry.item_id],
                justify="center",
                no_wrap=True,
                width=3,
            )
        for row in entries:
            cells: list[Text] = []
            for column in entries:
                if row.item_id == column.item_id:
                    cells.append(Text("·", style="dim white"))
                else:
                    probability = state.pairwise_probabilities[row.item_id][
                        column.item_id
                    ]
                    cells.append(_probability_cell(probability))
            table.add_row(self.labels[row.item_id], *cells)
        self.console.print(table)
        self.console.print(
            "[dim]Legend: [bright_green]█ ≥.85[/] "
            "[green]▓ ≥.70[/] [cyan]▒ ≥.58[/] [white]░ .42-.58[/] "
            "[bright_black]· <.42[/][/]"
        )

    def show_completion(self, *, attempts: int, successful: int) -> None:
        self._narrate(
            "8. RUN COMPLETE",
            (
                f"The configured comparison loop has finished after {attempts} attempted "
                f"{'call' if attempts == 1 else 'calls'} and "
                f"{_count_phrase(successful, 'successful judgment')}. The final terminal "
                "ranking is also written to CSV and JSONL, while the Markdown summary, "
                "raw provider exchanges, observations, timing state, and probability "
                "matrices remain in the run directory for audit and sharing."
            ),
            style="bold bright_green",
        )

    def _narrate(
        self,
        title: str,
        body: str,
        *,
        style: str = "bold bright_cyan",
    ) -> None:
        self.console.print()
        self.console.print()
        self.console.print(Text(title, style=style))
        self.console.print()
        self.console.print(Text(body, style="white"))
        self.console.print()


def ranking_movement(
    previous_order: Sequence[str],
    current_order: Sequence[str],
    *,
    top_k: int,
) -> tuple[bool, str]:
    if not previous_order:
        return True, "initial ranking"
    old_rank = {item_id: rank for rank, item_id in enumerate(previous_order)}
    max_displacement = max(
        abs(old_rank[item_id] - new_rank)
        for new_rank, item_id in enumerate(current_order)
    )
    old_top = set(previous_order[:top_k])
    new_top = set(current_order[:top_k])
    top_changed = old_top != new_top
    meaningful = top_changed or max_displacement >= 2
    details = (
        f"max_displacement={max_displacement}, top_k_changed={str(top_changed).lower()}"
    )
    return meaningful, details


def _metric_summary(item: RankItem) -> str:
    metrics = item.metrics
    return (
        f"likes={metrics.likes or 0}, replies={metrics.replies or 0}, "
        f"reposts={metrics.reposts or 0}, views={metrics.views or 0}, "
        f"media={len(item.media)}"
    )


def _probability_cell(probability: float) -> Text:
    if probability >= 0.85:
        return Text("█", style="bold bright_green")
    if probability >= 0.70:
        return Text("▓", style="green")
    if probability >= 0.58:
        return Text("▒", style="cyan")
    if probability >= 0.42:
        return Text("░", style="white")
    return Text("·", style="bright_black")


def _count_phrase(count: int, singular: str) -> str:
    suffix = "" if count == 1 else "s"
    return f"{count} {singular}{suffix}"
