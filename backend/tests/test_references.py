import json

import pytest
from fastapi.testclient import TestClient

import main
from agents import thinker
from conftest import FakeProvider
from executor import execute_plan
from llm import LLM
from plan_check import check_plan
from planner import normalize_steps
from references import MissingInput, find_refs, resolve
from tools import registry


IDS = {"s1", "s2", "s3"}
RESULTS = {
    "s1": {"drive": "C:\\", "free_gb": 44.4},
    "s2": {"output": "D:\\"},            # a think step's answer
    "s3": {"error": "Folder not found"},
}


# ---------------- filling in references ----------------

def test_whole_reference_keeps_type():
    assert resolve("$s1.free_gb", RESULTS, IDS) == 44.4
    assert resolve("$s1", RESULTS, IDS) == {"drive": "C:\\", "free_gb": 44.4}
    # a think step's "$s2" means its output
    assert resolve("$s2", RESULTS, IDS) == "D:\\"


def test_reference_inside_text_and_nested():
    value = {"path": "$s2", "note": "Free: $s1.free_gb GB", "list": ["$s2"]}
    assert resolve(value, RESULTS, IDS) == {"path": "D:\\", "note": "Free: 44.4 GB", "list": ["D:\\"]}


def test_dollar_text_that_is_not_a_step_is_untouched():
    assert resolve("Costs $5 or $USD, see $s9", RESULTS, IDS) == "Costs $5 or $USD, see $s9"


@pytest.mark.parametrize("value,why", [
    ("$s3", "failed"),
    ("$s1.nothing", "no field"),
])
def test_bad_references_raise(value, why):
    with pytest.raises(MissingInput, match=why):
        resolve(value, RESULTS, IDS)


def test_find_refs():
    assert find_refs({"a": "$s1.x and $s2", "b": ["$s3"], "c": 5}) == {"s1", "s2", "s3"}


def test_reference_adds_missing_dependency():
    steps = normalize_steps([
        {"id": "s1", "tool": "get_all_disks"},
        {"id": "s2", "tool": "list_files", "args": {"path": "$s1.drive"}},
    ])
    fixed, warnings = check_plan(steps)
    assert fixed[1]["depends_on"] == ["s1"]
    assert "added it to depends_on" in warnings[0]


# ---------------- think step + references in the executor ----------------

@pytest.fixture
def fake_think(monkeypatch):
    fake = FakeProvider({"thinking step": "C:\\"})
    monkeypatch.setattr(thinker, "_llm", LLM([fake]))
    return fake


def test_think_then_use_its_answer(fake_think):
    results = execute_plan({"steps": [
        {"id": "s1", "tool": "get_all_disks"},
        {"id": "s2", "tool": "think", "depends_on": ["s1"],
         "args": {"instruction": "Pick the drive with most free space. Return only its path."}},
        {"id": "s3", "tool": "list_files", "args": {"path": "$s2", "limit": 3}},
    ]})

    # think saw the disk results
    prompt = fake_think.calls[0]["prompt"]
    assert '"s1"' in prompt and "free_gb" in prompt
    assert results["think"] == {"output": "C:\\"}

    # list_files ran on the drive the think step picked
    assert results["list_files"]["path"].upper().startswith("C:\\")


def test_step_is_skipped_when_its_input_failed():
    results = execute_plan({"steps": [
        {"id": "s1", "tool": "list_files", "args": {"path": "Z:/does/not/exist"}},
        {"id": "s2", "tool": "get_disk_usage", "args": {"drive": "$s1.path"}, "depends_on": ["s1"]},
    ]})
    assert "Skipped" in results["get_disk_usage"]["error"] and "failed" in results["get_disk_usage"]["error"]


def test_think_needs_instruction():
    assert "instruction" in registry.run_tool("think", {})["error"]


# ---------------- through the API ----------------

def test_api_plan_with_think_and_reference(fake_llm, monkeypatch):
    plan = json.dumps({"goal": "list biggest drive", "steps": [
        {"id": "s1", "step": 1, "action": "disks", "tool": "get_all_disks", "args": {}, "depends_on": []},
        {"id": "s2", "step": 2, "action": "pick", "tool": "think",
         "args": {"instruction": "Pick the drive with most free space. Return only its path."}, "depends_on": ["s1"]},
        {"id": "s3", "step": 3, "action": "list", "tool": "list_files", "args": {"path": "$s2", "limit": 3}, "depends_on": ["s2"]},
    ]})
    fake = fake_llm({
        "You are the planning component": plan,
        "thinking step": "C:\\",
        "Goal Verification": '{"goal_satisfied": true, "reason": "ok", "missing_information": []}',
    })
    # think uses the same fake model as everything else here
    monkeypatch.setattr(thinker, "_llm", main.llm)

    body = TestClient(main.app).post("/ask", json={"question": "List files on my biggest drive"}).json()
    assert body["tools_used"] == ["get_all_disks", "think", "list_files"]
    # planner was taught references and the think tool
    assert "$s1.field" in fake.calls[0]["prompt"] and '"think"' in fake.calls[0]["prompt"]
