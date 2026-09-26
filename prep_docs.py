"""Build the real-document sets with their answer keys.

receipts   : CORD v2 test split (real Indonesian receipts, CC-BY-4.0), first 30
sroie      : ICDAR 2019 SROIE test split (real Malaysian receipts, CC-BY-4.0 via jsdnrs/ICDAR2019-SROIE), 30
invoices   : RVL-CDIP invoice class (real 1990s scanned invoices); fields hand-transcribed, then human-verified
statements : AgamiAI Indian bank statements (synthetic scans, Apache-2.0), page 1 of 10 statements
contracts  : CUAD v1 (real SEC-filed contracts, CC-BY-4.0), 15 contracts, 6 lawyer-labelled fields
"""
import csv, io, json, random
from pathlib import Path
import pyarrow.parquet as pq
import pymupdf
from scrapling.fetchers import Fetcher

HERE = Path(__file__).parent
DATA, DOCS = HERE / "data", HERE / "docs"
HF = "https://huggingface.co/datasets"


def get(url):
    r = Fetcher.get(url)
    assert r.status == 200 and len(r.body) > 100, f"bad fetch {r.status} {url}"
    return r.body


def save(kind, name, image_bytes, truth, ext="jpg", extra=None):
    d = DOCS / kind
    d.mkdir(parents=True, exist_ok=True)
    if image_bytes is not None:
        (d / f"{name}.{ext}").write_bytes(image_bytes)
    rec = {"doc_type": kind, "truth": truth}
    rec.update(extra or {})
    (d / f"{name}.json").write_text(json.dumps(rec, indent=1, ensure_ascii=False))


def receipts(n=30):
    t = pq.read_table(DATA / "cord_test.parquet").to_pylist()
    for i, row in enumerate(t[:n]):
        gt = json.loads(row["ground_truth"])["gt_parse"]
        menu = gt.get("menu", [])
        menu = menu if isinstance(menu, list) else [menu]
        first = lambda v: v[0] if isinstance(v, list) else v  # CORD sometimes stores a field twice as a list
        items = []
        for m in menu:
            if not isinstance(m, dict):
                continue
            items.append({"name": first(m.get("nm")), "price": first(m.get("price"))})
            subs = m.get("sub") or []
            for sb in (subs if isinstance(subs, list) else [subs]):  # add-ons printed as their own lines
                if isinstance(sb, dict) and sb.get("nm"):
                    items.append({"name": first(sb.get("nm")), "price": first(sb.get("price"))})
        truth = {
            "total": first((gt.get("total") or {}).get("total_price")),
            "subtotal": first((gt.get("sub_total") or {}).get("subtotal_price")),
            "tax": first((gt.get("sub_total") or {}).get("tax_price")),
            "items": items,
        }
        save("receipts", f"rcpt_{i:03d}", row["image"]["bytes"], truth, ext="png")


def sroie(n=30):
    import ast
    t = pq.read_table(DATA / "sroie_test.parquet").to_pylist()
    for i, row in enumerate(t[:n]):
        ent = row["entities"] if isinstance(row["entities"], dict) else ast.literal_eval(row["entities"])
        save("sroie", f"sroie_{i:03d}", row["image"]["bytes"], dict(ent), ext="jpg", extra={"source": row["key"]})


def rvl_candidates(n=60):
    """Dump candidate real invoices for hand transcription (labels are added later, not here)."""
    t = pq.read_table(DATA / "rvl_inv0.parquet").slice(0, n).to_pylist()
    d = HERE / "staging" / "rvl"
    d.mkdir(parents=True, exist_ok=True)
    for i, row in enumerate(t):
        (d / f"rvl_{i:03d}.png").write_bytes(row["image"]["bytes"])


def invoices_katanaml_unused():
    t = pq.read_table(DATA / "inv_test.parquet").to_pylist()
    for i, row in enumerate(t):
        gt = json.loads(row["ground_truth"])["gt_parse"]
        h, s = gt["header"], gt["summary"]
        items = gt["items"] if isinstance(gt["items"], list) else [gt["items"]]
        truth = {k: h.get(k) for k in ["invoice_no", "invoice_date", "seller_tax_id", "client_tax_id", "iban"]}
        truth.update({k: s.get(k) for k in ["total_net_worth", "total_vat", "total_gross_worth"]})
        truth["items"] = [{"description": it.get("item_desc"), "qty": it.get("item_qty"),
                           "gross_worth": it.get("item_gross_worth")} for it in items]
        save("invoices", f"inv_{i:03d}", row["image"]["bytes"], truth, ext="png")


