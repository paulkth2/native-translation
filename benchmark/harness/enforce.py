#!/usr/bin/env python3
"""
enforce.py — local En→Ko translation ENFORCEMENT loop (no server / no infra needed).

Per string:  translate (skill + 2-pass) -> check (heuristics) -> if it fails, send a
TARGETED auto-post-edit ("fix exactly these issues, change nothing else") -> re-check.
Repeat up to --max-fix times. Anything still failing is escalated to a human-review file
instead of being silently shipped. The non-negotiables (placeholder integrity, DNT,
required terms, josa-after-variable) are what gate it.

Runs entirely locally. Point --model at a local model or a supported cloud provider:
  ollama:hf.co/Qwen/Qwen2.5-7B-Instruct-GGUF   (fully offline; `ollama pull` first)
  hyperclova:HCX-007                            (CLOVA Studio API, CLOVA_API_KEY)
  anthropic:claude-opus-4-8 / openai:* / gemini:*   (cloud, if reachable)
  passthrough / reference                       (no-model wiring tests)

Input  : the testset jsonl, run.py-style jsonl, or a flat i18n resource {key: "english"}.
Output : <out> jsonl (id, translation, status, tries, remaining flags) + <out>.review.jsonl.

Examples
--------
python enforce.py --model ollama:hunyuan-mt --in ../testset/en_ko_ui.jsonl --out out/ko.jsonl
python enforce.py --model reference --in ../testset/en_ko_ui.jsonl --out out/ko.jsonl   # demo: all clean
python enforce.py --model passthrough --in ../testset/en_ko_ui.jsonl --out out/ko.jsonl # demo: escalates
"""
from __future__ import annotations
import argparse
import json
import pathlib
import sys

import adapters
import score

SEV_MAJOR = ("major", "critical")

# flag type -> Korean corrective instruction for the targeted post-edit
FIX_INSTRUCTION = {
    "pronoun": "대명사(당신/너의/그의/그녀 등)를 제거하거나 자연스럽게 생략하세요.",
    "plural_deul": "불필요한 복수 표지 '들'을 제거하세요.",
    "josa_after_var": "변수 바로 뒤에 조사를 고정하지 말고 '을(를)','이(가)','은(는)','(으)로' 형태로 표기하세요.",
    "have_gajida": "'가지다/가지고 있다'를 '~에/에게 ~이 있다'로 바꾸세요.",
    "article_hana": "불필요한 '하나의/한 개의'를 제거하세요.",
    "double_passive": "이중피동(되어지다/보여지다 등)을 단순 피동이나 능동으로 바꾸세요.",
    "connective": "문장 첫머리의 '그리고/그러나/그래서'를 연결어미(-고/-지만/-니까)로 바꾸세요.",
    "exclamation": "느낌표(!)를 제거하세요.",
    "ascii_ellipsis": "'...'를 말줄임표 문자 '…'로 바꾸세요.",
    "label_trailing_period": "버튼/레이블 끝의 마침표를 제거하세요.",
    "placeholder_mismatch": "원문의 모든 플레이스홀더/태그를 개수와 내용 그대로 유지하세요.",
}


def fix_instruction(flag: dict) -> str:
    t = flag["type"]
    if t == "dnt_violation":
        return f"'{flag.get('detail','')}'은(는) 번역·음역하지 말고 영문 그대로 두세요."
    if t == "term_missing":
        return f"필수 용어 '{flag.get('detail','')}'을(를) 반영하세요."
    return FIX_INSTRUCTION.get(t, f"문제 '{t}'를 수정하세요.")


def major_flags(rec_like: dict) -> list[dict]:
    return [f for f in score.heuristic_flags(rec_like) if f["severity"] in SEV_MAJOR]


