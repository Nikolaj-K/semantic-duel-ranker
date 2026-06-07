#!/usr/bin/env python
"""
What: Run a fast end-to-end mock ranking against three checked-in sample items.
Run:  python scripts/smoke_check.py
I/O:  Reads test_data/sample_10_rank_items.jsonl and writes only to a temp dir.
Deps: Install the project first with `python -m pip install -e .`.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from semantic_duel_ranker.cli import main


def main_smoke() -> None:
    project_root = Path(__file__).resolve().parents[1]
    sample_path = project_root / "test_data" / "sample_10_rank_items.jsonl"
    assert sample_path.exists(), f"Missing checked-in smoke fixture: {sample_path}"

    with tempfile.TemporaryDirectory(prefix="semantic-duel-smoke-") as temporary:
        run_root = Path(temporary) / "runs"
        exit_code = main(
            [
                "rank",
                "--provider",
                "mock",
                "--input",
                str(sample_path),
                "--limit",
                "3",
                "--top-k",
                "3",
                "--budget",
                "2",
                "--run-dir",
                str(run_root),
            ]
        )
        assert exit_code == 0, f"Smoke ranking exited with status {exit_code}"
        run_dirs = [path for path in run_root.iterdir() if path.is_dir()]
        assert len(run_dirs) == 1, f"Expected one smoke run, found {len(run_dirs)}"
        run_dir = run_dirs[0]
        state = json.loads((run_dir / "run_state.json").read_text(encoding="utf-8"))
        assert state["status"] == "complete", state
        assert state["successful_observations"] == 2, state
        assert (run_dir / "summary.md").exists()
        assert (run_dir / "rankings" / "final_ranking.csv").exists()
        print(f"Smoke check passed: {run_dir}")


if __name__ == "__main__":
    main_smoke()
