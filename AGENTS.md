# Repository Guidelines

## Project Structure & Module Organization
- `scripts/` holds the ETL stages: `sf311_transform.py`, `sf311_audit.py`, `sf311_eval.py`, and the Streamlit labeler.
- `data/` stores the raw dump (`homeless.txt`), generated datasets, and evaluation reports; keep large files out of version control.
- `tests/` is reserved for pytest suites; mirror module names (e.g., `tests/test_sf311_transform.py`).
- `pyproject.toml` and `uv.lock` define the Python 3.11 toolchain; `Makefile` centralizes every workflow target.

## Build, Test, and Development Commands
Run `make init` once to sync the uv environment. Core pipeline steps:
- `make transform` parses the raw API export and emits JSONL, Parquet, and CSV artifacts under `data/`.
- `make audit` performs pandas sanity checks and prints sample summaries.
- `make eval` runs binary assertions and writes `data/eval_report.json`.
Use `make labeler` to launch the Streamlit HITL app. `make lint`, `make fmt`, `make test`, and `make ci` (sync → lint → tests → pipeline) keep changes production-ready.

## Coding Style & Naming Conventions
Follow Black-formatted, 4-space-indented Python with type-friendly helpers. Run `make fmt` before committing. Ruff enforces import order and lightweight lint rules; fix warnings instead of ignoring them. Use snake_case for functions, modules, and filenames; constants stay upper snake. Prefer pure functions inside `scripts/` and keep Streamlit callbacks declarative.

## Testing Guidelines
Author pytest cases under `tests/` using the `test_*.py` pattern. Focus on deterministic transforms: supply fixture JSON snippets and assert normalized rows. When behavior depends on configuration flags (e.g., `--size-max`), parametrize tests to cover edge values. Always run `make test` after modifying parsers or evaluators, and attach sample outputs when relevant.

## Commit & Pull Request Guidelines
Write imperative, present-tense commit subjects (e.g., “Add audit coverage checks”), mirroring the existing history. Scope each commit to one concern and include context in the body if data migrations are required. Pull requests should summarize the affected pipeline stage, list verification commands run, and link tracking issues. Include before/after artifact diffs or screenshots for Streamlit changes when helpful.

## Data Handling Tips
Keep API dumps outside public branches unless sanitized. Confirm `.gitignore` excludes bulky artifacts before running `make all`. When sharing samples, truncate with `make sample` to avoid leaking sensitive rows.
