#!/usr/bin/env python3
"""
Smart Model Router for Olla
Auto-routes queries to the best local model based on content analysis.
Sits between OpenCode and Olla: OpenCode -> Smart Router -> Olla -> ollama-arc

Routing:
- Diagnostics      -> qwen2.5:14b      (fast, reliable for sysadmin)
- Scripting/Code   -> qwen2.5-coder:14b (code generation)
- Reasoning        -> deepseek-r1:14b   (chain-of-thought)
- Longform/Logs    -> gemma3:12b        (long context, summaries)
- Heavy lifting    -> gemma4:27b        (complex analysis, large context)
- Tool calling     -> mistral-small3.2:24b (strong function calling)
- Default          -> qwen3.5:14b       (improved reasoning, best all-rounder)
"""

import os
import re
import json
import httpx
from typing import Tuple
from fastapi import FastAPI, Request, Response

OLLA_URL = os.environ.get("OLLA_URL", "http://olla:40114")
LISTEN_HOST = os.environ.get("LISTEN_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "40115"))

MODELS = {
    "diagnostics":      "qwen2.5:14b",
    "scripting":        "qwen2.5-coder:14b",
    "reasoning":        "deepseek-r1:14b",
    "longform":         "gemma3:12b",
    "heavy":            "gemma4:27b",
    "tools":            "mistral-small3.2:24b",
    "default":          "qwen3.5:14b",
}

PATTERNS = {
    "diagnostics": [
        r"\b(diagnos|health|status|check|monitor|alert|reachable|unreachable|uptime)\b",
        r"\b(system report|get_all|list models|loaded models|vram)\b",
        r"\b(is .+ running|is .+ up|is .+ down|ping)\b",
        r"\b(ollama|open.?webui|pipeline|container|docker)\b",
        r"\b(gpu|cpu|memory|ram|disk usage)\b",
        r"\b(logs? file|journal|syslog|dmesg|kern)\b",
    ],
    "scripting": [
        r"\b(script|bash|shell|command|cron|systemd|service|config)\b",
        r"\b(yaml|compose|dockerfile|ansible|terraform)\b",
        r"\b(fix|debug|error|traceback|exception|failed|exit code)\b",
        r"\b(install|setup|configure|deploy|update|upgrade)\b",
        r"\b(python|javascript|typescript|code|function|class|import)\b",
        r"\b(write a|create a|generate|implement|refactor)\b.*\b(function|class|script|module)\b",
    ],
    "reasoning": [
        r"\b(why|root cause|explain|analyze|compare|optimize|recommend)\b",
        r"\b(should i|what would you|best approach|pros and cons|trade.?off)\b",
        r"\b(performance|bottleneck|slow|latency|memory leak|high cpu)\b",
        r"\b(architecture|design|strategy|best practice|decouple|refactor)\b",
        r"\b(math|calculate|derive|proof|theorem|logic|reason)\b",
    ],
    "longform": [
        r"\b(log|logs|summarize|summary|document|report)\b",
        r"\b(what does this mean|walk me through|step by step|explain this)\b",
        r"\b(write a|draft a|create a document|generate a report)\b",
        r"\b(review|proofread|edit|rewrite|format|structure)\b",
    ],
    "heavy": [
        r"\b(analyze this entire|full analysis|comprehensive review)\b",
        r"\b(large context|long document|big codebase|entire project)\b",
        r"\b(complex|sophisticated|architectural|system.?wide)\b",
    ],
}


def classify(text: str) -> Tuple[str, str]:
    t = text.lower()
    # Score each category
    best_category = "default"
    best_score = 0
    for category, patterns in PATTERNS.items():
        score = 0
        for pattern in patterns:
            matches = re.findall(pattern, t)
            score += len(matches)
        if score > best_score:
            best_score = score
            best_category = category
    return MODELS[best_category], best_category


def should_route(body: bytes, path: str) -> bool:
    """Only route chat completion requests for local models."""
    if not path.startswith("v1/chat/completions"):
        return False
    try:
        data = json.loads(body)
        model = data.get("model", "")
        # Skip cloud models — route those directly
        if any(c in model for c in ("claude", "gemini", "gpt")):
            return False
        return True
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
            # Use text content from various formats
            if isinstance(content, str):
                user_message = content
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        user_message = part.get("text", "")
                        break
            break

    if user_message:
        model, reason = classify(user_message)
        data["model"] = model
        print(f"[SmartRouter] '{user_message[:80]}...' -> {model} ({reason})")
        return json.dumps(data).encode()

    return body


app = FastAPI(title="Smart Model Router")


@app.api_route("/health", methods=["GET"])
async def health():
    return {"status": "ok"}


@app.api_route("/v1/models", methods=["GET"])
async def list_models():
    """Return available models from Olla."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
        resp = await client.get(f"{OLLA_URL}/olla/ollama/v1/models")
        return Response(content=resp.content, status_code=resp.status_code, headers=dict(resp.headers))


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def proxy(request: Request, path: str):
    if path.startswith("v1/"):
        body = await request.body()

        if body and should_route(body, path):
            body = await handle_request(body)

        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=10.0)) as client:
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
    print(f"[SmartRouter] Models: {', '.join(MODELS.values())}")
    uvicorn.run(app, host=LISTEN_HOST, port=LISTEN_PORT)


if __name__ == "__main__":
    main()
