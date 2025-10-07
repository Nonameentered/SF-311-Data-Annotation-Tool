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
   - Adds outcome tracking (`outcome_alignment`, `follow_up_need`), a “My recent labels” sidebar expander for quick jumps, and an undo button that deletes the most recent label if pressed immediately.
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
  "priority": "High" | "Medium" | "Low",
  "features": {
    "lying_face_down": bool,
    "safety_issue": bool,
    "drugs": bool,
    "tents_count": integer,
    "blocking": bool,
    "on_ramp": bool,
    "propane_or_flame": bool,
    "children_present": bool,
    "wheelchair": bool,
    "num_people_bin": "0" | "1" | "2-3" | "4-5" | "6+",
    "size_feet_bin": "0" | "1-20" | "21-80" | "81-150" | "150+",
    "goa_window": "unknown" | "respond_sub2h" | "respond_2_6h" | "respond_6_24h" | "respond_over_24h",
  },
  "notes": "string | null",
  "review_status": "pending" | "agree" | "disagree",
  "review_notes": "string | null",
  "image_paths": ["data/images/..."],
  "image_checksums": ["sha256:..."],
  "revision_of": "uuid | null"
}
```
Future optional fields: `bounding_boxes`.

Supabase table (`labels`) should mirror the schema above (JSONB column for `features`, TEXT for IDs, BOOLEAN flags). Set row-level security or policies to allow authenticated users to insert/select their own rows while reviewers can read all.

## Streamlit Application Requirements
- **Layout**: balanced grid where images (max 9 photos) share the screen with decision/status cards so annotators see visuals and outcomes without scrolling.
- **Queue controls**: filters on photos/no photos, keywords, derived tags, and request status (`unlabeled`, `needs_review`, `labeled`). Deterministic per-user ordering keeps the queue predictable without forcing everyone through the same sequence.
- **Label workflow**: target two independent passes per request (primary label + reviewer). `MAX_ANNOTATORS_PER_REQUEST` gates participation (set to `2` for strict double labeling) while the UI surfaces prior submissions for context.
- **Review surface**: reviewers see a previous submission card, choose a review decision (`agree`/`disagree`), and enter dedicated reviewer notes. The primary notes box stays optional for initial annotators.
- **Prioritization**: default sorting favors rich-context items (images, notes) so the highest quality reports are labeled first; a sidebar toggle can force “photos or notes only.”
- **Auth & Roles**: Streamlit’s native `st.login()` integrates with Auth0 (or any OIDC provider). All authenticated users have reviewer capabilities today; adjust roles later if needed.
- **Persistence**: labels append to per-day JSONL logs (`data/labels/YYYYMMDD/labels.jsonl`). Each event includes a `label_id`, `revision_of`, and status flags to support reconciliation.
- **Resilience**: fail fast when Supabase credentials or dependencies are missing, surface actionable errors, and continue to handle missing images gracefully (show placeholders, surface fetch errors), allowing keyboard-enabled navigation.
- **Metrics**: surface counts for queue size, total labeled requests, and per-status tallies.
- **Planned upgrades**: see `docs/labeler_improvement_plan.md` for the snapshot ribbon, decision/status cards, outcome tags, and review workflow roadmap.

## Dataset Monitoring
- Save audit snapshots (`data/audit/{timestamp}.json`) capturing counts of requests, photo coverage, keyword frequencies, district distribution.
- Compare latest snapshot against prior to flag shifts (simple diff script or notebook).
- Track labeling throughput using Supabase queries (or JSONL backups) to compute per-annotator counts and status mix; surface trends in a lightweight dashboard or scheduled notebook.
- Store checksum manifest for images to detect corrupt files; rerun fetch when checksum changes.

## Deployment Playbook
1. **Supabase setup**: create a project and run the migrations under `supabase/migrations/` to provision the `labels` table. The app now connects with the service-role key because identity is handled by Auth0/Streamlit.
2. **Secrets**: provide `SUPABASE_URL`, `SUPABASE_SECRET_KEY`, and Auth0 OIDC details in `.streamlit/secrets.toml` (`[auth]` block with `client_id`, `client_secret`, `redirect_uri`, `server_metadata_url`, `cookie_secret`). Optional knobs: `LABELER_DATA_DIR`, `LABELS_OUTPUT_DIR`, `LABELS_JSONL_BACKUP`, and `MAX_ANNOTATORS_PER_REQUEST`.
3. **Build & deploy**: containerize or use Streamlit Community Cloud. During build run `uv sync`, `make transform`, `make fetch-images`, and mount persistent storage (S3/GCS or volume) for image cache + JSONL backups.
4. **Runtime**: launch the Streamlit app with `streamlit run streamlit_app.py --server.port $PORT --server.address 0.0.0.0`. Ensure the container can reach Supabase and image storage.
5. **Ops**: schedule `make transform`, `make fetch-images`, and `make audit --snapshot-out ...` (cron, GitHub Actions, or Cloud Scheduler) to refresh data and drop snapshots in storage. Use Supabase SQL or notebooks for reconciliation and exporting evaluation splits.

GitHub Actions pipeline (`.github/workflows/data-refresh.yml`) runs the transform → fetch-images → audit sequence on every push to `main` and nightly; configure repository secrets (`SUPABASE_URL`, `SUPABASE_SECRET_KEY`, optional storage keys) before enabling it.

## Open Questions / Future Work
- Conflict resolution workflow once multiple annotators submit conflicting priorities.
- Storage strategy for large image cache (consider external bucket once dataset grows).
- Integration of semi-automated suggestions (LLMs, detectors) into UI without biasing annotators.
- Deployment: Streamlit Community Cloud vs. container (document secrets + auth once selected).
