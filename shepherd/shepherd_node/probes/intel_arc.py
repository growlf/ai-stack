"""Intel Arc probe — STUB.

Phoenix runs Intel Arc Graphics (Meteor Lake). The xe kernel driver binds correctly
but BigDL ipex-llm's memory query returns 0 B against kernel 6.17.x (full investigation
in ~/enclave-core/docs/phoenix-arc-investigation-backlog.md).

When the upstream regression resolves OR we install an older kernel, this probe lights up
via `intel_gpu_top -J` or directly via the level-zero loader.

Until then: detect the hardware (so the dashboard surfaces it as "Intel Arc detected,
metrics pending") and return a stub HardwareMetrics with implementation_status='stub'.
"""

import os
import subprocess

from . import Probe, HardwareMetrics


class IntelArcProbe(Probe):
    def name(self) -> str:
        return "intel-arc"

    def is_available(self) -> bool:
        # Detect Arc presence without trying to read metrics.
        # /dev/dri/renderD128 + Arc-class hardware identification.
        if not os.path.exists("/dev/dri/renderD128"):
            return False
        try:
            r = subprocess.run(
                ["lspci", "-nn"],
                capture_output=True, text=True, timeout=2, check=False,
            )
            if r.returncode != 0:
                return False
            return "Arc" in r.stdout and "[8086:" in r.stdout  # Intel VGA controller
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def read_metrics(self) -> HardwareMetrics:
        # Stub: detect the hardware but don't try to read metrics until the
        # upstream regression is resolved. Use intel_gpu_top -J once it works.
        return HardwareMetrics(
            accelerator_type="intel-arc",
            accelerator_name="Intel(R) Arc(TM) Graphics (metrics pending — see phoenix-arc-investigation-backlog.md)",
            implementation_status="stub",
        )
