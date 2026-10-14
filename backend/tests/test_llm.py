import pytest

from conftest import FakeProvider
from llm import LLM, LLMError, parse_json


def test_plain_json():
    assert parse_json('{"a": 1}') == {"a": 1}


def test_json_fence():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_bare_fence():
    assert parse_json('```\n{"a": 1}\n```') == {"a": 1}


def test_prose_around_json():
    assert parse_json('Here is the plan:\n{"a": 1}\nDone.') == {"a": 1}


@pytest.mark.parametrize("text", ["", "no json here", "[1, 2]"])
def test_invalid(text):
    with pytest.raises(ValueError):
        parse_json(text)


def test_falls_back_to_second_provider():
    openai = FakeProvider({"": ConnectionError("openai down")}, name="openai")
    gemini = FakeProvider({"": "answer from gemini"}, name="gemini")
    assert LLM([openai, gemini]).text("hi") == "answer from gemini"
    assert len(openai.calls) == 1 and len(gemini.calls) == 1


def test_empty_answer_also_falls_back():
    openai = FakeProvider({"": ""}, name="openai")
    gemini = FakeProvider({"": "ok"}, name="gemini")
    assert LLM([openai, gemini]).text("hi") == "ok"


def test_first_provider_used_when_it_works():
    openai = FakeProvider({"": "from openai"}, name="openai")
    gemini = FakeProvider({"": "from gemini"}, name="gemini")
    assert LLM([openai, gemini]).text("hi") == "from openai"
    assert gemini.calls == []


def test_all_providers_fail():
    openai = FakeProvider({"": ConnectionError("a")}, name="openai")
    gemini = FakeProvider({"": ConnectionError("b")}, name="gemini")
    with pytest.raises(LLMError, match="openai: a.*gemini: b"):
        LLM([openai, gemini]).text("hi")
