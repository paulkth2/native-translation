#!/usr/bin/env python3
"""translate.py — Korean (En→Ko) translation + enforcement via a configurable chat-completions endpoint.

Pipeline (research-grounded — see ./SKILL.md): mask protected spans → translate/polish via the
gateway → deterministic gate → span-targeted fix → escalate. The deterministic gate is the EXTERNAL
signal the literature says reliable correction needs (intrinsic self-critique is unreliable); a
single gated fix-pass is optimal.

Zero dependencies (Python 3 stdlib only). Network calls use your configured endpoint
(OpenAI-compatible /chat/completions). Reasoning models return a separate reasoning_content; empty
answers are retried then fail non-zero (never a silent empty string).

TIER-1/2 capabilities:
  A. mask code/inline-code/URLs before translate, restore after (unbreakable by construction)
  B. glossary-RAG: inject only in-segment terms with their target form; gate checks retention
  C. span/flag-targeted fix: refine ONLY the flagged spans (TEaR-style), not "improve everything"
  D. register escalate-to-fix: a requested-but-violated register is fixed, not just warned (--fix)
  E. --doc mode: preserve markdown structure verbatim, translate prose blocks
  F. batch --auto-dnt: pin recurring brand/identifier tokens corpus-wide (consistency)

USAGE
  python3 translate.py "Sign in to continue"
  python3 translate.py --mode polish --register hapsyo "당신의 계정이 삭제되어질 것입니다"
  python3 translate.py --glossary "Anthropic=Anthropic,toggle=토글" --fix "Enable the toggle"
  python3 translate.py --doc < README.en.md > README.ko.md
  python3 translate.py --in catalog.jsonl --out ko.jsonl --auto-dnt
  python3 translate.py --dry-run "..."

Render-time josa: keep the {name}을(를) / {file}(으)로 marker; resolve with josa.py (attach/fill).

KEY/CONFIG: api_key = env LLM_API_KEY -> ./.api_key ONLY (never a config file — a bearer token must not
  be committed). endpoint = env LLM_ENDPOINT -> config endpoint -> config base_url+/chat/completions ->
  default. model = --model -> env LLM_MODEL -> config model -> default.
"""
import argparse
import difflib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ENDPOINT = ""  # Set LLM_ENDPOINT or config.local.json explicitly.
DEFAULT_MODEL = ""  # Set LLM_MODEL, --model, or config.local.json explicitly.
MAX_TOKENS_CAP = 32768
MASK_OPEN, MASK_CLOSE = "⟦", "⟧"
_SENTINEL_ESC = ""                  # private-use escape for a literal ⟦ present in the source (RG-01)
_SENTINEL_RE = re.compile(r"⟦(\d+)⟧")
_ESC_UNDO_RE = re.compile(_SENTINEL_ESC + "([01])")     # ⟨ESC⟩0 (literal ESC) / ⟨ESC⟩1 (literal ⟦)
_ESC_STRAY_RE = re.compile(_SENTINEL_ESC + "(?![01])")  # a bare escape char = mangled/hallucinated

# ── deterministic checks (ported from korean-product-translation/scripts/gate.mjs) ──────────────
# placeholder tokens (balanced {…} are scanned separately, ICU-nesting-aware): printf incl. the
# escaped %% literal, real tag-shaped <…>, and the literal \n token (RG-04/05/06).
_PRINTF_SPEC = r"%\d*\$?[sd]"          # a printf value placeholder (incl. positional/width: %1$s, %12d)
_PRINTF_RE = re.compile(r"%%|" + _PRINTF_SPEC)
_TAG_RE = re.compile(r"</?[A-Za-z][^<>]*>")
_NL_RE = re.compile(r"\\n")
# josa_after_var: a hardcoded single-form particle glued directly to a placeholder terminator (brace
# close or printf spec). Tags/'>' are NOT terminators — the particle there attaches to the wrapped
# Korean content, not a runtime value (RG-07). Validated (dual-form / lexical boundary) in
# josa_after_var() below.
_PH_TERM_RE = re.compile(r"\}|" + _PRINTF_SPEC)   # a placeholder terminator: brace close or printf spec
_JOSA_DUAL = {"을": "를", "를": "을", "이": "가", "가": "이", "은": "는", "는": "은", "와": "과", "과": "와"}
# exact valid parenthesized dual forms accepted immediately after a terminator (render-time safe)
_JOSA_PAREN_FORMS = ("(으)로", "(이)나", "(이)랑", "(이)란", "(이)에요")
# single-form (hardcoded) particle families that flag when glued to a placeholder — longest-first so
# 이에요/이나/이란/이랑 win over bare 이; 으로 over 로 (RG-07/08, X2 families).
_JOSA_SINGLE_RE = re.compile(r"(?:으로|이에요|이나|이란|이랑|예요|을|를|은|는|과|와|이|가|로|나|란|랑)")
# CJK unified + CJK compatibility ideographs (RG-18/F03) + kana + cyrillic
FOREIGN_SCRIPT_RE = re.compile(r"[\u4e00-\u9fff\uf900-\ufaff\u3040-\u30ff\u0400-\u04ff]")
# ICU auto-detect (replaces the dead `category` wiring): {name, plural|select|selectordinal, …} in the
# SOURCE. For ICU sources we skip whole-string placeholder parity (leaf messages may be translated)
# but still flag an unbalanced brace count in the output (RG-04 stays caught).
_ICU_RE = re.compile(r"\{\s*\w+\s*,\s*(?:plural|select|selectordinal)\s*,")
_ICU_HEAD_RE = re.compile(r"^\{\s*([A-Za-z0-9_]+)\s*,\s*(plural|select|selectordinal)\b")  # a wrapper's head (var,kw)
HANGUL_RE = re.compile(r"[가-힣]")
LETTER_RE = re.compile(r"[가-힣A-Za-z]")
# masked spans (NOT placeholders — josa needs those visible). URL tokenizer stops before markdown/
# placeholder delimiters, quotes, angle brackets, our sentinels and Hangul; trailing sentence
# punctuation is stripped back out. Fence opener: ``` or ~~~ with ≤3 leading spaces (RG-02/03/31).
_URL_RE = re.compile(r"https?://[^\s`{}\[\]\"'<>⟦⟧가-힣]+")
_URL_TRAIL = ".,;:!?)…"
_FENCE_OPEN_RE = re.compile(r"^( {0,3})(`{3,}|~{3,})(.*)$")
# quoted spans masked ONLY in polish mode (prose-safety): straight "…", curly “…”, Korean 『…』/「…」.
# Masked AFTER code (so a " inside code is already a sentinel) and BEFORE URLs (a quote wrapping a URL
# becomes one span). Keeps quoted dialogue/quotations verbatim so a register rewrite cannot touch them.
_QUOTE_MASK_RE = re.compile(r'"[^"]*"|“[^”]*”|『[^』]*』|「[^」]*」')
_QUOTE_CHARS = set('"“”『』「」')          # multiset compared by the quote-parity gate check
# (type, regex, severity, fixable). D1: only high-precision heuristics are fixable (drive --fix
# rewrites); the FP-prone ones stay advisory (warn only, never rewrite). passive_eui/double_passive
# widened (RG-10/11: 에 의하여/의한; 열려/닫혀/주어져… + optional space, incl. 집니다). plural_deul made
# context-aware (RG-12): quantifier/determiner + 들, or 들 모두/전체 — not bare human plurals.
HEURISTICS = [
    ("passive_eui", re.compile(r"에\s*의(?:해|하여|한)"), "major", True),
    ("double_passive", re.compile(r"(?:되어|보여|쓰여|불려|잊혀|모여|닦여|갈려|열려|닫혀|담겨|알려|찍혀|섞여|쓰이|믿겨|느껴)\s*[지진질져집](?!\s*지?\s*않)"), "major", True),
    ("pronoun", re.compile(r"당신|너의|당신의|그의|그녀"), "major", False),
    ("have_gajida", re.compile(r"가지고\s*있|를\s*가지|을\s*가지|가집니다"), "major", False),
    ("plural_deul", re.compile(r"\d+\s*개(?:의)?[^들]{0,12}들|(?:모든|여러|많은|각)\s*\S{0,8}들|들\s*(?:모두|전체)"), "minor", False),
    ("article_hana", re.compile(r"하나의|한\s*개의"), "minor", False),
    # NARROWED (was: bare 그리고|그러나|그래서 anywhere — a documented FP source; legitimate discourse
    # 그리고 mid-sentence is fine). Now only the machine tell: a discourse connective OPENING a sentence
    # AND immediately comma-set off ('그리고, …' / '…. 그러나, …'). Advisory (D1).
    ("connective", re.compile(r"(?:^|[.!?…]\s+)(?:그리고|그러나|그래서|또한|따라서|하지만),\s"), "minor", False),
    # ── Korean naturalness heuristics — MECHANICAL layer. All ADVISORY (D1: never in
    # FIXABLE_HEURISTICS). Humans judge the keeps; we do NOT try to regex-exempt legitimate re-bracketing.
    # comma_after_conn: a comma glued right after a Korean connective ending — the KatFishNet (ACL 2025)
    # machine signature of MT output. Flags even the "legitimate re-bracketing" case (advisory).
    ("comma_after_conn", re.compile(r"(?:고|서|며|면서|는데|라서|지만),\s"), "minor", False),
    # loan_transliteration: needless katakana-style loans; 리프레시 only when NOT 리프레시 토큰 (a real
    # compound), and 회귀 only in the '회귀가/를/이 발생' bug sense — NOT 회귀 테스트/회귀 분석 (legit CS terms).
    ("loan_transliteration", re.compile(r"리프레시(?!\s*토큰)|드래프트|멘션|프라이어|리마인드|회귀(?:가|를|이)\s*발생"), "minor", False),
    # stock_calque: the recurring 번역투 frames (에 의해 is intentionally NOT here — it stays in its own
    # fixable passive_eui heuristic above; do not fold it in).
    ("stock_calque", re.compile(r"에\s*대(?:하여|한)|에\s*있어서|에도\s*불구하고|[를을]\s*필요로\s*하|로\s*인하여|라고\s*할\s*수\s*있|것이\s*가능하"), "minor", False),
    # redundant_pair: a native + Sino word that mean the same thing stacked (다시 재-, 미리 예방/사전, …).
    ("redundant_pair", re.compile(r"다시\s*재(?:시도|시작|확인|접속)|미리\s*(?:예방|사전)|남은\s*잔여|서로\s*상호"), "minor", False),
    # hollow_frame: empty scaffolding ('~는 것이 필요', '~있을 예정').
    ("hollow_frame", re.compile(r"는\s*것이\s*필요|이?가?\s*있을\s*예정"), "minor", False),
]
TRANSLATIONESE_TYPES = {t for t, _, _, _ in HEURISTICS}
FIXABLE_HEURISTICS = {t for t, _, _, fx in HEURISTICS if fx}
REGISTER_NAME = {"haeyo": "해요체", "hapsyo": "합쇼체(-습니다체)", "noun": "명사형 종결(마침표 없이)", "handa": "한다체"}


