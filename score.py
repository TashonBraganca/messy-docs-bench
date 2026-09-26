"""Score raw model outputs against answer keys.

Harsh on values, tolerant only on format: '02-06-89' == '1989-02-06', '$2,600.00' == '2600', but one wrong digit is wrong.
A field whose key is null must come back null; anything else counts as a hallucination.
Answer keys marked "SKIP" (too ambiguous, set by the human check) are not scored.
Usage: python3 score.py [--verified staging/verified_labels.json]
"""
import argparse, json, math, re, statistics
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from pathlib import Path
from dateutil import parser as dparser

HERE = Path(__file__).parent
DOCS, RES = HERE / "docs", HERE / "results"

# ---------- normalisers ----------

def is_null(v):
    return v is None or (isinstance(v, str) and v.strip().lower() in ("", "null", "none", "n/a", "-"))


def norm_text(v):
    s = str(v).lower().replace("\n", " ")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


LEGAL = r"\b(inc|incorporated|ltd|limited|llc|co|corp|corporation|company|the|spol s r o|sdn bhd|bhd|plc)\b"


def norm_org(v):
    return re.sub(r"\s+", " ", re.sub(LEGAL, " ", norm_text(v))).strip()


def norm_id(v):
    return re.sub(r"[^A-Za-z0-9]", "", str(v)).upper()


def money(v):
    if isinstance(v, (int, float)):
        return Decimal(str(v))
    s = str(v).strip()
    neg = s.startswith("(") and s.endswith(")") or s.startswith("-")
    s = re.sub(r",-$", "", s)
    s = re.sub(r"[^0-9.,]", "", s).strip(".,")  # 'Rp.118.000' -> '118.000'
    if not s:
        return None
    if "," in s and "." in s:
        dec = "." if s.rfind(".") > s.rfind(",") else ","
        s = s.replace("," if dec == "." else ".", "").replace(",", ".")
    elif "." in s and re.fullmatch(r"\d{1,3}(\.\d{3})+", s):
        s = s.replace(".", "")
    elif "," in s:
        s = s.replace(",", "") if re.fullmatch(r"\d{1,3}(,\d{3})+", s) else s.replace(",", ".")
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None
    return -d if neg else d


def same_money(a, b):
    x, y = money(a), money(b)
    return x is not None and y is not None and abs(x - y) < Decimal("0.005")


def date(v, dayfirst):
    if re.match(r"^\s*\d{4}-\d{1,2}-\d{1,2}", str(v)):  # ISO is year-month-day whatever the source convention
        dayfirst = False
    elif re.search(r"\b\d{1,2}\.\d{1,2}\.\d{2,4}\b", str(v)):  # 1.4.2011 is European day.month.year
        dayfirst = True
    try:
        return dparser.parse(str(v).replace("/", "-") if re.fullmatch(r"\w{3} \d{1,2}-\d{2}", str(v)) else str(v),
                             dayfirst=dayfirst, fuzzy=True).date()
    except (ValueError, OverflowError, TypeError):
        return None


def same_date(truth, pred, dayfirst):
    t, p = date(truth, dayfirst), date(pred, dayfirst)
    return t is not None and t == p


DUR = {"day": 1, "week": 7, "month": 30, "year": 365}
WORDN = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "twelve": 12, "thirty": 30, "sixty": 60, "ninety": 90}


def duration(v):
    s = norm_text(v)
    m = re.search(r"(\d+|" + "|".join(WORDN) + r")\s*(day|week|month|year)", s)
    if not m:
        return None
    n = int(m.group(1)) if m.group(1).isdigit() else WORDN[m.group(1)]
    return n * DUR[m.group(2)]


def same_duration(t, p):
    dt, dp = duration(t), duration(p)
    if dt is not None:
        return dt == dp
    nt, np_ = norm_text(t), norm_text(p)
    return nt == np_ or (len(nt) > 3 and nt in np_)


def same_law(t, p):
    strip = lambda s: re.sub(r"\b(state|commonwealth|laws?|of|the|usa|united states|u s)\b", " ", norm_text(s)).split()
    return " ".join(strip(t)) != "" and " ".join(strip(t)) in " ".join(strip(p))


# ---------- per-type field rules ----------

def rule(kind, **kw):
    return (kind, kw)


