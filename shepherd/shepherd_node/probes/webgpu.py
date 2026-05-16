"""WebGPU probe — STUB.

For the future case where a browser tab acts as a herd participant via WebLLM/WebGPU.
This probe wouldn't run server-side; the browser-side participant would report metrics
via a future POST /herd/report-from-browser endpoint. Stub exists to keep the schema
shape stable.
"""

from . import Probe, HardwareMetrics


class WebGpuProbe(Probe):
    def name(self) -> str:
        return "webgpu"

    def is_available(self) -> bool:
        # Server-side never available; browser-side participants self-report.
        return False

    def read_metrics(self) -> HardwareMetrics:
        return HardwareMetrics(
            accelerator_type="webgpu",
            accelerator_name="WebGPU (browser-side reporting pending)",
            implementation_status="stub",
        )
