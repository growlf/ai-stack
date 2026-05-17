"""NVIDIA verification probe — cross-checks Ollama size_vram against host nvidia-smi.

Detects the failure mode where Ollama claims size_vram > 0 for resident models
but nvidia-smi shows the GPU memory unused. That's the silent-CPU-fallback class
that hit cluster-llm on 2026-05-16 (NVML handle drift in the inference container
after 42h of uptime; Ollama metadata stale).

The cross-source signals consulted:
  - host_nvidia_smi: parses `nvidia-smi --query-gpu=memory.used,memory.total`
    for whole-GPU VRAM usage
  - ollama_api_ps: sums claimed size_vram across all currently-loaded models
  - divergence: |host_smi_used - ollama_claimed| > tolerance → alive=False

Future: container-side nvidia-smi exec (currently deferred — needs docker socket
access from shepherd-node, which adds privilege scope. Layer 3.5 work.).
"""

import asyncio
import shutil
from typing import Optional

from . import VerificationProbe, VerificationResult


# Tolerance for size_vram vs host-smi mismatch. Ollama rounds + reports
# bookkeeping VRAM, not exact device-side allocation. ~500MB tolerance
# avoids false alarms on rounding while still catching the GB-scale
# fallback case (today's regression was 8GB claimed vs 0 MB actual).
DIVERGENCE_TOLERANCE_MB = 500


class NvidiaVerificationProbe(VerificationProbe):
    def name(self) -> str:
        return "nvidia"

    def is_applicable(self) -> bool:
        return shutil.which("nvidia-smi") is not None

    async def verify(self, ollama_ps_state: Optional[dict] = None) -> VerificationResult:
        sources = []
        reasons = []
        extra: dict = {}

        # Source 1: host nvidia-smi VRAM-in-use
        sources.append("host_nvidia_smi")
        try:
            proc = await asyncio.create_subprocess_exec(
                "nvidia-smi",
                "--query-gpu=memory.used,memory.total",
                "--format=csv,noheader,nounits",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)
            if proc.returncode != 0:
                reasons.append(f"host nvidia-smi exit {proc.returncode}: {stderr.decode().strip()[:200]}")
                host_used_mb = None
            else:
                line = stdout.decode().strip().splitlines()[0]
                host_used_mb = int(line.split(",")[0].strip())
                extra["host_smi_used_mb"] = host_used_mb
        except (asyncio.TimeoutError, FileNotFoundError, ValueError, IndexError) as e:
            reasons.append(f"host nvidia-smi parse error: {type(e).__name__}: {e}")
            host_used_mb = None

        # Source 2: Ollama /api/ps claimed size_vram
        sources.append("ollama_api_ps")
        claimed_total_mb = 0
        models_with_vram_claim = 0
        if ollama_ps_state and "models" in ollama_ps_state:
            for m in ollama_ps_state.get("models", []):
                claimed = (m.get("size_vram", 0) or 0) // (1024 * 1024)
                if claimed > 0:
                    claimed_total_mb += claimed
                    models_with_vram_claim += 1
            extra["ollama_claimed_total_mb"] = claimed_total_mb
            extra["ollama_models_claiming_vram"] = models_with_vram_claim
        else:
            extra["ollama_api_ps"] = "no state provided"

        # Cross-source comparison
        if host_used_mb is not None and claimed_total_mb > 0:
            divergence_mb = claimed_total_mb - host_used_mb
            extra["divergence_mb"] = divergence_mb
            if divergence_mb > DIVERGENCE_TOLERANCE_MB:
                reasons.append(
                    f"Ollama claims {claimed_total_mb} MB resident across {models_with_vram_claim} "
                    f"model(s), but host nvidia-smi shows only {host_used_mb} MB in use "
                    f"(divergence {divergence_mb} MB > {DIVERGENCE_TOLERANCE_MB} MB tolerance). "
                    f"Likely silent CPU fallback — container's NVML handle drifted or GPU passthrough lost."
                )

        return VerificationResult(
            accelerator_type="nvidia",
            alive=(len(reasons) == 0),
            sources_checked=sources,
            divergence_reasons=reasons,
            implementation_status="implemented",
            extra=extra,
        )
