"""Fixes from the 22 Oct review (Step 13.5), one test per problem."""

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import main
import task_logger
from executor import run_plan
from graph import merge_results
from mcp_client import MCPManager
from tools import registry


client = TestClient(main.app)

GOAL_OK = '{"goal_satisfied": true, "reason": "ok", "missing_information": []}'
GOAL_NO = '{"goal_satisfied": false, "reason": "post missing", "missing_information": ["the post"]}'


def plan(*steps):
    return {"steps": [
        {"id": sid, "step": n, "action": tool, "tool": tool, "args": args, "depends_on": deps}
        for n, (sid, tool, args, deps) in enumerate(steps, start=1)
    ]}


@pytest.fixture
def tools(monkeypatch):
    """summarize reads the steps it waits for; value returns its input."""

    monkeypatch.setattr(registry, "TOOLS", list(registry.TOOLS))
    monkeypatch.setattr(registry, "TOOLS_BY_NAME", dict(registry.TOOLS_BY_NAME))
    monkeypatch.setattr(registry, "tool_registry", dict(registry.tool_registry))

    def value(v: str = ""):
        return {"v": v}

    def summarize(instruction: str = "", inputs: dict | None = None):
        return {"summary_of": sorted(inputs or {})}

    registry.register_tool(registry.Tool("value", value, "Return v.", {"v": "text"}))
    registry.register_tool(registry.Tool("summarize", summarize, "Summarize inputs.", {"instruction": "text"},
                                         takes_inputs=True))


# ---------------- 1: same instruction over different steps ----------------

def test_same_instruction_over_different_inputs_runs_twice(tools):
    run = run_plan(plan(
        ("s1", "value", {"v": "ram"}, []),
        ("s2", "value", {"v": "cpu"}, []),
        ("s3", "summarize", {"instruction": "One line."}, ["s1"]),
        ("s4", "summarize", {"instruction": "One line."}, ["s2"]),
    ))

    assert run["by_id"]["s3"] == {"summary_of": ["s1"]}
    assert run["by_id"]["s4"] == {"summary_of": ["s2"]}      # was: s1's summary


def test_true_duplicates_still_run_once(tools):
    run = run_plan(plan(("s1", "value", {"v": "x"}, []), ("s2", "value", {"v": "x"}, [])))
    assert list(run["results"]) == ["value"] and run["by_id"]["s2"] == {"v": "x"}


# ---------------- 10: identical approval steps ----------------

def test_identical_approval_steps_wait_once(docs_dir):
    twice = plan(("s1", "practice_post", {"text": "Hi", "title": "t"}, []),
                 ("s2", "practice_post", {"text": "Hi", "title": "t"}, []))

    first = run_plan(twice)
    assert [p["step_id"] for p in first["pending"]] == ["s1"]

    done = run_plan(twice, done=first["by_id"], approvals={"s1": {"decision": "approve"}})
    assert done["by_id"]["s2"] == done["by_id"]["s1"] and "error" not in done["by_id"]["s1"]


# ---------------- 4: recovery uses the first plan's results ----------------

def test_recovery_can_use_first_plan_results(tools):
    known = {"s1": {"v": "C:/Users"}}
    run = run_plan(plan(
        ("r1", "value", {"v": "$s1.v"}, []),
        ("r2", "summarize", {"instruction": "Use it."}, ["s1"]),
    ), known=known)

    assert run["by_id"]["r1"] == {"v": "C:/Users"}          # was: the text "$s1.v"
    assert run["by_id"]["r2"] == {"summary_of": ["s1"]}       # was: no inputs


# ---------------- 2: a "no" is never redone ----------------

def test_rejected_tool_is_never_run_again(docs_dir):
    known = {"s1": {"error": "Not run: you rejected this step.", "rejected": True}}
    earlier = [{"id": "s1", "tool": "practice_post"}]

    run = run_plan(plan(("r1", "practice_post", {"text": "Again", "title": "t"}, [])),
                   earlier_steps=earlier, known=known, allow_approval=False)

    result = run["by_id"]["r1"]
    assert result["rejected"] and "you rejected practice_post earlier" in result["error"]
    assert not list(docs_dir.glob("*.txt"))


