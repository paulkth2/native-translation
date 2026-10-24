#!/usr/bin/env python3
"""
visualize.py — visualize the naive-vs-scaffold method-lift difference.

Prints an ASCII comparison and writes a self-contained HTML report (no JS/deps).

    python visualize.py --naive out/claude_naive.jsonl --scaffold out/claude_scaffold.jsonl \
                        --out ../report.html
"""
from __future__ import annotations
import argparse
import collections
import html
import json
import pathlib

import score

SEV_MAJOR = ("major", "critical")


def load_scored(path):
    rows = []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        r["_flags"] = [f for f in score.heuristic_flags(r) if f["severity"] != "info"]
        r["_major"] = any(f["severity"] in SEV_MAJOR for f in r["_flags"])
        rows.append(r)
    return rows


def clean_rate(rows):
    return round(100 * sum(1 for r in rows if not r["_major"]) / len(rows), 1) if rows else 0.0


def ascii_bar(pct, width=24):
    fill = round(width * pct / 100)
    return "█" * fill + "·" * (width - fill)


# --------------------------------------------------------------------------- #
def render_html(naive, scaffold, by_id_s):
    nc, sc = clean_rate(naive), clean_rate(scaffold)
    flag_counts = collections.Counter(f["type"] for r in naive for f in r["_flags"])
    n_major = sum(1 for r in naive if r["_major"])
    s_major = sum(1 for r in scaffold if r["_major"])

    # per-category clean%
    cats = {}
    for r in naive:
        cats.setdefault(r["category"], [0, 0])[0] += 0 if r["_major"] else 1
    cat_tot = collections.Counter(r["category"] for r in naive)

    def bar(pct, color):
        return (f'<div class="track"><div class="fill" style="width:{pct}%;background:{color}">'
                f'</div><span class="lbl">{pct}%</span></div>')

    # flag-type rows
    flag_rows = "".join(
        f'<tr><td class="ft">{html.escape(t)}</td>'
        f'<td class="num">{c}</td><td class="num">0</td>'
        f'<td>{"▆" * min(c, 30)}</td></tr>'
        for t, c in flag_counts.most_common()
    )

    # per-category rows
    cat_rows = ""
    for cat in sorted(cat_tot):
        clean_n = cats.get(cat, [0, 0])[0]
        pct_n = round(100 * clean_n / cat_tot[cat])
        cat_rows += (f'<tr><td>{html.escape(cat)}</td>'
                     f'<td class="cell">{bar(pct_n, "#e06c75")}</td>'
                     f'<td class="cell">{bar(100, "#56b6c2")}</td></tr>')

    # example rows (where naive had a major flag)
    ex = ""
    for r in naive:
        if not r["_major"]:
            continue
        s = by_id_s.get(r["id"], {})
        tags = " ".join(f'<span class="tag">{html.escape(f["type"])}</span>'
                        for f in r["_flags"])
        ex += (f'<tr><td class="src">{html.escape(r["source_en"])}</td>'
               f'<td class="bad">{html.escape(r["translation"])}<br>{tags}</td>'
               f'<td class="good">{html.escape(s.get("translation", ""))}</td></tr>')

    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<title>En→Ko method-lift: naive vs skill scaffold</title>
<style>
 body{{font-family:Pretendard,"Noto Sans KR",system-ui,sans-serif;max-width:960px;margin:2rem auto;padding:0 1rem;color:#222;line-height:1.6}}
 h1{{font-size:1.5rem}} h2{{margin-top:2.2rem;border-bottom:2px solid #eee;padding-bottom:.3rem}}
 .cards{{display:flex;gap:1rem;flex-wrap:wrap}}
 .card{{flex:1;min-width:180px;border:1px solid #e5e5e5;border-radius:10px;padding:1rem;text-align:center}}
 .big{{font-size:2rem;font-weight:700}} .up{{color:#56b6c2}} .down{{color:#e06c75}}
 table{{border-collapse:collapse;width:100%;margin-top:.6rem;font-size:.92rem}}
 td,th{{border-bottom:1px solid #eee;padding:.45rem .5rem;vertical-align:top;text-align:left}}
 .num{{text-align:right;font-variant-numeric:tabular-nums}} .ft{{font-family:ui-monospace,monospace}}
 .track{{position:relative;background:#f1f1f1;border-radius:5px;height:20px;min-width:120px}}
 .fill{{height:20px;border-radius:5px}} .lbl{{position:absolute;right:6px;top:0;font-size:.78rem;line-height:20px}}
 .cell{{width:42%}} .src{{color:#555}} .bad{{color:#b3261e}} .good{{color:#0a7d3c}}
 .tag{{display:inline-block;background:#fde8e6;color:#b3261e;border-radius:4px;padding:1px 6px;font-size:.72rem;margin:2px 2px 0 0;font-family:ui-monospace,monospace}}
 .note{{color:#777;font-size:.85rem}}
</style></head><body>
<h1>English→Korean: naive vs. skill scaffold + two-pass polish</h1>
<p class="note">Single model: <b>Claude Opus 4.8</b>, {len(naive)} product/UI strings. Score = objective
 번역투/register/placeholder/josa heuristics (regex, no model bias). This is a method-lift demo, not the
 multi-model bake-off; COMET/LLM-judge add fidelity but need keys/libs.</p>
<div class="cards">
 <div class="card"><div>clean rate</div><div class="big"><span class="down">{nc}%</span> → <span class="up">{sc}%</span></div><div class="note">rows with no major issue</div></div>
 <div class="card"><div>rows with a major flag</div><div class="big"><span class="down">{n_major}</span> → <span class="up">{s_major}</span></div></div>
 <div class="card"><div>total 번역투/bug flags</div><div class="big"><span class="down">{sum(flag_counts.values())}</span> → <span class="up">0</span></div></div>
</div>
<h2>Flags removed by the scaffold</h2>
<table><tr><th>flag type</th><th class="num">naive</th><th class="num">scaffold</th><th>naive count</th></tr>{flag_rows}</table>
<h2>Clean rate by category</h2>
<table><tr><th>category</th><th class="cell">naive</th><th class="cell">scaffold</th></tr>{cat_rows}</table>
<h2>Before → after (rows the scaffold fixed)</h2>
<table><tr><th>source (EN)</th><th>naive (❌ flags)</th><th>scaffold (✅)</th></tr>{ex}</table>
</body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--naive", required=True)
    ap.add_argument("--scaffold", required=True)
    ap.add_argument("--out", default="report.html")
    args = ap.parse_args()

    naive = load_scored(args.naive)
    scaffold = load_scored(args.scaffold)
    by_id_s = {r["id"]: r for r in scaffold}

    nc, sc = clean_rate(naive), clean_rate(scaffold)
    flag_counts = collections.Counter(f["type"] for r in naive for f in r["_flags"])

    print("\nEn→Ko method lift  (Claude Opus 4.8, %d strings)\n" % len(naive))
    print(f"  clean rate   naive    |{ascii_bar(nc)}| {nc}%")
    print(f"               scaffold |{ascii_bar(sc)}| {sc}%\n")
    print("  flags removed by the scaffold (naive → scaffold):")
    for t, c in flag_counts.most_common():
        print(f"    {t:<16} {c:>2} → 0")
    print(f"\n  total flags  {sum(flag_counts.values())} → 0")

    out = pathlib.Path(args.out)
    out.write_text(render_html(naive, scaffold, by_id_s), encoding="utf-8")
    print(f"\n  HTML report → {out.resolve()}")


if __name__ == "__main__":
    main()
