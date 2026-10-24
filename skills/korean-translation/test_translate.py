#!/usr/bin/env python3
"""Unit tests for translate.py — no network. Run: python3 test_translate.py
Covers the deterministic gate (N1/N2/F2/F5/F1) and Tier-1/2 functions (mask/glossary/auto-dnt/doc/fix)."""
import os
import sys
import io
import json
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout, redirect_stderr

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import translate  # noqa: E402
import josa  # noqa: E402


def types(flags):
    return {f["type"] for f in flags}


def opts(**kw):
    base = dict(mode="translate", register=None, terms={}, fix=False, mask=True, max_tokens=0, max_retries=2,
                temperature=0.2, timeout=5, show_reasoning=False, concurrency=4, auto_dnt=False,
                strict=False, dry_run=False, polish_dialogue=False, max_diff_ratio=0.6)
    base.update(kw)
    return base


class Gate(unittest.TestCase):
    def test_placeholder_mismatch(self):            # N1
        self.assertIn("placeholder_mismatch", types(translate.postcheck("%d items in {folder}", "{folder}에 결과 d개")))

    def test_placeholder_ok_and_icu_skip(self):
        self.assertNotIn("placeholder_mismatch", types(translate.postcheck("%d in {f}", "{f}에 %d개")))
        self.assertNotIn("placeholder_mismatch", types(translate.postcheck("{n, plural, other {#}}", "기타", category="icu")))

    def test_josa_after_var(self):                  # N2
        self.assertIn("josa_after_var", types(translate.postcheck("Save to {folder}", "{folder}을 저장")))
        self.assertNotIn("josa_after_var", types(translate.postcheck("Save to {folder}", "{folder}을(를) 저장")))

    def test_translationese_and_foreign(self):      # F5
        self.assertTrue({"passive_eui", "pronoun", "double_passive"} <= types(translate.postcheck("x", "당신에 의해 삭제되어진 파일")))
        self.assertIn("foreign_script", types(translate.postcheck("reporter", "記者")))
        self.assertEqual(translate.postcheck("Delete the file", "파일을 삭제합니다"), [])

    def test_register(self):                        # F1
        self.assertEqual(translate.register_flag("다시 시도해요", "hapsyo")["type"], "register_mismatch")
        self.assertIsNone(translate.register_flag("다시 시도합니다", "hapsyo"))


class Masking(unittest.TestCase):                     # Tier-1 A
    def test_roundtrip_and_placeholders_visible(self):
        src = "Run `npm i` then see https://x.io/docs and set {flag}"
        masked, mapping = translate.mask_protected(src)
        self.assertNotIn("npm i", masked)            # inline code masked
        self.assertNotIn("https://x.io/docs", masked)  # URL masked
        self.assertIn("{flag}", masked)              # placeholder NOT masked (josa needs it)
        self.assertEqual(translate.unmask(masked, mapping), src)  # exact restore

    def test_fenced_code_masked(self):
        masked, mapping = translate.mask_protected("before\n```\ncode()\n```\nafter")
        self.assertNotIn("code()", masked)
        self.assertIn("code()", translate.unmask(masked, mapping))


class Glossary(unittest.TestCase):                    # Tier-1 B
    def test_load_inline_and_segment(self):
        terms = translate.load_terms("Anthropic,Gemma", "toggle=토글,API=API")
        self.assertEqual(terms["Anthropic"], "Anthropic")  # DNT (tgt==src)
        self.assertEqual(terms["toggle"], "토글")
        self.assertEqual(set(translate.segment_terms("Enable the toggle", terms)), {"toggle"})

    def test_load_tsv_file(self):
        d = tempfile.mkdtemp(); fp = os.path.join(d, "g.tsv")
        open(fp, "w", encoding="utf-8").write("dashboard\t대시보드\nlogin\t로그인\n")
        self.assertEqual(translate.load_terms("", fp)["dashboard"], "대시보드")

    def test_term_missing_and_dnt(self):
        t = {"toggle": "토글", "Anthropic": "Anthropic"}
        self.assertIn("term_missing", types(translate.postcheck("the toggle", "스위치 켜기", t)))
        self.assertIn("dnt_violation", types(translate.postcheck("Anthropic ships", "앤트로픽 출시", t)))
        self.assertEqual(translate.postcheck("the toggle", "토글 켜기", t), [])


class AutoDnt(unittest.TestCase):                     # Tier-2 F
    def test_recurring_caps_pinned(self):
        # tokens must recur in >=2 segments AND appear >=1x non-sentence-initial (Q3/LV-08)
        a = translate.auto_dnt(["Ships with Anthropic Claude", "Enable Anthropic and Gemma", "We use Gemma"])
        self.assertIn("Anthropic", a)  # 2 segments, mid-sentence
        self.assertIn("Gemma", a)
        self.assertNotIn("Claude", a)  # appears once

    def test_stopwords_and_initial_only_excluded(self):   # Q3: LV-08
        rows = ["Open the file to continue.", "Open settings and save.",
                "The report is ready.", "The system will update."]
        a = translate.auto_dnt(rows)
        self.assertNotIn("Open", a)   # common-word stoplist
        self.assertNotIn("The", a)    # only ever sentence-initial
        self.assertIn("GitHub", translate.auto_dnt(["Connect to GitHub now.", "Push to GitHub daily."]))


class Doc(unittest.TestCase):                          # Tier-2 E
    def test_split_preserves_structure_and_extracts_prose(self):
        md = "# Title\n\nHello world.\n\n```\ncode\n```\n\n- item one"
        blocks = translate.split_doc(md)
        prose = [b for b in blocks if b["type"] == "prose"]
        self.assertEqual([b["text"] for b in prose], ["Title", "Hello world.", "item one"])
        self.assertEqual(translate.reassemble_doc(blocks, [b["text"] for b in prose]), md)  # identity round-trip

    def test_code_lines_are_verbatim(self):
        blocks = translate.split_doc("```\nx=1\n```")
        self.assertTrue(all(b["type"] == "verbatim" for b in blocks))


class FixNote(unittest.TestCase):                      # Tier-1 C / Tier-2 D
    def test_targets_flagged_types(self):
        note = translate.fix_note([{"type": "josa_after_var", "severity": "major"},
                             {"type": "register_mismatch", "severity": "minor"}], ["{x}"], "hapsyo")
        self.assertIn("이중형", note)
        self.assertIn("합쇼체", note)


class TranslateOne(unittest.TestCase):
    def setUp(self):
        self._orig = translate.call_once

    def tearDown(self):
        translate.call_once = self._orig

    def _seq(self, responses):
        it = iter(responses)
        translate.call_once = lambda *a, **k: next(it)

    def test_empty_recovers_then_clean(self):          # N3
        self._seq([("", "reasoning", "length"), ("로그인하세요", "", "stop")])
        self.assertEqual(translate.translate_one("Sign in", opts(), ("k", "https://e", "m"))["status"], "clean")

    def test_persistent_empty_nonsilent(self):
        self._seq([("", "r", "length")] * 3)
        self.assertEqual(translate.translate_one("hard", opts(), ("k", "https://e", "m"))["status"], "empty")

    def test_fix_flag_retries_major_then_clean(self):  # Tier-1 C: --fix escalates major
        self._seq([("{folder}으로 저장", "", "stop"), ("{folder}(으)로 저장", "", "stop")])
        r = translate.translate_one("Save to {folder}", opts(fix=True), ("k", "https://e", "m"))
        self.assertEqual(r["status"], "clean")
        self.assertEqual(r["tries"], 2)

    def test_major_not_retried_without_fix(self):      # without --fix, major flags only warn
        self._seq([("{folder}으로 저장", "", "stop")])
        r = translate.translate_one("Save to {folder}", opts(fix=False), ("k", "https://e", "m"))
        self.assertEqual(r["status"], "flagged")
        self.assertEqual(r["tries"], 1)

    def test_masking_integration_restores(self):       # Tier-1 A end-to-end through translate_one
        # model is given masked text and echoes it; unmask must restore the URL verbatim
        def stub(endpoint, system, text, *a, **k):
            return (text + " 참고", "", "stop")  # echoes the masked user text
        translate.call_once = stub
        r = translate.translate_one("See https://naver.com now", opts(), ("k", "https://e", "m"))
        self.assertIn("https://naver.com", r["translation"])


class Batch(unittest.TestCase):
    def setUp(self):
        self._orig = translate.call_once
        translate.call_once = lambda *a, **k: ("번역됨", "", "stop")

    def tearDown(self):
        translate.call_once = self._orig

    def test_jsonl_roundtrip(self):
        d = tempfile.mkdtemp(); inp, outp = os.path.join(d, "i.jsonl"), os.path.join(d, "o.jsonl")
        with open(inp, "w", encoding="utf-8") as fh:
            for i, s in enumerate(["Hello", "World"]):
                fh.write(json.dumps({"id": f"k{i}", "source_en": s}) + "\n")
        rc = translate.run_batch(opts(in_path=inp, out_path=outp), ("k", "https://e", "m"))
        with open(outp, encoding="utf-8") as fh:
            rows = [json.loads(l) for l in fh]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rc, 0)


class PlaceholderTokenizer(unittest.TestCase):        # Q2: RG-04/05/06
    def test_icu_missing_outer_brace_caught(self):    # RG-04: balanced-brace scanner
        src = "{n, plural, other {#개의 파일}}"
        out = "{n, plural, other {#개의 파일}"          # dropped the outer close brace
        self.assertNotEqual(translate.placeholders(src), translate.placeholders(out))
        self.assertIn("placeholder_mismatch", types(translate.postcheck(src, out)))

    def test_balanced_icu_parity_ok(self):
        src = "{n, plural, other {# files}}"
        self.assertEqual(translate.placeholders(src), translate.placeholders(src))
        self.assertNotIn("placeholder_mismatch", types(translate.postcheck(src, src)))

    def test_escaped_percent_counted(self):           # RG-05: %% is a token
        self.assertIn("%%", translate.placeholders("Progress: %% complete"))
        self.assertIn("placeholder_mismatch", types(translate.postcheck("Progress: %% done", "진행률 완료")))

    def test_plain_comparison_not_a_tag(self):        # RG-06: 'a < b' is not a placeholder
        self.assertEqual(translate.placeholders("Show a < b and c > d"), translate.placeholders("표시"))
        self.assertNotIn("placeholder_mismatch", types(translate.postcheck("Show a < b and c > d", "a는 b보다 작음")))

    def test_real_tags_still_counted(self):
        self.assertIn("<b>", translate.placeholders("<b>Hi</b>"))
        self.assertIn("</b>", translate.placeholders("<b>Hi</b>"))
        self.assertIn("placeholder_mismatch", types(translate.postcheck("<b>Hi</b>", "안녕")))

    def test_printf_width_and_positional_preserved(self):   # no regression vs old regex
        self.assertEqual(set(translate.placeholders("%1$s used %12d bytes")), {"%1$s", "%12d"})


