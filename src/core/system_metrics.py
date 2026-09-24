"""Collect CarPC system resource information (CPU, RAM, disk, queue, ...)."""

from __future__ import annotations

import asyncio
import os
import sys
import time
from dataclasses import dataclass, field

import psutil


@dataclass
class SystemMetrics:
    """Snapshot of system resource information at one point in time."""

    timestamp: float = 0.0

    # ── CPU ──────────────────────────────────────────────────────────────────
    cpu_percent: float = 0.0  # total system CPU %
    cpu_percent_per_core: list[float] = field(default_factory=list)  # per-core CPU %
    cpu_count_logical: int = 0
    cpu_count_physical: int = 0
    cpu_freq_current_mhz: float = 0.0
    cpu_freq_max_mhz: float = 0.0

    # ── Process (CarPC process) ──────────────────────────────────────────────
    process_cpu_percent: float = 0.0
    process_memory_rss_mb: float = 0.0  # Resident Set Size
    process_memory_vms_mb: float = 0.0  # Virtual Memory Size
    process_memory_percent: float = 0.0
    process_threads: int = 0
    process_open_files: int = 0
    process_pid: int = 0

    # ── RAM ──────────────────────────────────────────────────────────────────
    ram_total_mb: float = 0.0
    ram_available_mb: float = 0.0
    ram_used_mb: float = 0.0
    ram_percent: float = 0.0

    # ── Swap ─────────────────────────────────────────────────────────────────
    swap_total_mb: float = 0.0
    swap_used_mb: float = 0.0
    swap_percent: float = 0.0

    # ── Disk ─────────────────────────────────────────────────────────────────
    disk_total_gb: float = 0.0
    disk_used_gb: float = 0.0
    disk_free_gb: float = 0.0
    disk_percent: float = 0.0

    # ── Network I/O ──────────────────────────────────────────────────────────
    net_bytes_sent: int = 0
    net_bytes_recv: int = 0
    net_packets_sent: int = 0
    net_packets_recv: int = 0

    # ── Application-specific ─────────────────────────────────────────────────
    queue_size: int = 0  # current asyncio.Queue size
    queue_maxsize: int = 0
    queue_usage_percent: float = 0.0
    heap_allocated_mb: float = 0.0  # Python heap (sys.getsizeof approximation)
    gc_objects: int = 0  # Number of objects tracked by the garbage collector
    asyncio_tasks: int = 0  # Number of running tasks
    uptime_seconds: float = 0.0
    python_version: str = ""
    platform: str = ""


_MB = 1024 * 1024
_GB = 1024 * 1024 * 1024
_process = psutil.Process(os.getpid())


def collect_system_metrics(
    *,
    rx_queue: asyncio.Queue | None = None,
    start_time: float = 0.0,
) -> SystemMetrics:
    """Collect a snapshot of system resources. Non-blocking and safe to call from an async context."""
    import gc
    import platform

    now = time.time()
    m = SystemMetrics(timestamp=now)

    # ── CPU ──────────────────────────────────────────────────────────────────
    m.cpu_percent = psutil.cpu_percent(interval=None)
    m.cpu_percent_per_core = psutil.cpu_percent(interval=None, percpu=True)
    m.cpu_count_logical = psutil.cpu_count(logical=True) or 0
    m.cpu_count_physical = psutil.cpu_count(logical=False) or 0
    freq = psutil.cpu_freq()
    if freq:
        m.cpu_freq_current_mhz = round(freq.current, 1)
        m.cpu_freq_max_mhz = round(freq.max, 1)

    # ── Process ──────────────────────────────────────────────────────────────
    try:
        with _process.oneshot():
            m.process_cpu_percent = _process.cpu_percent(interval=None)
            mem_info = _process.memory_info()
            m.process_memory_rss_mb = round(mem_info.rss / _MB, 2)
            m.process_memory_vms_mb = round(mem_info.vms / _MB, 2)
            m.process_memory_percent = round(_process.memory_percent(), 2)
            m.process_threads = _process.num_threads()
            try:
                m.process_open_files = len(_process.open_files())
            except (psutil.AccessDenied, OSError):
                m.process_open_files = -1
            m.process_pid = _process.pid
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass

    # ── RAM ──────────────────────────────────────────────────────────────────
    vm = psutil.virtual_memory()
    m.ram_total_mb = round(vm.total / _MB, 1)
    m.ram_available_mb = round(vm.available / _MB, 1)
    m.ram_used_mb = round(vm.used / _MB, 1)
    m.ram_percent = vm.percent

    # ── Swap ─────────────────────────────────────────────────────────────────
    sw = psutil.swap_memory()
    m.swap_total_mb = round(sw.total / _MB, 1)
    m.swap_used_mb = round(sw.used / _MB, 1)
    m.swap_percent = sw.percent

    # ── Disk (partition containing the working directory) ───────────────────
    try:
        disk = psutil.disk_usage(os.getcwd())
        m.disk_total_gb = round(disk.total / _GB, 2)
        m.disk_used_gb = round(disk.used / _GB, 2)
        m.disk_free_gb = round(disk.free / _GB, 2)
        m.disk_percent = disk.percent
    except OSError:
        pass

    # ── Network I/O ──────────────────────────────────────────────────────────
    net = psutil.net_io_counters()
    if net:
        m.net_bytes_sent = net.bytes_sent
        m.net_bytes_recv = net.bytes_recv
        m.net_packets_sent = net.packets_sent
        m.net_packets_recv = net.packets_recv

    # ── Application-specific ─────────────────────────────────────────────────
    if rx_queue is not None:
        m.queue_size = rx_queue.qsize()
        m.queue_maxsize = rx_queue.maxsize
        m.queue_usage_percent = (
            round(rx_queue.qsize() / rx_queue.maxsize * 100, 1)
            if rx_queue.maxsize > 0
            else 0.0
        )

    # Python heap approximation (total size of GC-tracked objects)
    m.gc_objects = len(gc.get_objects())
    # sys.getsizeof is not recursive; use memory_info RSS as the primary indicator
    m.heap_allocated_mb = m.process_memory_rss_mb

    # asyncio tasks
    try:
        loop = asyncio.get_running_loop()
        m.asyncio_tasks = len(asyncio.all_tasks(loop))
    except RuntimeError:
        m.asyncio_tasks = 0

    m.uptime_seconds = round(now - start_time, 1) if start_time else 0.0
    m.python_version = sys.version.split()[0]
    m.platform = platform.platform()

    return m


def metrics_to_dict(m: SystemMetrics) -> dict:
    """Convert SystemMetrics to a flat dict for JSON responses."""
    from dataclasses import asdict

    return asdict(m)
