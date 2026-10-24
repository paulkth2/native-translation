# Korean-augmented MQM rubric (En→Ko, product/UI)

Based on GEMBA-MQM (Kocmi & Federmann, WMT23) with Korean-specific error categories added. Use for LLM-as-judge and/or human evaluation.

## Error categories

**Accuracy**
- `mistranslation` — meaning changed
- `omission` — source content dropped
- `addition` — content not in source
- `untranslated` — English left untranslated that should be Korean

**Fluency**
- `grammar` — ungrammatical Korean
- `spelling` — 맞춤법 error
- `register` — **honorific/speech-level mismatch or inconsistency (존댓말/반말, 합쇼체/해요체 mixing)**
- `inconsistency` — same concept rendered differently

**Korean-specific** *(the rows generic MQM misses)*
- `josa` — wrong particle (조사), incl. josa hardcoded after a variable
- `spacing` — 띄어쓰기 error
- `translationese` — 번역투: passive calque, pronoun over-translation, plural 들 overuse, 가지다 for "have", literal connectives, unsegmented long sentence
- `loanword` — 외래어 표기법 violation or avoidable loanword
- `honorific` — wrong honorific level for the audience/surface

**Terminology**
- `term` — wrong or inconsistent domain/glossary term

**Locale/format**
- `number_date` — number/date/currency not in Korean convention
- `placeholder` — placeholder/tag changed, dropped, or mis-ordered; **count mismatch with source**

**Style**
- `awkward` — unnatural but not ungrammatical

**Other**
- `non_translation` — gibberish / refusal / wrong language

## Severity weights (per error)
- `critical` = **−25** (blocks comprehension or breaks the UI — e.g. dropped placeholder, wrong meaning)
- `major` = **−5** (disrupts but understandable — e.g. register mismatch, clear 번역투)
- `minor` = **−1** (cosmetic — e.g. a draggable 들, spacing nit)

**Segment MQM score** = `max(0, 100 + Σ severity_weight)`.

## Dimension ratings (1–5), product/UI-tuned
- **Adequacy** — meaning preserved, placeholders/terms intact.
- **Fluency** — grammatical, correct 맞춤법/띄어쓰기.
- **Naturalness** — reads as native product copy, free of 번역투.
- **Register fit** — correct & consistent speech level for the surface (button/message/error).

A translation can be adequate+fluent yet fail **Naturalness** (the 번역투 case) or **Register fit** (honorific mismatch) — those two dimensions are where Korean quality is usually won or lost.
