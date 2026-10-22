import json
import os
import sys
from pathlib import Path

import pytest

import mcp_client
from agents import research
from conftest import FakeProvider
from executor import execute_plan
from llm import LLM
from mcp_client import MCPManager, normalize_name, result_text
from tools import registry


FAKE_SERVER = str(Path(__file__).parent / "fixtures" / "fake_mcp_server.py")

BRIEF = '{"summary": "AI agents are growing fast.", "key_points": ["Point one [1]", "Point two [2]"]}'


@pytest.fixture(scope="module")
def manager():
    """One real MCP connection to the fake search server (stdio, offline)."""

    m = MCPManager({
        "fake": {
            "transport": "stdio",
            "command": sys.executable,
            "args": [FAKE_SERVER],
            "expose": ["extract"],
            "approval": [],
        }
    })
    m.connect()
    yield m
    m.close()


@pytest.fixture
def with_manager(manager, monkeypatch):
    monkeypatch.setattr(mcp_client, "_manager", manager)
    return manager


@pytest.fixture
def fake_research_llm(monkeypatch):
    fake = FakeProvider({"research agent": BRIEF})
    monkeypatch.setattr(research, "_llm", LLM([fake]))
    return fake


# ---------------- connection ----------------

def test_connects_and_lists_tools(manager):
    assert manager.status["fake"]["state"] == "connected"
    assert manager.find("search") == "fake_search"
    assert manager.find("nothing") is None


def test_call_returns_text_and_drops_unknown_args(manager):
    text = manager.call("fake-search", {"query": "llm", "max_results": 2, "bogus": 1})
    assert "Title: llm result 2" in text and "result 3" not in text


def test_missing_key_skips_server_without_crashing(monkeypatch):
    monkeypatch.delenv("NO_SUCH_KEY_FOR_TEST", raising=False)
    m = MCPManager({"web": {"transport": "streamable_http", "url": "https://x",
                            "required_env": ["NO_SUCH_KEY_FOR_TEST"]}})
    m.connect()
    assert m.status["web"]["state"] == "skipped"
    assert "NO_SUCH_KEY_FOR_TEST" in m.status["web"]["reason"]
    m.close()


def test_broken_server_reports_error_without_crashing():
    m = MCPManager({"broken": {"transport": "stdio", "command": sys.executable,
                               "args": ["-c", "import sys; sys.exit(1)"]}})
    m.connect()
    assert m.status["broken"]["state"] == "error"
    m.close()


def test_env_values_are_filled(monkeypatch):
    monkeypatch.setenv("FAKE_KEY_X", "abc")
    assert mcp_client._fill_env({"h": ["Bearer ${FAKE_KEY_X}"]}) == {"h": ["Bearer abc"]}


