"""Apple Silicon probe — STUB.

Awaits first community contributor with an M-series Mac. Real implementation will use
`powermetrics --samplers gpu_power -i 1000 -n 1` parsed output, or MLX runtime APIs
once we have a Mac to test against.
"""

import platform

from . import Probe, HardwareMetrics


class AppleSiliconProbe(Probe):
    def name(self) -> str:
        return "apple-silicon"

    def is_available(self) -> bool:
        return platform.system() == "Darwin" and platform.machine() == "arm64"

    def read_metrics(self) -> HardwareMetrics:
        return HardwareMetrics(
            accelerator_type="apple-silicon",
            accelerator_name="Apple Silicon (metrics pending implementation)",
            implementation_status="stub",
        )
