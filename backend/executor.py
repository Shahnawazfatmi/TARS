import contextvars
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import cards
import config
import task_logger
from plan_check import PlanError, check_plan, execution_waves
from planner import normalize_steps
from references import MissingInput, find_refs, resolve
from tools.registry import max_per_task, requires_approval, run_tool, takes_inputs


logger = logging.getLogger("fatmee.executor")

_pool = ThreadPoolExecutor(
    max_workers=config.TOOL_WORKERS,
    thread_name_prefix="fatmee-tool",
)

# Result key used when a plan cannot be run at all.
PLAN_ERROR_KEY = "plan_check"

def _dedupe_key(step: dict) -> tuple:
    args = step.get("args") or {}

    if not isinstance(args, dict):
        args = {}

    key = (step["tool"], json.dumps(args, sort_keys=True, default=str))

    if takes_inputs(step["tool"]):
        # Its real input is the results it waits for: the same
        # instruction over different steps is different work.
        key += (tuple(sorted(str(dep) for dep in step.get("depends_on") or [])),)

    return key


def tool_steps(plan: dict) -> list:
    """Steps that call a tool, with duplicates (same tool + args) removed."""

    seen = set()
    steps = []

    for step in plan.get("steps", []):

        if not step.get("tool"):
            continue

        key = _dedupe_key(step)

        if key in seen:
            continue

        seen.add(key)
        steps.append({
            "id": step.get("id"),
            "tool": step["tool"],
            "args": step.get("args") if isinstance(step.get("args"), dict) else {},
            "depends_on": step.get("depends_on") or [],
        })

    return steps


def result_keys(steps: list) -> list:
    """
    Name each step's result.

    A tool used once is keyed by its name. A tool used more than
    once (e.g. list_files on two folders) gets #2, #3...
    """

    counts = {}
    keys = []

    for step in steps:
        tool = step["tool"]
        counts[tool] = counts.get(tool, 0) + 1
        keys.append(tool if counts[tool] == 1 else f"{tool}#{counts[tool]}")

    return keys


def _run(step: dict):

    started_at = datetime.now().isoformat(timespec="milliseconds")
    start = time.perf_counter()
    exception = None

    try:
        # AI calls made inside the tool are logged under its name.
        with task_logger.stage(f"tool:{step['tool']}"):
            result = run_tool(step["tool"], step["args"])

    except Exception as e:
        logger.warning("Tool %s failed: %s", step["tool"], e)
        result = {"error": str(e)}
        exception = e

    # Earlier results given to think / create_document can be long,
    # so the log shows only which steps they came from.
    shown = dict(step["args"])

    if isinstance(shown.get("inputs"), dict):
        shown["inputs"] = f"<results of {', '.join(shown['inputs']) or 'no steps'}>"

    shown.pop("user_request", None)

    seconds = time.perf_counter() - start

    logger.info(
        "Tool %s%s finished in %.2fs",
        step["tool"],
        f" {shown}" if shown else "",
        seconds,
    )

    task_logger.current().tool_call(step, result, seconds, started_at, exception)

    return result, seconds


def over_limit_steps(steps: list, earlier_steps: list | None = None) -> dict:
    """
    Steps that would run a tool more often than its max_per_task.

    Steps already run for this question (earlier_steps, e.g. before a
    recovery plan) count too, so replanning cannot get around the
    limit. Returns {step id: limit}.
    """

    used = {}

    for step in earlier_steps or []:
        tool = step.get("tool")

        if tool and max_per_task(tool):
            used[tool] = used.get(tool, 0) + 1

    over = {}
    seen = set()

    for step in steps:
        tool = step.get("tool")
        limit = max_per_task(tool)

        if not limit:
            continue

        key = _dedupe_key(step)

        if key in seen:        # a duplicate shares the first step's result
            continue

        seen.add(key)
        used[tool] = used.get(tool, 0) + 1

        if used[tool] > limit:
            over[step["id"]] = limit

    return over


def _no_event(kind: str, message: str, data: dict) -> None:
    pass


def _preview_args(args: dict) -> dict:
    """What the user sees before approving: the real inputs, without internals."""

    return {k: v for k, v in args.items() if k not in ("inputs", "user_request")}


