#!/usr/bin/env python3
"""
Smart Model Router for Olla
Uses qwen2.5:1.5b as a classifier to select the best model for each query.
Sits between OpenCode and Olla: OpenCode -> Smart Router -> Olla -> ollama-arc

Routing:
- Diagnostics      -> qwen2.5:1.5b     (small, fast for sysadmin)
- Scripting/Code   -> qwen2.5-coder:14b (code generation)
- Reasoning        -> deepseek-r1:14b   (chain-of-thought, no tools)
- Longform/Logs    -> gemma3:12b        (long context, summaries, no tools)
- Heavy lifting    -> gemma3:12b        (complex analysis, large context, no tools)
- Tool calling     -> llama3.1:8b       (strong function calling)
- Default          -> qwen2.5:1.5b      (general conversation)
"""

import os
import json
import asyncio
import time
import httpx
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Optional, Tuple
from fastapi import FastAPI, Request, Response

OLLA_URL = os.environ.get("OLLA_URL", "http://olla:40114")
LISTEN_HOST = os.environ.get("LISTEN_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "40115"))
CAPABILITY_REFRESH_INTERVAL = int(os.environ.get("CAPABILITY_REFRESH_INTERVAL", "300"))

MODELS = {
    "scripting":   "qwen2.5-coder:14b",
    "reasoning":   "deepseek-r1:14b",
    "longform":    "gemma3:12b",
    "heavy":       "gemma3:12b",
    "tools":       "llama3.1:8b",
    "default":     "qwen2.5:7b",
}

# Model families known to support / not support tool calling.
# Used as fallback when the Olla API is unavailable.
_TOOL_CAPABLE_FAMILIES = {
    "mistral", "llama3", "llama3.1", "llama3.2", "llama3.3",
    "qwen2.5", "qwen3", "phi3.5", "phi4", "command-r",
    "aya", "granite3", "nemotron", "hermes",
}
_TOOL_INCAPABLE_FAMILIES = {
    "deepseek-r1", "deepseek-r2",
    "gemma", "gemma2", "gemma3", "gemma4",
    "nomic", "mxbai", "snowflake", "all-minilm",
}

_CLASSIFY_MODEL = os.environ.get("CLASSIFY_MODEL", "qwen2.5:1.5b")

_CLASSIFY_SYSTEM_PROMPT = (
    "Categorize this user message into EXACTLY ONE category:\n"
    "- scripting: code, bash, yaml, config, debugging, automation — asking to write or run code\n"
    "- reasoning: analysis, explanation, comparison, architecture, math — complex analytical tasks\n"
    "- longform: summarization, document writing, editing, reports — extended writing tasks\n"
    "- default: general conversation, questions about files/directories, asking about the system, or anything else\n"
    "IMPORTANT: Questions about files, directories, paths, or the local environment are ALWAYS 'default'.\n"
    "Simple greetings like 'hi' are ALWAYS 'default'.\n"
    "Reply with ONLY the category name."
)


@dataclass
class ModelCapabilities:
    name: str
    tools: bool
    available: bool = True


class CapabilityRegistry:
    """
    Discovers which models are loaded in Olla and their tool-calling capability.

    On startup and periodically, queries Olla's /v1/models endpoint to build a
    live list of available models. Tool support is inferred from the model family
    name; this avoids requiring a separate /api/show call per model.

    If Olla is unreachable, the registry falls back to the static MODELS dict —
    routing still works, it just won't know about availability.
    """

    def __init__(self):
        self._registry: dict[str, ModelCapabilities] = {}
        self._last_refresh: float = 0.0

    @staticmethod
    def _infer_tools(model_name: str) -> bool:
        base = model_name.split(":")[0].lower()
        for family in _TOOL_INCAPABLE_FAMILIES:
            if base.startswith(family) or family in base:
                return False
        for family in _TOOL_CAPABLE_FAMILIES:
            if base.startswith(family) or family in base:
                return True
        return True  # optimistic default for unknown families

    async def refresh(self) -> None:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
                resp = await client.get(f"{OLLA_URL}/olla/ollama/v1/models")
                resp.raise_for_status()
                data = resp.json()
                models = data.get("data", [])
                fresh: dict[str, ModelCapabilities] = {}
                for m in models:
                    name = m.get("id", "")
                    if name:
                        fresh[name] = ModelCapabilities(
                            name=name,
                            tools=self._infer_tools(name),
                            available=True,
                        )
                self._registry = fresh
                self._last_refresh = time.monotonic()
                print(f"[SmartRouter] Capability registry refreshed — {len(fresh)} models: "
                      f"{', '.join(fresh)}")
        except Exception as exc:
            print(f"[SmartRouter] Capability refresh failed ({exc}) — "
                  f"routing with static MODELS fallback")

    def supports_tools(self, model_name: str) -> bool:
        cap = self._registry.get(model_name)
        return cap.tools if cap else self._infer_tools(model_name)

    def is_available(self, model_name: str) -> bool:
        if not self._registry:
            return True  # no data yet — assume available
        return model_name in self._registry

    def best_tools_model(self) -> Optional[str]:
        """Return the first available model that supports tool calling."""
        for name, cap in self._registry.items():
            if cap.tools and cap.available:
                return name
        return None

    def best_available(self, exclude: str = "") -> Optional[str]:
        """Return any available model, optionally excluding a specific name."""
        for name in self._registry:
            if name != exclude:
                return name
        return None

    @property
    def stale(self) -> bool:
        return (time.monotonic() - self._last_refresh) > CAPABILITY_REFRESH_INTERVAL


registry = CapabilityRegistry()


async def _periodic_refresh():
    while True:
        await asyncio.sleep(CAPABILITY_REFRESH_INTERVAL)
        if registry.stale:
            await registry.refresh()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await registry.refresh()
    task = asyncio.create_task(_periodic_refresh())
    yield
    task.cancel()


async def classify(text: str) -> Tuple[str, str]:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            resp = await client.post(
                f"{OLLA_URL}/olla/ollama/v1/chat/completions",
                json={
                    "model": _CLASSIFY_MODEL,
                    "messages": [
                        {"role": "system", "content": _CLASSIFY_SYSTEM_PROMPT},
                        {"role": "user", "content": text[:1000]},
                    ],
                    "temperature": 0,
                    "max_tokens": 10,
                },
            )
            resp.raise_for_status()
            category = resp.json()["choices"][0]["message"]["content"].strip().lower()
            if category in MODELS:
                return MODELS[category], category
    except Exception as exc:
        print(f"[SmartRouter] Classifier call failed ({exc}) — using default")
    return MODELS["default"], "default"


def _parse_body(body: bytes) -> Optional[dict]:
    """Parse request body once. Returns None on invalid JSON."""
    try:
        return json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return None


def should_route(data: dict, path: str) -> bool:
    """Only route chat completion requests for local models."""
    if not path.startswith("v1/chat/completions"):
        return False
    model = data.get("model", "")
    return not any(c in model for c in ("claude", "gemini", "gpt"))


async def handle_request(data: dict) -> dict:
    messages = data.get("messages", [])
    if not messages:
        return data

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
        return data

    needs_tools = bool(data.get("tools") or data.get("functions"))

    # Always classify first — don't short-circuit on tool presence alone
    model, reason = await classify(user_message)

    # Only force the tools model when tools are needed for the task
    if needs_tools and reason == "scripting":
        preferred = MODELS["tools"]
        if not registry.supports_tools(preferred):
            fallback = registry.best_tools_model()
            if fallback:
                print(f"[SmartRouter] {preferred} has no tool support, using {fallback}")
                preferred = fallback
            else:
                print(f"[SmartRouter] WARNING: no tool-capable model available; "
                      f"sending {preferred} anyway (may fail)")
        model, reason = preferred, f"tools ({reason})"

    if needs_tools and not registry.supports_tools(model):
        fallback = "qwen2.5:14b"
        print(f"[SmartRouter] {model} does not support tools, falling back to {fallback}")
        model, reason = fallback, f"tools-fallback ({reason})"

    if not registry.is_available(model):
        fallback = registry.best_available(exclude=model) or MODELS["default"]
        print(f"[SmartRouter] {model} not available, falling back to {fallback}")
        model, reason = fallback, f"fallback ({reason})"

    data["model"] = model
    print(f"[SmartRouter] '{user_message[:80]}' -> {model} ({reason})")
    return data


app = FastAPI(title="Smart Model Router", lifespan=lifespan)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "models_loaded": len(registry._registry),
        "registry_stale": registry.stale,
    }


