"""Run every model on every document. Cached by (model, variant, prompt, input bytes); failures are never cached.

Usage: python3 run.py [model ...] [--only doc_type] [--limit N] [--variant single|check]
Raw outputs: results/raw/<model>/<variant>/<doc_id>.json    Errors: results/errors.jsonl    Spend: results/spend.json
"""
import argparse, hashlib, json, sys, threading, time, traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from models import MODELS
from tasks import TASKS, prompt, SELF_CHECK

HERE = Path(__file__).parent
DOCS, RES = HERE / "docs", HERE / "results"
LOCK = threading.Lock()


def load_docs(only=None, limit=None):
    out = []
    for d in sorted(DOCS.iterdir()):
        if not d.is_dir() or (only and d.name not in only):
            continue
        dtype = "invoices" if d.name == "invoices" else d.name
        files = sorted(d.glob("*.json"))
        for f in files[:limit] if limit else files:
            meta = json.loads(f.read_text())
            src = next((p for p in [f.with_suffix(x) for x in (".jpg", ".png", ".txt")] if p.exists()), None)
            out.append({"id": f.stem, "type": dtype, "input": src, "meta": meta})
    return out


def parse_json(text):
    s, e = text.find("{"), text.rfind("}")
    if s < 0 or e < 0:
        raise ValueError("no JSON object in response")
    return json.loads(text[s:e + 1])


def spend():
    p = RES / "spend.json"
    return json.loads(p.read_text()) if p.exists() else {}


def add_spend(model, cost):
    with LOCK:
        s = spend()
        s[model] = round(s.get(model, 0.0) + cost, 6)
        (RES / "spend.json").write_text(json.dumps(s, indent=1))


def one(model, variant, doc):
    cfg = MODELS[model]
    p = prompt(doc["type"])
    raw_in = doc["input"].read_bytes()
    kind = TASKS[doc["type"]]["input"]
    kwargs = {"image": doc["input"]} if kind == "image" else {"text": raw_in.decode("utf-8", "ignore")}
    if variant == "check":
        first = RES / "raw" / model / "single" / f"{doc['id']}.json"
        if not first.exists():
            return "skip-no-first"
        first_rec = json.loads(first.read_text())
        p = p + "\n\n" + SELF_CHECK + json.dumps(first_rec.get("parsed"), ensure_ascii=False)
    key = hashlib.sha256((model + variant + p).encode() + raw_in).hexdigest()
    out = RES / "raw" / model / variant / f"{doc['id']}.json"
    if out.exists() and json.loads(out.read_text()).get("key") == key:
        return "cached"
    if cfg["budget"] is not None and spend().get(model, 0) >= cfg["budget"]:
        return "over-budget"
    t = time.time()
    try:
        text, usage = cfg["call"](p, **kwargs)
        add_spend(model, usage.get("cost", 0.0))
        try:
            parsed, perr = parse_json(text), None
        except Exception as e:  # an unparseable answer is a real result (scored as all-wrong), not a failure
            parsed, perr = None, str(e)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"key": key, "model": model, "variant": variant, "doc": doc["id"], "type": doc["type"],
                                   "text": text, "parsed": parsed, "parse_error": perr, "usage": usage,
                                   "latency_s": round(time.time() - t, 2), "at": time.time()}, ensure_ascii=False, indent=1))
        return "ok"
    except Exception as e:
        with LOCK, open(RES / "errors.jsonl", "a") as f:
            f.write(json.dumps({"model": model, "variant": variant, "doc": doc["id"], "error": repr(e)[:500],
                                "at": time.time()}) + "\n")
        return "error"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("models", nargs="*")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--variant", default="single", choices=["single", "check"])
    a = ap.parse_args()
    RES.mkdir(exist_ok=True)
    docs = load_docs(a.only, a.limit)
    models = a.models or list(MODELS)
    print(f"{len(docs)} docs x {len(models)} models, variant={a.variant}", flush=True)

    def run_model(m):
        if a.variant == "check" and not MODELS[m]["self_check"]:
            return m, {"skipped": len(docs)}
        counts = {}
        with ThreadPoolExecutor(MODELS[m]["workers"]) as ex:
            futs = [ex.submit(one, m, a.variant, d) for d in docs]
            for f in as_completed(futs):
                r = f.result()
                counts[r] = counts.get(r, 0) + 1
        return m, counts

    with ThreadPoolExecutor(len(models)) as ex:
        for f in as_completed([ex.submit(run_model, m) for m in models]):
            m, c = f.result()
            print(f"{m}: {c}  spend=${spend().get(m, 0):.4f}", flush=True)


if __name__ == "__main__":
    main()
