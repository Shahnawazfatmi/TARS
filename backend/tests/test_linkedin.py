import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import main
from agents import writer
from conftest import FakeProvider
from connectors import linkedin as li
from executor import run_plan
from goal_verifier import GoalVerifier
from llm import LLM
from mcp_client import MCPManager
from tools import registry


BACKEND = Path(__file__).resolve().parents[1]
GOOD_TOKEN = {"access_token": "TOKEN-SECRET", "expires_at": int(time.time()) + 86400 * 50,
              "scope": "openid,profile,email,w_member_social", "person_urn": "urn:li:person:abc123", "name": "Test Person"}


# ============================================================
# A fake LinkedIn (local HTTP server), used by the connector and
# by the real MCP server process
# ============================================================

class FakeLinkedIn:

    def __init__(self):
        self.requests = []
        self.fail = {}          # path -> status code to return
        fake = self

        class Handler(BaseHTTPRequestHandler):

            def log_message(self, *args):
                pass

            def _reply(self, status, body=None, headers=None):
                self.send_response(status)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                data = json.dumps(body).encode() if body is not None else b""
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _record(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                fake.requests.append({"method": self.command, "path": self.path,
                                      "headers": dict(self.headers), "body": raw})
                return raw

            def do_GET(self):
                self._record()
                path = self.path.split("?")[0]
                if path in fake.fail:
                    return self._reply(fake.fail[path], {"message": "nope"})
                if path == "/v2/userinfo":
                    return self._reply(200, {"sub": "abc123", "name": "Test Person", "email": "t@example.com"})
                self._reply(404, {"message": "not found"})

            def do_PUT(self):
                self._record()
                self._reply(201)

            def do_POST(self):
                raw = self._record()
                path = self.path.split("?")[0]
                if path in fake.fail:
                    return self._reply(fake.fail[path], {"message": "nope"})
                if path == "/oauth/v2/accessToken":
                    return self._reply(200, {"access_token": "TOKEN-SECRET", "expires_in": 5184000,
                                             "scope": "openid,profile,email,w_member_social"})
                if path == "/rest/images":
                    return self._reply(200, {"value": {"uploadUrl": f"{fake.base}/upload/1", "image": "urn:li:image:IMG1"}})
                if path == "/rest/posts":
                    fake.posted = json.loads(raw)
                    return self._reply(201, headers={"x-restli-id": "urn:li:share:999"})
                self._reply(404, {"message": "not found"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def calls(self, method, path):
        return [r for r in self.requests if r["method"] == method and r["path"].split("?")[0] == path]

    def close(self):
        self.server.shutdown()


@pytest.fixture
def linkedin(monkeypatch, tmp_path):
    fake = FakeLinkedIn()
    monkeypatch.setenv("FATMEE_LINKEDIN_API_BASE", fake.base)
    monkeypatch.setenv("FATMEE_LINKEDIN_AUTH_BASE", fake.base)
    monkeypatch.setenv("FATMEE_LINKEDIN_TOKEN_PATH", str(tmp_path / "token.bin"))
    monkeypatch.setenv("LINKEDIN_CLIENT_ID", "client-id")
    monkeypatch.setenv("LINKEDIN_CLIENT_SECRET", "client-secret")
    yield fake
    fake.close()


@pytest.fixture
def picture(tmp_path):
    from PIL import Image
    path = tmp_path / "post.png"
    Image.new("RGB", (64, 64), (0, 100, 200)).save(path)
    return path


# ============================================================
# connector: official API calls
# ============================================================

def test_login_url_asks_only_for_granted_scopes(linkedin):
    url = li.authorization_url("STATE1")
    assert url.startswith(linkedin.base + "/oauth/v2/authorization?")
    assert "scope=openid+profile+email+w_member_social" in url
    assert "state=STATE1" in url and "client_id=client-id" in url
    assert "redirect_uri=http%3A%2F%2F127.0.0.1%3A8000%2Flinkedin%2Fcallback" in url


def test_login_needs_the_app_keys(monkeypatch):
    monkeypatch.delenv("LINKEDIN_CLIENT_ID", raising=False)
    with pytest.raises(li.LinkedInError, match="LINKEDIN_CLIENT_ID"):
        li.authorization_url("x")


def test_code_exchange_saves_an_encrypted_token(linkedin):
    connected = li.exchange_code("CODE1")

    assert connected["name"] == "Test Person"
    sent = linkedin.calls("POST", "/oauth/v2/accessToken")[0]["body"].decode()
    assert "grant_type=authorization_code" in sent and "code=CODE1" in sent and "client_secret=client-secret" in sent

    raw = li.token_path().read_bytes()
    assert b"TOKEN-SECRET" not in raw                       # not readable on disk
    assert li.load_token()["person_urn"] == "urn:li:person:abc123"
    # status never shows the token
    assert "TOKEN-SECRET" not in json.dumps(li.status())
    assert li.status()["connected"] and li.status()["name"] == "Test Person"


def test_profile(linkedin):
    li.save_token(GOOD_TOKEN)
    profile = li.get_profile()
    assert profile["id"] == "abc123" and profile["email"] == "t@example.com"
    assert linkedin.calls("GET", "/v2/userinfo")[0]["headers"]["Authorization"] == "Bearer TOKEN-SECRET"


def test_dry_run_checks_but_publishes_nothing(linkedin, picture):
    li.save_token(GOOD_TOKEN)
    result = li.create_post("Hello (world) #AI", str(picture))

    assert result["dry_run"] and not result["posted"] and "nothing was published" in result["note"]
    assert result["text"] == "Hello (world) #AI" and result["as"] == "Test Person"
    assert linkedin.calls("POST", "/rest/posts") == [] and linkedin.calls("POST", "/rest/images") == []


def test_live_post_with_image(linkedin, picture, monkeypatch):
    monkeypatch.setenv("FATMEE_LINKEDIN_DRY_RUN", "false")
    li.save_token(GOOD_TOKEN)

    result = li.create_post("Most AI models talk (a lot). Jev decides. #AI", str(picture), alt_text="A sorting screen")

    assert result["posted"] and result["post_url"] == "https://www.linkedin.com/feed/update/urn:li:share:999/"

    init = linkedin.calls("POST", "/rest/images")[0]
    assert json.loads(init["body"]) == {"initializeUploadRequest": {"owner": "urn:li:person:abc123"}}
    assert init["headers"]["LinkedIn-Version"] == "202609" and init["headers"]["X-Restli-Protocol-Version"] == "2.0.0"
    assert linkedin.calls("PUT", "/upload/1")[0]["body"] == picture.read_bytes()

    post = linkedin.posted
    assert post["author"] == "urn:li:person:abc123" and post["visibility"] == "PUBLIC"
    assert post["commentary"] == "Most AI models talk \\(a lot\\). Jev decides. #AI"   # escaped, hashtag kept
    assert post["content"]["media"] == {"id": "urn:li:image:IMG1", "altText": "A sorting screen"}
    assert post["lifecycleState"] == "PUBLISHED" and post["distribution"]["feedDistribution"] == "MAIN_FEED"


def test_text_only_post(linkedin, monkeypatch):
    monkeypatch.setenv("FATMEE_LINKEDIN_DRY_RUN", "false")
    li.save_token(GOOD_TOKEN)
    li.create_post("Just text.")
    assert "content" not in linkedin.posted and linkedin.calls("POST", "/rest/images") == []


@pytest.mark.parametrize("status,words", [
    (401, "Connect again"), (403, "Share on LinkedIn"), (429, "daily limit"), (426, "FATMEE_LINKEDIN_VERSION"),
])
def test_linkedin_errors_are_plain_words(linkedin, monkeypatch, status, words):
    monkeypatch.setenv("FATMEE_LINKEDIN_DRY_RUN", "false")
    li.save_token(GOOD_TOKEN)
    linkedin.fail["/rest/posts"] = status

    with pytest.raises(li.LinkedInError, match=words) as error:
        li.create_post("Hello")
    assert "TOKEN-SECRET" not in str(error.value)


def test_post_checks_before_sending(linkedin, tmp_path):
    with pytest.raises(li.LinkedInError, match="not connected"):
        li.create_post("Hello")

    li.save_token({**GOOD_TOKEN, "expires_at": int(time.time()) - 10})
    with pytest.raises(li.LinkedInError, match="expired"):
        li.create_post("Hello")

    with pytest.raises(li.LinkedInError, match="3000"):
        li.check_post("x" * 3001)
    with pytest.raises(li.LinkedInError, match="not found"):
        li.check_post("Hello", str(tmp_path / "missing.png"))
    (tmp_path / "doc.pdf").write_text("x")
    with pytest.raises(li.LinkedInError, match="PNG, JPG and GIF"):
        li.check_post("Hello", str(tmp_path / "doc.pdf"))
    assert linkedin.requests == []                           # nothing was sent


@pytest.mark.parametrize("text,escaped", [
    ("Plain text", "Plain text"),
    ("(brackets) [x] {y} <z>", "\\(brackets\\) \\[x\\] \\{y\\} \\<z\\>"),
    ("me@x a_b *c* ~d~ |e| \\f", "me\\@x a\\_b \\*c\\* \\~d\\~ \\|e\\| \\\\f"),
    ("#AI and #Agents2026 but # alone", "#AI and #Agents2026 but \\# alone"),
])
def test_little_text_escaping(text, escaped):
    assert li.escape_commentary(text) == escaped


# ============================================================
# the LinkedIn MCP server (real process, over MCP/stdio)
# ============================================================

@pytest.fixture
def mcp_linkedin(linkedin, monkeypatch):
    li.save_token(GOOD_TOKEN)

    spec = json.loads((BACKEND / "mcp_servers.json").read_text(encoding="utf-8"))["linkedin"]
    manager = MCPManager({"linkedin": spec}, base_dir=str(BACKEND))
    manager.connect()

    monkeypatch.setattr(registry, "TOOLS", list(registry.TOOLS))
    monkeypatch.setattr(registry, "TOOLS_BY_NAME", dict(registry.TOOLS_BY_NAME))
    monkeypatch.setattr(registry, "tool_registry", dict(registry.tool_registry))
    registry.register_mcp_tools(manager, manager.servers)

    yield manager, linkedin
    manager.close()


def test_mcp_server_connects_and_registers_safely(mcp_linkedin):
    manager, _ = mcp_linkedin

    assert manager.status["linkedin"]["state"] == "connected"
    assert manager.status["linkedin"]["tools"] == ["linkedin_create_post", "linkedin_get_profile", "linkedin_status"]
    # publishing waits for approval and runs at most once; reading doesn't need approval
    assert registry.requires_approval("linkedin_create_post")
    assert registry.max_per_task("linkedin_create_post") == 1
    assert not registry.requires_approval("linkedin_get_profile")
    assert not registry.requires_approval("linkedin_status")


def test_mcp_profile_and_status(mcp_linkedin):
    profile = registry.run_tool("linkedin_get_profile", {})
    assert profile["name"] == "Test Person"                  # a dict, usable as $s1.name
    status = registry.run_tool("linkedin_status", {})
    assert status["connected"] and status["dry_run"] and "TOKEN-SECRET" not in json.dumps(status)


def test_mcp_post_waits_for_approval_then_publishes(mcp_linkedin, picture, monkeypatch):
    manager, linkedin = mcp_linkedin

    plan = {"steps": [{"id": "s1", "tool": "linkedin_create_post",
                       "args": {"text": "Hello LinkedIn #AI", "image_path": str(picture)}}]}

    first = run_plan(plan)
    assert first["pending"][0]["args"]["text"] == "Hello LinkedIn #AI"
    assert linkedin.calls("POST", "/rest/posts") == []      # nothing before the OK

    # The server process reads FATMEE_LINKEDIN_DRY_RUN when it starts:
    # this one started with the default, dry run.
    approved = run_plan(plan, done=first["by_id"], approvals={"s1": {"decision": "approve"}})
    result = approved["results"]["linkedin_create_post"]
    assert result["dry_run"] and linkedin.calls("POST", "/rest/posts") == []

    # A dry run is the user's own setting: the AI goal check is told it
    # is SETTLED, not missing (live on 21 Oct it was judged a failure).
    class Recorder:
        prompt = ""

        def json(self, prompt, component=""):
            Recorder.prompt = prompt
            return {"goal_satisfied": True, "reason": "dry run as configured", "missing_information": []}

    GoalVerifier(Recorder()).verify("post it on LinkedIn", plan, approved["results"])
    assert "SETTLED STEPS" in Recorder.prompt and "linkedin_create_post: ran in dry run" in Recorder.prompt


def test_mcp_live_post_after_approval(linkedin, picture, monkeypatch):
    monkeypatch.setenv("FATMEE_LINKEDIN_DRY_RUN", "false")
    li.save_token(GOOD_TOKEN)

    spec = json.loads((BACKEND / "mcp_servers.json").read_text(encoding="utf-8"))["linkedin"]
    manager = MCPManager({"linkedin": spec}, base_dir=str(BACKEND))
    manager.connect()

    try:
        result = json.loads(manager.call("linkedin_create_post", {"text": "Live #AI", "image_path": str(picture)}))
    finally:
        manager.close()

    assert result["posted"] and result["post_url"].endswith("urn:li:share:999/")
    assert linkedin.posted["content"]["media"]["id"] == "urn:li:image:IMG1"


def test_server_is_skipped_without_keys(monkeypatch):
    monkeypatch.delenv("LINKEDIN_CLIENT_ID", raising=False)
    spec = json.loads((BACKEND / "mcp_servers.json").read_text(encoding="utf-8"))["linkedin"]
    manager = MCPManager({"linkedin": spec}, base_dir=str(BACKEND))
    manager.connect()
    assert manager.status["linkedin"]["state"] == "skipped" and "LINKEDIN_CLIENT_ID" in manager.status["linkedin"]["reason"]
    manager.close()


def test_only_listed_settings_reach_the_server_process(monkeypatch):
    from mcp_client import _local_process
    monkeypatch.setenv("FATMEE_LINKEDIN_DRY_RUN", "false")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-leak")

    connection = _local_process(
        {"transport": "stdio", "command": "python", "args": ["local_mcp/linkedin_server.py"], "cwd": "."},
        {"pass_env": ["FATMEE_LINKEDIN_DRY_RUN"]},
        str(BACKEND),
    )
    assert connection["command"] == sys.executable
    assert connection["args"] == [str(BACKEND / "local_mcp" / "linkedin_server.py")]
    assert connection["env"] == {"FATMEE_LINKEDIN_DRY_RUN": "false", "FATMEE_NO_DOTENV": "1"}


def test_server_process_does_not_read_dotenv():
    # The real process check: with FATMEE_NO_DOTENV=1 (always set for
    # local servers), importing config must not load backend/.env, so
    # only pass_env settings exist. (Before 22 Oct the server process
    # read .env itself and got every key.)
    import subprocess

    script = "import os, config; print(sorted(k for k in os.environ if 'KEY' in k or 'SECRET' in k))"
    env = {k: v for k, v in os.environ.items() if k.upper() in ("SYSTEMROOT", "PATH", "TEMP", "TMP")}
    env["FATMEE_NO_DOTENV"] = "1"

    out = subprocess.run([sys.executable, "-c", script], cwd=str(BACKEND), env=env,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[]"


# ============================================================
# FATMEE's login pages
# ============================================================

def test_connect_and_callback(linkedin, monkeypatch):
    client = TestClient(main.app)

    redirect = client.get("/linkedin/connect", follow_redirects=False)
    assert redirect.status_code == 307
    location = redirect.headers["location"]
    state = location.split("state=")[1].split("&")[0]

    # a callback that FATMEE did not start is refused
    assert client.get("/linkedin/callback", params={"code": "C", "state": "forged"}).status_code == 400

    page = client.get("/linkedin/callback", params={"code": "C", "state": state})
    assert page.status_code == 200 and "Connected to LinkedIn" in page.text and "Test Person" in page.text

    # the same state cannot be used twice
    assert client.get("/linkedin/callback", params={"code": "C", "state": state}).status_code == 400

    status = client.get("/linkedin/status").json()
    assert status["connected"] and "TOKEN-SECRET" not in json.dumps(status)

    assert client.delete("/linkedin/token").json() == {"disconnected": True}
    assert client.get("/linkedin/status").json()["connected"] is False


def test_login_cancelled_or_not_configured(monkeypatch):
    client = TestClient(main.app)
    page = client.get("/linkedin/callback", params={"error": "user_cancelled_login", "error_description": "The user cancelled"})
    assert page.status_code == 400 and "The user cancelled" in page.text

    monkeypatch.delenv("LINKEDIN_CLIENT_ID", raising=False)
    monkeypatch.delenv("LINKEDIN_CLIENT_SECRET", raising=False)
    assert client.get("/linkedin/connect", follow_redirects=False).status_code == 400


# ============================================================
# the post writer agent and the plan
# ============================================================

POST_JSON = json.dumps({"text": "**AI agents grew 3x.**\n\n## Why\n\n\n\nThey act [1].\n\n#AI #Agents", "hashtags": ["#AI", "#Agents"]})


def test_post_writer(monkeypatch):
    fake = FakeProvider({"LinkedIn post writer": POST_JSON})
    monkeypatch.setattr(writer, "_llm", LLM([fake]))

    research = {"summary": "Agents act [1].", "sources": [{"n": 1, "title": "News", "url": "https://news.example/a"}]}
    post = writer.write_post(instruction="Share this", inputs={"s1": research}, user_request="Post about agents")

    # markdown and research numbers removed
    assert post["text"] == "AI agents grew 3x.\n\nWhy\n\nThey act.\n\n#AI #Agents"
    assert post["hashtags"] == ["#AI", "#Agents"] and post["sources"] == ["https://news.example/a"]
    prompt = fake.calls[0]["prompt"]
    assert "Post about agents" in prompt and "[1] News - https://news.example/a" in prompt
    assert "No invented numbers" in prompt and "Sources:" in prompt and "No [1]-style" in prompt

    no_links = writer.write_post(instruction="Share this", inputs={"s1": research}, include_sources="false")
    assert no_links["sources"] == [] and "Do not add links" in fake.calls[-1]["prompt"]
    assert "needs to know" in writer.write_post()["error"]


def test_planner_knows_the_linkedin_flow(fake_llm):
    fake = fake_llm({"You are the planning component": '{"goal": "x", "steps": []}'})
    TestClient(main.app).post("/ask", json={"question": "Post about AI agents on LinkedIn"})
    prompt = fake.calls[0]["prompt"]
    assert '"write_post"' in prompt and "linkedin_create_post" in prompt
    assert "$<write_post id>.text" in prompt and "practice_post" in prompt
    # never publishes when the user only asked to write a draft
    assert "only when the user asks to post, publish or" in prompt
