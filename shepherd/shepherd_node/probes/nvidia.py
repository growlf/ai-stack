"""NVIDIA probe — reads metrics via nvidia-smi subprocess."""

import shutil
import subprocess

from . import Probe, HardwareMetrics


class NvidiaProbe(Probe):
    def name(self) -> str:
        return "nvidia"

    def is_available(self) -> bool:
        return shutil.which("nvidia-smi") is not None and self._smi_responds()

    def _smi_responds(self) -> bool:
        try:
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                capture_output=True, timeout=2, check=False,
            )
            return r.returncode == 0 and r.stdout.strip() != b""
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def read_metrics(self) -> HardwareMetrics:
        # Single subprocess call, parse CSV row. Cheap (~5-15ms).
        r = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.used,memory.total,utilization.gpu,power.draw,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True, text=True, timeout=3, check=False,
        )
        if r.returncode != 0:
            return HardwareMetrics(
                accelerator_type="nvidia",
                accelerator_name="NVIDIA (unreachable)",
                implementation_status="implemented",
            )
        first = r.stdout.strip().splitlines()[0]
        parts = [p.strip() for p in first.split(",")]
        name, mem_used, mem_total, util, power, temp = parts

        def _to_int(s: str) -> int | None:
            try:
                return int(s)
            except (ValueError, TypeError):
                return None

        def _to_float(s: str) -> float | None:
            try:
                return float(s)
            except (ValueError, TypeError):
                return None

        return HardwareMetrics(
            accelerator_type="nvidia",
            accelerator_name=name,
            vram_used_mb=_to_int(mem_used),
            vram_total_mb=_to_int(mem_total),
            utilization_pct=_to_int(util),
            power_watts=_to_float(power),
            temp_celsius=_to_int(temp),
            implementation_status="implemented",
        )
