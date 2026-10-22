import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main
from agents import thinker
from executor import execute_plan, run_plan
from graph import build_graph, waiting_task
from progress import ProgressLog
from tools import registry


POST_TEXT = "Most AI models talk. Jev decides."

PLAN = json.dumps({"goal": "practice post", "steps": [
    {"id": "s1", "step": 1, "action": "check RAM", "tool": "get_ram_usage", "args": {}, "depends_on": []},
    {"id": "s2", "step": 2, "action": "write the post", "tool": "think",
     "args": {"instruction": "Write a short post. Return only the post."}, "depends_on": ["s1"]},
    {"id": "s3", "step": 3, "action": "publish the practice post", "tool": "practice_post",
     "args": {"text": "$s2", "title": "Jev post"}, "depends_on": ["s2"]},
]})

GOAL_OK = '{"goal_satisfied": true, "reason": "ok", "missing_information": []}'


@pytest.fixture
def flow(docs_dir, fake_llm, monkeypatch):
    """A fake planner + think step, and a client for the real API."""

    fake = fake_llm({
        "You are the planning component": PLAN,
        "thinking step": POST_TEXT,
        "Goal Verification": GOAL_OK,
    })
    monkeypatch.setattr(thinker, "_llm", main.llm)
    return fake, TestClient(main.app), docs_dir


def _calls(fake, marker):
    return sum(marker in call["prompt"] for call in fake.calls)


# ---------------- executor ----------------

def test_step_waits_and_the_steps_that_need_it_wait_too(docs_dir):
    run = run_plan({"steps": [
        {"id": "s1", "tool": "practice_post", "args": {"text": "hi"}},
        {"id": "s2", "tool": "get_ram_usage", "depends_on": ["s1"]},
        {"id": "s3", "tool": "get_cpu_usage"},
    ]})

    assert [p["step_id"] for p in run["pending"]] == ["s1"]
    assert run["pending"][0]["args"] == {"text": "hi"}
    # s2 waits for s1; s3 needs nothing, so it already ran
    assert "get_ram_usage" not in run["results"] and "get_cpu_usage" in run["results"]
    assert list(docs_dir.iterdir()) == []      # nothing was saved yet


def test_finished_steps_are_reused_after_the_decision(docs_dir, monkeypatch):
    calls = []
    real = registry.TOOLS_BY_NAME["get_cpu_usage"]
    monkeypatch.setitem(registry.TOOLS_BY_NAME, "get_cpu_usage",
                        registry.Tool("get_cpu_usage", lambda: calls.append(1) or {"cpu": 5}, real.description))

    plan = {"steps": [
        {"id": "s1", "tool": "get_cpu_usage"},
        {"id": "s2", "tool": "practice_post", "args": {"text": "cpu $s1.cpu"}, "depends_on": ["s1"]},
    ]}
    first = run_plan(plan)
    assert first["pending"][0]["args"]["text"] == "cpu 5"   # the preview shows the real text

    second = run_plan(plan, done=first["by_id"], approvals={"s2": {"decision": "approve"}})
    assert second["results"]["practice_post"]["saved"] and second["pending"] == []
    assert len(calls) == 1                                   # s1 never ran twice


def test_reject_and_edit(docs_dir):
    plan = {"steps": [
        {"id": "s1", "tool": "practice_post", "args": {"text": "old"}},
        {"id": "s2", "tool": "get_ram_usage", "args": {}, "depends_on": ["s1"]},
    ]}

    rejected = execute_plan(plan, approvals={"s1": {"decision": "reject"}})
    assert rejected["practice_post"]["rejected"] and "rejected" in rejected["practice_post"]["error"]
    # a step built on a rejected step is never run
    assert "needs s1, which you rejected" in rejected["get_ram_usage"]["error"]

    edited = execute_plan(plan, approvals={"s1": {"decision": "approve", "changes": {"text": "new text"}}})
    assert Path(edited["practice_post"]["path"]).read_text(encoding="utf-8").strip() == "new text"