def test_reject_keeps_the_users_no_through_recovery(docs_dir, fake_llm):
    # Worst case: the AI goal check still says "not satisfied" and the
    # replanner proposes the same post again.
    fake = fake_llm({
        "You are the planning component": json.dumps({"goal": "post", "steps": [
            {"step": 1, "action": "post", "tool": "practice_post", "args": {"text": "Hi", "title": "t"}}]}),
        "Goal Verification": [GOAL_NO, GOAL_NO],
        "recovery planning": json.dumps({"reason": "post again", "steps": [
            {"step": 1, "action": "post", "tool": "practice_post", "args": {"text": "Hi", "title": "t"}}]}),
    })

    task_id = client.post("/ask", json={"question": "Post hi"}).json()["task_id"]
    done = client.post(f"/approvals/{task_id}", json={"decision": "reject"}).json()

    assert done["status"] == "done" and not list(docs_dir.glob("*.txt"))

    goal_prompt = next(c["prompt"] for c in fake.calls if "Goal Verification" in c["prompt"])
    assert "SETTLED STEPS" in goal_prompt and "practice_post: the user rejected" in goal_prompt

    answer_prompt = fake.calls[-1]["prompt"]
    # the original "you rejected" result is still there (not overwritten by the limit message)
    assert "Not run: you rejected this step." in answer_prompt
    assert "at most 1 time" not in answer_prompt


def test_settled_text_is_empty_without_settled_steps():
    from goal_verifier import settled_text
    assert settled_text({"get_ram_usage": {"total_gb": 16}}) == ""


# ---------------- 3: recovery keeps earlier results ----------------

def test_recovery_results_never_replace_earlier_ones():
    merged = merge_results(
        {"research_topic": {"topic": "A"}, "list_files": {"error": "Folder not found"}},
        {"research_topic": {"topic": "B"}, "list_files": {"files": []}},
    )
    assert merged == {
        "research_topic": {"topic": "A"}, "list_files": {"error": "Folder not found"},
        "research_topic#2": {"topic": "B"}, "list_files#2": {"files": []},
    }


# ---------------- 5: edits must name a real input ----------------

def test_misspelled_edit_is_refused_and_nothing_runs(docs_dir, fake_llm):
    fake_llm({
        "You are the planning component": json.dumps({"goal": "post", "steps": [
            {"step": 1, "action": "post", "tool": "practice_post", "args": {"text": "AI words", "title": "t"}}]}),
        "Goal Verification": GOAL_OK,
    })
    task_id = client.post("/ask", json={"question": "Post"}).json()["task_id"]

    wrong = client.post(f"/approvals/{task_id}", json={"decision": "approve", "changes": {"post_text": "Mine"}})
    assert wrong.status_code == 422 and "post_text" in wrong.json()["detail"] and "text" in wrong.json()["detail"]
    assert not list(docs_dir.glob("*.txt"))                    # nothing published

    client.post(f"/approvals/{task_id}", json={"decision": "approve", "changes": {"text": "Mine"}})
    assert list(docs_dir.glob("*.txt"))[0].read_text(encoding="utf-8").strip() == "Mine"


# ---------------- everyday decision words ----------------

@pytest.mark.parametrize("word,expected", [("OK", "approve"), (" yes ", "approve"), ("no", "reject"),
                                           ("Cancel", "reject"), ("approve", "approve")])
def test_everyday_decision_words(word, expected):
    assert main.Decision(decision=word).decision == expected


def test_unknown_decision_word_is_still_refused():
    with pytest.raises(ValueError):
        main.Decision(decision="maybe")


# ---------------- 11: task id reuse ----------------

def test_task_id_in_the_diary_cannot_be_reused(fake_llm):
    from datetime import datetime

    main.episodes.add(task_id="old-task-0001", session_id="default", started_at=datetime.now(),
                      question="old", status="answered")

    res = client.post("/ask", json={"question": "new", "task_id": "old-task-0001"})
    assert res.status_code == 409


# ---------------- 7: no blind retry of a post ----------------

