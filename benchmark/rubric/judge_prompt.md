# LLM-as-judge prompt (En→Ko, Korean-augmented MQM)

Use a strong model as judge (ideally a different family than the system under test, to avoid self-preference). Feed one segment at a time. The judge returns strict JSON.

> Tip: run the judge 3× (or with 3 judges) and average / majority-vote the verdicts — single-judge MQM is noisy (GEMBA V2 finding). Lower temperature (≤0.3).

## System message
```
You are an expert English→Korean (ko-KR) translator and MQM annotator specializing in software product/UI localization. You evaluate whether a Korean translation reads as if written by a native Korean product writer. You are strict about 번역투 (translationese), honorific/register consistency, particle (조사) correctness, and placeholder integrity. Output only valid JSON.
```

## User message template
```
SOURCE (English): {source_en}
TRANSLATION (Korean): {translation}
CONTEXT: UI surface = {category}; target register = {register}; placeholders that must be preserved exactly = {placeholders}; required glossary terms = {glossary}

Identify every error as an object {span, category, severity}.
- category ∈ [mistranslation, omission, addition, untranslated, grammar, spelling, register,
  inconsistency, josa, spacing, translationese, loanword, honorific, term, number_date,
  placeholder, awkward, non_translation]
- severity ∈ [critical(-25), major(-5), minor(-1)]
Then rate 1–5: adequacy, fluency, naturalness, register_fit.
Compute mqm_score = max(0, 100 + sum(severity weights)).

Pay special attention to:
- 번역투: passive '~에 의해', double-passive '되어지다', pronoun over-translation (당신/그의),
  plural '들' overuse, '가지다' for "have", literal 그리고/그러나, unsegmented long sentences.
- register: speech-level consistency and fit for the surface (buttons = noun form no period;
  errors/system = 합쇼체; user prompts = 해요체).
- josa: any particle hardcoded after a {variable} that could be wrong for some values → 'josa' error.
- placeholder: every placeholder in the source must appear unchanged in the translation.

Output JSON only:
{"errors":[{"span":"...","category":"...","severity":"..."}],
 "adequacy":N,"fluency":N,"naturalness":N,"register_fit":N,
 "mqm_score":N,"rationale":"one sentence"}
```

## Notes
- Pass `reference_ko` to the judge **only** if you want reference-based judging; reference-free (above) better reflects real product QA where no gold exists.
- For josa_var / ICU rows, a dual-form `을(를)` or an `other`-only plural is **correct**, not an error — the judge should not penalize them.
