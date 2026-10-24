#!/usr/bin/env python3
"""
score.py — score En→Ko benchmark outputs.

Always-on (no deps): 번역투 / register / placeholder / josa-after-variable / glossary heuristics.
Optional (if installed):
  - chrF++  via sacrebleu           (pip install "sacrebleu[ko]")          -- needs reference_ko
  - COMET   via unbabel-comet       (pip install unbabel-comet)            -- needs reference_ko
  - COMET-Kiwi (reference-free QE)   gated HF model; huggingface-cli login
  - kiwipiepy typo flags             (pip install kiwipiepy)

Usage
-----
python score.py out/claude.jsonl --comet-kiwi
python score.py out/*.jsonl --chrf --comet --comet-kiwi --csv report.csv
"""
from __future__ import annotations
import argparse
import glob
import json
import re
import sys
from collections import Counter

# ----- placeholder / heuristic patterns --------------------------------------
PLACEHOLDER_RE = re.compile(r"\{[^}]*\}|%\d*\$?[sd]|<[^>]+>|\\n")
JOSA_AFTER_VAR_RE = re.compile(r"(\}|%\d*\$?[sd]|>)\s*(을|를|이|가|은|는|과|와|로|으로)(?!\s*\()")
DUAL_FORM_RE = re.compile(r"을\(를\)|를\(을\)|이\(가\)|가\(이\)|은\(는\)|는\(은\)|와\(과\)|과\(와\)|\(으\)로")

# (name, regex, severity)  severity: critical/major/minor/info
HEURISTICS = [
    ("passive_eui",      re.compile(r"에\s*의해"),                                  "major"),
    ("double_passive",   re.compile(r"(되어|보여|쓰여|불려|잊혀|모여|닦여|갈려)[지진질져]"), "major"),
    ("pronoun",          re.compile(r"당신|너의|당신의|그의|그녀"),                  "major"),
    ("have_gajida",      re.compile(r"가지고\s*있|를\s*가지|을\s*가지|가집니다"),     "major"),
    ("plural_deul",      re.compile(r"들(이|을|은|는|과|에게|의|도|만|까지|에서)"),   "minor"),
    ("article_hana",     re.compile(r"하나의|한\s*개의"),                            "minor"),
    ("connective",       re.compile(r"그리고|그러나|그래서"),                         "minor"),
    ("exclamation",      re.compile(r"!"),                                          "minor"),
]
SEVERITY_WEIGHT = {"critical": 25, "major": 5, "minor": 1, "info": 0}


def find_placeholders(text: str) -> Counter:
    return Counter(PLACEHOLDER_RE.findall(text or ""))


def heuristic_flags(rec: dict) -> list[dict]:
    t = rec.get("translation", "") or ""
    cat = rec.get("category", "")
    flags = []

    for name, rx, sev in HEURISTICS:
        if rx.search(t):
            flags.append({"type": name, "severity": sev})

    # placeholder integrity vs source (skip ICU rows: nested braces need a real parser)
    if "icu" not in cat:
        src_ph = find_placeholders(rec.get("source_en", ""))
        out_ph = find_placeholders(t)
        if src_ph != out_ph:
            flags.append({"type": "placeholder_mismatch", "severity": "critical",
                          "detail": f"src={dict(src_ph)} out={dict(out_ph)}"})

    # josa hardcoded right after a variable (and not a dual form)
    if JOSA_AFTER_VAR_RE.search(t):
        flags.append({"type": "josa_after_var", "severity": "major"})

    # dual form present (expected on josa_var/placeholder rows -> info; else minor nudge)
    if DUAL_FORM_RE.search(t):
        sev = "info" if cat in {"josa_var", "placeholder"} else "minor"
        flags.append({"type": "dual_form", "severity": sev})

    # label/button should not end with a period
    if cat in {"button", "toggle"} and t.rstrip().endswith((".", "。")):
        flags.append({"type": "label_trailing_period", "severity": "minor"})

    # progress strings should use the ellipsis char, not "..."
    if cat == "status" and "..." in t:
        flags.append({"type": "ascii_ellipsis", "severity": "minor"})

    # required glossary terms present (substring / josa-tolerant).
    # Skip when the source term is itself a placeholder/variable (not translatable here),
    # or a DNT term equal to its source (kept in English).
    placeholders = rec.get("placeholders", [])
    for src, tgt in (rec.get("glossary") or {}).items():
        if "{" + src + "}" in placeholders:
            continue
        if tgt == src:  # DNT term: must survive verbatim (not translated/transliterated)
            if src not in t:
                flags.append({"type": "dnt_violation", "severity": "major", "detail": src})
            continue
        if tgt and tgt not in t and tgt.lower() not in t.lower():
            flags.append({"type": "term_missing", "severity": "major", "detail": tgt})

    return flags


def heuristic_score(flags: list[dict]) -> int:
    return max(0, 100 - sum(SEVERITY_WEIGHT[f["severity"]] for f in flags))


# ----- optional neural / surface metrics -------------------------------------
def maybe_chrf(records):
    try:
        from sacrebleu.metrics import CHRF
    except Exception:
        return None
    chrf = CHRF(word_order=2)  # chrF++
    for r in records:
        ref = r.get("reference_ko", "")
        if ref and r.get("translation"):
            r["chrf"] = chrf.sentence_score(r["translation"], [ref]).score
    scored = [r["chrf"] for r in records if "chrf" in r]
    return sum(scored) / len(scored) if scored else None