def statements(per_type=5):
    base = f"{HF}/AgamiAI/Indian-Bank-Statements/resolve/main/train"
    k = 0
    for folder in ["India_Bank_Statement_Scanned_Type1", "India_Bank_Statement_Scanned_Type2"]:
        for j in range(1, per_type + 1):
            stem = f"{j:05d}"
            truth_all = json.loads(get(f"{base}/{folder}/{stem}.json"))
            pdf = pymupdf.open(stream=get(f"{base}/{folder}/{stem}.pdf"), filetype="pdf")
            png = pdf[0].get_pixmap(dpi=150).tobytes("png")
            header = {x: truth_all[x] for x in ["bank_name", "account_holder", "account_number", "ifsc_code",
                                                 "opening_balance", "start_date", "end_date"]}
            txns = []
            for t in truth_all["transactions"]:
                if "debit" in t:  # Type1: separate debit/credit columns, ISO dates
                    d, c, b, dt = t["debit"], t["credit"], t["balance"], t["date"][:10]
                else:  # Type2: one amount column with CR/DR, dd/mm/yyyy dates
                    amt = t["transaction_amount"]
                    d, c = (amt, None) if t["cr_dr"] == "DR" else (None, amt)
                    b = t["available_balance"]
                    dd, mm, yy = t["date"].split("/")
                    dt = f"{yy}-{mm}-{dd}"
                txns.append({"date": dt, "debit": d, "credit": c, "balance": b})
            save("statements", f"stmt_{k:03d}", png, {"header": header, "transactions": txns}, ext="png",
                 extra={"source": f"{folder}/{stem}", "pages_in_pdf": len(pdf)})
            k += 1


CUAD_FIELDS = {"agreement_date": "Agreement Date-Answer", "effective_date": "Effective Date-Answer",
               "expiration_date": "Expiration Date-Answer", "governing_law": "Governing Law-Answer",
               "renewal_term": "Renewal Term-Answer", "notice_period_to_terminate_renewal": "Notice Period To Terminate Renewal- Answer"}


def contracts(n=15):
    rows = {r["Filename"].rsplit(".", 1)[0]: r for r in csv.DictReader(open(DATA / "master_clauses.csv", encoding="utf-8", errors="ignore"))}
    listing = json.loads(get("https://huggingface.co/api/datasets/theatticusproject/cuad/tree/main/CUAD_v1/full_contract_txt/Part_I"))
    random.seed(11)
    cands = [x for x in listing if x["type"] == "file" and 12000 < x.get("size", 0) < 60000]
    random.shuffle(cands)
    k = 0
    for x in cands:
        stem = x["path"].split("/")[-1].rsplit(".", 1)[0]
        row = rows.get(stem)
        if not row:
            continue
        truth = {f: (row[c].strip() or None) for f, c in CUAD_FIELDS.items()}
        if sum(v is not None for v in truth.values()) < 3:
            continue
        text = get(f"{HF}/theatticusproject/cuad/resolve/main/{x['path'].replace(' ', '%20')}").decode("utf-8", "ignore")
        d = DOCS / "contracts"; d.mkdir(parents=True, exist_ok=True)
        (d / f"ctr_{k:03d}.txt").write_text(text)
        save("contracts", f"ctr_{k:03d}", None, truth, extra={"source": stem, "chars": len(text)})
        k += 1
        if k == n:
            break
    print("contracts:", k)


if __name__ == "__main__":
    import sys
    steps = sys.argv[1:] or ["receipts", "sroie", "rvl", "statements", "contracts"]
    if "receipts" in steps: receipts(); print("receipts done")
    if "sroie" in steps: sroie(); print("sroie done")
    if "rvl" in steps: rvl_candidates(); print("rvl candidates staged")
    if "statements" in steps: statements(); print("statements done")
    if "contracts" in steps: contracts()
