import inspect
from dataclasses import dataclass, field
from typing import Callable

from agents import document, research, thinker
from tools import memory_tools as mt
from tools import system_tools as st


@dataclass(frozen=True)
class Tool:
    """One tool: the function, what it does, and its inputs."""

    name: str
    func: Callable
    description: str
    parameters: dict = field(default_factory=dict)

    # True for tools that act on the outside world (post, send,
    # delete). Set here in code so the AI cannot switch it off.
    needs_approval: bool = False

    # True for tools that read the results of the steps they wait
    # for (think, create_document). The executor passes them in.
    takes_inputs: bool = False

    def spec(self) -> dict:
        """Description shown to the planner."""

        spec = {"name": self.name, "description": self.description}

        if self.parameters:
            spec["parameters"] = self.parameters

        if self.needs_approval:
            spec["needs_approval"] = True

        return spec


TOOLS = [
    Tool(
        "get_system_info", st.get_system_info,
        "Operating system name, release, version, Windows edition, "
        "architecture and computer name.",
    ),
    Tool(
        "get_current_directory", st.get_current_directory,
        "The current working directory of FATMEE.",
    ),
    Tool(
        "list_files", st.list_files,
        "List files and folders inside a folder, with sizes.",
        {
            "path": "Folder to list, e.g. '.', '~/Desktop', 'C:/Users'. Default '.'",
            "limit": "Maximum entries to return. Default 100",
        },
    ),
    Tool(
        "get_cpu_usage", st.get_cpu_usage,
        "Current CPU usage percentage, per-core usage and clock speed.",
    ),
    Tool(
        "get_ram_usage", st.get_ram_usage,
        "Current RAM usage: total, used, available, percent, and swap.",
    ),
    Tool(
        "get_disk_usage", st.get_disk_usage,
        "Total, used and free space on one drive.",
        {"drive": "Drive letter such as 'C' or 'D'. Default: system drive"},
    ),
    Tool(
        "get_all_disks", st.get_all_disks,
        "Space used and free on every drive.",
    ),
    Tool(
        "get_hardware_info", st.get_hardware_info,
        "Hardware: CPU model, physical and logical cores, total RAM, "
        "all GPUs with VRAM, and architecture.",
    ),
    Tool(
        "get_battery_status", st.get_battery_status,
        "Battery percentage, whether it is charging, and time left.",
    ),
    Tool(
        "get_network_info", st.get_network_info,
        "Active network adapters, IP addresses, link speed and data used.",
    ),
    Tool(
        "check_internet_connection", st.check_internet_connection,
        "Whether the internet is reachable, and the latency in ms.",
    ),
    Tool(
        "get_wifi_info", st.get_wifi_info,
        "Connected Wi-Fi network name (SSID), signal strength and speed.",
    ),
    Tool(
        "get_top_processes", st.get_top_processes,
        "Programs using the most memory or CPU right now.",
        {
            "sort_by": "'memory' or 'cpu'. Default 'memory'",
            "limit": "How many processes to return. Default 10",
        },
    ),
    Tool(
        "get_system_uptime", st.get_system_uptime,
        "When the computer was last started and how long it has been on.",
    ),
    Tool(
        "get_installed_programs", st.get_installed_programs,
        "Installed programs and versions, optionally filtered by name.",
        {
            "name_filter": "Part of a program name, e.g. 'python'. Default: all",
            "limit": "Maximum programs to return. Default 50",
        },
    ),
    Tool(
        "recall_tasks", mt.recall_tasks,
        "FATMEE's diary of past tasks: what the user asked before, when, "
        "which tools ran, and whether it worked. Use for questions about "
        "the past ('what did I ask yesterday?', 'did I check my battery?').",
        {
            "day": "'today', 'yesterday', a weekday like 'monday', or a date "
                   "'YYYY-MM-DD'. Leave empty for the most recent tasks",
            "days_back": "Instead of day: tasks from the last N days, e.g. 7",
            "keyword": "Word to search for in past tasks, e.g. 'battery'",
            "status": "'answered', 'partial', 'failed' or 'waiting_approval'",
            "limit": "Maximum tasks to return. Default 20",
        },
    ),
    Tool(
        "think", thinker.think,
        "Reasoning step: works on the results of the steps in its "
        "depends_on, e.g. compare, pick the best, shortlist, summarize "
        "or write a draft. Its answer can be used later as $<id>.",
        {
            "instruction": "What to do with the earlier results, e.g. "
                           "'Pick the drive with the most free space. "
                           "Return only its path.'",
        },
        takes_inputs=True,
    ),
    Tool(
        "research_topic", research.research_topic,
        "Research agent: searches the web for current information on ONE "
        "topic and returns key points with numbered source links. For "
        "several topics, use one step per topic (they run at the same time).",
        {
            "topic": "What to research, e.g. 'AI agent trends October 2026'",
            "max_sources": "How many web results to use (1-10). Default 5",
            "time_range": "Only recent results: 'day', 'week', 'month' or 'year'",
        },
    ),
    Tool(
        "create_document", document.create_document,
        "Document agent: writes a Word (.docx) report from the results of "
        "the steps in its depends_on (research, think, system info...) and "
        "saves it. Research sources become a clickable Sources list. "
        "Returns where the file was saved.",
        {
            "instruction": "What the document should be, e.g. 'A one-page "
                           "report on AI agent trends for my team'",
            "title": "Document title. Default: chosen from the content",
            "content": "Text to include as-is, e.g. an earlier answer from "
                       "the conversation. Default: none",
            "file_name": "File name. Leave out unless the user names one",
            "folder": "Only if the user names a place: 'desktop', 'documents', "
                      "'downloads' or a folder in their home. Leave out "
                      "otherwise (saves to Desktop/FATMEE Reports)",
            "open_after": "true only if the user asks to open the file. "
                          "Leave out otherwise",
        },
        takes_inputs=True,
    ),
]


