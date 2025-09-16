# SF311 Homelessness Labeling Design Doc

## Purpose
Transform raw SF311 homelessness calls (`data/homeless.txt`) into a reliable evaluation dataset for triaging requests by priority. The system should surface high-signal features from text and images, capture consistent human labels, and provide tooling to benchmark candidate models (LLMs or lighter classifiers).

## Current Assets
- **ETL scripts** (`scripts/sf311_transform.py`, `sf311_audit.py`, `sf311_eval.py`) normalize records, run QA checks, and emit rich features (keywords, tag bounds, image URLs).
- **Streamlit HITL prototype** (`scripts/labeler_app.py`) queues transformed reports, renders photos, and records multi-label priority annotations into `data/golden.jsonl`.
- **Existing tags** inside `homeless_tags` (e.g., `safety_issue`, `tents_or_makeshift_present`, `person_lying_face_down_on_sidewalk`). These provide weak, sometimes inconsistent supervisory signals.

## Pipeline Architecture
1. **Ingest & Normalize**: Use `make transform` to parse SF311 exports (API wrapper, JSON/JSONL) into canonical fields and heuristic features (keyword flags, derived private-property indicator, photo presence). Clamp numeric tags to plausible ranges.
2. **Audit & QA**: `make audit` reports distributional stats and identifies inconsistent tag combinations; `make eval` writes binary QA results for regression testing.
3. **Label Collection**: Human annotators filter queues (e.g., only photo-backed, "passed_out" keyword) and assign priority classes (P1–P4) plus auxiliary feature bins via Streamlit. Consider exporting the same interface via Shiny for Python if deployment constraints favor a server-rendered UI; the backend data contract remains identical.
4. **Dataset Assembly**: Merge transformed rows with golden labels, snapshotting splits (train/dev/eval) and preserving provenance (annotator, timestamp, abstentions).

## Labeling Strategy
- **Text Signals**: Pre-highlight keywords (blocking, fire, children) and allow annotators to confirm or override them. LLM assistance (e.g., GPT-4o mini, Claude Haiku) can draft rationales cheaply via batch prompts; keep them out of the gold labels but store as machine suggestions.
- **Image Annotations**: Start with binary/tiered attributes (tents present, visible fire, crowd size). Bounding boxes are optional—add them later only if object localization becomes critical or misclassification rates remain high. If needed, evaluate cheaper vision models (e.g., Grounding DINO, MobileSAM) for pre-annotations before requesting manual refinements.
- **Cost Controls**: Use small open-source language models (e.g., `phi-4`, `llama-3.1-8b`) or API micro-models for pseudo-labeling. Accept abstentions and review conflicts via double labeling on high-priority cases.

## Leveraging Existing Tags
Treat `homeless_tags` values as noisy priors. They offer quick positives for tents, drugs, and safety issues but miss nuance (no confidence scores, occasional contradictory flags). Use them to:
- Seed labeler queues (e.g., prioritize `safety_issue=True` for early review).
- Generate heuristics for model bootstrapping.
- Flag inconsistencies during QA (already partially covered by `make eval`).
Avoid training solely on these tags; compare them against human-labeled subsets to quantify precision/recall.

## Evaluation Dataset Plan
- Target ~1k+ labeled requests with balanced coverage across districts, photo availability, and keyword scenarios.
- Store each record with source text, image URLs (hashed if sensitive), automated feature vector, human priority, and rationale notes.
- Maintain versioned splits and changelog per release (e.g., `data/eval/v1/`).
- Track metrics: priority accuracy, recall@P1 for emergencies, feature agreement rates. Run evaluations via `make eval` plus dedicated pytest cases for dataset integrity.

## Next Steps
1. Hard-code dataset schema and merge logic for `data/golden.jsonl` into the transform pipeline.
2. Add CLI tools for sampling unlabeled vs. labeled coverage and exporting annotation tasks.
3. Prototype LLM prompting vs. gradient-boosted baselines on text-only features; layer image signals once photo coverage improves.
4. Document deployment plan for the labeling UI (Streamlit Cloud vs. containerized service) and add authentication before inviting annotators.
