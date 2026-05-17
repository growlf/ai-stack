"""AMD ROCm probe — STUB.

Awaits first community contributor with an AMD GPU. Real implementation will use
`rocm-smi --showmeminfo vram --json` or similar; falls through to stub until then.
"""

import shutil

from . import Probe, HardwareMetrics


class AmdRocmProbe(Probe):
    def name(self) -> str:
        return "amd"

    def is_available(self) -> bool:
        # Detect by presence of rocm-smi (if installed, ROCm hardware likely present).
        return shutil.which("rocm-smi") is not None

    def read_metrics(self) -> HardwareMetrics:
        return HardwareMetrics(
            accelerator_type="amd",
            accelerator_name="AMD GPU via ROCm (metrics pending implementation)",
            implementation_status="stub",
        )
