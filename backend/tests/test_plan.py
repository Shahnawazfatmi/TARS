import threading
import time

import pytest

from executor import PLAN_ERROR_KEY, execute_plan
from plan_check import PlanError, check_plan, execution_waves
from planner import normalize_steps
from tools import registry
from verifier import Verifier


def test_normalize_gives_ids_deps_and_approval_from_code():
    steps = normalize_steps([
        {"tool": "get_ram_usage", "needs_approval": True},
        {"id": "s2", "tool": "get_cpu_usage", "depends_on": "s1"},
    ])
    assert [s["id"] for s in steps] == ["s1", "s2"]
    assert steps[1]["depends_on"] == ["s1"]
    # The model said True, but get_ram_usage is read-only: code decides
    assert steps[0]["needs_approval"] is False


def test_check_plan_repairs_small_mistakes():
    steps = normalize_steps([
        {"step": 1, "tool": "get_ram_usage"},
        {"step": 2, "tool": "get_cpu_usage", "depends_on": [1, "s2", "ghost"]},
    ])
    fixed, warnings = check_plan(steps)
    # step number 1 -> s1, self-dependency and unknown id dropped
    assert fixed[1]["depends_on"] == ["s1"]
    assert len(warnings) == 2


def test_loop_is_rejected():
    steps = normalize_steps([
        {"id": "a", "tool": "get_ram_usage", "depends_on": ["b"]},
        {"id": "b", "tool": "get_cpu_usage", "depends_on": ["a"]},
    ])
    with pytest.raises(PlanError):
        check_plan(steps)

    results = execute_plan({"steps": steps})
    assert "loop" in results[PLAN_ERROR_KEY]["error"]
    assert not Verifier().verify({"steps": steps}, results)["verified"]


def test_waves_follow_dependencies():
    steps = normalize_steps([
        {"id": "research1", "tool": "x"},
        {"id": "research2", "tool": "x"},
        {"id": "shortlist", "tool": "x", "depends_on": ["research1", "research2"]},
        {"id": "image", "tool": "x", "depends_on": ["shortlist"]},
        {"id": "caption", "tool": "x", "depends_on": ["shortlist"]},
        {"id": "post", "tool": "x", "depends_on": ["image", "caption"]},
    ])
    waves = [[s["id"] for s in wave] for wave in execution_waves(steps)]
    assert waves == [["research1", "research2"], ["shortlist"], ["image", "caption"], ["post"]]


@pytest.fixture
def timed_tools(monkeypatch):
    """Two fake tools that record when they start and end."""

    log = []
    lock = threading.Lock()

    def make(name):
        def tool(label: str = ""):
            start = time.perf_counter()
            time.sleep(0.3)
            with lock:
                log.append((label or name, start, time.perf_counter()))
            return {"ok": label or name}
        return tool

    fakes = {
        "slow_a": registry.Tool("slow_a", make("slow_a"), "test"),
        "slow_b": registry.Tool("slow_b", make("slow_b"), "test"),
        "send_mail": registry.Tool("send_mail", make("send_mail"), "test", needs_approval=True),
    }
    monkeypatch.setattr(registry, "TOOLS_BY_NAME", {**registry.TOOLS_BY_NAME, **fakes})
    return log


def test_independent_steps_run_together(timed_tools):
    start = time.perf_counter()
    execute_plan({"steps": [
        {"id": "s1", "tool": "slow_a"},
        {"id": "s2", "tool": "slow_b"},
    ]})
    # Two 0.3 s steps in parallel take well under 0.6 s
    assert time.perf_counter() - start < 0.55


def test_dependent_step_waits(timed_tools):
    execute_plan({"steps": [
        {"id": "s2", "tool": "slow_b", "depends_on": ["s1"]},
        {"id": "s1", "tool": "slow_a"},
    ]})
    times = {name: (start, end) for name, start, end in timed_tools}
    # s2 is listed first but starts only after s1 has finished
    assert times["slow_b"][0] >= times["slow_a"][1]


def test_approval_tool_never_runs_without_approval(timed_tools):
    results = execute_plan({"steps": [{"id": "s1", "tool": "send_mail"}]})
    assert results["send_mail"]["needs_approval"] is True
    assert timed_tools == []  # the tool body never ran
