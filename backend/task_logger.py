"""
Task logs: one JSON line per finished task, to find FATMEE's weak
spots, debug failures, and measure speed and cost.

    backend/logs/tasks.jsonl       one complete record per finished task
    backend/logs/open/<id>.json    a task still running or waiting for
                                   approval (rewritten after each stage)

This is observability, not memory: FATMEE never reads these logs back.

The graph, executor, LLM and image layers report into the record of
the task they work for. The record travels in a context variable,
which LangGraph passes into its nodes and the executor copies into
its tool threads, so concurrent tasks never mix.

Every hook is safe: a logging problem is reported in the normal log
("fatmee.task_log ... TASK LOG ERROR") and never changes what FATMEE
does. Secrets are removed and long values cut before anything is
written.

Record fields and status rules: docs/FATMEE_GUIDE.md, "Task logs".
"""

import contextvars
import functools
import json
import logging
import os
import re
import threading
import time
from contextlib import contextmanager, nullcontext
from datetime import datetime

import config


logger = logging.getLogger("fatmee.task_log")

SCHEMA_VERSION = 1

TASK_STATUSES = ("success", "partial", "failed", "incomplete")

FAILURE_CATEGORIES = (
    "planning_error",
    "invalid_plan",
    "unknown_tool",
    "tool_execution_error",
    "step_skipped",
    "execution_verification_failed",
    "goal_verification_failed",
    "replanning_error",
    "max_recovery_attempts",
    "llm_error",
    "timeout",
    "unknown_error",
)

# Graph stage -> timing field. Recovery includes its replanner call
# (also counted alone in replanning_s).
_PHASE_TIMINGS = {
    "planning": "planning_s",
    "execution": "execution_s",
    "goal_check": "verification_s",
    "recovery": "recovery_s",
    "answer": "answer_s",
}

# LangGraph pauses a task by raising these; they are not failures.
_CONTROL_FLOW = ("GraphInterrupt", "NodeInterrupt", "ParentCommand")


def _now() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


def _round(seconds) -> float | None:
    return None if seconds is None else round(seconds, 3)


# ============================================================
# SANITIZING (secrets out, long values cut)
# ============================================================

_SECRET_KEY = re.compile(
    r"api[_-]?key|secret|passw(or)?d|authori[sz]ation|cookie|credential|private[_-]?key"
    r"|(^|[_-])(access|refresh|id|auth|bearer)?[_-]?token$",
    re.IGNORECASE,
)

