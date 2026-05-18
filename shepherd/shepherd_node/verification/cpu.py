"""CPU floor verification — no GPU expected, so trivially 'alive' from a
GPU-acceleration perspective. Used on nodes without any accelerator hardware
recognized, or as the fallback if no other probe applies.

The alive=True here is *not* a claim that inference is fast; it's a claim that
the system is in its expected state (CPU-only, no GPU to lose). A CPU-only
node serving on CPU is normal; a GPU-tagged node falling back to CPU is the
regression class we're catching elsewhere.
"""

from . import VerificationProbe, VerificationResult


class CpuVerificationProbe(VerificationProbe):
    def name(self) -> str:
        return "cpu"

    def is_applicable(self) -> bool:
        return True  # Always applicable as floor

    async def verify(self, ollama_ps_state: dict | None = None) -> VerificationResult:
        return VerificationResult(
            accelerator_type="cpu",
            alive=True,
            sources_checked=[],
            divergence_reasons=[],
            implementation_status="not-applicable",
            extra={"note": "No GPU expected on this host; CPU-only inference is the expected state"},
        )
