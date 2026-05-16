"""System-level metrics: CPU%, RAM, network throughput. From psutil + /proc."""

import psutil
import time

# Cache last network counter for delta computation
_last_net = {"ts": 0.0, "rx": 0, "tx": 0}


def collect_system() -> dict:
    cpu_pct = psutil.cpu_percent(interval=None)  # non-blocking
    mem = psutil.virtual_memory()
    load_avg = psutil.getloadavg() if hasattr(psutil, "getloadavg") else (0.0, 0.0, 0.0)
    rx_kbps, tx_kbps = _network_throughput()
    return {
        "cpu_pct": round(cpu_pct, 1),
        "ram_used_mb": mem.used // (1024 * 1024),
        "ram_total_mb": mem.total // (1024 * 1024),
        "load_avg_1m": round(load_avg[0], 2),
        "network_rx_kbps": rx_kbps,
        "network_tx_kbps": tx_kbps,
    }


def _network_throughput() -> tuple[float, float]:
    """Delta-based throughput. First call returns 0,0; subsequent calls return kbps."""
    now = time.time()
    counters = psutil.net_io_counters()
    rx, tx = counters.bytes_recv, counters.bytes_sent
    elapsed = now - _last_net["ts"] if _last_net["ts"] else 0
    if elapsed > 0:
        rx_kbps = round((rx - _last_net["rx"]) * 8 / 1024 / elapsed, 1)
        tx_kbps = round((tx - _last_net["tx"]) * 8 / 1024 / elapsed, 1)
    else:
        rx_kbps = 0.0
        tx_kbps = 0.0
    _last_net.update({"ts": now, "rx": rx, "tx": tx})
    return rx_kbps, tx_kbps
