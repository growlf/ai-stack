"""Intel Iris (integrated graphics) verification probe — stub.

When implemented: same shape as Intel Arc probe (Iris uses the same level-zero
toolchain), with usage-tier baselines appropriate for integrated graphics.
"""

import shutil
from typing import Optional

from . import VerificationProbe, VerificationResult


class IntelIrisVerificationProbe(VerificationProbe):
    def name(self) -> str:
        return "intel-iris"

    def is_applicable(self) -> bool:
        return shutil.which("intel_gpu_top") is not None

    async def verify(self, ollama_ps_state: Optional[dict] = None) -> VerificationResult:
        return VerificationResult(
            accelerator_type="intel-iris",
            alive=True,
            sources_checked=[],
            divergence_reasons=[],
            implementation_status="stub",
            extra={"note": "Intel Iris verification pending — same fix shape as Intel Arc"},
        )
