import uuid

from fastapi.testclient import TestClient

import main
from memory import ConversationStore, format_history


client = TestClient(main.app)

PLAN_RAM = '{"goal": "ram", "steps": [{"id": "s1", "action": "ram", "tool": "get_ram_usage", "args": {}}]}'
GOAL_OK = '{"goal_satisfied": true, "reason": "ok", "missing_information": []}'


def new_session() -> str:
    return "t" + uuid.uuid4().hex[:12]


# ---------------- store ----------------

def test_store_saves_reads_and_survives_restart(tmp_path):
    path = str(tmp_path / "mem.db")
    store = ConversationStore(path)
    store.add_exchange("s", "How much RAM?", "14.8 GB", task_id="t1", meta={"tools_used": ["get_ram_usage"]})
    store.add_exchange("s", "And free?", "4.9 GB free")

    # A new store on the same file = server restart
    again = ConversationStore(path)
    recent = again.recent("s", limit=10)
    assert [m["content"] for m in recent] == ["How much RAM?", "14.8 GB", "And free?", "4.9 GB free"]
    assert again.messages("s")[1]["meta"] == {"tools_used": ["get_ram_usage"]}


def test_recent_keeps_only_the_latest(tmp_path):
    store = ConversationStore(str(tmp_path / "mem.db"))
    for i in range(6):
        store.add_exchange("s", f"q{i}", f"a{i}")
    assert [m["content"] for m in store.recent("s", limit=4)] == ["q4", "a4", "q5", "a5"]


def test_sessions_are_separate_and_clear_works(tmp_path):
    store = ConversationStore(str(tmp_path / "mem.db"))
    store.add_exchange("a", "qa", "aa")
    store.add_exchange("b", "qb", "ab")
    assert store.clear("a") == 2
    assert store.recent("a") == []
    assert len(store.recent("b")) == 2


def test_format_history():
    assert format_history([]) == ""
    text = format_history([
        {"role": "user", "content": "How much RAM?"},
        {"role": "assistant", "content": "x" * 50},
    ], max_chars=20)
    assert "User: How much RAM?" in text
    assert "FATMEE: " + "x" * 20 + " ...(shortened)" in text


# ---------------- API ----------------

def test_follow_up_question_sees_earlier_conversation(fake_llm):
    session = new_session()
    fake = fake_llm({"You are the planning component": PLAN_RAM, "Goal Verification": GOAL_OK})

    first = client.post("/ask", json={"question": "How much RAM do I have?", "session_id": session}).json()
    assert first["session_id"] == session

    calls_before = len(fake.calls)
    client.post("/ask", json={"question": "And how much is free?", "session_id": session})

    second_plan_prompt = fake.calls[calls_before]["prompt"]
    assert "User: How much RAM do I have?" in second_plan_prompt
    assert "FATMEE: Final answer from fake model." in second_plan_prompt


def test_other_sessions_are_not_mixed_in(fake_llm):
    one, two = new_session(), new_session()
    fake = fake_llm({"You are the planning component": PLAN_RAM, "Goal Verification": GOAL_OK})

    client.post("/ask", json={"question": "secret question one", "session_id": one})
    calls_before = len(fake.calls)
    client.post("/ask", json={"question": "hello", "session_id": two})

    assert "secret question one" not in fake.calls[calls_before]["prompt"]


def test_session_endpoints(fake_llm):
    session = new_session()
    fake_llm({"You are the planning component": '{"goal": "chat", "steps": []}'})
    client.post("/ask", json={"question": "Hi FATMEE", "session_id": session})

    body = client.get(f"/sessions/{session}").json()
    assert body["count"] == 2
    assert body["messages"][0] == {**body["messages"][0], "role": "user", "content": "Hi FATMEE"}

    assert client.delete(f"/sessions/{session}").json()["deleted_messages"] == 2
    assert client.get(f"/sessions/{session}").json()["count"] == 0


def test_failed_request_is_not_remembered(fake_llm):
    session = new_session()
    fake_llm({"You are the planning component": ConnectionError("offline")})
    assert client.post("/ask", json={"question": "RAM?", "session_id": session}).status_code == 503
    assert client.get(f"/sessions/{session}").json()["count"] == 0


def test_bad_session_id_rejected():
    assert client.post("/ask", json={"question": "hi", "session_id": "../etc"}).status_code == 422
    assert client.get("/sessions/bad id!").status_code in (404, 422)
