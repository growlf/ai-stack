"""
Tests for pipelines/smart_model_router.py

Covers:
- Pipeline initialisation and Valves defaults
- _classify: every pattern category (diagnostics, scripting, reasoning, longform, default)
- _classify: case-insensitivity
- inlet: normal routing, empty body, no user message, mixed role messages
- inlet: debug-mode system-message injection (prepend vs insert)
- inlet: custom Valve overrides
"""

import sys
import os
import pytest

# Allow importing the pipeline module without Open-WebUI installed
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "pipelines"))
from smart_model_router import Pipeline  # noqa: E402


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture()
def pipeline():
    return Pipeline()


# ─── Initialisation ──────────────────────────────────────────────────────────

class TestPipelineInit:
    def test_name(self, pipeline):
        assert pipeline.name == "Smart Model Router"

    def test_type(self, pipeline):
        assert pipeline.type == "filter"

    def test_id(self, pipeline):
        assert pipeline.id == "smart_model_router"

    def test_default_diagnostics_model(self, pipeline):
        assert pipeline.valves.diagnostics_model == "qwen2.5:14b"

    def test_default_scripting_model(self, pipeline):
        assert pipeline.valves.scripting_model == "qwen2.5-coder:14b"

    def test_default_reasoning_model(self, pipeline):
        assert pipeline.valves.reasoning_model == "deepseek-r1:14b"

    def test_default_longform_model(self, pipeline):
        assert pipeline.valves.longform_model == "gemma3:12b"

    def test_debug_off_by_default(self, pipeline):
        assert pipeline.valves.debug is False


# ─── _classify ───────────────────────────────────────────────────────────────

class TestClassifyDiagnostics:
    """Tests for queries that should route to the diagnostics model.

    The diagnostics patterns are evaluated first, so any query containing
    a diagnostic keyword will route here regardless of other keywords.
    """

    @pytest.mark.parametrize("text", [
        # health / status / check
        "check health of the system",
        "what is the status of the server",
        "monitor resource usage",
        "send an alert when a threshold is reached",
        "is the host reachable?",
        "is nginx up?",
        "is redis down?",
        "ping the gateway",
        "show uptime stats",
        # service / container keywords
        "ollama is not responding",
        "open webui is crashing",
        "the pipeline crashed",
        "docker container won't start",
        # hardware keywords
        "check gpu load",
        "how much cpu is in use?",
        "show memory usage",
        "disk usage report",
        # API shortcuts
        "get_all instances",
        "list models on this host",
        "loaded models in vram",
        "show loaded models",
        # misc
        "what is the uptime?",
        "is the container unreachable?",
    ])
    def test_routes_to_diagnostics(self, pipeline, text):
        model, reason = pipeline._classify(text)
        assert model == pipeline.valves.diagnostics_model
        assert reason == "diagnostics"


class TestClassifyScripting:
    """Tests for queries that route to the scripting / code model.

    Note: The diagnostics patterns run first.  Inputs that also contain
    diagnostic keywords (e.g. 'docker', 'container') will match diagnostics
    instead — those cases are covered in TestRoutingPriority below.
    """

    @pytest.mark.parametrize("text", [
        "write a bash script to restart nginx",
        "shell command to list open ports",
        "create a cron job for backups",
        "configure a systemd unit file",
        "write a Dockerfile",
        "ansible playbook for deployment",
        "terraform config for AWS",
        "how do I fix this error in Python?",
        "debug this traceback",
        "exception thrown in the handler",
        "process failed with exit code 1",
        "setup the database",
        "configure nginx reverse proxy",
        "deploy the application",
        "update the package list",
        "upgrade the kernel",
        "write a Python function",
        "JavaScript class for authentication",
        "TypeScript interface example",
        "import the module",
        "code review for this snippet",
        "show me the yaml syntax",
    ])
    def test_routes_to_scripting(self, pipeline, text):
        model, reason = pipeline._classify(text)
        assert model == pipeline.valves.scripting_model
        assert reason == "scripting"


class TestClassifyReasoning:
    """Tests for queries that route to the reasoning model.

    Inputs must not contain diagnostic keywords (which run earlier)
    or scripting keywords.
    """

    @pytest.mark.parametrize("text", [
        "root cause of the crash",
        "explain how kubernetes works",
        "compare Redis and Memcached",
        "optimize this query for performance",
        "recommend a caching strategy",
        "should I use nginx or apache?",
        "what would you suggest for scaling?",
        "best approach for zero-downtime deploys",
        "pros and cons of microservices",
        "high latency in network requests",
        "architecture of a distributed system",
        "design the API layer",
        "strategy for data migration",
        "best practice for secrets management",
        "tradeoff between SQL and NoSQL",
    ])
    def test_routes_to_reasoning(self, pipeline, text):
        model, reason = pipeline._classify(text)
        assert model == pipeline.valves.reasoning_model
        assert reason == "reasoning"


