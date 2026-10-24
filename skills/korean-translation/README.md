# Korean Translation skill

Translate English UI strings and technical content, polish Korean prose, and check mechanical localization constraints.

See [SKILL.md](SKILL.md) for commands, endpoint configuration, flags, failure handling, and review guidance. The [project README](../../README.md) includes plugin installation and end-to-end examples.

The runner requires Python 3 and a configured HTTPS Chat Completions endpoint, model, and bearer key. The core has no third-party Python dependencies.

```sh
python3 translate.py --dry-run "Delete {fileName}"
python3 test_translate.py
python3 josa.py
```

These commands run without a live model call. `--dry-run` reports the request with credentials redacted. Configure `LLM_ENDPOINT`, `LLM_MODEL`, and `LLM_API_KEY` before a real translation.