def _scan_braces(text):
    """Each top-level balanced {…} group as one token (ICU-nesting-aware). An unbalanced opener
    emits the rest of the string so a dropped/added outer brace changes the multiset (RG-04). An
    unmatched top-level '}' closer is emitted as its own token so '{x}}' vs '{x}' mismatches (RG-orphan)."""
    toks, i, n = [], 0, len(text)
    while i < n:
        if text[i] == "{":
            depth, j = 0, i
            while j < n:
                if text[j] == "{":
                    depth += 1
                elif text[j] == "}":
                    depth -= 1
                    if depth == 0:
                        toks.append(text[i:j + 1]); i = j + 1; break
                j += 1
            else:
                toks.append(text[i:]); i = n
        elif text[i] == "}":
            toks.append("}"); i += 1                     # unmatched closer → integrity token
        else:
            i += 1
    return toks


def placeholders(text):
    text = text or ""
    return Counter(_scan_braces(text) + _PRINTF_RE.findall(text) + _TAG_RE.findall(text) + _NL_RE.findall(text))


def _is_fence_close(line, ch, length):
    k = 0
    while k < len(line) and line[k] == " ":
        k += 1
    if k > 3:
        return False
    run = 0
    while k + run < len(line) and line[k + run] == ch:
        run += 1
    return run >= length and line[k + run:].strip() == ""


def scan_fences(text):
    """Pure line-oriented CommonMark-style fenced-code scanner (shared by masking and doc mode).
    Returns [(start_line, end_line), …] 0-based inclusive covering each fenced block from opener
    through the matching closer (or EOF if unclosed). Openers: ``` or ~~~ with ≤3 leading spaces;
    a closer is a run of the SAME char, length ≥ the opener's, only trailing whitespace after it;
    for ` fences the opener's info string must contain no backtick (RG-03/31)."""
    lines = (text or "").split("\n")
    blocks, i, n = [], 0, len(lines)
    while i < n:
        m = _FENCE_OPEN_RE.match(lines[i])
        if m and not (m.group(2)[0] == "`" and "`" in m.group(3)):
            ch, length = m.group(2)[0], len(m.group(2))
            j = i + 1
            while j < n and not _is_fence_close(lines[j], ch, length):
                j += 1
            end = j if j < n else n - 1                # closer line included; unclosed → to EOF
            blocks.append((i, end)); i = end + 1
        else:
            i += 1
    return blocks


def _escape_sentinels(text):
    """Collision-free, bijective escaping (RG-01). Escape the escape char itself first (⟨ESC⟩→⟨ESC⟩0)
    so a literal U+E000 in the source round-trips, THEN escape MASK_OPEN (⟦→⟨ESC⟩1). The two escapes
    never collide because the second char (0 vs 1) disambiguates them on the reverse pass."""
    return (text or "").replace(_SENTINEL_ESC, _SENTINEL_ESC + "0").replace(MASK_OPEN, _SENTINEL_ESC + "1")


def _unescape_sentinels(text):
    """Reverse _escape_sentinels: ⟨ESC⟩1→⟦, ⟨ESC⟩0→⟨ESC⟩ (literal), in a single unambiguous pass."""
    return _ESC_UNDO_RE.sub(lambda m: MASK_OPEN if m.group(1) == "1" else _SENTINEL_ESC, text)


def _mask_urls(text, token):
    def repl(m):
        url, trail = m.group(0), ""
        while url and url[-1] in _URL_TRAIL:            # keep trailing sentence punctuation in-text
            url, trail = url[:-1], url[-1] + trail
        return token(url) + trail if url else m.group(0)
    return _URL_RE.sub(repl, text)


def mask_protected(text, mask_quotes=False):
    """A: replace fenced code → inline code → (quoted spans, polish only) → URLs with ⟦n⟧ sentinels;
    return (masked, mapping). Ordering lets backticks/braces bound URLs (RG-02); the line-oriented fence
    scan handles ~~~ and unclosed fences (RG-03/31); a pre-existing ⟦ in the source is escaped so it
    cannot collide with our sentinels and is restored on unmask (RG-01). When mask_quotes is set (polish
    mode, no --polish-dialogue) quoted dialogue/quotations are masked too, AFTER code and BEFORE URLs, so
    they survive verbatim and a register/de-번역투 rewrite cannot touch them (prose-safety)."""
    mapping = []
    text = _escape_sentinels(text)

    def token(orig):
        mapping.append(orig)
        return f"{MASK_OPEN}{len(mapping) - 1}{MASK_CLOSE}"

    lines = text.split("\n")                            # 1) fenced blocks
    for start, end in reversed(scan_fences(text)):      # reversed keeps earlier line indices valid
        lines[start:end + 1] = [token("\n".join(lines[start:end + 1]))]
    text = "\n".join(lines)
    text = re.sub(r"`[^`]+`", lambda m: token(m.group(0)), text)  # 2) inline code
    if mask_quotes:                                     # 2.5) quoted spans (polish only) — a span may now
        text = _QUOTE_MASK_RE.sub(lambda m: token(m.group(0)), text)  # wrap a lower-index code sentinel
    text = _mask_urls(text, token)                      # 3) bare URLs
    return text, mapping


