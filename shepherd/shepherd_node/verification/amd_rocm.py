"""AMD ROCm verification probe — stub.

When implemented: cross-check via `rocm-smi --showmemuse` / `rocm-smi --showpids`
against Ollama size_vram, same shape as the NVIDIA probe with ROCm-specific
tooling.
"""

import shutil

from . import VerificationProbe, VerificationResult


class AmdRocmVerificationProbe(VerificationProbe):
    def name(self) -> str:
        return "amd"

    def is_applicable(self) -> bool:
        return shutil.which("rocm-smi") is not None

    async def verify(self, ollama_ps_state: dict | None = None) -> VerificationResult:
        return VerificationResult(
            accelerator_type="amd",
            alive=True,
            sources_checked=[],
            divergence_reasons=[],
            implementation_status="stub",
            extra={"note": "AMD ROCm verification pending — implement when first AMD herd peer comes online"},
        )
