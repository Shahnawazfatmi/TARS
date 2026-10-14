import ctypes
import functools
import os
import platform
import socket
import subprocess
import time
from datetime import datetime

import psutil

try:
    import winreg
except ImportError:  # Not on Windows
    winreg = None


GB = 1024 ** 3

# Prime the CPU counter so later non-blocking reads are meaningful.
psutil.cpu_percent(interval=None)


def _gb(value: float) -> float:
    return round(value / GB, 2)


# ============================================================
# BASIC SYSTEM
# ============================================================

def get_system_info() -> dict:
    """Get basic system information."""

    info = {
        "os": platform.system(),
        "os_release": platform.release(),
        "os_version": platform.version(),
        "machine": platform.machine(),
        "hostname": socket.gethostname(),
    }

    if hasattr(platform, "win32_edition"):
        edition = platform.win32_edition()

        if edition:
            info["windows_edition"] = edition

    return info


def get_current_directory() -> str:
    """Get the current working directory."""

    return os.getcwd()


def list_files(path: str = ".", limit: int = 100) -> dict:
    """List files and folders inside a directory."""

    target = os.path.abspath(os.path.expanduser(os.path.expandvars(path)))

    if not os.path.isdir(target):
        return {"error": f"Folder not found: {target}"}

    entries = []

    try:
        with os.scandir(target) as it:
            for entry in it:
                try:
                    is_dir = entry.is_dir()
                    size = None if is_dir else entry.stat().st_size

                except OSError:
                    is_dir, size = False, None

                entries.append({
                    "name": entry.name,
                    "type": "folder" if is_dir else "file",
                    "size_bytes": size,
                })

    except PermissionError:
        return {"error": f"Permission denied: {target}"}

    entries.sort(key=lambda e: (e["type"] != "folder", e["name"].lower()))

    limit = max(1, int(limit))

    return {
        "path": target,
        "total": len(entries),
        "shown": min(limit, len(entries)),
        "entries": entries[:limit],
    }


# ============================================================
# USAGE
# ============================================================

def get_cpu_usage() -> dict:
    """Get current CPU usage."""

    # Non-blocking: compares against the previous call instead
    # of sleeping for a second.
    result = {
        "usage_percent": psutil.cpu_percent(interval=None),
        "per_core_percent": psutil.cpu_percent(interval=None, percpu=True),
    }

    freq = psutil.cpu_freq()

    if freq:
        result["current_mhz"] = round(freq.current)
        result["max_mhz"] = round(freq.max) if freq.max else None

    return result


def get_ram_usage() -> dict:
    """Get current RAM usage."""

    memory = psutil.virtual_memory()
    swap = psutil.swap_memory()

    return {
        "total_gb": _gb(memory.total),
        "used_gb": _gb(memory.used),
        "available_gb": _gb(memory.available),
        "usage_percent": memory.percent,
        "swap_total_gb": _gb(swap.total),
        "swap_used_gb": _gb(swap.used),
    }


def get_disk_usage(drive: str = "") -> dict:
    """Get disk usage for one drive (default: the system drive)."""

    if not drive:
        drive = os.environ.get("SystemDrive", "C:") + "\\" if os.name == "nt" else "/"

    elif os.name == "nt" and len(drive) <= 2:
        drive = drive.rstrip(":\\") + ":\\"

    try:
        disk = psutil.disk_usage(drive)

    except (FileNotFoundError, PermissionError, OSError):
        return {"error": f"Drive not available: {drive}"}

    return {
        "drive": drive,
        "total_gb": _gb(disk.total),
        "used_gb": _gb(disk.used),
        "free_gb": _gb(disk.free),
        "usage_percent": disk.percent,
    }


def get_all_disks() -> list:
    """Get usage for every mounted drive."""

    disks = []

    for part in psutil.disk_partitions(all=False):

        if "cdrom" in part.opts or not part.fstype:
            continue

        try:
            usage = psutil.disk_usage(part.mountpoint)

        except (PermissionError, OSError):
            continue

        disks.append({
            "drive": part.mountpoint,
            "filesystem": part.fstype,
            "total_gb": _gb(usage.total),
            "free_gb": _gb(usage.free),
            "usage_percent": usage.percent,
        })

    return disks


# ============================================================
# HARDWARE
# ============================================================

def _cpu_name() -> str:
    """Return the marketing CPU name (e.g. 'Intel Core i7-1255U')."""

    if winreg:
        try:
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
            ) as key:
                name, _ = winreg.QueryValueEx(key, "ProcessorNameString")
                return name.strip()

        except OSError:
            pass

    return platform.processor() or "Unknown"


_DISPLAY_CLASS = (
    r"SYSTEM\CurrentControlSet\Control\Class"
    r"\{4d36e968-e325-11ce-bfc1-08002be10318}"
)