def unmask(text, mapping):
    """Restore masked spans by ID-keyed single-pass substitution, then un-escape literals."""
    return unmask_verified(text, mapping)[0]


def unmask_verified(text, mapping):
    """Return (restored, ok). ok is False on sentinel drift — a mapping entry never consumed, a
    duplicated/out-of-range sentinel, or a residual sentinel left in the text (RG-01). Expansion is
    ITERATIVE: a quoted span (masked after code, polish mode) can wrap a lower-index code/URL sentinel,
    so a mapping value may itself contain sentinels. Indices strictly decrease when nested, so ≤len(mapping)
    passes terminate; a stuck out-of-range/residual sentinel breaks the loop and fails verification."""
    seen = Counter()

    def repl(m):
        i = int(m.group(1))
        seen[i] += 1
        return mapping[i] if 0 <= i < len(mapping) else m.group(0)

    restored = text or ""
    for _ in range(len(mapping) + 1):
        if not _SENTINEL_RE.search(restored):
            break
        expanded = _SENTINEL_RE.sub(repl, restored)
        if expanded == restored:                        # only unresolved (out-of-range) sentinels remain
            break
        restored = expanded
    ok = (all(seen.get(i, 0) == 1 for i in range(len(mapping)))   # every entry consumed exactly once
          and all(0 <= i < len(mapping) for i in seen)            # no unknown/out-of-range index
          and not _SENTINEL_RE.search(restored)                  # no residual ⟦n⟧ (even w/ empty mapping)
          and not _ESC_STRAY_RE.search(restored))                # no bare/mangled escape char (RG-01)
    return _unescape_sentinels(restored), ok


def load_terms(dnt_arg, glossary_arg):
    """B: build a src->tgt term map. DNT entries are tgt==src (verbatim). Glossary may be a file."""
    terms = {}
    for t in (dnt_arg or "").split(","):
        t = t.strip()
        if t:
            terms[t] = t
    if glossary_arg:
        if os.path.exists(glossary_arg):
            if glossary_arg.endswith(".json"):
                terms.update({str(k): str(v) for k, v in json.load(open(glossary_arg, encoding="utf-8")).items()})
            else:  # tsv: src<TAB>tgt
                for line in open(glossary_arg, encoding="utf-8"):
                    parts = line.rstrip("\n").split("\t")
                    if len(parts) >= 2 and parts[0].strip():
                        terms[parts[0].strip()] = parts[1].strip()
        else:
            for pair in glossary_arg.split(","):
                if "=" in pair:
                    s, t = pair.split("=", 1)
                    if s.strip():
                        terms[s.strip()] = t.strip()
    return terms


def segment_terms(text, terms):
    return {s: t for s, t in terms.items() if s and s in (text or "")}


# ~40 common English UI words that recur but are NOT brand/identifier DNT tokens (LV-08).
_DNT_STOP = {w.lower() for w in (
    "Save Delete Cancel Settings Search Sign Continue Edit New Open Close Back Next Done Help Home "
    "View File Add Remove Share Copy Paste Send Get Set Update Upload Download Log Start Stop Try "
    "Learn More About Terms Privacy Loading Error Warning The").split()}
_DNT_TOK_RE = re.compile(r"\b[A-Z][A-Za-z0-9]{2,}\b")
# leading structural markers that do NOT make a token mid-sentence: whitespace, opening quotes/brackets,
# and list bullets. A numbered marker ('1.') leaves a trailing '.' which the sentence-punct test treats
# as initial anyway, so digits/'.' are intentionally NOT stripped here (X3 auto_dnt position fix).
_DNT_LEAD_STRIP = " \t\r\n\"'`“”‘’«»「」『』()[]{}<>*+•·‧–—-"


def auto_dnt(texts):
    """F: pin recurring identifier-like tokens as DNT. A token qualifies only if it (a) recurs in >=2
    segments, (b) appears >=1x NOT sentence/segment-initial, and (c) is not a common-word stopword —
    so Title-case sentence starters like 'Open'/'The' are not mistaken for brands (LV-08)."""
    seg_count, noninitial = Counter(), set()
    for t in texts:
        t = t or ""
        seen = set()
        for m in _DNT_TOK_RE.finditer(t):
            seen.add(m.group(0))
            # strip leading quotes/brackets/list markers so '"Save' / '- Save' still count as initial
            prefix = t[:m.start()].rstrip(_DNT_LEAD_STRIP)
            if prefix and prefix[-1] not in ".!?…":     # not at a sentence/segment start
                noninitial.add(m.group(0))
        for tok in seen:
            seg_count[tok] += 1
    return {tok: tok for tok, n in seg_count.items()
            if n >= 2 and tok in noninitial and tok.lower() not in _DNT_STOP}


def _strip_nontranslatable(text):
    """Drop placeholder/markup/URL/mask/escaped-newline tokens so a Hangul-ratio measures translatable
    prose only — technical identifiers preserved from the source must not read as 'not Korean'."""
    text = _URL_RE.sub(" ", text or "")
    text = _SENTINEL_RE.sub(" ", text)
    for tok in _scan_braces(text):
        text = text.replace(tok, " ")
    text = _PRINTF_RE.sub(" ", text)
    text = _TAG_RE.sub(" ", text)
    return _NL_RE.sub(" ", text)


def josa_after_var(text):
    """True if a hardcoded single-form particle is glued directly onto a placeholder terminator (a
    brace close or printf spec). A render-time value can be consonant- or vowel-final, so the particle
    must be the dual form '{x}을(를)' / '{x}(으)로' / '{x}(이)나'. Accept ONLY the exact adjacent valid
    forms; flag single forms ('{x}을', '{x}이나', '{x}로'), malformed parenthesized markers ('(으로)',
    '(으) 로', '으(로)', '을(를'), and printf-glued particles across all specs (%1$s을). A following
    Hangul syllable means the 'particle' merely starts a word (로딩/나머지), not a postposition (RG-07/08)."""
    text = text or ""
    for m in _PH_TERM_RE.finditer(text):
        rest = text[m.end():]
        if not rest:
            continue
        if any(rest.startswith(f) for f in _JOSA_PAREN_FORMS):          # valid (으)로 / (이)나 … forms
            continue
        if any(rest.startswith(p + "(" + cp + ")") for p, cp in _JOSA_DUAL.items()):  # valid 을(를) …
            continue
        if rest[0] == "(" or rest.startswith("으("):    # malformed parenthesized marker ((으로), 으(로))
            return True
        ms = _JOSA_SINGLE_RE.match(rest)
        if ms:
            after = rest[ms.end():]
            if after[:1] and HANGUL_RE.match(after[:1]):   # word-initial syllable, not a postposition
                continue
            return True
    return False


# ── register (per-sentence terminal-ending classification) ──────────────────────────────────────
_REG_QUOTE_RE = re.compile(r"\"[^\"]*\"|'[^']*'|“[^”]*”|‘[^’]*’|「[^」]*」|『[^』]*』|`[^`]*`")
_REG_HAP = ("니다", "니까", "십시오")
_REG_OFF = {"hapsyo": {"haeyo", "handa"}, "haeyo": {"hapsyo", "handa"},
            "handa": {"hapsyo", "haeyo"}, "noun": {"hapsyo", "haeyo", "handa"}}


def _shield_for_register(text):
    text = _URL_RE.sub(" ", text or "")
    text = _REG_QUOTE_RE.sub(" ", text)                # quoted product/message literals
    for tok in _scan_braces(text):
        text = text.replace(tok, " ")
    text = _PRINTF_RE.sub(" ", text)
    return _TAG_RE.sub(" ", text)


