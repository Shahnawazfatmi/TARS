import os
import sys
import tempfile
from pathlib import Path

import pytest

# Run tests without real keys and without touching the network.
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

# Never touch the real conversation file (backend/data/fatmee.db).
# Forced, not setdefault: a real FATMEE_DB_PATH must not leak in.
_TEST_DB_DIR = tempfile.mkdtemp(prefix="fatmee-tests-")
os.environ["FATMEE_DB_PATH"] = os.path.join(_TEST_DB_DIR, "test.db")
os.environ["FATMEE_CHECKPOINT_DB_PATH"] = os.path.join(_TEST_DB_DIR, "checkpoints.db")

# Never write test tasks into the real task log (backend/logs/).
os.environ["FATMEE_TASK_LOG_DIR"] = os.path.join(_TEST_DB_DIR, "logs")

# Never touch the real LinkedIn login or the real LinkedIn: a temp
# token file, and API addresses that point nowhere (tests that need
# LinkedIn start their own fake server and set these themselves).
os.environ["FATMEE_LINKEDIN_TOKEN_PATH"] = os.path.join(_TEST_DB_DIR, "linkedin_token.bin")
os.environ["FATMEE_LINKEDIN_API_BASE"] = "http://127.0.0.1:9"
os.environ["FATMEE_LINKEDIN_AUTH_BASE"] = "http://127.0.0.1:9"
os.environ["FATMEE_LINKEDIN_DRY_RUN"] = "true"

# Never connect to real MCP servers (Tavily etc.): tests stay offline
# and free even when real keys are in .env. MCP tests use their own
# fake server instead.
_EMPTY_MCP_CONFIG = os.path.join(_TEST_DB_DIR, "mcp_servers.json")

with open(_EMPTY_MCP_CONFIG, "w", encoding="utf-8") as _f:
    _f.write("{}")

os.environ["FATMEE_MCP_CONFIG"] = _EMPTY_MCP_CONFIG

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class FakeProvider:
    """Answers each FATMEE component with a scripted reply."""

    def __init__(self, replies, name="fake", model="fake-model"):
        self.replies = replies
        self.name = name
        self.model = model
        self.calls = []

    def generate(self, prompt, system, json_mode):
        self.calls.append({"prompt": prompt, "system": system, "json_mode": json_mode})

        for marker, reply in self.replies.items():
            if marker in prompt:
                value = reply.pop(0) if isinstance(reply, list) else reply

                if isinstance(value, Exception):
                    raise value

                return value

        return "Final answer from fake model."


@pytest.fixture
def docs_dir(tmp_path, monkeypatch):
    """Save test documents and images in a temp folder, never on the real Desktop."""

    import config
    from agents import files

    monkeypatch.setattr(config, "DOCUMENTS_DIR", str(tmp_path))
    monkeypatch.setattr(config, "IMAGES_DIR", str(tmp_path))
    monkeypatch.setattr(config, "PRACTICE_DIR", str(tmp_path))
    monkeypatch.setattr(files, "allowed_roots", lambda: [tmp_path.resolve()])
    return tmp_path


@pytest.fixture
def fake_llm(monkeypatch):
    """Replace the real providers with one scripted fake provider."""

    import main

    def install(replies):
        fake = FakeProvider(replies)
        monkeypatch.setattr(main.llm, "providers", [fake])
        return fake

    return install
