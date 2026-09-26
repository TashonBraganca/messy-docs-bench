"""Build the LinkedIn carousel (8 portrait slides, one PDF) from the scored results, so numbers never drift from the data.
Re-run after any re-score: python3 make_carousel.py  ->  charts/carousel.pdf (+ slide PNGs for X)
"""
import json
from collections import defaultdict
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from PIL import Image

HERE = Path(__file__).parent
rows = json.loads((HERE / "results" / "scored_rows.json").read_text())
OUT = HERE / "charts"
BG, INK, DIM, LINE = "#0f1316", "#e8ecef", "#93a1ad", "#263039"
C = {"claude-opus-5.5": "#e0692a", "claude-sonnet-5": "#f0a36e", "gpt-5.6-terra": "#4d86f0", "qwen3-vl-8b-local": "#2fa865"}
NAME = {"claude-opus-5.5": "Claude Opus 5.5", "claude-sonnet-5": "Claude Sonnet 5", "gpt-5.6-terra": "GPT-5.6 Terra",
        "qwen3-vl-8b-local": "Qwen3-VL 8B (free, laptop)"}
ORDER = list(NAME)


def stats():
    f = defaultdict(lambda: [0, 0]); d = defaultdict(lambda: defaultdict(list)); t = defaultdict(lambda: defaultdict(list))
    for r in rows:
        if r["variant"] != "single":
            continue
        f[r["model"]][0] += r["ok"]; f[r["model"]][1] += 1
        d[r["model"]][r["doc"]].append(r["ok"]); t[(r["model"], r["type"])][r["doc"]].append(r["ok"])
    out = {}
    for m in ORDER:
        docs = d[m]
        out[m] = {"field": f[m][0] / f[m][1], "doc": sum(all(v) for v in docs.values()) / len(docs),
                  "doc_k": sum(all(v) for v in docs.values()), "n": len(docs)}
    per = {k: (sum(all(v) for v in dd.values()), len(dd)) for k, dd in t.items()}
    return out, per


S, PER = stats()
n_docs = S[ORDER[0]]["n"]
check = [json.loads(p.read_text()) for p in (HERE / "results/raw/gpt-5.6-terra/check").glob("*.json")]
same = sum(1 for c in check if c["parsed"] == json.loads((HERE / "results/raw/gpt-5.6-terra/single" / f"{c['doc']}.json").read_text())["parsed"])


def slide():
    fig = plt.figure(figsize=(7.2, 9), dpi=150, facecolor=BG)
    return fig


def text(fig, x, y, s, size=14, color=INK, weight="normal", **kw):
    fig.text(x, y, s, fontsize=size, color=color, weight=weight, va="top", **kw)


def footer(fig, i):
    text(fig, 0.07, 0.045, f"messy-docs-bench · {n_docs} documents · 4 models", 8.5, DIM)
    text(fig, 0.88, 0.045, f"{i}/8", 8.5, DIM)


slides = []
# 1 hook
f = slide(); best = S["claude-opus-5.5"]
text(f, 0.07, 0.9, "I tested 4 AI models on", 23, weight="bold")
text(f, 0.07, 0.845, f"{n_docs} messy business documents.", 23, weight="bold")
text(f, 0.07, 0.72, f"The best one still got {100 - round(best['doc'] * 100)}% of them", 22, "#e0692a", "bold")
text(f, 0.07, 0.67, "wrong somewhere.", 22, "#e0692a", "bold")
text(f, 0.07, 0.55, "1980s faxed invoices. Receipts from Jakarta and\nKuala Lumpur. US tax forms. Indian bank statements.\nReal SEC contracts.", 13, DIM, linespacing=1.5)
text(f, 0.07, 0.34, "Strict scoring: a field is right only if\nthe value is right. Swipe →", 13, INK, linespacing=1.5)
footer(f, 1); slides.append(f)
# 2 field accuracy lies
f = slide(); text(f, 0.07, 0.9, "Field accuracy hides the problem.", 22, weight="bold")
text(f, 0.07, 0.84, "One wrong field means a person re-checks the\nwhole document. So I count documents with\nEVERY field right.", 12.5, DIM, linespacing=1.5)
ax = f.add_axes([0.36, 0.14, 0.56, 0.56], facecolor=BG)
y = range(len(ORDER))
ax.barh([i + 0.2 for i in y], [S[m]["field"] * 100 for m in ORDER], 0.38, color="#3a4550", label="fields right")
ax.barh([i - 0.2 for i in y], [S[m]["doc"] * 100 for m in ORDER], 0.38, color=[C[m] for m in ORDER], label="whole document right")
for i, m in enumerate(ORDER):
    ax.text(S[m]["field"] * 100 + 1, i + 0.2, f"{S[m]['field']*100:.1f}%", va="center", color=DIM, fontsize=9)
    ax.text(S[m]["doc"] * 100 + 1, i - 0.2, f"{S[m]['doc']*100:.0f}%", va="center", color=INK, fontsize=10, weight="bold")
