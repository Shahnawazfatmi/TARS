import json

from fastapi.testclient import TestClient

import main


client = TestClient(main.app)

PLAN_RAM = json.dumps({"goal": "ram", "steps": [
    {"step": 1, "action": "read ram", "tool": "get_ram_usage", "args": {}}
]})
GOAL_OK = '{"goal_satisfied": true, "reason": "ok", "missing_information": []}'
GOAL_NO = '{"goal_satisfied": "false", "reason": "need cpu", "missing_information": ["cpu"]}'


def test_home():
    assert client.get("/").json()["status"] == "online"


def test_tools_endpoint_lists_new_tools():
    names = {t["name"] for t in client.get("/tools").json()}
    assert {"get_battery_status", "get_top_processes", "get_wifi_info"} <= names


def test_chat_question_uses_no_tools(fake_llm):
    fake = fake_llm({"You are the planning component": '{"goal": "chat", "steps": []}'})
    body = client.post("/ask", json={"question": "What is ML?"}).json()
    assert body["tools_used"] == []
    # planner + answer only
    assert len(fake.calls) == 2


def test_tool_question_happy_path(fake_llm):
    fake = fake_llm({"You are the planning component": PLAN_RAM, "Goal Verification": GOAL_OK})
    body = client.post("/ask", json={"question": "How much RAM?"}).json()
    assert body["tools_used"] == ["get_ram_usage"]
    assert len(fake.calls) == 3
    # JSON mode requested for planner
    assert fake.calls[0]["json_mode"] is True


def test_string_false_triggers_goal_recovery(fake_llm):
    replan = json.dumps({"reason": "cpu", "steps": [
        {"step": 1, "action": "cpu", "tool": "get_cpu_usage", "args": {}}
    ]})
    fake = fake_llm({
        "You are the planning component": PLAN_RAM,
        "Goal Verification": [GOAL_NO, GOAL_OK],
        "recovery planning": replan,
    })
    body = client.post("/ask", json={"question": "RAM and CPU?"}).json()
    assert set(body["tools_used"]) == {"get_ram_usage", "get_cpu_usage"}


def test_failed_tool_is_replanned(fake_llm):
    bad_plan = json.dumps({"goal": "x", "steps": [
        {"step": 1, "action": "x", "tool": "made_up_tool", "args": {}}
    ]})
    replan = json.dumps({"reason": "fix", "steps": [
        {"step": 1, "action": "ram", "tool": "get_ram_usage", "args": {}}
    ]})
    fake_llm({
        "You are the planning component": bad_plan,
        "recovery planning": replan,
        "Goal Verification": GOAL_OK,
    })
    body = client.post("/ask", json={"question": "RAM?"}).json()
    assert body["tools_used"] == ["get_ram_usage"]


def test_llm_outage_returns_clear_503(fake_llm):
    fake_llm({"You are the planning component": ConnectionError("offline")})
    res = client.post("/ask", json={"question": "RAM?"})
    assert res.status_code == 503
    assert "could not reach" in res.json()["response"]


def test_invalid_planner_json_is_an_error_not_a_guess(fake_llm):
    fake_llm({"You are the planning component": "sorry, no plan"})
    res = client.post("/ask", json={"question": "RAM?"})
    assert res.status_code == 503


def test_empty_question_rejected():
    assert client.post("/ask", json={"question": ""}).status_code == 422


def test_no_tool_for_missing_info_stops_early(fake_llm):
    """GPU temperature: no tool provides it, so no second goal check."""
    plan = json.dumps({"goal": "gpu temp", "steps": [
        {"step": 1, "action": "hw", "tool": "get_hardware_info", "args": {}}
    ]})
    fake = fake_llm({
        "You are the planning component": plan,
        "Goal Verification": [
            '{"goal_satisfied": false, "reason": "no temp", "missing_information": ["GPU temperature"]}'
        ],
        "recovery planning": '{"reason": "no tool reads temperature", "steps": []}',
    })
    body = client.post("/ask", json={"question": "GPU temperature?"}).json()
    assert body["missing_information"] == ["GPU temperature"]
    # planner + goal check + replan + final answer, no second goal check
    assert len(fake.calls) == 4
    assert "GPU temperature" in fake.calls[-1]["prompt"]


def test_folder_not_found_is_answered_not_failed(fake_llm):
    plan = json.dumps({"goal": "list", "steps": [
        {"step": 1, "action": "list", "tool": "list_files", "args": {"path": "Z:/does/not/exist"}}
    ]})
    fake = fake_llm({
        "You are the planning component": plan,
        "recovery planning": '{"reason": "the error is the answer", "steps": []}',
        "Goal Verification": GOAL_OK,
    })
    res = client.post("/ask", json={"question": "List Z:/does/not/exist"})
    body = res.json()
    assert res.status_code == 200
    assert "could not complete" not in body["response"]
    assert body["tools_used"] == []
    # The final answer sees the real error message
    assert "Folder not found" in fake.calls[-1]["prompt"]


def test_reply_has_task_id_and_graph_frees_memory(fake_llm):
    fake_llm({"You are the planning component": PLAN_RAM, "Goal Verification": GOAL_OK})
    body = client.post("/ask", json={"question": "RAM?"}).json()
    assert len(body["task_id"]) == 32
    # Finished tasks are removed from the checkpointer
    config_ = {"configurable": {"thread_id": body["task_id"]}}
    assert not main.fatmee.get_state(config_).values


def test_dependent_plan_runs_in_order_through_api(fake_llm):
    plan = json.dumps({"goal": "x", "steps": [
        {"id": "s1", "step": 1, "action": "hw", "tool": "get_hardware_info", "args": {}, "depends_on": []},
        {"id": "s2", "step": 2, "action": "ram", "tool": "get_ram_usage", "args": {}, "depends_on": ["s1"]},
    ]})
    fake_llm({"You are the planning component": plan, "Goal Verification": GOAL_OK})
    body = client.post("/ask", json={"question": "7B LLM?"}).json()
    assert body["tools_used"] == ["get_hardware_info", "get_ram_usage"]