def test_server_error_is_not_retried_blindly(monkeypatch, tmp_path):
    from connectors import linkedin as li

    calls = []

    class Response:
        status_code = 503
        headers = {}

        def json(self):
            return {"message": "busy"}

    class Http:
        def post(self, url, **kwargs):
            calls.append(url)
            return Response()

    monkeypatch.setenv("FATMEE_LINKEDIN_DRY_RUN", "false")
    monkeypatch.setenv("FATMEE_LINKEDIN_TOKEN_PATH", str(tmp_path / "token.bin"))
    li.save_token({"access_token": "T", "expires_at": 9999999999, "person_urn": "urn:li:person:1", "name": "Me"})

    with pytest.raises(li.LinkedInError, match="may or may not have been published"):
        li.create_post("Hello", http=Http())

    assert len(calls) == 1


# ---------------- 6: timeouts say what to do ----------------

def test_mcp_timeout_uses_the_server_setting_and_explains(monkeypatch):
    manager = MCPManager({"slow": {"timeout": 120}})
    manager.tools["slow_post"] = SimpleNamespace(args_schema={}, ainvoke=lambda args: None)
    manager.tool_server["slow_post"] = "slow"
    seen = {}

    def times_out(coroutine, timeout):
        seen["timeout"] = timeout
        raise TimeoutError()

    monkeypatch.setattr(manager, "_run", times_out)

    try:
        with pytest.raises(TimeoutError, match="did not answer within 120 s.*check before trying again"):
            manager.call("slow_post", {})
    finally:
        manager.close()

    assert seen["timeout"] == 120


# ---------------- 8: no AI after the action ----------------

def test_ai_outage_after_tools_still_reports_what_happened(fake_llm):
    fake_llm({
        "You are the planning component": json.dumps({"goal": "ram", "steps": [
            {"step": 1, "action": "ram", "tool": "get_ram_usage", "args": {}}]}),
        "Goal Verification": ConnectionError("AI down"),
        "Now provide the final answer": ConnectionError("AI down"),
    })

    res = client.post("/ask", json={"question": "How much RAM?"})

    assert res.status_code == 200                              # was: 503, results lost
    body = res.json()
    assert body["response"].startswith("FATMEE could not reach an AI model") and "get_ram_usage" in body["response"]
    assert body["tools_used"] == ["get_ram_usage"]


def test_replanner_outage_is_not_a_crash():
    from llm import LLMError
    from replanner import Replanner

    class Down:
        def json(self, *args, **kwargs):
            raise LLMError("all down")

    replan = Replanner(Down()).create_replan("g", {"steps": []}, {}, {}, {}, [])
    assert replan["steps"] == [] and replan["reason"].startswith("Replanning failed")


# ---------------- 13 / 14: task log ----------------

def test_skipped_step_without_goal_check_is_not_success(tmp_path):
    store = task_logger.TaskLogStore(str(tmp_path / "logs"), enabled=True)
    record = store.start("skip-task-01", "q")

    with task_logger.active(record):
        record.tool_call({"id": "s1", "tool": "value", "args": {}}, {"v": 1}, 0.1, "now")

    data = record.close({"tool_results": {
        "value": {"v": 1},
        "create_image": {"error": "Skipped: FATMEE runs create_image at most 1 time per question.",
                         "limit_reached": True},
    }}, {"response": "done"})

    assert data["goal_satisfied"] is None and data["task_status"] == "partial"
    assert "step_skipped" in data["failure_categories"]


def test_failed_call_counted_once_after_restart(tmp_path):
    folder = str(tmp_path / "logs")
    before = task_logger.TaskLogStore(folder, enabled=True)
    record = before.start("restart-task-1", "q")
    record.tool_call({"id": "s1", "tool": "list_files", "args": {}}, {"error": "Folder not found"}, 0.1, "now")
    before.paused("restart-task-1", [])

    after = task_logger.TaskLogStore(folder, enabled=True)      # FATMEE restarted
    resumed = after.resume("restart-task-1")
    data = resumed.close({"tool_results": {"list_files": {"error": "Folder not found"}}}, {"response": "r"})

    assert [f["category"] for f in data["failures"]] == ["tool_execution_error"]