def _gpus_from_registry() -> list:
    """
    Read GPUs from the Windows display-adapter registry keys.

    HardwareInformation.qwMemorySize holds the real VRAM, unlike
    WMI AdapterRAM, which is capped at 4 GB.
    """

    if not winreg:
        return []

    gpus = []

    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _DISPLAY_CLASS)

    except OSError:
        return []

    with root:
        index = 0

        while True:
            try:
                sub = winreg.EnumKey(root, index)

            except OSError:
                break

            index += 1

            if not sub.isdigit():
                continue

            try:
                with winreg.OpenKey(root, sub) as key:
                    name, _ = winreg.QueryValueEx(key, "DriverDesc")

                    vram = None

                    for value_name in (
                        "HardwareInformation.qwMemorySize",
                        "HardwareInformation.MemorySize",
                    ):
                        try:
                            raw, _ = winreg.QueryValueEx(key, value_name)

                            if isinstance(raw, bytes):
                                raw = int.from_bytes(raw, "little")

                            vram = int(raw)
                            break

                        except (OSError, ValueError, TypeError):
                            continue

            except OSError:
                continue

            if "basic display" in name.lower() or "remote display" in name.lower():
                continue

            gpus.append({
                "name": name,
                "vram_gb": _gb(vram) if vram else None,
            })

    return gpus


def _gpus_from_nvidia_smi() -> list:
    """Read NVIDIA GPUs (works on any OS with NVIDIA drivers)."""

    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )

    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return []

    if result.returncode != 0:
        return []

    gpus = []

    for line in result.stdout.strip().splitlines():
        try:
            name, vram_mb = [part.strip() for part in line.rsplit(",", 1)]
            gpus.append({
                "name": name,
                "vram_gb": round(float(vram_mb) / 1024, 2),
            })

        except ValueError:
            continue

    return gpus


@functools.lru_cache(maxsize=1)
def _static_hardware() -> dict:
    """Hardware that never changes while FATMEE runs (cached)."""

    gpus = _gpus_from_registry()

    # nvidia-smi is more precise for NVIDIA cards and covers Linux.
    nvidia = _gpus_from_nvidia_smi() if (
        not gpus or any("nvidia" in g["name"].lower() for g in gpus)
    ) else []

    if nvidia:
        others = [g for g in gpus if "nvidia" not in g["name"].lower()]
        gpus = nvidia + others

    primary = max(
        gpus,
        key=lambda g: g["vram_gb"] or 0,
        default={"name": "Unknown", "vram_gb": None},
    )

    return {
        "cpu": _cpu_name(),
        "architecture": platform.machine(),
        "physical_cores": psutil.cpu_count(logical=False),
        "logical_cores": psutil.cpu_count(logical=True),
        "ram_gb": _gb(psutil.virtual_memory().total),
        "gpu": primary["name"],
        "vram_gb": primary["vram_gb"],
        "gpus": gpus,
    }


def get_hardware_info() -> dict:
    """Get detailed hardware information."""

    # Return a copy so callers can't modify the cached value.
    info = dict(_static_hardware())
    info["gpus"] = [dict(g) for g in info["gpus"]]

    return info


# ============================================================
# POWER, NETWORK, PROCESSES
# ============================================================

def get_battery_status() -> dict:
    """Get battery level and charging state."""

    battery = psutil.sensors_battery()

    if battery is None:
        return {"has_battery": False}

    if battery.secsleft in (psutil.POWER_TIME_UNLIMITED, psutil.POWER_TIME_UNKNOWN):
        minutes_left = None

    else:
        minutes_left = round(battery.secsleft / 60)

    return {
        "has_battery": True,
        "percent": round(battery.percent, 1),
        "plugged_in": battery.power_plugged,
        "minutes_left": minutes_left,
    }


def get_network_info() -> dict:
    """Get active network adapters, IP addresses and traffic totals."""

    stats = psutil.net_if_stats()
    addresses = psutil.net_if_addrs()

    adapters = []

    for name, addrs in addresses.items():
        stat = stats.get(name)

        if not stat or not stat.isup:
            continue

        ipv4 = [a.address for a in addrs if a.family == socket.AF_INET]

        if not ipv4 or all(ip.startswith("127.") for ip in ipv4):
            continue

        adapters.append({
            "name": name,
            "ipv4": ipv4,
            "speed_mbps": stat.speed or None,
        })

    io = psutil.net_io_counters()

    return {
        "hostname": socket.gethostname(),
        "adapters": adapters,
        "sent_gb": _gb(io.bytes_sent),
        "received_gb": _gb(io.bytes_recv),
    }


