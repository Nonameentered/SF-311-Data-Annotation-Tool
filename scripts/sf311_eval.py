#!/usr/bin/env python3
"""
sf311_eval.py

Binary pass/fail checks aligned with Hamel's eval FAQ:
- Favor crisp assertions over fuzzy ratings
- Targeted, app-specific correctness

Outputs a JSON report with pass rates + example failures.
"""
from __future__ import annotations
import argparse, json, re
from pathlib import Path
from typing import Any, Dict, List, Callable


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="Path to transformed JSONL")
    ap.add_argument("--report", required=True, help="Where to write JSON report")
    ap.add_argument(
        "--fail-examples", type=int, default=5, help="Examples per failing test"
    )
    return ap.parse_args()


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    return [
        json.loads(l)
        for l in path.read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]


# ---- Tests ----
RE_PASSED_OUT = re.compile(r"\b(passed[- ]?out|unconscious)\b", re.I)
RE_BLOCKING = re.compile(r"\b(block(ing)?|obstruct(ion|ing))\b", re.I)
RE_PRIV_PROP = re.compile(r"\bprivate property\b", re.I)


def contains(text: str | None, pat: re.Pattern) -> bool:
    return bool(text and pat.search(text))


def check_kw_passed_out(r: Dict[str, Any]) -> bool:
    expected = contains(r.get("text"), RE_PASSED_OUT)
    return bool(r.get("kw_passed_out")) == expected


def check_blocking_kw(r: Dict[str, Any]) -> bool:
    expected = contains(r.get("text"), RE_BLOCKING)
    return bool(r.get("kw_blocking")) == expected


def check_private_property_kw(r: Dict[str, Any]) -> bool:
    expected = contains(r.get("text"), RE_PRIV_PROP)
    return bool(r.get("derived_is_private_property")) == expected


def check_lying_consistency(r: Dict[str, Any]) -> bool:
    pos = r.get("tag_person_position") or ""
    if "lying" in pos:
        # Allow True or None (abstention ok), but NOT False
        return r.get("tag_lying_face_down") in (True, None)
    return True


def check_tents_size_consistency(r: Dict[str, Any]) -> bool:
    if r.get("tag_tents_present") is True and r.get("tag_size_feet") is not None:
        return r["tag_size_feet"] > 0
    return True


def check_size_bounds(r: Dict[str, Any]) -> bool:
    v = r.get("tag_size_feet")
    return True if v is None else (0 <= v <= 400)


def check_num_people_bounds(r: Dict[str, Any]) -> bool:
    v = r.get("tag_num_people")
    return True if v is None else (0 <= v <= 25)


TESTS: Dict[str, Callable[[Dict[str, Any]], bool]] = {
    "kw_passed_out_flag": check_kw_passed_out,
    "kw_blocking_flag": check_blocking_kw,
    "kw_private_property_flag": check_private_property_kw,
    "lying_consistency": check_lying_consistency,
    "tents_size_consistency": check_tents_size_consistency,
    "size_feet_bounds": check_size_bounds,
    "num_people_bounds": check_num_people_bounds,
}


def run_checks(records: List[Dict[str, Any]], k_examples: int) -> Dict[str, Any]:
    totals = {name: 0 for name in TESTS}
    passes = {name: 0 for name in TESTS}
    examples = {name: [] for name in TESTS}
    for r in records:
        for name, fn in TESTS.items():
            totals[name] += 1
            ok = False
            try:
                ok = bool(fn(r))
            except Exception:
                ok = False
            if ok:
                passes[name] += 1
            else:
                if len(examples[name]) < k_examples:
                    examples[name].append(
                        {
                            "request_id": r.get("request_id"),
                            "text_snippet": (r.get("text") or "")[:160],
                            "tag_person_position": r.get("tag_person_position"),
                            "tag_lying_face_down": r.get("tag_lying_face_down"),
                            "tag_tents_present": r.get("tag_tents_present"),
                            "tag_size_feet": r.get("tag_size_feet"),
                            "kw_blocking": r.get("kw_blocking"),
                            "kw_passed_out": r.get("kw_passed_out"),
                            "derived_is_private_property": r.get(
                                "derived_is_private_property"
                            ),
                        }
                    )
    return {
        "totals": totals,
        "passes": passes,
        "pass_rates": {
            k: (passes[k] / totals[k] if totals[k] else None) for k in totals
        },
        "examples_failed": examples,
    }


def main():
    args = parse_args()
    recs = load_jsonl(Path(args.input))
    report = run_checks(recs, args.fail_examples)
    Path(args.report).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"[ok] wrote report: {args.report}")


if __name__ == "__main__":
    main()
