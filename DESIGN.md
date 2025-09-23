# SF311 Homelessness Labeling v1 Design

## Mission
Stand up an image-first labeling workflow for SF311 homelessness reports so we can ship a vetted evaluation set quickly. Priorities:
- Keep high-signal images and text aligned while annotators label a request at a time.
- Cache external media locally to avoid link rot and to support offline review.
- Capture clean labels (priority + contextual features) and track dataset drift run-to-run.
- Run LLM-based analyses offline only after we have gold labels; the app stays human-first.

## End-to-End Pipeline
1. **Ingest & Cache Media**
   - Run `make fetch-images` → download referenced photo URLs into `data/images/{request_id}/file.jpg` with SHA256 checksums. Skip re-downloads when checksum matches; log failures.
   - Update `.gitignore` to exclude cached media.
   - Persist a manifest `data/images/manifest.jsonl` mapping remote URL → local path + checksum.
2. **Transform & Feature Extraction**
   - `make transform` calls `scripts/sf311_transform.py` to normalize raw rows into JSONL/Parquet/CSV.
   - Augment rows with cached image metadata (`image_paths`, `image_checksums`, `image_fetch_status`).
   - Maintain lightweight keyword flags and derived metrics; prune unused helpers later.
3. **Audit & Drift Tracking**
   - `make audit` prints distributions (photo coverage, districts, tag/value counts) and now writes a JSON snapshot under `data/audit/` for longitudinal monitoring.
   - `make eval` runs rule-based checks (keyword agreement, numeric bounds) producing `data/eval_report.json`.
   - Add pytest coverage for schema + checksum expectations (e.g., `tests/test_transform.py`).
4. **Label Collection (Streamlit)**
   - `make labeler` launches `streamlit_app.py` (which wraps `scripts/labeler_app`).
   - Supabase email/password (self-serve sign-up, email verification) is required. Provide `SUPABASE_URL` plus a `SUPABASE_PUBLISHABLE_KEY`/`SUPABASE_ANON_KEY` via env vars or secrets before launching.
   - Enforces a cap of three distinct annotators per request; individuals can load/revise their prior submission, flag items for review, and set label status (`pending`/`resolved`).
   - Saves events into a Supabase `labels` table (JSON payload) and optionally mirrors to per-day JSONL backups (`data/labels/{date}/labels.jsonl`). Set `LABELS_JSONL_BACKUP=1` to enable the mirror when desired.
   - Bounding boxes are postponed; design keeps room to plug `streamlit-drawable-canvas` later.
5. **Gold Set Assembly**
   - CLI (`scripts/label_ops.py`, future) merges verified labels with transformed rows, resolves conflicts, and materializes evaluation splits under `data/eval/v1/`.
   - Offline notebooks/CLI run GPT-5-nano or lightweight OSS models on the gold set for benchmarking; outputs live outside the app (`analysis/`).

## Label Schema (v1)
Each label entry contains:
```json
{
  "label_id": "uuid",
  "request_id": "string",
  "annotator_uid": "uuid",
  "annotator": "string",  // display name
  "annotator_display": "string",
  "role": "annotator" | "reviewer",
  "timestamp": "ISO-8601",
  "priority": "P1" | "P2" | "P3" | "P4",
  "features": {
    "lying_face_down": bool,
    "safety_issue": bool,
    "drugs": bool,
    "tents_present": bool,
    "blocking": bool,
    "on_ramp": bool,
    "propane_or_flame": bool,
    "children_present": bool,
    "wheelchair": bool,
    "num_people_bin": "0" | "1" | "2-3" | "4-5" | "6+",
    "size_feet_bin": "0" | "1-20" | "21-80" | "81-150" | "150+"
  },
  "abstain": bool,
  "needs_review": bool,
  "status": "pending" | "resolved",
  "notes": "string | null",
  "image_paths": ["data/images/..."],
  "image_checksums": ["sha256:..."],
  "revision_of": "uuid | null"
}
```
Future optional fields: `bounding_boxes`.

Supabase table (`labels`) should mirror the schema above (JSONB column for `features`, TEXT for IDs, BOOLEAN flags). Set row-level security or policies to allow authenticated users to insert/select their own rows while reviewers can read all.

## Streamlit Application Requirements
- **Layout**: balanced grid where images (max 9 photos) share the screen with decision/status cards so annotators see visuals and outcomes without scrolling.
- **Queue controls**: filters on photos/no photos, keywords, derived tags, and request status (`unlabeled`, `needs_review`, `conflict`, `labeled`). Deterministic seeding keeps the queue predictable.
- **Auth & Roles**: login form backed by `config/annotators.json` (overridable via env var). Annotator identity is fixed for the session and stored with every label event.
- **Persistence**: labels append to per-day JSONL logs (`data/labels/YYYYMMDD/labels.jsonl`). Each event includes a `label_id`, `revision_of`, and status flags to support reconciliation.
- **Resilience**: handle missing images gracefully (show placeholders, surface fetch errors), allow keyboard-enabled navigation.
- **Metrics**: surface counts for queue size, total labeled requests, conflicts, and per-status tallies.
- **Planned upgrades**: see `docs/labeler_improvement_plan.md` for the snapshot ribbon, decision/status cards, outcome tags, and review workflow roadmap.

## Dataset Monitoring
- Save audit snapshots (`data/audit/{timestamp}.json`) capturing counts of requests, photo coverage, keyword frequencies, district distribution.
- Compare latest snapshot against prior to flag shifts (simple diff script or notebook).
- Track labeling throughput using Supabase queries (or JSONL backups) to compute per-annotator counts, status mix, and conflicts; surface trends in a lightweight dashboard or scheduled notebook.
- Store checksum manifest for images to detect corrupt files; rerun fetch when checksum changes.

## Deployment Playbook
1. **Supabase setup**: create a project, enable email auth with self-service sign-up, and create a `labels` table with columns matching the schema (use JSONB for `features`, arrays for `image_paths`/`image_checksums`). Configure RLS to allow authenticated users to insert/update their own rows and reviewers to read all data.
2. **Secrets**: provide `SUPABASE_URL` + `SUPABASE_PUBLISHABLE_KEY` (or, if unavailable, an anon key) and optional `LABELER_DATA_DIR`, `LABELS_OUTPUT_DIR`, `LABELS_JSONL_BACKUP` via environment variables or Streamlit secrets.
3. **Build & deploy**: containerize or use Streamlit Community Cloud. During build run `uv sync`, `make transform`, `make fetch-images`, and mount persistent storage (S3/GCS or volume) for image cache + JSONL backups.
4. **Runtime**: launch the Streamlit app with `streamlit run streamlit_app.py --server.port $PORT --server.address 0.0.0.0`. Ensure the container can reach Supabase and image storage.
5. **Ops**: schedule `make transform`, `make fetch-images`, and `make audit --snapshot-out ...` (cron, GitHub Actions, or Cloud Scheduler) to refresh data and drop snapshots in storage. Use Supabase SQL or notebooks for reconciliation and exporting evaluation splits.

GitHub Actions pipeline (`.github/workflows/data-refresh.yml`) runs the transform → fetch-images → audit sequence on every push to `main` and nightly; configure repository secrets (`SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, optional storage keys) before enabling it.

## Open Questions / Future Work
- Conflict resolution workflow once multiple annotators submit conflicting priorities.
- Storage strategy for large image cache (consider external bucket once dataset grows).
- Integration of semi-automated suggestions (LLMs, detectors) into UI without biasing annotators.
- Deployment: Streamlit Community Cloud vs. container (document secrets + auth once selected).
