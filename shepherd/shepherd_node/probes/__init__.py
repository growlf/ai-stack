"""Probe interface for hardware/accelerator metrics.

Each probe is an adapter for a specific hardware family (NVIDIA, Intel Arc, AMD, etc.).
The shepherd-node service iterates installed probes, calls is_available() to detect which
applies on this host, and uses the matching probe to read accelerator metrics.

Stub probes return HardwareMetrics with all optional fields None — they advertise that the
hardware type was *recognized* without claiming we can read metrics for it yet. A new
community contributor with (say) AMD hardware sees their node in the dashboard as
"AMD detected, full metrics pending implementation" rather than silent absence.
"""

from abc import ABC, abstractmethod
from typing import Optional
from pydantic import BaseModel


class HardwareMetrics(BaseModel):
    accelerator_type: str
    """One of: nvidia | intel-arc | intel-iris | amd | apple-silicon | arm-npu | webgpu | cpu"""

    accelerator_name: str
    """Human-readable, e.g. 'RTX 3090 Ti' or 'Intel(R) Arc(TM) Graphics'."""

    vram_used_mb: Optional[int] = None
    """None for CPU-only or stub probes."""

    vram_total_mb: Optional[int] = None

    utilization_pct: Optional[int] = None
    """0-100. None for CPU-only or when the probe can't read this."""

    power_watts: Optional[float] = None

    temp_celsius: Optional[int] = None

    implementation_status: str = "implemented"
    """Either 'implemented' (real readings) or 'stub' (hardware recognized, metrics not yet)."""


class Probe(ABC):
    """Hardware probe interface. Implementations live in this package as one file each."""

    @abstractmethod
    def name(self) -> str:
        """Short probe identifier, e.g. 'nvidia', 'intel-arc', 'cpu'."""

    @abstractmethod
    def is_available(self) -> bool:
        """True if this probe can produce real readings on the current host."""

    @abstractmethod
    def read_metrics(self) -> HardwareMetrics:
        """Synchronous read. Cheap implementations preferred — called every poll cycle."""


def discover_probes() -> list[Probe]:
    """Return all probe instances in registration order. First available wins."""
    from .nvidia import NvidiaProbe
    from .intel_arc import IntelArcProbe
    from .intel_iris import IntelIrisProbe
    from .amd_rocm import AmdRocmProbe
    from .apple_silicon import AppleSiliconProbe
    from .webgpu import WebGpuProbe
    from .cpu import CpuProbe
    return [
        NvidiaProbe(),
        IntelArcProbe(),
        IntelIrisProbe(),
        AmdRocmProbe(),
        AppleSiliconProbe(),
        WebGpuProbe(),
        CpuProbe(),
    ]


def select_probe() -> Probe:
    """Pick the first available probe. CPU always wins if nothing else does."""
    for probe in discover_probes():
        if probe.is_available():
            return probe
    from .cpu import CpuProbe
    return CpuProbe()