RULES = {
    "w2": {**{k: rule("money") for k in ["box1_wages", "box2_federal_tax", "box3_ss_wages", "box4_ss_tax",
                                          "box5_medicare_wages", "box6_medicare_tax", "box12a_amount",
                                          "box16_state_wages", "box17_state_tax"]},
           **{k: rule("id") for k in ["employee_ssn", "employer_ein", "box12a_code", "box15_state"]},
           **{k: rule("text") for k in ["employee_first_name", "employee_last_name", "employer_name_address",
                                        "employee_address"]}},
    "receipts": {"total": rule("money"), "subtotal": rule("money"), "tax": rule("money"), "items": rule("items")},
    "sroie": {"company": rule("org"), "date": rule("date", dayfirst=True), "address": rule("text"), "total": rule("money")},
    "invoices": {"vendor": rule("org"), "invoice_number": rule("id"), "invoice_date": rule("date", dayfirst=False),
                 "total": rule("money")},
    "statements": {"bank_name": rule("org"), "account_holder": rule("org"), "account_number": rule("id"),
                   "ifsc_code": rule("id"), "opening_balance": rule("money"), "start_date": rule("date", dayfirst=False),
                   "end_date": rule("date", dayfirst=False), "transactions": rule("txns")},
    "contracts": {"agreement_date": rule("date", dayfirst=False), "effective_date": rule("date", dayfirst=False),
                  "expiration_date": rule("date", dayfirst=False), "governing_law": rule("law"),
                  "renewal_term": rule("duration"), "notice_period_to_terminate_renewal": rule("duration")},
}
DAYFIRST_DOCS = {"rvl_008"}  # Czech invoice, dd.mm.yyyy


def items_match(truth, pred):
    """Receipt line items: a predicted item counts if its price matches and its name is >= 0.8 similar."""
    pred = [p for p in (pred or []) if isinstance(p, dict)]
    used, hit = set(), 0
    for t in truth:
        for j, p in enumerate(pred):
            if j in used:
                continue
            if same_money(t.get("price"), p.get("price")) and \
               SequenceMatcher(None, norm_text(t.get("name") or ""), norm_text(p.get("name") or "")).ratio() >= 0.75:
                used.add(j); hit += 1
                break
    return hit, len(truth), len(pred)


def txns_match(truth, pred):
    """Bank rows are located by their running balance. A row is right if date, debit and credit also match.
    'missed' counts true rows skipped between the first and last row the model returned."""
    pred = [p for p in (pred or []) if isinstance(p, dict)]
    idx_of = {}
    for i, t in enumerate(truth):
        idx_of.setdefault(str(money(t["balance"]).quantize(Decimal("0.01"))), i)
    matched, correct, halluc = [], 0, 0
    for p in pred:
        b = money(p.get("balance")) if not is_null(p.get("balance")) else None
        i = idx_of.get(str(b.quantize(Decimal("0.01")))) if b is not None else None
        if i is None:
            halluc += 1
            continue
        matched.append(i)
        t = truth[i]
        ok = same_date(t["date"], p.get("date"), False) and \
            all((is_null(t[k]) and is_null(p.get(k))) or (not is_null(t[k]) and same_money(t[k], p.get(k)))
                for k in ("debit", "credit"))
        correct += ok
    missed = len(set(range(min(matched), max(matched) + 1)) - set(matched)) if matched else 0
    starts_top = bool(matched) and min(matched) == 0
    return {"returned": len(pred), "correct": correct, "halluc": halluc, "missed_in_range": missed,
            "starts_top": starts_top,
            "exact": bool(matched) and correct == len(pred) and halluc == 0 and missed == 0 and starts_top}


def check(kind, kw, truth, pred, doc_id):
    if isinstance(truth, dict) and "any" in truth:  # human reviewer accepted several readings
        results = [check(kind, kw, t, pred, doc_id) for t in truth["any"]]
        ok = any(r[0] for r in results)
        return ok, {"accepted_alternatives": len(truth["any"]), **({"halluc": False} if ok else results[0][1])}
    if kind == "items":
        hit, nt, np_ = items_match(truth or [], pred)
        return hit == nt == np_, {"item_hit": hit, "item_truth": nt, "item_pred": np_}
    if kind == "txns":
        r = txns_match(truth, pred)
        return r["exact"], r
    if is_null(truth):
        return is_null(pred), {"truth_null": True, "halluc": not is_null(pred)}
    if is_null(pred):
        return False, {"missing": True}
    if kind == "money":
        return same_money(truth, pred), {}
    if kind == "id":
        return norm_id(truth) == norm_id(pred), {}
    if kind == "text":
        ok = norm_text(truth) == norm_text(pred)
        return ok, {"near": SequenceMatcher(None, norm_text(truth), norm_text(pred)).ratio() >= 0.9}
    if kind == "org":
        return norm_org(truth) == norm_org(pred), {"near": SequenceMatcher(None, norm_org(truth), norm_org(pred)).ratio() >= 0.9}
    if kind == "date":
        if date(truth, False) is None:  # non-date keys such as 'perpetual'
            return norm_text(truth) != "" and norm_text(truth) in norm_text(pred), {}
        return same_date(truth, pred, kw["dayfirst"] or doc_id in DAYFIRST_DOCS), {}
    if kind == "law":
        return same_law(truth, pred), {}
    if kind == "duration":
        return same_duration(truth, pred), {}
    raise ValueError(kind)


