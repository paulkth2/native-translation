# Final self-review checklist (ko-KR product/UI)

Reject and fix the translation if any box would be unchecked.

## Register & form
- [ ] One speech level throughout (no 합쇼체/해요체/한다체 mixing).
- [ ] Buttons/labels are noun-style with **no trailing period**.
- [ ] Full-sentence messages end with a period; errors use the standard verbs (~할 수 없습니다 / ~하지 못했습니다 / ~하는 동안 오류가 발생했습니다).

## 번역투 (translationese)
- [ ] No `~에 의해` / inanimate-subject passive; active voice preferred.
- [ ] No double-passive (`되어지다`, `보여지다`, `쓰여지다`).
- [ ] No `당신/너/그/그녀`; subjects/pronouns dropped where Korean allows.
- [ ] No unnecessary plural `들`.
- [ ] "have" rendered as `~에/에게 ~이 있다`, not `가지다`.
- [ ] No stray `하나의/한 개의` for a/an.
- [ ] No sentence-initial `그리고/그러나/그래서` chains; `A, B, and C` → `A, B, C`.
- [ ] Long English sentences split into natural Korean clauses.

## Placeholders, terms, josa
- [ ] Every placeholder/tag preserved, same count, contents untouched.
- [ ] DNT/brand/product names kept in English.
- [ ] Required glossary terms all present (josa-stripped match).
- [ ] **No particle hardcoded immediately after a `{variable}`** (resolved in code, rephrased, or dual-form).

## Korean conventions
- [ ] Plurals use a single `other` form; counts use counters (개/명/번/장), not 들.
- [ ] Numbers `1,234`; dates `2026년 6월 13일`; currency `₩1,000,000` / `1,000,000원`.
- [ ] No `!`; progress uses `…`.
- [ ] Korean not abbreviated; Latin acronyms kept in English.

## Orthography
- [ ] 맞춤법 / 띄어쓰기 correct (verify with `kiwipiepy` re-tokenization if automated).
- [ ] Loanwords follow 외래어 표기법; "download" → 다운로드하다/내려받다 (not 다운로드 받다).
