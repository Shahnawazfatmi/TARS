"""
FATMEE's request flow as a LangGraph graph.

    plan -> execute -> (recover_execution) -> check_goal
         -> (recover_goal -> recheck_goal) -> answer

    execute -> wait_for_approval -> execute   (a step needs your OK)

A step that needs approval pauses the whole task: its state is saved
in SQLite (backend/data/checkpoints.db), so it survives a restart, and
the task continues when you approve or reject (resume()). Steps that
already ran are reused, never run twice.

Each node is a small function that reads the shared state and
returns the fields it changes. The planner, verifiers and replanner
are the same components as before; the graph only wires them.
"""

import json
import logging
import operator
import os
import sqlite3
import time
import uuid
from typing import Annotated, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

import cards
import config
import task_logger
from executor import execute_plan, run_plan
from goal_verifier import GoalVerifier, settled_text
from llm import LLM, LLMError
from plan_check import PlanError, check_plan
from planner import Planner
from progress import get_progress
from replanner import Replanner
from tools.registry import tool_specs
from verifier import Verifier


# Graph nodes receive LangGraph's run settings as `config`, which hides
# the config module inside them, so FATMEE's settings get this name.
settings = config


logger = logging.getLogger("fatmee.graph")


SYSTEM_INSTRUCTION = """
You are FATMEE, a desktop AI assistant.

Answer the user's question using the provided tool results.

Rules:

1. Use the tool results for computer-related information.
2. Do not invent computer specifications.
3. Do not make unsupported performance claims.
4. If the available information is insufficient, say so.
5. Keep the answer concise and useful.
6. Explain the result naturally instead of dumping raw JSON.
7. A tool error such as "Folder not found" is a real answer:
   tell the user plainly what it means.
8. If some information could not be obtained, answer what you
   can, then say clearly what FATMEE cannot check yet.
9. When results come from web research, give the key findings
   (3-5 short points) with the source number after each fact
   (like [1]), and end with a "Sources" list of the numbered titles
   and links that you used. Do this even when a document or image
   was also made. If several topics were researched, number each
   topic's sources separately.
10. When a file was saved (a document or an image), say its file
    name and the folder it is in. Do not repeat the whole document.
11. When the user approved something that was then saved, posted or
    sent, quote its text exactly as the result shows it. Never
    rewrite or improve it: the user must see what really went out.
    If the user rejected a step, say plainly that it was not done.
12. If a result says "dry_run": true, say plainly that this was a
    dry run (test mode) and nothing was published or sent, and give
    the result's note on how to switch it off. This is the expected
    outcome, not a failure.
13. When a file was read, answer from it: give the answer to the
    user's question (or the summary and key points), with page
    numbers. If the result has a "note" (only part of a long file
    was read) or a "warning", say so in one line.
"""

# Answers with no tool results: from the model's own knowledge.
KNOWLEDGE_INSTRUCTION = """
You are FATMEE, a desktop AI assistant. Answer from your own knowledge,
clearly and concisely. If the answer depends on recent events or facts
that may have changed since you learned them, say so in one short line
and offer to search the web.
"""


# ============================================================
# STATE
# ============================================================

class FatmeeState(TypedDict, total=False):
    question: str
    # Recent conversation as prompt text ("" for a new conversation)
    history: str
    # Kept so a paused task can be finished later (diary, conversation)
    session_id: str
    started_at: str
    specs: list
    plan: dict
    # Every step that ran, across the plan and recovery plans
    executed_steps: Annotated[list, operator.add]
    tool_results: dict
    verification: dict
    goal_verification: dict
    goal_checked: bool
    recovery_steps: bool
    issues: list
    missing_information: list
    response: dict
    # {step id: result} of steps that already ran, reused after a pause
    step_results: dict
    # Steps waiting for the user's OK
    pending: list
    # The user's decisions: {step id: {"decision": ..., "changes": ...}}
    approvals: dict


def failed(result) -> bool:
    return isinstance(result, dict) and "error" in result


def has_tool_steps(plan: dict) -> bool:
    return any(step.get("tool") for step in plan.get("steps", []))


def merge_results(earlier: dict, new: dict) -> dict:
    """
    Add a recovery plan's results without replacing earlier ones: a
    second research_topic becomes research_topic#2, so the first
    research (or "you rejected it") is still there for the answer.
    """

    merged = dict(earlier)

    for key, result in new.items():

        if key in merged:
            tool = key.split("#")[0]
            number = 2

            while f"{tool}#{number}" in merged:
                number += 1

            key = f"{tool}#{number}"

        merged[key] = result

    return merged