def flatten(doc_type, d):
    """Statements nest the header; lift it so every scored field sits at the top level."""
    if not isinstance(d, dict):
        return {}
    if doc_type == "statements":
        out = dict(d.get("header") or {})
        out["transactions"] = d.get("transactions")
        return out
    return d


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (max(0.0, c - h), min(1.0, c + h))


def load_truth(verified):
    truth = {}
    for f in DOCS.glob("*/*.json"):
        m = json.loads(f.read_text())
        truth[f.stem] = {"type": m["doc_type"], "truth": m["truth"], "meta": m}
    if verified and Path(verified).exists():
        for k, v in json.loads(Path(verified).read_text()).items():
            if ":" in k:  # label-audit item "doc:field"
                doc, fld = k.split(":", 1)
                if doc in truth and v.get("checked"):
                    truth[doc]["truth"][fld] = v["fields"][fld]
                    truth[doc]["meta"].setdefault("audited", []).append(fld)
                continue
            if k in truth:
                for fld, val in v["fields"].items():
                    if truth[k]["type"] == "statements":
                        continue
                    truth[k]["truth"][fld] = val
                truth[k]["meta"]["label_status"] = "human-verified" if v.get("checked") else "human-unchecked"
    return truth


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verified", default=str(HERE / "staging" / "verified_labels.json"))
    a = ap.parse_args()
    truth = load_truth(a.verified)
    rows = []
    for f in sorted((RES / "raw").glob("*/*/*.json")):
        r = json.loads(f.read_text())
        t = truth.get(r["doc"])
        if not t:
            continue
        pred = flatten(r["type"], r.get("parsed"))
        tv = flatten(r["type"], t["truth"]) if r["type"] != "statements" else \
            {**t["truth"]["header"], "transactions": t["truth"]["transactions"]}
        for fld, (kind, kw) in RULES[r["type"]].items():
            if isinstance(tv.get(fld), str) and (tv[fld].strip().upper() == "SKIP" or "[]" in tv[fld]):
                continue
            ok, info = check(kind, kw, tv.get(fld), pred.get(fld), r["doc"])
            rows.append({"model": r["model"], "variant": r["variant"], "type": r["type"], "doc": r["doc"], "field": fld,
                         "ok": bool(ok), "truth": tv.get(fld) if kind not in ("items", "txns") else None,
                         "pred": pred.get(fld) if kind not in ("items", "txns") else None,
                         "parse_error": r.get("parse_error"), "damage": t["meta"].get("damage"),
                         "label_status": t["meta"].get("label_status", "published"), **info,
                         "latency": r.get("latency_s"), "cost": (r.get("usage") or {}).get("cost", 0.0)})
    (RES / "scored_rows.json").write_text(json.dumps(rows, ensure_ascii=False, default=str))
    summarize(rows)


def summarize(rows):
    by = defaultdict(list)
    for r in rows:
        by[(r["model"], r["variant"], r["type"])].append(r)
    out = []
    for (m, v, ty), rs in sorted(by.items()):
        docs = defaultdict(list)
        for r in rs:
            docs[r["doc"]].append(r["ok"])
        k, n = sum(r["ok"] for r in rs), len(rs)
        nulls = [r for r in rs if r.get("truth_null")]
        lo, hi = wilson(k, n)
        dk = sum(all(x) for x in docs.values())
        out.append({"model": m, "variant": v, "type": ty, "fields": n, "field_acc": k / n, "ci": [lo, hi],
                    "docs": len(docs), "doc_exact": dk / len(docs), "doc_exact_ci": list(wilson(dk, len(docs))),
                    "halluc": sum(r.get("halluc", False) for r in nulls), "null_fields": len(nulls),
                    "parse_fail_docs": len({r["doc"] for r in rs if r["parse_error"]})})
    (RES / "summary.json").write_text(json.dumps(out, indent=1))
    for s in out:
        print(f"{s['model']:20s} {s['variant']:6s} {s['type']:10s} fields {s['field_acc']*100:5.1f}% "
              f"[{s['ci'][0]*100:4.1f}-{s['ci'][1]*100:4.1f}] n={s['fields']:4d} | docs fully right "
              f"{s['doc_exact']*100:5.1f}% of {s['docs']:3d} | halluc {s['halluc']}/{s['null_fields']} | parsefail {s['parse_fail_docs']}")


if __name__ == "__main__":
    main()