def _sentence_register(sent):
    """Terminal-ending class of one sentence: hapsyo / haeyo / handa / None (noun-style, no ending)."""
    s = sent.strip()
    while s and s[-1] in " \t)]}\"'”’」』":               # drop trailing wrappers
        s = s[:-1]
    if not s:
        return None
    if s.endswith(_REG_HAP):                            # 니다/니까/십시오
        return "hapsyo"
    if s.endswith("요") or s.endswith("죠"):             # 해요체 allomorphs (돼요/눌러요/네요/죠/세요…)
        return "haeyo"
    if s.endswith("다"):                                 # 한다체 (니다 already handled above)
        return "handa"
    return None


def register_flag(output, register):
    """Per-sentence terminal-ending register check (RG-19/20/21/22, LV-04). Shields quoted/code/URL/
    placeholder spans so ending-like text inside a label ('하세요체') or a quoted product name is not
    counted; flags when any sentence's terminal ending is off the requested register (mixed included).
    Minor severity but FIXABLE (D1) — precision is now high."""
    if not register:
        return None
    shielded = _shield_for_register(output)
    classes = {c for c in (_sentence_register(s) for s in re.split(r"[.!?…\n]+", shielded)) if c}
    off = _REG_OFF[register] & classes
    if off:
        return {"type": "register_mismatch", "severity": "minor",
                "detail": f"requested {register}, found {'/'.join(sorted(off))}"}
    return None


def _nonbrace_tokens(text):
    """printf/%%, tag-shaped <…>, and literal \\n tokens — the non-ICU-wrapper placeholders that must
    survive verbatim in an ICU string (leaf text may be translated, but these may not vanish)."""
    text = text or ""
    return Counter(_PRINTF_RE.findall(text) + _TAG_RE.findall(text) + _NL_RE.findall(text))


def _brace_partition(text):
    """Split a string's top-level brace groups (from _scan_braces) into two multisets: ICU WRAPPERS
    keyed by their (var, keyword) head — parsed with the anchored _ICU_HEAD_RE — and PLAIN brace tokens
    (bare vars like '{count}', orphan closers, unbalanced remainders) keyed by exact string. Leaf content
    inside a wrapper is intentionally ignored here, so a translated leaf never changes a wrapper's key."""
    wrappers, plain = Counter(), Counter()
    for g in _scan_braces(text or ""):
        m = _ICU_HEAD_RE.match(g)
        if m:
            wrappers[(m.group(1), m.group(2))] += 1
        else:
            plain[g] += 1
    return wrappers, plain


def _icu_parity_mismatch(source, output):
    """ICU-aware parity (X1/X3/X5): the ICU-wrapper LEAVES may be translated, but the runtime skeleton
    must be preserved. Flag when (a) any non-brace token (printf/%%/tag/\\n) multiset differs, (b) the
    output braces are unbalanced (dropped/added brace), (c) the ICU-wrapper (var, keyword) multiset differs
    — exact string equality on the head var, so a rename (n→count, user→username) or a wrapper downgraded to
    a bare '{var}' is caught and a substring never passes — or (d) the PLAIN (non-wrapper) brace-token
    multiset differs (a dropped sibling, or a sibling replaced by a duplicate of another var). Leaf edits
    inside a matched wrapper touch neither multiset, so they stay clean."""
    if _nonbrace_tokens(source) != _nonbrace_tokens(output):          # (a) printf/tag/\n siblings survive
        return True
    if output.count("{") != output.count("}"):                        # (b) brace-balance guard (RG-04)
        return True
    src_w, src_p = _brace_partition(source)
    out_w, out_p = _brace_partition(output)
    return src_w != out_w or src_p != out_p                           # (c) wrappers 1:1 + (d) plain tokens


def postcheck(source, output, terms=None, register=None, category=None, mapping=None,
              mode=None, max_diff_ratio=None):
    flags = []
    if category is not None:                   # explicit legacy category path (wrapper-internal skip)
        if "icu" in category:                  # skip leaf parity, but a stray brace still shows up as
            if output.count("{") != output.count("}"):   # an unbalanced count (RG-04 caught)
                flags.append({"type": "placeholder_mismatch", "severity": "critical"})
        elif placeholders(source) != placeholders(output):
            flags.append({"type": "placeholder_mismatch", "severity": "critical"})
    elif _ICU_RE.search(source or ""):         # auto-detected ICU: structure-aware parity (NOT a skip)
        if _icu_parity_mismatch(source, output):
            flags.append({"type": "placeholder_mismatch", "severity": "critical"})
    elif placeholders(source) != placeholders(output):
        flags.append({"type": "placeholder_mismatch", "severity": "critical"})
    if josa_after_var(output):
        flags.append({"type": "josa_after_var", "severity": "major"})
    for type_, rx, sev, _fx in HEURISTICS:
        if rx.search(output):
            flags.append({"type": type_, "severity": sev})
    # term retention is checked against a copy with restored masked spans neutralized, so a DNT/glossary
    # token that only survives INSIDE a restored URL/code span does not falsely satisfy retention (X5).
    term_output = output
    for span in (mapping or []):
        if span:
            term_output = term_output.replace(span, " ")
    for src, tgt in (terms or {}).items():
        if src in (source or "") and tgt not in term_output:
            flags.append({"type": "dnt_violation" if tgt == src else "term_missing",
                          "severity": "major", "detail": f"{src}->{tgt}" if tgt != src else src})
    # foreign_script: only NEWLY introduced CJK/kana/cyrillic — source-preserved filenames / native
    # language labels (日本語, 中文) must not fire (RG-18).
    src_foreign = set(FOREIGN_SCRIPT_RE.findall(source or ""))
    if any(c not in src_foreign for c in FOREIGN_SCRIPT_RE.findall(output)):
        flags.append({"type": "foreign_script", "severity": "major"})
    # low_korean: ratio over translatable residue only — drop masks/URLs/placeholders/\n and Latin
    # tokens preserved from the source, so 'OAuth Client ID 복사' does not read as 'not Korean' (RG-17/LV-10).
    resid = _strip_nontranslatable(output)
    for tok in sorted(set(re.findall(r"[A-Za-z][A-Za-z0-9_]*", source or "")), key=len, reverse=True):
        resid = resid.replace(tok, " ")
    letters = len(LETTER_RE.findall(resid))
    if letters >= 8 and len(HANGUL_RE.findall(resid)) / letters < 0.3:
        flags.append({"type": "low_korean", "severity": "minor"})
    if output.strip() == (source or "").strip() and len(re.findall(r"[A-Za-z]+", source or "")) >= 3:
        flags.append({"type": "untranslated", "severity": "minor"})   # LV-07 wrong_clean class
    # quote parity (prose-safety): the quote-char multiset must match when the SOURCE has quotes. Critical in
    # polish (a dropped-dialogue guard — a full re-voicing deletes quotation marks), major otherwise. In
    # polish-WITH-masking the quotes are sentinel-masked and restored verbatim, so this never fires — it
    # guards --polish-dialogue and translate mode against dropped/added quotes.
    src_quotes = Counter(c for c in (source or "") if c in _QUOTE_CHARS)
    if src_quotes and src_quotes != Counter(c for c in (output or "") if c in _QUOTE_CHARS):
        flags.append({"type": "quote_mismatch", "severity": "critical" if mode == "polish" else "major"})
    # minimal-edit contract (prose-safety, polish only): flag a near-total rewrite. Advisory (major) — it
    # NEVER drives a --fix retry (D1: not in the fixable set) but counts for --strict. --max-diff-ratio caps
    # the allowed change fraction (default 0.6; use ~0.15 for manuscripts where polish must be bounded).
    if mode == "polish" and max_diff_ratio is not None:
        changed = 1 - difflib.SequenceMatcher(None, source or "", output or "").ratio()
        if changed > max_diff_ratio:
            flags.append({"type": "excessive_rewrite", "severity": "major",
                          "detail": f"{changed:.2f}>{max_diff_ratio}"})
    reg = register_flag(output, register)
    if reg:
        flags.append(reg)
    return flags


def auto_max_tokens(text, override):
    if override:
        return override
    n = len(text or "")
    # short tier 8192→4096: L4 showed 2048 sufficed for ≤200-char strings; the empty-retry doubling
    # covers the rare tail (Q6). Longer tiers unchanged.
    return 4096 if n <= 200 else 12000 if n <= 800 else 16384 if n <= 2000 else 24000