def plain_answer(results: dict) -> str:
    """
    The results as they are, for when no AI model can write the answer
    after tools already ran (e.g. a post went out): the user must
    still learn what happened.
    """

    lines = ["FATMEE could not reach an AI model to write the answer. Here is what happened:", ""]

    for name, result in results.items():

        if failed(result):
            lines.append(f"- {name}: not done ({result['error']})")
            continue

        text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
        lines.append(f"- {name}: {text[:800]}{'…' if len(text) > 800 else ''}")

    return "\n".join(lines)


def _reporter(config_: RunnableConfig):
    """Live progress for the task this node is running."""

    return get_progress().reporter(config_["configurable"]["thread_id"])


def make_checkpointer():
    """Saved task state in SQLite, so a paused task survives a restart."""

    from langgraph.checkpoint.sqlite import SqliteSaver

    os.makedirs(os.path.dirname(os.path.abspath(config.CHECKPOINT_DB_PATH)), exist_ok=True)

    # One connection shared by all requests; SqliteSaver locks it itself.
    connection = sqlite3.connect(config.CHECKPOINT_DB_PATH, check_same_thread=False)

    return SqliteSaver(connection)


# ============================================================
# GRAPH
# ============================================================

def build_graph(llm: LLM, checkpointer=None):
    """Create FATMEE's compiled graph around one LLM (state saved in SQLite by default)."""

    planner = Planner(llm)
    replanner = Replanner(llm)
    verifier = Verifier()
    goal_verifier = GoalVerifier(llm)

    # ---------------- nodes ----------------

    def plan(state: FatmeeState, config: RunnableConfig) -> dict:

        report = _reporter(config)
        report("planning", "Planning the steps")

        specs = tool_specs()
        new_plan = planner.create_plan(state["question"], specs, state.get("history", ""))
        plan_error = None

        try:
            steps, warnings = check_plan(new_plan["steps"])
            new_plan = {**new_plan, "steps": steps}

            if warnings:
                new_plan["repairs"] = warnings

        except PlanError as e:
            # Executor reports this as a failed step, which sends
            # the request to recovery.
            logger.warning("Plan has a loop: %s", e)
            plan_error = str(e)

        logger.info("Plan: %s", new_plan)
        task_logger.current().plan(new_plan, plan_error)

        planned = [
            {"id": step.get("id"), "tool": step["tool"], "action": step.get("action", ""),
             "needs_approval": bool(step.get("needs_approval"))}
            for step in new_plan.get("steps", []) if step.get("tool")
        ]
        report(
            "planned",
            f"Plan: {', '.join(step['tool'] for step in planned) or 'no tools needed'}",
            {"steps": planned},
        )

        return {
            "specs": specs,
            "plan": new_plan,
            "executed_steps": new_plan["steps"],
            "issues": [],
            "missing_information": [],
        }

    def execute(state: FatmeeState, config: RunnableConfig) -> dict:

        run = run_plan(
            state["plan"],
            question=state["question"],
            done=state.get("step_results"),
            approvals=state.get("approvals"),
            on_event=_reporter(config),
        )

        update = {"tool_results": run["results"], "step_results": run["by_id"], "pending": run["pending"]}

        if run["pending"]:
            # Some steps wait for the user's OK: verify after they ran.
            logger.info("Waiting for approval: %s", [p["step_id"] for p in run["pending"]])
            return update

        verification = verifier.verify(state["plan"], run["results"])

        logger.info("Verification: %s", verification)
        task_logger.current().verification(verification, "execution")

        return {**update, "verification": verification}

    def wait_for_approval(state: FatmeeState, config: RunnableConfig) -> dict:

        # The task stops here and is saved. resume() continues it with
        # the user's decisions as the value of interrupt().
        decisions = interrupt({"pending": state["pending"]})

        _reporter(config)("approved", "Got your decision, continuing", {"decisions": decisions})
        task_logger.current().approvals_decided(decisions)

        return {"approvals": {**(state.get("approvals") or {}), **(decisions or {})}, "pending": []}

    def _recover(state: FatmeeState, goal_verification: dict, config_: RunnableConfig) -> tuple:

        report = _reporter(config_)
        report("replanning", "Something is missing: trying another way")

        clock = time.perf_counter()

        replan = replanner.create_replan(
            goal=state["question"],
            previous_plan=state["plan"],
            tool_results=state["tool_results"],
            verification=state["verification"],
            goal_verification=goal_verification,
            available_tools=state["specs"],
            history=state.get("history", ""),
        )

        logger.info("Recovery plan: %s", replan)

        replanner_s = time.perf_counter() - clock
        clock = time.perf_counter()

        # Steps already run count toward per-question limits (images).
        # Recovery plans never pause for approval. They can use the
        # first plan's results ("$s1"), and never redo a step the user
        # rejected.
        results = execute_plan(
            replan,
            earlier_steps=state.get("executed_steps", []),
            question=state["question"],
            allow_approval=False,
            on_event=report,
            known=state.get("step_results") or {},
        )
        verification = verifier.verify(replan, results)

        logger.info("Recovery verification: %s", verification)

        if goal_verification:
            trigger, previous = "goal_not_satisfied", {
                "reason": goal_verification.get("reason", ""),
                "missing_information": goal_verification.get("missing_information", []),
            }
        else:
            trigger, previous = "execution_failed", {"errors": state["verification"].get("errors", [])}

        trace = task_logger.current()
        trace.replan(trigger, previous, replan, replanner_s, results, verification, time.perf_counter() - clock)

        if has_tool_steps(replan):
            trace.verification(verification, "recovery")

        return replan, results, verification

    def recover_execution(state: FatmeeState, config: RunnableConfig) -> dict:

        logger.info("Tool execution failed - replanning")

        replan, results, verification = _recover(state, {}, config)

        # Keep failed results too: an error such as "Folder not
        # found" is often the real answer the user needs.
        return {
            "tool_results": merge_results(state["tool_results"], results),
            "executed_steps": replan["steps"],
            "verification": verification,
            "issues": [] if verification["verified"] else verification["errors"],
        }

    def check_goal(state: FatmeeState, config: RunnableConfig) -> dict:

        if not settings.GOAL_CHECK or not state["tool_results"]:
            return {"goal_checked": False}

        _reporter(config)("checking", "Checking the results answer your question")

        clock = time.perf_counter()

        try:
            goal_verification = goal_verifier.verify(
                state["question"],
                {"steps": state["executed_steps"]},
                state["tool_results"],
                state.get("history", ""),
            )

        except LLMError as e:
            # Tools already ran (maybe a post went out): answer with
            # what is known instead of failing the whole task.
            logger.warning("Goal check skipped, no AI model reachable: %s", e)
            task_logger.current().failure("llm_error", "goal_check", str(e))
            return {"goal_checked": False}

        logger.info("Goal verification: %s", goal_verification)
        task_logger.current().goal_check(goal_verification, time.perf_counter() - clock, "execution")

        return {"goal_checked": True, "goal_verification": goal_verification}

    def recover_goal(state: FatmeeState, config: RunnableConfig) -> dict:

        logger.info("Goal not satisfied - replanning")

        replan, results, verification = _recover(state, state["goal_verification"], config)

        if not has_tool_steps(replan):

            # No available tool can supply what is missing, so
            # checking the goal again would change nothing.
            logger.info("No tool can provide the missing information")

            return {
                "recovery_steps": False,
                "missing_information": state["goal_verification"]["missing_information"],
            }

        return {
            "recovery_steps": True,
            "tool_results": merge_results(state["tool_results"], results),
            "executed_steps": replan["steps"],
            "issues": [] if verification["verified"] else verification["errors"],
        }

    def recheck_goal(state: FatmeeState) -> dict:

        clock = time.perf_counter()

        try:
            goal_verification = goal_verifier.verify(
                state["question"],
                {"steps": state["executed_steps"]},
                state["tool_results"],
                state.get("history", ""),
            )

        except LLMError as e:
            logger.warning("Goal re-check skipped, no AI model reachable: %s", e)
            task_logger.current().failure("llm_error", "goal_check", str(e))
            return {"missing_information": []}

        logger.info("Goal verification after replanning: %s", goal_verification)
        task_logger.current().goal_check(goal_verification, time.perf_counter() - clock, "recovery")

        missing = (
            [] if goal_verification["goal_satisfied"]
            else goal_verification["missing_information"]
        )

        return {"goal_verification": goal_verification, "missing_information": missing}

    def answer(state: FatmeeState, config: RunnableConfig) -> dict:

        _reporter(config)("answering", "Writing the answer")

        results = state.get("tool_results") or {}
        history = state.get("history", "")

        # Conversational / knowledge question
        if not results:
            chat_prompt = (
                f"{history}\nUSER QUESTION:\n{state['question']}"
                if history else state["question"]
            )

            return {"response": {
                "response": llm.text(chat_prompt, system_instruction=KNOWLEDGE_INSTRUCTION, component="answer"),
                "tools_used": [],
            }}

        prompt = f"""
{history}
USER QUESTION:
{state["question"]}

TOOL RESULTS:
{json.dumps(results, separators=(",", ":"), default=str)}
"""

        # Steps the user rejected or ran in dry run: say so plainly (rules 11, 12).
        prompt += settled_text(results)

        if state.get("issues"):
            prompt += f"""
TOOL PROBLEMS:
{json.dumps(state["issues"])}
"""

        if state.get("missing_information"):
            prompt += f"""
INFORMATION FATMEE COULD NOT GET (no tool provides it):
{json.dumps(state["missing_information"])}
"""

        if any(isinstance(r, dict) and r.get("sources") for r in results.values()):
            # Models tend to drop the research when a file was also
            # made, so rule 9 is repeated right next to the results.
            prompt += (
                "\nThe results include web research: give its key findings "
                "with source numbers like [1] and end with a Sources list "
                "(rule 9), even if a document or image was also made.\n"
            )

        prompt += "\nNow provide the final answer to the user.\n"

        try:
            text = llm.text(prompt, system_instruction=SYSTEM_INSTRUCTION, component="answer")

        except LLMError as e:
            logger.warning("Answer written without AI, no model reachable: %s", e)
            task_logger.current().failure("llm_error", "answer", str(e))
            text = plain_answer(results)

        response = {
            "response": text,
            "tools_used": [name for name, r in results.items() if not failed(r)],
        }

        if state.get("missing_information"):
            response["missing_information"] = state["missing_information"]

        return {"response": response}

    # ---------------- routing ----------------

    def after_execute(state: FatmeeState) -> str:
        if state.get("pending"):
            return "wait_for_approval"
        return "check_goal" if state["verification"]["verified"] else "recover_execution"

    def after_check_goal(state: FatmeeState) -> str:
        if not state["goal_checked"] or state["goal_verification"]["goal_satisfied"]:
            return "answer"
        return "recover_goal"

    def after_recover_goal(state: FatmeeState) -> str:
        return "recheck_goal" if state["recovery_steps"] else "answer"

    # ---------------- wiring ----------------

    graph = StateGraph(FatmeeState)

    # traced(): each node's time and failures go into the task log,
    # under its stage name. The node itself is unchanged.
    trace = task_logger.traced

    graph.add_node("plan", trace("planning", plan))
    graph.add_node("execute", trace("execution", execute))
    graph.add_node("wait_for_approval", trace("approval", wait_for_approval))
    graph.add_node("recover_execution", trace("recovery", recover_execution))
    graph.add_node("check_goal", trace("goal_check", check_goal))
    graph.add_node("recover_goal", trace("recovery", recover_goal))
    graph.add_node("recheck_goal", trace("goal_check", recheck_goal))
    graph.add_node("answer", trace("answer", answer))

    graph.add_edge(START, "plan")
    graph.add_edge("plan", "execute")
    graph.add_conditional_edges("execute", after_execute, ["check_goal", "recover_execution", "wait_for_approval"])
    graph.add_edge("wait_for_approval", "execute")
    graph.add_edge("recover_execution", "check_goal")
    graph.add_conditional_edges("check_goal", after_check_goal, ["answer", "recover_goal"])
    graph.add_conditional_edges("recover_goal", after_recover_goal, ["recheck_goal", "answer"])
    graph.add_edge("recheck_goal", "answer")
    graph.add_edge("answer", END)

    # The checkpointer saves each task's state under its task id, so a
    # task can pause for approval and resume later, even after a restart.
    return graph.compile(checkpointer=checkpointer or make_checkpointer())


