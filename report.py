"""Turn results/scored_rows.json into the numbers and charts for chat and posts.

Headline metric: "fully right" = every scored field on the document correct, i.e. no human touch needed.
Charts go to charts/*.png at 2x resolution.
"""
import json, math
from collections import defaultdict
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from score import wilson

HERE = Path(__file__).parent
RES, CH = HERE / "results", HERE / "charts"
CH.mkdir(exist_ok=True)
rows = json.loads((RES / "scored_rows.json").read_text())

MODELS = ["claude-opus-5.5", "claude-sonnet-5", "gpt-5.6-terra", "qwen3-vl-8b-local"]
LABEL = {"claude-opus-5.5": "Claude Opus 5.5", "claude-sonnet-5": "Claude Sonnet 5", "gpt-5.6-terra": "GPT-5.6 Terra",
         "qwen3-vl-8b-local": "Qwen3-VL 8B Instruct (laptop)"}
TYPES = ["w2", "receipts", "sroie", "invoices", "statements", "contracts"]
TNAME = {"w2": "US W-2 (synthetic)", "receipts": "Receipts, Indonesia (real)", "sroie": "Receipts, Malaysia (real)",
         "invoices": "Invoices, 1980s-90s scans (real)", "statements": "Bank statements (synthetic)",
         "contracts": "Contracts, SEC filings (real)"}


def docs_of(model, variant, ty):
    d = defaultdict(list)
    for r in rows:
        if r["model"] == model and r["variant"] == variant and r["type"] == ty:
            d[r["doc"]].append(r["ok"])
    return d


def table(variant="single"):
    out = {}
    for m in MODELS:
        for ty in TYPES:
            d = docs_of(m, variant, ty)
            if not d:
                continue
            k = sum(all(v) for v in d.values())
            f_ok = sum(sum(v) for v in d.values()); f_n = sum(len(v) for v in d.values())
            out[(m, ty)] = {"docs": len(d), "exact": k, "rate": k / len(d), "ci": wilson(k, len(d)),
                            "field_rate": f_ok / f_n, "fields": f_n}
    return out


def chart_exact(t):
    fig, ax = plt.subplots(figsize=(11, 6), dpi=200)
    ms = [m for m in MODELS if any((m, ty) in t for ty in TYPES)]
    w = 0.8 / len(ms)
    colors = ["#c2410c", "#ea9a5c", "#2563eb", "#15803d"]
    for i, m in enumerate(ms):
        xs, ys, lo, hi = [], [], [], []
        for j, ty in enumerate(TYPES):
            if (m, ty) not in t:
                continue
            s = t[(m, ty)]
            xs.append(j + (i - (len(ms) - 1) / 2) * w); ys.append(s["rate"] * 100)
            lo.append((s["rate"] - s["ci"][0]) * 100); hi.append((s["ci"][1] - s["rate"]) * 100)
        ax.bar(xs, ys, w, label=LABEL[m], color=colors[i % 4], yerr=[lo, hi], capsize=2, error_kw={"lw": 0.8})
    ax.set_xticks(range(len(TYPES)))
    ax.set_xticklabels([TNAME[t_].replace(" (", "\n(") for t_ in TYPES], fontsize=8)
    ax.set_ylabel("% of documents with EVERY field right")
    ax.set_ylim(0, 105)
    ax.set_title("How often does the model get the whole document right?  (bars: 95% confidence)", fontsize=11)
    ax.legend(fontsize=8, loc="upper center", bbox_to_anchor=(0.5, -0.16), ncol=4, frameon=False)
    ax.spines[["top", "right"]].set_visible(False)
    fig.text(0.01, 0.01, "Invoice keys hand-transcribed, then checked by a second person. n per bar: W-2 32, receipts 30+30, invoices 20, statements 10, contracts 15.",
             fontsize=7, color="#555")
    fig.tight_layout(rect=(0, 0.03, 1, 1)); fig.savefig(CH / "fully_right.png"); plt.close(fig)


def damage_breakdown():
    out = {}
    for m in MODELS:
        by = defaultdict(lambda: defaultdict(list))
        for r in rows:
            if r["model"] == m and r["variant"] == "single" and r["type"] == "w2":
                by[r["damage"]][r["doc"]].append(r["ok"])
        if by:
            out[m] = {lvl: (sum(all(v) for v in d.values()), len(d)) for lvl, d in sorted(by.items())}
    return out


def self_check_delta():
    out = {}
    for m in MODELS:
        for ty in TYPES:
            a, b = docs_of(m, "single", ty), docs_of(m, "check", ty)
            common = set(a) & set(b)
            if not common:
                continue
            ka = sum(all(a[d]) for d in common); kb = sum(all(b[d]) for d in common)
            fixed = sum((not all(a[d])) and all(b[d]) for d in common)
            broke = sum(all(a[d]) and (not all(b[d])) for d in common)
            out[(m, ty)] = {"n": len(common), "before": ka, "after": kb, "fixed": fixed, "broke": broke}
    return out


def failures(model, ty, n=8):
    return [r for r in rows if r["model"] == model and r["variant"] == "single" and r["type"] == ty and not r["ok"]][:n]


if __name__ == "__main__":
    t = table()
    chart_exact(t)
    print("FULLY RIGHT (single pass)")
    for ty in TYPES:
        cells = [f"{LABEL[m][:14]:14s} {t[(m,ty)]['exact']:3d}/{t[(m,ty)]['docs']:<3d} {t[(m,ty)]['rate']*100:5.1f}%"
                 for m in MODELS if (m, ty) in t]
        print(f"{ty:10s} | " + " | ".join(cells))
    print("\nW-2 BY DAMAGE (fully right)")
    for m, d in damage_breakdown().items():
        print(f"{LABEL[m]:22s} " + "  ".join(f"lvl {lvl}: {k}/{n}" for lvl, (k, n) in d.items()))
    print("\nSELF-CHECK PASS")
    for (m, ty), s in self_check_delta().items():
        print(f"{LABEL[m]:22s} {ty:10s} {s['before']}/{s['n']} -> {s['after']}/{s['n']}  fixed {s['fixed']}, broke {s['broke']}")
    json.dump({"fully_right": {f"{m}|{ty}": v for (m, ty), v in t.items()},
               "damage": damage_breakdown(), "self_check": {f"{m}|{ty}": v for (m, ty), v in self_check_delta().items()}},
              open(RES / "report.json", "w"), indent=1, default=str)
