"""
title: Smart Model Router
description: Auto-routes queries to the best local model based on content analysis.
             Diagnostics → qwen2.5:14b, Scripting → qwen2.5-coder:14b,
             Reasoning → deepseek-r1:14b, Longform → gemma3:12b
version: 0.3.1
"""

from pydantic import BaseModel, Field
from typing import Optional
import re


class Pipeline:
    class Valves(BaseModel):
        diagnostics_model: str = Field(
            default="qwen2.5:14b",
            description="Tool calling, health checks, system status, alerts"
        )
        scripting_model: str = Field(
            default="qwen2.5-coder:14b",
            description="Configs, scripts, shell commands, code"
        )
        reasoning_model: str = Field(
            default="deepseek-r1:14b",
            description="Complex troubleshooting, root cause analysis"
        )
        longform_model: str = Field(
            default="gemma3:12b",
            description="Long logs, summaries, documentation"
        )
        debug: bool = Field(
            default=False,
            description="Prepend routing decision to each response"
        )

    def __init__(self):
        self.name = "Smart Model Router"
        self.type = "filter"
        self.id = "smart_model_router"
        self.valves = self.Valves()

    async def on_startup(self):
        print("[Router] Pipeline started")

    async def on_shutdown(self):
        print("[Router] Pipeline stopped")

    def _classify(self, text: str) -> tuple[str, str]:
        """Classify query and return (model, reason)."""
        t = text.lower()

        diagnostic_patterns = [
            r"\b(diagnos|health|status|check|monitor|alert|reachable|unreachable|uptime)\b",
            r"\b(system report|get_all|list models|loaded models|vram)\b",
            r"\b(is .+ running|is .+ up|is .+ down|ping)\b",
            r"\b(ollama|open.?webui|pipeline|container|docker)\b",
            r"\b(gpu|cpu|memory|ram|disk usage)\b",
        ]
        for p in diagnostic_patterns:
            if re.search(p, t):
                return self.valves.diagnostics_model, "diagnostics"

        scripting_patterns = [
            r"\b(script|bash|shell|command|cron|systemd|service|config)\b",
            r"\b(yaml|compose|dockerfile|ansible|terraform)\b",
            r"\b(fix|debug|error|traceback|exception|failed|exit code)\b",
            r"\b(install|setup|configure|deploy|update|upgrade)\b",
            r"\b(python|javascript|typescript|code|function|class|import)\b",
        ]
        for p in scripting_patterns:
            if re.search(p, t):
                return self.valves.scripting_model, "scripting"

        reasoning_patterns = [
            r"\b(why|root cause|explain|analyze|compare|optimize|recommend)\b",
            r"\b(should i|what would you|best approach|pros and cons)\b",
            r"\b(performance|bottleneck|slow|latency|memory leak|high cpu)\b",
            r"\b(architecture|design|strategy|best practice|tradeoff)\b",
        ]
        for p in reasoning_patterns:
            if re.search(p, t):
                return self.valves.reasoning_model, "reasoning"

        longform_patterns = [
            r"\b(log|logs|summarize|summary|document|report)\b",
            r"\b(what does this mean|walk me through|step by step|explain this)\b",
            r"\b(write a|draft a|create a document|generate a report)\b",
        ]
        for p in longform_patterns:
            if re.search(p, t):
                return self.valves.longform_model, "longform"

        # Default — diagnostics model handles most sysadmin queries well
        return self.valves.diagnostics_model, "default"

    async def inlet(self, body: dict, user: Optional[dict] = None) -> dict:
        """Intercept request, classify, and override model before it goes out."""
        messages = body.get("messages", [])
        if not messages:
            return body

        # Find the last user message
        user_message = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                user_message = m.get("content", "")
                break

        if not user_message:
            return body

        model, reason = self._classify(user_message)
        body["model"] = model
        print(f"[Router] '{user_message[:80]}' → {model} ({reason})")

        if self.valves.debug:
            debug_msg = f"[Router → {model} ({reason})]"
            if messages and messages[0].get("role") == "system":
                messages[0]["content"] = debug_msg + "\n" + messages[0]["content"]
            else:
                messages.insert(0, {"role": "system", "content": debug_msg})
            body["messages"] = messages

        return body
