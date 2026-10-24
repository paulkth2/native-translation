#!/usr/bin/env python3
"""Build a Claude-vs-Codex side-by-side HTML for native review. Writes ../cmp_report.html."""
import html
import json
import pathlib


def load(p):
    return {json.loads(l)["id"]: json.loads(l) for l in open(p, encoding="utf-8") if l.strip()}


# id -> (lean, note)  for the divergent rows (my informed, non-native read)
NOTES = {
    "err-004": ("Claude", "‘undone’→되돌리다(revert) is faithful; Codex 취소(cancel) is a different action."),
    "cfm-001": ("Codex", "‘정말’ conveys ‘Are you sure’; Claude dropped the emphasis."),
    "cfm-003": ("Codex", "‘discard’→버리다(Codex) is closer than 취소(cancel)(Claude)."),
    "josa-001": ("Tie", "passive 삭제되었습니다(Codex) vs active 삭제했습니다(Claude); both natural."),
    "josa-002": ("Codex", "⚠ Claude ‘[으]로’ is MALFORMED; Codex ‘(으)로’ is the correct dual form."),
    "josa-004": ("Claude", "{user}님이 (honorific + safe josa) reads better; Codex used [이/가], dropped 님."),
    "cnt-002": ("Codex", "‘항목 N개’ is the natural counter order; ‘N개 항목’ less standard."),
    "cnt-003": ("Codex", "⚠ Claude DROPPED the ICU plural wrapper (l10n bug); Codex kept it (extra ‘입니다’ is a nit)."),
    "cnt-004": ("Tie", "‘N명이’ vs ‘N명’ — sentence vs label style; both fine."),
    "ph-001": ("Native", "환영합니다(Codex, standard) vs 어서 오세요(Claude, warmer) — brand-voice call."),
    "ph-002": ("Claude", "‘Last’→마지막(Claude) accurate; Codex 최근(recent) drifts in meaning."),
    "sts-004": ("Codex", "‘Enabled’→사용 설정됨(Codex) closer than 사용 중(Claude, ‘in use’)."),
    "tns-002": ("Native", "멤버(loanword) vs 구성원(native) — house-term call; both used 있다 (correct)."),
    "tns-003": ("Native", "멘션(Codex, idiomatic) + 나 vs 언급+회원님(Claude, polite) — idiom vs politeness."),
    "seg-001": ("Codex", "Codex split into 2 sentences (re-segmentation rule); Claude used ~며."),
    "seg-002": ("Native", "수신(Codex, idiomatic ‘알림 수신’) vs 전송(Claude, literal ‘delivered’)."),
    "ntf-001": ("Codex", "Codex dropped ‘your’ (pro-drop); Claude kept 회원님의 (slightly redundant)."),
}
COLOR = {"Claude": "#56b6c2", "Codex": "#c678dd", "Tie": "#999", "Native": "#e5a000"}


def main():
    base = pathlib.Path(__file__).resolve().parent
    C, X = load(base / "out/score_claude.jsonl"), load(base / "out/score_codex.jsonl")
    rows = ""
    tally = {"Claude": 0, "Codex": 0, "Tie": 0, "Native": 0}
    for i, c in C.items():
        x = X[i]
        diff = c["translation"].strip() != x["translation"].strip()
        if not diff:
            continue
        lean, note = NOTES.get(i, ("Native", ""))
        tally[lean] += 1
        rows += (f'<tr><td class="id">{i}</td><td class="src">{html.escape(c["source_en"])}</td>'
                 f'<td class="ko">{html.escape(c["translation"])}</td>'
                 f'<td class="ko">{html.escape(x["translation"])}</td>'
                 f'<td style="color:{COLOR[lean]};font-weight:700">{lean}</td>'
                 f'<td class="note">{html.escape(note)}</td></tr>')
    chips = " ".join(f'<span style="background:{COLOR[k]}22;color:{COLOR[k]};padding:2px 8px;'
                     f'border-radius:6px">{k}: {v}</span>' for k, v in tally.items())
    out = base.parent / "cmp_report.html"
    out.write_text(f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<title>Claude vs Codex — En→Ko (native review)</title><style>
 body{{font-family:Pretendard,"Noto Sans KR",system-ui,sans-serif;max-width:1100px;margin:2rem auto;padding:0 1rem;line-height:1.55}}
 h1{{font-size:1.4rem}} .meta{{color:#666;font-size:.9rem}}
 table{{border-collapse:collapse;width:100%;margin-top:1rem;font-size:.92rem}}
 td,th{{border-bottom:1px solid #eee;padding:.5rem;vertical-align:top;text-align:left}}
 th{{border-bottom:2px solid #ddd}} .id{{font-family:ui-monospace,monospace;color:#888;font-size:.8rem;white-space:nowrap}}
 .src{{color:#555;max-width:230px}} .ko{{max-width:260px}} .note{{color:#666;font-size:.86rem;max-width:300px}}
 .claude{{background:#56b6c20d}} .codex{{background:#c678dd0d}}
</style></head><body>
<h1>Claude vs. Codex — English→Korean, product/UI</h1>
<p class="meta">Identical scaffold, single pass, 41 strings. <b>24 identical, 17 differ</b> (shown).
 Both = <b>100% clean on the heuristic gate</b> → decided on naturalness/correctness only.
 Lean is my <i>non-native</i> read — you are the authority. Tally: {chips}</p>
<table><tr><th>id</th><th>source (EN)</th><th class="claude">Claude</th><th class="codex">Codex</th><th>lean</th><th>note</th></tr>
{rows}</table>
<p class="meta">⚠ = real localization mechanic error (not just style). The two ⚠ rows (Claude malformed josa
 marker; Claude dropped ICU wrapper) are partly a <b>skill gap</b> — pin marker syntax + forbid dropping ICU —
 fixable for either engine.</p>
</body></html>""", encoding="utf-8")
    print("tally:", tally)
    print("wrote", out)


if __name__ == "__main__":
    main()
