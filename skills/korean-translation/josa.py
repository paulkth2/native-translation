"""
josa.py — runtime Korean particle (조사) selection for text following a variable.

Dependency-free. Resolves the correct allomorph of a Korean particle based on the
final phoneme (batchim / 받침) of the preceding word, including:
  - the ㄹ special case for the (으)로 family,
  - Sino-Korean number readings (1->일->ㄹ, 10->십->ㅂ, 16->십육->ㄱ, ...),
  - Latin-letter readings (L->엘->ㄹ, M->엠->ㅁ, B->비->vowel, ...).

Use at the RENDER layer. Never hardcode a particle right after a {placeholder}.

    >>> attach("메뉴", "을/를")
    '메뉴를'
    >>> attach("서울", "으로")     # ㄹ exception
    '서울로'
    >>> attach("부산", "으로")
    '부산으로'
    >>> attach("김민준", "이/가")
    '김민준이'
    >>> attach("Slack", "은/는")   # k -> 케이? no: last char 'k' name '케이' -> vowel... see note
    'Slack은'
    >>> attach("파일 10", "으로")   # 10 -> 십 -> ㅂ
    '파일 10으로'
    >>> select("서울", "로")        # just the particle
    '로'

Supported particle keys (pass either allomorph or the canonical slash form):
    을/를  이/가  은/는  와/과  (으)로 / 으로 / 로  (이)나 / 이나  (이)란  (이)랑  (이)에요
"""

from __future__ import annotations

import unicodedata

HANGUL_BASE = 0xAC00
HANGUL_END = 0xD7A3
JONG_COUNT = 28
JONG_RIEUL = 8  # index of ㄹ in the jongseong table (single ㄹ only)

# --- Sino-Korean number reading (final syllable is what matters) ---------------
_SINO_DIGITS = ["", "일", "이", "삼", "사", "오", "육", "칠", "팔", "구"]
_SINO_SMALL = ["", "십", "백", "천"]          # 10^0..10^3 within a 4-digit group
_SINO_BIG = ["", "만", "억", "조", "경", "해"]  # 10^4 groups


