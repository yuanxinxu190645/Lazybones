"""Hardware detection and resource budgets for Lazybones.

The module deliberately has no Tk dependency so it can also be imported by
worker processes and unit tests.  Every value is bounded: a malformed old
settings file must never be able to create hundreds of workers.
"""

from __future__ import annotations

import ctypes
import os
from dataclasses import asdict, dataclass
from typing import Any


MODE_LABELS = {
    "eco": "省资源",
    "balanced": "均衡（推荐）",
    "high": "高性能",
    "custom": "自定义",
}

MODE_IDS_BY_LABEL = {label: mode for mode, label in MODE_LABELS.items()}


@dataclass(frozen=True)
class GpuStatus:
    name: str = "未检测到独立显卡"
    memory_mb: int = 0
    driver_version: str = ""
    directml_available: bool = False
    cpu_provider_available: bool = False
    runtime_error: str = ""

    @property
    def detected(self) -> bool:
        return bool(self.memory_mb or self.name != "未检测到独立显卡")


@dataclass(frozen=True)
class SystemResources:
    logical_cores: int
    total_memory_mb: int
    available_memory_mb: int
    gpu: GpuStatus


@dataclass(frozen=True)
class PerformanceBudget:
    mode: str
    cpu_workers: int
    ai_workers: int
    active_documents: int
    memory_limit_percent: int
    ocr_dpi: int
    gpu_mode: str
    gpu_ocr_enabled: bool
    gpu_ocr_ready: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clamp(value: Any, low: int, high: int, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(low, min(high, parsed))


def _memory_status() -> tuple[int, int]:
    try:
        import psutil

        memory = psutil.virtual_memory()
        return int(memory.total // (1024 * 1024)), int(
            memory.available // (1024 * 1024))
    except (ImportError, OSError):
        pass

    if os.name == "nt":
        class MemoryStatusEx(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatusEx()
        status.dwLength = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            divisor = 1024 * 1024
            return int(status.ullTotalPhys // divisor), int(
                status.ullAvailPhys // divisor)
    return 0, 0


def _detect_nvidia_registry() -> tuple[str, int, str]:
    if os.name != "nt":
        return "未检测到独立显卡", 0, ""
    try:
        import winreg

        root = r"SYSTEM\CurrentControlSet\Control\Video"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, root) as parent:
            for i in range(winreg.QueryInfoKey(parent)[0]):
                adapter_id = winreg.EnumKey(parent, i)
                key_path = root + "\\" + adapter_id + r"\0000"
                try:
                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                        key_path) as key:
                        name = str(winreg.QueryValueEx(key, "DriverDesc")[0])
                        if "NVIDIA" not in name.upper():
                            continue
                        try:
                            raw_memory = winreg.QueryValueEx(
                                key, "HardwareInformation.qwMemorySize")[0]
                            memory_mb = int(raw_memory) // (1024 * 1024)
                        except (FileNotFoundError, OSError, TypeError):
                            memory_mb = 0
                        try:
                            driver = str(winreg.QueryValueEx(
                                key, "DriverVersion")[0])
                        except (FileNotFoundError, OSError):
                            driver = ""
                        return name, memory_mb, driver
                except (FileNotFoundError, OSError):
                    continue
    except (ImportError, FileNotFoundError, OSError):
        pass
    return "未检测到独立显卡", 0, ""


def detect_gpu_status() -> GpuStatus:
    name, memory_mb, driver = _detect_nvidia_registry()
    providers: list[str] = []
    runtime_error = ""
    try:
        import onnxruntime as ort

        providers = list(ort.get_available_providers())
    except Exception as exc:  # optional runtime or a broken native DLL
        runtime_error = f"{type(exc).__name__}: {exc}"
    return GpuStatus(
        name=name,
        memory_mb=memory_mb,
        driver_version=driver,
        directml_available="DmlExecutionProvider" in providers,
        cpu_provider_available="CPUExecutionProvider" in providers,
        runtime_error=runtime_error,
    )


def detect_system_resources() -> SystemResources:
    total, available = _memory_status()
    return SystemResources(
        logical_cores=max(1, int(os.cpu_count() or 1)),
        total_memory_mb=total,
        available_memory_mb=available,
        gpu=detect_gpu_status(),
    )


def resolve_performance_budget(
        settings: dict[str, Any],
        resources: SystemResources | None = None) -> PerformanceBudget:
    resources = resources or detect_system_resources()
    mode = str(settings.get("performance_mode", "balanced")).lower()
    if mode not in MODE_LABELS:
        mode = "balanced"

    cores = max(1, resources.logical_cores)
    max_cpu = max(1, min(12, cores - 1 if cores > 1 else 1))
    if mode == "eco":
        cpu_workers, ai_workers, active_docs = 1, 1, 1
        memory_limit, ocr_dpi = 65, 160
    elif mode == "high":
        cpu_workers = min(6, max_cpu)
        ai_workers = min(6, max(2, cores // 2))
        active_docs = min(6, max(cpu_workers, ai_workers))
        memory_limit, ocr_dpi = 85, 220
    elif mode == "custom":
        cpu_workers = _clamp(settings.get("custom_cpu_workers"), 1,
                             max_cpu, min(2, max_cpu))
        ai_workers = _clamp(settings.get("custom_ai_workers"), 1, 12, 3)
        active_docs = _clamp(settings.get("custom_active_documents"),
                             1, 12, 3)
        memory_limit = _clamp(settings.get("memory_limit_percent"),
                              50, 90, 75)
        ocr_dpi = _clamp(settings.get("ocr_dpi"), 120, 300, 190)
    else:
        cpu_workers = min(2, max_cpu)
        ai_workers = min(3, max(1, cores // 3))
        active_docs = min(3, max(cpu_workers, ai_workers))
        memory_limit, ocr_dpi = 75, 190

    # Keep headroom on low-memory computers.  PDF/table extraction can briefly
    # consume hundreds of MB, so use the *currently available* memory too.
    if resources.available_memory_mb:
        if resources.total_memory_mb:
            used_mb = max(
                0, resources.total_memory_mb - resources.available_memory_mb)
            allowed_used_mb = (
                resources.total_memory_mb * memory_limit // 100)
            task_headroom_mb = max(0, allowed_used_mb - used_mb)
        else:
            task_headroom_mb = resources.available_memory_mb
        safe_by_ram = max(1, task_headroom_mb // 900)
        cpu_workers = min(cpu_workers, safe_by_ram)
        active_docs = min(active_docs, max(1, safe_by_ram + 1))

    gpu_mode = str(settings.get("gpu_mode", "auto")).lower()
    if gpu_mode not in {"auto", "off", "force"}:
        gpu_mode = "auto"
    gpu_requested = bool(settings.get("gpu_ocr_enabled", True))
    gpu_ready = (
        gpu_requested
        and gpu_mode != "off"
        and resources.gpu.directml_available
    )

    return PerformanceBudget(
        mode=mode,
        cpu_workers=max(1, cpu_workers),
        ai_workers=max(1, min(ai_workers, active_docs)),
        active_documents=max(1, active_docs),
        memory_limit_percent=memory_limit,
        ocr_dpi=ocr_dpi,
        gpu_mode=gpu_mode,
        gpu_ocr_enabled=gpu_requested,
        gpu_ocr_ready=gpu_ready,
    )


class PerformanceManager:
    """Refreshable snapshot used by the UI and extraction scheduler."""

    def __init__(self, settings: dict[str, Any]):
        self.resources = detect_system_resources()
        self.budget = resolve_performance_budget(settings, self.resources)

    def refresh(self, settings: dict[str, Any], *, redetect: bool = False):
        if redetect:
            self.resources = detect_system_resources()
        self.budget = resolve_performance_budget(settings, self.resources)
        return self.budget

    def hardware_summary(self) -> str:
        ram_gb = self.resources.total_memory_mb / 1024
        gpu = self.resources.gpu
        gpu_text = gpu.name
        if gpu.memory_mb:
            gpu_text += f" {gpu.memory_mb / 1024:.1f} GB"
        runtime = "DirectML 可用" if gpu.directml_available else "DirectML 不可用"
        return (f"CPU {self.resources.logical_cores} 线程 · 内存 {ram_gb:.1f} GB · "
                f"{gpu_text} · {runtime}")

    def budget_summary(self) -> str:
        b = self.budget
        gpu = "GPU OCR" if b.gpu_ocr_ready else "CPU OCR"
        return (f"{MODE_LABELS[b.mode]}：文档进程 {b.cpu_workers} · "
                f"AI 并发 {b.ai_workers} · 同时任务 {b.active_documents} · "
                f"OCR {b.ocr_dpi} DPI · {gpu}")
