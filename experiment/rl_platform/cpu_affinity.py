"""Assign rollout and evaluation workers to distinct available CPU cores."""

from __future__ import annotations

import os
from pathlib import Path


def available_worker_cpus() -> list[int]:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.GetProcessAffinityMask.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(ctypes.c_size_t)]
        process_mask, system_mask = ctypes.c_size_t(), ctypes.c_size_t()
        if not kernel.GetProcessAffinityMask(kernel.GetCurrentProcess(), ctypes.byref(process_mask), ctypes.byref(system_mask)):
            raise ctypes.WinError(ctypes.get_last_error())
        return [cpu for cpu in range(ctypes.sizeof(process_mask) * 8) if process_mask.value & (1 << cpu)]

    # Use only one hardware thread per physical core, including under cpusets.
    cores: dict[tuple[str, str], int] = {}
    for cpu in sorted(os.sched_getaffinity(0)):
        topology: Path = Path(f"/sys/devices/system/cpu/cpu{cpu}/topology")
        key: tuple[str, str] = ((topology / "physical_package_id").read_text().strip(),
                               (topology / "core_id").read_text().strip())
        cores.setdefault(key, cpu)
    return list(cores.values())


def bind_worker_cpu(cpu: int) -> None:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.SetProcessAffinityMask.argtypes = [wintypes.HANDLE, ctypes.c_size_t]
        if not kernel.SetProcessAffinityMask(kernel.GetCurrentProcess(), 1 << cpu):
            raise ctypes.WinError(ctypes.get_last_error())
    else:
        os.sched_setaffinity(0, {cpu})