_SECRET_TEXT = [
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"), "Bearer [REDACTED]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"), "[REDACTED]"),            # OpenAI
    (re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"), "[REDACTED]"),           # Google
    (re.compile(r"\btvly-[A-Za-z0-9_-]{16,}"), "[REDACTED]"),          # Tavily
    (re.compile(
        r"(?i)\b(api[_-]?key|access_token|refresh_token|client_secret|code|token|password)=[^&\s\"']+"
    ), r"\1=[REDACTED]"),
]

_SECRET_ENV = re.compile(r"KEY|SECRET|TOKEN|PASSWORD", re.IGNORECASE)

_MAX_ITEMS = 50
_MAX_DEPTH = 8


@functools.lru_cache(maxsize=1)
def _secret_values() -> tuple:
    """Values of secret settings (.env is loaded by config), longest first."""

    values = {
        value for name, value in os.environ.items()
        if _SECRET_ENV.search(name)
        and not name.upper().endswith(("_PATH", "_DIR", "_BASE", "_URL", "_URI"))
        and value and len(value) >= 8
    }

    return tuple(sorted(values, key=len, reverse=True))


class _Cleaner:
    """Removes secrets and cuts long values; remembers if anything was cut."""

    def __init__(self, limit: int):
        self.limit = limit
        self.truncated = False

    def text(self, value: str, limit: int | None = None) -> str:

        for secret in _secret_values():
            if secret in value:
                value = value.replace(secret, "[REDACTED]")

        for pattern, replacement in _SECRET_TEXT:
            value = pattern.sub(replacement, value)

        limit = limit or self.limit

        if len(value) > limit:
            self.truncated = True
            return f"{value[:limit]}… [truncated: {len(value)} chars]"

        return value

    def __call__(self, value, depth: int = 0):

        if isinstance(value, str):
            return self.text(value)

        if value is None or isinstance(value, (bool, int, float)):
            return value

        if depth >= _MAX_DEPTH:
            self.truncated = True
            return "[truncated: nested too deep]"

        if isinstance(value, dict):
            out = {}

            for number, (key, item) in enumerate(value.items()):

                if number >= _MAX_ITEMS:
                    self.truncated = True
                    out["_truncated_items"] = len(value) - _MAX_ITEMS
                    break

                key = str(key)
                out[key] = "[REDACTED]" if _SECRET_KEY.search(key) else self(item, depth + 1)

            return out

        if isinstance(value, (list, tuple, set)):
            items = list(value)
            out = [self(item, depth + 1) for item in items[:_MAX_ITEMS]]

            if len(items) > _MAX_ITEMS:
                self.truncated = True
                out.append(f"[truncated: {len(items)} items]")

            return out

        return self.text(str(value))


def sanitize(value, limit: int | None = None):
    """A copy of value with secrets removed and long strings cut."""

    return _Cleaner(limit or config.TASK_LOG_MAX_CHARS)(value)


def _shown_args(args: dict) -> dict:
    """Tool inputs as the log shows them: earlier results only by step id."""

    shown = dict(args or {})

    if isinstance(shown.get("inputs"), dict):
        shown["inputs"] = f"<results of {', '.join(shown['inputs']) or 'no steps'}>"

    # The user's own words are already the record's "goal".
    shown.pop("user_request", None)

    return shown


def _error_of(result) -> str | None:
    if isinstance(result, dict) and "error" in result:
        return str(result["error"])
    return None


# ============================================================
# SAFETY
# ============================================================

def _safe(method):
    """A logging hook must never break the task: report the problem instead."""

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):

        try:
            return method(self, *args, **kwargs)

        except Exception:
            store = getattr(self, "store", self)
            store.errors += 1
            logger.exception("TASK LOG ERROR in %s (the task itself continues)", method.__name__)
            return None

    return wrapper


# ============================================================
# ONE TASK
# ============================================================