def test_recovery_plans_never_pause(docs_dir):
    results = execute_plan({"steps": [{"id": "r1", "tool": "practice_post", "args": {"text": "x"}}]}, allow_approval=False)
    assert "only in the first plan" in results["practice_post"]["error"]


def test_practice_post_needs_text_and_approval():
    assert registry.requires_approval("practice_post")
    assert registry.max_per_task("practice_post") == 1
    assert "needs its text" in registry.run_tool("practice_post", {})["error"]


# ---------------- through the API ----------------

def test_ask_pauses_then_approve_finishes(flow):
    fake, client, folder = flow

    body = client.post("/ask", json={"question": "Write a practice post", "session_id": "appr"}).json()

    assert body["status"] == "waiting_approval"
    task_id = body["task_id"]
    waiting = body["approvals"][0]
    assert waiting["step_id"] == "s3" and waiting["tool"] == "practice_post"
    # the preview shows the post the think step wrote, filled in by code
    assert waiting["args"]["text"] == POST_TEXT
    assert "I need your OK" in body["response"] and f"/approvals/{task_id}" in body["response"]
    assert not list(folder.glob("*.txt"))                    # not saved before the OK
    assert body["tools_used"] == ["get_ram_usage", "think"]

    listed = client.get("/approvals").json()
    assert task_id in [t["task_id"] for t in listed["tasks"]]
    assert client.get(f"/approvals/{task_id}").json()["question"] == "Write a practice post"

    done = client.post(f"/approvals/{task_id}", json={"decision": "approve"}).json()

    assert done["status"] == "done" and "practice_post" in done["tools_used"]
    saved = list(folder.glob("*.txt"))
    assert len(saved) == 1 and saved[0].read_text(encoding="utf-8").strip() == POST_TEXT
    # the answer is given the exact saved text and told not to rewrite it
    final = [c for c in fake.calls if c["system"] and "quote its text exactly" in c["system"]][-1]
    assert POST_TEXT in final["prompt"]
    # planning and the think step ran once: nothing ran twice
    assert _calls(fake, "You are the planning component") == 1
    assert _calls(fake, "thinking step") == 1

    # the task is finished: no longer waiting
    assert client.get(f"/approvals/{task_id}").status_code == 404
    assert client.post(f"/approvals/{task_id}", json={"decision": "approve"}).status_code == 404

    # conversation and diary
    messages = client.get("/sessions/appr").json()["messages"]
    assert [m["content"] for m in messages if m["role"] == "user"] == ["Write a practice post", "Approved."]
    entry = client.get("/episodes", params={"session_id": "appr"}).json()["episodes"][0]
    assert entry["task_id"] == task_id and entry["status"] == "answered"


def test_reject_does_not_save_and_does_not_replan(flow):
    fake, client, folder = flow

    task_id = client.post("/ask", json={"question": "Write a practice post"}).json()["task_id"]
    done = client.post(f"/approvals/{task_id}", json={"decision": "reject"}).json()

    assert done["status"] == "done"
    assert not list(folder.glob("*.txt"))
    # "no" is a decision, not a failure: no recovery planning
    assert _calls(fake, "recovery planning") == 0


def test_approve_with_edits(flow):
    _, client, folder = flow

    task_id = client.post("/ask", json={"question": "Write a practice post"}).json()["task_id"]
    client.post(f"/approvals/{task_id}", json={"decision": "approve", "step_id": "s3", "changes": {"text": "My own words."}})

    assert list(folder.glob("*.txt"))[0].read_text(encoding="utf-8").strip() == "My own words."


def test_bad_decisions(flow):
    _, client, _ = flow

    assert client.post("/approvals/nosuchtask1", json={"decision": "approve"}).status_code == 404
    assert client.post("/approvals/bad id!", json={"decision": "approve"}).status_code in (404, 422)

    task_id = client.post("/ask", json={"question": "Write a practice post"}).json()["task_id"]
    assert client.post(f"/approvals/{task_id}", json={"decision": "approve", "step_id": "s9"}).status_code == 422
    assert client.post(f"/approvals/{task_id}", json={"decision": "maybe"}).status_code == 422
    # still waiting after the bad requests
    assert client.get(f"/approvals/{task_id}").status_code == 200