TOOLS_BY_NAME = {tool.name: tool for tool in TOOLS}

# Kept for existing code that expects name -> function.
tool_registry = {tool.name: tool.func for tool in TOOLS}


def requires_approval(name: str | None) -> bool:
    """Whether a tool must wait for the user's OK before running."""

    tool = TOOLS_BY_NAME.get(name or "")

    return bool(tool and tool.needs_approval)


def takes_inputs(name: str | None) -> bool:
    """Whether a tool reads the results of the steps it waits for."""

    tool = TOOLS_BY_NAME.get(name or "")

    return bool(tool and tool.takes_inputs)


def tool_specs() -> list:
    """All tool descriptions, for the planner and replanner."""

    return [tool.spec() for tool in TOOLS]


def register_tool(tool: Tool) -> None:
    """Add (or replace) a tool at startup, e.g. one from an MCP server."""

    for i, existing in enumerate(TOOLS):
        if existing.name == tool.name:
            TOOLS[i] = tool
            break
    else:
        TOOLS.append(tool)

    TOOLS_BY_NAME[tool.name] = tool
    tool_registry[tool.name] = tool.func


def register_mcp_tools(manager, servers: dict) -> list:
    """
    Make the MCP tools listed under "expose" callable by the planner.

    Approval comes from the "approval" list in mcp_servers.json, in
    code, so an MCP server cannot switch it off. Returns the names added.
    """

    from mcp_client import normalize_name

    added = []

    for name in sorted(manager.tools):
        spec = servers.get(manager.tool_server[name], {})

        if not any(normalize_name(p) in name for p in spec.get("expose", [])):
            continue

        tool = manager.tools[name]
        properties = manager.schema(name).get("properties", {})

        parameters = {
            arg: (info.get("description") or info.get("title") or info.get("type", ""))
            for arg, info in properties.items()
        }

        def call(_name=name, **kwargs):
            return manager.call(_name, kwargs)

        register_tool(Tool(
            name,
            call,
            (tool.description or name).strip().split("\n")[0][:300],
            parameters,
            needs_approval=any(normalize_name(p) in name for p in spec.get("approval", [])),
        ))

        added.append(name)

    return added


def run_tool(name: str, args: dict | None = None):
    """
    Run one tool.

    Unknown arguments are dropped so a model typo can't crash
    the tool.
    """

    tool = TOOLS_BY_NAME.get(name)

    if tool is None:
        return {"error": f"Unknown tool: {name}"}

    args = args or {}

    params = inspect.signature(tool.func).parameters

    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        # MCP tools take **kwargs and check arguments against their own schema.
        clean_args = {k: v for k, v in args.items() if not k.startswith("_")}

    else:
        clean_args = {k: v for k, v in args.items() if k in params}

    return tool.func(**clean_args)