class TaskRecord:
    """Everything observed about one task. Safe to update from several threads."""

    def __init__(self, store: "TaskLogStore", task_id: str, question: str = "", session_id: str = ""):
        self.store = store
        self.lock = threading.RLock()
        self.clean = _Cleaner(config.TASK_LOG_MAX_CHARS)

        # Stage running now; kept when it raises, to name the failure.
        self.phase_name = None
        self._segment_start = None

        self.data = {
            "schema_version": SCHEMA_VERSION,
            "task_id": task_id,
            "session_id": session_id,
            "started_at": _now(),
            "finished_at": None,
            "goal": self.clean.text(question, 4000),
            "planned_goal": None,
            "plan": None,
            "tool_calls": [],
            "execution_verifications": [],
            "goal_verifications": [],
            "replans": [],
            "approvals": {"requested": [], "decisions": []},
            "llm_calls": [],
            "image_calls": [],
            "response": None,
            "missing_information": [],
            "failures": [],
            "exception": None,
            "execution_success": None,
            "goal_satisfied": None,
            "task_status": None,
            "failure_category": None,
            "timings": {
                "planning_s": 0.0,
                "execution_s": 0.0,
                "verification_s": 0.0,
                "recovery_s": 0.0,
                "replanning_s": 0.0,
                "answer_s": 0.0,
                "approval_wait_s": None,
                "active_s": 0.0,
                "wall_s": None,
            },
            "counts": {},
            "usage": {},
            "segments": 0,
            "state": "running",
            "truncated": False,
        }

    # ---------------- timing ----------------

    def begin_segment(self) -> None:
        with self.lock:
            self._segment_start = time.perf_counter()
            self.data["segments"] += 1

    def end_segment(self) -> None:
        with self.lock:
            if self._segment_start is not None:
                self.data["timings"]["active_s"] += time.perf_counter() - self._segment_start
                self._segment_start = None

    @contextmanager
    def phase(self, name: str):
        """Time one graph stage and let calls inside it know the stage."""

        token = _stage.set(name)
        start = time.perf_counter()
        self.phase_name = name

        try:
            yield

        except BaseException as e:
            if type(e).__name__ in _CONTROL_FLOW:
                self.phase_name = None
            raise

        else:
            self.phase_name = None

        finally:
            _stage.reset(token)
            self._add_time(name, time.perf_counter() - start)

    @_safe
    def _add_time(self, name: str, seconds: float) -> None:

        field = _PHASE_TIMINGS.get(name)

        with self.lock:
            if field:
                self.data["timings"][field] += seconds

        self.store._snapshot(self)

    # ---------------- what happened ----------------

    @_safe
    def failure(self, category: str, stage: str, message: str, **evidence) -> None:

        if category not in FAILURE_CATEGORIES:
            category = "unknown_error"

        with self.lock:
            self.data["failures"].append({
                "category": category,
                "stage": stage,
                "message": self.clean.text(str(message), 500),
                **self.clean(evidence),
            })

    @_safe
    def plan(self, plan: dict, error: str | None = None) -> None:

        steps = [
            {
                "id": step.get("id"),
                "tool": step.get("tool"),
                "action": step.get("action", ""),
                "args": _shown_args(step.get("args") if isinstance(step.get("args"), dict) else {}),
                "depends_on": step.get("depends_on", []),
                "needs_approval": bool(step.get("needs_approval")),
            }
            for step in plan.get("steps", [])
        ]

        with self.lock:
            self.data["planned_goal"] = self.clean.text(str(plan.get("goal", "")), 1000)
            self.data["plan"] = self.clean({"steps": steps, "repairs": plan.get("repairs", [])})

        if error:
            self.failure("invalid_plan", "planning", error)

    @_safe
    def tool_call(self, step: dict, result, seconds: float, started_at: str, exception=None) -> None:

        error = _error_of(result)
        stage = _stage.get() or "execution"

        call = {
            "step_id": step.get("id"),
            "tool": step.get("tool"),
            "stage": stage,
            "args": self.clean(_shown_args(step.get("args") or {})),
            "started_at": started_at,
            "duration_s": _round(seconds),
            "ok": error is None,
        }

        if error is None:
            call["result"] = self.clean(result)
        else:
            call["error"] = self.clean.text(error, 1000)

        if exception is not None:
            call["exception_type"] = type(exception).__name__

        with self.lock:
            self.data["tool_calls"].append(call)

        if error is not None:

            if error.startswith("Unknown tool:"):
                category = "unknown_tool"
            elif isinstance(exception, TimeoutError) or "timed out" in error.lower():
                category = "timeout"
            else:
                category = "tool_execution_error"

            self.failure(category, stage, error, tool=step.get("tool"), step_id=step.get("id"))

    @_safe
    def verification(self, result: dict, after: str) -> None:

        with self.lock:
            self.data["execution_verifications"].append({"after": after, **self.clean(result)})

        if not result.get("verified", True):
            self.failure("execution_verification_failed", after, "; ".join(result.get("errors", []))[:500],
                         errors=result.get("errors", []))

    @_safe
    def goal_check(self, result: dict, seconds: float, after: str) -> None:

        entry = {
            "after": after,
            "goal_satisfied": result.get("goal_satisfied"),
            "reason": self.clean.text(str(result.get("reason", "")), 1000),
            "missing_information": self.clean(result.get("missing_information", [])),
            "duration_s": _round(seconds),
        }

        with self.lock:
            self.data["goal_verifications"].append(entry)

        if result.get("goal_satisfied") is False:
            self.failure("goal_verification_failed", "goal_check", entry["reason"],
                         missing_information=entry["missing_information"])

    @_safe
    def replan(self, trigger: str, previous_failure, replan: dict, replanner_s: float,
               results: dict, verification: dict, execution_s: float) -> None:

        steps = [
            {"id": s.get("id"), "tool": s.get("tool"), "action": s.get("action", ""),
             "args": _shown_args(s.get("args") if isinstance(s.get("args"), dict) else {})}
            for s in replan.get("steps", [])
        ]

        tool_steps = [s for s in steps if s["tool"]]

        if not tool_steps:
            outcome = "no_tool_can_help"
        elif verification.get("verified"):
            outcome = "steps_succeeded"
        else:
            outcome = "steps_failed"

        entry = {
            "attempt": len(self.data["replans"]) + 1,
            "trigger": trigger,
            "previous_failure": self.clean(previous_failure),
            "reason": self.clean.text(str(replan.get("reason", "")), 1000),
            "steps": self.clean(steps),
            "replanner_s": _round(replanner_s),
            "execution_s": _round(execution_s),
            "verification": self.clean(verification),
            "outcome": outcome,
        }

        with self.lock:
            self.data["replans"].append(entry)
            self.data["timings"]["replanning_s"] += replanner_s

        if entry["reason"].startswith("Replanning failed"):
            self.failure("replanning_error", "recovery", entry["reason"])

    @_safe
    def llm_call(self, provider: str, model: str, component: str, seconds: float,
                 ok: bool, usage: dict | None = None, error: str | None = None) -> None:

        call = {
            "provider": provider,
            "model": model,
            "component": component,
            "duration_s": _round(seconds),
            "ok": ok,
            "input_tokens": (usage or {}).get("input_tokens"),
            "output_tokens": (usage or {}).get("output_tokens"),
            "total_tokens": (usage or {}).get("total_tokens"),
            "estimated_cost_usd": _cost(model, usage),
        }

        if error:
            call["error"] = self.clean.text(error, 500)

        with self.lock:
            self.data["llm_calls"].append(call)

    @_safe
    def image_call(self, provider: str, model: str, seconds: float, ok: bool, error: str | None = None) -> None:

        call = {
            "provider": provider,
            "model": model,
            "component": _stage.get() or "unknown",
            "duration_s": _round(seconds),
            "ok": ok,
        }

        if error:
            call["error"] = self.clean.text(error, 500)

        with self.lock:
            self.data["image_calls"].append(call)

    @_safe
    def approvals_requested(self, pending: list) -> None:

        with self.lock:
            self.data["approvals"]["requested"].extend(
                {"step_id": p.get("step_id"), "tool": p.get("tool"), "args": self.clean(p.get("args", {}))}
                for p in pending
            )
            self.data["paused_at"] = _now()
            self.data["state"] = "waiting_approval"

    @_safe
    def approvals_decided(self, decisions: dict) -> None:

        with self.lock:
            self.data["approvals"]["decisions"].extend(
                {"step_id": step_id, "decision": d.get("decision"), "edited": bool(d.get("changes"))}
                for step_id, d in (decisions or {}).items()
            )

            paused_at = self.data.pop("paused_at", None)

            if paused_at:
                waited = (datetime.now() - datetime.fromisoformat(paused_at)).total_seconds()
                self.data["timings"]["approval_wait_s"] = (self.data["timings"]["approval_wait_s"] or 0) + waited

            self.data["state"] = "running"

    # ---------------- the end ----------------

    def close(self, state: dict | None, reply: dict | None, exception=None, incomplete: str = "") -> dict:
        """Work out the outcome fields; returns the finished record."""

        with self.lock:
            data = self.data
            state = state or {}

            data["finished_at"] = _now()
            data["state"] = "finished"

            if reply is not None:
                data["response"] = self.clean.text(str(reply.get("response", "")), config.TASK_LOG_RESPONSE_CHARS)

            data["missing_information"] = self.clean(state.get("missing_information") or [])

            # Steps that never ran (limits, failed inputs). A step the
            # user rejected is a decision, not a failure. Failed calls
            # are matched as stored, so this also works after a restart.
            failed_calls = {(c["tool"], c.get("error")) for c in data["tool_calls"] if not c["ok"]}

            for key, result in (state.get("tool_results") or {}).items():
                error = _error_of(result)

                if error is None or result.get("rejected"):
                    continue

                if (key.split("#")[0], self.clean.text(error, 1000)) in failed_calls:
                    continue

                if key == "plan_check":
                    if any(f["category"] == "invalid_plan" for f in data["failures"]):
                        continue        # already recorded when the plan was made
                    category = "invalid_plan"
                else:
                    category = "step_skipped"

                self.failure(category, "execution", error, result_key=key)

            if exception is not None:
                phase = self.phase_name or "unknown"
                category = _exception_category(exception, phase)
                data["exception"] = {
                    "type": type(exception).__name__,
                    "message": self.clean.text(str(exception), 500),
                    "stage": phase,
                }
                self.failure(category, phase, str(exception))

            if incomplete:
                self.failure("unknown_error", self.phase_name or data.get("last_stage") or "unknown", incomplete)

            calls = data["tool_calls"]
            ok_calls = [c for c in calls if c["ok"]]
            checks = data["goal_verifications"]

            data["execution_success"] = (len(ok_calls) == len(calls)) if calls else None
            data["goal_satisfied"] = checks[-1]["goal_satisfied"] if checks else None

            if (data["goal_satisfied"] is False and data["replans"]
                    and any(r["outcome"] != "no_tool_can_help" for r in data["replans"])):
                self.failure("max_recovery_attempts", "goal_check",
                             f"Goal still not satisfied after {len(data['replans'])} recovery attempt(s).")

            data["task_status"] = _task_status(data, exception, incomplete)
            data["failure_category"] = data["failures"][0]["category"] if data["failures"] else None
            data["failure_categories"] = sorted({f["category"] for f in data["failures"]})

            data["counts"] = {
                "planned_steps": len((data["plan"] or {}).get("steps", [])),
                "tool_calls": len(calls),
                "failed_tool_calls": len(calls) - len(ok_calls),
                "recovery_attempts": len(data["replans"]),
                "goal_checks": len(checks),
                "llm_calls": len(data["llm_calls"]),
                "llm_failed_attempts": sum(1 for c in data["llm_calls"] if not c["ok"]),
                "image_calls": len(data["image_calls"]),
                "approvals_requested": len(data["approvals"]["requested"]),
            }

            data["usage"] = _usage_totals(data["llm_calls"])

            timings = data["timings"]

            for field in list(timings):
                timings[field] = _round(timings[field])

            started = datetime.fromisoformat(data["started_at"])
            timings["wall_s"] = _round((datetime.now() - started).total_seconds())

            data["truncated"] = self.clean.truncated
            data.pop("paused_at", None)

            return data

    def snapshot(self) -> dict:
        with self.lock:
            return {**json.loads(json.dumps(self.data, default=str)), "last_stage": self.phase_name}