def run_plan(
    plan: dict,
    earlier_steps: list | None = None,
    question: str = "",
    done: dict | None = None,
    approvals: dict | None = None,
    allow_approval: bool = True,
    on_event=None,
    known: dict | None = None,
) -> dict:
    """
    Run the plan's tool steps in dependency order.

    Steps in the same wave run in parallel; a step starts only after
    every step it depends on has finished.

    - earlier_steps: steps already run for this question (for
      per-question limits).
    - known: results of earlier plans' steps ({step id: result}), e.g.
      the first plan's for a recovery plan. Its steps can use them
      ("$s1", depends_on), and a tool the user rejected there is never
      run again.
    - question: the user's own words, given to tools that take inputs
      (as "user_request"; tools without that parameter ignore it).
    - done: results of steps that already ran ({step id: result}).
      They are reused, never run again, e.g. after an approval pause.
    - approvals: the user's decisions ({step id: {"decision":
      "approve" | "reject", "changes": {...}}}).
    - allow_approval: False for recovery plans, which never pause.
    - on_event(kind, message, data): live progress for the user.

    A step that needs approval and has no decision yet is not run: it
    is returned in "pending", and the steps that need it wait too.

    Returns {"results": {result key: result}, "by_id": {step id:
    result}, "pending": [steps waiting for approval]}.
    """

    on_event = on_event or _no_event
    done = done or {}
    approvals = approvals or {}
    known = known or {}

    # Gives ids and depends_on to plans that were not made by the
    # planner (normalizing an already-normal plan changes nothing).
    steps = normalize_steps(plan.get("steps", []))

    if not any(step.get("tool") for step in steps):
        return {"results": {}, "by_id": {}, "pending": []}

    try:
        steps, _ = check_plan(steps, known)
        waves = execution_waves(steps, known)

    except PlanError as e:
        logger.warning("Plan cannot run: %s", e)
        return {"results": {PLAN_ERROR_KEY: {"error": str(e)}}, "by_id": {}, "pending": []}

    # Same keys the verifier expects; duplicate steps share a key.
    unique = tool_steps({"steps": steps})
    key_by_dedupe = {
        _dedupe_key(step): key
        for step, key in zip(unique, result_keys(unique))
    }

    over_limit = over_limit_steps(steps, earlier_steps)

    results = {}
    by_id = dict(known)   # step id -> result, for "$s1" references
    key_of = {}           # step id -> result key
    step_ids = {step["id"] for step in steps} | set(known)
    pending = []          # steps waiting for the user's OK
    pending_keys = set()  # their result keys (an identical step waits with them)
    blocked = set()       # ids of pending steps and the steps that need them

    # ids of steps the user said no to, and the steps that need them
    rejected = {sid for sid, result in known.items() if isinstance(result, dict) and result.get("rejected")}

    # The user's "no" stands for this whole question: never run that tool again.
    refused_tools = {
        step.get("tool") for step in earlier_steps or []
        if step.get("id") in rejected and step.get("tool")
    }

    for number, wave in enumerate(waves, start=1):

        batch = []

        for step in wave:

            if not step.get("tool"):
                continue

            key = key_by_dedupe[_dedupe_key(step)]
            key_of[step["id"]] = key

            if step["id"] in done:
                # Already ran before a pause: reuse, never run twice.
                results[key] = by_id[step["id"]] = done[step["id"]]
                continue

            if key in results or key in (k for k, _ in batch):
                continue

            if key in pending_keys:
                # Identical to a step already waiting for approval:
                # one decision covers both.
                blocked.add(step["id"])
                continue

            needs = set(step.get("depends_on", [])) | (find_refs(step.get("args") or {}) & step_ids)

            if needs & blocked:
                # Waits for a step that waits for approval.
                blocked.add(step["id"])
                continue

            if step["tool"] in refused_tools:
                results[key] = by_id[step["id"]] = {
                    "error": f"Not run: you rejected {step['tool']} earlier for this question.",
                    "rejected": True,
                }
                rejected.add(step["id"])
                continue

            if needs & rejected:
                # Never run something built on a step the user said no to.
                said_no = sorted(needs & rejected)[0]
                results[key] = by_id[step["id"]] = {
                    "error": f"Skipped: this step needs {said_no}, which you rejected.",
                    "rejected": True,
                }
                rejected.add(step["id"])
                continue

            if step["id"] in over_limit:
                limit = over_limit[step["id"]]
                results[key] = {
                    "error": f"Skipped: FATMEE runs {step['tool']} at most {limit} "
                             f"time{'s' if limit != 1 else ''} per question.",
                    "limit_reached": True,
                }
                by_id[step["id"]] = results[key]
                continue

            # Fill in "$s1"-style references with earlier results. A
            # step whose input failed is skipped, not run with bad input.
            try:
                args = resolve(step.get("args") or {}, by_id, step_ids)

            except MissingInput as e:
                results[key] = {"error": f"Skipped: this step {e}."}
                by_id[step["id"]] = results[key]
                continue

            if takes_inputs(step["tool"]):
                # think / create_document / create_image work on the
                # results they wait for.
                args = {**args, "inputs": {sid: by_id[sid] for sid in sorted(needs) if sid in by_id}}

                if question:
                    args["user_request"] = question

            if requires_approval(step["tool"]):

                if not allow_approval:
                    results[key] = by_id[step["id"]] = {
                        "error": "This step needs your approval, and FATMEE asks for "
                                 "approval only in the first plan. Ask again to try it.",
                    }
                    continue

                decision = approvals.get(step["id"])

                if decision is None:
                    # Pause here: the user decides, then the plan continues.
                    preview = {
                        "step_id": step["id"],
                        "tool": step["tool"],
                        "action": step.get("action", ""),
                        "args": _preview_args(args),
                    }
                    pending.append(preview)
                    pending_keys.add(key)
                    blocked.add(step["id"])
                    on_event("approval_needed", f"Waiting for your OK: {step['tool']}", preview)
                    continue

                if decision.get("decision") != "approve":
                    results[key] = by_id[step["id"]] = {
                        "error": "Not run: you rejected this step.",
                        "rejected": True,
                    }
                    rejected.add(step["id"])
                    on_event("step", f"{step['tool']} rejected by you", {"id": step["id"], "tool": step["tool"], "ok": False})
                    continue

                # Approved, possibly with the user's edits (e.g. new post text).
                changes = decision.get("changes") or {}

                if changes:
                    args = {**args, **changes}

            batch.append((key, {"id": step["id"], "tool": step["tool"], "args": args}))

        if not batch:
            continue

        logger.info("Wave %d: %s", number, [key for key, _ in batch])
        on_event(
            "wave",
            f"Wave {number}: {', '.join(step['tool'] for _, step in batch)}",
            {"wave": number, "steps": [{"id": step["id"], "tool": step["tool"]} for _, step in batch]},
        )

        # Each tool thread gets a copy of this context, so it reports
        # into this task's log record.
        futures = [
            (key, step, _pool.submit(contextvars.copy_context().run, _run, step))
            for key, step in batch
        ]

        for key, step, future in futures:
            result, seconds = future.result()
            results[key] = by_id[step["id"]] = result
            ok = not (isinstance(result, dict) and "error" in result)
            on_event(
                "step",
                f"{step['tool']} {'finished' if ok else 'failed'} in {seconds:.1f}s",
                {
                    "id": step["id"], "tool": step["tool"], "ok": ok, "seconds": round(seconds, 2),
                    "card": cards.card(step["tool"], result),
                    "error": None if ok else str(result.get("error", ""))[:300],
                },
            )

        # Duplicate steps share the first step's result.
        for step_id, key in key_of.items():
            if step_id not in by_id and key in results:
                by_id[step_id] = results[key]

    return {"results": results, "by_id": by_id, "pending": pending}


def execute_plan(plan: dict, earlier_steps: list | None = None, question: str = "", **kwargs) -> dict:
    """run_plan, returning only {result key: result} (for code that needs just the results)."""

    return run_plan(plan, earlier_steps, question, **kwargs)["results"]