def test_helpers():
    assert normalize_name("Tavily-Search") == "tavily_search"
    assert result_text([{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]) == "a\nb"


# ---------------- registry ----------------

def test_only_exposed_tools_are_registered(manager, monkeypatch):
    monkeypatch.setattr(registry, "TOOLS", list(registry.TOOLS))
    monkeypatch.setattr(registry, "TOOLS_BY_NAME", dict(registry.TOOLS_BY_NAME))
    monkeypatch.setattr(registry, "tool_registry", dict(registry.tool_registry))

    added = registry.register_mcp_tools(manager, manager.servers)
    assert added == ["fake_extract"]
    assert "fake_search" not in registry.TOOLS_BY_NAME
    assert "Page https://a.com: hello" in registry.run_tool("fake_extract", {"urls": ["https://a.com"]})


def test_approval_list_blocks_mcp_tool(manager, monkeypatch):
    monkeypatch.setattr(registry, "TOOLS", list(registry.TOOLS))
    monkeypatch.setattr(registry, "TOOLS_BY_NAME", dict(registry.TOOLS_BY_NAME))
    monkeypatch.setattr(registry, "tool_registry", dict(registry.tool_registry))

    servers = {"fake": {**manager.servers["fake"], "approval": ["extract"]}}
    registry.register_mcp_tools(manager, servers)
    assert registry.requires_approval("fake_extract")

    from executor import run_plan
    run = run_plan({"steps": [{"id": "s1", "tool": "fake_extract", "args": {"urls": ["https://a.com"]}}]})
    # the MCP tool waits for the user's OK instead of running
    assert run["pending"][0]["tool"] == "fake_extract" and run["results"] == {}


# ---------------- research agent ----------------

def test_research_topic_returns_sourced_brief(with_manager, fake_research_llm):
    result = registry.run_tool("research_topic", {"topic": "AI agents", "max_sources": 3})
    assert result["summary"] == "AI agents are growing fast."
    assert result["key_points"] == ["Point one [1]", "Point two [2]"]
    assert [s["url"] for s in result["sources"]] == [f"https://example.com/{i}" for i in (1, 2, 3)]
    assert result["sources"][0] == {"n": 1, "title": "AI agents result 1", "url": "https://example.com/1"}
    # The model only sees numbered sources and the raw results
    prompt = fake_research_llm.calls[0]["prompt"]
    assert "[3] AI agents result 3 - https://example.com/3" in prompt


def test_research_runs_in_parallel_for_several_topics(with_manager, fake_research_llm):
    results = execute_plan({"steps": [
        {"id": "s1", "tool": "research_topic", "args": {"topic": "topic A"}},
        {"id": "s2", "tool": "research_topic", "args": {"topic": "topic B"}},
    ]})
    assert results["research_topic"]["topic"] == "topic A"
    assert results["research_topic#2"]["topic"] == "topic B"


def test_research_without_search_says_how_to_fix(monkeypatch, fake_research_llm):
    monkeypatch.setattr(mcp_client, "_manager", None)
    result = registry.run_tool("research_topic", {"topic": "x"})
    assert "TAVILY_API_KEY" in result["error"]


def test_research_needs_a_topic():
    assert registry.run_tool("research_topic", {})["error"] == "No topic given."


def test_extract_sources_handles_layouts():
    text = "Title: A\nURL: https://a.com/x.\nsee https://b.com/y and https://a.com/x"
    assert research.extract_sources(text) == [
        {"title": "A", "url": "https://a.com/x"},
        {"title": "https://b.com/y", "url": "https://b.com/y"},
    ]


def test_planner_sees_research_tool():
    names = {t["name"] for t in registry.tool_specs()}
    assert "research_topic" in names


def test_find_prefers_exact_tool_name():
    m = MCPManager({})
    m.tools = {"tavily_research": object(), "tavily_search": object(), "tavily_extract": object()}
    assert m.find("search") == "tavily_search"      # not tavily_research
    assert m.find("extract") == "tavily_extract"
    m.close()


def test_parse_results_reads_tavily_json():
    raw = json.dumps({"query": "x", "results": [
        {"url": "https://a.com", "title": "A title", "content": "Alpha facts"},
        {"url": "https://b.com", "title": "", "content": "Beta"},
        {"title": "no url, skipped"},
        {"url": "https://c.com", "title": "C"},
    ]})
    results = research.parse_results(raw, max_sources=2)
    assert results == [
        {"title": "A title", "url": "https://a.com", "content": "Alpha facts"},
        {"title": "https://b.com", "url": "https://b.com", "content": "Beta"},
    ]


def test_parse_results_falls_back_to_text():
    results = research.parse_results("Title: A\nURL: https://a.com", max_sources=5)
    assert results == [{"title": "A", "url": "https://a.com", "content": ""}]


def test_answer_writer_is_told_to_keep_sources():
    from graph import SYSTEM_INSTRUCTION
    assert "Sources" in SYSTEM_INSTRUCTION and "[1]" in SYSTEM_INSTRUCTION
