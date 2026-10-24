#!/usr/bin/env python3
"""
run.py — produce Korean translations for the benchmark test set with a chosen model.

Examples
--------
# smoke-test the pipeline with no API key (returns source unchanged):
python run.py --model passthrough --out out/passthrough.jsonl

# sanity-check scoring (should score near ceiling):
python run.py --model reference --out out/reference.jsonl

# real run, frontier model, with the skill scaffold + two-pass polish:
export ANTHROPIC_API_KEY=...
python run.py --model anthropic:claude-opus-4-8 --two-pass --out out/claude.jsonl

# naive baseline (no skill), to measure how much the scaffold helps:
python run.py --model anthropic:claude-opus-4-8 --naive --out out/claude_naive.jsonl
"""
from __future__ import annotations
import argparse
import json
import pathlib
import sys
import time

import adapters

DEFAULT_TESTSET = pathlib.Path(__file__).resolve().parents[1] / "testset" / "en_ko_ui.jsonl"


def load_testset(path: pathlib.Path) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as f:
        for ln, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as e:
                sys.exit(f"Bad JSON on {path}:{ln}: {e}")
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description="Run En→Ko translations for the benchmark.")
    ap.add_argument("--model", required=True, help="adapter spec, e.g. anthropic:claude-opus-4-8")
    ap.add_argument("--testset", type=pathlib.Path, default=DEFAULT_TESTSET)
    ap.add_argument("--out", type=pathlib.Path, required=True)
    ap.add_argument("--two-pass", action="store_true", help="translate, then native-polish (Pass 2)")
    ap.add_argument("--naive", action="store_true", help="no skill scaffold (baseline)")
    ap.add_argument("--limit", type=int, default=0, help="only first N rows (0 = all)")
    args = ap.parse_args()

    testset = load_testset(args.testset)
    if args.limit:
        testset = testset[: args.limit]
    adapter = adapters.get_adapter(args.model)
    use_skill = not args.naive

    args.out.parent.mkdir(parents=True, exist_ok=True)
    ok = err = 0
    with open(args.out, "w", encoding="utf-8") as fout:
        for i, entry in enumerate(testset, 1):
            # ReferenceEcho needs the gold answer
            if hasattr(adapter, "current_reference"):
                adapter.current_reference = entry.get("reference_ko", "")
            rec = {
                "id": entry["id"], "model": args.model, "two_pass": args.two_pass,
                "naive": args.naive, "category": entry.get("category"),
                "register": entry.get("register"), "source_en": entry["source_en"],
                "placeholders": entry.get("placeholders", []),
                "glossary": entry.get("glossary", {}),
                "reference_ko": entry.get("reference_ko", ""),
            }
            try:
                system, user = adapters.build_translate_prompt(entry, use_skill=use_skill)
                draft = adapter(system, user)
                translation = draft
                if args.two_pass and use_skill:
                    s2, u2 = adapters.build_polish_prompt(entry, draft)
                    translation = adapter(s2, u2)
                    rec["draft_ko"] = draft
                rec["translation"] = translation
                ok += 1
                status = "ok"
            except Exception as e:  # one bad row shouldn't kill the run
                rec["translation"] = ""
                rec["error"] = f"{type(e).__name__}: {e}"
                err += 1
                status = "ERR"
            fout.write(json.dumps(rec, ensure_ascii=False) + "\n")
            print(f"[{i}/{len(testset)}] {entry['id']:<10} {status}", file=sys.stderr)
            if not hasattr(adapter, "current_reference"):
                time.sleep(0.05)  # gentle pacing for real network APIs

    print(f"\nDone: {ok} ok, {err} errors → {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
