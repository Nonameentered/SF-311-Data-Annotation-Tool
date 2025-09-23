# Deployment Guide (Supabase + GitHub Actions)

## 1. Supabase Project Setup
1. Create a new Supabase project and enable **Email/Password** sign-in (Auth → Providers).
2. In Auth → Email Templates, keep the default confirmation email so new annotators verify themselves.
3. Open the SQL Editor, copy the contents of the migrations under `supabase/migrations/` (or run `supabase db push`) to create the tables and policies. The initial migrations are idempotent, so running them multiple times is safe.
4. (Optional) Add a `reviewer` role claim to any supervisor accounts via Auth → Users → Edit user metadata (`{"role": "reviewer"}`) so they can see all labels.

## 2. Secrets & Environment Variables
Create a `.env` (local) or use Streamlit secrets / deployment secrets with the following values:

```
SUPABASE_URL=https://<project>.supabase.co
SUPABASE_PUBLISHABLE_KEY=<publishable-key>
# If your project has not migrated yet, set SUPABASE_ANON_KEY instead.
LABELS_JSONL_BACKUP=0
LABELER_DATA_DIR=/app/data
LABELS_OUTPUT_DIR=/app/data/labels
MAX_ANNOTATORS_PER_REQUEST=3
```

For Streamlit Cloud, add the same values to `.streamlit/secrets.toml` or the cloud dashboard. Because `LABELS_JSONL_BACKUP` defaults to `0` when Supabase is configured, production runs will avoid writing local JSONL copies.
If you temporarily supply a service-role key for local testing, rotate it and remove it from secrets before deploying.

GitHub repository secrets required by CI/CD:
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY` (only for server-side jobs that require elevated privileges) or `SUPABASE_ANON_KEY` for read/write tests
- `LABELS_JSONL_BACKUP` set to `0`
- Any storage credentials needed for image fetches (e.g., `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` if pulling from private S3)

## 3. GitHub Actions (data refresh)
The workflow in `.github/workflows/data-refresh.yml` runs on pushes to `main` and nightly. It:
1. Installs dependencies via `uv sync`.
2. Runs `make transform`, `make fetch-images`, and `make audit --snapshot-out data/audit/latest.json`.
3. Uploads artifacts (`data/transformed.*`, `data/images/manifest.jsonl`, audit snapshot) for inspection or downstream jobs.

Make sure your repository secrets supply the Supabase keys and any other credentials the workflow requires.

## 4. Deploying the Streamlit App
1. Provision a server (Render/Fly.io) or use Streamlit Community Cloud.
2. Ensure the environment contains the same variables as in step 2, pointing `LABELER_DATA_DIR`/`LABELS_OUTPUT_DIR` to persistent storage (volume or mounted bucket).
3. During build/startup:
   ```bash
   uv sync
   make transform
   make fetch-images
   streamlit run streamlit_app.py --server.port $PORT --server.address 0.0.0.0
   ```
4. Test the signup flow: create a new account via the Streamlit UI, confirm the email (Supabase sends it), label a request, and verify the row appears in the Supabase `labels` table.

## 5. Ongoing Operations
- Schedule the GitHub Actions workflow (already configured) to keep transformed data and audit snapshots fresh.
- Use Supabase SQL or the Dashboard to reconcile conflicts and export gold splits (e.g., `COPY (SELECT * FROM labels WHERE status = 'resolved') TO ...`).
- Monitor Supabase auth logs and storage metrics. Consider enabling Row Level Security logs for auditing annotator activity.
- Keep the migrations under `supabase/migrations/` in sync with schema changes; run `supabase db push` whenever you add columns or adjust policies.