class MaskingHardening(unittest.TestCase):            # Q2: RG-01/02/03/31
    def test_url_stops_before_hangul_and_trailing_punct(self):   # RG-02
        _, mp = translate.mask_protected("Read https://example.com에서, 확인")
        self.assertEqual(mp, ["https://example.com"])

    def test_url_trailing_comma_stripped(self):        # RG-02
        _, mp = translate.mask_protected("Go to https://example.com, then continue")
        self.assertEqual(mp, ["https://example.com"])

    def test_inline_code_masked_before_url(self):      # RG-02: ordering keeps backticks intact
        masked, _ = translate.mask_protected("Run `curl https://example.com` now")
        self.assertNotIn("curl", masked)
        self.assertNotIn("`", masked)

    def test_url_leaves_placeholder_brace(self):       # RG-02: URL must not eat the closing }
        masked, mp = translate.mask_protected("Use {link:https://example.com} now")
        self.assertIn("}", masked)                     # closing brace preserved (not swallowed)
        self.assertEqual(masked.count("{"), masked.count("}"))
        self.assertEqual(mp, ["https://example.com"])  # only the URL got masked

    def test_unclosed_fence_masked_to_eof(self):       # RG-03
        masked, _ = translate.mask_protected("Before\n```\nrm -rf /\nno close")
        self.assertNotIn("rm -rf", masked)

    def test_tilde_fence_masked(self):                 # RG-31
        masked, mp = translate.mask_protected("before\n~~~js\nconst x=1;\n~~~\nafter")
        self.assertNotIn("const x=1;", masked)
        self.assertEqual(translate.unmask(masked, mp), "before\n~~~js\nconst x=1;\n~~~\nafter")

    def test_longer_fence_not_split_by_inner(self):    # RG-03: 4-backtick fence over inner ```
        src = "before\n````\n```nested\n```\n````\nafter"
        masked, mp = translate.mask_protected(src)
        self.assertEqual(masked.count("⟦"), 1)
        self.assertNotIn("nested", masked)
        self.assertEqual(translate.unmask(masked, mp), src)

    def test_sentinel_in_source_roundtrips(self):      # RG-01: pre-existing ⟦n⟧ must survive
        src = "`literal ⟦1⟧` `other`"
        masked, mp = translate.mask_protected(src)
        self.assertEqual(translate.unmask(masked, mp), src)

    def test_toplevel_sentinel_lookalike_roundtrips(self):   # RG-01
        src = "Literal ⟦0⟧ plus https://example.com"
        masked, mp = translate.mask_protected(src)
        self.assertEqual(translate.unmask(masked, mp), src)

    def test_scan_fences_line_ranges(self):            # shared helper (Q5 reuses it)
        self.assertEqual(translate.scan_fences("a\n```\nx\n```\nb"), [(1, 3)])
        self.assertEqual(translate.scan_fences("a\n~~~\nx\n~~~\nb"), [(1, 3)])
        self.assertEqual(translate.scan_fences("a\n```\nunclosed"), [(1, 2)])   # to EOF
        self.assertEqual(translate.scan_fences("no fences here"), [])


class MaskIntegrity(unittest.TestCase):               # Q2: RG-01 integrity flag via translate_one
    def setUp(self):
        self._orig = translate.call_once

    def tearDown(self):
        translate.call_once = self._orig

    def test_dropped_sentinel_is_broken(self):
        # model drops the URL sentinel entirely -> integrity fail -> critical -> broken
        translate.call_once = lambda *a, **k: ("확인", "", "stop")
        r = translate.translate_one("See https://naver.com", opts(max_retries=1), ("k", "https://e", "m"))
        self.assertIn("mask_integrity", types(r["flags"]))
        self.assertEqual(r["status"], "broken")

    def test_duplicated_sentinel_is_broken(self):
        # model duplicates one sentinel and drops another -> corruption
        def stub(endpoint, system, text, *a, **k):
            return ("⟦0⟧ 그리고 ⟦0⟧", "", "stop")   # ⟦1⟧ missing, ⟦0⟧ twice
        translate.call_once = stub
        r = translate.translate_one("Run `npm i` and see https://naver.com",
                              opts(max_retries=0), ("k", "https://e", "m"))
        self.assertIn("mask_integrity", types(r["flags"]))
        self.assertEqual(r["status"], "broken")

    def test_preserved_sentinels_clean(self):
        def stub(endpoint, system, text, *a, **k):
            return (text, "", "stop")   # echoes the masked text incl. sentinels
        translate.call_once = stub
        r = translate.translate_one("See https://naver.com now", opts(), ("k", "https://e", "m"))
        self.assertNotIn("mask_integrity", types(r["flags"]))
        self.assertIn("https://naver.com", r["translation"])


class TermsMaskConsistency(unittest.TestCase):        # Q2/C1: terms inside masked spans not required
    def setUp(self):
        self._orig = translate.call_once

    def tearDown(self):
        translate.call_once = self._orig

    def test_term_only_inside_url_not_required(self):
        def stub(endpoint, system, text, *a, **k):
            return (text + " 참고", "", "stop")   # echoes masked text, URL preserved
        translate.call_once = stub
        r = translate.translate_one("See https://example.com", opts(terms={"example": "예시"}),
                             ("k", "https://e", "m"))
        self.assertNotIn("term_missing", types(r["flags"]))

    def test_term_in_visible_text_still_required(self):
        translate.call_once = lambda *a, **k: ("스위치 켜기", "", "stop")
        r = translate.translate_one("Enable the toggle", opts(terms={"toggle": "토글"}),
                             ("k", "https://e", "m"))
        self.assertIn("term_missing", types(r["flags"]))


class Josa(unittest.TestCase):                        # Q1: josa.py hardening (was ZERO coverage)
    def test_fsx_acronyms_are_vowel_final(self):      # RG-25 (F/S/X portion)
        self.assertEqual(josa.attach("PDF", "을/를"), "PDF를")
        self.assertEqual(josa.attach("CSS", "으로"), "CSS로")
        self.assertEqual(josa.attach("UX", "을/를"), "UX를")

    def test_letter_l_and_b_still_solid(self):        # regression guard
        self.assertEqual(josa.attach("L", "을/를"), "L을")   # 엘 -> ㄹ
        self.assertEqual(josa.attach("B", "을/를"), "B를")   # 비 -> vowel

    def test_terminal_punctuation_ignored(self):      # RG-26
        self.assertEqual(josa.attach("서울.", "을/를"), "서울.을")
        self.assertEqual(josa.attach("부산!", "으로"), "부산!으로")
        self.assertEqual(josa.attach("파일,", "을/를"), "파일,을")
        self.assertEqual(josa.attach("파일…", "을/를"), "파일…을")
        self.assertEqual(josa.attach('"서울."', "을/를"), '"서울."을')

    def test_unicode_digit_glyphs_do_not_crash(self):  # RG-24
        self.assertEqual(josa.attach("항목②", "을/를"), "항목②를")
        self.assertEqual(josa.attach("H₂", "을/를"), "H₂를")
        self.assertEqual(josa.attach("x²", "을/를"), "x²를")

    def test_ascii_numeric_readings_unchanged(self):   # regression guard
        self.assertEqual(josa.attach("파일 10", "으로"), "파일 10으로")   # 십 -> ㅂ
        self.assertEqual(josa.attach("1", "으로"), "1로")                # 일 -> ㄹ
        self.assertEqual(josa.attach("파일 1,024", "을/를"), "파일 1,024를")  # 이십사 -> vowel

    def test_fullwidth_comma_grouping(self):           # RG-28
        big = "１，０００，０００，０００，０００"
        self.assertEqual(josa.attach(big, "을/를"), big + "를")   # 일조 -> 조 vowel

    def test_fill_literal_bracket_does_not_crash(self):  # RG-23
        self.assertEqual(josa.fill("{code}[beta] enabled", code="API"), "API[beta] enabled")
        self.assertEqual(josa.fill("{name}[을/를] 삭제", name="메뉴"), "메뉴를 삭제")

    def test_fill_adjacent_markers_still_work(self):   # regression guard
        self.assertEqual(josa.fill("{a}[을/를]{b}[이/가]", a="파일", b="폴더"), "파일을폴더가")

    def test_empty_and_whitespace_value_no_orphan_particle(self):  # RG-27
        self.assertEqual(josa.attach("", "을/를"), "")
        self.assertEqual(josa.attach("   ", "을/를"), "   ")
        self.assertEqual(josa.fill("{name}[을/를] 삭제", name=""), " 삭제")

    def test_select_empty_is_safe_vowel_default(self):  # RG-27: no crash
        self.assertEqual(josa.select("", "을/를"), "를")


