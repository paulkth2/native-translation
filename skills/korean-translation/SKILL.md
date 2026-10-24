---
name: korean-translation
description: Translate English product, UI, and technical content into Korean, polish Korean writing, and check placeholders, terminology, register, and particles using a configurable model endpoint and deterministic quality checks.
---

# Korean Translation

Use this skill for English-to-Korean localization, Korean prose polishing, and QA of translated UI strings. The runner uses Python's standard library and a user-configured HTTPS Chat Completions endpoint.

## Configuration

Use the directory containing this file as `SKILL_DIR`. Before making model calls, confirm the user has configured:

- `LLM_ENDPOINT`: the complete HTTPS chat-completions endpoint.
- `LLM_MODEL`, `--model`, or a configuration-file model: the exact route to use.
- `LLM_API_KEY`: the bearer key. The gitignored `.api_key` beside the script is also supported.

Endpoint and model settings may be supplied in `config.local.json`, based on `config.example.json`. Never put a key in that file, source control, a command argument, or output. There is no default destination or model. Endpoint compatibility and model availability depend on the user's provider.

Use `--dry-run` to inspect a request without making a network call. Do not silently substitute a provider or model when a request fails.

## Workflow

1. Establish the content type, intended audience, register, glossary, and protected terms from the task.
2. Keep placeholders and runtime variables intact. Choose `translate` for English source or `polish` for existing Korean.
3. Run the CLI. Add `--fix` for targeted repair of fixable flags; use `--strict` when every remaining flag must fail the check.
4. Inspect status, flags, and exit code. Report remaining problems and any untranslated source retained by document mode.
5. Review meaning and naturalness. A clean mechanical check is not proof of translation quality.
6. Remind the caller to resolve dual-form particle markers after substituting runtime values.

## Commands

```sh
python3 "$SKILL_DIR/translate.py" --register haeyo "Sign in to continue"
python3 "$SKILL_DIR/translate.py" --mode polish --register hapsyo "당신의 계정이 삭제되어질 것입니다"
python3 "$SKILL_DIR/translate.py" --glossary "workspace=워크스페이스,toggle=토글" --fix "Enable the workspace toggle"
python3 "$SKILL_DIR/translate.py" --doc < input.en.md > output.ko.md
python3 "$SKILL_DIR/translate.py" --in catalog.jsonl --out catalog.ko.jsonl --auto-dnt
python3 "$SKILL_DIR/translate.py" --dry-run "Delete {fileName}"
```

Registers: `haeyo`, `hapsyo`, `noun`, and `handa`. Do not force one register across contexts that require different UI conventions.

Batch input is JSONL with `id` and `source_en` or `text`; rows can override `register`, `dnt`, `glossary`, and `mode`. The output includes each row's translation, status, tries, and flags.

## Protection and checks

- **Masking:** code, inline code, and URLs are protected before translation and restored afterward. Placeholders remain visible for particle checks. Polish mode also protects quoted spans unless `--polish-dialogue` is explicitly chosen.
- **Integrity:** placeholder counts, format specifiers, mask integrity, and relevant quotation marks are checked.
- **Terminology:** only glossary terms relevant to the segment are added to the prompt; retention is checked afterward.
- **Register and Korean:** checks cover selected register mismatches, particles after variables, introduced foreign scripts, and common translationese patterns.
- **Repair:** the runner uses a bounded retry budget (`--max-retries`, default 2), keeps the best attempt, and targets the reported fixable failures.
- **Advisory signals:** pronouns, some literal constructions, unnecessary plurals, low Korean content, redundant phrases, and other style heuristics can be false positives. They warn a reviewer; they do not all trigger rewriting.

For polish mode, `--max-diff-ratio` flags excessive rewriting. A value around `0.15` can help review minimal edits, but it is an advisory check, not a hard editing constraint or a semantic-preservation guarantee.

## Output and failure handling

Single-string mode writes the Korean result to stdout and diagnostics to stderr. Status is `clean`, `flagged`, `broken`, or `empty`; batch rows can also be `error`.

- Single-string mode fails rather than emitting a broken or empty result.
- Document mode retains the source for a failed block and exits nonzero.
- Batch mode records row-level failures and exits nonzero.
- `--strict` exits 2 on remaining flags in any mode.

Always surface `broken`, `empty`, and `error` results. Describe `flagged` output as needing review. Do not present a flagged translation as ready to ship without stating its flags. Never treat an HTTP error or an empty response as a successful translation.

Document mode preserves supported Markdown structure, including code fences and selected block markers. Pipe-table cells are not translated. Inspect documents after translation; complex Markdown and semantic drift require human review.

## Render-time particles

The gate accepts dual forms such as `{fileName}을(를)` and `{folder}(으)로`. The `josa.py` template helper uses a different, explicit convention: `{fileName}[을/를]` and `{folder}[(으)로]`. Convert markers when integrating it. The module provides `attach(word, particle)` and `fill(template, **values)` for use after runtime values are known. Latin names, abbreviations, and ambiguous pronunciations may need application-specific handling.

## References and checks

- [Anti-translationese](references/anti-translationese.md)
- [Review checklist](references/checklist.md)
- [Terminology](references/glossary-and-terminology.md)
- [Register and UI](references/register-and-ui.md)
- [Particles](references/josa.md)

```sh
python3 "$SKILL_DIR/test_translate.py"
python3 "$SKILL_DIR/josa.py"
```

Tests use mocked model calls. Run live translation only when the task calls for it and the destination is configured.