# ── prompt construction ─────────────────────────────────────────────────────────────────────────
_RULES = """규칙(반드시 지킬 것):
- 능동태를 쓴다. 피동 남용 금지(‘~에 의해’, ‘되어지다’ 금지).
- 불필요한 대명사를 지운다(‘당신/그/그녀/그것’).
- 불필요한 복수 ‘들’을 붙이지 않는다.
- 영어 ‘have’를 ‘가지다’로 직역하지 않는다 → ‘~에/에게 ~이(가) 있다’.
- 영어 ‘a/an’을 ‘하나의’로 옮기지 않는다.
- 접속은 연결어미(-고/-지만/-아서)로 한다. ‘그리고/그러나’ 남용 금지.
- 긴 문장은 끊는다.
- 한국어 관례: 숫자 1,234 · 날짜 2026년 6월 13일 · 통화 ₩1,000,000 · 줄임표 ‘…’ · 느낌표 자제."""
_CONCISE = "간결히 판단하고 곧바로 한국어 결과만 출력하라. 장황한 사고 과정을 길게 쓰지 말 것."
_HANGUL_ONLY = ("한글로만 출력하라(원문 보존이 필요한 라틴 문자 식별자·약어는 예외). 한자나 다른 언어 단어를 "
                "쓰지 말 것. 원문에 없는 사실·숫자·문장을 더하지 말 것.")
_TAIL = "출력: 한국어 결과 텍스트만 출력한다. 설명·따옴표·코드펜스·머리말 없이."
# register shield (prose-safety): quoted dialogue/quotations are NOT a target of the register/style rewrite.
_QUOTE_SHIELD = '따옴표("…", “…”, 「…」) 안의 대사·인용문은 문체 변경 대상이 아니다 — 원문 그대로 둔다.'
_REGISTER = {"haeyo": "문체: 해요체(사용자 안내·대화형 UI).", "hapsyo": "문체: 합쇼체(시스템 메시지·오류·격식).",
             "noun": "문체: 명사형 종결(버튼·라벨·메뉴). 마침표 없음.", "handa": "문체: 한다체(문어·도움말 본문)."}
_SYS = {"translate": "너는 영어를 한국어(ko-KR)로 옮기는 전문 번역가다. 원문의 의미를 보존하되, 번역투 없는 자연스러운 한국어 제품/UI 문구로 옮긴다.",
        "polish": "너는 어색하거나 번역투가 섞인 한국어를 의미 변화 없이 자연스러운 원어민 한국어로 다듬는 교정가다. 새 정보를 더하거나 빼지 않는다."}
# P2 few-shot (LV-14): 3 worked translate examples — placeholder+이중형 조사, printf 보존+수량 단위, 문체.
# The proven A/B win came from fewer josa-after-variable errors; the examples model the dual-form marker.
_FEWSHOT_TRANSLATE = ("예시(형식만 참고하고 실제 입력 의미에 맞게 번역하라):\n"
                      "- 입력(hapsyo): Move {file} to {folder}. → 출력: {file}을(를) {folder}(으)로 이동합니다.\n"
                      "- 입력(noun): %d new messages → 출력: 새 메시지 %d개\n"
                      "- 입력(haeyo): Delete {file}. → 출력: {file}을(를) 삭제해요.")
# Polish few-shot (LV-05/06): meaning-preservation + register application (don't turn 미래/피동 into 능동 삭제).
_FEWSHOT_POLISH = ("예시(의미는 그대로 두고 번역투만 다듬어라):\n"
                   "- 입력: 당신의 계정은 삭제되어질 것입니다. → 출력: 계정이 삭제될 예정입니다.\n"
                   "- 입력(hapsyo 요청): 파일을 저장했어요. → 출력: 파일을 저장했습니다.")
# Korean naturalness heuristics — compact prompt distillation. Polish gets the
# safety-first floor (≤6 short lines: minimal edits, protect negation/hedges, no comma-after-connective,
# don't change meaning strength). Translate gets the single mechanical comma rule (its other floor items
# already overlap the itemized _RULES). These steer the model toward the same floor the gate measures.
_FLOOR_POLISH = ("자연스러움 하한선(반드시 지킬 것):\n"
                 "- 한 번만 점검하고 각 수정은 문제를 없애는 최소 편집으로 할 것.\n"
                 "- 부정어·한정어·헤지는 절대 지우지 말 것(뜻이 바뀜).\n"
                 "- 연결어미 뒤에 쉼표를 찍지 말 것(빼면 오독될 때만 유지).\n"
                 "- 사실·수치·주장 강도는 바꾸지 말 것.")
_FLOOR_TRANSLATE = "연결어미(-고/-서/-며) 뒤에 쉼표를 찍지 않는다."


def build_system(mode, register, terms_subset, phs, has_mask):
    parts = [_SYS[mode]]
    if register:
        parts.append(_REGISTER.get(register, ""))
    parts += [_CONCISE, _RULES]
    parts.append(_FLOOR_TRANSLATE if mode == "translate" else _FLOOR_POLISH)   # naturalness-floor distillation
    if has_mask:
        parts.append(f"‘{MASK_OPEN}숫자{MASK_CLOSE}’ 형태의 토큰(예: {MASK_OPEN}0{MASK_CLOSE})은 코드·URL 자리표시자다. "
                     f"절대 번역·수정·삭제하지 말고 위치만 유지하라.")
    if phs:
        parts.append(f"이 입력의 서식 지정자·플레이스홀더 [{', '.join(sorted(set(phs)))}] 를 글자 그대로, 개수까지 똑같이 "
                     f"보존하라. 숫자·문자를 한국어 단어에 붙이거나 삭제하지 말 것.")
        parts.append("플레이스홀더 바로 뒤의 조사는 단정하지 말고 반드시 이중형으로 출력하라: ‘{name}을(를)’, ‘{file}(으)로’.")
    if terms_subset:
        pairs = "; ".join(f"‘{s}’→‘{t}’" + ("(원문 그대로 유지)" if s == t else "") for s, t in terms_subset.items())
        parts.append("다음 용어는 지정된 표기를 한 가지로만 쓰되 한국어 문맥에 맞게 활용(조사·어미)하라: " + pairs + ".")
    parts.append(_FEWSHOT_TRANSLATE if mode == "translate" else _FEWSHOT_POLISH)   # Q6/P2 worked examples
    parts.append(_HANGUL_ONLY)
    if register:
        parts.append(f"문서 끝까지 반드시 {REGISTER_NAME[register]}를 유지하라.")
        if mode == "polish":                            # register shield (prose-safety): belt for a
            parts.append(_QUOTE_SHIELD)                 # --polish-dialogue run where quotes aren't masked
    parts.append(_TAIL)
    return "\n".join(p for p in parts if p)


def fix_note(flags, phs, register):
    """C/D: build a span-targeted retry instruction from the actual flags."""
    seen, notes = set(), []
    for f in flags:
        t = f["type"]
        if t in seen:
            continue
        seen.add(t)
        if t == "placeholder_mismatch":
            notes.append(f"서식 지정자·플레이스홀더 [{', '.join(sorted(set(phs)))}]를 원문과 똑같이(개수까지) 보존하라.")
        elif t == "mask_integrity":
            notes.append(f"‘{MASK_OPEN}0{MASK_CLOSE}’ 형태의 자리표시자 토큰을 하나도 빠뜨리거나 중복하지 말고 원문 그대로 유지하라.")
        elif t == "josa_after_var":
            notes.append("플레이스홀더 바로 뒤 조사를 이중형으로 바꿔라: ‘{x}을(를)’, ‘{x}(으)로’.")
        elif t in ("dnt_violation", "term_missing"):
            notes.append(f"용어 ‘{f.get('detail','')}’를 지정대로 정확히 사용하라.")
        elif t == "register_mismatch":
            notes.append(f"문체를 {REGISTER_NAME.get(register, register)}로 통일하라.")
        elif t == "foreign_script":
            notes.append("한자·일본어·기타 외국어 문자를 한글로 바꿔라.")
        elif t in ("passive_eui", "double_passive"):   # ONLY the two FIXABLE passive types; advisory
            notes.append("피동 표현을 능동태로 바꿔라(‘~에 의해/의하여/의한’, ‘되어지다/열려지다’ 등 이중 피동 제거).")
            # translationese types (대명사/들/하나의/그리고) are advisory-only → never a fix instruction.
    return "[재시도 지시] 직전 출력의 다음 문제만 고쳐 한국어 결과만 다시 출력하라: " + " ".join(notes)


