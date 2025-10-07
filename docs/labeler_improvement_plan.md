# Streamlit Labeler Improvement Plan

This roadmap focuses on delivering a production-ready Supabase + Streamlit labeling app while keeping local development lightweight. The goal is to ship tangible UX improvements (status visibility, outcome capture, review workflow) without introducing additional infrastructure.

## 0. Near-Term Action Items

1. **Environments**: keep only Local (developer machines) and Production. Use ad-hoc preview builds when necessary instead of maintaining a staging stack.
2. **Configuration**: store Supabase secrets in `.streamlit/secrets.toml` locally (copied from the example) and via the hosting platform’s secret manager in production. Supabase remains the single source for auth + data; JSONL backups are optional.
3. **Milestone 1**: implement the UI restructure (snapshot ribbon, decision/status cards, outcome sentence).
4. **Milestone 2** *(Sept 23, 2025 – implemented)*: adds the “My Labels” view, undo action, and outcome-alignment fields once the new layout is approved.
5. **Milestone 3**: refine queue prioritization and clean up redundant UI elements.

## 1. UI Restructure Roadmap

- **Layout principle**: adopt a balanced 60/40 split (images vs. decision pane) and prepare for future view presets (“Image focus” vs. “Context focus”).
- **Snapshot ribbon**: insert a fixed row under the header summarising `Status`, `Time to resolution`, `Last updated`, and an outcome snippet. Implement via a sticky `st.container()` around the existing metrics block.
- **Right panel cards**:
  - *Decision card*: priority, GOA expectation bucket, information sources, save/skip buttons, hotkeys.
  - *Status card*: open/closed badge, closure notes, post-closure notes, after-action link.
  Move current widgets into these sections and trim redundant info from the “Summary” tab.
- **Outcome highlight**: generate a short sentence (e.g., “Closed after 6h → Outreach team dispatched”) so social workers see results immediately.
- **Tabs clean-up**: keep “Summary” for secondary fields (neighborhood, keywords), “Annotation history” unchanged, “Raw data” for debugging.
- **Optional footer ribbon**: evaluate after testing whether save/skip should be duplicated in a sticky footer.

## 2. Field Naming & Data Model Adjustments

- Update UI labels: display “Closure notes” (status_notes), “Post-closure notes” (resolution_notes), and “Time to resolution”. Ensure tooltips in `FIELD_GLOSSARY` stay in sync.
- **Status**: `outcome_alignment` (single-select) and `follow_up_need` (multi-select) are live, with Supabase columns provisioned in `20250923002000_labels_add_outcome_fields.sql`.
- Friendly feature labels now surface responder language (e.g., “Person lying face down”, “Mobility device mentioned”).
- Keep existing keyword flags for now; revisit once downstream usage is audited.

## 3. Queue Logic Enhancements

- Extend `subset()` to optionally prioritize open or slow-resolving cases (toggle in sidebar).
- Ensure the new priority toggle composes with “Rich context first” ordering and the “Require photos or notes” filter.
- Keep random/ID sort modes for QA and auditor workflows.

## 4. Code Cleanup Targets

- After the cards land, remove duplicate data from the summary tab and prune unused imports (e.g., drop `pandas` usage if tables move to Markdown/metric chips).
- Reassess the `streamlit_shortcuts` dependency once sticky ribbons are in place; native buttons may suffice.
- Update `.env.example` and docs to include only Supabase-related settings.

## 5. Review & Undo Experience

- **Status**: “My recent labels” sidebar expander lists the latest submissions and jumps directly into a request with your last payload.
- **Status**: “Undo last save” deletes the most recent row (per-annotator delete policy) and restores the prior form state.
- **Future review mode**: keep a note to add a reviewer-focused queue later; no immediate work required but avoid design conflicts.

## 6. Testing Checklist

- Unit tests for the outcome sentence helper, new queue-sorting logic, and inclusion of extra annotation fields in the saved payload.
- Extend existing fixtures (e.g., `tests/test_labeler_app_subset.py`, `tests/test_sf311_transform.py`) to cover renamed UI fields and new defaults.

## 7. Implementation Sequence

1. Update documentation (`DESIGN.md`, `docs/deployment.md`) to reflect the Supabase-only architecture.
2. Implement the UI restructure (Milestone 1) and pause for user validation.
3. Add My Labels, undo control, and outcome-alignment fields (Milestone 2).
4. Enhance queue prioritisation and tidy redundant UI bits (Milestone 3).
5. Follow up with code cleanup and dependency review.

Create separate commits per milestone to keep the history easy to review.
