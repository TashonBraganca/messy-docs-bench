"""Model backends. Each call returns (text, usage dict). Failures raise; the runner never caches them.

azure        : the user's own Azure OpenAI deployment (GPT-5.6 Terra)
openrouter   : paid per token by the user; spend is capped per model in run.py
claude-cli   : the user's Claude subscription via headless `claude -p`, with no tools, no MCP, no settings,
               run from an empty folder, image passed inline as base64
"""
import base64, json, mimetypes, subprocess, tempfile, time, urllib.request
from pathlib import Path

ENV_FILE = Path(__import__("os").environ.get("BENCH_ENV_FILE", ".env"))


def env():
    vals = {}
    for line in ENV_FILE.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            vals[k.strip()] = v.strip().strip('"')
    return vals


def _post(url, payload, headers, timeout=900):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _b64(path):
    mime = mimetypes.guess_type(str(path))[0] or "image/png"
    return mime, base64.b64encode(Path(path).read_bytes()).decode()


def _openai_content(prompt, image=None, text=None):
    parts = []
    if image:
        mime, data = _b64(image)
        parts.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}", "detail": "high"}})
    body = prompt + (f"\n\nDOCUMENT TEXT:\n{text}" if text else "")
    parts.append({"type": "text", "text": body})
    return parts


def azure(prompt, image=None, text=None):
    e = env()
    url = (f"{e['AZURE_OPENAI_ENDPOINT'].rstrip('/')}/openai/deployments/{e['AZURE_OPENAI_DEPLOYMENT']}"
           f"/chat/completions?api-version={e.get('AZURE_OPENAI_API_VERSION', '2025-04-01-preview')}")
    r = _post(url, {"messages": [{"role": "user", "content": _openai_content(prompt, image, text)}],
                    "max_completion_tokens": 16000}, {"api-key": e["AZURE_OPENAI_API_KEY"]})
    u = r.get("usage", {})
    return r["choices"][0]["message"]["content"], {"in": u.get("prompt_tokens"), "out": u.get("completion_tokens"), "cost": 0.0}


def openrouter(model):
    def call(prompt, image=None, text=None):
        e = env()
        r = _post("https://openrouter.ai/api/v1/chat/completions",
                  {"model": model, "messages": [{"role": "user", "content": _openai_content(prompt, image, text)}],
                   "max_tokens": 8000, "temperature": 0, "usage": {"include": True}},
                  {"Authorization": f"Bearer {e['OPENROUTER_API_KEY']}"})
        if "error" in r:
            raise RuntimeError(str(r["error"])[:300])
        u = r.get("usage", {})
        return r["choices"][0]["message"]["content"], {"in": u.get("prompt_tokens"), "out": u.get("completion_tokens"),
                                                       "cost": float(u.get("cost") or 0.0)}
    return call


CLEAN_SYSTEM = "You extract data from business documents. Answer with a single JSON object only."


def claude_cli(model):
    def call(prompt, image=None, text=None):
        content = []
        if image:
            mime, data = _b64(image)
            content.append({"type": "image", "source": {"type": "base64", "media_type": mime, "data": data}})
        content.append({"type": "text", "text": prompt + (f"\n\nDOCUMENT TEXT:\n{text}" if text else "")})
        msg = {"type": "user", "message": {"role": "user", "content": content}}
        cmd = ["claude", "-p", "--model", model, "--input-format", "stream-json", "--output-format", "stream-json",
               "--verbose", "--tools", "", "--strict-mcp-config", "--no-session-persistence", "--setting-sources", "",
               "--system-prompt", CLEAN_SYSTEM]
        with tempfile.TemporaryDirectory() as empty:
            r = subprocess.run(cmd, input=json.dumps(msg) + "\n", capture_output=True, text=True, timeout=600, cwd=empty)
        init, result = None, None
        for line in r.stdout.splitlines():
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("type") == "system" and ev.get("subtype") == "init":
                init = ev
            if ev.get("type") == "result":
                result = ev
        if not init or init.get("tools") or init.get("mcp_servers"):
            raise RuntimeError(f"not a clean session: tools={init and init.get('tools')} mcp={init and init.get('mcp_servers')}")
        if not result or result.get("is_error"):
            raise RuntimeError(f"claude cli failed rc={r.returncode}: {(result or {}).get('result') or r.stderr[:300]}")
        u = result.get("usage", {})
        return result["result"], {"in": u.get("input_tokens"), "out": u.get("output_tokens"),
                                  "cost": 0.0, "notional_cost": result.get("total_cost_usd"),
                                  "model_used": list((result.get("modelUsage") or {}).keys())}
    return call


def ollama(model):
    """Local open-weights model on the user's Mac (free). Images go in as base64."""
    def call(prompt, image=None, text=None):
        msg = {"role": "user", "content": prompt + (f"\n\nDOCUMENT TEXT:\n{text}" if text else "")}
        if image:
            msg["images"] = [_b64(image)[1]]
        r = _post("http://127.0.0.1:11434/api/chat",
                  {"model": model, "messages": [msg], "stream": False,
                   "options": {"temperature": 0, "num_ctx": 32768, "num_predict": 4096}}, {}, timeout=1800)
        return r["message"]["content"], {"in": r.get("prompt_eval_count"), "out": r.get("eval_count"), "cost": 0.0}
    return call


MODELS = {
    "qwen3-vl-8b-local": {"call": ollama("qwen3-vl:8b-instruct"), "workers": 1, "budget": None, "self_check": True},
    "gpt-5.6-terra": {"call": azure, "workers": 4, "budget": None, "self_check": True},
    "gemini-3.8-flash": {"call": openrouter("google/gemini-3.8-flash"), "workers": 4, "budget": 1.50, "self_check": True},
    "qwen3-vl-32b": {"call": openrouter("qwen/qwen3-vl-32b-instruct"), "workers": 4, "budget": 1.50, "self_check": True},
    "claude-sonnet-5": {"call": claude_cli("sonnet"), "workers": 3, "budget": None, "self_check": True},
    "claude-opus-5.5": {"call": claude_cli("opus"), "workers": 3, "budget": None, "self_check": True},
}
