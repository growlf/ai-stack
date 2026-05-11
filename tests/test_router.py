"""
Tests for router/smart_model_router.py

Tests classify(), should_route(), handle_request(), CapabilityRegistry._infer_tools(),
and registry fallback behaviour. No external services needed.
"""

import json
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "router"))
from smart_model_router import (
    classify, should_route, handle_request, _parse_body,
    MODELS, CapabilityRegistry,
    _TOOL_CAPABLE_FAMILIES, _TOOL_INCAPABLE_FAMILIES,
)


# ---------------------------------------------------------------------------
# classify() — keyword-based model selection
# ---------------------------------------------------------------------------

class TestClassify:
    def test_diagnostic_keywords_route_to_diagnostics_model(self):
        model, category = classify("check if ollama is running")
        assert category == "diagnostics"
        assert model == MODELS["diagnostics"]

    def test_code_keywords_route_to_scripting_model(self):
        model, category = classify("write a python script to parse JSON")
        assert category == "scripting"
        assert model == MODELS["scripting"]

    def test_reasoning_keywords_route_to_reasoning_model(self):
        model, category = classify("explain why this architecture is slow")
        assert category == "reasoning"
        assert model == MODELS["reasoning"]

    def test_longform_keywords_route_to_longform_model(self):
        model, category = classify("summarize the logs from last night")
        assert category == "longform"
        assert model == MODELS["longform"]

    def test_heavy_analysis_routes_to_heavy_model(self):
        # "comprehensive review" (heavy p1) + "entire project" (heavy p2) = score 2
        # beats longform ("review" = 1) and reasoning (0) without ambiguous tie
        model, category = classify("comprehensive review of the entire project")
        assert category == "heavy"
        assert model == MODELS["heavy"]

    def test_empty_input_routes_to_default(self):
        model, category = classify("")
        assert category == "default"
        assert model == MODELS["default"]

    def test_generic_input_routes_to_default(self):
        model, category = classify("hello")
        assert category == "default"
        assert model == MODELS["default"]

    def test_case_insensitive_matching(self):
        model1, cat1 = classify("CHECK the system status")
        model2, cat2 = classify("check the system status")
        assert cat1 == cat2
        assert model1 == model2

    def test_long_text_is_capped_at_500_chars(self):
        """Very long inputs should not cause excessive regex work."""
        # "health" is an exact whole-word match; "diagnose" is not ("diagnos" + \b fails on "diagnose")
        long_text = "health " + "x" * 10_000
        model, category = classify(long_text)
        assert category == "diagnostics"

    def test_returns_valid_model_name(self):
        model, _ = classify("check memory usage")
        assert model in MODELS.values()


# ---------------------------------------------------------------------------
# _parse_body() — JSON parsing helper
# ---------------------------------------------------------------------------

class TestParseBody:
    def test_parses_valid_json(self):
        body = json.dumps({"model": "test"}).encode()
        result = _parse_body(body)
        assert result == {"model": "test"}

    def test_returns_none_for_invalid_json(self):
        assert _parse_body(b"not json") is None

    def test_returns_none_for_empty_bytes(self):
        assert _parse_body(b"") is None


# ---------------------------------------------------------------------------
# should_route() — now takes a parsed dict
# ---------------------------------------------------------------------------

class TestShouldRoute:
    def _data(self, model: str) -> dict:
        return {"model": model, "messages": [{"role": "user", "content": "hi"}]}

    def test_routes_local_model_on_chat_endpoint(self):
        assert should_route(self._data("qwen3.5:14b"), "v1/chat/completions") is True

    def test_does_not_route_claude_model(self):
        assert should_route(self._data("claude-3-5-sonnet"), "v1/chat/completions") is False

    def test_does_not_route_gpt_model(self):
        assert should_route(self._data("gpt-4o"), "v1/chat/completions") is False

    def test_does_not_route_gemini_model(self):
        assert should_route(self._data("gemini-pro"), "v1/chat/completions") is False

    def test_does_not_route_non_chat_path(self):
        assert should_route(self._data("qwen3.5:14b"), "v1/models") is False

    def test_does_not_route_health_path(self):
        assert should_route(self._data("qwen3.5:14b"), "health") is False


