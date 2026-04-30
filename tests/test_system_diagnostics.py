"""
Tests for tools/system_diagnostics.py

Covers:
- Tools initialisation
- _instance_url: valid / invalid instance
- check_health: reachable, unreachable (network error), non-200 status
- check_all_instances: all reachable, mixed, all unreachable
- list_all_models: success, network error
- list_loaded_models: success, network error
- show_model_info: success, network error, unknown instance
- free_model: success (200), failure (non-200), network error, unknown instance
- get_all: reachable with loaded models, unreachable, ps error
"""

import sys
import os
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
from system_diagnostics import Tools, OLLAMA_INSTANCES  # noqa: E402


# ─── Helpers ─────────────────────────────────────────────────────────────────

def make_response(status_code: int, body: dict) -> MagicMock:
    """Create a mock httpx.Response."""
    r = MagicMock(spec=httpx.Response)
    r.status_code = status_code
    r.json.return_value = body
    return r


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture()
def tools():
    t = Tools()
    t.instances = {
        "local": "http://localhost:11434",
        "remote1": "http://10.0.0.1:11434",
    }
    return t


# ─── Initialisation ──────────────────────────────────────────────────────────

class TestInit:
    def test_instances_loaded_from_module_constant(self):
        t = Tools()
        assert t.instances == OLLAMA_INSTANCES

    def test_local_instance_always_present(self):
        t = Tools()
        assert "local" in t.instances


# ─── _instance_url ────────────────────────────────────────────────────────────

class TestInstanceUrl:
    def test_known_instance_returns_url(self, tools):
        url, err = tools._instance_url("local")
        assert url == "http://localhost:11434"
        assert err is None

    def test_second_known_instance(self, tools):
        url, err = tools._instance_url("remote1")
        assert url == "http://10.0.0.1:11434"
        assert err is None

    def test_unknown_instance_returns_error(self, tools):
        url, err = tools._instance_url("nonexistent")
        assert url is None
        assert "nonexistent" in err
        assert "local" in err   # lists available instances

    def test_empty_string_instance(self, tools):
        url, err = tools._instance_url("")
        assert url is None
        assert err is not None


# ─── check_health ────────────────────────────────────────────────────────────

class TestCheckHealth:
    @pytest.mark.asyncio
    async def test_reachable_instance(self, tools):
        resp = make_response(200, {"models": [{"name": "m1"}, {"name": "m2"}]})
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = json.loads(await tools.check_health("local"))

        assert result["status"] == "reachable"
        assert result["instance"] == "local"
        assert result["model_count"] == 2
        assert result["http_code"] == 200
        assert result["url"] == "http://localhost:11434"

    @pytest.mark.asyncio
    async def test_unreachable_instance(self, tools):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = json.loads(await tools.check_health("local"))

        assert result["status"] == "unreachable"
        assert "error" in result

    @pytest.mark.asyncio
    async def test_unknown_instance_returns_error_json(self, tools):
        result = json.loads(await tools.check_health("ghost"))
        assert "error" in result
        assert "ghost" in result["error"]

    @pytest.mark.asyncio
    async def test_empty_models_list(self, tools):
        resp = make_response(200, {"models": []})
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = json.loads(await tools.check_health("local"))

        assert result["model_count"] == 0
        assert result["status"] == "reachable"

    @pytest.mark.asyncio
    async def test_default_instance_is_local(self, tools):
        """check_health() with no argument should check 'local'."""
        resp = make_response(200, {"models": []})
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = json.loads(await tools.check_health())

        assert result["instance"] == "local"


# ─── check_all_instances ─────────────────────────────────────────────────────

class TestCheckAllInstances:
    @pytest.mark.asyncio
    async def test_all_reachable(self, tools):
        resp = make_response(200, {"models": [{"name": "x"}]})
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = json.loads(await tools.check_all_instances())

        assert result["local"]["status"] == "reachable"
        assert result["remote1"]["status"] == "reachable"

    @pytest.mark.asyncio
    async def test_all_unreachable(self, tools):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = json.loads(await tools.check_all_instances())

        for name in tools.instances:
            assert result[name]["status"] == "unreachable"
            assert "error" in result[name]

    @pytest.mark.asyncio
    async def test_mixed_reachability(self, tools):
        reachable = make_response(200, {"models": []})

        call_count = {"n": 0}

        async def selective_get(url, **kwargs):
            call_count["n"] += 1
            if "localhost" in url:
                return reachable
            raise httpx.ConnectError("refused")

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = selective_get

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = json.loads(await tools.check_all_instances())

        assert result["local"]["status"] == "reachable"
        assert result["remote1"]["status"] == "unreachable"

    @pytest.mark.asyncio
    async def test_returns_url_for_all_instances(self, tools):
        resp = make_response(200, {"models": []})
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = json.loads(await tools.check_all_instances())

        for name, url in tools.instances.items():
            assert result[name]["url"] == url


# ─── list_all_models ──────────────────────────────────────────────────────────

class TestListAllModels:
    @pytest.mark.asyncio
    async def test_success(self, tools):
        models_body = {"models": [{"name": "llama3"}, {"name": "phi3"}]}
        resp = make_response(200, models_body)
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = json.loads(await tools.list_all_models("local"))

        assert result["instance"] == "local"
        assert result["data"] == models_body

    @pytest.mark.asyncio
    async def test_network_error(self, tools):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = json.loads(await tools.list_all_models("local"))

        assert "error" in result

    @pytest.mark.asyncio
    async def test_unknown_instance(self, tools):
        result = json.loads(await tools.list_all_models("ghost"))
        assert "error" in result