def _exception_category(exception, phase: str) -> str:

    name = type(exception).__name__
    text = str(exception)

    if isinstance(exception, TimeoutError) or "timeout" in name.lower() or "timed out" in text.lower():
        return "timeout"

    if name == "LLMError":

        if text.startswith("Planner returned invalid JSON"):
            return "planning_error"

        return "llm_error"

    if name == "PlanError":
        return "invalid_plan"

    if phase == "planning":
        return "planning_error"

    if phase == "recovery":
        return "replanning_error"

    return "unknown_error"


def _task_status(data: dict, exception, incomplete: str) -> str:
    """
    success     answered; the goal check passed (or, with no goal
                check, every tool call worked or no tool was needed);
                nothing reported missing
    partial     answered, but the goal check failed, something is
                missing, or some tool calls failed, while at least one
                tool call worked
    failed      stopped by an error, or tools were needed and none
                worked while the goal was not satisfied
    incomplete  never finished (FATMEE stopped while it ran)
    """

    if incomplete:
        return "incomplete"

    if exception is not None:
        return "failed"

    calls = data["tool_calls"]
    any_ok = any(c["ok"] for c in calls)
    skipped = any(f["category"] in ("invalid_plan", "step_skipped") for f in data["failures"])
    needed_tools = bool(calls) or skipped
    goal = data["goal_satisfied"]
    missing = bool(data["missing_information"])

    if goal is True and not missing:
        return "success"

    if goal is None and not missing and not skipped:
        if not needed_tools or all(c["ok"] for c in calls):
            return "success"

    if needed_tools and not any_ok:
        return "failed"

    return "partial"


