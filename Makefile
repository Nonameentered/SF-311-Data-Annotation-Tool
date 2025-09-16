# ==== Config ====
UV           ?= uv
PYTHON_VER   ?= 3.11
INPUT        ?= data/homeless.txt
OUT_JSONL    ?= data/transformed.jsonl
OUT_PARQUET  ?= data/transformed.parquet
OUT_CSV      ?= data/transformed.csv
REPORT       ?= data/eval_report.json
SIZE_MAX     ?= 400
SHOW         ?= 10

.DEFAULT_GOAL := help

define _header
	@printf "\n\033[1;36m▶ %s\033[0m\n" "$(1)"
endef

.PHONY: help
help: ## Show this help
	@awk 'BEGIN {FS = ":.*##"; printf "\n\033[1mAvailable targets\033[0m\n"} /^[a-zA-Z0-9_\-]+:.*?##/ {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

.PHONY: all
all: transform audit eval ## Run transform → audit → eval (full pipeline)


.PHONY: sync
sync: ## Sync environment from pyproject/uv.lock
	$(call _header,Sync env)
	$(UV) sync

.PHONY: transform
transform: ## Parse + normalize raw file → JSONL (and Parquet/CSV)
	$(call _header,Transform)
	mkdir -p data
	$(UV) run scripts/sf311_transform.py \
	  --input  $(INPUT) \
	  --jsonl  $(OUT_JSONL) \
	  --parquet $(OUT_PARQUET) \
	  --csv    $(OUT_CSV) \
	  --size-max $(SIZE_MAX)

.PHONY: audit
audit: ## Pandas EDA: counts, distributions, sanity checks
	$(call _header,Audit)
	$(UV) run scripts/sf311_audit.py \
	  --input $(OUT_JSONL) \
	  --show  $(SHOW)

.PHONY: eval
eval: ## Binary pass/fail feature checks → JSON report
	$(call _header,Eval)
	$(UV) run scripts/sf311_eval.py \
	  --input  $(OUT_JSONL) \
	  --report $(REPORT)

.PHONY: labeler
labeler: ## Launch Streamlit HITL labeler
	$(call _header,Streamlit Labeler)
	$(UV) run streamlit run scripts/labeler_app.py

.PHONY: lint
lint: ## Lint with ruff; style-check with black
	$(call _header,Lint)
	$(UV) run ruff check .
	$(UV) run black --check .

.PHONY: fmt
fmt: ## Auto-format with black
	$(call _header,Format)
	$(UV) run black .

.PHONY: test
test: ## Run unit tests (if any)
	$(call _header,Tests)
	$(UV) run pytest -q

.PHONY: ci
ci: sync lint test all ## One-stop target for CI (sync → lint → tests → pipeline)

.PHONY: clean
clean: ## Remove generated artifacts (careful!)
	$(call _header,Clean)
	-rm -f $(OUT_JSONL) $(OUT_PARQUET) $(OUT_CSV) $(REPORT)

.PHONY: sample
sample: ## Create a tiny sample JSONL from transformed data (first 50 rows)
	$(call _header,Sample)
	head -n 50 $(OUT_JSONL) > data/sample.jsonl
	@echo "Wrote data/sample.jsonl"

.PHONY: codex
codex:
	@codex -m gpt-5-codex -c model_reasoning_effort="high" --dangerously-bypass-approvals-and-sandbox