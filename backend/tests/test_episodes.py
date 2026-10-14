import json
import uuid
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import main
import memory
from memory import EpisodeStore, resolve_period
from tools.registry import run_tool


client = TestClient(main.app)

PLAN_RAM = json.dumps({"goal": "Check RAM", "steps": [
    {"id": "s1", "action": "ram", "tool": "get_ram_usage", "args": {}}
]})
GOAL_OK = '{"goal_satisfied": true, "reason": "ok", "missing_information": []}'

# A fixed "now": Tuesday 13 Oct 2026, 21:00
NOW = datetime(2026, 10, 13, 21, 0)


# ---------------- dates are worked out in code ----------------

@pytest.mark.parametrize("day,expected_day", [
    ("today", "2026-10-13"),
    ("Yesterday", "2026-10-12"),
    ("monday", "2026-10-12"),
    ("tuesday", "2026-10-13"),   # today is Tuesday
    ("wednesday", "2026-10-07"),  # most recent Wednesday
    ("2026-10-01", "2026-10-01"),
])
def test_resolve_day(day, expected_day):
    start, end, label = resolve_period(day, now=NOW)
    assert start.date().isoformat() == expected_day
    assert end - start == timedelta(days=1)


def test_resolve_days_back_and_none():
    start, end, label = resolve_period(days_back=7, now=NOW)
    assert start.date().isoformat() == "2026-10-06" and end > NOW
    assert resolve_period(now=NOW) == (None, None, "most recent")
    with pytest.raises(ValueError):
        resolve_period("someday", now=NOW)


# ---------------- the diary store ----------------

@pytest.fixture
def store(tmp_path):
    return EpisodeStore(str(tmp_path / "ep.db"))


def add(store, when, question, status="answered", session="s", **kw):
    store.add(task_id=uuid.uuid4().hex, session_id=session, started_at=when,
              question=question, status=status, **kw)


def test_search_by_day_keyword_status_session(store):
    add(store, NOW - timedelta(days=1, hours=2), "Is my battery charging?", tools=["get_battery_status"])
    add(store, NOW - timedelta(hours=1), "How much RAM do I have?", summary="14.8 GB")
    add(store, NOW - timedelta(minutes=5), "List Z:/nope", status="failed", session="other",
        problems=["list_files: Folder not found"])

    start, end, _ = resolve_period("yesterday", now=NOW)
    entries, total = store.search(start=start, end=end)
    assert total == 1 and entries[0]["tools"] == ["get_battery_status"]

    # Keyword search uses the full-text index, prefix match "batt" -> battery
    assert store.search(keyword="batt")[1] == 1
    assert store.search(keyword="14.8")[1] == 1          # found in the summary
    assert store.search(status="failed")[0][0]["problems"] == ["list_files: Folder not found"]
    assert store.search(session_id="other")[1] == 1

    # Newest first, limit with correct total
    entries, total = store.search(limit=2)
    assert total == 3 and [e["question"] for e in entries] == ["List Z:/nope", "How much RAM do I have?"]


def test_update_replaces_entry_and_summary_is_short(store):
    store.add(task_id="t1", session_id="s", started_at=NOW, question="Post it",
              status="waiting_approval", summary="x" * 1000)
    store.add(task_id="t1", session_id="s", started_at=NOW, question="Post it", status="answered")
    entries, total = store.search()
    assert total == 1 and entries[0]["status"] == "answered"
    # Search index updated too: no duplicate hit
    assert store.search(keyword="post")[1] == 1

    store.add(task_id="t2", session_id="s", started_at=NOW, question="q", status="answered", summary="y" * 1000)
    assert len(store.search(keyword="q")[0][0]["summary"]) <= memory.SUMMARY_CHARS + 3


def test_bad_inputs(store):
    with pytest.raises(ValueError):
        store.add(task_id="t", session_id="s", started_at=NOW, question="q", status="weird")
    # Symbols only: no crash, nothing found
    assert store.search(keyword='"*()') == ([], 0)


# ---------------- the recall tool ----------------

def test_recall_tool(monkeypatch, store):
    monkeypatch.setattr(memory, "_episode_store", store)
    now = datetime.now()
    add(store, now - timedelta(minutes=10), "Is my battery charging?", summary="Not charging, 57%")
    add(store, now - timedelta(days=1), "Check disk space")

    today = run_tool("recall_tasks", {"day": "today"})
    assert today["found"] == 1
    assert today["tasks"][0]["question"] == "Is my battery charging?"
    assert set(today["tasks"][0]) >= {"when", "question", "status", "tools", "summary"}

    assert run_tool("recall_tasks", {"day": "yesterday"})["found"] == 1
    assert run_tool("recall_tasks", {"days_back": 7, "keyword": "disk"})["found"] == 1
    assert "error" in run_tool("recall_tasks", {"day": "someday"})
    assert "error" in run_tool("recall_tasks", {"status": "weird"})


# ---------------- through the API ----------------

def latest_episode(question):
    entries, _ = memory.get_episode_store().search(keyword=question, limit=1)
    return entries[0]


def test_answered_task_is_saved(fake_llm):
    fake = fake_llm({"You are the planning component": PLAN_RAM, "Goal Verification": GOAL_OK})
    q = "ramcheck" + uuid.uuid4().hex[:6]
    body = client.post("/ask", json={"question": q}).json()

    episode = latest_episode(q)
    assert episode["task_id"] == body["task_id"]
    assert episode["status"] == "answered"
    assert episode["goal"] == "Check RAM"
    assert episode["tools"] == ["get_ram_usage"]
    assert episode["duration_s"] is not None
    # Planner is told today's date
    assert "TODAY: " + datetime.now().strftime("%A %Y-%m-%d") in fake.calls[0]["prompt"]


def test_partial_and_failed_tasks_are_saved(fake_llm):
    plan = json.dumps({"goal": "gpu temp", "steps": [{"id": "s1", "action": "hw", "tool": "get_hardware_info", "args": {}}]})
    fake_llm({
        "You are the planning component": plan,
        "Goal Verification": '{"goal_satisfied": false, "reason": "x", "missing_information": ["GPU temperature"]}',
        "recovery planning": '{"reason": "no tool", "steps": []}',
    })
    q = "gputemp" + uuid.uuid4().hex[:6]
    client.post("/ask", json={"question": q})
    episode = latest_episode(q)
    assert episode["status"] == "partial"
    assert "Could not get: GPU temperature" in episode["problems"]

    fake_llm({"You are the planning component": ConnectionError("offline")})
    q = "offline" + uuid.uuid4().hex[:6]
    assert client.post("/ask", json={"question": q}).status_code == 503
    episode = latest_episode(q)
    assert episode["status"] == "failed" and "offline" in episode["problems"][0]


def test_episodes_endpoint(fake_llm):
    fake_llm({"You are the planning component": '{"goal": "chat", "steps": []}'})
    q = "hello" + uuid.uuid4().hex[:6]
    client.post("/ask", json={"question": q})

    body = client.get("/episodes", params={"day": "today", "keyword": q}).json()
    assert body["found"] == 1 and body["episodes"][0]["question"] == q

    assert client.get("/episodes", params={"day": "someday"}).status_code == 422
    assert client.get("/episodes", params={"status": "weird"}).status_code == 422