class GatePrecisionRecall(unittest.TestCase):         # Q3: RG-07/08/10/11/12/17/18/19-22, LV-04/07/08
    # ---- josa_after_var tightening (RG-07 FP, RG-08 malformed) ----
    def test_josa_no_fp_on_tags_and_word_starts(self):     # RG-07
        for out in ["<b>이메일</b> 주소", "<b>제목</b>이 필요합니다", "값 > 이 기준이면 실패합니다",
                    "문자 } 이후 계속됩니다", "{app} 로딩 중…"]:
            self.assertNotIn("josa_after_var", types(translate.postcheck("x", out)), out)

    def test_josa_flags_hardcoded_particle(self):
        self.assertIn("josa_after_var", types(translate.postcheck("Save to {folder}", "{folder}을 저장")))
        self.assertIn("josa_after_var", types(translate.postcheck("x", "%s으로 이동")))
        self.assertIn("josa_after_var", types(translate.postcheck("x", "{file}로 이동")))

    def test_josa_valid_dualform_passes(self):             # RG-08 accept-list
        for out in ["{folder}을(를) 저장", "%s은(는) 사용", "{n}이(가) 있음", "{file}(으)로 이동"]:
            self.assertNotIn("josa_after_var", types(translate.postcheck("x", out)), out)

    def test_josa_malformed_dualform_flags(self):          # RG-08
        for out in ["{folder}을(를 저장", "{folder}을 (를) 저장", "{folder}을(foo) 저장",
                    "{folder}을() 저장", "{folder}을(으로) 저장"]:
            self.assertIn("josa_after_var", types(translate.postcheck("Save to {folder}", out)), out)

    # ---- plural_deul context-aware advisory (RG-12) ----
    def test_plural_deul_no_fp_on_natural_plurals(self):
        for out in ["친구들과 공유", "학생들에게 안내", "친구들이 왔습니다", "개발자들의 의견", "참석자들만 남았습니다"]:
            self.assertNotIn("plural_deul", types(translate.postcheck("x", out)), out)

    def test_plural_deul_flags_calques(self):
        self.assertIn("plural_deul", types(translate.postcheck("x", "3개의 파일들")))
        self.assertIn("plural_deul", types(translate.postcheck("x", "모든 사용자들 로그인")))
        self.assertIn("plural_deul", types(translate.postcheck("x", "사용자들 모두 계속")))     # G2 PL03
        self.assertIn("plural_deul", types(translate.postcheck("x", "알림들 전체를 검토")))    # G2 PL04

    def test_plural_deul_is_advisory(self):                # D1
        self.assertNotIn("plural_deul", translate.FIXABLE_HEURISTICS)

    # ---- passive_eui / double_passive widening (RG-10/11) ----
    def test_passive_eui_widened(self):
        self.assertIn("passive_eui", types(translate.postcheck("x", "정책에 의하여 승인됐습니다")))   # G2 P03
        self.assertIn("passive_eui", types(translate.postcheck("x", "규칙에 의한 처리")))
        self.assertIn("passive_eui", types(translate.postcheck("x", "관리자에 의해 삭제")))
        self.assertIn("passive_eui", translate.FIXABLE_HEURISTICS)

    def test_double_passive_widened_and_spaced(self):
        for out in ["창이 자동으로 닫혀집니다", "메뉴가 열려집니다",              # new spec stems (RG-11)
                    "파일이 삭제되어 질 예정입니다", "요청이 처리되어 집니다"]:      # G2 D03/D04 (spaced)
            self.assertIn("double_passive", types(translate.postcheck("x", out)), out)
        self.assertIn("double_passive", translate.FIXABLE_HEURISTICS)

    # ---- foreign_script source-aware (RG-18) ----
    def test_foreign_script_source_preserved_ok(self):
        self.assertNotIn("foreign_script",
                         types(translate.postcheck("File name: 日本語ガイド.pdf", "파일 이름: 日本語ガイド.pdf")))
        self.assertNotIn("foreign_script", types(translate.postcheck("View in 中文(简体).", "中文(简体)로 보기")))

    def test_foreign_script_new_and_compat_hanja(self):
        self.assertIn("foreign_script", types(translate.postcheck("reporter", "記者")))
        self.assertIn("foreign_script", types(translate.postcheck("Check assignee Lee.", "담당자 李를 확인합니다")))  # U+F9E1

    # ---- low_korean masked/identifier-aware (RG-17, LV-10) ----
    def test_low_korean_no_fp_on_preserved_identifiers(self):
        for src, out in [("Copy OAuth Client ID.", "OAuth Client ID 복사"),
                         ("SAML SSO URL settings", "SAML SSO URL 설정"),
                         ("iOS App Store Connect API key", "iOS App Store Connect API 키"),
                         ("Page {page} of {total}.", "{total}장 중 {page}장")]:
            self.assertNotIn("low_korean", types(translate.postcheck(src, out)), out)

    def test_low_korean_still_flags_untranslated_latin(self):
        self.assertIn("low_korean", types(translate.postcheck("x", "abcdefghij 킥")))

    # ---- untranslated advisory (LV-07) ----
    def test_untranslated_flag(self):
        icu = "{count, plural, one {# file} other {# files}}"
        self.assertIn("untranslated", types(translate.postcheck(icu, icu)))
        self.assertNotIn("untranslated", types(translate.postcheck("Save", "저장")))

    # ---- ICU auto-detect (ICU-unreachable) ----
    def test_icu_autodetect_skips_leaf_parity(self):
        src = "{count, plural, other {# files}}"
        out = "{count, plural, other {# 파일}}"          # leaf translated, braces balanced
        self.assertNotIn("placeholder_mismatch", types(translate.postcheck(src, out)))

    def test_icu_autodetect_still_catches_brace_drop(self):   # RG-04 preserved
        src = "{n, plural, other {#개의 파일}}"
        out = "{n, plural, other {#개의 파일}"            # dropped outer brace -> unbalanced
        self.assertIn("placeholder_mismatch", types(translate.postcheck(src, out)))

    def test_non_icu_parity_unaffected(self):
        self.assertIn("placeholder_mismatch", types(translate.postcheck("<b>Hi</b>", "안녕")))
        self.assertNotIn("placeholder_mismatch", types(translate.postcheck("%d in {f}", "{f}에 %d개")))

    # ---- register per-sentence rewrite (RG-19/20/21/22, LV-04) ----
    def test_register_mixed_flagged(self):                 # RG-19 / G2 R02
        self.assertIsNotNone(translate.register_flag("파일을 저장합니다. 다시 시도해요.", "hapsyo"))
        self.assertIsNotNone(translate.register_flag("파일을 저장합니다. 계속해요.", "hapsyo"))
        self.assertIsNotNone(translate.register_flag("파일을 삭제해요. 계속 진행합니다.", "haeyo"))

    def test_register_handa_enforced(self):                # RG-20
        self.assertIsNotNone(translate.register_flag("파일을 삭제합니다.", "handa"))
        self.assertIsNotNone(translate.register_flag("파일을 삭제한다.", "hapsyo"))
        self.assertIsNotNone(translate.register_flag("삭제한다", "noun"))

    def test_register_haeyo_allomorphs(self):              # RG-21
        self.assertIsNotNone(translate.register_flag("교육요금이 변경돼요.", "hapsyo"))
        self.assertIsNotNone(translate.register_flag("버튼을 눌러요.", "hapsyo"))

    def test_register_no_fp_on_quotes_and_labels(self):    # RG-22
        self.assertIsNone(translate.register_flag('제품명 "합니다" 사용', "noun"))
        self.assertIsNone(translate.register_flag("하세요체 안내", "noun"))
        self.assertIsNone(translate.register_flag('"삭제합니다" 버튼', "haeyo"))
        self.assertIsNone(translate.register_flag("교육요금 보기", "hapsyo"))   # word-internal 요

    def test_register_clean_baselines(self):
        self.assertIsNone(translate.register_flag("다시 시도합니다", "hapsyo"))
        self.assertIsNone(translate.register_flag("저장해요", "haeyo"))
        self.assertIsNone(translate.register_flag("삭제한다", "handa"))
        self.assertIsNone(translate.register_flag("설정", "noun"))


class GateFixability(unittest.TestCase):              # Q3/D1: fixable vs advisory in translate_one
    def setUp(self):
        self._orig = translate.call_once

    def tearDown(self):
        translate.call_once = self._orig

    def test_register_fixable_retries(self):           # register now FIXABLE
        seq = iter([("다시 시도해요", "", "stop"), ("다시 시도합니다", "", "stop")])
        translate.call_once = lambda *a, **k: next(seq)
        r = translate.translate_one("Try again", opts(fix=True, register="hapsyo"), ("k", "https://e", "m"))
        self.assertEqual(r["status"], "clean")
        self.assertEqual(r["tries"], 2)

    def test_advisory_flag_never_retried_under_fix(self):   # pronoun is advisory
        translate.call_once = lambda *a, **k: ("당신의 파일을 삭제합니다", "", "stop")
        r = translate.translate_one("Delete your file", opts(fix=True), ("k", "https://e", "m"))
        self.assertEqual(r["tries"], 1)
        self.assertEqual(r["status"], "flagged")
        self.assertIn("pronoun", types(r["flags"]))

    def test_passive_fixable_retries(self):
        seq = iter([("관리자에 의해 삭제됩니다", "", "stop"), ("관리자가 삭제합니다", "", "stop")])
        translate.call_once = lambda *a, **k: next(seq)
        r = translate.translate_one("Deleted by admin", opts(fix=True), ("k", "https://e", "m"))
        self.assertEqual(r["status"], "clean")
        self.assertEqual(r["tries"], 2)


