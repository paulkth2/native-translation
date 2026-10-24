# Register & Korean UI conventions

## Register / speech level (문체)

Korean has multiple speech levels. For product UI, only three matter. **Pick by surface type and never mix within a product.**

| Surface | Register | Pattern | Examples |
|---|---|---|---|
| Buttons, labels, menu, titles, column headers | **Noun-style (명사형)** — no sentence ending, no period | noun / verbal-noun + 하기 | Save→**저장**, Delete→**삭제**, Sign in→**로그인**, Share→**공유하기**, Settings→**설정** |
| Prompts directed at the user (instructions, CTAs, confirmations) | **해요체** (polite, friendly) | ~하세요 / ~할까요? / ~하시겠어요? | "Try again"→**다시 시도하세요**, "Continue?"→**계속하시겠어요?** |
| System statements, status, errors, success | **합쇼체** (polite, formal) | ~합니다 / ~됩니다 / ~했습니다 | "Saved."→**저장되었습니다.**, "Update failed."→**업데이트하지 못했습니다.** |
| Reference/technical docs (optional, doc-wide) | **한다체 / 평서체** | ~한다 / ~이다 | "The function returns null."→**함수는 null을 반환한다.** |

Vendor anchors: **Microsoft** splits 해요체 (user prompts) vs 합쇼체 (system/errors), uses `~하세요` not `~하십시오` (reserve `-십시오` for legal/EULA). **Apple** is more formal (합쇼체 + ~하십시오). **Mozilla** uses 합쇼체, non-imperative. **Toss** (consumer) uses 해요체 everywhere. → choose one house style and document it.

### Label vs message phrasing
- **Labels/buttons**: shortest natural noun form, **no period**. "Update failed" as a *title* → **업데이트 실패** (fragment), not a sentence.
- **Messages/errors**: full sentence in 합쇼체 **with period**. Standardize error verbs:
  - "Cannot / Could not …" → **~할 수 없습니다**
  - "Failed to …" → **~하지 못했습니다**
  - "An error occurred while …" → **~하는 동안 오류가 발생했습니다**
- **Period rule**: if the Korean ends in ~하세요/~합니다, add a period even when the English ended with a colon. Pure noun fragments take no period.

## Plurals & counters

**Korean CLDR plural category = `other` only.** There is no singular/plural distinction.
- ICU: write **only the `other` arm**. `{count, plural, other {파일 #개}}`. Never author `one`/`other` pairs — English `one {# file} other {# files}` collapses to one Korean form.
- Quantity = **noun + number + counter (분류사)**, not a plural suffix:

| English | Korean | Counter |
|---|---|---|
| 3 files | 파일 3개 | 개 (general things) |
| 2 users | 사용자 2명 | 명 (people; 분 = honorific) |
| 5 attempts | 5번 | 번 (times) |
| 2 pages | 2장 | 장 (sheets/pages) |

With a digit placeholder, `{n}개` reads naturally — but pick the right counter for people (명) / times (번) when the noun is known; generic 개 reads oddly for those.

## Numbers, dates, currency, units (ko-KR)
- **Numbers**: period decimal, comma grouping — `1,234,567.89`.
- **Dates**: Y→M→D — `2026년 6월 13일`; numeric `2026. 6. 13.` (trailing period) or ISO `2026-06-13`. Time: `오전/오후` before it.
- **Currency (KRW)**: `₩1,000,000` (symbol before) or `1,000,000원` (word after); no decimals normally.
- Prefer ICU skeletons (`::currency/KRW`, `date, long`) over hardcoding.

## Punctuation & orthography
- **Avoid `!`** (reads as shouting). Use `…` (U+2026) for progress: **저장하는 중…**.
- **Don't abbreviate Korean** — no "설" for "설정". Keep Latin acronyms in English (PDF, URL, API); format unfamiliar ones as "English (한국어 풀이)".
- **외래어 표기법**: follow 국립국어원 standard transliteration; keep well-known loanwords (컴퓨터, 다운로드). "download" as a verb → **다운로드하다** / **내려받다**, never "다운로드 받다".
- **띄어쓰기 (spacing)** changes meaning — keep words correctly spaced; attach suffixes like -용 with no space to an English token: **Windows Server용**.

## Layout / CSS for Korean
- Korean text **contracts ~10–15% in character count** vs English but each glyph is ~full-width and taller → boxes are often comparable-width and **taller**. Constrain by rendered size, not character count.
- Line breaking: `word-break: keep-all` (break at spaces, keep eojeol whole) + `overflow-wrap: break-word` as a URL safety valve. Scope to `:lang(ko)`.
- Line-height **≥ 1.5** (KRDS floor); don't rely on tight leading. Pretendard / Noto Sans KR. Don't italicize CJK.
- Common 2-syllable button labels (확인·취소·저장·삭제·설정·닫기·다음·완료) are narrower than English — don't hard-size buttons to English width.

```css
:lang(ko) {
  word-break: keep-all;
  overflow-wrap: break-word;
  line-height: 1.6;
  font-family: Pretendard, "Noto Sans KR", system-ui, sans-serif;
}
```