def load_entries(path: pathlib.Path, fmt: str) -> list[dict]:
    text = path.read_text(encoding="utf-8")
    if fmt == "json" or (fmt == "auto" and path.suffix == ".json"):
        data = json.loads(text)
        # flat {key: "english"} resource
        return [{"id": k, "source_en": v, "placeholders": [], "glossary": {},
                 "register": "", "category": "", "reference_ko": ""}
                for k, v in data.items()]
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def translate(adapter, entry, two_pass) -> str:
    sys_t, usr_t = adapters.build_translate_prompt(entry, use_skill=True)
    draft = adapter(sys_t, usr_t)
    if two_pass:
        sys_p, usr_p = adapters.build_polish_prompt(entry, draft)
        draft = adapter(sys_p, usr_p)
    return draft


def post_edit(adapter, entry, draft, flags) -> str:
    system = adapters.load_system_prompt()
    issues = "\n".join(f"- {fix_instruction(f)}" for f in flags)
    user = ("다음 한국어 번역에서 아래 문제만 고치고 나머지는 절대 바꾸지 마세요. "
            "의미와 플레이스홀더는 그대로 유지하고, 고친 번역만 출력하세요.\n\n"
            f"문제:\n{issues}\n\n원문(영어): {entry['source_en']}\n번역: {draft}")
    return adapter(system, user)


def main() -> None:
    ap = argparse.ArgumentParser(description="Local En→Ko enforcement loop.")
    ap.add_argument("--model", required=True, help="adapter spec, e.g. ollama:hunyuan-mt")
    ap.add_argument("--in", dest="inp", required=True, type=pathlib.Path)
    ap.add_argument("--out", required=True, type=pathlib.Path)
    ap.add_argument("--format", choices=["auto", "jsonl", "json"], default="auto")
    ap.add_argument("--max-fix", type=int, default=2, help="auto-fix attempts before escalating")
    ap.add_argument("--no-two-pass", action="store_true")
    args = ap.parse_args()

    adapter = adapters.get_adapter(args.model)
    entries = load_entries(args.inp, args.format)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    review_path = args.out.with_suffix(args.out.suffix + ".review.jsonl")

    counts = {"clean": 0, "fixed": 0, "needs_review": 0, "error": 0}
    out_f = open(args.out, "w", encoding="utf-8")
    rev_f = open(review_path, "w", encoding="utf-8")

    for i, e in enumerate(entries, 1):
        e.setdefault("placeholders", []); e.setdefault("glossary", {})
        if hasattr(adapter, "current_reference"):
            adapter.current_reference = e.get("reference_ko", "")
        rec = {"id": e.get("id"), "model": args.model, "source_en": e["source_en"],
               "category": e.get("category", ""), "reference_ko": e.get("reference_ko", "")}
        try:
            draft = translate(adapter, e, two_pass=not args.no_two_pass)
            probe = {**e, "translation": draft}
            flags = major_flags(probe)
            status, tries = ("clean", 0) if not flags else ("", 0)
            while flags and tries < args.max_fix:
                draft = post_edit(adapter, e, draft, flags)
                probe = {**e, "translation": draft}
                flags = major_flags(probe)
                tries += 1
            if not status:
                status = "fixed" if not flags else "needs_review"
            rec.update(translation=draft, status=status, tries=tries,
                       remaining_flags=[f["type"] for f in flags])
        except Exception as ex:
            status = "error"
            rec.update(translation="", status=status, error=f"{type(ex).__name__}: {ex}")

        counts[status] = counts.get(status, 0) + 1
        out_f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        if status in ("needs_review", "error"):
            rev_f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"[{i}/{len(entries)}] {str(rec['id']):<10} {status}"
              + (f" (after {rec.get('tries')} fix)" if status == "fixed" else ""), file=sys.stderr)

    out_f.close(); rev_f.close()
    total = sum(counts.values())
    auto_ok = counts["clean"] + counts["fixed"]
    print(f"\n=== enforcement summary ({total} strings) ===", file=sys.stderr)
    for k in ("clean", "fixed", "needs_review", "error"):
        print(f"  {k:<13} {counts.get(k,0)}", file=sys.stderr)
    print(f"  auto-passed   {auto_ok}/{total} ({round(100*auto_ok/total) if total else 0}%)", file=sys.stderr)
    print(f"  → {args.out}   (human review: {review_path}, {counts['needs_review']+counts['error']} items)",
          file=sys.stderr)


if __name__ == "__main__":
    main()