def maybe_comet(records, model_name, key):
    try:
        from comet import download_model, load_from_checkpoint
    except Exception:
        return None
    try:
        model = load_from_checkpoint(download_model(model_name))
    except Exception as e:
        print(f"  (COMET {model_name} unavailable: {e})", file=sys.stderr)
        return None
    ref_based = "kiwi" not in model_name.lower()
    data, idx = [], []
    for i, r in enumerate(records):
        if not r.get("translation"):
            continue
        if ref_based and not r.get("reference_ko"):
            continue
        d = {"src": r["source_en"], "mt": r["translation"]}
        if ref_based:
            d["ref"] = r["reference_ko"]
        data.append(d)
        idx.append(i)
    if not data:
        return None
    out = model.predict(data, batch_size=8, gpus=0, progress_bar=False)
    for i, s in zip(idx, out.scores):
        records[i][key] = s
    return out.system_score


def maybe_kiwi(records):
    try:
        from kiwipiepy import Kiwi
    except Exception:
        return
    kiwi = Kiwi()
    for r in records:
        t = r.get("translation", "")
        if not t:
            continue
        toks = kiwi.tokenize(t)
        suspect = [tok.form for tok in toks if getattr(tok, "typo_cost", 0) and tok.typo_cost > 0]
        if suspect:
            r.setdefault("_flags", []).append(
                {"type": "kiwi_typo", "severity": "minor", "detail": ",".join(suspect[:5])})


# ----- driver ----------------------------------------------------------------
def load(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def main():
    ap = argparse.ArgumentParser(description="Score En→Ko benchmark outputs.")
    ap.add_argument("files", nargs="+", help="run.py output jsonl file(s) / globs")
    ap.add_argument("--chrf", action="store_true")
    ap.add_argument("--comet", action="store_true", help="reference-based wmt22-comet-da")
    ap.add_argument("--comet-kiwi", action="store_true", help="reference-free QE (gated HF model)")
    ap.add_argument("--kiwi", action="store_true", help="kiwipiepy typo flags")
    ap.add_argument("--csv", help="write per-row CSV here")
    args = ap.parse_args()

    paths = [p for f in args.files for p in glob.glob(f)] or args.files
    all_rows = []
    summary = []

    for path in paths:
        records = load(path)
        for r in records:
            r["_flags"] = heuristic_flags(r)
            r["_hscore"] = heuristic_score(r["_flags"])
        if args.kiwi:
            maybe_kiwi(records)
            for r in records:  # recompute after kiwi appended flags
                r["_hscore"] = heuristic_score(r["_flags"])

        chrf = maybe_chrf(records) if args.chrf else None
        comet = (maybe_comet(records, "Unbabel/wmt22-comet-da", "comet") if args.comet else None)
        cometkiwi = (maybe_comet(records, "Unbabel/wmt23-cometkiwi-da-xl", "comet_kiwi")
                     if args.comet_kiwi else None)

        n = len(records)
        clean = sum(1 for r in records
                    if not any(f["severity"] in ("major", "critical") for f in r["_flags"]))
        flag_counts = Counter(f["type"] for r in records for f in r["_flags"]
                              if f["severity"] != "info")
        model = records[0].get("model", path) if records else path
        mode = ("2pass" if records and records[0].get("two_pass") else
                "naive" if records and records[0].get("naive") else "1pass")
        summary.append({
            "file": path, "model": model, "mode": mode, "n": n,
            "clean_rate": round(100 * clean / n, 1) if n else 0,
            "mean_hscore": round(sum(r["_hscore"] for r in records) / n, 1) if n else 0,
            "chrf++": round(chrf, 2) if chrf is not None else None,
            "comet": round(comet, 4) if comet is not None else None,
            "comet_kiwi": round(cometkiwi, 4) if cometkiwi is not None else None,
            "top_flags": dict(flag_counts.most_common(6)),
        })
        for r in records:
            r["_file"] = path
        all_rows.extend(records)

    # ---- print summary ----
    print("\n=== En→Ko benchmark summary ===")
    hdr = f'{"model":<34}{"mode":<7}{"n":>4}{"clean%":>8}{"hscore":>8}{"chrF++":>8}{"COMET":>8}{"COMETkiwi":>11}'
    print(hdr)
    print("-" * len(hdr))
    for s in summary:
        print(f'{str(s["model"])[:33]:<34}{s["mode"]:<7}{s["n"]:>4}{s["clean_rate"]:>8}'
              f'{s["mean_hscore"]:>8}'
              f'{(s["chrf++"] if s["chrf++"] is not None else "-"):>8}'
              f'{(s["comet"] if s["comet"] is not None else "-"):>8}'
              f'{(s["comet_kiwi"] if s["comet_kiwi"] is not None else "-"):>11}')
    print("\nTop heuristic flags per file:")
    for s in summary:
        print(f'  {str(s["model"])[:40]:<42} {s["top_flags"]}')

    if args.csv:
        import csv
        cols = ["_file", "model", "id", "category", "register", "source_en", "translation",
                "reference_ko", "_hscore", "chrf", "comet", "comet_kiwi"]
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(cols + ["flags"])
            for r in all_rows:
                w.writerow([r.get(c, "") for c in cols]
                           + ["; ".join(f"{x['type']}({x['severity']})" for x in r.get("_flags", []))])
        print(f"\nPer-row CSV → {args.csv}", file=sys.stderr)


if __name__ == "__main__":
    main()
