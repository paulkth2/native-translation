#!/usr/bin/env python3
"""
judge.py — Korean-augmented MQM LLM-as-judge over benchmark outputs.

Reference-free by default (reflects real product QA with no gold). Use a judge from a
DIFFERENT model family than the system under test to avoid self-preference. Average
multiple runs to reduce judge noise.

Usage
-----
export OPENAI_API_KEY=...     # or ANTHROPIC_API_KEY / GEMINI_API_KEY for the judge
python judge.py out/claude.jsonl --judge openai:gpt-5.5 --runs 3 --out out/claude.judged.jsonl
"""
from __future__ import annotations
import argparse
import json
import re
import sys

import adapters

JUDGE_SYSTEM = (
    "You are an expert English→Korean (ko-KR) translator and MQM annotator specializing in "
    "software product/UI localization. You judge whether Korean reads as native product copy. "
    "You are strict about 번역투, honorific/register consistency, particle (조사) correctness, "
    "and placeholder integrity. Output only valid JSON."
)

JUDGE_TEMPLATE = """SOURCE (English): {source_en}
TRANSLATION (Korean): {translation}
CONTEXT: UI surface = {category}; target register = {register}; placeholders that must be preserved = {placeholders}; required glossary terms = {glossary}

Identify every error as {{span, category, severity}}.
category in [mistranslation, omission, addition, untranslated, grammar, spelling, register, inconsistency, josa, spacing, translationese, loanword, honorific, term, number_date, placeholder, awkward, non_translation]
severity in [critical, major, minor]  (weights -25/-5/-1)
Then rate 1-5: adequacy, fluency, naturalness, register_fit.
mqm_score = max(0, 100 + sum(weights)).
Note: a dual-form like 을(를) on a variable row, and an `other`-only plural, are CORRECT — do not penalize.
Output JSON only:
{{"errors":[{{"span":"...","category":"...","severity":"..."}}],"adequacy":N,"fluency":N,"naturalness":N,"register_fit":N,"mqm_score":N,"rationale":"one sentence"}}"""

JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_json(text: str):
    m = JSON_RE.search(text or "")
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def judge_one(adapter, rec) -> dict | None:
    user = JUDGE_TEMPLATE.format(
        source_en=rec.get("source_en", ""), translation=rec.get("translation", ""),
        category=rec.get("category", ""), register=rec.get("register", ""),
        placeholders=rec.get("placeholders", []), glossary=rec.get("glossary", {}),
    )
    return parse_json(adapter(JUDGE_SYSTEM, user))


def mean(xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return round(sum(xs) / len(xs), 2) if xs else None


def main():
    ap = argparse.ArgumentParser(description="LLM-as-judge MQM scoring.")
    ap.add_argument("file", help="run.py output jsonl")
    ap.add_argument("--judge", required=True, help="judge adapter spec, e.g. openai:gpt-5.5")
    ap.add_argument("--runs", type=int, default=1, help="repeat & average (reduces noise)")
    ap.add_argument("--out", help="write per-row verdicts here")
    args = ap.parse_args()

    adapter = adapters.get_adapter(args.judge)
    rows = [json.loads(l) for l in open(args.file, encoding="utf-8") if l.strip()]

    dims = {k: [] for k in ("adequacy", "fluency", "naturalness", "register_fit", "mqm_score")}
    cat_counter = {}
    out_rows = []

    for i, rec in enumerate(rows, 1):
        if not rec.get("translation"):
            continue
        verdicts = [v for v in (judge_one(adapter, rec) for _ in range(args.runs)) if v]
        if not verdicts:
            print(f"[{i}/{len(rows)}] {rec.get('id')}: judge returned no JSON", file=sys.stderr)
            continue
        agg = {k: mean([v.get(k) for v in verdicts]) for k in dims}
        for k in dims:
            if agg[k] is not None:
                dims[k].append(agg[k])
        for v in verdicts:
            for e in v.get("errors", []):
                c = e.get("category", "?")
                cat_counter[c] = cat_counter.get(c, 0) + 1
        out_rows.append({**{x: rec.get(x) for x in ("id", "category", "source_en", "translation")},
                         "judged": agg, "errors": verdicts[0].get("errors", [])})
        print(f"[{i}/{len(rows)}] {rec.get('id'):<10} mqm={agg['mqm_score']} "
              f"nat={agg['naturalness']} reg={agg['register_fit']}", file=sys.stderr)

    print("\n=== Judge summary (mean over rows) ===")
    for k in dims:
        print(f"  {k:<14} {mean(dims[k])}")
    print("\nError categories (count over all runs):")
    for c, n in sorted(cat_counter.items(), key=lambda kv: -kv[1]):
        print(f"  {c:<16} {n}")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            for r in out_rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"\nVerdicts → {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
