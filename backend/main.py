import logging
import re
import time
from datetime import datetime

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import config
from agents import document, research, thinker
from graph import build_graph, new_task_id, run
from llm import LLM, LLMError
from mcp_client import MCPManager, set_manager
from memory import (
    EPISODE_STATUSES,
    ConversationStore,
    format_history,
    get_episode_store,
    resolve_period,
)
from tools.registry import register_mcp_tools, tool_specs


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

# Agents share the same LLM (OpenAI first, Gemini as fallback).
research.configure(llm)
thinker.configure(llm)
document.configure(llm)

# The request flow (plan -> run -> verify -> recover -> answer)
# lives in graph.py as a LangGraph graph.
fatmee = build_graph(llm)

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


class Question(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    session_id: str = SESSION_ID


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


@app.post("/ask")
def ask(data: Question, background: BackgroundTasks):

    task_id = new_task_id()
    started_at = datetime.now()
    clock = time.perf_counter()

    history = format_history(conversations.recent(data.session_id))

    try:
        reply, details = run(fatmee, data.question, history, task_id=task_id)

    except Exception as e:
        # Failed tasks go in the diary too, so "what failed today?"
        # works. The conversation keeps only answered questions.
        _save_quietly(
            episodes.add,
            task_id=task_id,
            session_id=data.session_id,
            started_at=started_at,
            question=data.question,
            status="failed",
            duration_s=time.perf_counter() - clock,
            problems=[str(e)[:300]],
        )
        raise

    duration = time.perf_counter() - clock

    # Memory is written after the reply has been sent, so saving
    # never adds to the user's wait.
    background.add_task(
        _save_quietly,
        conversations.add_exchange,
        data.session_id,
        data.question,
        reply["response"],
        task_id=task_id,
        meta={"tools_used": reply.get("tools_used", [])},
    )

    background.add_task(
        _save_quietly,
        episodes.add,
        task_id=task_id,
        session_id=data.session_id,
        started_at=started_at,
        question=data.question,
        status="partial" if reply.get("missing_information") else "answered",
        duration_s=duration,
        goal=details["goal"],
        tools=details["tools"],
        summary=reply["response"],
        problems=details["problems"],
    )

    return {**reply, "session_id": data.session_id}


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