def check_internet_connection() -> dict:
    """Check whether the internet is reachable and measure latency."""

    for host in ("8.8.8.8", "1.1.1.1"):
        start = time.perf_counter()

        try:
            with socket.create_connection((host, 53), timeout=2):
                pass

            return {
                "online": True,
                "latency_ms": round((time.perf_counter() - start) * 1000),
                "checked_host": host,
            }

        except OSError:
            continue

    return {"online": False, "latency_ms": None}


def get_wifi_info() -> dict:
    """Get the connected Wi-Fi network name and signal (Windows)."""

    if os.name != "nt":
        return {"error": "Wi-Fi details are only available on Windows."}

    try:
        result = subprocess.run(
            ["netsh", "wlan", "show", "interfaces"],
            capture_output=True,
            text=True,
            timeout=5,
        )

    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return {"error": "Could not run netsh."}

    fields = {}

    for line in result.stdout.splitlines():
        if ":" not in line:
            continue

        key, _, value = line.partition(":")
        fields.setdefault(key.strip().lower(), value.strip())

    if not fields.get("state"):
        return {"connected": False, "reason": "No Wi-Fi adapter found."}

    return {
        "connected": fields.get("state", "").lower() == "connected",
        "ssid": fields.get("ssid"),
        "signal": fields.get("signal"),
        "radio_type": fields.get("radio type"),
        "receive_mbps": fields.get("receive rate (mbps)"),
        "transmit_mbps": fields.get("transmit rate (mbps)"),
    }


class _UnicodeString(ctypes.Structure):
    _fields_ = [
        ("Length", ctypes.c_ushort),
        ("MaximumLength", ctypes.c_ushort),
        ("Buffer", ctypes.c_void_p),
    ]


class _SystemProcessInfo(ctypes.Structure):
    """Start of SYSTEM_PROCESS_INFORMATION (only the fields FATMEE reads)."""

    _fields_ = [
        ("NextEntryOffset", ctypes.c_ulong),
        ("NumberOfThreads", ctypes.c_ulong),
        ("WorkingSetPrivateSize", ctypes.c_longlong),
        ("HardFaultCount", ctypes.c_ulong),
        ("NumberOfThreadsHighWatermark", ctypes.c_ulong),
        ("CycleTime", ctypes.c_ulonglong),
        ("CreateTime", ctypes.c_longlong),
        ("UserTime", ctypes.c_longlong),
        ("KernelTime", ctypes.c_longlong),
        ("ImageName", _UnicodeString),
        ("BasePriority", ctypes.c_long),
        ("UniqueProcessId", ctypes.c_void_p),
        ("InheritedFromUniqueProcessId", ctypes.c_void_p),
        ("HandleCount", ctypes.c_ulong),
        ("SessionId", ctypes.c_ulong),
        ("UniqueProcessKey", ctypes.c_void_p),
        ("PeakVirtualSize", ctypes.c_size_t),
        ("VirtualSize", ctypes.c_size_t),
        ("PageFaultCount", ctypes.c_ulong),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
    ]