class TranslateOneFlow(unittest.TestCase):            # Q4: keep-best + explicit max-tokens cap (C1)
    def setUp(self):
        self._orig = translate.call_once

    def tearDown(self):
        translate.call_once = self._orig

    def test_keep_best_returns_earlier_better_attempt(self):    # C1/lens: keep-BEST, not last
        # attempt1 has ONE major flag; the retry (attempt2) is strictly worse (two majors) -> keep #1
        seq = iter([("{folder}을 저장", "", "stop"),                 # josa_after_var only
                    ("{folder}을 당신이 저장", "", "stop")])           # josa_after_var + pronoun (worse)
        translate.call_once = lambda *a, **k: next(seq)
        r = translate.translate_one("Save to {folder}", opts(fix=True, max_retries=1), ("k", "https://e", "m"))
        self.assertEqual(r["translation"], "{folder}을 저장")         # the better earlier attempt
        self.assertEqual(r["status"], "flagged")

    def test_keep_best_prefers_nonempty_over_trailing_empty(self):  # empty is worse than any flagged output
        seq = iter([("{folder}을 저장", "", "stop"), ("", "", "length")])  # good-ish, then empty
        translate.call_once = lambda *a, **k: next(seq)
        r = translate.translate_one("Save to {folder}", opts(fix=True, max_retries=1), ("k", "https://e", "m"))
        self.assertEqual(r["translation"], "{folder}을 저장")         # not the empty final attempt
        self.assertNotEqual(r["status"], "empty")

    def test_explicit_max_tokens_never_doubled(self):           # C1: explicit --max-tokens is a hard cap
        seen = []

        def stub(endpoint, system, text, model, api_key, max_tokens, *a, **k):
            seen.append(max_tokens)
            return ("", "", "length") if len(seen) == 1 else ("로그인하세요", "", "stop")
        translate.call_once = stub
        r = translate.translate_one("Hello", opts(max_tokens=123, max_retries=1), ("k", "https://e", "m"))
        self.assertEqual(seen, [123, 123])                      # NOT [123, 246]
        self.assertEqual(r["status"], "clean")

    def test_auto_max_tokens_still_doubles_on_empty(self):      # only auto-scale doubles
        seen = []

        def stub(endpoint, system, text, model, api_key, max_tokens, *a, **k):
            seen.append(max_tokens)
            return ("", "", "length") if len(seen) == 1 else ("로그인하세요", "", "stop")
        translate.call_once = stub
        r = translate.translate_one("Hi", opts(max_tokens=0, max_retries=1), ("k", "https://e", "m"))
        self.assertEqual(seen[0], 4096)                         # short tier (Q6)
        self.assertEqual(seen[1], 8192)                         # doubled
        self.assertEqual(r["status"], "clean")


class Resolve(unittest.TestCase):                     # key/endpoint/model resolution (operator directive 2026-07-13)
    ENV_VARS = ("LLM_API_KEY", "LLM_ENDPOINT", "LLM_MODEL")

    def setUp(self):
        self._saved = {k: os.environ.pop(k, None) for k in self.ENV_VARS}
        self._orig_rkf = translate.read_key_file

    def tearDown(self):
        translate.read_key_file = self._orig_rkf
        for k, v in self._saved.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)

    # ── api_key: env LLM_API_KEY -> ./.api_key -> nothing (never a config file: a bearer token must not be committed) ──
    def test_api_key_not_read_from_config(self):       # C2: config api_key is ignored
        translate.read_key_file = lambda: None
        api_key, endpoint, model = translate.resolve(None, {"api_key": "NOPE", "endpoint": "https://x", "model": "m"})
        self.assertIsNone(api_key)                          # config api_key ignored (docs: env/.api_key only)
        self.assertEqual(endpoint, "https://x")             # non-key config still honored
        self.assertEqual(model, "m")

    def test_env_key_wins_over_file(self):             # LLM_API_KEY beats ./.api_key
        os.environ["LLM_API_KEY"] = "envkey"
        translate.read_key_file = lambda: "filekey"
        self.assertEqual(translate.resolve(None, {"api_key": "cfgkey"})[0], "envkey")

    def test_key_file_used_when_no_env(self):          # ./.api_key used when env is unset
        translate.read_key_file = lambda: "filekey"
        self.assertEqual(translate.resolve(None, {})[0], "filekey")

    def test_no_key_when_env_and_file_absent(self):    # neither source present -> None (guard rejects later)
        translate.read_key_file = lambda: None
        self.assertIsNone(translate.resolve(None, {})[0])

    def test_read_key_file_reads_dot_api_key_not_hcx(self):   # the on-disk key file is ./.api_key, never ./.hcx_key
        d = tempfile.mkdtemp()
        with open(os.path.join(d, ".api_key"), "w", encoding="utf-8") as fh:
            fh.write("FROM-API-KEY\n")
        with open(os.path.join(d, ".hcx_key"), "w", encoding="utf-8") as fh:
            fh.write("FROM-HCX-KEY\n")                       # a stale legacy file must NOT be picked up
        orig_here = translate.HERE
        translate.HERE = d
        try:
            self.assertEqual(translate.read_key_file(), "FROM-API-KEY")
        finally:
            translate.HERE = orig_here

    # ── model: --model -> env LLM_MODEL -> config model -> DEFAULT_MODEL ──
    def test_model_requires_configuration(self):
        self.assertEqual(translate.resolve(None, {})[2], "")

    def test_no_sovereign_model_attr(self):            # HCX/sovereign constant purged from code
        self.assertFalse(hasattr(translate, "SOVEREIGN_MODEL"))

    def test_model_resolution_order(self):
        translate.read_key_file = lambda: None
        self.assertEqual(translate.resolve("cli", {"model": "cfg"})[2], "cli")      # --model wins
        os.environ["LLM_MODEL"] = "envm"
        self.assertEqual(translate.resolve(None, {"model": "cfg"})[2], "envm")      # env over config
        os.environ.pop("LLM_MODEL")
        self.assertEqual(translate.resolve(None, {"model": "cfg"})[2], "cfg")       # config over default
        self.assertEqual(translate.resolve(None, {})[2], translate.DEFAULT_MODEL)   # default last

    # ── endpoint: env LLM_ENDPOINT -> config endpoint -> config base_url+/chat/completions -> DEFAULT_ENDPOINT ──
    def test_endpoint_requires_configuration(self):
        self.assertEqual(translate.resolve(None, {})[1], "")

    def test_endpoint_resolution_order(self):
        translate.read_key_file = lambda: None
        os.environ["LLM_ENDPOINT"] = "https://env/v1/chat/completions"
        self.assertEqual(translate.resolve(None, {"endpoint": "https://cfg"})[1], "https://env/v1/chat/completions")
        os.environ.pop("LLM_ENDPOINT")
        self.assertEqual(translate.resolve(None, {"endpoint": "https://cfg"})[1], "https://cfg")
        self.assertEqual(translate.resolve(None, {"base_url": "https://base"})[1], "https://base/chat/completions")
        self.assertEqual(translate.resolve(None, {})[1], translate.DEFAULT_ENDPOINT)


class RunDoc(unittest.TestCase):                      # Q4/C1/C2/D2: doc-mode flow safety
    def setUp(self):
        self._orig = translate.call_once

    def tearDown(self):
        translate.call_once = self._orig

    def _run(self, text, o):
        buf, err = io.StringIO(), io.StringIO()
        with redirect_stdout(buf), redirect_stderr(err):
            rc = translate.run_doc(text, o, ("k", "https://e", "m"))
        return rc, buf.getvalue(), err.getvalue()

    def test_broken_block_keeps_source_text(self):    # D2: broken/empty block keeps SOURCE, exit 1
        translate.call_once = lambda *a, **k: ("", "", "length")     # every prose block comes back empty
        rc, out, err = self._run("# Title\n\nBody text here.", opts(max_retries=0))
        self.assertEqual(rc, 1)
        self.assertIn("Title", out)                            # source kept, not blank
        self.assertIn("Body text here.", out)

    def test_good_blocks_translated_broken_kept(self):  # mixed: good block translated, empty kept
        seq = iter([("제목", "", "stop"), ("", "", "length")])
        translate.call_once = lambda *a, **k: next(seq)
        rc, out, err = self._run("# Title\n\nBody text here.", opts(max_retries=0))
        self.assertEqual(rc, 1)
        self.assertIn("제목", out)                              # good block used its translation
        self.assertIn("Body text here.", out)                  # broken block kept source

    def test_strict_doc_exits_2_on_flag(self):         # C1/C2: --strict honored in doc mode
        translate.call_once = lambda *a, **k: ("{folder}으로 저장", "", "stop")   # josa_after_var (major)
        rc, out, err = self._run("Save to {folder}", opts(strict=True, max_retries=0))
        self.assertEqual(rc, 2)

    def test_dry_run_previews_first_block_no_calls(self):   # C1: doc dry-run
        called = []
        translate.call_once = lambda *a, **k: called.append(1) or ("x", "", "stop")
        rc, out, err = self._run("# Title\n\n```\ncode\n```\n\nBody", opts(dry_run=True))
        self.assertEqual(rc, 0)
        self.assertEqual(called, [])                           # no network calls
        data = json.loads(out)
        self.assertIn("first_prose_request", data)
        self.assertIn("block_plan", data)

    def test_per_block_exception_contained(self):      # D2: block exception -> error status, source kept
        def boom(*a, **k):
            raise RuntimeError("gateway down")
        translate.call_once = boom
        rc, out, err = self._run("Just one paragraph.", opts(max_retries=0))
        self.assertEqual(rc, 1)
        self.assertIn("Just one paragraph.", out)              # source kept despite the exception


