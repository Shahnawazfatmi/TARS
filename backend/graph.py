"""
FATMEE's request flow as a LangGraph graph.

    plan -> execute -> (recover_execution) -> check_goal
         -> (recover_goal -> recheck_goal) -> answer

Each node is a small function that reads the shared state and
returns the fields it changes. The planner, verifiers and replanner
are the same components as before; the graph only wires them.
"""

import json
import logging
import operator
import uuid
from typing import Annotated, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

import config
from executor import execute_plan
from goal_verifier import GoalVerifier
from llm import LLM
from plan_check import PlanError, check_plan
from planner import Planner
from replanner import Replanner
from tools.registry import tool_specs
from verifier import Verifier


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
"""


# ============================================================
# STATE
# ============================================================

class FatmeeState(TypedDict, total=False):
    question: str
    # Recent conversation as prompt text ("" for a new conversation)
    history: str
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


def failed(result) -> bool:
    return isinstance(result, dict) and "error" in result


def has_tool_steps(plan: dict) -> bool:
    return any(step.get("tool") for step in plan.get("steps", []))


# ============================================================
# GRAPH
# ============================================================

def build_graph(llm: LLM):
    """Create FATMEE's compiled graph around one LLM."""

    planner = Planner(llm)
    replanner = Replanner(llm)
    verifier = Verifier()
    goal_verifier = GoalVerifier(llm)

    # ---------------- nodes ----------------

    def plan(state: FatmeeState) -> dict:

        specs = tool_specs()
        new_plan = planner.create_plan(state["question"], specs, state.get("history", ""))

        try:
            steps, warnings = check_plan(new_plan["steps"])
            new_plan = {**new_plan, "steps": steps}

            if warnings:
                new_plan["repairs"] = warnings

        except PlanError as e:
            # Executor reports this as a failed step, which sends
            # the request to recovery.
            logger.warning("Plan has a loop: %s", e)

        logger.info("Plan: %s", new_plan)

        return {
            "specs": specs,
            "plan": new_plan,
            "executed_steps": new_plan["steps"],
            "issues": [],
            "missing_information": [],
        }

    def execute(state: FatmeeState) -> dict:

        results = execute_plan(state["plan"], question=state["question"])
        verification = verifier.verify(state["plan"], results)

        logger.info("Verification: %s", verification)

        return {"tool_results": results, "verification": verification}

    def _recover(state: FatmeeState, goal_verification: dict) -> tuple:

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

        # Steps already run count toward per-question limits (images).
        results = execute_plan(replan, earlier_steps=state.get("executed_steps", []), question=state["question"])
        verification = verifier.verify(replan, results)

        logger.info("Recovery verification: %s", verification)

        return replan, results, verification

    def recover_execution(state: FatmeeState) -> dict:

        logger.info("Tool execution failed - replanning")

        replan, results, verification = _recover(state, {})

        # Keep failed results too: an error such as "Folder not
        # found" is often the real answer the user needs.
        return {
            "tool_results": {**state["tool_results"], **results},
            "executed_steps": replan["steps"],
            "verification": verification,
            "issues": [] if verification["verified"] else verification["errors"],
        }

    def check_goal(state: FatmeeState) -> dict:

        if not config.GOAL_CHECK or not state["tool_results"]:
            return {"goal_checked": False}

        goal_verification = goal_verifier.verify(
            state["question"],
            {"steps": state["executed_steps"]},
            state["tool_results"],
            state.get("history", ""),
        )

        logger.info("Goal verification: %s", goal_verification)

        return {"goal_checked": True, "goal_verification": goal_verification}

    def recover_goal(state: FatmeeState) -> dict:

        logger.info("Goal not satisfied - replanning")

        replan, results, verification = _recover(state, state["goal_verification"])

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
            "tool_results": {**state["tool_results"], **results},
            "executed_steps": replan["steps"],
            "issues": [] if verification["verified"] else verification["errors"],
        }

    def recheck_goal(state: FatmeeState) -> dict:

        goal_verification = goal_verifier.verify(
            state["question"],
            {"steps": state["executed_steps"]},
            state["tool_results"],
            state.get("history", ""),
        )

        logger.info("Goal verification after replanning: %s", goal_verification)

        missing = (
            [] if goal_verification["goal_satisfied"]
            else goal_verification["missing_information"]
        )

        return {"goal_verification": goal_verification, "missing_information": missing}

    def answer(state: FatmeeState) -> dict:

        results = state.get("tool_results") or {}
        history = state.get("history", "")

        # Conversational / knowledge question
        if not results:
            chat_prompt = (
                f"{history}\nUSER QUESTION:\n{state['question']}"
                if history else state["question"]
            )

            return {"response": {
                "response": llm.text(chat_prompt),
                "tools_used": [],
            }}

        prompt = f"""
{history}
USER QUESTION:
{state["question"]}

TOOL RESULTS:
{json.dumps(results, separators=(",", ":"), default=str)}
"""

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

        response = {
            "response": llm.text(prompt, system_instruction=SYSTEM_INSTRUCTION),
            "tools_used": [name for name, r in results.items() if not failed(r)],
        }

        if state.get("missing_information"):
            response["missing_information"] = state["missing_information"]

        return {"response": response}

    # ---------------- routing ----------------

    def after_execute(state: FatmeeState) -> str:
        return "check_goal" if state["verification"]["verified"] else "recover_execution"

    def after_check_goal(state: FatmeeState) -> str:
        if not state["goal_checked"] or state["goal_verification"]["goal_satisfied"]:
            return "answer"
        return "recover_goal"

    def after_recover_goal(state: FatmeeState) -> str:
        return "recheck_goal" if state["recovery_steps"] else "answer"

    # ---------------- wiring ----------------

    graph = StateGraph(FatmeeState)

    graph.add_node("plan", plan)
    graph.add_node("execute", execute)
    graph.add_node("recover_execution", recover_execution)
    graph.add_node("check_goal", check_goal)
    graph.add_node("recover_goal", recover_goal)
    graph.add_node("recheck_goal", recheck_goal)
    graph.add_node("answer", answer)

    graph.add_edge(START, "plan")
    graph.add_edge("plan", "execute")
    graph.add_conditional_edges("execute", after_execute, ["check_goal", "recover_execution"])
    graph.add_edge("recover_execution", "check_goal")
    graph.add_conditional_edges("check_goal", after_check_goal, ["answer", "recover_goal"])
    graph.add_conditional_edges("recover_goal", after_recover_goal, ["recheck_goal", "answer"])
    graph.add_edge("recheck_goal", "answer")
    graph.add_edge("answer", END)

    # The checkpointer saves each task's state under its task id.
    # In memory for now; it becomes SQLite with the Memory part, and
    # is what will let a task pause for approval and resume later.
    return graph.compile(checkpointer=InMemorySaver())


def new_task_id() -> str:
    return uuid.uuid4().hex


def run(app, question: str, history: str = "", task_id: str | None = None) -> tuple:
    """
    Run one question through the graph.

    Returns (reply, details): the reply goes to the user; details
    (goal, tools that ran, problems) fill in the diary entry.
    """

    task_id = task_id or new_task_id()
    config_ = {"configurable": {"thread_id": task_id}}

    try:
        state = app.invoke({"question": question, "history": history}, config_)

    finally:
        # Nothing waits on a finished task yet, so free its memory.
        app.checkpointer.delete_thread(task_id)

    reply = {**state["response"], "task_id": task_id}

    results = state.get("tool_results") or {}

    details = {
        "goal": state.get("plan", {}).get("goal", ""),
        "tools": [name for name, r in results.items() if not failed(r)],
        "problems": [
            *(f"{name}: {r['error']}" for name, r in results.items() if failed(r)),
            *(f"Could not get: {m}" for m in state.get("missing_information") or []),
        ],
    }

    return reply, details
