"""Intel Arc verification probe — stub. Layer 3 abstraction lands; vendor-specific
implementation comes when Phoenix Arc kernel/ipex-llm regression is resolved.

When implemented, will cross-check:
  - host `intel_gpu_top -J -s 100` snapshot for Arc engine usage
  - sycl-ls device detection (or level-zero ze_init success) inside container
  - Ollama /api/ps size_vram claims vs Arc memory in use

Stub returns alive=True with status=stub so the dashboard knows the hardware
was recognized but actively-verified state isn't yet asserted.
"""

import shutil
from typing import Optional

from . import VerificationProbe, VerificationResult


class IntelArcVerificationProbe(VerificationProbe):
    def name(self) -> str:
        return "intel-arc"

    def is_applicable(self) -> bool:
        # Loose check: intel_gpu_top installed AND there's a discrete Arc GPU.
        # The metrics probe in ../probes/intel_arc.py owns the precise detection;
        # we mirror its hardware sense here.
        return shutil.which("intel_gpu_top") is not None

    async def verify(self, ollama_ps_state: Optional[dict] = None) -> VerificationResult:
        return VerificationResult(
            accelerator_type="intel-arc",
            alive=True,
            sources_checked=[],
            divergence_reasons=[],
            implementation_status="stub",
            extra={"note": "Intel Arc verification pending — see Phoenix Arc backlog at ~/enclave-core/docs/phoenix-arc-investigation-backlog.md"},
        )
