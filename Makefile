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
IMAGES_DIR   ?= data/images
MANIFEST     ?= $(IMAGES_DIR)/manifest.jsonl
GOA_FEATURES ?= data/derived/goa_features.parquet
GOA_REPORT_DIR ?= data/reports
GOA_EDA_DOC ?= docs/goa_eda.md
GOA_TRENDS_DOC ?= docs/goa_trends.md
GOA_FEATURES_DOC ?= docs/goa_features.md
GOA_REPORT_DOC ?= docs/goa_analysis_report.md
GOA_DAILY_PLOT ?= docs/assets/goa/goa_daily_rate.png

EXPORT_DIR   ?= data/exports
DEV_SECRETS ?= .streamlit/secrets.toml
PROD_SECRETS ?= ~/.config/streamlit/prod.secrets.toml
EXPORT_SECRETS ?=
EXPORT_PREFIX ?=
EXPORT_SINCE ?=
EXPORT_FLAGS ?=

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
	  --size-max $(SIZE_MAX) \
	  --manifest $(MANIFEST)

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

.PHONY: fetch-images
fetch-images: ## Download and cache images referenced in transformed data
	$(call _header,Fetch Images)
	mkdir -p $(IMAGES_DIR)
	$(UV) run scripts/fetch_images.py \
	  --input $(OUT_JSONL) \
	  --out-dir $(IMAGES_DIR) \
	  --manifest $(MANIFEST)

.PHONY: export
export: ## Export Supabase labels to JSONL/CSV (set EXPORT_SECRETS / EXPORT_FLAGS)
	$(call _header,Export Labels)
	$(UV) run python scripts/export_labels.py \
	  --output-dir $(EXPORT_DIR) \
	  $(if $(EXPORT_SECRETS),--secrets-file $(EXPORT_SECRETS),) \
	  $(if $(EXPORT_PREFIX),--prefix $(EXPORT_PREFIX),) \
	  $(if $(EXPORT_SINCE),--since $(EXPORT_SINCE),) \
	  $(EXPORT_FLAGS)

.PHONY: export-dev
export-dev: EXPORT_SECRETS := $(DEV_SECRETS)
export-dev: ## Export labels using local secrets file
export-dev: export

.PHONY: export-prod
export-prod: EXPORT_SECRETS := $(PROD_SECRETS)
export-prod: ## Export labels using production secrets file
export-prod: export

.PHONY: goa-data
goa-data: ## Prepare responder GOA feature dataset
	$(call _header,GOA Prepare)
	$(UV) run python scripts/goa_prepare.py \
	  --input $(OUT_PARQUET) \
	  --output $(GOA_FEATURES)

.PHONY: goa-eda
goa-eda: goa-data ## Run baseline exploratory summaries for GOA
	$(call _header,GOA EDA)
	$(UV) run python scripts/goa_eda.py \
	  --input $(GOA_FEATURES) \
	  --report-dir $(GOA_REPORT_DIR) \
	  --doc $(GOA_EDA_DOC) \
	  --asset-dir docs/assets/goa

.PHONY: goa-trends
goa-trends: goa-data ## Generate daily/weekly GOA trends and resolution histograms
	$(call _header,GOA Trends)
	$(UV) run python scripts/goa_trends.py \
	  --input $(GOA_FEATURES) \
	  --report-dir $(GOA_REPORT_DIR) \
	  --doc $(GOA_TRENDS_DOC)

.PHONY: goa-features
goa-features: goa-data ## Compute GOA feature-level correlations
	$(call _header,GOA Features)
	$(UV) run python scripts/goa_features.py \
	  --input $(GOA_FEATURES) \
	  --report-dir $(GOA_REPORT_DIR) \
	  --doc $(GOA_FEATURES_DOC) \
	  --asset-dir docs/assets/goa

.PHONY: goa-resolution
goa-resolution: goa-data ## Summarize GOA resolution timing stats and plot distributions
	$(call _header,GOA Resolution)
	$(UV) run python scripts/goa_resolution_analysis.py \
	  --features $(GOA_FEATURES) \
	  --output $(GOA_REPORT_DIR)/status_resolution_summary.csv \
	  --figure docs/assets/goa/goa_resolution_boxplot.png

.PHONY: goa-report
goa-report: goa-eda goa-trends goa-features goa-resolution ## Assemble comprehensive GOA report
	$(call _header,GOA Report)
	$(UV) run python scripts/goa_report.py \
	  --features $(GOA_FEATURES) \
	  --report-dir $(GOA_REPORT_DIR) \
	  --output $(GOA_REPORT_DOC) \
	  --daily-plot $(GOA_DAILY_PLOT) \
	  --asset-dir docs/assets/goa


.PHONY: labeler
labeler: ## Launch Streamlit HITL labeler
	$(call _header,Streamlit Labeler)
	$(UV) run streamlit run streamlit_app.py

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