def _windows_process_snapshot() -> dict:
    """
    Every process in one system call: {pid: (name, cpu_time, memory)}.

    psutil opens each process separately, which costs ~5 ms per
    process on this PC (about 2 s for 340 processes). This reads
    the same data in ~50 ms. cpu_time is in 100 ns units and
    memory is the working set in bytes, the same as psutil's rss.
    """

    ntdll = ctypes.WinDLL("ntdll")
    query = ntdll.NtQuerySystemInformation
    query.restype = ctypes.c_long

    size = 1 << 20
    needed = ctypes.c_ulong()

    while True:
        buf = ctypes.create_string_buffer(size)
        status = query(5, buf, size, ctypes.byref(needed)) & 0xFFFFFFFF

        if status == 0xC0000004:  # buffer too small
            size = max(size * 2, needed.value + 65536)
            continue

        if status != 0:
            raise OSError(f"NtQuerySystemInformation failed: {status:#x}")

        break

    procs = {}
    offset = 0

    while True:
        entry = _SystemProcessInfo.from_buffer(buf, offset)
        pid = entry.UniqueProcessId or 0

        name = (
            ctypes.wstring_at(entry.ImageName.Buffer, entry.ImageName.Length // 2)
            if entry.ImageName.Buffer else ""
        )

        procs[pid] = (name, entry.UserTime + entry.KernelTime, entry.WorkingSetSize)

        if not entry.NextEntryOffset:
            break

        offset += entry.NextEntryOffset

    return procs


def _top_processes_windows(sort_by: str, limit: int) -> list:

    interval = 0.5
    first = _windows_process_snapshot()

    if sort_by == "cpu":
        time.sleep(interval)
        second = _windows_process_snapshot()

    else:
        second = first

    cores = psutil.cpu_count() or 1
    rows = []

    for pid, (name, cpu_time, memory) in second.items():

        if pid == 0:  # System Idle Process
            continue

        row = {
            "pid": pid,
            "name": name,
            "memory_mb": round(memory / (1024 ** 2), 1),
        }

        if sort_by == "cpu":
            before = first.get(pid)
            used = cpu_time - before[1] if before else 0
            row["cpu_percent"] = round(used / (interval * 1e7) / cores * 100, 1)

        rows.append(row)

    key = "cpu_percent" if sort_by == "cpu" else "memory_mb"
    rows.sort(key=lambda r: r[key], reverse=True)

    return rows[:limit]


def get_top_processes(sort_by: str = "memory", limit: int = 10) -> list:
    """Get the processes using the most CPU or memory."""

    sort_by = "cpu" if str(sort_by).lower().startswith("c") else "memory"
    limit = max(1, min(int(limit), 50))

    if os.name == "nt":
        try:
            return _top_processes_windows(sort_by, limit)

        except (OSError, AttributeError, ValueError):
            pass  # Fall back to psutil below

    procs = list(psutil.process_iter(["pid", "name", "memory_info"]))

    if sort_by == "cpu":
        for p in procs:
            try:
                p.cpu_percent(interval=None)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        time.sleep(0.5)

    rows = []
    cores = psutil.cpu_count() or 1

    for p in procs:
        try:
            mem = p.info["memory_info"]
            row = {
                "pid": p.info["pid"],
                "name": p.info["name"],
                "memory_mb": round(mem.rss / (1024 ** 2), 1) if mem else 0,
            }

            if sort_by == "cpu":
                row["cpu_percent"] = round(p.cpu_percent(interval=None) / cores, 1)

            rows.append(row)

        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    key = "cpu_percent" if sort_by == "cpu" else "memory_mb"
    rows.sort(key=lambda r: r.get(key, 0), reverse=True)

    return rows[:limit]


def get_system_uptime() -> dict:
    """Get when the computer last started and how long it has run."""

    boot = psutil.boot_time()
    seconds = int(time.time() - boot)

    return {
        "boot_time": datetime.fromtimestamp(boot).strftime("%Y-%m-%d %H:%M:%S"),
        "uptime_hours": round(seconds / 3600, 1),
        "uptime_readable": f"{seconds // 86400}d {seconds % 86400 // 3600}h {seconds % 3600 // 60}m",
    }


_UNINSTALL_KEYS = [
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
    r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
]


# Words too common to count as a match on their own
# ("Microsoft Office" should not match every Microsoft program).
_GENERIC_WORDS = {
    "microsoft", "windows", "inc", "corporation", "corp", "the",
    "app", "apps", "software", "for", "and", "pro", "64", "x64", "bit",
}


def _read_installed_programs() -> dict:
    """{display name: version} for every installed program."""

    found = {}

    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for path in _UNINSTALL_KEYS:
            try:
                root = winreg.OpenKey(hive, path)

            except OSError:
                continue

            with root:
                for i in range(winreg.QueryInfoKey(root)[0]):
                    try:
                        with winreg.OpenKey(root, winreg.EnumKey(root, i)) as key:
                            name, _ = winreg.QueryValueEx(key, "DisplayName")

                            try:
                                version, _ = winreg.QueryValueEx(key, "DisplayVersion")
                            except OSError:
                                version = None

                    except OSError:
                        continue

                    found.setdefault(name, version)

    return found


def get_installed_programs(name_filter: str = "", limit: int = 50) -> dict:
    """
    List installed programs (Windows), optionally filtered by name.

    When nothing matches the full filter, programs sharing a
    meaningful word are returned as "similar" (e.g. "Microsoft
    Office" finds "WPS Office").
    """

    if not winreg:
        return {"error": "Installed programs are only available on Windows."}

    found = _read_installed_programs()
    needle = name_filter.lower().strip()
    limit = max(1, int(limit))

    def as_list(names):
        return [
            {"name": n, "version": found[n]}
            for n in sorted(names, key=str.lower)
        ]

    if not needle:
        programs = as_list(found)

        return {
            "total": len(programs),
            "shown": min(limit, len(programs)),
            "programs": programs[:limit],
        }

    exact = as_list(n for n in found if needle in n.lower())

    result = {
        "filter": name_filter,
        "total": len(exact),
        "shown": min(limit, len(exact)),
        "programs": exact[:limit],
    }

    if not exact:
        words = {
            w for w in needle.replace("-", " ").split()
            if len(w) >= 3 and w not in _GENERIC_WORDS
        }

        similar = as_list(
            n for n in found
            if any(w in n.lower() for w in words)
        )

        result["similar"] = similar[:limit]

    return result
