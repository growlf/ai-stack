"""Apple Silicon (M-series) verification probe — stub.

When implemented: cross-check via `powermetrics --samplers gpu_power` (or
similar) and Metal-via-MLX residency checks. Apple Silicon shares unified
memory between CPU and GPU, so the threat model differs from discrete GPUs —
"size_vram > 0 but unused" doesn't cleanly apply. Instead we'd check actual
Metal engine activity during inference.
"""

import shutil

from . import VerificationProbe, VerificationResult


class AppleSiliconVerificationProbe(VerificationProbe):
    def name(self) -> str:
        return "apple-silicon"

    def is_applicable(self) -> bool:
        return shutil.which("powermetrics") is not None

    async def verify(self, ollama_ps_state: dict | None = None) -> VerificationResult:
        return VerificationResult(
            accelerator_type="apple-silicon",
            alive=True,
            sources_checked=[],
            divergence_reasons=[],
            implementation_status="stub",
            extra={
                "note": "Apple Silicon verification pending — unified-memory model differs from discrete GPUs"
            },
        )