ax.set_yticks(list(y)); ax.set_yticklabels([NAME[m] for m in ORDER], color=INK, fontsize=10); ax.invert_yaxis()
ax.set_xlim(0, 112); ax.tick_params(colors=DIM, labelsize=8); [s.set_visible(False) for s in ax.spines.values()]
ax.legend(loc="upper center", bbox_to_anchor=(0.3, 1.1), ncol=2, fontsize=9, frameon=False, labelcolor=DIM)
footer(f, 2); slides.append(f)
# 3 by type
f = slide(); text(f, 0.07, 0.9, "Whole document right, by type", 20, weight="bold")
types = [("w2", "US W-2*"), ("receipts", "Receipts, Jakarta"), ("sroie", "Receipts, Malaysia"), ("invoices", "1980s invoices"),
         ("statements", "Indian bank stmt*"), ("contracts", "SEC contracts")]
ax = f.add_axes([0.32, 0.2, 0.62, 0.62], facecolor=BG)
w = 0.2
for j, m in enumerate(ORDER):
    vals = [PER[(m, t)][0] / PER[(m, t)][1] * 100 for t, _ in types]
    ax.barh([i + (j - 1.5) * w for i in range(len(types))], vals, w, color=C[m], label=NAME[m])
ax.set_yticks(range(len(types))); ax.set_yticklabels([l for _, l in types], color=INK, fontsize=10); ax.invert_yaxis()
ax.set_xlim(0, 100); ax.tick_params(colors=DIM, labelsize=8); [s.set_visible(False) for s in ax.spines.values()]
ax.legend(loc="upper center", bbox_to_anchor=(0.3, -0.06), ncol=2, fontsize=8.5, frameon=False, labelcolor=DIM)
text(f, 0.07, 0.075, "* synthetic. n = 10 to 32 per type, so gaps of a few points are noise.", 8.5, DIM)
footer(f, 3); slides.append(f)
# 4 Rachael
f = slide(); text(f, 0.07, 0.9, "GPT-5.6 Terra 'fixes' names.", 22, weight="bold")
_im = Image.open(HERE / "docs/w2/w2_008.jpg"); _w, _h = _im.size
crop = _im.crop((int(_w * 0.04), int(_h * 0.44), int(_w * 0.56), int(_h * 0.62)))
if crop:
    ax = f.add_axes([0.07, 0.56, 0.86, 0.26]); ax.imshow(crop, cmap="gray"); ax.axis("off")
for k, (m, v, ok) in enumerate([("Claude Opus 5.5", "Rachael", 1), ("Claude Sonnet 5", "Rachael", 1),
                                 ("Qwen3-VL 8B (laptop)", "Rachael", 1), ("GPT-5.6 Terra", "Rachel", 0)]):
    yy = 0.5 - k * 0.055
    text(f, 0.09, yy, m, 12, INK); text(f, 0.6, yy, v, 13, "#2fa865" if ok else "#e5484d", "bold", family="monospace")
