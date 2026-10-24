# Josa (조사) after a variable — the product/UI killer

Korean particles have two allomorphs, chosen by whether the **preceding word ends in a consonant (받침/batchim) or a vowel**. When the preceding word is a runtime `{variable}`, you cannot hardcode the particle. This is *the* classic Korean localization bug.

## The particle pairs

| Particle (role) | after **vowel** | after **consonant** | ㄹ-batchim behaves as |
|---|---|---|---|
| subject 이/가 | 가 | 이 | consonant → 이 |
| object 을/를 | 를 | 을 | consonant → 을 |
| topic 은/는 | 는 | 은 | consonant → 은 |
| and/with 와/과 | 와 | 과 | consonant → 과 |
| **means/dir (으)로** | **로** | **으로** | **vowel-like → 로** (서울**로**, not 서울으로) |
| copula (이)에요 | 예요 | 이에요 | consonant → 이에요 |
| also (이)나 | 나 | 이나 | consonant → 이나 |

**The ㄹ exception applies ONLY to (으)로:** a word ending in ㄹ takes **로** (서울로, 지하철로). For every *other* particle, ㄹ is an ordinary consonant (서울**을**, 서울**이**, 서울**과**).

## The batchim algorithm

Hangul syllables occupy U+AC00–U+D7A3 with layout `((초성×21)+중성)×28 + 종성`. For the **last character** of the word:

```
code = ord(lastChar)
jong = (code - 0xAC00) % 28
hasBatchim = jong != 0          # 0 ⇒ ends in a vowel
isRieul    = jong == 8          # the ㄹ final (single ㄹ only; ㄺ/ㄽ are not ㄹ here)

# selection
if particle is the (으)로 family:  use 로 if (not hasBatchim or isRieul) else 으로
else:                              use the consonant form if hasBatchim else the vowel form
```

## Numbers and Latin letters (by pronunciation)

When the word ends in a digit or Latin letter, the particle follows how it's **read aloud**:

| Trailing | Read as | Ends in | 을/를 · (으)로 |
|---|---|---|---|
| 1 | 일 | **ㄹ** | 을 · **로** |
| 3 | 삼 | ㅁ | 을 · 으로 |
| 6 | 육 | ㄱ | 을 · 으로 |
| 7 | 칠 | **ㄹ** | 을 · **로** |
| 8 | 팔 | **ㄹ** | 을 · **로** |
| 10 | 십 | ㅂ | 을 · 으로 |
| L / R | 엘 / 알 | **ㄹ** | 을 · **로** |
| M / N | 엠 / 엔 | ㅁ/ㄴ | 을 · 으로 |
| B, C, D, …(vowel-name) | 비, 시, … | vowel | 를 · 로 |

Note multi-digit numbers depend on the *whole* reading: `10`→십(ㅂ), `16`→십육→육(ㄱ), `21`→이십일→일(ㄹ). `josa.py` (in the skill root) computes the Sino-Korean reading and inspects its last syllable, so it gets these right.

## How to fix it (in priority order)

1. **Resolve at render time in code.**
   - Python: `from josa import attach; attach("메뉴", "을/를") → "메뉴를"` (see `josa.py` in the skill root).
   - JS/TS: **es-hangul** — `josa('서울', '으로') → '서울로'`.
   - i18n message strings: **i18next-korean-postposition-processor** — translators write `"{{city}}[[으로]] 이동"` and the processor resolves it (handles numbers).
2. **Rephrase to avoid the adjacency.** Restructure so the variable isn't immediately followed by a phoneme-sensitive particle (e.g. end the clause at the variable, or use a particle that doesn't alternate like 에/도/만 where meaning allows).
3. **Dual form — last resort.** `을(를)`, `이(가)`, `은(는)`, `와(과)`, `(으)로`. Universally understood but reads as unpolished; acceptable in terse logs/admin, avoid in consumer copy.

## Framework reality
- **ICU MessageFormat / gettext**: no native josa support (only plural/select). Precompute the particle in code before the string reaches the formatter, or use the i18next processor.
- The dominant Korean-company OSS for this is Toss's **es-hangul** (JS) and the Python `korean` / `korean-josa-python` packages (which implement the ㄹ→로 exception explicitly).
