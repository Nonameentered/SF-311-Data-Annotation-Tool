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
SUPABASE_SECRET_KEY=<secret-key>
LABELS_JSONL_BACKUP=0  # set to 1 only if you want local JSONL mirroring
LABELER_DATA_DIR=data          # override only if your environment uses a different path
LABELS_OUTPUT_DIR=data/labels
MAX_ANNOTATORS_PER_REQUEST=2   # enforce double-label workflow

[auth]
redirect_uri="http://localhost:8501/oauth2callback"
cookie_secret="generate-a-long-random-string"
client_id="<auth0-client-id>"
client_secret="<auth0-client-secret>"
server_metadata_url="https://<tenant>.us.auth0.com/.well-known/openid-configuration"
```

With Supabase configured (service-role key kept server-side) and Auth0 handling identity, the Streamlit app writes labels directly to the tables provisioned by the SQL migrations under `supabase/migrations/` (run `supabase db push` after cloning).
All authenticated users are treated as reviewers; adjust in code later if you need role separation.

### Exporting labels for backups & analysis

Use the helper script to download the latest labels into `data/exports/<date>`:

```bash
uv run python scripts/export_labels.py --secrets-file .streamlit/secrets.toml
# or point at a production secrets file
uv run python scripts/export_labels.py --secrets-file ~/.config/streamlit/prod.secrets.toml
```

The exporter reads Supabase credentials from (in priority order) CLI flags, the supplied secrets TOML, or environment variables (preferring `SUPABASE_SECRET_KEY`). You can still pass `--url` / `--service-key` explicitly, override the output location (`--output-dir`, `--prefix`), or filter by timestamp (`--since 2025-10-01T00:00:00Z`). The script writes both `labels.jsonl` and a flattened `labels.csv` suitable for pandas/BI tools.

Prefer make? Use `make export-dev` (local secrets), `make export-prod` (production secrets), or `make export EXPORT_SECRETS=path/to/secrets.toml EXPORT_PREFIX=manual-run` for one-off snapshots.

To automate this in production, enable the scheduled GitHub Action at `.github/workflows/export-labels.yml`. Add repository secrets `SUPABASE_URL` and `SUPABASE_SECRET_KEY`; the workflow runs daily at 08:00 UTC and uploads the snapshot as an artifact for seven days.

## CI / Data Refresh

`.github/workflows/data-refresh.yml` runs nightly and on pushes to `main`, executing `make transform`, `make fetch-images`, and `make audit --snapshot-out data/audit/latest.json`. Add repository secrets `SUPABASE_URL`, `SUPABASE_SECRET_KEY` (or `SUPABASE_ANON_KEY`), plus any storage credentials, before enabling the workflow.