class TestClassifyLongform:
    """Tests for queries that route to the longform model.

    Inputs must not contain diagnostic, scripting, or reasoning keywords,
    as those categories are evaluated first.
    """

    @pytest.mark.parametrize("text", [
        "show me the log",
        "summarize the output",
        "give me a summary",
        "document this module",
        "what does this mean?",
        "step by step guide",
        "write a blog post",
        "draft a proposal",
        "create a document for the team",
        "generate a report for management",
    ])
    def test_routes_to_longform(self, pipeline, text):
        model, reason = pipeline._classify(text)
        assert model == pipeline.valves.longform_model
        assert reason == "longform"


class TestRoutingPriority:
    """
    Documents cases where an earlier pattern category wins over a later one
    when both keywords appear in the same query.  These are intentional
    observations of the priority ordering: diagnostics > scripting >
    reasoning > longform.
    """

    @pytest.mark.parametrize("text,expected_reason", [
        # 'docker' (diagnostics) wins over 'install' / 'yaml' (scripting)
        ("show me the yaml for docker compose", "diagnostics"),
        ("install docker on Ubuntu",            "diagnostics"),
        # 'cpu' / 'memory' (diagnostics) wins over reasoning keywords
        ("why is the CPU spiking?",             "diagnostics"),
        ("possible memory leak",                "diagnostics"),
        ("high cpu on the worker",              "diagnostics"),
        # 'service' (scripting) wins over 'slow' (reasoning)
        ("the service is slow",                 "scripting"),
        # 'analyze' (reasoning) wins over 'logs' (longform)
        ("analyze these logs",                  "reasoning"),
        # 'setup' (scripting) wins over 'walk me through' (longform)
        ("walk me through the setup",           "scripting"),
        # 'error' (scripting) wins over 'explain this' (longform)
        ("explain this error message",          "scripting"),
        # 'diagnos' stem requires exact word boundary — 'diagnose' is default
        ("diagnose the problem",                "default"),
    ])
    def test_priority_winner(self, pipeline, text, expected_reason):
        _, reason = pipeline._classify(text)
        assert reason == expected_reason


class TestClassifyDefault:
    @pytest.mark.parametrize("text", [
        "hello",
        "what time is it?",
        "thanks",
        "ok",
        "tell me something interesting",
        "random question with no keywords",
    ])
    def test_routes_to_default(self, pipeline, text):
        model, reason = pipeline._classify(text)
        assert model == pipeline.valves.diagnostics_model
        assert reason == "default"

    def test_empty_string(self, pipeline):
        model, reason = pipeline._classify("")
        assert model == pipeline.valves.diagnostics_model
        assert reason == "default"


class TestClassifyCaseInsensitivity:
    def test_uppercase_diagnostic(self, pipeline):
        model, reason = pipeline._classify("CHECK HEALTH")
        assert reason == "diagnostics"

    def test_mixed_case_scripting(self, pipeline):
        model, reason = pipeline._classify("Write A BASH Script")
        assert reason == "scripting"

    def test_uppercase_reasoning(self, pipeline):
        model, reason = pipeline._classify("WHY IS IT SLOW?")
        assert reason == "reasoning"

    def test_uppercase_longform(self, pipeline):
        model, reason = pipeline._classify("SUMMARIZE THE LOGS")
        assert reason == "longform"


class TestClassifyCustomValves:
    def test_custom_diagnostics_model_returned(self):
        p = Pipeline()
        p.valves.diagnostics_model = "custom-diag:7b"
        model, _ = p._classify("check health")
        assert model == "custom-diag:7b"

    def test_custom_scripting_model_returned(self):
        p = Pipeline()
        p.valves.scripting_model = "custom-code:7b"
        model, _ = p._classify("write a bash script")
        assert model == "custom-code:7b"

    def test_custom_reasoning_model_returned(self):
        p = Pipeline()
        p.valves.reasoning_model = "custom-reason:7b"
        model, _ = p._classify("why is it slow?")
        assert model == "custom-reason:7b"

    def test_custom_longform_model_returned(self):
        p = Pipeline()
        p.valves.longform_model = "custom-long:7b"
        model, _ = p._classify("summarize the logs")
        assert model == "custom-long:7b"


