import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor

import config
from plan_check import PlanError, check_plan, execution_waves
from planner import normalize_steps
from references import MissingInput, find_refs, resolve
from tools.registry import requires_approval, run_tool, takes_inputs


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

    return (step["tool"], json.dumps(args, sort_keys=True, default=str))


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

    start = time.perf_counter()

    try:
        result = run_tool(step["tool"], step["args"])

    except Exception as e:
        logger.warning("Tool %s failed: %s", step["tool"], e)
        result = {"error": str(e)}

    # Earlier results given to think / create_document can be long,
    # so the log shows only which steps they came from.
    shown = dict(step["args"])

    if isinstance(shown.get("inputs"), dict):
        shown["inputs"] = f"<results of {', '.join(shown['inputs']) or 'no steps'}>"

    logger.info(
        "Tool %s%s finished in %.2fs",
        step["tool"],
        f" {shown}" if shown else "",
        time.perf_counter() - start,
    )

    return result


def execute_plan(plan: dict) -> dict:
    """
    Run the plan's tool steps in dependency order.

    Steps in the same wave run in parallel; a step starts only after
    every step it depends on has finished.
    """

    # Gives ids and depends_on to plans that were not made by the
    # planner (normalizing an already-normal plan changes nothing).
    steps = normalize_steps(plan.get("steps", []))

    if not any(step.get("tool") for step in steps):
        return {}

    try:
        steps, _ = check_plan(steps)
        waves = execution_waves(steps)

    except PlanError as e:
        logger.warning("Plan cannot run: %s", e)
        return {PLAN_ERROR_KEY: {"error": str(e)}}

    # Same keys the verifier expects; duplicate steps share a key.
    unique = tool_steps({"steps": steps})
    key_by_dedupe = {
        _dedupe_key(step): key
        for step, key in zip(unique, result_keys(unique))
    }

    results = {}
    by_id = {}            # step id -> result, for "$s1" references
    key_of = {}           # step id -> result key
    step_ids = {step["id"] for step in steps}

    for number, wave in enumerate(waves, start=1):

        batch = []

        for step in wave:

            if not step.get("tool"):
                continue

            key = key_by_dedupe[_dedupe_key(step)]
            key_of[step["id"]] = key

            if key in results or key in (k for k, _ in batch):
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
                # think / create_document work on the results they wait for.
                needed = set(step.get("depends_on", [])) | (find_refs(step.get("args") or {}) & step_ids)
                args = {**args, "inputs": {sid: by_id[sid] for sid in sorted(needed) if sid in by_id}}

            if requires_approval(step["tool"]):
                # Approval pausing arrives with the first tool that
                # needs it (LinkedIn / Gmail). Until then, never run.
                results[key] = {
                    "error": "This step needs your approval, which FATMEE cannot ask for yet.",
                    "needs_approval": True,
                }
                continue

            batch.append((key, {"id": step["id"], "tool": step["tool"], "args": args}))

        if not batch:
            continue

        logger.info("Wave %d: %s", number, [key for key, _ in batch])

        futures = [(key, step["id"], _pool.submit(_run, step)) for key, step in batch]

        for key, step_id, future in futures:
            results[key] = future.result()
            by_id[step_id] = results[key]

        # Duplicate steps share the first step's result.
        for step_id, key in key_of.items():
            if step_id not in by_id and key in results:
                by_id[step_id] = results[key]

    return results
