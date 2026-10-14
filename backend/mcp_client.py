"""
Connects FATMEE to MCP servers (search, later LinkedIn, Gmail, GitHub...).

Servers are listed in mcp_servers.json. At startup each one is
connected and its tools are loaded. MCP is async, while FATMEE's tools
run in normal threads, so all MCP work happens on one background event
loop and tools are called through `call()`.
"""

import asyncio
import json
import logging
import os
import re
import threading

from langchain_mcp_adapters.client import MultiServerMCPClient

import config


logger = logging.getLogger("fatmee.mcp")

# Settings in mcp_servers.json that are FATMEE's, not the MCP library's.
_FATMEE_KEYS = {"description", "required_env", "expose", "approval", "enabled"}

_ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")


def normalize_name(name: str) -> str:
    """'tavily-search' and 'tavily_search' are the same tool."""

    return name.replace("-", "_").lower()


def _fill_env(value):
    """Replace ${NAME} with the value from the environment (.env)."""

    if isinstance(value, str):
        return _ENV_PATTERN.sub(lambda m: os.getenv(m.group(1), ""), value)

    if isinstance(value, dict):
        return {k: _fill_env(v) for k, v in value.items()}

    if isinstance(value, list):
        return [_fill_env(v) for v in value]

    return value


def result_text(result) -> str:
    """Turn an MCP tool result (text blocks, dict or str) into text."""

    if isinstance(result, str):
        return result

    if isinstance(result, list):
        parts = []

        for block in result:
            if isinstance(block, dict) and "text" in block:
                parts.append(block["text"])
            else:
                parts.append(str(block))

        return "\n".join(parts)

    if isinstance(result, tuple):  # (content, artifact)
        return result_text(result[0])

    return json.dumps(result, default=str) if isinstance(result, dict) else str(result)


class MCPManager:
    """Owns the MCP connections and the event loop they run on."""

    def __init__(self, servers: dict):
        self.servers = servers
        self.tools = {}       # normalized name -> LangChain tool
        self.tool_server = {} # normalized name -> server name
        self.status = {}      # server name -> {"state": ..., ...}

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever,
            name="fatmee-mcp",
            daemon=True,
        )
        self._thread.start()

    @classmethod
    def from_file(cls, path: str | None = None) -> "MCPManager":

        path = path or config.MCP_CONFIG

        if not os.path.exists(path):
            logger.info("No MCP config at %s; MCP is off.", path)
            return cls({})

        with open(path, encoding="utf-8") as f:
            raw = json.load(f)

        servers = {name: spec for name, spec in raw.items() if not name.startswith("_")}

        return cls(servers)

    def _run(self, coroutine, timeout: float):
        future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)

        try:
            return future.result(timeout=timeout)

        except TimeoutError:
            future.cancel()
            raise

    def connect(self) -> None:
        """Connect to every usable server. Never stops FATMEE from starting."""

        for name, spec in self.servers.items():

            if spec.get("enabled") is False:
                self.status[name] = {"state": "disabled"}
                continue

            missing = [key for key in spec.get("required_env", []) if not os.getenv(key)]

            if missing:
                self.status[name] = {
                    "state": "skipped",
                    "reason": f"Add {', '.join(missing)} to backend/.env and restart FATMEE.",
                }
                logger.info("MCP server %s skipped: %s not set", name, ", ".join(missing))
                continue

            connection = _fill_env({k: v for k, v in spec.items() if k not in _FATMEE_KEYS})

            try:
                client = MultiServerMCPClient({name: connection})
                tools = self._run(client.get_tools(server_name=name), timeout=config.MCP_TIMEOUT)

            except Exception as e:
                # Log only the error type: the full message can include
                # the request URL or headers, i.e. the API key.
                self.status[name] = {"state": "error", "reason": f"{type(e).__name__}: could not connect"}
                logger.warning("MCP server %s could not connect: %s", name, type(e).__name__)
                continue

            for tool in tools:
                key = normalize_name(tool.name)
                self.tools[key] = tool
                self.tool_server[key] = name

            self.status[name] = {
                "state": "connected",
                "tools": sorted(normalize_name(t.name) for t in tools),
            }

            logger.info("MCP server %s connected: %s", name, self.status[name]["tools"])

    def find(self, word: str) -> str | None:
        """
        The connected tool best matching `word`.

        An exact name or "<server>_<word>" wins over a partial match,
        so "search" finds tavily_search, not tavily_research.
        """

        word = normalize_name(word)
        names = sorted(self.tools)

        for name in names:
            if name == word or name.endswith("_" + word):
                return name

        for name in names:
            if word in name:
                return name

        return None

    def schema(self, name: str) -> dict:
        """The tool's input schema (JSON schema)."""

        tool = self.tools[normalize_name(name)]
        schema = tool.args_schema

        if not isinstance(schema, dict):
            schema = schema.model_json_schema()

        return schema or {}

    def call(self, name: str, args: dict, timeout: float | None = None) -> str:
        """Run an MCP tool from normal (non-async) code and return its text."""

        tool = self.tools.get(normalize_name(name))

        if tool is None:
            raise KeyError(f"MCP tool not connected: {name}")

        properties = self.schema(name).get("properties", {})

        # Drop arguments the tool does not declare (a model typo or an
        # option this server version does not support).
        clean = {k: v for k, v in args.items() if not properties or k in properties}

        result = self._run(tool.ainvoke(clean), timeout=timeout or config.MCP_TIMEOUT)

        return result_text(result)

    def close(self) -> None:
        self._loop.call_soon_threadsafe(self._loop.stop)


# ============================================================
# SHARED INSTANCE
# ============================================================

_manager = None


def get_manager() -> MCPManager | None:
    return _manager


def set_manager(manager: MCPManager | None) -> None:
    global _manager
    _manager = manager
