"""
title: System Diagnostics
description: Query multiple Ollama instances for models, GPU status, health, and control.
             Instances are configured via OLLAMA_INSTANCES below.
             Add remote machines by adding OLLAMA_REMOTE_<name>=http://<ip>:11434
             to your .env and re-running post-install.sh, or edit OLLAMA_INSTANCES directly.
version: 1.1.0
"""

import httpx
import json

# ─── Configure your Ollama instances here ────────────────────────────────────
# The local instance is always present.
# Add remote machines to match your OLLAMA_REMOTE_* entries in .env.
# post-install.sh will generate this block automatically if you use that workflow.
OLLAMA_INSTANCES = {
    "local": "http://ollama-arc:11434",     # Local Intel Arc iGPU (always present)
    # "remote1": "http://10.0.0.X:11434",  # Example: remote machine on your LAN
    # "remote2": "http://10.0.0.Y:11434",  # Add as many as you need
}


class Tools:
    def __init__(self):
        self.instances = OLLAMA_INSTANCES

    def _instance_url(self, instance: str) -> tuple[str, str | None]:
        """Return (url, error) for a named instance."""
        url = self.instances.get(instance)
        if not url:
            available = ", ".join(self.instances.keys())
            return None, f"Unknown instance '{instance}'. Available: {available}"
        return url, None

    async def check_health(self, instance: str = "local") -> str:
        """Ping an Ollama instance to confirm it is reachable. instance: local | remote1 | remote2 ..."""
        url, err = self._instance_url(instance)
        if err:
            return json.dumps({"error": err})
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(f"{url}/api/tags")
                return json.dumps({
                    "instance": instance,
                    "url": url,
                    "status": "reachable",
                    "http_code": r.status_code,
                    "model_count": len(r.json().get("models", [])),
                }, indent=2)
        except Exception as e:
            return json.dumps({
                "instance": instance,
                "url": url,
                "status": "unreachable",
                "error": str(e),
            })

    async def check_all_instances(self) -> str:
        """Ping every configured Ollama instance and return a health summary."""
        results = {}
        async with httpx.AsyncClient(timeout=5) as client:
            for name, url in self.instances.items():
                try:
                    r = await client.get(f"{url}/api/tags")
                    results[name] = {
                        "status": "reachable",
                        "url": url,
                        "http_code": r.status_code,
                        "model_count": len(r.json().get("models", [])),
                    }
                except Exception as e:
                    results[name] = {
                        "status": "unreachable",
                        "url": url,
                        "error": str(e),
                    }
        return json.dumps(results, indent=2)

    async def list_all_models(self, instance: str = "local") -> str:
        """List all available models with sizes and quantization. instance: local | remote1"""
        url, err = self._instance_url(instance)
        if err:
            return json.dumps({"error": err})
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(f"{url}/api/tags")
                return json.dumps({
                    "instance": instance,
                    "url": url,
                    "data": r.json()
                }, indent=2)
        except Exception as e:
            return json.dumps({"instance": instance, "url": url, "error": str(e)})

    async def list_loaded_models(self, instance: str = "local") -> str:
        """List currently loaded models and VRAM/RAM usage. instance: local | remote1 ..."""
        url, err = self._instance_url(instance)
        if err:
            return json.dumps({"error": err})
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(f"{url}/api/ps")
                return json.dumps({
                    "instance": instance,
                    "url": url,
                    "data": r.json()
                }, indent=2)
        except Exception as e:
            return json.dumps({"instance": instance, "url": url, "error": str(e)})

    async def show_model_info(self, instance: str, model_name: str) -> str:
        """Get modelfile, parameters, and template for a specific model. instance: local | remote1 ..."""
        url, err = self._instance_url(instance)
        if err:
            return json.dumps({"error": err})
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(f"{url}/api/show", json={"name": model_name})
                return json.dumps({
                    "instance": instance,
                    "model": model_name,
                    "data": r.json()
                }, indent=2)
        except Exception as e:
            return json.dumps({"instance": instance, "model": model_name, "error": str(e)})

    async def free_model(self, instance: str, model_name: str) -> str:
        """Unload a model from VRAM/RAM on a given instance. instance: local | remote1 ..."""
        url, err = self._instance_url(instance)
        if err:
            return json.dumps({"error": err})
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(
                    f"{url}/api/generate",
                    json={"model": model_name, "keep_alive": 0}
                )
                return json.dumps({
                    "instance": instance,
                    "model": model_name,
                    "status": "unloaded" if r.status_code == 200 else "failed",
                    "http_code": r.status_code,
                }, indent=2)
        except Exception as e:
            return json.dumps({"instance": instance, "model": model_name, "error": str(e)})

    async def get_all(self) -> str:
        """Full diagnostic report: health, loaded models, and model lists for all instances."""
        results = {}
        async with httpx.AsyncClient(timeout=10) as client:
            for name, url in self.instances.items():
                entry = {"url": url}
                try:
                    health = await client.get(f"{url}/api/tags")
                    tags = health.json()
                    entry["status"] = "reachable"
                    entry["http_code"] = health.status_code
                    entry["model_count"] = len(tags.get("models", []))
                    entry["models"] = tags.get("models", [])
                except Exception as e:
                    entry["status"] = "unreachable"
                    entry["error"] = str(e)
                    results[name] = entry
                    continue
                try:
                    ps = await client.get(f"{url}/api/ps")
                    entry["loaded_models"] = ps.json().get("models", [])
                except Exception as e:
                    entry["loaded_models_error"] = str(e)
                results[name] = entry
        return json.dumps(results, indent=2)