text(f, 0.07, 0.24, "Also: Kelleyland → Kellyland, Stevenmouth →\nSteventown, 'and Sons' → 'and Songs'.\nOn a tax form, each one is a wrong name.", 12.5, DIM, linespacing=1.5)
footer(f, 4); slides.append(f)
# 5 Indian dates
q = PER[("qwen3-vl-8b-local", "statements")]; tw = PER[("qwen3-vl-8b-local", "w2")]; terra_w2 = PER[("gpt-5.6-terra", "w2")]
f = slide(); text(f, 0.07, 0.9, "The free laptop model:", 22, weight="bold")
text(f, 0.07, 0.83, f"beat GPT-5.6 Terra on W-2s ({tw[0]}/{tw[1]} vs {terra_w2[0]}/{terra_w2[1]}),", 15, "#2fa865", "bold")
text(f, 0.07, 0.78, f"then failed Indian bank statements ({q[0]}/{q[1]}).", 15, "#e5484d", "bold")
text(f, 0.07, 0.66, "Printed:", 13, DIM); text(f, 0.35, 0.665, "02-01-2024", 22, INK, "bold", family="monospace")
text(f, 0.07, 0.58, "Model read:", 13, DIM); text(f, 0.35, 0.585, "1 Feb 2024", 22, "#e5484d", "bold", family="monospace")
text(f, 0.07, 0.5, "Correct:", 13, DIM); text(f, 0.35, 0.505, "2 Jan 2024", 22, "#2fa865", "bold", family="monospace")
text(f, 0.07, 0.38, "Every amount and every balance was right.\nOnly the dates flipped, the American way.\nThat is the whole problem for Indian accounting.", 12.5, DIM, linespacing=1.5)
footer(f, 5); slides.append(f)
# 6 self check
f = slide(); text(f, 0.07, 0.9, "'Check your own work'\nchanged nothing.", 24, weight="bold", linespacing=1.3)
text(f, 0.07, 0.72, f"{same} of {len(check)}", 54, "#4d86f0", "bold")
text(f, 0.07, 0.6, "answers came back identical when GPT-5.6 Terra\nwas shown the document again and asked to\ncorrect its own extraction.", 13, DIM, linespacing=1.5)
text(f, 0.07, 0.42, "It fixed 1 document and broke 1.\nA second pass is not an evaluation.", 14, INK, linespacing=1.5)
footer(f, 6); slides.append(f)
# 7 cost
f = slide(); text(f, 0.07, 0.9, "The model bill is noise.", 24, weight="bold")
for k, (m, c) in enumerate([("GPT-5.6 Terra", "~$5.60 per 1,000 docs (measured)"), ("Claude Sonnet 5", "~$4.50 (estimated)"),
                             ("Claude Opus 5.5", "~$9.80 (estimated)"), ("Qwen3-VL 8B, laptop", "$0")]):
    text(f, 0.07, 0.78 - k * 0.07, m, 13, INK); text(f, 0.5, 0.78 - k * 0.07, c, 12, DIM)
text(f, 0.07, 0.44, "Under 1 cent a document for every model.", 16, "#e0692a", "bold")
text(f, 0.07, 0.38, "The errors are the cost: a wrong SSN,\na flipped date, a 'corrected' name.", 13, DIM, linespacing=1.5)
footer(f, 7); slides.append(f)
# 8 method + limits
f = slide(); text(f, 0.07, 0.9, "Method and limits", 22, weight="bold")
text(f, 0.07, 0.83,
     "• Same prompt for every model, full-resolution images\n"
     "• Claude ran with no tools; Qwen ran locally via Ollama\n"
     "• Scorer tested on 31 cases, including ones that must fail\n"
     "• 10 to 32 documents per type: small gaps are noise\n"
     "• W-2s and bank statements are synthetic; the W-2s\n   were generated this week, so no model has seen them\n"
     "• Invoice answer keys hand-transcribed, then checked\n   by a second person against the images\n"
     "• Public datasets may be in training data",
     12, DIM, linespacing=1.7)
text(f, 0.07, 0.34, "Prompts, answer keys and every raw output:\nlink in the comments.", 15, INK, "bold", linespacing=1.4)
text(f, 0.07, 0.22, "Next: fine-tuning the free 8B model to fix\nthese failures. Results in two weeks, win or lose.", 13, "#2fa865", linespacing=1.5)
footer(f, 8); slides.append(f)

with PdfPages(OUT / "carousel.pdf") as pdf:
    for i, fig in enumerate(slides, 1):
        pdf.savefig(fig, facecolor=BG)
        fig.savefig(OUT / f"slide_{i}.png", facecolor=BG)
        plt.close(fig)
print("carousel:", OUT / "carousel.pdf", "| terra self-check identical:", same, "/", len(check))
