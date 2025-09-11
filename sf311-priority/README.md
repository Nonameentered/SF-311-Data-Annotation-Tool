# SF311 Homelessness — Features, HITL Labeling & Evals

This bundle turns raw SF311 homelessness reports into **model-ready features**, adds a **human-in-the-loop** labeling app (Streamlit), and ships **binary, app-specific evals**.

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
- Human labels append to `data/golden.jsonl`