class BatchSafety(unittest.TestCase):                 # Q4/C1/C2/L4: batch flow safety
    def setUp(self):
        self._orig = translate.call_once

    def tearDown(self):
        translate.call_once = self._orig

    def _write(self, lines):
        d = tempfile.mkdtemp()
        inp, outp = os.path.join(d, "i.jsonl"), os.path.join(d, "o.jsonl")
        with open(inp, "w", encoding="utf-8") as fh:
            for line in lines:
                fh.write(line + "\n")
        return inp, outp

    def _run(self, o):
        buf, err = io.StringIO(), io.StringIO()
        with redirect_stdout(buf), redirect_stderr(err):
            rc = translate.run_batch(o, ("k", "https://e", "m"))
        return rc, buf.getvalue(), err.getvalue()

    def test_dry_run_no_calls_no_out(self):            # C1: batch honors --dry-run
        called = []
        translate.call_once = lambda *a, **k: called.append(1) or ("x", "", "stop")
        inp, outp = self._write([json.dumps({"id": "a", "source_en": "Hello"}),
                                 json.dumps({"id": "b", "source_en": "World"})])
        rc, out, err = self._run(opts(in_path=inp, out_path=outp, dry_run=True))
        self.assertEqual(rc, 0)
        self.assertEqual(called, [])                           # no calls
        self.assertFalse(os.path.exists(outp))                 # --out never written
        self.assertEqual(len(json.loads(out)), 2)              # one preview per row

    def test_strict_batch_exits_2_on_flag(self):       # C2: --strict in batch
        translate.call_once = lambda *a, **k: ("{folder}으로 저장", "", "stop")   # josa_after_var
        inp, outp = self._write([json.dumps({"id": "a", "source_en": "Save to {folder}"})])
        rc, out, err = self._run(opts(in_path=inp, out_path=outp, strict=True))
        self.assertEqual(rc, 2)

    def test_invalid_row_mode_is_error(self):          # per-row enum validation
        translate.call_once = lambda *a, **k: ("번역", "", "stop")
        inp, outp = self._write([json.dumps({"id": "a", "source_en": "Hi", "mode": "bogus"})])
        rc, out, err = self._run(opts(in_path=inp, out_path=outp))
        with open(outp, encoding="utf-8") as fh:
            row = json.loads(fh.readline())
        self.assertEqual(row["status"], "error")
        self.assertTrue(all(f.get("severity") for f in row["flags"]))   # severity key on error flags
        self.assertEqual(rc, 1)

    def test_invalid_row_register_is_error(self):
        translate.call_once = lambda *a, **k: ("번역", "", "stop")
        inp, outp = self._write([json.dumps({"id": "a", "source_en": "Hi", "register": "formal"})])
        rc, out, err = self._run(opts(in_path=inp, out_path=outp))
        with open(outp, encoding="utf-8") as fh:
            row = json.loads(fh.readline())
        self.assertEqual(row["status"], "error")

    def test_bad_jsonl_line_exits_2_with_line_number(self):    # guarded input read
        inp, outp = self._write([json.dumps({"id": "a", "source_en": "Hi"}), "{not valid json"])
        rc, out, err = self._run(opts(in_path=inp, out_path=outp))
        self.assertEqual(rc, 2)
        self.assertIn(":2:", err)                              # line number reported

    def test_missing_input_file_exits_2(self):
        rc, out, err = self._run(opts(in_path="/nonexistent/missing-input.jsonl", out_path="/tmp/o.jsonl"))
        self.assertEqual(rc, 2)

    def test_concurrency_zero_does_not_crash(self):    # concurrency = max(1, concurrency)
        translate.call_once = lambda *a, **k: ("번역", "", "stop")
        inp, outp = self._write([json.dumps({"id": "a", "source_en": "Hi"})])
        rc, out, err = self._run(opts(in_path=inp, out_path=outp, concurrency=0))
        self.assertEqual(rc, 0)

    def test_null_dnt_and_glossary_handled(self):      # per-row option build INSIDE try (null dnt)
        translate.call_once = lambda *a, **k: ("번역", "", "stop")
        inp, outp = self._write([json.dumps({"id": "a", "source_en": "Hi", "dnt": None, "glossary": None})])
        rc, out, err = self._run(opts(in_path=inp, out_path=outp))
        with open(outp, encoding="utf-8") as fh:
            row = json.loads(fh.readline())
        self.assertEqual(row["status"], "clean")               # handled gracefully, not a crash


class DocStructure(unittest.TestCase):                # Q5: RG-29/30/32/33/35/36/37
    def _prose_texts(self, blocks):
        return [b["text"] for b in blocks if b["type"] == "prose"]

    def _identity(self, md):
        blocks = translate.split_doc(md)
        return translate.reassemble_doc(blocks, self._prose_texts(blocks))

    def test_tilde_fence_verbatim(self):               # reuses scan_fences (Q2)
        blocks = translate.split_doc("before\n~~~js\nconst x=1;\n~~~\nafter")
        self.assertTrue(any(b["type"] == "verbatim" and "const x=1;" in b["text"] for b in blocks))
        self.assertEqual(self._identity("before\n~~~js\nconst x=1;\n~~~\nafter"),
                         "before\n~~~js\nconst x=1;\n~~~\nafter")

    def test_indented_code_verbatim(self):             # RG-29
        md = "Intro line.\n\n    def f():\n        return 1\n\nAfter"
        blocks = translate.split_doc(md)
        self.assertTrue(any(b["type"] == "verbatim" and "def f():" in b["text"] for b in blocks))
        self.assertTrue(any(b["type"] == "verbatim" and "return 1" in b["text"] for b in blocks))
        self.assertEqual(self._prose_texts(blocks), ["Intro line.", "After"])
        self.assertEqual(self._identity(md), md)

    def test_indented_code_not_grabbing_list_continuation(self):   # RG-34 parked: don't misfire mid-paragraph
        md = "A paragraph line\n    that wraps with indentation"
        blocks = translate.split_doc(md)
        # the wrapped line is a paragraph continuation, not a standalone indented code block
        self.assertFalse(any(b["type"] == "verbatim" and "that wraps" in b.get("text", "") for b in blocks))

    def test_yaml_front_matter_verbatim(self):         # RG-30
        md = "---\ntitle: Hello\ntags:\n  - docs\n---\n\n# Body"
        blocks = translate.split_doc(md)
        self.assertEqual(self._prose_texts(blocks), ["Body"])
        self.assertEqual(self._identity(md), md)

    def test_front_matter_only_at_doc_start(self):     # a --- mid-doc after prose is a setext underline, not FM
        md = "Heading\n---\n\nBody"
        blocks = translate.split_doc(md)
        self.assertIn("Heading", self._prose_texts(blocks))

    def test_pipe_table_verbatim(self):                # RG-32
        md = "| A | B |\n|---|---|\n| one | two |"
        blocks = translate.split_doc(md)
        self.assertTrue(all(b["type"] == "verbatim" for b in blocks))
        self.assertTrue(any(b.get("kind") == "table" for b in blocks))
        self.assertEqual(translate.reassemble_doc(blocks, []), md)

    def test_blockquote_prefix_preserved(self):        # RG-33
        blocks = translate.split_doc("> Quote line")
        prose = [b for b in blocks if b["type"] == "prose"]
        self.assertEqual(len(prose), 1)
        self.assertEqual(prose[0]["prefix"], "> ")
        self.assertEqual(prose[0]["text"], "Quote line")
        self.assertEqual(translate.reassemble_doc(blocks, ["인용 줄"]), "> 인용 줄")

    def test_setext_underline_verbatim(self):          # RG-36
        md = "Title\n=====\n\nBody"
        blocks = translate.split_doc(md)
        self.assertTrue(any(b["type"] == "verbatim" and set(b["text"]) == {"="} for b in blocks))
        self.assertEqual(self._prose_texts(blocks), ["Title", "Body"])
        self.assertEqual(self._identity(md), md)

    def test_html_block_verbatim(self):                # RG-37
        blocks = translate.split_doc("<div>\nHello\n</div>")
        self.assertTrue(any(b["type"] == "verbatim" and b["text"] == "<div>" for b in blocks))
        self.assertTrue(any(b["type"] == "verbatim" and b["text"] == "</div>" for b in blocks))

    def test_crlf_normalized_to_lf(self):              # RG-35
        blocks = translate.split_doc("# Title\r\n\r\nBody\r\n")
        self.assertTrue(all("\r" not in b.get("text", "") for b in blocks))

    def test_existing_structure_still_roundtrips(self):    # regression: original doc test invariant
        md = "# Title\n\nHello world.\n\n```\ncode\n```\n\n- item one"
        self.assertEqual(self._identity(md), md)


class DocTablesNote(unittest.TestCase):               # Q5: stderr note when tables kept verbatim (L4)
    def setUp(self):
        self._orig = translate.call_once
        translate.call_once = lambda *a, **k: ("번역", "", "stop")

    def tearDown(self):
        translate.call_once = self._orig

    def test_table_note_and_crlf_note_on_stderr(self):
        buf, err = io.StringIO(), io.StringIO()
        with redirect_stdout(buf), redirect_stderr(err):
            translate.run_doc("Intro\r\n\r\n| A | B |\r\n|---|---|\r\n| 1 | 2 |\r\n", opts(max_retries=0), ("k", "https://e", "m"))
        e = err.getvalue()
        self.assertIn("table", e.lower())                      # tables-kept-untranslated note
        self.assertIn("CRLF", e)                               # CRLF note


class FewShot(unittest.TestCase):                     # Q6/P2/LV-14: worked examples in the prompt
    def test_translate_prompt_has_worked_examples(self):
        sys_p = translate.build_system("translate", None, {}, [], False)
        self.assertIn("예시", sys_p)
        self.assertIn("{file}을(를)", sys_p)                    # dual-form josa example
        self.assertIn("%d", sys_p)                             # printf-preservation / counter example

    def test_polish_prompt_has_meaning_preservation_example(self):
        sys_p = translate.build_system("polish", None, {}, [], False)
        self.assertIn("예시", sys_p)
        self.assertIn("의미", sys_p)                            # meaning-preservation note

    def test_translate_and_polish_fewshot_differ(self):
        self.assertNotEqual(translate.build_system("translate", None, {}, [], False),
                            translate.build_system("polish", None, {}, [], False))


class AutoMaxTokens(unittest.TestCase):               # Q6: short tier 8192->4096 (L4)
    def test_short_tier_is_4096(self):
        self.assertEqual(translate.auto_max_tokens("short", 0), 4096)
        self.assertEqual(translate.auto_max_tokens("a" * 200, 0), 4096)

    def test_other_tiers_unchanged(self):
        self.assertEqual(translate.auto_max_tokens("a" * 201, 0), 12000)
        self.assertEqual(translate.auto_max_tokens("a" * 800, 0), 12000)
        self.assertEqual(translate.auto_max_tokens("a" * 801, 0), 16384)
        self.assertEqual(translate.auto_max_tokens("a" * 2000, 0), 16384)
        self.assertEqual(translate.auto_max_tokens("a" * 2001, 0), 24000)

    def test_explicit_override_wins(self):
        self.assertEqual(translate.auto_max_tokens("short", 5000), 5000)


