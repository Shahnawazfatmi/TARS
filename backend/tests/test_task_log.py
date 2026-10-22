import json
import logging
import re
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import analyze_logs
import config
import main
import task_logger
from conftest import FakeProvider
from graph import run
from tools import registry


client = TestClient(main.app)

GOAL_OK = '{"goal_satisfied": true, "reason": "ok", "missing_information": []}'
GOAL_NO = '{"goal_satisfied": false, "reason": "need more", "missing_information": ["more"]}'
NO_STEPS = '{"reason": "nothing else can help", "steps": []}'


def plan(*steps):
    return json.dumps({"goal": "test", "steps": [
        {"step": n, "action": tool, "tool": tool, "args": args}
        for n, (tool, args) in enumerate(steps, start=1)
    ]})


def replan(*steps):
    return json.dumps({"reason": "try again", "steps": [
        {"step": n, "action": tool, "tool": tool, "args": args}
        for n, (tool, args) in enumerate(steps, start=1)
    ]})


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A fresh task log in a temp folder."""

    log = task_logger.TaskLogStore(str(tmp_path / "logs"), enabled=True)
    monkeypatch.setattr(task_logger, "_store", log)
    return log


@pytest.fixture
def tools(monkeypatch):
    """Test tools: echo_value works, explode always fails."""

    monkeypatch.setattr(registry, "TOOLS", list(registry.TOOLS))
    monkeypatch.setattr(registry, "TOOLS_BY_NAME", dict(registry.TOOLS_BY_NAME))
    monkeypatch.setattr(registry, "tool_registry", dict(registry.tool_registry))

    def echo_value(value: str = "", extra: str = ""):
        time.sleep(0.02)
        return {"value": value, **({"extra": extra} if extra else {})}

    def explode(why: str = ""):
        raise RuntimeError(f"disk on fire {why}".strip())

    registry.register_tool(registry.Tool("echo_value", echo_value, "Echo.", {"value": "text", "extra": "text"}))
    registry.register_tool(registry.Tool("explode", explode, "Fails.", {"why": "text"}))


def records(log) -> list:
    try:
        with open(log.path, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]
    except FileNotFoundError:
        return []


def only_record(log) -> dict:
    found = records(log)
    assert len(found) == 1
    return found[0]


# ---------------- lifecycle ----------------

def test_successful_task_is_one_complete_record(store, tools, fake_llm):
    fake_llm({"You are the planning component": plan(("echo_value", {"value": "hi"})), "Goal Verification": GOAL_OK})

    body = client.post("/ask", json={"question": "Echo hi please"}).json()
    record = only_record(store)

    assert record["task_id"] == body["task_id"] and record["goal"] == "Echo hi please"
    assert record["task_status"] == "success" and record["execution_success"] is True
    assert record["goal_satisfied"] is True and record["failure_category"] is None
    assert record["plan"]["steps"][0]["tool"] == "echo_value"

    call = record["tool_calls"][0]
    assert call["ok"] and call["args"] == {"value": "hi"} and call["result"] == {"value": "hi"}
    assert call["duration_s"] >= 0.02 and call["stage"] == "execution"

    assert [c["component"] for c in record["llm_calls"]] == ["planner", "goal_verifier", "answer"]
    assert record["llm_calls"][0]["provider"] == "fake" and record["llm_calls"][0]["model"] == "fake-model"

    # The fake reports no tokens: null, never zero or a guess.
    assert record["usage"]["total_tokens"] is None and record["usage"]["estimated_cost_usd"] is None
    assert record["llm_calls"][0]["input_tokens"] is None

    t = record["timings"]
    assert t["active_s"] > 0 and t["wall_s"] >= t["active_s"] - 0.01
    assert t["execution_s"] >= 0.02 and t["planning_s"] >= 0 and t["answer_s"] >= 0
    assert record["counts"]["tool_calls"] == 1 and record["counts"]["llm_calls"] == 3
    assert record["response"] == body["response"]

    # nothing left open
    assert not list(Path(store.open_dir).glob("*.json"))


def test_tool_failure_is_categorized(store, tools, fake_llm):
    fake_llm({
        "You are the planning component": plan(("explode", {})),
        "recovery planning": NO_STEPS,
        "Goal Verification": GOAL_NO,
    })

    client.post("/ask", json={"question": "Break something"})
    record = only_record(store)

    call = record["tool_calls"][0]
    assert not call["ok"] and call["error"] == "disk on fire" and call["exception_type"] == "RuntimeError"

    assert record["failure_category"] == "tool_execution_error"
    assert {"tool_execution_error", "execution_verification_failed", "goal_verification_failed"} <= set(
        record["failure_categories"])
    assert record["replans"][0]["trigger"] == "execution_failed"
    assert record["replans"][0]["outcome"] == "no_tool_can_help"
    assert record["execution_success"] is False and record["goal_satisfied"] is False
    # tools were needed and none worked
    assert record["task_status"] == "failed"


def test_goal_failure_then_replanning_succeeds(store, tools, fake_llm):
    fake_llm({
        "You are the planning component": plan(("echo_value", {"value": "a"})),
        "Goal Verification": [GOAL_NO, GOAL_OK],
        "recovery planning": replan(("echo_value", {"value": "b"})),
    })

    client.post("/ask", json={"question": "Echo a and b"})
    record = only_record(store)

    assert [g["goal_satisfied"] for g in record["goal_verifications"]] == [False, True]
    assert [g["after"] for g in record["goal_verifications"]] == ["execution", "recovery"]

    attempt = record["replans"][0]
    assert attempt["trigger"] == "goal_not_satisfied" and attempt["outcome"] == "steps_succeeded"
    assert attempt["previous_failure"]["missing_information"] == ["more"]
    assert attempt["steps"][0]["args"] == {"value": "b"} and attempt["replanner_s"] >= 0

    assert [c["stage"] for c in record["tool_calls"]] == ["execution", "recovery"]
    assert record["task_status"] == "success" and record["goal_satisfied"] is True
    assert "goal_verification_failed" in record["failure_categories"]
    assert record["counts"]["recovery_attempts"] == 1


def test_multiple_recovery_attempts(store, tools, fake_llm):
    fake_llm({
        "You are the planning component": plan(("explode", {})),
        "recovery planning": [replan(("explode", {"why": "again"})), replan(("echo_value", {"value": "x"}))],
        "Goal Verification": [GOAL_NO, GOAL_NO],
    })

    client.post("/ask", json={"question": "Keep trying"})
    record = only_record(store)

    assert [r["trigger"] for r in record["replans"]] == ["execution_failed", "goal_not_satisfied"]
    assert [r["outcome"] for r in record["replans"]] == ["steps_failed", "steps_succeeded"]
    assert record["counts"]["recovery_attempts"] == 2
    assert record["counts"]["tool_calls"] == 3 and record["counts"]["failed_tool_calls"] == 2
    assert "max_recovery_attempts" in record["failure_categories"]
    # one tool call worked, the goal still isn't met
    assert record["task_status"] == "partial"


def test_planner_outage_is_a_failed_task(store, fake_llm):
    fake_llm({"You are the planning component": ConnectionError("offline")})

    assert client.post("/ask", json={"question": "RAM?"}).status_code == 503
    record = only_record(store)

    assert record["task_status"] == "failed" and record["failure_category"] == "llm_error"
    assert record["exception"]["type"] == "LLMError" and record["exception"]["stage"] == "planning"
    assert record["llm_calls"][0]["ok"] is False and "offline" in record["llm_calls"][0]["error"]


def test_invalid_plan_json_is_a_planning_error(store, fake_llm):
    fake_llm({"You are the planning component": "sorry, no plan"})

    client.post("/ask", json={"question": "RAM?"})
    assert only_record(store)["failure_category"] == "planning_error"


def test_fallback_provider_and_real_token_counts(store, tools, monkeypatch):
    class Broken(FakeProvider):
        def generate(self, prompt, system, json_mode):
            raise ConnectionError("down")

    class Counting(FakeProvider):
        def generate(self, prompt, system, json_mode):
            return super().generate(prompt, system, json_mode), {
                "input_tokens": 100, "output_tokens": 20, "total_tokens": 120}

    replies = {"You are the planning component": plan(("echo_value", {"value": "1"})), "Goal Verification": GOAL_OK}
    monkeypatch.setattr(main.llm, "providers", [Broken({}, name="first"), Counting(replies)])
    monkeypatch.setattr(config, "LLM_PRICES", {"fake-model": {"input": 1.0, "output": 2.0}})

    client.post("/ask", json={"question": "Count tokens"})
    record = only_record(store)

    assert record["counts"]["llm_failed_attempts"] == 3          # each call tried "first" once
    assert [c["ok"] for c in record["llm_calls"][:2]] == [False, True]
    assert record["usage"]["input_tokens"] == 300 and record["usage"]["total_tokens"] == 360
    # 3 priced calls of (100*1 + 20*2) / 1M; the failed attempts have no usage and no cost
    assert record["llm_calls"][1]["estimated_cost_usd"] == pytest.approx(0.00014)
    assert record["usage"]["estimated_cost_usd"] is None             # not every call is priced
    assert record["task_status"] == "success"


def test_approval_pause_and_resume_make_one_record(store, docs_dir, fake_llm):
    fake_llm({
        "You are the planning component": plan(("practice_post", {"text": "Hello world", "title": "t"})),
        "Goal Verification": GOAL_OK,
    })

    task_id = client.post("/ask", json={"question": "Post hello"}).json()["task_id"]

    # paused: nothing finished yet, an open record says it waits
    assert records(store) == []
    with open(store._open_path(task_id), encoding="utf-8") as f:
        assert json.load(f)["state"] == "waiting_approval"

    client.post(f"/approvals/{task_id}", json={"decision": "approve"})
    record = only_record(store)

    assert record["approvals"]["requested"][0]["tool"] == "practice_post"
    assert record["approvals"]["decisions"] == [{"step_id": "s1", "decision": "approve", "edited": False}]
    assert record["segments"] == 2 and record["timings"]["approval_wait_s"] >= 0
    assert record["tool_calls"][0]["tool"] == "practice_post" and record["tool_calls"][0]["ok"]
    assert record["task_status"] == "success"


# ---------------- concurrency ----------------

def test_concurrent_tasks_get_separate_records(store, tools, monkeypatch):
    class Echo(FakeProvider):
        def generate(self, prompt, system, json_mode):
            if "You are the planning component" in prompt:
                number = re.search(r"USER GOAL:\s*echo (\d+)", prompt).group(1)
                return plan(("echo_value", {"value": number}))
            if "Goal Verification" in prompt:
                return GOAL_OK
            return "done"

    monkeypatch.setattr(main.llm, "providers", [Echo({})])

    errors = []

    def ask(number):
        try:
            run(main.fatmee, f"echo {number}", task_id=f"concurrent-{number:03d}")
        except Exception as e:  # surfaced below
            errors.append(e)

    threads = [threading.Thread(target=ask, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    found = records(store)
    assert sorted(r["task_id"] for r in found) == [f"concurrent-{n:03d}" for n in range(8)]

    for record in found:
        number = record["goal"].split()[-1]
        # each record holds only its own tool call and its own 3 AI calls
        assert [c["args"]["value"] for c in record["tool_calls"]] == [number]
        assert record["counts"]["llm_calls"] == 3


# ---------------- safety ----------------

def test_secrets_are_redacted_and_long_values_cut(monkeypatch):
    monkeypatch.setenv("FATMEE_TEST_SECRET", "supersecretvalue123")
    task_logger._secret_values.cache_clear()

    try:
        clean = task_logger.sanitize({
            "api_key": "abc",
            "headers": {"Authorization": "Bearer abcdefghijkl", "Accept": "json"},
            "access_token": "AQV-token",
            "input_tokens": 12,
            "note": "used sk-abcdefghijklmnopqrstuv and AIzaSyA1234567890123456789012345678901 "
                    "at https://x.test/cb?code=AQXYZ123&state=ok with supersecretvalue123",
            "text": "Bearer zyxwvutsrqpo1234",
            "long": "y" * 5000,
            "many": list(range(80)),
        }, limit=100)

    finally:
        task_logger._secret_values.cache_clear()

    assert clean["api_key"] == clean["headers"]["Authorization"] == clean["access_token"] == "[REDACTED]"
    assert clean["headers"]["Accept"] == "json" and clean["input_tokens"] == 12

    text = json.dumps(clean)
    for secret in ("sk-abcdef", "AIzaSyA", "AQXYZ123", "supersecretvalue123", "zyxwvutsrqpo"):
        assert secret not in text
    assert "code=[REDACTED]" in clean["note"] and "state=ok" in clean["note"]

    assert clean["long"].endswith("… [truncated: 5000 chars]") and len(clean["long"]) < 140
    assert len(clean["many"]) == 51 and clean["many"][-1] == "[truncated: 80 items]"


def test_tool_output_is_sanitized_in_the_record(store, tools, fake_llm, monkeypatch):
    monkeypatch.setattr(config, "TASK_LOG_MAX_CHARS", 200)
    big = "Bearer abcdefghijkl123 " + "z" * 5000

    fake_llm({"You are the planning component": plan(("echo_value", {"value": "v", "extra": big})),
              "Goal Verification": GOAL_OK})

    client.post("/ask", json={"question": "Echo a lot"})
    record = only_record(store)

    result = record["tool_calls"][0]["result"]
    assert "abcdefghijkl123" not in json.dumps(record)
    assert result["extra"].startswith("Bearer [REDACTED]") and "[truncated:" in result["extra"]
    assert record["truncated"] is True


def test_logging_failure_does_not_break_the_task(store, tools, fake_llm, monkeypatch, caplog):
    def broken_write(line):
        raise OSError("disk full")

    def broken_args(args):
        raise ValueError("bad args")

    monkeypatch.setattr(store, "_append", broken_write)
    monkeypatch.setattr(task_logger, "_shown_args", broken_args)

    fake_llm({"You are the planning component": plan(("echo_value", {"value": "ok"})), "Goal Verification": GOAL_OK})

    with caplog.at_level(logging.ERROR, logger="fatmee.task_log"):
        response = client.post("/ask", json={"question": "Still answer me"})

    assert response.status_code == 200 and response.json()["tools_used"] == ["echo_value"]
    # the problem is visible, not silent
    assert store.errors >= 2 and "TASK LOG ERROR" in caplog.text


def test_crash_leaves_an_incomplete_record(tmp_path):
    folder = str(tmp_path / "logs")
    before = task_logger.TaskLogStore(folder, enabled=True)

    running = before.start("crashed-task-1", "Do a long thing", "default")
    with task_logger.active(running):
        try:
            with running.phase("execution"):
                raise KeyboardInterrupt  # stands in for the process dying mid-step
        except KeyboardInterrupt:
            pass

    waiting = before.start("waiting-task-1", "Post later", "default")
    before.paused("waiting-task-1", [{"step_id": "s1", "tool": "practice_post", "args": {}}])
    assert waiting is not None

    # FATMEE starts again
    after = task_logger.TaskLogStore(folder, enabled=True)
    assert after.recover_incomplete() == 1

    record = only_record(after)
    assert record["task_id"] == "crashed-task-1" and record["task_status"] == "incomplete"
    assert record["failures"][-1]["stage"] == "execution"
    # a task waiting for approval can still be resumed: left open
    assert after._read_open("waiting-task-1")["state"] == "waiting_approval"


def test_logging_can_be_switched_off(tmp_path, monkeypatch, tools, fake_llm):
    off = task_logger.TaskLogStore(str(tmp_path / "logs"), enabled=False)
    monkeypatch.setattr(task_logger, "_store", off)
    fake_llm({"You are the planning component": plan(("echo_value", {"value": "1"})), "Goal Verification": GOAL_OK})

    assert client.post("/ask", json={"question": "Echo 1"}).status_code == 200
    assert records(off) == []


# ---------------- analysis ----------------

def test_analysis_survives_bad_lines_and_shows_no_content(tmp_path, capsys):
    path = tmp_path / "tasks.jsonl"
    good = {
        "task_id": "a", "goal": "my private question", "task_status": "success", "goal_satisfied": True,
        "execution_success": True, "failure_categories": [],
        "tool_calls": [{"tool": "echo_value", "ok": True, "args": {"value": "private"}}],
        "timings": {"active_s": 2.0, "wall_s": 3.0}, "counts": {"tool_calls": 1, "recovery_attempts": 0,
                                                                "llm_calls": 3},
        "usage": {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110, "estimated_cost_usd": 0.01},
    }
    partial = {
        "task_id": "b", "task_status": "partial", "goal_satisfied": False, "execution_success": False,
        "failure_categories": ["tool_execution_error", "goal_verification_failed"],
        "tool_calls": [{"tool": "explode", "ok": False}], "timings": {"active_s": 4.0},
        "counts": {"tool_calls": 1, "recovery_attempts": 2}, "usage": {"total_tokens": None},
    }
    path.write_text(
        json.dumps(good) + "\n{not json\n" + json.dumps([1, 2]) + "\n\n"
        + json.dumps(partial) + "\n" + json.dumps({"task_id": "c"}) + "\n",
        encoding="utf-8",
    )

    summary = analyze_logs.summarize(*analyze_logs.load(str(path)))

    assert summary["log_file_tasks"] == 3 and summary["malformed_lines"] == 2
    assert summary["task_status"]["success"] == 1 and summary["task_status"]["unknown"] == 1
    assert summary["goal_satisfaction_rate"] == 0.5 and summary["tasks_with_goal_check"] == 2
    assert summary["active_latency_s"] == {"average": 3.0, "median": 3.0}
    assert summary["top_failing_tools"] == [("explode", 1)]
    assert dict(summary["top_failure_categories"])["tool_execution_error"] == 1
    assert summary["tokens"]["total_tokens"] == {"total": 110, "tasks_reporting": 1}
    assert summary["average_recovery_attempts"] == 1.0

    analyze_logs.main([str(path)])
    printed = capsys.readouterr().out
    assert "Tasks: 3" in printed and "private" not in printed


def test_analysis_of_empty_or_missing_file(tmp_path):
    (tmp_path / "empty.jsonl").write_text("", encoding="utf-8")

    for name in ("empty.jsonl", "missing.jsonl"):
        summary = analyze_logs.summarize(*analyze_logs.load(str(tmp_path / name)))
        assert summary["log_file_tasks"] == 0 and summary["goal_satisfaction_rate"] is None
        assert summary["active_latency_s"]["average"] is None
