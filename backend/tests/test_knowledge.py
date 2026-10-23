import json

from fastapi.testclient import TestClient

import main
from graph import KNOWLEDGE_INSTRUCTION
from tools.registry import tool_specs


EMPTY_PLAN = json.dumps({"goal": "explain LLMs", "steps": []})


def test_general_question_is_answered_without_tools(fake_llm):
    fake = fake_llm({
        "You are the planning component": EMPTY_PLAN,
        "What is an LLM?": "An LLM is a model trained on lots of text.",
    })

    reply = TestClient(main.app).post("/ask", json={"question": "What is an LLM?"}).json()

    assert reply["response"] == "An LLM is a model trained on lots of text."
    assert reply["tools_used"] == [] and reply["cards"] == []
    # only the planner and the answer: no research, no goal check
    assert len(fake.calls) == 2
    assert fake.calls[1]["system"] == KNOWLEDGE_INSTRUCTION


def test_planner_is_told_when_to_answer_from_knowledge(fake_llm):
    fake = fake_llm({"You are the planning component": EMPTY_PLAN})

    TestClient(main.app).post("/ask", json={"question": "Explain RAG simply"})

    prompt = fake.calls[0]["prompt"]
    assert 'Return an empty plan ("steps": []) when FATMEE can answer from its own' in prompt
    assert 'Use "research_topic" only when the answer depends on recent' in prompt


def test_research_tool_says_it_is_for_current_facts():
    research = next(t for t in tool_specs() if t["name"] == "research_topic")
    assert "general knowledge questions need no research" in research["description"]
