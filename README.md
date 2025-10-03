# SF311 Homelessness — Features, HITL Labeling & Evals

This bundle turns raw SF311 homelessness reports into **model-ready features**, adds a **human-in-the-loop** labeling app (Streamlit), and ships **binary, app-specific evals**.

See `AGENTS.md` for contributor expectations and workflow guidelines.

## Quickstart

```bash
make init          # sets up uv env + deps
make transform     # data/homeless.txt -> data/transformed.{jsonl,parquet,csv}
make audit         # pandas coverage/mismatch checks
make eval          # binary pass/fail assertions -> data/eval_report.json
make labeler       # launch Streamlit HITL app
```

Place your raw dump at: `data/homeless.txt` (API wrapper with `'body'` JSON array, or JSON array, or JSONL).

Outputs:
- `data/transformed.jsonl` (+ `.parquet`, `.csv`)
- `data/eval_report.json`
- Audit snapshots → `data/audit/`
- Streamlit label events → Supabase `labels` table (optional JSONL mirror when enabled)

### Labeler UI Highlights
- Friendly feature controls (e.g., “Person lying face down”, “Mobility device mentioned”) with tooltips that mirror responder language.
- New outcome tracking fields: single-select **Outcome alignment** and multi-select **Follow-up needs** (`mental_health`, `shelter`, `case_management`, `medical`, `sanitation`, `legal`, `other`).
- **My recent labels** sidebar expander shows your last submissions with one-click “Load” to jump back into a request.
- **Undo last save** button appears after each submission—available for quick corrections until you move on.

## Environment Configuration

Copy `.streamlit/secrets.example.toml` → `.streamlit/secrets.toml` and provide:

```
SUPABASE_URL=https://<project>.supabase.co
SUPABASE_PUBLISHABLE_KEY=<publishable-or-anon-key>
# If still on legacy keys, provide SUPABASE_ANON_KEY instead.
LABELS_JSONL_BACKUP=0  # set to 1 only if you want local JSONL mirroring
LABELER_DATA_DIR=data          # override only if your environment uses a different path
LABELS_OUTPUT_DIR=data/labels
MAX_ANNOTATORS_PER_REQUEST=3
```

With Supabase configured, the Streamlit app offers self-service signup/login and writes labels directly to the tables provisioned by the SQL migrations under `supabase/migrations/` (run `supabase db push` after cloning).

## CI / Data Refresh

`.github/workflows/data-refresh.yml` runs nightly and on pushes to `main`, executing `make transform`, `make fetch-images`, and `make audit --snapshot-out data/audit/latest.json`. Add repository secrets `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` (or `SUPABASE_ANON_KEY`), plus any storage credentials, before enabling the workflow.