def _cost(model: str, usage: dict | None) -> float | None:
    """USD estimate, only with real token counts and a price you set (FATMEE_LLM_PRICES)."""

    price = config.LLM_PRICES.get(model) if usage else None

    if not price:
        return None

    tokens_in, tokens_out = usage.get("input_tokens"), usage.get("output_tokens")

    if tokens_in is None or tokens_out is None:
        return None

    try:
        return round((tokens_in * float(price["input"]) + tokens_out * float(price["output"])) / 1_000_000, 6)
    except (KeyError, TypeError, ValueError):
        return None


def _usage_totals(calls: list) -> dict:
    """Token and cost totals; null when no call reported them."""

    def total(field):
        values = [c[field] for c in calls if c.get(field) is not None]
        return sum(values) if values else None

    priced = [c for c in calls if c.get("estimated_cost_usd") is not None]

    return {
        "input_tokens": total("input_tokens"),
        "output_tokens": total("output_tokens"),
        "total_tokens": total("total_tokens"),
        "llm_calls_with_usage": sum(1 for c in calls if c.get("total_tokens") is not None),
        # Only a full sum is a real estimate: null if any call is unpriced.
        "estimated_cost_usd": (
            round(sum(c["estimated_cost_usd"] for c in priced), 6)
            if calls and len(priced) == len(calls) else None
        ),
        "llm_calls_priced": len(priced),
    }


