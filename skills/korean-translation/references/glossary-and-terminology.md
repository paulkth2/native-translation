# Glossary, terminology & placeholder protection

Closed frontier APIs **cannot do hard constrained decoding** (no per-token logit control), so terminology enforcement = **prompt injection + post-hoc check + auto post-edit**. Treat the LLM's adherence as probabilistic and verify.

## 1. Inject only the terms present in the segment
Don't dump the whole termbase — match the source string against the glossary and inject just the matched entries. Whole-termbase dumps dilute the context and hurt quality.

Prompt form (WMT25-winning pattern):
```
다음 용어를 반드시 그대로 사용하여 번역하세요(필요시 조사만 붙임):
- dashboard → 대시보드
- sign in → 로그인
- workspace → 워크스페이스 (고유기능명, 변경 금지)
```
Supply the Korean term as a **base form (lemma)**; instruct the model to attach josa/endings naturally. Frontier LLMs inflect Korean well from a lemma — that's why prompt injection is usually enough for Korean.

## 2. Do-Not-Translate (DNT)
Mark items that must pass through unchanged: product/brand names, code identifiers, keyboard keys, file paths, env vars, API names.
- Keep them in English: `App Store`, `Firefox`, `Ctrl`, `null`, `package.json`.
- Korean particles attach directly to the English token: `App Store에서`, `Firefox를`.
- Tag them in the glossary with a `DNT` flag (see `assets/glossary.sample.tsv`).

## 3. Placeholder & tag protection
Before translating, **mask** variables/tags so the model can't mangle them, then restore:
- Patterns: `{var}`, `{{var}}`, `%s`, `%d`, `%1$s`, `<b>…</b>`, `</br>`, `\n`, `:emoji:`.
- Rule for the model: *"Keep every placeholder and tag exactly as-is, same count; you may reorder them to fit Korean word order; never translate or add spaces inside them."*
- Masking (replace with `⟦0⟧`, `⟦1⟧`, …) is more reliable than asking nicely; restore after.

## 4. Post-check (the enforcement layer)
After translation, verify programmatically and auto-fix:
1. **Placeholder integrity** — the multiset of placeholders/tags in the output equals the source. If not, reject/retry.
2. **Term presence** — each required Korean term appears, matching **with josa stripped** (so "대시보드를" counts as "대시보드"). Use a morphological match (`kiwipiepy`) or a suffix-tolerant regex. If a required term is missing, send a targeted post-edit:
   ```
   다음 번역에서 누락된 용어를 반영해 고치되, 나머지는 바꾸지 마세요.
   누락: workspace → 워크스페이스
   번역: {draft}
   ```
3. **DNT integrity** — DNT tokens appear verbatim (not transliterated/translated).

`benchmark/harness/score.py` implements placeholder-integrity and term-presence checks you can reuse in production.

## 5. Translation Memory (optional, high value)
- **Exact TM match** → bypass the LLM (deterministic reuse).
- **High-fuzzy TM** → inject 5–10 matched pairs as few-shot examples ("adaptive MT"); this reliably improves in-domain terminology and style.

## Sources of UI terminology
- **Microsoft Terminology Collection** (TBX, Korean) — IT/UI base glossary.
- Your product's existing localized strings (build a TM from OPUS GNOME/KDE/Mozilla for generic UI verbs).
