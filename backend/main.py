import html
import json
import logging
import re
import threading
import time
from datetime import datetime
from typing import Literal

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator

import config
import agents
import task_logger
from connectors import linkedin as linkedin_api
from graph import build_graph, new_task_id, resume, run, waiting_task
from image_gen import ImageGenerator
from llm import LLM, LLMError
from mcp_client import MCPManager, set_manager
from progress import FINAL_EVENTS, get_progress
from memory import (
    EPISODE_STATUSES,
    ConversationStore,
    format_history,
    get_episode_store,
    resolve_period,
)
from tools.registry import register_mcp_tools, tool_parameters, tool_specs


# ============================================================
# CONFIGURATION
# ============================================================

logging.basicConfig(
    level=config.LOG_LEVEL,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)

logger = logging.getLogger("fatmee")

app = FastAPI(title="FATMEE")

# OpenAI first, Gemini as fallback (see FATMEE_LLM_PROVIDERS)
llm = LLM.from_config()

# MCP servers (mcp_servers.json): web search now; LinkedIn, Gmail and
# GitHub later. A server without its API key is skipped, not fatal.
mcp = MCPManager.from_file()
mcp.connect()
set_manager(mcp)
register_mcp_tools(mcp, mcp.servers)

# Image models: OpenAI first, Gemini as fallback (see FATMEE_IMAGE_PROVIDERS)
images = ImageGenerator.from_config()

# Agents share the same LLM and image models.
agents.configure(llm, images)

# The request flow (plan -> run -> verify -> recover -> answer)
# lives in graph.py as a LangGraph graph.
fatmee = build_graph(llm)

# Task logs (logs/tasks.jsonl): tasks that were running when FATMEE
# last stopped are written down as "incomplete".
task_logger.get_task_log().recover_incomplete()

# Working memory: the conversation so far, in a local SQLite file.
conversations = ConversationStore()

# Episodic memory: FATMEE's diary of every task, in the same file.
episodes = get_episode_store()


# ============================================================
# ERRORS
# ============================================================