# ─── list_loaded_models ───────────────────────────────────────────────────────

class TestListLoadedModels:
    @pytest.mark.asyncio
    async def test_success(self, tools):
        ps_body = {"models": [{"name": "llama3", "size": 8000000000}]}
        resp = make_response(200, ps_body)
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = json.loads(await tools.list_loaded_models("local"))

        assert result["instance"] == "local"
        assert result["data"] == ps_body

    @pytest.mark.asyncio
    async def test_network_error(self, tools):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = json.loads(await tools.list_loaded_models("local"))

        assert "error" in result

    @pytest.mark.asyncio
    async def test_unknown_instance(self, tools):
        result = json.loads(await tools.list_loaded_models("nowhere"))
        assert "error" in result


# ─── show_model_info ──────────────────────────────────────────────────────────

class TestShowModelInfo:
    @pytest.mark.asyncio
    async def test_success(self, tools):
        info_body = {"modelfile": "FROM llama3", "parameters": {}}
        resp = make_response(200, info_body)
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = json.loads(await tools.show_model_info("local", "llama3"))

        assert result["instance"] == "local"
        assert result["model"] == "llama3"
        assert result["data"] == info_body

    @pytest.mark.asyncio
    async def test_network_error(self, tools):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = json.loads(await tools.show_model_info("local", "llama3"))

        assert "error" in result
        assert result["model"] == "llama3"

    @pytest.mark.asyncio
    async def test_unknown_instance(self, tools):
        result = json.loads(await tools.show_model_info("ghost", "llama3"))
        assert "error" in result


# ─── free_model ───────────────────────────────────────────────────────────────

class TestFreeModel:
    @pytest.mark.asyncio
    async def test_success_200(self, tools):
        resp = make_response(200, {})
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = json.loads(await tools.free_model("local", "llama3"))

        assert result["status"] == "unloaded"
        assert result["http_code"] == 200

    @pytest.mark.asyncio
    async def test_failure_non_200(self, tools):
        resp = make_response(500, {})
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = json.loads(await tools.free_model("local", "llama3"))

        assert result["status"] == "failed"
        assert result["http_code"] == 500

    @pytest.mark.asyncio
    async def test_network_error(self, tools):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = json.loads(await tools.free_model("local", "llama3"))

        assert "error" in result

    @pytest.mark.asyncio
    async def test_unknown_instance(self, tools):
        result = json.loads(await tools.free_model("ghost", "llama3"))
        assert "error" in result

    @pytest.mark.asyncio
    async def test_sends_keep_alive_zero(self, tools):
        """free_model must send keep_alive=0 to unload the model."""
        resp = make_response(200, {})
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            await tools.free_model("local", "llama3")

        call_kwargs = mock_client.post.call_args
        assert call_kwargs[1]["json"]["keep_alive"] == 0
        assert call_kwargs[1]["json"]["model"] == "llama3"


# ─── get_all ─────────────────────────────────────────────────────────────────

class TestGetAll:
    @pytest.mark.asyncio
    async def test_all_reachable_with_loaded_models(self, tools):
        tags_body = {"models": [{"name": "m1"}, {"name": "m2"}]}
        ps_body = {"models": [{"name": "m1"}]}

        async def get_side_effect(url, **kwargs):
            if "/api/tags" in url:
                return make_response(200, tags_body)
            if "/api/ps" in url:
                return make_response(200, ps_body)

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = get_side_effect

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = json.loads(await tools.get_all())

        for name in tools.instances:
            assert result[name]["status"] == "reachable"
            assert result[name]["model_count"] == 2
            assert result[name]["loaded_models"] == [{"name": "m1"}]

    @pytest.mark.asyncio
    async def test_unreachable_skips_ps_call(self, tools):
        """When /api/tags fails, the entry is marked unreachable and /api/ps is not called."""
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = json.loads(await tools.get_all())

        for name in tools.instances:
            assert result[name]["status"] == "unreachable"
            assert "error" in result[name]
            assert "loaded_models" not in result[name]

    @pytest.mark.asyncio
    async def test_ps_error_recorded(self, tools):
        """When /api/tags succeeds but /api/ps fails, loaded_models_error is recorded."""
        tags_body = {"models": []}

        async def get_side_effect(url, **kwargs):
            if "/api/tags" in url:
                return make_response(200, tags_body)
            raise httpx.ConnectError("ps refused")

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = get_side_effect

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = json.loads(await tools.get_all())

        for name in tools.instances:
            assert result[name]["status"] == "reachable"
            assert "loaded_models_error" in result[name]
            assert "loaded_models" not in result[name]

    @pytest.mark.asyncio
    async def test_includes_url_for_all_instances(self, tools):
        tags_body = {"models": []}
        ps_body = {"models": []}

        async def get_side_effect(url, **kwargs):
            if "/api/tags" in url:
                return make_response(200, tags_body)
            return make_response(200, ps_body)

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = get_side_effect

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = json.loads(await tools.get_all())

        for name, url in tools.instances.items():
            assert result[name]["url"] == url
