# Korean localization benchmark

A small evaluation harness for comparing translation methods and models on English-to-Korean product/UI strings.

The tagged dataset is a starting probe, not a representative production corpus. Replace or extend it with strings from your application before drawing conclusions.

## Run without a model

```sh
cd harness
python3 run.py --model passthrough --out out/passthrough.jsonl
python3 run.py --model reference --out out/reference.jsonl
python3 score.py out/passthrough.jsonl out/reference.jsonl
```

`passthrough` leaves source text untranslated; `reference` returns the dataset's reference translation. These exercise the harness and scoring path without network calls.

## Compare methods

Choose a supported adapter and model, configure its credentials, then compare a minimal prompt with the translation-and-polish scaffold:

```sh
python3 run.py --model PROVIDER:MODEL --naive --out out/naive.jsonl
python3 run.py --model PROVIDER:MODEL --two-pass --out out/scaffold.jsonl
python3 score.py out/naive.jsonl out/scaffold.jsonl
```

Adapters include Anthropic, OpenAI, Gemini, public Papago and CLOVA Studio APIs, Hugging Face, Ollama, and a Codex CLI path. Consult `harness/adapters.py` for the exact environment variables and optional SDKs. API adapters send text to the chosen provider and may incur charges.

## Evaluation layers

| Layer | What it measures |
|---|---|
| Built-in heuristics | Selected placeholder, particle, register, and translationese failures |
| Optional chrF/COMET metrics | Reference-based or learned translation-quality signals |
| Optional model judge | Korean MQM rubric scores and naturalness judgments |
| Human review | Meaning, context, and native-language quality |

Optional metrics and model SDKs are listed in `requirements.txt`. The default heuristic pass uses the standard library. Benchmark heuristics are separate from the more extensive checks in `skills/korean-translation/translate.py`; their scores are not interchangeable.

`harness/enforce.py` provides an experimental translate/check/repair loop that writes unresolved items to a review file. `demo_make_outputs.py` generates synthetic demonstration data; generated reports must not be presented as measured model results.

The rubric lives in `rubric/`, the seed dataset in `testset/`, and the runnable tools in `harness/`. Automated scores and a small seed dataset cannot establish a model leaderboard.
