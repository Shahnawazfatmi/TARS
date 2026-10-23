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


# ---------------- local model server ----------------

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import config
import llm


@pytest.fixture
def local_server():
    """A fake OpenAI-style model server on this PC; records each request."""

    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, body))
            reply = json.dumps({
                "id": "x", "object": "chat.completion", "created": 0, "model": body["model"],
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": '{"ok": true}'}}],
                "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(reply)))
            self.end_headers()
            self.wfile.write(reply)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/v1", requests
    server.shutdown()


def test_local_provider_request(local_server):
    url, requests = local_server
    provider = llm.LocalProvider(url, "my-model")

    text, usage = provider.generate("hi", "be brief", json_mode=True)

    path, body = requests[0]
    assert path == "/v1/chat/completions"
    assert body["model"] == "my-model"
    assert body["messages"] == [{"role": "system", "content": "be brief"}, {"role": "user", "content": "hi"}]
    assert body["response_format"] == {"type": "json_object"}
    # thinking is off by default, so the answer is not used up
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert text == '{"ok": true}'
    assert usage == {"input_tokens": 7, "output_tokens": 3, "total_tokens": 10}


def test_local_provider_without_system_or_json(local_server):
    url, requests = local_server
    llm.LocalProvider(url, "my-model").generate("hi", None, json_mode=False)

    body = requests[0][1]
    assert body["messages"] == [{"role": "user", "content": "hi"}]
    assert "response_format" not in body


def test_local_server_off_falls_back_fast():
    import time
    # port 9 on this PC: nothing listens, so the connection is refused
    local = llm.LocalProvider("http://127.0.0.1:9/v1", "my-model")
    gemini = FakeProvider({"": "from gemini"}, name="gemini")

    start = time.perf_counter()
    assert LLM([local, gemini]).text("hi") == "from gemini"
    assert time.perf_counter() - start < 10
    assert local.client.max_retries == 0


def test_local_is_first_by_default_and_skipped_without_url(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDERS", ["local", "gemini"])
    monkeypatch.setattr(config, "LOCAL_LLM_URL", "http://127.0.0.1:9/v1")
    assert [p.name for p in llm.build_providers()] == ["local", "gemini"]

    monkeypatch.setattr(config, "LOCAL_LLM_URL", "")
    assert [p.name for p in llm.build_providers()] == ["gemini"]