def new_task_id() -> str:
    return uuid.uuid4().hex


def _config(task_id: str) -> dict:
    return {"configurable": {"thread_id": task_id}}


def waiting_message(task_id: str, pending: list) -> str:
    """What FATMEE says when it stops for your OK (no AI call needed)."""

    lines = [f"I need your OK before I continue ({len(pending)} step{'s' if len(pending) != 1 else ''}):", ""]

    for number, item in enumerate(pending, start=1):
        lines.append(f"{number}. {item['tool']}: {item.get('action') or 'run this step'}")

        for name, value in item["args"].items():
            text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
            lines.append(f"   {name}: {text[:500]}{'...' if len(text) > 500 else ''}")

    lines += [
        "",
        f'Approve or reject it: POST /approvals/{task_id} with {{"decision": "approve"}} or {{"decision": "reject"}}.',
    ]

    return "\n".join(lines)


def _details(state: dict) -> dict:
    """Goal, tools that ran and problems, for the diary entry."""

    results = state.get("tool_results") or {}

    return {
        "goal": state.get("plan", {}).get("goal", ""),
        "tools": [name for name, r in results.items() if not failed(r)],
        "problems": [
            *(f"{name}: {r['error']}" for name, r in results.items() if failed(r)),
            *(f"Could not get: {m}" for m in state.get("missing_information") or []),
        ],
    }


