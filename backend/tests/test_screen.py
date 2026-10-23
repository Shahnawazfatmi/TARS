import json
import uuid

import pytest
from fastapi.testclient import TestClient

import cards
import main
from agents import thinker
from progress import get_progress


POST_TEXT = "Most AI models talk. Jev decides."

PLAN = json.dumps({"goal": "practice post", "steps": [
    {"id": "s1", "step": 1, "action": "write the post", "tool": "think",
     "args": {"instruction": "Write a short post. Return only the post."}, "depends_on": []},
    {"id": "s2", "step": 2, "action": "publish the practice post", "tool": "practice_post",
     "args": {"text": "$s1", "title": "Jev post"}, "depends_on": ["s1"]},
]})

GOAL_OK = '{"goal_satisfied": true, "reason": "ok", "missing_information": []}'


@pytest.fixture
def client(docs_dir, fake_llm, monkeypatch):
    fake_llm({
        "You are the planning component": PLAN,
        "thinking step": POST_TEXT,
        "Goal Verification": GOAL_OK,
    })
    monkeypatch.setattr(thinker, "_llm", main.llm)
    return TestClient(main.app)


# ---------------- cards ----------------

def test_cards_for_each_tool(tmp_path):
    image = tmp_path / "pic.png"
    image.write_bytes(b"png")

    made = cards.card("create_image", {"saved": True, "path": str(image), "file_name": "pic.png",
                                       "folder": str(tmp_path), "size": "1200x1200", "concept": "a lab"})
    assert made == {"tool": "create_image", "type": "image", "title": "a lab", "size": "1200x1200", "model": "",
                    "path": str(image), "file_name": "pic.png", "folder": str(tmp_path)}

    research = cards.card("research_topic", {"topic": "AI", "summary": "s", "key_points": ["a"] * 9,
                                             "sources": [{"n": 1, "title": "T", "url": "https://x"}]})
    assert research["points"] == ["a"] * 5 and research["sources"] == [{"title": "T", "url": "https://x"}]

    assert cards.card("write_post", {"text": "hi", "characters": 2})["text"] == "hi"
    assert cards.card("linkedin_create_post", {"posted": False, "dry_run": True, "text": "hi"})["dry_run"] is True


def test_no_card_for_failures_unknown_tools_or_odd_results():
    assert cards.card("create_image", {"error": "quota"}) is None
    assert cards.card("get_ram_usage", {"used_gb": 3}) is None
    assert cards.card("research_topic", "not a dict") is None
    # a broken result never breaks the task
    assert cards.card("research_topic", {"key_points": 5}) is None


def test_task_cards_keep_order_and_read_repeated_keys():
    results = {"write_post": {"text": "a"}, "get_ram_usage": {}, "write_post#2": {"text": "b"}}
    assert [c["text"] for c in cards.task_cards(results)] == ["a", "b"]


# ---------------- files ----------------

def test_only_files_fatmee_made_can_be_fetched(client, docs_dir, tmp_path_factory):
    inside = docs_dir / "pic.png"
    inside.write_bytes(b"\x89PNG fake")
    assert client.get("/files", params={"path": str(inside)}).content == b"\x89PNG fake"

    # a file elsewhere on the PC is refused, until FATMEE made it
    elsewhere = tmp_path_factory.mktemp("other") / "secret.png"
    elsewhere.write_bytes(b"x")
    assert client.get("/files", params={"path": str(elsewhere)}).status_code == 404
    cards.remember_file(str(elsewhere))
    assert client.get("/files", params={"path": str(elsewhere)}).status_code == 200

    # wrong file types and missing files are refused even in FATMEE's folders
    script = docs_dir / "run.py"
    script.write_text("print(1)")
    assert client.get("/files", params={"path": str(script)}).status_code == 404
    assert client.get("/files", params={"path": str(docs_dir / "gone.png")}).status_code == 404
    assert client.get("/files", params={"path": "C:/Windows/win.ini"}).status_code == 404


def test_open_file_refuses_other_files(client):
    assert client.post("/files/open", json={"path": "C:/Windows/notepad.exe"}).status_code == 404


def test_status_has_no_secrets(client):
    status = client.get("/status").json()

    assert set(status) == {"llm", "images", "linkedin", "search"}
    assert set(status["linkedin"]) == {"connected", "dry_run", "name"}
    assert "test-key" not in json.dumps(status)


def test_local_model_path_is_shortened():
    assert main._model_name("/home/x/models/Qwen3.6-35B-A3B-Q4_K_M.gguf") == "Qwen3.6-35B-A3B-Q4_K_M"
    assert main._model_name("gemini-3.6-flash") == "gemini-3.6-flash"


# ---------------- the whole flow the screen follows ----------------

def test_screen_flow_events_cards_and_approval(client):
    task_id = uuid.uuid4().hex

    reply = client.post("/ask", json={"question": "practice post", "task_id": task_id}).json()

    assert reply["status"] == "waiting_approval"
    # the post written before the pause is already a card
    assert reply["cards"] == [{"tool": "think", "type": "text", "text": POST_TEXT}]

    events = get_progress().events(task_id)
    kinds = [e["kind"] for e in events]
    assert kinds[0] == "planning" and "planned" in kinds and kinds[-1] == "waiting_approval"

    step = next(e for e in events if e["kind"] == "step")
    assert step["data"]["card"]["text"] == POST_TEXT and step["data"]["error"] is None

    pending = events[-1]["data"]["pending"]
    assert pending[0]["tool"] == "practice_post" and pending[0]["args"]["text"] == POST_TEXT

    done = client.post(f"/approvals/{task_id}", json={"decision": "approve"}).json()

    assert done["status"] == "done"
    practice = next(c for c in done["cards"] if c["type"] == "practice")
    # the saved practice file can be previewed
    assert client.get("/files", params={"path": practice["path"]}).status_code == 200

    # the events continue after the decision, and end with done
    after = [e["kind"] for e in get_progress().events(task_id, events[-1]["n"])]
    assert after[0] == "approved" and after[-1] == "done"

    # the conversation keeps the cards, for the screen's history
    history = client.get("/sessions/default").json()["messages"]
    assert history[-1]["meta"]["cards"]


# ---------------- side panels ----------------

def test_system_status_reads_real_tools(client, monkeypatch):
    from tools import system_tools
    monkeypatch.setattr(system_tools, "get_ram_usage", lambda: {"usage_percent": 50})
    monkeypatch.setattr(system_tools, "get_battery_status", lambda: 1 / 0)
    monkeypatch.setattr(system_tools, "check_internet_connection", lambda: {"online": True})

    status = client.get("/system").json()

    assert status["ram"] == {"usage_percent": 50} and status["internet"]["online"] is True
    # one broken reading never breaks the card
    assert status["battery"] == {"error": "ZeroDivisionError"}


def test_recent_files_lists_only_fatmee_files_newest_first(client, docs_dir):
    import os
    old, new = docs_dir / "old.png", docs_dir / "new report.docx"
    old.write_bytes(b"png")
    new.write_bytes(b"docx")
    (docs_dir / "notes.py").write_text("x")
    os.utime(old, (1_700_000_000, 1_700_000_000))

    files = client.get("/files/recent").json()["files"]

    assert [f["name"] for f in files][:2] == ["new report.docx", "old.png"]
    assert {f["kind"] for f in files} >= {"image", "docx"} and all(f["name"] != "notes.py" for f in files)
