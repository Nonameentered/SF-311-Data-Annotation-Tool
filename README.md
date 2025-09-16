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
- Streamlit label events → Supabase `labels` table (or `data/labels/` in local fallback)

## Environment Configuration

Copy `.env.example` → `.env` (or fill `.streamlit/secrets.toml`) and provide:

```
SUPABASE_URL=https://<project>.supabase.co
SUPABASE_ANON_KEY=<anon-or-service-role-key>
LABELS_JSONL_BACKUP=0
LABELER_DATA_DIR=/app/data
LABELS_OUTPUT_DIR=/app/data/labels
MAX_ANNOTATORS_PER_REQUEST=3
```

With Supabase configured, the Streamlit app offers self-service signup/login and writes labels directly to the `labels` table defined in `supabase/labels_table.sql`.

## CI / Data Refresh

`.github/workflows/data-refresh.yml` runs nightly and on pushes to `main`, executing `make transform`, `make fetch-images`, and `make audit --snapshot-out data/audit/latest.json`. Add repository secrets `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` (or `SUPABASE_ANON_KEY`), plus any storage credentials, before enabling the workflow.
