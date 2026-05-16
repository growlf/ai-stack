"""Intel Iris (Xe / UHD) probe — STUB.

For older Intel iGPUs (Iris Xe, UHD Graphics) on NUC-class hardware. Shares the
xe/i915 driver story with Arc. nuk1 previously worked on its Iris per Garth; same
investigation arc applies.
"""

import os
import subprocess

from . import Probe, HardwareMetrics


class IntelIrisProbe(Probe):
    def name(self) -> str:
        return "intel-iris"

    def is_available(self) -> bool:
        if not os.path.exists("/dev/dri/renderD128"):
            return False
        try:
            r = subprocess.run(
                ["lspci", "-nn"],
                capture_output=True, text=True, timeout=2, check=False,
            )
            if r.returncode != 0:
                return False
            text = r.stdout
            # Heuristic: Intel VGA controller that isn't Arc.
            return "[8086:" in text and ("Iris" in text or "UHD" in text or "HD Graphics" in text) and "Arc" not in text
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def read_metrics(self) -> HardwareMetrics:
        return HardwareMetrics(
            accelerator_type="intel-iris",
            accelerator_name="Intel(R) Iris/UHD Graphics (metrics pending)",
            implementation_status="stub",
        )