# ---------------------------------------------------------------------------
# handle_request() — tool-detection and routing (takes + returns dict)
# ---------------------------------------------------------------------------

class TestHandleRequest:
    def _data(self, content: str, tools=None, functions=None) -> dict:
        d = {"model": "qwen3.5:14b", "messages": [{"role": "user", "content": content}]}
        if tools is not None:
            d["tools"] = tools
        if functions is not None:
            d["functions"] = functions
        return d

    @pytest.mark.asyncio
    async def test_tool_request_routes_to_tools_model(self):
        """Tool-bearing requests must never land on a non-tool model."""
        tools = [{"type": "function", "function": {"name": "get_weather"}}]
        result = await handle_request(self._data("what is the weather?", tools=tools))
        assert result["model"] == MODELS["tools"], (
            f"Tool request routed to {result['model']} — must be {MODELS['tools']}"
        )

    @pytest.mark.asyncio
    async def test_functions_field_forces_tools_model(self):
        """Legacy 'functions' field should also trigger tool-model routing."""
        functions = [{"name": "search", "description": "Search"}]
        result = await handle_request(self._data("search for cats", functions=functions))
        assert result["model"] == MODELS["tools"]

    @pytest.mark.asyncio
    async def test_reasoning_without_tools_routes_to_reasoning_model(self):
        """deepseek-r1 is acceptable when tools are NOT in the request."""
        result = await handle_request(self._data("explain why this is slow and analyze the architecture"))
        assert result["model"] == MODELS["reasoning"]

    @pytest.mark.asyncio
    async def test_core_regression_tool_request_does_not_hit_deepseek(self):
        """The original bug: tool request must never route to deepseek-r1."""
        tools = [{"type": "function", "function": {"name": "run_cmd"}}]
        result = await handle_request(
            self._data("analyze the architecture and recommend changes", tools=tools)
        )
        assert result["model"] != MODELS["reasoning"], (
            "deepseek-r1 does not support tools — must not receive tool-bearing requests"
        )

    @pytest.mark.asyncio
    async def test_no_messages_returns_data_unchanged(self):
        data = {"model": "qwen3.5:14b", "messages": []}
        result = await handle_request(data)
        assert result is data

    @pytest.mark.asyncio
    async def test_no_user_message_returns_data_unchanged(self):
        data = {"model": "qwen3.5:14b", "messages": [{"role": "system", "content": "You are helpful."}]}
        result = await handle_request(data)
        assert result is data

    @pytest.mark.asyncio
    async def test_multipart_content_format_is_handled(self):
        """Content as a list of typed parts should still classify correctly."""
        data = {
            "model": "qwen3.5:14b",
            "messages": [{
                "role": "user",
                "content": [{"type": "text", "text": "diagnose the gpu health"}]
            }]
        }
        result = await handle_request(data)
        assert result["model"] == MODELS["diagnostics"]

    @pytest.mark.asyncio
    async def test_returns_dict_not_bytes(self):
        """handle_request now returns a dict, not bytes."""
        result = await handle_request(self._data("hello world"))
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# CapabilityRegistry._infer_tools() — static method, no I/O
# ---------------------------------------------------------------------------