@app.exception_handler(LLMError)
def llm_error_handler(request: Request, exc: LLMError):

    return JSONResponse(
        status_code=503,
        content={
            "response": (
                "FATMEE could not reach any AI model (OpenAI or "
                "Gemini). Check your internet connection and API "
                "keys, then try again."
            ),
            "error": str(exc),
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    """Log why a request was rejected, then return the normal 422."""

    reasons = "; ".join(
        f"{'.'.join(str(p) for p in err.get('loc', []))}: {err.get('msg')}"
        for err in exc.errors()
    )

    body = (await request.body()).decode("utf-8", errors="replace")

    logger.warning(
        "Rejected %s %s (422): %s | body: %r",
        request.method,
        request.url.path,
        reasons,
        body[:300],
    )

    return await request_validation_exception_handler(request, exc)


# ============================================================
# REQUEST MODEL
# ============================================================

SESSION_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"

SESSION_ID = Field(
    default="default",
    pattern=SESSION_PATTERN,
    description="Conversation to continue. Leave as 'default' for one ongoing conversation.",
)


TASK_PATTERN = r"^[A-Za-z0-9_-]{8,64}$"


class Question(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    session_id: str = SESSION_ID
    task_id: str | None = Field(
        default=None,
        pattern=TASK_PATTERN,
        description="Optional: pick the task id yourself, to follow live "
                    "progress at /tasks/{task_id}/stream while it runs.",
    )


# Everyday words that mean the same decision.
_DECISION_WORDS = {
    "approve": "approve", "approved": "approve", "ok": "approve", "okay": "approve", "yes": "approve", "y": "approve",
    "reject": "reject", "rejected": "reject", "no": "reject", "n": "reject", "cancel": "reject",
}


class Decision(BaseModel):
    decision: Literal["approve", "reject"] = Field(
        description='"approve" or "reject" (also accepted: ok, yes, no, cancel).',
    )
    step_id: str | None = Field(
        default=None,
        description="Which waiting step. Leave out to decide for every waiting step.",
    )
    changes: dict | None = Field(
        default=None,
        description='Edits to apply before running, e.g. {"text": "new post text"}. Needs step_id if several steps wait.',
    )

    model_config = {"json_schema_extra": {"examples": [{"decision": "approve"}]}}

    @field_validator("decision", mode="before")
    @classmethod
    def _everyday_words(cls, value):
        if isinstance(value, str):
            return _DECISION_WORDS.get(value.strip().lower(), value)
        return value


# ============================================================
# HOME
# ============================================================

@app.get("/")
def home():
    return {
        "status": "online",
        "assistant": "FATMEE"
    }


@app.get("/tools")
def list_tools():
    """Tools FATMEE can use."""

    return tool_specs()


@app.get("/mcp")
def mcp_status():
    """Which MCP servers are connected, skipped (missing key) or failing."""

    return mcp.status


# ============================================================
# ASK
# ============================================================

def _save_quietly(save, *args, **kwargs) -> None:
    """Run a memory write; log a failure instead of breaking the reply."""

    try:
        save(*args, **kwargs)

    except Exception:
        logger.exception("Could not save to memory")


def _status(reply: dict) -> str:
    if reply.get("status") == "waiting_approval":
        return "waiting_approval"
    return "partial" if reply.get("missing_information") else "answered"


def _remember(background, session_id, task_id, started_at, user_text, question, reply, details, duration):
    """
    Save the exchange and the diary entry after the reply is sent, so
    saving never adds to the user's wait. A paused task is saved as
    "waiting_approval" and updated when it finishes.
    """

    background.add_task(
        _save_quietly,
        conversations.add_exchange,
        session_id,
        user_text,
        reply["response"],
        task_id=task_id,
        meta={"tools_used": reply.get("tools_used", []), "status": reply.get("status")},
    )

    background.add_task(
        _save_quietly,
        episodes.add,
        task_id=task_id,
        session_id=session_id,
        started_at=started_at,
        question=question,
        status=_status(reply),
        duration_s=duration,
        goal=details["goal"],
        tools=details["tools"],
        summary=reply["response"],
        problems=details["problems"],
    )


def _record_failure(task_id, session_id, started_at, question, clock, error) -> None:
    # Failed tasks go in the diary too, so "what failed today?" works.
    # The conversation keeps only answered questions.
    _save_quietly(
        episodes.add,
        task_id=task_id,
        session_id=session_id,
        started_at=started_at,
        question=question,
        status="failed",
        duration_s=time.perf_counter() - clock,
        problems=[str(error)[:300]],
    )


@app.post("/ask")
def ask(data: Question, background: BackgroundTasks):

    task_id = data.task_id or new_task_id()

    if data.task_id and (get_progress().known(task_id) or waiting_task(fatmee, task_id) or episodes.exists(task_id)):
        raise HTTPException(status_code=409, detail="This task id is already used. Pick a new one.")

    started_at = datetime.now()
    clock = time.perf_counter()

    history = format_history(conversations.recent(data.session_id))

    try:
        reply, details = run(
            fatmee, data.question, history,
            task_id=task_id,
            session_id=data.session_id,
            started_at=started_at.isoformat(timespec="seconds"),
        )

    except Exception as e:
        _record_failure(task_id, data.session_id, started_at, data.question, clock, e)
        raise

    _remember(
        background, data.session_id, task_id, started_at,
        data.question, data.question, reply, details, time.perf_counter() - clock,
    )

    return {**reply, "session_id": data.session_id}


# ============================================================
# APPROVALS (steps that wait for your OK)
# ============================================================

# Tasks being resumed right now, so a double click can't run a step twice.
_resuming = set()
_resuming_lock = threading.Lock()


def _check_task_id(task_id: str) -> None:

    if not re.match(TASK_PATTERN, task_id):
        raise HTTPException(status_code=422, detail="Invalid task id.")


def _waiting_or_404(task_id: str) -> dict:

    _check_task_id(task_id)

    task = waiting_task(fatmee, task_id)

    if task is None:
        raise HTTPException(status_code=404, detail="No task with this id is waiting for approval.")

    return task


def _waiting_tasks(limit: int) -> list:

    entries, _ = episodes.search(status="waiting_approval", limit=limit)

    waiting = []

    for entry in entries:
        task = waiting_task(fatmee, entry["task_id"])

        if task:
            waiting.append(task)

    return waiting


@app.get("/approvals")
def list_approvals(limit: int = Query(20, ge=1, le=100)):
    """Tasks waiting for your OK, newest first, with what each step would do."""

    waiting = _waiting_tasks(limit)

    return {"waiting": len(waiting), "tasks": waiting}


@app.post("/approvals")
def decide_only_waiting(data: Decision, background: BackgroundTasks):
    """
    Approve or reject without a task id, when exactly one task is
    waiting. If several wait, use POST /approvals/{task_id}.
    """

    waiting = _waiting_tasks(100)

    if not waiting:
        raise HTTPException(status_code=404, detail="Nothing is waiting for approval.")

    if len(waiting) > 1:
        raise HTTPException(
            status_code=409,
            detail={
                "message": f"{len(waiting)} tasks are waiting. Say which one with POST /approvals/{{task_id}}.",
                "waiting": [{"task_id": t["task_id"], "question": t["question"]} for t in waiting],
            },
        )

    return decide(waiting[0]["task_id"], data, background)


def _check_changes(item: dict, changes: dict) -> None:
    """
    An edit must name a real input of the step, e.g. "text" for a post.
    A misspelled field would be dropped later and the original text
    published, so it is refused here with the fields that can change.
    """

    allowed = (set(item.get("args") or {}) | set(tool_parameters(item["tool"]))) - {"inputs", "user_request"}
    unknown = sorted(set(changes) - allowed)

    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"{item['tool']} has no input called {', '.join(unknown)}. "
                   f"You can change: {', '.join(sorted(allowed)) or 'nothing'}.",
        )


@app.get("/approvals/{task_id}")
def get_approval(task_id: str):
    """One waiting task: the question and the steps that need your OK."""

    return _waiting_or_404(task_id)


@app.post("/approvals/{task_id}")
def decide(task_id: str, data: Decision, background: BackgroundTasks):
    """
    Approve or reject waiting steps. Approved steps run (with your
    edits, if any) and the task finishes; rejected steps are skipped.
    """

    task = _waiting_or_404(task_id)
    pending_ids = [item["step_id"] for item in task["pending"]]

    if data.step_id and data.step_id not in pending_ids:
        raise HTTPException(status_code=422, detail=f"Step {data.step_id} is not waiting. Waiting: {pending_ids}")

    if data.changes and not data.step_id and len(pending_ids) > 1:
        raise HTTPException(status_code=422, detail="Several steps are waiting: say which one to edit with step_id.")

    targets = [data.step_id] if data.step_id else pending_ids

    if data.changes and data.decision == "approve":
        _check_changes(next(p for p in task["pending"] if p["step_id"] == targets[0]), data.changes)

    decisions = {
        step_id: {"decision": data.decision, "changes": data.changes or {}}
        for step_id in targets
    }

    with _resuming_lock:
        if task_id in _resuming:
            raise HTTPException(status_code=409, detail="This task is already continuing.")
        _resuming.add(task_id)

    clock = time.perf_counter()

    try:
        started_at = datetime.fromisoformat(task["started_at"]) if task["started_at"] else datetime.now()
    except ValueError:
        started_at = datetime.now()

    logger.info("Task %s: %s %s", task_id, data.decision, targets)

    try:
        reply, details = resume(fatmee, task_id, decisions)

    except Exception as e:
        _record_failure(task_id, task["session_id"], started_at, task["question"], clock, e)
        raise

    finally:
        with _resuming_lock:
            _resuming.discard(task_id)

    user_text = f"{'Approved' if data.decision == 'approve' else 'Rejected'}{' with changes' if data.changes else ''}."

    _remember(
        background, task["session_id"], task_id, started_at,
        user_text, task["question"], reply, details, time.perf_counter() - clock,
    )

    return {**reply, "session_id": task["session_id"]}


# ============================================================
# LIVE PROGRESS (for the future screen and voice)
# ============================================================

@app.get("/tasks/{task_id}/events")
def task_events(task_id: str, after: int = Query(0, ge=0)):
    """What a task has done so far (recent tasks only). Use after= to get only newer events."""

    _check_task_id(task_id)

    progress = get_progress()

    if not progress.known(task_id):
        raise HTTPException(status_code=404, detail="No recent task with this id.")

    return {"task_id": task_id, "events": progress.events(task_id, after)}


@app.get("/tasks/{task_id}/stream")
def task_stream(task_id: str, after: int = Query(0, ge=0)):
    """
    Follow a task live (Server-Sent Events). Open it before or while
    calling /ask with the same task_id. The stream ends when the task
    is done, waits for approval, or fails.
    """

    _check_task_id(task_id)

    progress = get_progress()
    deadline = time.monotonic() + 15 * 60

    def events():
        last = after
        quiet_since = time.monotonic()

        while time.monotonic() < deadline:

            for event in progress.events(task_id, last):
                last = event["n"]
                quiet_since = time.monotonic()
                yield f"event: {event['kind']}\ndata: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"

                if event["kind"] in FINAL_EVENTS:
                    return

            if time.monotonic() - quiet_since > 15:
                # Keeps the connection open through quiet stretches.
                quiet_since = time.monotonic()
                yield ": still working\n\n"

            time.sleep(0.3)

    return StreamingResponse(events(), media_type="text/event-stream")


# ============================================================
# SESSIONS (working memory)
# ============================================================

def _check_session_id(session_id: str) -> None:

    if not re.match(SESSION_PATTERN, session_id):
        raise HTTPException(status_code=422, detail="Invalid session id.")


@app.get("/sessions/{session_id}")
def get_session(session_id: str):
    """The full conversation of one session."""

    _check_session_id(session_id)

    messages = conversations.messages(session_id)

    return {"session_id": session_id, "count": len(messages), "messages": messages}


@app.delete("/sessions/{session_id}")
def clear_session(session_id: str):
    """Forget a conversation and start fresh."""

    _check_session_id(session_id)

    deleted = conversations.clear(session_id)

    logger.info("Cleared session %s (%d messages)", session_id, deleted)

    return {"session_id": session_id, "deleted_messages": deleted}


# ============================================================
# LINKEDIN LOGIN (OAuth 2.0; posting itself is done by the
# LinkedIn MCP server in local_mcp/linkedin_server.py)
# ============================================================

# Login attempts started here: state -> expiry time. The state proves a
# callback belongs to a login FATMEE started (protects against CSRF).
_linkedin_states = {}
_linkedin_lock = threading.Lock()
_LOGIN_MINUTES = 10


def _page(title: str, message: str, status_code: int = 200) -> HTMLResponse:
    """A tiny page for the browser tab LinkedIn sends you back to."""

    return HTMLResponse(
        f"<!doctype html><meta charset='utf-8'><title>FATMEE - {html.escape(title)}</title>"
        f"<body style='font-family:system-ui;max-width:40rem;margin:4rem auto;padding:0 1rem'>"
        f"<h2>{html.escape(title)}</h2><p>{html.escape(message)}</p></body>",
        status_code=status_code,
    )


@app.get("/linkedin/connect")
def linkedin_connect():
    """Open this in your browser to log in to LinkedIn once (about every 60 days)."""

    if not linkedin_api.is_configured():
        raise HTTPException(
            status_code=400,
            detail="Add LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET to backend/.env, then restart FATMEE.",
        )

    state = linkedin_api.new_state()
    now = time.time()

    with _linkedin_lock:
        for old in [s for s, expiry in _linkedin_states.items() if expiry < now]:
            del _linkedin_states[old]
        _linkedin_states[state] = now + _LOGIN_MINUTES * 60

    return RedirectResponse(linkedin_api.authorization_url(state))


@app.get("/linkedin/callback")
def linkedin_callback(code: str = "", state: str = "", error: str = "", error_description: str = ""):
    """LinkedIn sends your browser here after you log in."""

    if error:
        return _page("LinkedIn login not completed", f"LinkedIn said: {error_description or error}.", 400)

    with _linkedin_lock:
        expiry = _linkedin_states.pop(state, 0)

    if not code or expiry < time.time():
        return _page(
            "Login link expired",
            "This login was not started by FATMEE, or took longer than "
            f"{_LOGIN_MINUTES} minutes. Start again at /linkedin/connect.",
            400,
        )

    try:
        connected = linkedin_api.exchange_code(code)

    except linkedin_api.LinkedInError as e:
        return _page("LinkedIn login failed", str(e), 400)

    except Exception as e:
        logger.warning("LinkedIn login failed: %s", type(e).__name__)
        return _page("LinkedIn login failed", "Could not reach LinkedIn. Check the internet connection and try again.", 502)

    logger.info("LinkedIn connected as %s", connected["name"] or "(no name)")

    days = max(0, round((connected["expires_at"] - time.time()) / 86400))

    return _page(
        "Connected to LinkedIn",
        f"FATMEE is connected as {connected['name'] or 'you'} for about {days} days. You can close this tab.",
    )


@app.get("/linkedin/status")
def linkedin_status():
    """Connected or not, as whom, until when, dry run on or off (no secrets)."""

    return {**linkedin_api.status(), "mcp_server": mcp.status.get("linkedin", {"state": "not configured"})}


@app.delete("/linkedin/token")
def linkedin_disconnect():
    """Forget the saved LinkedIn login on this PC."""

    return {"disconnected": linkedin_api.delete_token()}


# ============================================================
# EPISODES (episodic memory)
# ============================================================

@app.get("/episodes")
def list_episodes(
    day: str = Query("", description="today, yesterday, a weekday, or YYYY-MM-DD"),
    days_back: int = Query(0, ge=0, le=3650, description="Tasks from the last N days"),
    keyword: str = Query("", max_length=200),
    status: str = Query("", description="answered, partial, failed or waiting_approval"),
    session_id: str = Query("", max_length=64),
    limit: int = Query(20, ge=1, le=200),
):
    """FATMEE's diary of past tasks, newest first."""

    if status and status not in EPISODE_STATUSES:
        raise HTTPException(status_code=422, detail=f"status must be one of {EPISODE_STATUSES}")

    if session_id:
        _check_session_id(session_id)

    try:
        start, end, label = resolve_period(day, days_back)

    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    entries, total = episodes.search(
        start=start,
        end=end,
        keyword=keyword,
        status=status,
        session_id=session_id,
        limit=limit,
    )

    return {"period": label, "found": total, "shown": len(entries), "episodes": entries}