class ResidualFixes(unittest.TestCase):
    """Residual-fix wave (X1–X5 evidence). One class per item; TDD."""

    # ── Item 1: ICU-aware parity (X1/X3/X5) ──────────────────────────────────
    def test_icu_drop_all_placeholders_flags(self):           # X1/X3: dropped wrapper
        src = "{count, plural, one {# file} other {# files}}"
        self.assertIn("placeholder_mismatch", types(translate.postcheck(src, "파일")))

    def test_icu_rename_variable_flags(self):                 # X1: renamed runtime var
        src = "{count, plural, one {# file} other {# files}}"
        out = "{total, plural, one {# 파일} other {# 파일}}"
        self.assertIn("placeholder_mismatch", types(translate.postcheck(src, out)))

    def test_icu_dropped_sibling_placeholder_flags(self):     # X5: {user} dropped
        src = "{n, plural, one {# item} other {# items}} by {user}"
        out = "{n, plural, one {항목 #개} other {항목 #개}}"
        self.assertIn("placeholder_mismatch", types(translate.postcheck(src, out)))

    def test_icu_missing_printf_sibling_flags(self):          # X3: %d dropped next to ICU
        src = "{count, plural, other {# files}} %d"
        out = "{count, plural, other {# 파일}}"
        self.assertIn("placeholder_mismatch", types(translate.postcheck(src, out)))

    def test_icu_leaf_translation_still_clean(self):          # regression: leaf text may change
        src = "{count, plural, other {# files}}"
        out = "{count, plural, other {# 파일}}"
        self.assertNotIn("placeholder_mismatch", types(translate.postcheck(src, out)))

    def test_icu_category_legacy_skip_preserved(self):        # Q2 test relies on this
        self.assertNotIn("placeholder_mismatch",
                         types(translate.postcheck("{n, plural, other {#}}", "기타", category="icu")))

    # ── Item 1b: ICU head-parity tightening (exact var/keyword 1:1, plain multiset) ──
    def test_icu_wrapper_downgraded_to_bare_var_flags(self):   # (1) wrapper -> bare {var}
        src = "{count, plural, one{one file} other{# files}}"
        out = "{count}"
        self.assertIn("placeholder_mismatch", types(translate.postcheck(src, out)))

    def test_icu_var_rename_substring_not_a_pass(self):        # (2) n -> count; n ⊂ count must not pass
        src = "{n, plural, one{one file} other{# files}}"
        out = "{count, plural, one{파일 하나} other{#개 파일}}"
        self.assertIn("placeholder_mismatch", types(translate.postcheck(src, out)))

    def test_icu_select_var_rename_substring_not_a_pass(self):  # (3) user -> username; user ⊂ username
        src = "{user, select, admin{Admin} other{User}}"
        out = "{username, select, admin{관리자} other{사용자}}"
        self.assertIn("placeholder_mismatch", types(translate.postcheck(src, out)))

    def test_icu_plain_sibling_replaced_by_dup_flags(self):    # (4) non-ICU {user} replaced by dup {count}
        src = "{count, plural, one{one file} other{# files}} for {user}"
        out = "{count, plural, one{파일 하나} other{#개 파일}} for {count}"
        self.assertIn("placeholder_mismatch", types(translate.postcheck(src, out)))

    def test_icu_multiple_wrappers_leaf_edits_clean(self):     # positive control: 2 wrappers, leaf edits ok
        src = "{count, plural, one {# file} other {# files}} and {role, select, admin {Admin} other {User}}"
        out = "{count, plural, one {파일 #개} other {파일 #개}} 및 {role, select, admin {관리자} other {사용자}}"
        self.assertNotIn("placeholder_mismatch", types(translate.postcheck(src, out)))

    def test_icu_plus_printf_sibling_preserved_clean(self):    # positive control: ICU wrapper + printf, reordered
        src = "{count, plural, one {# file} other {# files}} on %s"
        out = "%s에서 {count, plural, one {파일 하나} other {#개 파일}}"
        self.assertNotIn("placeholder_mismatch", types(translate.postcheck(src, out)))

    # ── Item 2: orphan closing braces (X1) ───────────────────────────────────
    def test_scan_braces_emits_orphan_closer(self):
        self.assertEqual(translate._scan_braces("{x}}"), ["{x}", "}"])
        self.assertEqual(translate._scan_braces("}{x}"), ["}", "{x}"])

    def test_orphan_brace_is_placeholder_mismatch(self):
        for out in ["사용 {x}}", "}{x}", "{x}}"]:
            self.assertIn("placeholder_mismatch", types(translate.postcheck("Use {x}", out)), out)

    def test_balanced_nested_braces_no_orphan(self):          # regression
        self.assertEqual(translate._scan_braces("{a {b}}"), ["{a {b}}"])

    # ── Item 3: hallucinated sentinels with empty mapping (X1/X5) ─────────────
    def test_residual_sentinel_flagged_without_mapping(self):
        self._orig = translate.call_once
        translate.call_once = lambda *a, **k: ("번역 ⟦0⟧", "", "stop")
        try:
            r = translate.translate_one("원문", opts(max_retries=0), ("k", "https://e", "m"))
        finally:
            translate.call_once = self._orig
        self.assertIn("mask_integrity", types(r["flags"]))
        self.assertEqual(r["status"], "broken")

    def test_unmask_verified_flags_residual_empty_mapping(self):
        _, ok = translate.unmask_verified("안녕 ⟦0⟧", [])
        self.assertFalse(ok)
        _, ok2 = translate.unmask_verified("안녕하세요", [])
        self.assertTrue(ok2)

    # ── Item 4: collision-free U+E000 escape (X1) ────────────────────────────
    def test_literal_u_e000_roundtrips(self):
        src = "값  and `code` plus https://e.test"
        masked, mapping = translate.mask_protected(src)
        restored, ok = translate.unmask_verified(masked, mapping)
        self.assertEqual(restored, src)
        self.assertTrue(ok)

    def test_mask_open_literal_still_roundtrips(self):        # regression
        src = "Literal ⟦0⟧ plus https://example.com"
        masked, mp = translate.mask_protected(src)
        self.assertEqual(translate.unmask(masked, mp), src)

    def test_mangled_escape_char_flagged(self):              # lone escape char = mangled sentinel
        _, ok = translate.unmask_verified("안녕  세계", [])
        self.assertFalse(ok)

    # ── Item 5: josa_after_var gaps (X2) ─────────────────────────────────────
    def test_josa_single_form_families_flag(self):
        for out in ["{x}이나 선택", "{x}나 선택", "{x}이랑 공유", "{x}랑 공유",
                    "{x}이란 값", "{x}란 값", "{x}이에요", "{x}예요"]:
            self.assertIn("josa_after_var", types(translate.postcheck("Choose {x}", out)), out)

    def test_josa_paren_families_valid(self):
        for out in ["{x}(이)나 선택", "{x}(이)랑 공유", "{x}(이)란 값", "{x}(이)에요", "{x}(으)로 이동"]:
            self.assertNotIn("josa_after_var", types(translate.postcheck("x", out)), out)

    def test_josa_malformed_paren_ro_flags(self):
        for out in ["{x}(으로) 이동", "{x}(으) 로 이동", "{x}으(로) 이동", "{x}(으 로 이동"]:
            self.assertIn("josa_after_var", types(translate.postcheck("Move to {x}", out)), out)

    def test_josa_printf_all_specs(self):                    # align with _PRINTF_RE
        self.assertIn("josa_after_var", types(translate.postcheck("x", "%1$s을 이동")))
        self.assertIn("josa_after_var", types(translate.postcheck("x", "%12d를 표시")))

    def test_josa_word_start_after_var_no_fp(self):          # regression
        self.assertNotIn("josa_after_var", types(translate.postcheck("x", "{x}나머지 계속")))
        self.assertNotIn("josa_after_var", types(translate.postcheck("x", "{app} 로딩 중…")))

    # ── Item 6: double_passive negation FP (X3) ──────────────────────────────
    def test_double_passive_negation_not_flagged(self):
        self.assertNotIn("double_passive", types(translate.postcheck("x", "이 기능은 더 이상 쓰이지 않습니다")))
        self.assertNotIn("double_passive", types(translate.postcheck("x", "파일이 삭제되어 지지 않습니다")))

    def test_double_passive_still_flags_real(self):          # regression
        self.assertIn("double_passive", types(translate.postcheck("x", "창이 자동으로 닫혀집니다")))
        self.assertIn("double_passive", types(translate.postcheck("x", "당신에 의해 삭제되어진 파일")))

    # ── Item 7: auto_dnt position stripping (X3) ─────────────────────────────
    def test_auto_dnt_quoted_initial_excluded(self):
        a = translate.auto_dnt(['"Anthropic" is available.', '"Anthropic" supports SSO.'])
        self.assertNotIn("Anthropic", a)

    def test_auto_dnt_bullet_initial_excluded(self):
        a = translate.auto_dnt(["- Profile settings are available.", "- Profile pictures sync."])
        self.assertNotIn("Profile", a)

    def test_auto_dnt_mid_sentence_still_pinned(self):       # regression
        a = translate.auto_dnt(["Use Anthropic here.", "Enable Anthropic now."])
        self.assertIn("Anthropic", a)

    def test_auto_dnt_numbered_list_initial_excluded(self):
        a = translate.auto_dnt(["1. Anthropic ships today.", "1. Anthropic ships again."])
        self.assertNotIn("Anthropic", a)

    # ── Item 8: DNT-in-URL false satisfaction (X5) ───────────────────────────
    def test_dnt_survives_only_in_url_still_flags(self):
        self._orig = translate.call_once
        translate.call_once = lambda *a, **k: ("앤트로픽 열기 ⟦0⟧", "", "stop")
        try:
            r = translate.translate_one("Open Anthropic at https://docs.example/Anthropic",
                                  opts(terms={"Anthropic": "Anthropic"}, max_retries=0),
                                  ("k", "https://e", "m"))
        finally:
            translate.call_once = self._orig
        self.assertIn("dnt_violation", types(r["flags"]))

    def test_dnt_visible_retained_clean(self):              # regression: real retention passes
        flags = translate.postcheck("Open Anthropic", "Anthropic 열기",
                              {"Anthropic": "Anthropic"}, mapping=["https://x/Anthropic"])
        self.assertNotIn("dnt_violation", types(flags))

    # ── Item 9: batch crash path (X5) ────────────────────────────────────────
    def test_batch_malformed_row_is_error_not_crash(self):
        d = tempfile.mkdtemp()
        inp, outp = os.path.join(d, "i.jsonl"), os.path.join(d, "o.jsonl")
        with open(inp, "w", encoding="utf-8") as fh:
            fh.write("[]\n")
        self._orig = translate.call_once
        translate.call_once = lambda *a, **k: ("번역", "", "stop")
        buf, err = io.StringIO(), io.StringIO()
        try:
            with redirect_stdout(buf), redirect_stderr(err):
                rc = translate.run_batch(opts(in_path=inp, out_path=outp), ("k", "https://e", "m"))
        finally:
            translate.call_once = self._orig
        with open(outp, encoding="utf-8") as fh:
            row = json.loads(fh.readline())
        self.assertEqual(row["status"], "error")
        self.assertTrue(all(f.get("severity") for f in row["flags"]))
        self.assertEqual(rc, 1)

    def test_batch_malformed_row_dry_run_no_crash(self):
        d = tempfile.mkdtemp()
        inp, outp = os.path.join(d, "i.jsonl"), os.path.join(d, "o.jsonl")
        with open(inp, "w", encoding="utf-8") as fh:
            fh.write("[]\n")
        buf, err = io.StringIO(), io.StringIO()
        with redirect_stdout(buf), redirect_stderr(err):
            rc = translate.run_batch(opts(in_path=inp, out_path=outp, dry_run=True), ("k", "https://e", "m"))
        self.assertEqual(rc, 0)
        self.assertIn("error", json.loads(buf.getvalue())[0])

    # ── Item 10: fix_note purity (X5) ────────────────────────────────────────
    def test_fix_note_passive_only_mentions_passive(self):
        note = translate.fix_note([{"type": "passive_eui", "severity": "major"}], [], None)
        for forbidden in ("대명사", "들", "하나의", "그리고"):
            self.assertNotIn(forbidden, note)
        self.assertIn("능동", note)

    def test_fix_note_double_passive_only_mentions_passive(self):
        note = translate.fix_note([{"type": "double_passive", "severity": "major"}], [], None)
        for forbidden in ("대명사", "하나의", "그리고"):
            self.assertNotIn(forbidden, note)

    # ── Item 11: mid-doc thematic break (X4) ─────────────────────────────────
    def test_mid_doc_hr_dash_verbatim(self):
        blocks = translate.split_doc("Intro\n\n---\n\nAfter")
        prose = [b["text"] for b in blocks if b["type"] == "prose"]
        self.assertEqual(prose, ["Intro", "After"])
        self.assertTrue(any(b["type"] == "verbatim" and b["text"] == "---" for b in blocks))

    def test_mid_doc_hr_underscore_and_star_verbatim(self):
        for rule in ["___", "***", "* * *"]:
            blocks = translate.split_doc("A\n\n" + rule + "\n\nB")
            self.assertTrue(any(b["type"] == "verbatim" and b["text"] == rule for b in blocks), rule)
            self.assertEqual([b["text"] for b in blocks if b["type"] == "prose"], ["A", "B"])

    def test_setext_still_wins_over_hr(self):               # regression
        blocks = translate.split_doc("Heading\n---\n\nBody")
        self.assertIn("Heading", [b["text"] for b in blocks if b["type"] == "prose"])

    def test_hr_doc_roundtrips(self):
        md = "Intro\n\n---\n\nAfter"
        blocks = translate.split_doc(md)
        self.assertEqual(translate.reassemble_doc(blocks, [b["text"] for b in blocks if b["type"] == "prose"]), md)

    # ── Item 12: URL stops before ] ──────────────────────────────────────────
    def test_url_stops_before_square_bracket(self):
        _, mp = translate.mask_protected("See [https://example.com] now")
        self.assertEqual(mp, ["https://example.com"])


