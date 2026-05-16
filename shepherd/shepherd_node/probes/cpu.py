"""CPU probe — the floor. Always available; reports system-only metrics with no accelerator."""

import platform

from . import Probe, HardwareMetrics


class CpuProbe(Probe):
    def name(self) -> str:
        return "cpu"

    def is_available(self) -> bool:
        return True  # Always available — floor probe.

    def read_metrics(self) -> HardwareMetrics:
        # CPU-only nodes report no VRAM. The system collector handles CPU%, RAM, etc.
        # separately; this probe just identifies the accelerator class as "none".
        return HardwareMetrics(
            accelerator_type="cpu",
            accelerator_name=f"CPU-only ({platform.processor() or platform.machine()})",
            implementation_status="implemented",
        )
