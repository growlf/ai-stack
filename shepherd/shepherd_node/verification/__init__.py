"""VerificationProbe interface — Layer 3 of the GPU-integrity plan.

Parallel to the metrics `Probe` interface in ../probes/__init__.py. Where `Probe`
answers "what are the current readings?", `VerificationProbe` answers "is the
GPU still functionally accessible from where inference actually runs?"

The threat model is the 2026-05-16 cluster-llm regression: Ollama reported
size_vram=8GB; nvidia-smi inside the container errored with NVML init failure;
host nvidia-smi showed 0 MiB in use. All three sources said something different,
and the disagreement *was* the diagnostic signal.

Three-condition green criteria (Forge's framing):
  (a) container/inference-side accelerator init succeeds (NVML / level_zero / HIP)
  (b) Ollama /api/ps reports size_vram > 0 for currently-loaded models
  (c) recent inference latency stays within baseline tokens-per-second tier

Each vendor implements the same VerificationResult shape; shepherd-control
compares results vendor-agnostically. The cross-source comparison logic
deliberately does NOT know about nvidia-smi vs intel_gpu_top vs rocm-smi —
that's the adapter's job, behind a uniform interface.

CPU floor returns "no GPU expected, all green" (vacuously). Stubs return
"hardware recognized, probe not yet implemented" — surfaces existence
without claiming readings we can't make.
"""

from abc import ABC, abstractmethod
from typing import Optional, Literal

from pydantic import BaseModel


class VerificationResult(BaseModel):
    accelerator_type: str
    """Mirrors HardwareMetrics.accelerator_type. e.g. 'nvidia', 'intel-arc', 'cpu'."""

    alive: bool
    """The headline answer. True only if all configured checks pass. False on any divergence."""

    sources_checked: list[str]
    """Names of the cross-source signals consulted, e.g. ['host_nvidia_smi', 'container_nvidia_smi', 'ollama_api_ps']."""

    divergence_reasons: list[str]
    """Human-readable reasons for alive=False. Empty when alive=True. Multiple reasons can fire simultaneously."""

    implementation_status: Literal["implemented", "stub", "not-applicable"] = "implemented"
    """'implemented' = real verification. 'stub' = hardware recognized but probe pending.
    'not-applicable' = CPU floor (no GPU to verify; alive is always True)."""

    extra: dict = {}
    """Optional vendor-specific diagnostic detail (raw command output, parsed values, etc.).
    For debugging; consumers should rely on alive + divergence_reasons for decision-making."""


class RecoveryAttempt(BaseModel):
    attempted: bool
    """True if a recovery action was actually run."""

    success: Optional[bool] = None
    """True/False if recovery completed and was re-verified. None if we don't know yet."""

    action: str = ""
    """What was attempted, e.g. 'docker restart ollama'."""

    message: str = ""
    """Human-readable detail (stdout/stderr summary, or 'not supported on this probe')."""


class VerificationProbe(ABC):
    """Vendor-pluggable GPU-state verification. One implementation per accelerator family."""

    @abstractmethod
    def name(self) -> str:
        """Short identifier, e.g. 'nvidia', 'intel-arc', 'cpu'. Match the metrics-probe name() value."""

    @abstractmethod
    def is_applicable(self) -> bool:
        """True if this probe can run on the current host (relevant hardware + tooling present)."""

    @abstractmethod
    async def verify(self, ollama_ps_state: Optional[dict] = None) -> VerificationResult:
        """Run cross-source checks and return a VerificationResult.

        ollama_ps_state is the most recent /api/ps response (or None if collection failed).
        Probe uses this alongside its vendor-specific signals (host smi tool, container
        exec, etc.) to produce the alive/divergence answer.
        """

    async def recover(self) -> RecoveryAttempt:
        """Attempt to recover from divergence. Default: no-op (probe doesn't support recovery).

        NVIDIA implementation runs `docker restart ollama` (today's regression class is
        NVML handle drift inside a container; restart re-establishes the handle).
        Vendor-specific implementations override; CPU and stubs leave the default no-op.
        """
        return RecoveryAttempt(
            attempted=False,
            success=None,
            action="",
            message=f"Recovery not implemented for probe '{self.name()}'",
        )


def select_verification_probe(metrics_probe_name: str) -> "VerificationProbe":
    """Choose a verification probe matching the active metrics probe name.

    Mirrors `select_probe()` in ../probes/__init__.py — keeps verification + metrics
    aligned on the same hardware family.
    """
    from .nvidia import NvidiaVerificationProbe
    from .intel_arc import IntelArcVerificationProbe
    from .intel_iris import IntelIrisVerificationProbe
    from .amd_rocm import AmdRocmVerificationProbe
    from .apple_silicon import AppleSiliconVerificationProbe
    from .cpu import CpuVerificationProbe

    by_name = {
        "nvidia": NvidiaVerificationProbe,
        "intel-arc": IntelArcVerificationProbe,
        "intel-iris": IntelIrisVerificationProbe,
        "amd": AmdRocmVerificationProbe,
        "apple-silicon": AppleSiliconVerificationProbe,
        "cpu": CpuVerificationProbe,
    }
    probe_cls = by_name.get(metrics_probe_name, CpuVerificationProbe)
    return probe_cls()