class ProseSafety(unittest.TestCase):
    """Prose-safety wave (literary-prose incident 2026-07-02). Quote protection in polish, quote-parity
    gate, minimal-edit contract, register shield. One class per requirement; TDD."""

    # ── 1. quote protection in polish mode (mask quoted spans) ────────────────
    def test_polish_masks_straight_quoted_span(self):
        masked, mapping = translate.mask_protected('그는 "앉아요." 라고 했다', mask_quotes=True)
        self.assertNotIn("앉아요", masked)
        self.assertIn('"앉아요."', mapping)
        self.assertEqual(translate.unmask(masked, mapping), '그는 "앉아요." 라고 했다')

    def test_default_does_not_mask_quotes(self):        # translate/back-compat: quotes stay visible
        masked, _ = translate.mask_protected('그는 "앉아요." 라고 했다')
        self.assertIn('"앉아요."', masked)

    def test_polish_masks_curly_and_corner_and_book_quotes(self):
        src = '“곡선” 그리고 「대사」 그리고 『책』'
        masked, mapping = translate.mask_protected(src, mask_quotes=True)
        for q in ("“곡선”", "「대사」", "『책』"):
            self.assertNotIn(q, masked)
            self.assertIn(q, mapping)
        self.assertEqual(translate.unmask(masked, mapping), src)

    def test_quote_masked_after_code_before_url(self):  # ordering: code → quotes → URLs
        # a quote wrapping a URL becomes ONE span; the URL is not separately masked
        masked, mapping = translate.mask_protected('"see https://x.io/docs"', mask_quotes=True)
        self.assertEqual(mapping, ['"see https://x.io/docs"'])
        self.assertEqual(masked.count("⟦"), 1)

    def test_polish_quote_wrapping_inline_code_roundtrips(self):   # nested sentinels (code inside quote)
        src = '그는 "`npm i` 실행" 이라 했다'
        masked, mapping = translate.mask_protected(src, mask_quotes=True)
        self.assertNotIn("npm i", masked)
        restored, ok = translate.unmask_verified(masked, mapping)
        self.assertEqual(restored, src)
        self.assertTrue(ok)

    def test_polish_masks_dialogue_survives_verbatim(self):   # end-to-end: register cannot touch dialogue
        src = '그는 말했다. "앉아요." 그리고 조용해졌다.'
        self._orig = translate.call_once

        def stub(endpoint, system, text, *a, **k):        # model rewrites narration, keeps the sentinel
            return (text.replace("그는 말했다", "그가 말했다").replace("조용해졌다", "조용해진다"), "", "stop")
        translate.call_once = stub
        try:
            r = translate.translate_one(src, opts(mode="polish", register="handa"), ("k", "https://e", "m"))
        finally:
            translate.call_once = self._orig
        self.assertIn('"앉아요."', r["translation"])       # dialogue verbatim, quotes intact
        self.assertNotIn("quote_mismatch", types(r["flags"]))

    # ── 2. quote-parity gate ──────────────────────────────────────────────────
    def test_quote_mismatch_critical_in_polish(self):
        flags = translate.postcheck('그는 "앉아요"라고 했다', "그는 앉는다고 했다", mode="polish")
        self.assertIn("quote_mismatch", types(flags))
        q = [f for f in flags if f["type"] == "quote_mismatch"][0]
        self.assertEqual(q["severity"], "critical")

    def test_quote_mismatch_major_in_translate(self):
        flags = translate.postcheck('He said "sit".', "그는 앉으라고 했다", mode="translate")
        self.assertIn("quote_mismatch", types(flags))
        q = [f for f in flags if f["type"] == "quote_mismatch"][0]
        self.assertEqual(q["severity"], "major")

    def test_quote_parity_holds_no_flag(self):
        flags = translate.postcheck('그는 "앉아요"라고 했다', '그는 "앉아요"라고 말했다', mode="polish")
        self.assertNotIn("quote_mismatch", types(flags))

    def test_quote_mismatch_no_source_quotes_no_flag(self):
        self.assertNotIn("quote_mismatch", types(translate.postcheck("Sit down.", "앉으세요", mode="polish")))

    def test_quote_mismatch_corner_quotes_counted(self):
        flags = translate.postcheck("「대사」가 있다", "대사가 있다", mode="polish")
        self.assertIn("quote_mismatch", types(flags))

    def test_quote_parity_backcompat_default_mode(self):   # no mode arg → major, still detects
        flags = translate.postcheck('"a"', "번역")
        self.assertIn("quote_mismatch", types(flags))
        self.assertEqual([f for f in flags if f["type"] == "quote_mismatch"][0]["severity"], "major")

    # ── 3. minimal-edit contract (excessive_rewrite, advisory) ────────────────
    def test_excessive_rewrite_flags_big_polish_rewrite(self):
        flags = translate.postcheck("파일을 저장했어요", "완전히 다른 새 문장입니다 그리고 더 길어요",
                              mode="polish", max_diff_ratio=0.3)
        self.assertIn("excessive_rewrite", types(flags))
        self.assertEqual([f for f in flags if f["type"] == "excessive_rewrite"][0]["severity"], "major")

    def test_excessive_rewrite_not_flagged_small_edit(self):
        flags = translate.postcheck("파일을 저장했어요", "파일을 저장했습니다", mode="polish", max_diff_ratio=0.6)
        self.assertNotIn("excessive_rewrite", types(flags))

    def test_excessive_rewrite_only_polish(self):          # translate mode never computes diff ratio
        flags = translate.postcheck("Save the file.", "완전히 다른 번역", mode="translate", max_diff_ratio=0.1)
        self.assertNotIn("excessive_rewrite", types(flags))

    def test_excessive_rewrite_respects_threshold(self):   # same pair, tighter threshold → fires
        src, out = "파일을 저장했어요", "파일을 저장했습니다"
        self.assertNotIn("excessive_rewrite", types(translate.postcheck(src, out, mode="polish", max_diff_ratio=0.6)))
        self.assertIn("excessive_rewrite", types(translate.postcheck(src, out, mode="polish", max_diff_ratio=0.05)))

    def test_excessive_rewrite_is_advisory_never_retried(self):   # D1: advisory, not fixable
        self._orig = translate.call_once
        translate.call_once = lambda *a, **k: ("완전히 다른 아주 새로운 문장", "", "stop")
        try:
            r = translate.translate_one("짧은 원문", opts(mode="polish", fix=True, max_diff_ratio=0.1),
                                  ("k", "https://e", "m"))
        finally:
            translate.call_once = self._orig
        self.assertIn("excessive_rewrite", types(r["flags"]))
        self.assertEqual(r["tries"], 1)                    # advisory → no retry even with --fix

    def test_excessive_rewrite_counts_for_strict(self):    # advisory but --strict-visible (it is a flag)
        flags = translate.postcheck("원문", "완전히 다른 문장입니다", mode="polish", max_diff_ratio=0.1)
        self.assertTrue(any(f["type"] == "excessive_rewrite" for f in flags))

    # ── 4. register shield ────────────────────────────────────────────────────
    def test_polish_register_shield_present(self):
        sys_p = translate.build_system("polish", "handa", {}, [], False)
        self.assertIn("대사·인용문은 문체 변경 대상이 아니다", sys_p)

    def test_translate_has_no_register_shield(self):
        sys_p = translate.build_system("translate", "handa", {}, [], False)
        self.assertNotIn("대사·인용문은 문체 변경 대상이 아니다", sys_p)

    def test_polish_without_register_has_no_shield(self):   # shield rides the register restatement line
        sys_p = translate.build_system("polish", None, {}, [], False)
        self.assertNotIn("대사·인용문은 문체 변경 대상이 아니다", sys_p)

    # ── 5. --polish-dialogue exposes quotes to the model ──────────────────────
    def test_polish_dialogue_exposes_quotes_in_prompt_path(self):
        # with polish_dialogue=True, quotes are NOT masked → a model that drops them trips the gate
        self._orig = translate.call_once
        translate.call_once = lambda *a, **k: ("그는 앉는다고 했다", "", "stop")   # dropped both quotes
        try:
            r = translate.translate_one('그는 "앉아요"라고 했다', opts(mode="polish", polish_dialogue=True),
                                  ("k", "https://e", "m"))
        finally:
            translate.call_once = self._orig
        self.assertIn("quote_mismatch", types(r["flags"]))
        self.assertEqual(r["status"], "broken")            # critical in polish


