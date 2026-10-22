import inspect
import json
from dataclasses import dataclass, field
from typing import Callable

import config
from agents import document, image, research, thinker, writer
from tools import memory_tools as mt
from tools import practice_tools as pt
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

    # Most times this tool may run for one question (0 = no limit),
    # e.g. paid images, or one LinkedIn post. Counted in code.
    max_per_task: int = 0

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
    Tool(
        "create_image", image.create_image,
        "Image agent: makes ONE picture sized for LinkedIn and saves it as "
        "PNG. Can show the results of the steps in its depends_on (e.g. "
        "research). Returns where the file was saved. Costs more than "
        "other tools: use only when the user asks for an image or for a "
        "LinkedIn post, and make only ONE create_image step per question.",
        {
            "description": "The user's picture idea in detail, if they gave "
                           "one, e.g. 'a robot reading a newspaper, watercolor'",
            "instruction": "What the picture is for, e.g. 'an image for a "
                           "LinkedIn post about this research'. Keep the user's "
                           "own words; FATMEE picks the scene itself",
            "shape": "'square' (normal post, default), 'landscape' (link or "
                     "article post) or 'portrait' (tall post)",
            "style": "Only if the user names one: 'photo', 'illustration', '3d', "
                     "'isometric', 'graphic' or their own words (e.g. 'watercolor'). "
                     "Leave out otherwise",
            "text_in_image": "Exact words to show in the picture, only if the "
                             "user asks for text. Leave out otherwise",
            "file_name": "File name. Leave out unless the user names one",
            "folder": "Only if the user names a place: 'desktop', 'pictures', "
                      "'documents' or a folder in their home. Leave out "
                      "otherwise (saves to Desktop/FATMEE Images)",
            "open_after": "true only if the user asks to open the picture. "
                          "Leave out otherwise",
        },
        takes_inputs=True,
        max_per_task=config.MAX_IMAGES_PER_TASK,
    ),
    Tool(
        "write_post", writer.write_post,
        "Post writer agent: writes ONE LinkedIn post (plain text, a strong "
        "first line, short paragraphs, 3-5 hashtags, up to two source links) "
        "from the results of the steps in its depends_on. Returns the post "
        "as \"text\". It does not publish anything.",
        {
            "instruction": "What the post should say or do, e.g. 'share the "
                           "most surprising finding with my network'",
            "include_sources": "false to leave out source links. Default true",
        },
        takes_inputs=True,
    ),
    Tool(
        "practice_post", pt.practice_post,
        "PRACTICE ONLY: rehearses publishing a post. FATMEE pauses and asks "
        "the user to approve; then it saves the post text (and image path) "
        "in a text file on the Desktop. Nothing is published. Use only when "
        "the user asks for a practice post or to test approval.",
        {
            "text": "The full post text, e.g. '$s2' from a think step that wrote it",
            "title": "Short name for the file. Default: the first words",
            "image_path": "Path of an image for the post, e.g. '$s3.path'. Optional",
        },
        needs_approval=True,
        max_per_task=1,
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


def max_per_task(name: str | None) -> int:
    """Most times a tool may run for one question (0 = no limit)."""

    tool = TOOLS_BY_NAME.get(name or "")

    return tool.max_per_task if tool else 0


def tool_parameters(name: str | None) -> list:
    """Names of a tool's inputs (empty for an unknown tool)."""

    tool = TOOLS_BY_NAME.get(name or "")

    return list(tool.parameters) if tool else []


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
            text = manager.call(_name, kwargs)

            # A server that answers with a JSON object gets a dict back,
            # so later steps can use its fields ("$s4.post_url").
            try:
                value = json.loads(text)
            except (TypeError, ValueError):
                return text

            return value if isinstance(value, dict) else text

        limits = {normalize_name(k): int(v) for k, v in (spec.get("max_per_task") or {}).items()}

        register_tool(Tool(
            name,
            call,
            (tool.description or name).strip().split("\n")[0][:300],
            parameters,
            needs_approval=any(normalize_name(p) in name for p in spec.get("approval", [])),
            max_per_task=next((v for k, v in limits.items() if k in name), 0),
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
