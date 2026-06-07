# Semantic Duel Ranker

`semantic_duel_ranker` is a local-first active preference-ranking tool for
short text items. It repeatedly selects an informative pair or small tuple,
asks either a deterministic mock judge or a local LM Studio model to rank the
items, converts the result into pairwise evidence, and updates a probabilistic
global ranking.

The package includes a reviewed 10-item JSONL fixture, a colorful narrated CLI,
strict provider-response validation, resumable run artifacts, and unit and
integration tests.

## Project Status And License

This is an early research/MVP implementation. No open-source license has been
chosen yet. Until a license is added, copyright law reserves the usual rights;
publishing the repository does not itself grant permission to reuse or
redistribute the code.

## Requirements

- Python 3.11 or newer.
- Runtime packages: NumPy, SciPy, Requests, and Rich.
- Optional development packages: pytest, Black, and Ruff.
- Optional real LLM judge: [LM Studio](https://lmstudio.ai/) with a loaded
  chat/instruct model and its local OpenAI-compatible server enabled.

`pyproject.toml` is the authoritative package definition. `requirements.txt`
and `requirements-dev.txt` are provided for users who prefer requirements
files.

## Installation

Create an isolated environment and install the package:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

Equivalent requirements-file installation:

```bash
python -m pip install -r requirements.txt
```

For development:

```bash
python -m pip install -e ".[dev]"
```

On Windows PowerShell, activate the virtual environment with:

```powershell
.venv\Scripts\Activate.ps1
```

## Quick Start

The mock provider runs immediately and does not require LM Studio:

```bash
semantic-duel-ranker rank \
  --provider mock \
  --input test_data/sample_10_rank_items.jsonl \
  --limit 3 \
  --top-k 3 \
  --preview-tweets 3 \
  --budget 3
```

`--limit 3` loads the first three items. `--budget 3` permits three provider
calls. The budget counts attempted calls, not input items.

## Configuration

Configuration is optional because every setting can be supplied through CLI
flags. For a reusable local setup:

```bash
cp config.example.toml config.toml
semantic-duel-ranker rank --config config.toml
```

`config.toml` is ignored by Git. Relative `input_path` and `run_root` values are
resolved from the directory containing the config file.

Precedence is:

1. CLI flags.
2. Supported environment variables.
3. Values in the TOML file supplied with `--config`.
4. Built-in portable defaults.

Supported environment variables:

```text
SEMANTIC_DUEL_LMSTUDIO_BASE_URL
SEMANTIC_DUEL_LMSTUDIO_API_KEY
SEMANTIC_DUEL_MODEL
SEMANTIC_DUEL_RUN_ROOT
```

Use the environment variable for any real API credential:

```bash
export SEMANTIC_DUEL_LMSTUDIO_API_KEY="replace-with-local-secret"
```

API keys are used in memory and intentionally omitted from generated
`runs/<run-id>/config.json` files. Avoid passing real credentials directly on a
shared shell command line because shell history may retain them.

An abbreviated config looks like:

```toml
[rank]
input_path = "test_data/sample_10_rank_items.jsonl"
provider = "mock"
top_k = 5
budget = 10
preview_tweets = 3

[lmstudio]
base_url = "http://127.0.0.1:1234/v1"
model = "replace-with-a-model-id-from-list-models"

[output]
run_root = "runs"
```

See [`config.example.toml`](config.example.toml) for every supported key.
Unknown sections or keys fail with a descriptive error rather than being
silently ignored.

## LM Studio Setup

Real comparisons require software that `pip` does not install:

1. Install [LM Studio](https://lmstudio.ai/).
2. Download and load a chat/instruct model that fits the available memory.
3. Start LM Studio's local OpenAI-compatible server.
4. List the visible model IDs:

```bash
semantic-duel-ranker list-models
```

If exactly one chat model is loaded, the ranker can infer it. Otherwise pass the
model explicitly:

```bash
semantic-duel-ranker rank \
  --provider lmstudio \
  --model replace-with-model-id \
  --input test_data/sample_10_rank_items.jsonl \
  --top-k 5 \
  --preview-tweets 10 \
  --budget 25
```

The default endpoint is `http://127.0.0.1:1234/v1`. Non-loopback endpoints are
rejected unless `--allow-non-loopback-lmstudio` is explicitly supplied.

Structured output negotiation tries JSON Schema, then JSON object mode, then
prompt-only JSON when the local server rejects a response-format feature.

## Input Data

The normal example input is:

```text
test_data/sample_10_rank_items.jsonl
```

Each line is one normalized `RankItem` with these stable fields:

```text
id, text, created_at, language, url, metrics, author, media, metadata, source
```

JSON arrays and simple CSV inputs are also supported by the generic loader.
Media metadata is preserved, but the application does not download or inspect
media content.

The fixture contains public social-post text and public profile metadata. Review
whether redistributing that material is appropriate for the intended
publication. Do not add private datasets, scraped exports, or confidential text
to Git.

### Optional Raw Tweet Export

I got example public tweets from [kaggle](https://www.kaggle.com/datasets/fastcurious/twitter-new-dataset-2024-march-data?resource=download) but didn't upload them here, in a `test_data_full` dir, as the json is 20 MB large.
The source export is not required for ranking, tests, or demonstrations.
If retained locally, place it under `test_data_full/`, which is ignored by Git.
It is only needed to audit the original source or regenerate the 10-item
fixture:

```bash
semantic-duel-ranker inspect-tweets \
  --input test_data_full/dataset_tweet-scraper_2024-03-04_15-52-13-507.json

semantic-duel-ranker extract-sample \
  --input test_data_full/dataset_tweet-scraper_2024-03-04_15-52-13-507.json \
  --output test_data/sample_10_rank_items.jsonl
```

The `source.path` stored in the fixture is provenance text only. Ranking does
not open or dereference it.

## Output And Logs

Every run creates a timestamped directory:

```text
runs/<run-id>/
  config.json
  items.jsonl
  observations.jsonl
  provider_errors.jsonl
  run_state.json
  summary.md
  rankings/
  matrices/
  llm/
```

- `summary.md` is the reader-facing final report.
- `observations.jsonl` contains validated provider judgments and evidence.
- `rankings/` contains current, stepwise, and final rankings.
- `matrices/` contains pairwise win-probability snapshots.
- `llm/` contains prompts, schemas, raw responses, and parse failures.
- `run_state.json` contains progress and timing aggregates.

The terminal output is intentionally explanatory and includes previews,
selection reasons, formulas, criterion scores, probability matrices, call
timings, averages, and ETA.

To capture a complete terminal transcript while retaining normal run artifacts:

```bash
script -q full_run.log semantic-duel-ranker rank \
  --provider mock \
  --input test_data/sample_10_rank_items.jsonl \
  --top-k 5 \
  --budget 10
```

Logs and run artifacts are ignored by Git because they can contain full input
text, prompts, model responses, and local runtime details.

Resume an interrupted run by increasing its budget:

```bash
semantic-duel-ranker rank --resume runs/replace-with-run-id --budget 40
```

## Mathematical Background

### Pairwise Comparisons

A [pairwise comparison](https://en.wikipedia.org/wiki/Pairwise_comparison) asks
which of two items better satisfies the ranking objective. This project also
supports small tuples; a tuple ordering such as `A > B > C` is decomposed into
the pairwise evidence `A > B`, `A > C`, and `B > C`.

### Tournament View

The collected judgments can be viewed as a weighted
[tournament graph](https://en.wikipedia.org/wiki/Tournament_(graph_theory)):
items are vertices and directed comparison outcomes are edges. A simple system
could rank vertices by wins, similar to
[Copeland's method](https://en.wikipedia.org/wiki/Copeland%27s_method). This
implementation reports weighted win/loss evidence but does not use raw Copeland
win counts as its final score.

### Bradley-Terry Model

The implemented ranking model is the probabilistic
[Bradley-Terry model](https://en.wikipedia.org/wiki/Bradley%E2%80%93Terry_model).
Each item has a latent score `theta`, with:

```text
P(i beats j) = sigmoid(theta_i - theta_j)
```

The model fits weighted comparison outcomes with regularization toward an
optional weak metadata prior. The metadata prior uses robustly standardized
`log1p` engagement metrics. Provider confidence changes evidence weight only
within a bounded range.

The active-selection heuristic favors uncertain pairs, neighboring ranks, the
top-K cutoff, under-compared items, and disagreements between the fitted model
and weak metadata prior.

### Evaluation

The project does not yet implement a benchmark evaluation pipeline. If
human-authored or known reference rankings are added, a natural rank-level
metric is [Kendall tau](https://en.wikipedia.org/wiki/Kendall_rank_correlation_coefficient),
which measures agreement between two orderings.

## Tests And Smoke Check

Run the fast end-to-end smoke check:

```bash
python scripts/smoke_check.py
```

Run the complete validation suite:

```bash
python -m black --check .
python -m ruff check .
python -m pytest
```

The smoke check uses the mock provider, reads three checked-in items, writes to
a temporary directory, and verifies final artifacts.

## Troubleshooting

### `semantic-duel-ranker: command not found`

Activate the virtual environment and reinstall the editable package:

```bash
source .venv/bin/activate
python -m pip install -e .
```

### LM Studio is unreachable

Confirm that LM Studio is running its local server and that the endpoint is
correct:

```bash
semantic-duel-ranker list-models
```

### No or multiple chat models are detected

Load one chat model or pass `--model` using an exact ID from `list-models`.

### Config file errors

Copy `config.example.toml` to `config.toml`. The CLI reports missing files,
invalid TOML, unknown sections, and unknown keys explicitly.

### A real comparison is slow

Local inference speed depends on model size, quantization, hardware, prompt
length, and output length. Each successful step logs provider duration,
average duration, token usage when available, effective output tokens per
second, and estimated remaining time.

## Security And Privacy

- Keep `.env`, `config.toml`, downloaded datasets, logs, and `runs/` out of Git.
- Do not commit API keys or point the application at a remote endpoint without
  reviewing transport security and the data being sent.
- Raw run artifacts contain full prompts, item text, profile metadata, and model
  output.
- Provider output is treated as untrusted and validated against the exact
  compared item IDs before becoming ranking evidence.