class NaturalnessFloor(unittest.TestCase):
    """Korean Naturalness Floor v2.4 (internal guide) — MECHANICAL layer. Every heuristic here is
    ADVISORY (D1: warn only, never in the --fix fixable set). The comma tell is the machine signature
    measured by KatFishNet (ACL 2025). Each heuristic: positive tells + a keeps-list of negatives. TDD."""

    def test_floor_heuristics_are_advisory(self):          # D1: all new heuristics are advisory
        for t in ("comma_after_conn", "loan_transliteration", "stock_calque", "redundant_pair", "hollow_frame"):
            self.assertIn(t, translate.TRANSLATIONESE_TYPES, t)
            self.assertNotIn(t, translate.FIXABLE_HEURISTICS, t)

    # ── 1. comma_after_conn (the KatFishNet machine signature) ───────────────
    def test_comma_after_conn_flags(self):
        for out in ["이 값은 손대지 않고, 캐시를 다시 채우는 배치가 정리합니다",   # keep-case STILL flags (advisory)
                    "저장했지만, 곧 실패했습니다",
                    "명령을 실행해서, 결과를 확인합니다",
                    "설정하면서, 계속 진행합니다",
                    "확인했는데, 문제가 남았습니다",
                    "값이 크며, 처리가 느립니다",
                    "그래서, 재부팅했습니다"]:
            self.assertIn("comma_after_conn", types(translate.postcheck("x", out)), out)

    def test_comma_after_conn_keeps(self):                 # no comma after the ending / comma not after one
        for out in ["값을 손대지 않고 캐시를 다시 채웁니다",   # 고 without a following comma
                    "사과, 배, 감을 담았습니다",              # comma follows 과/배/감, not a connective ending
                    "메뉴, 설정, 도구를 엽니다"]:
            self.assertNotIn("comma_after_conn", types(translate.postcheck("x", out)), out)

    # ── 2. loan_transliteration ──────────────────────────────────────────────
    def test_loan_transliteration_flags(self):
        for out in ["리프레시 버튼을 누르세요",
                    "초안 대신 드래프트를 저장합니다",
                    "멘션 알림을 켭니다",
                    "프라이어 확률을 계산합니다",
                    "리마인드 설정을 확인합니다",
                    "회귀가 발생했습니다"]:
            self.assertIn("loan_transliteration", types(translate.postcheck("x", out)), out)

    def test_loan_transliteration_keeps(self):             # every keep needs a negative
        for out in ["리프레시 토큰을 갱신합니다",      # 리프레시 토큰 (compound) — not a standalone loan
                    "회귀 테스트를 실행합니다",         # regression test — legit CS term
                    "회귀 분석 결과를 검토합니다"]:      # regression analysis — legit CS term
            self.assertNotIn("loan_transliteration", types(translate.postcheck("x", out)), out)

    # ── 3. stock_calque ──────────────────────────────────────────────────────
    def test_stock_calque_flags(self):
        for out in ["이 문제에 대하여 논의합니다",
                    "보안에 대한 설명입니다",
                    "성능에 있어서 우수합니다",
                    "오류에도 불구하고 계속됩니다",
                    "관리자 승인을 필요로 하는 기능입니다",     # spec frame is '[를을] 필요로 하' (하-, not 합-)
                    "네트워크 오류로 인하여 실패했습니다",
                    "버그라고 할 수 있습니다",
                    "값을 변경하는 것이 가능하다"]:            # spec frame is '것이 가능하' (하-, not 합-)
            self.assertIn("stock_calque", types(translate.postcheck("x", out)), out)

    def test_stock_calque_keeps(self):
        for out in ["이 문제를 논의합니다",
                    "관리자 승인이 필요합니다",      # 필요하다, not 필요로 하다
                    "값을 변경할 수 있습니다"]:       # 할 수 있 without the '라고 할 수 있' frame
            self.assertNotIn("stock_calque", types(translate.postcheck("x", out)), out)

    def test_e_uihae_stays_in_its_own_fixable_heuristic(self):   # 에 의해 NOT moved into stock_calque
        flags = translate.postcheck("x", "관리자에 의해 삭제됩니다")
        self.assertIn("passive_eui", types(flags))
        self.assertNotIn("stock_calque", types(flags))
        self.assertIn("passive_eui", translate.FIXABLE_HEURISTICS)     # still the fixable passive heuristic

    # ── 4. redundant_pair + hollow_frame ─────────────────────────────────────
    def test_redundant_pair_flags(self):
        for out in ["다시 재시도합니다", "다시 재시작하세요", "다시 재확인합니다", "다시 재접속합니다",
                    "미리 예방합니다", "미리 사전 점검합니다", "남은 잔여 시간입니다", "서로 상호 작용합니다"]:
            self.assertIn("redundant_pair", types(translate.postcheck("x", out)), out)

    def test_redundant_pair_keeps(self):
        for out in ["다시 시도합니다",       # 시도 without the redundant 재
                    "미리 확인합니다",       # 확인 not in the 예방|사전 pair
                    "잔여 시간입니다",       # 잔여 without the redundant 남은
                    "상호 작용합니다"]:      # 상호 without the redundant 서로
            self.assertNotIn("redundant_pair", types(translate.postcheck("x", out)), out)

    def test_hollow_frame_flags(self):
        for out in ["확인하는 것이 필요합니다",
                    "곧 업데이트가 있을 예정입니다",
                    "정기 점검이 있을 예정입니다"]:
            self.assertIn("hollow_frame", types(translate.postcheck("x", out)), out)

    def test_hollow_frame_keeps(self):
        for out in ["확인이 필요합니다",     # 필요 without the 는 것이 frame
                    "내일 배포됩니다",       # no 있을 예정 frame
                    "지금 바로 확인합니다"]:
            self.assertNotIn("hollow_frame", types(translate.postcheck("x", out)), out)

    # ── 5. connective NARROWED (was: bare 그리고|그러나|그래서 anywhere — a known FP source) ──
    def test_connective_narrowed_flags(self):              # only the sentence-initial + comma machine tell
        for out in ["그리고, 저장합니다",
                    "작업을 마쳤습니다. 그러나, 오류가 남았습니다",
                    "완료했습니다. 따라서, 종료합니다",
                    "확인했습니다. 또한, 검토합니다",
                    "시도했습니다. 하지만, 실패했습니다"]:
            self.assertIn("connective", types(translate.postcheck("x", out)), out)

    def test_connective_narrowed_keeps(self):              # the FPs the narrowing removes
        for out in ["그리고 계속 진행합니다",             # sentence-initial but NO comma → no longer flags
                    "이것을 하고 그리고 저것을 합니다",    # mid-sentence 그리고 → no longer flags
                    "사과 그리고 배를 샀습니다"]:          # mid-sentence → no longer flags
            self.assertNotIn("connective", types(translate.postcheck("x", out)), out)


class NaturalnessFloorPrompt(unittest.TestCase):          # floor distillation in the system prompt
    def test_polish_prompt_has_floor_distillation(self):
        sys_p = translate.build_system("polish", None, {}, [], False)
        self.assertIn("최소 편집", sys_p)                   # 한 번만 점검, 최소 편집
        self.assertIn("부정어", sys_p)                     # 부정어·한정어·헤지 보존
        self.assertIn("주장 강도", sys_p)                  # 사실·수치·주장 강도 불변
        self.assertIn("쉼표", sys_p)                       # 연결어미 뒤 쉼표 금지

    def test_translate_prompt_has_connective_comma_rule(self):
        sys_p = translate.build_system("translate", None, {}, [], False)
        self.assertIn("연결어미(-고/-서/-며) 뒤에 쉼표를 찍지 않는다", sys_p)

    def test_translate_prompt_lacks_polish_floor(self):    # the multi-line distillation is polish-only
        sys_p = translate.build_system("translate", None, {}, [], False)
        self.assertNotIn("최소 편집", sys_p)


if __name__ == "__main__":
    unittest.main(verbosity=2)
