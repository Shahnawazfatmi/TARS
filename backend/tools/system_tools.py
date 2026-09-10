import os
import platform
import psutil
import subprocess
import json


def get_system_info() -> dict:
    """Get basic system information."""

    return {
        "os": platform.system(),
        "os_version": platform.version(),
        "machine": platform.machine(),
    }


def get_current_directory() -> str:
    """Get the current working directory."""

    return os.getcwd()


def list_files() -> list:
    """List files and folders in the current working directory."""

    return os.listdir(".")


def get_cpu_usage() -> dict:
    """Get current CPU usage."""

    return {
        "usage_percent": psutil.cpu_percent(interval=1)
    }


def get_ram_usage() -> dict:
    """Get current RAM usage."""

    memory = psutil.virtual_memory()

    return {
        "total_gb": round(memory.total / (1024 ** 3), 2),
        "used_gb": round(memory.used / (1024 ** 3), 2),
        "available_gb": round(memory.available / (1024 ** 3), 2),
        "usage_percent": memory.percent,
    }


def get_disk_usage() -> dict:
    """Get current disk usage."""

    disk = psutil.disk_usage("/")

    return {
        "total_gb": round(disk.total / (1024 ** 3), 2),
        "used_gb": round(disk.used / (1024 ** 3), 2),
        "free_gb": round(disk.free / (1024 ** 3), 2),
        "usage_percent": disk.percent,
    }


def get_hardware_info() -> dict:
    """Get detailed hardware information."""

    gpu = "Unknown"
    vram_gb = None

    # ========================================================
    # NVIDIA GPU
    # ========================================================

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

        if result.returncode == 0 and result.stdout.strip():

            gpu_name, vram_mb = result.stdout.strip().split(", ")

            gpu = gpu_name
            vram_gb = round(float(vram_mb) / 1024, 2)

    except (
        FileNotFoundError,
        subprocess.TimeoutExpired,
        ValueError
    ):
        pass

    # ========================================================
    # AMD / Intel / Windows GPU FALLBACK
    # ========================================================

    if gpu == "Unknown":

        try:

            result = subprocess.run(
                [
                    "powershell",
                    "-Command",
                    "Get-CimInstance Win32_VideoController | "
                    "Select-Object Name, AdapterRAM | "
                    "ConvertTo-Json"
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )

            if result.returncode == 0 and result.stdout.strip():

                data = json.loads(result.stdout)

                if isinstance(data, dict):
                    data = [data]

                if data:

                    gpu_info = data[0]

                    gpu = gpu_info.get(
                        "Name",
                        "Unknown"
                    )

                    adapter_ram = gpu_info.get(
                        "AdapterRAM"
                    )

                    if adapter_ram:

                        vram_gb = round(
                            adapter_ram / (1024 ** 3),
                            2
                        )

        except (
            FileNotFoundError,
            subprocess.TimeoutExpired,
            ValueError,
            json.JSONDecodeError
        ):
            pass

    # ========================================================
    # SYSTEM INFORMATION
    # ========================================================

    memory = psutil.virtual_memory()

    return {

        "cpu": platform.processor(),

        "architecture": platform.machine(),

        "physical_cores": psutil.cpu_count(
            logical=False
        ),

        "logical_cores": psutil.cpu_count(
            logical=True
        ),

        "ram_gb": round(
            memory.total / (1024 ** 3),
            2
        ),

        "gpu": gpu,

        "vram_gb": vram_gb,
    }