def _sino_group(g: int) -> str:
    """Reading of a 0..9999 group (no big-unit suffix)."""
    out = ""
    for unit in (3, 2, 1, 0):  # 천, 백, 십, 일
        d = (g // (10 ** unit)) % 10
        if not d:
            continue
        if d == 1 and unit >= 1:   # Korean omits 일 before 십/백/천 (10 = 십, not 일십)
            out += _SINO_SMALL[unit]
        else:
            out += _SINO_DIGITS[d] + _SINO_SMALL[unit]
    return out


def sino_korean(n: int) -> str:
    """Sino-Korean reading of a non-negative integer (enough for UI quantities)."""
    if n == 0:
        return "영"
    parts = []
    gi = 0
    while n > 0 and gi < len(_SINO_BIG):
        g = n % 10000
        if g:
            parts.append(_sino_group(g) + _SINO_BIG[gi])
        n //= 10000
        gi += 1
    return "".join(reversed(parts))


# --- Latin letter readings: (has_batchim, is_rieul) ----------------------------
# Based on Korean letter names; only L and R end in ㄹ. Heuristic for Z (지 -> vowel).
# F/S/X read 에프/에스/엑스 — vowel-final, NOT batchim (contra a naive letter->consonant mapping).
_LETTER = {
    "a": (False, False), "b": (False, False), "c": (False, False), "d": (False, False),
    "e": (False, False), "f": (False, False), "g": (False, False), "h": (False, False),
    "i": (False, False), "j": (False, False), "k": (False, False), "l": (True, True),
    "m": (True, False),  "n": (True, False),  "o": (False, False), "p": (False, False),
    "q": (False, False), "r": (True, True),   "s": (False, False), "t": (False, False),
    "u": (False, False), "v": (False, False), "w": (False, False), "x": (False, False),
    "y": (False, False), "z": (False, False),
}

# trailing chars ignored when finding the final phoneme: whitespace, closing wrappers,
# and terminal sentence punctuation (. , ! ? … : ;). rstrip only touches the trailing run,
# so a comma between digits (1,024) is kept for the numeric scan; a trailing one is dropped.
_TRAIL = " \t\n\r\f\v)]}>'\"”’」』.,!?…:;，"
_GROUPING = (",", "，")  # ASCII + full-width (U+FF0C) thousands separators


def _final_phoneme(word: str):
    """
    Inspect the trailing token of `word` and return (has_batchim, is_rieul).
    Handles: Hangul syllable, a trailing integer (incl. commas), a Latin letter.
    Falls back to scanning backward past closing punctuation.
    """
    s = word.rstrip(_TRAIL)  # ignore trailing whitespace/closers/sentence punctuation
    if not s:
        return (False, False)

    # trailing integer (e.g. "파일 1,024" or full-width "１，０００") -> Sino reading.
    # isdecimal() gates int() so digit-LIKE glyphs (②/²/₂ pass isdigit but not int) never crash.
    j = len(s)
    while j > 0 and (s[j - 1].isdecimal() or s[j - 1] in _GROUPING):
        j -= 1
    if j < len(s) and any(ch.isdecimal() for ch in s[j:]):
        raw = s[j:]
        for g in _GROUPING:
            raw = raw.replace(g, "")
        try:
            num = int(unicodedata.normalize("NFKC", raw))  # NFKC folds full-width digits to ASCII
        except ValueError:
            num = None
        if num is not None:
            return _final_phoneme(sino_korean(num))  # recurse on the Hangul reading

    last = s[-1]
    code = ord(last)
    if HANGUL_BASE <= code <= HANGUL_END:
        jong = (code - HANGUL_BASE) % JONG_COUNT
        return (jong != 0, jong == JONG_RIEUL)
    if last.isalpha() and last.lower() in _LETTER:
        return _LETTER[last.lower()]
    # digit handled above; anything else (symbol/space) -> treat as vowel-final (safe default)
    return (False, False)


# --- particle resolution -------------------------------------------------------
# canonical_key -> (vowel_form, consonant_form, rieul_uses_vowel_form)
_PAIRS = {
    "을/를": ("를", "을", False),
    "이/가": ("가", "이", False),
    "은/는": ("는", "은", False),
    "와/과": ("와", "과", False),
    "(으)로": ("로", "으로", True),   # ㄹ-final -> 로 (vowel form)
    "(이)나": ("나", "이나", False),
    "(이)란": ("란", "이란", False),
    "(이)랑": ("랑", "이랑", False),
    "(이)에요": ("예요", "이에요", False),
}

# map every allomorph / shorthand to its canonical key
_ALIAS = {}
for _k, _pair in _PAIRS.items():
    for _form in (_k, _pair[0], _pair[1]):
        _ALIAS[_form] = _k
_ALIAS.update({"로": "(으)로", "으로": "(으)로", "이나": "(이)나", "나": "(이)나"})


def select(word: str, particle: str) -> str:
    """Return the correct allomorph of `particle` for the word preceding it."""
    key = _ALIAS.get(particle.strip())
    if key is None:
        raise ValueError(f"Unsupported particle: {particle!r}. "
                         f"Use one of {sorted(_PAIRS)} or an allomorph.")
    vowel_form, cons_form, rieul_is_vowel = _PAIRS[key]
    has_batchim, is_rieul = _final_phoneme(word)
    if not has_batchim:
        return vowel_form
    if is_rieul and rieul_is_vowel:
        return vowel_form
    return cons_form


def attach(word: str, particle: str) -> str:
    """Return `word` + the correct particle, e.g. attach('메뉴','을/를') -> '메뉴를'.
    Empty/whitespace-only word has no phoneme to inflect against, so return it unchanged
    with no particle (never emit an orphan '를')."""
    if not word.strip():
        return word
    return f"{word}{select(word, particle)}"


def fill(template: str, **values) -> str:
    """
    Render a template using the dual-form convention to mark josa.
    Write the placeholder immediately followed by a slash-form particle in brackets:

        fill("{name}[을/를] 삭제했습니다.", name="메뉴")   -> "메뉴를 삭제했습니다."
        fill("{city}[(으)로] 이동", city="서울")          -> "서울로 이동"

    Any '{var}[particle]' is replaced by value + the resolved particle.
    """
    import re

    def _sub(m):
        var, part = m.group(1), m.group(2)
        # only treat '{var}[payload]' as a josa marker when payload is a real particle;
        # literal bracket text (e.g. '{code}[beta]') is left for the plain-{var} pass below.
        if var not in values or _ALIAS.get(part.strip()) is None:
            return m.group(0)
        return attach(str(values[var]), part)

    out = re.sub(r"\{(\w+)\}\[([^\]]+)\]", _sub, template)
    # fill any remaining plain {var}
    for k, v in values.items():
        out = out.replace("{" + k + "}", str(v))
    return out


if __name__ == "__main__":
    # quick self-test
    cases = [
        ("메뉴", "을/를", "메뉴를"),
        ("파일", "을/를", "파일을"),
        ("서울", "으로", "서울로"),      # ㄹ -> 로
        ("부산", "으로", "부산으로"),
        ("서울", "을/를", "서울을"),      # ㄹ ordinary for 을/를
        ("김민준", "이/가", "김민준이"),
        ("우유", "이/가", "우유가"),
        ("10", "으로", "10으로"),        # 십 -> ㅂ
        ("1", "으로", "1로"),            # 일 -> ㄹ
        ("16", "을/를", "16을"),         # 십육 -> 육 -> ㄱ
        ("L", "을/를", "L을"),           # 엘 -> ㄹ (consonant for 을/를)
        ("B", "을/를", "B를"),           # 비 -> vowel
    ]
    ok = 0
    for word, part, expect in cases:
        got = attach(word, part)
        flag = "ok" if got == expect else "FAIL"
        if got == expect:
            ok += 1
        print(f"[{flag}] attach({word!r}, {part!r}) = {got!r}  (expect {expect!r})")
    print(f"\n{ok}/{len(cases)} passed")
    print(fill("{name}[을/를] {n}[(으)로] 옮겼습니다.", name="서울", n=3))