def _finish(app, task_id: str, state: dict) -> tuple:
    """Turn the graph's output into (reply, details), paused or done."""

    report = get_progress().reporter(task_id)
    interrupts = state.get("__interrupt__")

    if interrupts:
        # Paused: keep the saved state until the user decides.
        pending = interrupts[0].value["pending"]
        report("waiting_approval", "Waiting for your OK", {"pending": pending})
        task_logger.get_task_log().paused(task_id, pending)

        results = state.get("tool_results") or {}

        reply = {
            "status": "waiting_approval",
            "response": waiting_message(task_id, pending),
            "approvals": pending,
            "tools_used": [name for name, r in results.items() if not failed(r)],
            "task_id": task_id,
            "cards": cards.task_cards(results),
        }

        return reply, _details(state)

    # Finished: nothing waits on this task any more, so free its state.
    app.checkpointer.delete_thread(task_id)
    report("done", "Done")

    reply = {
        **state["response"], "status": "done", "task_id": task_id,
        "cards": cards.task_cards(state.get("tool_results")),
    }
    task_logger.get_task_log().finished(task_id, state, reply)

    return reply, _details(state)


def run(
    app,
    question: str,
    history: str = "",
    task_id: str | None = None,
    session_id: str = "default",
    started_at: str = "",
) -> tuple:
    """
    Run one question through the graph.

    Returns (reply, details): the reply goes to the user; details
    (goal, tools that ran, problems) fill in the diary entry. When a
    step needs approval, reply["status"] is "waiting_approval" and the
    task waits for resume().
    """

    task_id = task_id or new_task_id()
    task_log = task_logger.get_task_log()
    record = task_log.start(task_id, question, session_id)

    try:
        with task_logger.active(record):
            state = app.invoke(
                {"question": question, "history": history, "session_id": session_id, "started_at": started_at},
                _config(task_id),
            )

    except Exception as e:
        get_progress().emit(task_id, "failed", "Something went wrong")
        task_log.failed(task_id, e)
        app.checkpointer.delete_thread(task_id)
        raise

    return _finish(app, task_id, state)


def waiting_task(app, task_id: str) -> dict | None:
    """A paused task's question, session and waiting steps, or None if it isn't waiting."""

    snapshot = app.get_state(_config(task_id))

    if "wait_for_approval" not in (snapshot.next or ()):
        return None

    values = snapshot.values

    return {
        "task_id": task_id,
        "question": values.get("question", ""),
        "session_id": values.get("session_id", "default"),
        "started_at": values.get("started_at", ""),
        "pending": values.get("pending") or [],
    }


def resume(app, task_id: str, decisions: dict) -> tuple:
    """
    Continue a paused task with the user's decisions:
    {step id: {"decision": "approve" | "reject", "changes": {...}}}.
    """

    task_log = task_logger.get_task_log()
    record = task_log.resume(task_id)

    try:
        with task_logger.active(record):
            state = app.invoke(Command(resume=decisions), _config(task_id))

    except Exception as e:
        get_progress().emit(task_id, "failed", "Something went wrong")
        task_log.failed(task_id, e)
        app.checkpointer.delete_thread(task_id)
        raise

    return _finish(app, task_id, state)
