#!/usr/bin/env python3
"""
Smart Model Router for Olla
Uses a small LLM (qwen2.5:0.5b) to intelligently route queries to the best model.
Sits between OpenCode and Olla: OpenCode -> Smart Router -> Olla -> ollama-arc

How it works:
1. When user sends a request with model="auto" (or the default model), the
   router sends the query to qwen2.5:0.5b which picks the optimal model.
2. The routing model returns the best model name for that specific query.
3. The router replaces the model in the request and forwards to Olla.
4. If the user explicitly selects a specific model, it passes through unchanged.
5. On startup, the router checks for recommended model upgrades and logs suggestions.
"""

import os
import json
import asyncio
import httpx
from typing import Tuple, Set
from fastapi import FastAPI, Request, Response

OLLA_URL = os.environ.get("OLLA_URL", "http://olla:40114")
OLLAMA_DIRECT = os.environ.get("OLLAMA_DIRECT", "http://ollama-arc:11434")
ROUTING_MODEL = os.environ.get("ROUTING_MODEL", "qwen2.5:0.5b")
DEFAULT_MODEL = os.environ.get("DEFAULT_MODEL", "qwen3.5:14b")
LISTEN_HOST = os.environ.get("LISTEN_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "40115"))

CLOUD_KEYWORDS = ("claude", "gemini", "gpt")
AUTO_KEYWORDS = ("auto", "smart-router")

RECOMMENDED_UPGRADES = [
    {
        "trigger": "qwen2.5-coder:14b",
        "upgrade": "qwen3.5-coder:14b",
        "reason": "Newer Qwen 3.5 architecture — better code generation, fewer hallucinations",
        "size": "~9GB (Q4)",
    },
    {
        "trigger": "gemma3:12b",
        "upgrade": "gemma4:9b",
        "reason": "Gemma 4 architecture — superior context handling and instruction following",
        "size": "~5.5GB (Q4)",
    },
    {
        "trigger": None,
        "upgrade": "qwen3.5:14b",
        "reason": "Best general-purpose model for this hardware — improved reasoning over qwen2.5",
        "size": "~9GB (Q4)",
    },
    {
        "trigger": "llama3.1:8b",
        "upgrade": "llama3.2:3b",
        "reason": "Much faster for simple tasks, fits in <2GB — great fallback for quick queries",
        "size": "~2GB (Q4)",
    },
]

MODEL_DESCRIPTIONS = {
    "qwen2.5:14b": "System diagnostics, health checks, monitoring, log analysis",
    "qwen2.5-coder:14b": "Code generation, scripting, debugging, automation",
    "deepseek-r1:14b": "Deep reasoning, analysis, math, root cause explanations",
    "gemma3:12b": "Writing, documentation, summaries, editing, reports",
    "gemma4:27b": "Complex analysis, large context, comprehensive reviews",
    "mistral-small3.2:24b": "Tool calling, structured/JSON output, API work",
    "qwen3.5:14b": "General purpose, everyday tasks, mixed queries",
    "qwen2.5:7b": "General purpose, faster inference",
    "qwen2.5-coder:1.5b-base": "Lightweight code generation, fast",
    "llama3.1:8b": "General purpose, instruction following",
    "llama3:8b": "General purpose, fast inference",
    "mistral:7b": "Fast general purpose, good for quick tasks",
    "phoenix-sysadmin:latest": "System administration, diagnostics, monitoring",
}

SYSTEM_PROMPT = """Map request to model:

{models}

Rules: coding/scripting → best coder model. diagnostics/system → qwen2.5:14b. deep analysis/math → best reasoning model. writing/docs/summaries → best writing model. heavy/complex → largest model. everything else → {default}.

Reply only with the model name. No extra text."""

def describe_model(model_name: str) -> str:
    if model_name in MODEL_DESCRIPTIONS:
        return MODEL_DESCRIPTIONS[model_name]
    n = model_name.lower()
    if "coder" in n:
        return "Code generation and scripting"
    if "deepseek" in n or "qwq" in n:
        return "Deep reasoning and analysis"
    if "gemma" in n:
        return "Writing and long-form content"
    if "mistral" in n or "qwen" in n:
        return "General purpose, balanced performance"
    if "llama" in n:
        return "General purpose, instruction following"
    if "phi" in n:
        return "Lightweight, fast responses"
    if "nomic-embed" in n:
        return "Text embeddings"
    return "General purpose"

_available_models: Set[str] = set()
_routing_available: bool = False
_refresh_task = None
_REFRESH_INTERVAL = 300  # seconds

app = FastAPI(title="Smart Model Router")


async def fetch_available_models() -> Set[str]:
    for attempt in range(5):
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{OLLAMA_DIRECT}/api/tags")
                if resp.status_code == 200:
                    data = resp.json()
                    return {m["name"] for m in data.get("models", [])}
        except Exception as e:
            if attempt < 4:
                await asyncio.sleep(2 ** attempt)
            else:
                print(f"[SmartRouter] Could not fetch models after 5 attempts: {e}")
    return set()


async def refresh_models():
    global _available_models, _routing_available
    fresh = await fetch_available_models()
    if fresh:
        old_count = len(_available_models)
        _available_models = fresh
        _routing_available = ROUTING_MODEL in _available_models
        new_count = len(_available_models)
        if old_count != new_count:
            print(f"[SmartRouter] Models refreshed: {new_count} available ({old_count} previous)")
            _log_recommendations()
    else:
        print(f"[SmartRouter] Model refresh returned empty — keeping previous list ({len(_available_models)} models)")


def _log_recommendations():
    for rec in RECOMMENDED_UPGRADES:
        if rec["upgrade"] in _available_models:
            continue
        if rec["trigger"] is None or rec["trigger"] in _available_models:
            print(f"[SmartRouter] RECOMMEND: pull {rec['upgrade']}")
            print(f"[SmartRouter]   {rec['reason']} ({rec['size']})")
            print(f"[SmartRouter]   docker exec ollama-arc ollama pull {rec['upgrade']}")


async def periodic_refresh():
    while True:
        await asyncio.sleep(_REFRESH_INTERVAL)
        await refresh_models()


def _routable_models() -> dict:
    """Models the routing LLM should consider — exclude itself, embeddings, etc."""
    skip = {ROUTING_MODEL, "nomic-embed-text:latest", "smart-router:latest"}
    return {m: describe_model(m) for m in sorted(_available_models) if m not in skip}


def _resolve_model(raw: str) -> str:
    """Best-effort match: exact → prefix → fallback"""
    name = raw.split()[0].strip().lower().rstrip(".,;:#")
    if name in _available_models:
        return name
    prefix = name.split(":")[0] if ":" in name else name
    matches = sorted(m for m in _available_models if m.startswith(prefix))
    if matches:
        return matches[0]
    return ""


async def classify_with_llm(query: str, attempt: int = 0) -> Tuple[str, str]:
    """Use the routing LLM to select the best model for the query."""
    available = _routable_models()
    if not available:
        return DEFAULT_MODEL, "default(no-models)"

    default_for_prompt = DEFAULT_MODEL if DEFAULT_MODEL in available else next(iter(available))
    model_list = "\n".join(f"- {m}  # {d}" for m, d in available.items())
    prompt = SYSTEM_PROMPT.format(models=model_list, default=default_for_prompt)

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            resp = await client.post(
                f"{OLLAMA_DIRECT}/v1/chat/completions",
                json={
                    "model": ROUTING_MODEL,
                    "messages": [
                        {"role": "system", "content": "You are a query router. Output only the model name."},
                        {"role": "user", "content": f"{prompt}\nUser request: {query}\nModel:"},
                    ],
                    "temperature": 0.0,
                    "max_tokens": 32,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            raw = data["choices"][0]["message"]["content"].strip()
            model_name = _resolve_model(raw)

            if model_name:
                return model_name, model_name

            # Model not in our list — might be stale. Refresh and retry once.
            if attempt == 0:
                print(f"[SmartRouter] LLM returned '{raw}' — not in available set, refreshing...")
                await refresh_models()
                return await classify_with_llm(query, attempt=1)

            print(f"[SmartRouter] LLM returned '{raw}' — unrecognized, using fallback")
            fallback = next(iter(_routable_models().keys()), DEFAULT_MODEL)
            return fallback, "default(unknown)"

    except Exception as e:
        print(f"[SmartRouter] LLM routing failed: {e}, using default")
        return DEFAULT_MODEL, "default(error)"


def should_route(body: bytes, path: str) -> bool:
    """Only auto-classify default-model requests. Explicit choices pass through."""
    if not path.startswith("v1/chat/completions"):
        return False
    try:
        data = json.loads(body)
        model = data.get("model", "")
        if any(c in model for c in CLOUD_KEYWORDS):
            return False
        return not model or model == DEFAULT_MODEL or model in AUTO_KEYWORDS
    except (json.JSONDecodeError, KeyError):
        return False


async def handle_request(body: bytes) -> bytes:
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return body

    messages = data.get("messages", [])
    if not messages:
        return body

    user_message = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            content = m.get("content", "")
            if isinstance(content, str):
                user_message = content
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        user_message = part.get("text", "")
                        break
            break

    if not user_message:
        return body

    if _routing_available:
        model, reason = await classify_with_llm(user_message)
    else:
        model = DEFAULT_MODEL
        reason = "default(no-router)"

    data["model"] = model
    print(f"[SmartRouter] {model} ({reason}) <- \"{user_message[:60]}...\"")
    return json.dumps(data).encode()


@app.on_event("startup")
async def startup():
    global _available_models, _routing_available, _refresh_task

    print(f"[SmartRouter] Checking for routing model '{ROUTING_MODEL}'...")
    await refresh_models()

    if _routing_available:
        print(f"[SmartRouter] LLM routing ENABLED ({ROUTING_MODEL})")
    else:
        print(f"[SmartRouter] {ROUTING_MODEL} not available — using {DEFAULT_MODEL} for all requests")
        print(f"[SmartRouter] Pull with: docker exec ollama-arc ollama pull {ROUTING_MODEL}")

    _log_recommendations()

    _refresh_task = asyncio.create_task(periodic_refresh())
    print(f"[SmartRouter] Auto-refresh every {_REFRESH_INTERVAL}s")


@app.api_route("/health", methods=["GET"])
async def health():
    return {
        "status": "ok",
        "routing_model": ROUTING_MODEL,
        "routing_available": _routing_available,
        "default_model": DEFAULT_MODEL,
        "available_models": sorted(_available_models),
    }


@app.api_route("/refresh", methods=["POST"])
async def refresh():
    await refresh_models()
    return {
        "status": "ok",
        "routing_available": _routing_available,
        "available_models": sorted(_available_models),
    }


@app.api_route("/v1/models", methods=["GET"])
async def list_models():
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
        resp = await client.get(f"{OLLAMA_DIRECT}/v1/models")
        return Response(content=resp.content, status_code=resp.status_code, headers=dict(resp.headers))


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def proxy(request: Request, path: str):
    if path.startswith("v1/"):
        body = await request.body()
        if body and should_route(body, path):
            body = await handle_request(body)

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=10.0)
        ) as client:
            url = f"{OLLAMA_DIRECT}/{path}"
            headers = dict(request.headers)
            headers.pop("host", None)
            headers.pop("content-length", None)

            response = await client.request(
                method=request.method,
                url=url,
                headers=headers,
                content=body,
                params=dict(request.query_params),
            )
            return Response(
                content=response.content,
                status_code=response.status_code,
                headers=dict(response.headers),
            )

    return Response(status_code=404)


def main():
    import uvicorn
    print(f"[SmartRouter] Listening on {LISTEN_HOST}:{LISTEN_PORT}")
    print(f"[SmartRouter] Forwarding to Olla at {OLLA_URL}")
    print(f"[SmartRouter] Routing model: {ROUTING_MODEL}")
    print(f"[SmartRouter] Default model: {DEFAULT_MODEL}")
    uvicorn.run(app, host=LISTEN_HOST, port=LISTEN_PORT)


if __name__ == "__main__":
    main()