class TestInferTools:
    """
    _infer_tools() is the last line of defense when Olla is unreachable.
    These cases cover the known incapable families plus optimistic default.
    """

    def test_deepseek_r1_does_not_support_tools(self):
        assert CapabilityRegistry._infer_tools("deepseek-r1:14b") is False

    def test_deepseek_r2_does_not_support_tools(self):
        assert CapabilityRegistry._infer_tools("deepseek-r2:70b") is False

    def test_gemma3_does_not_support_tools(self):
        assert CapabilityRegistry._infer_tools("gemma3:12b") is False

    def test_gemma4_does_not_support_tools(self):
        assert CapabilityRegistry._infer_tools("gemma4:27b") is False

    def test_nomic_embed_does_not_support_tools(self):
        assert CapabilityRegistry._infer_tools("nomic-embed-text:latest") is False

    def test_mistral_supports_tools(self):
        assert CapabilityRegistry._infer_tools("mistral-small3.2:24b") is True

    def test_llama3_supports_tools(self):
        assert CapabilityRegistry._infer_tools("llama3.1:8b") is True

    def test_qwen2_5_supports_tools(self):
        assert CapabilityRegistry._infer_tools("qwen2.5:14b") is True

    def test_qwen3_supports_tools(self):
        assert CapabilityRegistry._infer_tools("qwen3.5:14b") is True

    def test_phi4_supports_tools(self):
        assert CapabilityRegistry._infer_tools("phi4:14b") is True

    def test_unknown_model_defaults_to_tools_capable(self):
        """Optimistic default: unknown families assumed tool-capable."""
        assert CapabilityRegistry._infer_tools("unknown-new-model:7b") is True

    def test_tag_stripped_before_family_check(self):
        """The ':tag' suffix must not affect family detection."""
        assert CapabilityRegistry._infer_tools("deepseek-r1:latest") is False
        assert CapabilityRegistry._infer_tools("mistral:latest") is True


# ---------------------------------------------------------------------------
# CapabilityRegistry — in-memory state (no Olla calls)
# ---------------------------------------------------------------------------

class TestCapabilityRegistryState:
    def _make_registry_with(self, models: dict[str, bool]) -> CapabilityRegistry:
        """Build a registry with pre-populated state."""
        from smart_model_router import ModelCapabilities
        reg = CapabilityRegistry()
        reg._registry = {
            name: ModelCapabilities(name=name, tools=capable)
            for name, capable in models.items()
        }
        return reg

    def test_supports_tools_returns_true_for_capable_model(self):
        reg = self._make_registry_with({"mistral-small3.2:24b": True})
        assert reg.supports_tools("mistral-small3.2:24b") is True

    def test_supports_tools_returns_false_for_incapable_model(self):
        reg = self._make_registry_with({"deepseek-r1:14b": False})
        assert reg.supports_tools("deepseek-r1:14b") is False

    def test_supports_tools_falls_back_to_infer_for_unknown_model(self):
        """When model not in registry, infer from name."""
        reg = CapabilityRegistry()  # empty registry
        assert reg.supports_tools("deepseek-r1:14b") is False
        assert reg.supports_tools("mistral:7b") is True

    def test_is_available_returns_true_when_model_in_registry(self):
        reg = self._make_registry_with({"qwen3.5:14b": True})
        assert reg.is_available("qwen3.5:14b") is True

    def test_is_available_returns_false_for_missing_model(self):
        reg = self._make_registry_with({"qwen3.5:14b": True})
        assert reg.is_available("deepseek-r1:14b") is False

    def test_is_available_returns_true_when_registry_empty(self):
        """Empty registry (Olla not yet reached) assumes all available."""
        reg = CapabilityRegistry()
        assert reg.is_available("any-model:7b") is True

    def test_best_tools_model_returns_first_capable(self):
        reg = self._make_registry_with({
            "deepseek-r1:14b": False,
            "mistral-small3.2:24b": True,
        })
        result = reg.best_tools_model()
        assert result == "mistral-small3.2:24b"

    def test_best_tools_model_returns_none_when_none_capable(self):
        reg = self._make_registry_with({
            "deepseek-r1:14b": False,
            "gemma3:12b": False,
        })
        assert reg.best_tools_model() is None

    def test_best_tools_model_returns_none_when_registry_empty(self):
        reg = CapabilityRegistry()
        assert reg.best_tools_model() is None