# ── HTTP (module-level so tests can monkeypatch) ──────────────────────────────────────────────────
def headers_for(endpoint):
    h = {"Content-Type": "application/json"}
    return h


def call_once(endpoint, system, text, model, api_key, max_tokens, temperature, timeout):
    body = {"model": model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": text}],
            "max_tokens": max_tokens, "temperature": temperature, "stream": False}
    headers = dict(headers_for(endpoint), Authorization=f"Bearer {api_key}")
    req = urllib.request.Request(endpoint, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    try:
        choice = payload["choices"][0]
        msg = choice.get("message", {}) or {}
        return ((msg.get("content") or "").strip(), (msg.get("reasoning_content") or "").strip(),
                choice.get("finish_reason"))
    except (KeyError, IndexError, TypeError):
        raise RuntimeError(f"unexpected gateway response shape: {json.dumps(payload)[:300]}")


def _result_score(r):
    """Lower is better: (#critical, #major, #flags). Lets keep-best prefer any non-broken output over
    an empty/broken one, and an earlier cleaner attempt over a worse retry (C1)."""
    f = r["flags"]
    return (sum(1 for x in f if x["severity"] == "critical"),
            sum(1 for x in f if x["severity"] == "major"), len(f))


def translate_one(text, o, resolved):
    """Mask → translate → unmask → gate → span-targeted fix. Returns {translation,status,flags,tries}.
    Keeps the BEST attempt by _result_score (not necessarily the last), so a retry that regresses never
    replaces a cleaner earlier output (C1)."""
    api_key, endpoint, model = resolved
    phs = list(placeholders(text).elements())
    mask_quotes = o["mode"] == "polish" and not o["polish_dialogue"]   # protect dialogue in polish
    masked, mapping = (mask_protected(text, mask_quotes=mask_quotes) if o["mask"] else (text, []))
    # terms computed from the MASKED source so a term appearing only inside a URL/code span is not
    # injected as a translatable obligation nor postchecked as missing (C1).
    terms_sub = segment_terms(masked, o["terms"])
    system = build_system(o["mode"], o["register"], terms_sub, phs, bool(mapping))
    fixable = {"placeholder_mismatch", "mask_integrity"}
    if o["fix"]:                # D1: advisory heuristics warn only; only high-precision types rewrite
        fixable |= {"josa_after_var", "dnt_violation", "term_missing", "register_mismatch", "foreign_script"} | FIXABLE_HEURISTICS
    explicit_max = bool(o["max_tokens"])   # an explicit --max-tokens is a hard cap, never doubled (C1)
    cur_max, tries, note = auto_max_tokens(text, o["max_tokens"]), 0, ""
    max_tries = max(1, o["max_retries"] + 1)
    best, best_score = None, None
    while tries < max_tries:
        tries += 1
        content, reasoning, finish = call_once(endpoint, system + note, masked, model, api_key,
                                               cur_max, o["temperature"], o["timeout"])
        if o["show_reasoning"] and reasoning:
            print(f"[reasoning]\n{reasoning}\n", file=sys.stderr)
        if not content:
            result = {"translation": "", "status": "empty", "tries": tries,
                      "flags": [{"type": "empty_content", "severity": "critical", "detail": f"finish_reason={finish}"}]}
            score = _result_score(result)
            if best is None or score < best_score:
                best, best_score = result, score
            if tries < max_tries:
                if not explicit_max:                     # only auto-sized requests grow after empty
                    cur_max = min(cur_max * 2, MAX_TOKENS_CAP)
                continue
            break
        out, mask_ok = unmask_verified(content, mapping)
        flags = postcheck(text, out, terms_sub, o["register"], mapping=mapping,
                          mode=o["mode"], max_diff_ratio=o["max_diff_ratio"])
        if not mask_ok:   # dropped/duplicated/residual/hallucinated sentinel corrupts protected spans (RG-01)
            flags.insert(0, {"type": "mask_integrity", "severity": "critical", "detail": "sentinel drift"})
        crit = [f for f in flags if f["severity"] == "critical"]
        result = {"translation": out, "status": "broken" if crit else ("flagged" if flags else "clean"),
                  "flags": flags, "tries": tries}
        score = _result_score(result)
        if best is None or score < best_score:
            best, best_score = result, score
        fix_flags = [f for f in flags if f["type"] in fixable]
        if fix_flags and tries < max_tries:              # advisory flags warn but never drive a retry
            note = "\n" + fix_note(fix_flags, phs, o["register"])
            continue
        break
    return dict(best, tries=tries)                       # report total attempts made


# ── document mode (E) ─────────────────────────────────────────────────────────────────────────
_FM_OPEN_RE = re.compile(r"^---\s*$")                    # YAML front matter opener (doc start only)
_FM_CLOSE_RE = re.compile(r"^(?:---|\.\.\.)\s*$")
_SETEXT_RE = re.compile(r"^ {0,3}[=-]{3,}\s*$")          # setext underline under a prose paragraph (RG-36)
# standalone thematic break (---, ___, ***): 3+ of one char, optional spaces, ≤3 leading spaces (RG-hr)
_HR_RE = re.compile(r"^ {0,3}(?:-[ \t]*){3,}$|^ {0,3}(?:_[ \t]*){3,}$|^ {0,3}(?:\*[ \t]*){3,}$")
_HTML_BLOCK_RE = re.compile(r"^\s*<[A-Za-z/!]")          # HTML block line (RG-37)
_BLOCKQUOTE_RE = re.compile(r"^(\s*>+\s?)(.*)$")         # blockquote prefix, like a list marker (RG-33)
_ATX_RE = re.compile(r"(#{1,6}\s+)(.*)")
_LIST_RE = re.compile(r"(\s*(?:[-*+]|\d+\.)\s+)(.*)")
_INDENT_CODE_RE = re.compile(r"^(?: {4}|\t)")            # 4-space / tab indented code (RG-29)


def _is_table_line(line):
    return line.lstrip().startswith("|") or line.count(" | ") >= 2   # pipe-table row (RG-32)


def split_doc(text):
    """Split markdown into ordered blocks: verbatim (structure kept byte-for-byte) vs prose (translatable
    text + a structural prefix restored on reassembly). Verbatim: fenced code (shared scan_fences), YAML
    front matter, indented code, pipe tables, setext underlines, HTML-block lines, blanks. Prose prefixes:
    ATX headings, list markers, blockquote markers. CRLF is normalized to LF at ingest (RG-35). List-
    continuation reflow and per-cell table translation are PARKED (RG-34)."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")   # CRLF/CR → LF at ingest (RG-35)
    lines = text.split("\n")
    n = len(lines)
    fence_lines = {k for s, e in scan_fences(text) for k in range(s, e + 1)}
    blocks, buf, i = [], [], 0

    def flush():
        if buf:
            blocks.append({"type": "prose", "prefix": "", "text": "\n".join(buf)})
            buf.clear()

    # YAML front matter: only a leading '---' that has a matching closing '---'/'...' (RG-30).
    if n and _FM_OPEN_RE.match(lines[0]):
        j = 1
        while j < n and not _FM_CLOSE_RE.match(lines[j]):
            j += 1
        if j < n:                                        # closer found → real front matter, all verbatim
            blocks += [{"type": "verbatim", "text": lines[k]} for k in range(0, j + 1)]
            i = j + 1

    while i < n:
        line = lines[i]
        if i in fence_lines:                             # inside a fenced block → verbatim (any fence char)
            flush(); blocks.append({"type": "verbatim", "text": line}); i += 1; continue
        if not line.strip():                             # blank line
            flush(); blocks.append({"type": "verbatim", "text": line}); i += 1; continue
        if _is_table_line(line):                         # pipe table row → verbatim (RG-32)
            flush(); blocks.append({"type": "verbatim", "text": line, "kind": "table"}); i += 1; continue
        # indented code: only when starting a fresh block (doc start / after blank / continuing code),
        # NOT a paragraph's wrapped continuation line (RG-29; list reflow PARKED RG-34)
        if _INDENT_CODE_RE.match(line) and not buf and (i == 0 or not lines[i - 1].strip()
                                                        or _INDENT_CODE_RE.match(lines[i - 1])):
            flush(); blocks.append({"type": "verbatim", "text": line}); i += 1; continue
        if _HTML_BLOCK_RE.match(line):                   # HTML-block line → verbatim (RG-37)
            flush(); blocks.append({"type": "verbatim", "text": line}); i += 1; continue
        if buf and _SETEXT_RE.match(line):               # setext underline under buffered prose (RG-36)
            flush(); blocks.append({"type": "verbatim", "text": line}); i += 1; continue
        if _HR_RE.match(line):                           # standalone thematic break (---/___/***) → verbatim
            flush(); blocks.append({"type": "verbatim", "text": line}); i += 1; continue
        mh, mq, ml = _ATX_RE.match(line), _BLOCKQUOTE_RE.match(line), _LIST_RE.match(line)
        if mh:
            flush(); blocks.append({"type": "prose", "prefix": mh.group(1), "text": mh.group(2)}); i += 1; continue
        if mq:
            flush(); blocks.append({"type": "prose", "prefix": mq.group(1), "text": mq.group(2)}); i += 1; continue
        if ml:
            flush(); blocks.append({"type": "prose", "prefix": ml.group(1), "text": ml.group(2)}); i += 1; continue
        buf.append(line); i += 1
    flush()
    return blocks


def reassemble_doc(blocks, translations):
    out, ti = [], 0
    for b in blocks:
        if b["type"] == "verbatim":
            out.append(b["text"])
        else:
            out.append(b["prefix"] + translations[ti]); ti += 1
    return "\n".join(out)


# ── CLI ───────────────────────────────────────────────────────────────────────────────────────
def load_config():
    cfg = {}
    for name in ("config.json", "config.local.json"):
        path = os.path.join(HERE, name)
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    cfg.update({k: v for k, v in json.load(fh).items() if v})
            except (OSError, ValueError) as exc:
                print(f"translate.py: warning: could not read {name}: {exc}", file=sys.stderr)
    return cfg


def read_key_file():
    path = os.path.join(HERE, ".api_key")
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                return fh.read().strip() or None
        except OSError as exc:
            print(f"translate.py: warning: could not read .api_key: {exc}", file=sys.stderr)
    return None


def resolve(model_override, cfg):
    # api_key comes ONLY from env or ./.api_key — never a config file (C2: match the documented policy;
    # a bearer token must not live in a committed config.json).
    api_key = os.environ.get("LLM_API_KEY") or read_key_file()
    endpoint = (os.environ.get("LLM_ENDPOINT") or cfg.get("endpoint")
                or (cfg["base_url"].rstrip("/") + "/chat/completions" if cfg.get("base_url") else None)
                or DEFAULT_ENDPOINT)
    model = model_override or os.environ.get("LLM_MODEL") or cfg.get("model") or DEFAULT_MODEL
    return api_key, endpoint, model


def fmt_flags(flags):
    return "; ".join(f"[{f.get('severity','?')}] {f['type']}" + (f":{f['detail']}" if f.get("detail") else "") for f in flags)


def preview_request(text, o, model, endpoint, api_key):
    """Build the exact request that WOULD be sent for `text` (key redacted) — shared by every --dry-run
    path so a preview matches the real per-string/per-block/per-row call (C1)."""
    phs = list(placeholders(text).elements())
    mask_quotes = o["mode"] == "polish" and not o["polish_dialogue"]
    masked, mapping = (mask_protected(text, mask_quotes=mask_quotes) if o["mask"] else (text, []))
    body = {"model": model, "max_tokens": auto_max_tokens(text, o["max_tokens"]), "temperature": o["temperature"],
            "messages": [{"role": "system",
                          "content": build_system(o["mode"], o["register"], segment_terms(masked, o["terms"]), phs, bool(mapping))},
                         {"role": "user", "content": masked}]}
    headers = dict(headers_for(endpoint), Authorization=("Bearer ****" if api_key else "Bearer <MISSING>"))
    return {"url": endpoint, "headers": headers, "body": body}


def run_doc(text, o, resolved):
    """Markdown doc mode (E). D2 policy: a broken/empty/error prose block keeps its SOURCE text (never a
    blank or a rejected translation on stdout), stderr explains, exit 1 on any such block; --strict exits
    2 on ANY remaining flag; --dry-run previews the first prose block + the block plan and makes no call."""
    api_key, endpoint, model = resolved
    had_crlf = "\r\n" in (text or "")
    blocks = split_doc(text)
    prose = [b for b in blocks if b["type"] == "prose"]

    if o["dry_run"]:
        plan = Counter(("table" if b.get("kind") == "table" else b["type"]) for b in blocks)
        preview = preview_request(prose[0]["text"], o, model, endpoint, api_key) if prose else None
        print(json.dumps({"block_plan": dict(plan), "prose_blocks": len(prose),
                          "first_prose_request": preview}, ensure_ascii=False, indent=2))
        print(f"translate.py: dry-run — {len(prose)} prose blocks, no calls made", file=sys.stderr)
        return 0

    def work(b):
        try:
            return translate_one(b["text"], o, resolved)
        except Exception as exc:                          # per-block exception contained (D2)
            return {"translation": "", "status": "error", "tries": 0,
                    "flags": [{"type": "error", "severity": "critical", "detail": str(exc)[:200]}]}
    with ThreadPoolExecutor(max_workers=max(1, o["concurrency"])) as ex:
        results = list(ex.map(work, prose))

    outs, kept = [], 0
    for b, r in zip(prose, results):
        if r["status"] in ("empty", "broken", "error"):   # D2: keep the SOURCE for a block we can't trust
            outs.append(b["text"]); kept += 1
        else:
            outs.append(r["translation"])
    print(reassemble_doc(blocks, outs))

    flags = Counter(f["type"] for r in results for f in r["flags"])
    summary = ("flags: " + ", ".join(f"{k}={v}" for k, v in flags.most_common())) if flags else "clean"
    kept_note = f", {kept} block(s) kept untranslated (broken/empty/error)" if kept else ""
    print(f"translate.py: doc — {len(prose)} prose blocks, {summary}{kept_note}", file=sys.stderr)
    if any(b.get("kind") == "table" for b in blocks):
        print("translate.py: doc — pipe tables kept untranslated (verbatim); translate cells by hand", file=sys.stderr)
    if had_crlf:
        print("translate.py: doc — input had CRLF line endings; output normalized to LF", file=sys.stderr)
    if o["strict"] and flags:
        return 2
    return 1 if kept else 0


def _read_jsonl(path):
    """Return (rows, error_rc). Missing file or a malformed line → clear message + exit-2 signal."""
    rows = []
    try:
        with open(path, encoding="utf-8") as fh:
            for ln, line in enumerate(fh, 1):
                if line.strip():
                    try:
                        rows.append(json.loads(line))
                    except ValueError as exc:
                        print(f"translate.py: {path}:{ln}: malformed JSONL line: {exc}", file=sys.stderr)
                        return None, 2
    except OSError as exc:
        print(f"translate.py: cannot read --in {path}: {exc}", file=sys.stderr)
        return None, 2
    return rows, 0


def run_batch(o, resolved):
    """Bulk JSONL. Guarded input read; per-row option build + enum validation INSIDE the try (a bad row
    → status:error, never a crash); --dry-run previews every row and writes nothing; --strict exits 2 on
    any flagged row; concurrency floored at 1."""
    api_key, endpoint, model = resolved
    rows, rc = _read_jsonl(o["in_path"])
    if rows is None:
        return rc
    base_terms = o["terms"]
    if o["auto_dnt"]:  # explicit terms win over auto-detected; non-dict rows contribute no DNT text
        texts = [(r.get("source_en") or r.get("text") or "") if isinstance(r, dict) else "" for r in rows]
        base_terms = {**auto_dnt(texts), **o["terms"]}

    def row_options(row):
        if not isinstance(row, dict):
            raise ValueError("row must be a JSON object")
        mode = row.get("mode", o["mode"])
        if mode not in ("translate", "polish"):
            raise ValueError(f"invalid mode {mode!r} (expected translate|polish)")
        register = row.get("register", o["register"])
        if register is not None and register not in _REGISTER:
            raise ValueError(f"invalid register {register!r} (expected {'|'.join(_REGISTER)})")
        rdnt = row.get("dnt") or []
        if not isinstance(rdnt, list):
            raise ValueError("'dnt' must be a list of strings")
        rgloss = row.get("glossary") or {}
        if not isinstance(rgloss, dict):
            raise ValueError("'glossary' must be an object")
        return dict(o, mode=mode, register=register,
                    terms={**base_terms, **load_terms(",".join(str(x) for x in rdnt), None),
                           **{str(k): str(v) for k, v in rgloss.items()}})

    if o["dry_run"]:
        out = []
        for row in rows:
            rid = row.get("id") if isinstance(row, dict) else None
            try:                                          # all row.get access guarded (malformed row → error)
                ro = row_options(row)
                text = row.get("source_en") or row.get("text") or ""
                out.append({"id": rid, **preview_request(text, ro, model, endpoint, api_key)})
            except Exception as exc:
                out.append({"id": rid, "error": str(exc)[:200]})
        print(json.dumps(out, ensure_ascii=False, indent=2))
        print(f"translate.py: dry-run — {len(rows)} rows previewed, no calls, {o['out_path']} not written", file=sys.stderr)
        return 0

    def work(row):
        rid = row.get("id") if isinstance(row, dict) else None
        try:                                              # option build + translate both inside the try
            ro = row_options(row)                          # raises on a non-dict / invalid row
            text = row.get("source_en") or row.get("text") or ""
            r = translate_one(text, ro, resolved)
        except Exception as exc:
            r = {"translation": "", "status": "error", "tries": 0,
                 "flags": [{"type": "error", "severity": "critical", "detail": str(exc)[:200]}]}
        return {"id": rid, "translation": r["translation"], "status": r["status"], "tries": r["tries"], "flags": r["flags"]}

    with ThreadPoolExecutor(max_workers=max(1, o["concurrency"])) as ex:
        results = list(ex.map(work, rows))
    with open(o["out_path"], "w", encoding="utf-8") as fh:
        for r in results:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    by_status, by_flag = Counter(r["status"] for r in results), Counter()
    for r in results:
        by_flag.update(f["type"] for f in r["flags"])
    print(f"translate.py: {len(results)} rows -> {o['out_path']}", file=sys.stderr)
    print("  status: " + ", ".join(f"{k}={v}" for k, v in sorted(by_status.items())), file=sys.stderr)
    if by_flag:
        print("  flags : " + ", ".join(f"{k}={v}" for k, v in by_flag.most_common()), file=sys.stderr)
    if o["strict"] and any(r["flags"] for r in results):
        return 2
    return 1 if any(by_status.get(s, 0) for s in ("empty", "broken", "error")) else 0


def main():
    p = argparse.ArgumentParser(description="Korean translation + enforcement via a configurable chat-completions endpoint.")
    p.add_argument("text", nargs="?")
    p.add_argument("--mode", choices=("translate", "polish"), default="translate")
    p.add_argument("--register", choices=tuple(_REGISTER))
    p.add_argument("--polish-dialogue", action="store_true",
                   help="polish mode: also polish quoted dialogue/quotations (default: protect verbatim)")
    p.add_argument("--max-diff-ratio", type=float, default=0.6,
                   help="polish mode: advisory excessive_rewrite flag when the changed fraction exceeds this "
                        "(default 0.6; use ~0.15 for manuscripts)")
    p.add_argument("--dnt", default="", help="comma-separated do-not-translate terms")
    p.add_argument("--glossary", default="", help="'src=tgt,...' or a .tsv/.json file")
    p.add_argument("--fix", action="store_true", help="escalate: span-targeted retry on major flags too")
    p.add_argument("--no-mask", dest="mask", action="store_false", help="disable code/URL masking")
    p.add_argument("--doc", action="store_true", help="markdown doc mode (preserve structure)")
    p.add_argument("--strict", action="store_true", help="exit non-zero on ANY remaining flag")
    p.add_argument("--model")
    p.add_argument("--max-tokens", type=int, default=0, help="0 = auto-scale by length")
    p.add_argument("--max-retries", type=int, default=2)
    p.add_argument("--temperature", type=float, default=0.2)
    p.add_argument("--timeout", type=float, default=120.0)
    p.add_argument("--concurrency", type=int, default=8)
    p.add_argument("--in", dest="in_path", help="JSONL: {id, source_en|text, register?, dnt?, glossary?, mode?}")
    p.add_argument("--out", dest="out_path")
    p.add_argument("--auto-dnt", action="store_true", help="batch: pin recurring brand/identifier tokens")
    p.add_argument("--show-reasoning", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()

    o = dict(mode=a.mode, register=a.register, terms=load_terms(a.dnt, a.glossary), fix=a.fix, mask=a.mask,
             max_tokens=a.max_tokens, max_retries=a.max_retries, temperature=a.temperature, timeout=a.timeout,
             show_reasoning=a.show_reasoning, concurrency=a.concurrency, in_path=a.in_path, out_path=a.out_path,
             auto_dnt=a.auto_dnt, strict=a.strict, dry_run=a.dry_run,
             polish_dialogue=a.polish_dialogue, max_diff_ratio=a.max_diff_ratio)
    api_key, endpoint, model = resolve(a.model, load_config())
    resolved = (api_key, endpoint, model)

    def guard_credentials():        # a real call requires explicit destination, model, and HTTPS
        if not endpoint:
            p.error("no endpoint. Set LLM_ENDPOINT or endpoint in config.local.json.")
        if not model:
            p.error("no model. Set LLM_MODEL, --model, or model in config.local.json.")
        if not api_key:
            p.error("no API key. Set LLM_API_KEY or put the key in ./.api_key.")
        if not endpoint.startswith("https://"):
            p.error(f"refusing to send a credential over non-HTTPS endpoint: {endpoint}")

    if a.in_path:
        if not a.out_path:
            p.error("--in requires --out")
        if not a.dry_run:           # HTTPS/key guard runs BEFORE batch (dry-run makes no call)
            guard_credentials()
        sys.exit(run_batch(o, resolved))

    text = a.text if a.text is not None else sys.stdin.read()
    text = text if a.doc else (text or "").strip()
    if not text.strip():
        p.error("no input text (argument or stdin)")

    if not a.dry_run:
        guard_credentials()
    if a.doc:                       # run_doc honors o['dry_run']/o['strict'] internally
        sys.exit(run_doc(text, o, resolved))
    if a.dry_run:
        print(json.dumps(preview_request(text, o, model, endpoint, api_key), ensure_ascii=False, indent=2))
        return

    try:
        res = translate_one(text.strip(), o, resolved)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300] if hasattr(exc, "read") else ""
        sys.exit(f"translate.py: gateway HTTP {exc.code}: {detail}")
    except (urllib.error.URLError, RuntimeError, TimeoutError, ValueError) as exc:
        sys.exit(f"translate.py: gateway call failed: {exc}")

    if res["flags"]:
        print(f"translate.py: {res['status']} ({res['tries']} tr) — {fmt_flags(res['flags'])}", file=sys.stderr)
    if res["status"] in ("empty", "broken"):
        sys.exit(f"translate.py: refusing to emit ({res['status']}): {fmt_flags(res['flags'])}")
    print(res["translation"])
    if a.strict and res["flags"]:
        sys.exit(2)


if __name__ == "__main__":
    main()