# ============================================================
# ALL TASKS
# ============================================================

class TaskLogStore:
    """Keeps open records and writes finished ones to tasks.jsonl."""

    def __init__(self, folder: str | None = None, enabled: bool | None = None):
        self.folder = folder or config.TASK_LOG_DIR
        self.path = os.path.join(self.folder, "tasks.jsonl")
        self.open_dir = os.path.join(self.folder, "open")
        self.enabled = config.TASK_LOG if enabled is None else enabled
        self.errors = 0      # logging problems so far (also in the normal log)

        self._records = {}
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()

    # ---------------- lifecycle ----------------

    @_safe
    def start(self, task_id: str, question: str, session_id: str = "") -> TaskRecord | None:

        if not self.enabled:
            return None

        record = TaskRecord(self, task_id, question, session_id)

        with self._lock:
            self._records[task_id] = record

        self._snapshot(record)

        return record

    @_safe
    def resume(self, task_id: str) -> TaskRecord | None:
        """The record of a paused task: in memory, or from its open file after a restart."""

        if not self.enabled:
            return None

        with self._lock:
            record = self._records.get(task_id)

        if record is None:
            record = TaskRecord(self, task_id)
            saved = self._read_open(task_id)

            if saved:
                saved.pop("last_stage", None)
                record.data.update(saved)
            else:
                record.data["note"] = "Started before task logging, or its open record was lost."

            with self._lock:
                self._records[task_id] = record

        return record

    @_safe
    def paused(self, task_id: str, pending: list) -> None:

        record = self._get(task_id)

        if record:
            record.approvals_requested(pending)
            self._snapshot(record)

    @_safe
    def finished(self, task_id: str, state: dict, reply: dict) -> None:

        record = self._pop(task_id)

        if record:
            self._write(record.close(state, reply), task_id)

    @_safe
    def failed(self, task_id: str, exception: BaseException, state: dict | None = None) -> None:

        record = self._pop(task_id)

        if record:
            self._write(record.close(state, None, exception=exception), task_id)

    @_safe
    def recover_incomplete(self) -> int:
        """
        At startup: tasks that were running when FATMEE stopped are
        written as "incomplete". Tasks waiting for approval stay open,
        because they can still be resumed.
        """

        if not self.enabled or not os.path.isdir(self.open_dir):
            return 0

        count = 0

        for name in sorted(os.listdir(self.open_dir)):

            if not name.endswith(".json"):
                continue

            task_id = name[:-5]
            saved = self._read_open(task_id)

            if not saved or saved.get("state") == "waiting_approval":
                continue

            record = TaskRecord(self, task_id)
            record.phase_name = saved.pop("last_stage", None)
            record.data.update(saved)
            record.data["last_stage"] = record.phase_name

            self._write(record.close(None, None, incomplete="FATMEE stopped before this task finished."), task_id)
            count += 1

        if count:
            logger.info("Task log: %d unfinished task(s) recorded as incomplete", count)

        return count

    # ---------------- files ----------------

    def _get(self, task_id: str) -> TaskRecord | None:
        with self._lock:
            return self._records.get(task_id)

    def _pop(self, task_id: str) -> TaskRecord | None:
        with self._lock:
            return self._records.pop(task_id, None)

    def _open_path(self, task_id: str) -> str:
        return os.path.join(self.open_dir, f"{re.sub(r'[^A-Za-z0-9_-]', '_', task_id)}.json")

    def _read_open(self, task_id: str) -> dict | None:

        try:
            with open(self._open_path(task_id), encoding="utf-8") as f:
                return json.load(f)

        except FileNotFoundError:
            return None

        except (OSError, ValueError):
            self.errors += 1
            logger.exception("TASK LOG ERROR: unreadable open record for %s", task_id)
            return None

    @_safe
    def _snapshot(self, record: TaskRecord) -> None:
        """Rewrite the task's open file, so a crash leaves evidence (atomic replace)."""

        os.makedirs(self.open_dir, exist_ok=True)

        path = self._open_path(record.data["task_id"])
        temp = f"{path}.{threading.get_ident()}.tmp"

        with open(temp, "w", encoding="utf-8") as f:
            json.dump(record.snapshot(), f, ensure_ascii=False, default=str)

        os.replace(temp, path)

    def _write(self, data: dict, task_id: str) -> None:
        """Append one finished record as one line, then drop its open file."""

        line = json.dumps(data, ensure_ascii=False, default=str)

        os.makedirs(self.folder, exist_ok=True)

        # One writer at a time, one complete line per write.
        with self._write_lock:
            self._append(line)

        try:
            os.remove(self._open_path(task_id))
        except FileNotFoundError:
            pass

    def _append(self, line: str) -> None:

        with open(self.path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()


# ============================================================
# CURRENT TASK (context) AND HOOKS
# ============================================================

_current = contextvars.ContextVar("fatmee_task_record", default=None)
_stage = contextvars.ContextVar("fatmee_task_stage", default="")


class _NoRecord:
    """Stands in when there is no task (logging off, or a call outside a task)."""

    def phase(self, name):
        return nullcontext()

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


_NO_RECORD = _NoRecord()


def current():
    """The record of the task running in this context (or a stand-in that does nothing)."""

    return _current.get() or _NO_RECORD


@contextmanager
def active(record: TaskRecord | None):
    """Make record the current task while the graph runs, and time it."""

    if record is None:
        yield
        return

    token = _current.set(record)
    record.begin_segment()

    try:
        yield

    finally:
        record.end_segment()
        _current.reset(token)


@contextmanager
def stage(name: str):
    """Name the work being done (e.g. "tool:create_image"), for the calls inside it."""

    token = _stage.set(name)

    try:
        yield

    finally:
        _stage.reset(token)


def traced(phase_name: str, node):
    """Wrap a graph node so its time and failures are recorded under phase_name."""

    @functools.wraps(node)
    def wrapper(*args, **kwargs):
        with current().phase(phase_name):
            return node(*args, **kwargs)

    return wrapper


def llm_call(provider, model, component, seconds, ok, usage=None, error=None) -> None:
    current().llm_call(provider, model, component or _stage.get() or "unknown", seconds, ok, usage, error)


def image_call(provider, model, seconds, ok, error=None) -> None:
    current().image_call(provider, model, seconds, ok, error)


# ============================================================
# SHARED STORE
# ============================================================

_store = None
_store_lock = threading.Lock()


def get_task_log() -> TaskLogStore:

    global _store

    with _store_lock:
        if _store is None:
            _store = TaskLogStore()
        return _store


def set_task_log(store: TaskLogStore | None) -> None:

    global _store

    with _store_lock:
        _store = store