@app.get("/v1/models")
async def list_models():
    """Return available models from Olla."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
        resp = await client.get(f"{OLLA_URL}/olla/ollama/v1/models")
        return Response(content=resp.content, status_code=resp.status_code,
                        headers=dict(resp.headers))


@app.get("/v1/router/capabilities")
async def list_capabilities():
    """Return the current capability registry — useful for debugging routing decisions."""
    await registry.refresh()
    return {
        "models": [
            {"name": cap.name, "tools": cap.tools, "available": cap.available}
            for cap in registry._registry.values()
        ],
        "preferred_tools_model": registry.best_tools_model(),
        "configured_models": MODELS,
    }


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def proxy(request: Request, path: str):
    if path.startswith("v1/"):
        raw_body = await request.body()
        body = raw_body  # sent downstream unless routing modifies it

        # Parse once — reuse the parsed dict for both the routing check and mutation
        if raw_body:
            data = _parse_body(raw_body)
            if data and should_route(data, path):
                routed = await handle_request(data)
                body = json.dumps(routed).encode()

        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10.0, read=300.0,
                                                             write=10.0, pool=10.0)) as client:
            url = f"{OLLA_URL}/olla/ollama/{path}"
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
    print(f"[SmartRouter] Capability refresh interval: {CAPABILITY_REFRESH_INTERVAL}s")
    uvicorn.run(app, host=LISTEN_HOST, port=LISTEN_PORT)


if __name__ == "__main__":
    main()
