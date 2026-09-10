from tools.system_tools import (
    get_system_info,
    get_current_directory,
    list_files,
    get_cpu_usage,
    get_ram_usage,
    get_disk_usage,
    get_hardware_info
)

tool_registry = {
    "get_system_info": get_system_info,
    "get_current_directory": get_current_directory,
    "list_files": list_files,
    "get_cpu_usage": get_cpu_usage,
    "get_ram_usage": get_ram_usage,
    "get_disk_usage": get_disk_usage,
    "get_hardware_info": get_hardware_info
}