# ─── inlet ───────────────────────────────────────────────────────────────────

class TestInlet:
    @pytest.mark.asyncio
    async def test_empty_messages_returns_body_unchanged(self, pipeline):
        body = {"messages": []}
        result = await pipeline.inlet(body)
        assert result == {"messages": []}

    @pytest.mark.asyncio
    async def test_missing_messages_key_returns_body_unchanged(self, pipeline):
        body = {"model": "some-model"}
        result = await pipeline.inlet(body)
        assert result == {"model": "some-model"}

    @pytest.mark.asyncio
    async def test_no_user_message_returns_body_unchanged(self, pipeline):
        body = {"messages": [{"role": "system", "content": "You are helpful."}]}
        result = await pipeline.inlet(body)
        # model should not be overridden because there is no user message
        assert "model" not in result

    @pytest.mark.asyncio
    async def test_routes_user_message_to_correct_model(self, pipeline):
        body = {
            "messages": [
                {"role": "user", "content": "check health of ollama"}
            ]
        }
        result = await pipeline.inlet(body)
        assert result["model"] == pipeline.valves.diagnostics_model

    @pytest.mark.asyncio
    async def test_uses_last_user_message(self, pipeline):
        body = {
            "messages": [
                {"role": "user", "content": "summarize these logs"},
                {"role": "assistant", "content": "Here is the summary."},
                {"role": "user", "content": "write a bash script"},
            ]
        }
        result = await pipeline.inlet(body)
        # Last user message matches scripting
        assert result["model"] == pipeline.valves.scripting_model

    @pytest.mark.asyncio
    async def test_skips_assistant_messages(self, pipeline):
        body = {
            "messages": [
                {"role": "assistant", "content": "diagnose the issue"},
                {"role": "user", "content": "why is it slow?"},
            ]
        }
        result = await pipeline.inlet(body)
        assert result["model"] == pipeline.valves.reasoning_model

    @pytest.mark.asyncio
    async def test_passes_user_kwarg(self, pipeline):
        """inlet should work when user dict is supplied."""
        body = {"messages": [{"role": "user", "content": "check gpu"}]}
        result = await pipeline.inlet(body, user={"id": "abc", "name": "Alice"})
        assert result["model"] == pipeline.valves.diagnostics_model

    @pytest.mark.asyncio
    async def test_empty_user_content_returns_body_unchanged(self, pipeline):
        body = {"messages": [{"role": "user", "content": ""}]}
        result = await pipeline.inlet(body)
        assert "model" not in result


class TestInletDebugMode:
    @pytest.mark.asyncio
    async def test_debug_prepends_to_existing_system_message(self):
        p = Pipeline()
        p.valves.debug = True
        body = {
            "messages": [
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "check health"},
            ]
        }
        result = await p.inlet(body)
        system_content = result["messages"][0]["content"]
        assert system_content.startswith("[Router →")
        assert "You are helpful." in system_content

    @pytest.mark.asyncio
    async def test_debug_inserts_system_message_when_none_exists(self):
        p = Pipeline()
        p.valves.debug = True
        body = {
            "messages": [
                {"role": "user", "content": "check health"},
            ]
        }
        result = await p.inlet(body)
        assert result["messages"][0]["role"] == "system"
        assert result["messages"][0]["content"].startswith("[Router →")

    @pytest.mark.asyncio
    async def test_debug_includes_reason_in_system_message(self):
        p = Pipeline()
        p.valves.debug = True
        body = {"messages": [{"role": "user", "content": "write a script"}]}
        result = await p.inlet(body)
        # The system message should contain the routing reason
        system_content = result["messages"][0]["content"]
        assert "scripting" in system_content

    @pytest.mark.asyncio
    async def test_no_debug_does_not_insert_system_message(self, pipeline):
        body = {"messages": [{"role": "user", "content": "check health"}]}
        result = await pipeline.inlet(body)
        # No system message should be injected in non-debug mode
        roles = [m["role"] for m in result["messages"]]
        assert "system" not in roles


# ─── Lifecycle hooks ─────────────────────────────────────────────────────────

class TestLifecycle:
    @pytest.mark.asyncio
    async def test_on_startup(self, pipeline, capsys):
        await pipeline.on_startup()
        captured = capsys.readouterr()
        assert "Pipeline started" in captured.out

    @pytest.mark.asyncio
    async def test_on_shutdown(self, pipeline, capsys):
        await pipeline.on_shutdown()
        captured = capsys.readouterr()
        assert "Pipeline stopped" in captured.out