def test_decide_without_task_id_when_one_waits(flow):
    _, client, folder = flow

    # earlier tests share the test database and leave tasks waiting
    for task in client.get("/approvals", params={"limit": 100}).json()["tasks"]:
        client.post(f"/approvals/{task['task_id']}", json={"decision": "reject"})

    assert client.post("/approvals", json={"decision": "approve"}).status_code == 404

    first = client.post("/ask", json={"question": "Write a practice post"}).json()["task_id"]
    second = client.post("/ask", json={"question": "Write a practice post"}).json()["task_id"]

    # two waiting: FATMEE won't guess, it lists them
    several = client.post("/approvals", json={"decision": "approve"})
    assert several.status_code == 409
    assert {t["task_id"] for t in several.json()["detail"]["waiting"]} == {first, second}
    assert not list(folder.glob("*.txt"))

    client.post(f"/approvals/{first}", json={"decision": "reject"})

    done = client.post("/approvals", json={"decision": "approve"}).json()
    assert done["status"] == "done" and list(folder.glob("*.txt"))
    assert client.get(f"/approvals/{second}").status_code == 404


def test_waiting_task_survives_a_restart(flow):
    _, client, folder = flow

    task_id = client.post("/ask", json={"question": "Write a practice post"}).json()["task_id"]

    # A new graph with a new connection to the same SQLite file, like
    # FATMEE after a restart.
    restarted = build_graph(main.llm)
    assert waiting_task(restarted, task_id)["pending"][0]["step_id"] == "s3"

    from graph import resume
    reply, _ = resume(restarted, task_id, {"s3": {"decision": "approve"}})
    assert reply["status"] == "done" and list(folder.glob("*.txt"))


# ---------------- live progress ----------------

def test_progress_events_tell_the_story(flow):
    _, client, _ = flow

    body = client.post("/ask", json={"question": "Write a practice post", "task_id": "mytask-001"}).json()
    assert body["task_id"] == "mytask-001"

    kinds = [e["kind"] for e in client.get("/tasks/mytask-001/events").json()["events"]]
    assert kinds[0] == "planning" and "planned" in kinds and "wave" in kinds and "step" in kinds
    assert kinds[-2:] == ["approval_needed", "waiting_approval"]

    last = client.get("/tasks/mytask-001/events").json()["events"][-1]["n"]
    client.post("/approvals/mytask-001", json={"decision": "approve"})

    newer = [e["kind"] for e in client.get("/tasks/mytask-001/events", params={"after": last}).json()["events"]]
    assert newer[0] == "approved" and newer[-1] == "done" and "answering" in newer

    # the same task id can't be used twice
    assert client.post("/ask", json={"question": "again", "task_id": "mytask-001"}).status_code == 409
    assert client.get("/tasks/unknown-task/events").status_code == 404


def test_progress_stream_sends_events_until_the_end(flow):
    _, client, _ = flow
    client.post("/ask", json={"question": "Write a practice post", "task_id": "streamtask-1"})

    with client.stream("GET", "/tasks/streamtask-1/stream") as response:
        text = "".join(response.iter_text())

    assert "event: planned" in text and text.rstrip().endswith("}")
    assert "event: waiting_approval" in text


def test_progress_log_keeps_only_recent_tasks():
    log = ProgressLog(max_tasks=2, max_events=3)
    for task in ("a", "b", "c"):
        for i in range(5):
            log.emit(task, "step", f"{task}{i}")

    assert not log.known("a") and log.known("c")
    assert [e["n"] for e in log.events("c")] == [3, 4, 5]
    assert [e["n"] for e in log.events("c", after=4)] == [5]
