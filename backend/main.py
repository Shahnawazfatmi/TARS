from fastapi import FastAPI
from pydantic import BaseModel
from google import genai
from dotenv import load_dotenv

from planner import Planner
from replanner import Replanner
from tools.registry import tool_registry
from verifier import Verifier
from goal_verifier import GoalVerifier

import os
import json


# ============================================================
# CONFIGURATION
# ============================================================

load_dotenv()

app = FastAPI(title="FATMEE")

client = genai.Client(
    api_key=os.getenv("GOOGLE_API_KEY")
)

planner = Planner(client)
replanner = Replanner(client)
verifier = Verifier()
goal_verifier = GoalVerifier(client)


# ============================================================
# REQUEST MODEL
# ============================================================

class Question(BaseModel):
    question: str


# ============================================================
# HOME
# ============================================================

@app.get("/")
def home():
    return {
        "status": "online",
        "assistant": "FATMEE"
    }


# ============================================================
# ASK
# ============================================================

@app.post("/ask")
def ask(data: Question):

    # ========================================================
    # TOOLS AVAILABLE TO FATMEE
    # ========================================================

    tools = [

        {
            "type": "function",
            "name": "get_system_info",
            "description": (
                "Get basic information about the user's computer, "
                "such as operating system, version, and architecture."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },

        {
            "type": "function",
            "name": "get_current_directory",
            "description": (
                "Get the current working directory of FATMEE."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },

        {
            "type": "function",
            "name": "list_files",
            "description": (
                "List files and folders inside the current working directory."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },

        {
            "type": "function",
            "name": "get_cpu_usage",
            "description": (
                "Get the current CPU usage percentage."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },

        {
            "type": "function",
            "name": "get_ram_usage",
            "description": (
                "Get current RAM usage including total, used, "
                "available memory and usage percentage."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },

        {
            "type": "function",
            "name": "get_disk_usage",
            "description": (
                "Get disk usage including total, used and free space."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },

        {
            "type": "function",
            "name": "get_hardware_info",
            "description": (
                "Get computer hardware information including CPU, "
                "CPU cores, RAM, GPU and system architecture."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    ]


    # ========================================================
    # 1. CREATE INITIAL PLAN
    # ========================================================

    plan = planner.create_plan(
        data.question,
        [tool["name"] for tool in tools]
    )

    print("\nPLAN:")
    print(plan)


    # ========================================================
    # 2. EXTRACT TOOLS FROM PLAN
    # ========================================================

    planned_tools = [
        step["tool"]
        for step in plan.get("steps", [])
        if step.get("tool")
    ]

    print("\nPLANNED TOOLS:")
    print(planned_tools)


    # ========================================================
    # 3. EXECUTE INITIAL PLAN
    # ========================================================

    tool_results = {}

    for tool_name in planned_tools:

        if tool_name not in tool_registry:
            print(f"WARNING: Tool not found: {tool_name}")
            continue

        try:

            result = tool_registry[tool_name]()

            tool_results[tool_name] = result

            print(f"\nEXECUTED: {tool_name}")
            print(result)

        except Exception as e:

            tool_results[tool_name] = {
                "error": str(e)
            }

            print(f"\nERROR executing {tool_name}: {e}")


    # ========================================================
    # 4. VERIFY TOOL EXECUTION
    # ========================================================

    verification = verifier.verify(
        plan,
        tool_results
    )

    print("\nVERIFICATION:")
    print(verification)


    # ========================================================
    # 4A. REPLAN IF TOOL EXECUTION FAILED
    # ========================================================

    if not verification["verified"]:

        print("\nREPLANNING...")

        replan = replanner.create_replan(
            goal=data.question,
            previous_plan=plan,
            tool_results=tool_results,
            verification=verification,
            goal_verification={},
            available_tools=[tool["name"] for tool in tools]
        )

        print("\nREPLAN:")
        print(replan)

        replan_results = {}

        for step in replan.get("steps", []):

            tool_name = step.get("tool")

            if not tool_name:
                continue

            if tool_name not in tool_registry:
                continue

            try:

                result = tool_registry[tool_name]()

                replan_results[tool_name] = result

                print(f"\nREPLANNED TOOL EXECUTED: {tool_name}")
                print(result)

            except Exception as e:

                replan_results[tool_name] = {
                    "error": str(e)
                }

        replan_verification = verifier.verify(
            replan,
            replan_results
        )

        print("\nREPLAN VERIFICATION:")
        print(replan_verification)

        if not replan_verification["verified"]:

            return {
                "response": (
                    "FATMEE could not complete the task "
                    "after replanning."
                ),
                "verification": replan_verification
            }

        # Add successful recovery results
        tool_results.update(replan_results)


    # ========================================================
    # 5. VERIFY USER GOAL
    # ========================================================

    goal_verification = goal_verifier.verify(
        data.question,
        plan,
        tool_results
    )

    print("\nGOAL VERIFICATION:")
    print(goal_verification)


    # ========================================================
    # 5A. REPLAN IF USER GOAL IS NOT SATISFIED
    # ========================================================

    if not goal_verification["goal_satisfied"]:

        print("\nGOAL NOT SATISFIED - REPLANNING...")

        replan = replanner.create_replan(
            goal=data.question,
            previous_plan=plan,
            tool_results=tool_results,
            verification=verification,
            goal_verification=goal_verification,
            available_tools=[tool["name"] for tool in tools]
        )

        print("\nGOAL RECOVERY PLAN:")
        print(replan)

        replan_results = {}

        for step in replan.get("steps", []):

            tool_name = step.get("tool")

            if not tool_name:
                continue

            if tool_name not in tool_registry:
                print(f"WARNING: Recovery tool not found: {tool_name}")
                continue

            try:

                result = tool_registry[tool_name]()

                replan_results[tool_name] = result

                print(f"\nRECOVERY TOOL EXECUTED: {tool_name}")
                print(result)

            except Exception as e:

                replan_results[tool_name] = {
                    "error": str(e)
                }

                print(f"\nERROR executing recovery tool: {e}")


        # ----------------------------------------------------
        # VERIFY RECOVERY TOOL EXECUTION
        # ----------------------------------------------------

        replan_verification = verifier.verify(
            replan,
            replan_results
        )

        print("\nRECOVERY VERIFICATION:")
        print(replan_verification)


        if not replan_verification["verified"]:

            return {
                "response": (
                    "FATMEE could not complete the task "
                    "after recovery."
                ),
                "verification": replan_verification,
                "goal_verification": goal_verification
            }


        # ----------------------------------------------------
        # ADD RECOVERY RESULTS
        # ----------------------------------------------------

        tool_results.update(replan_results)


        # ----------------------------------------------------
        # VERIFY USER GOAL AGAIN
        # ----------------------------------------------------

        goal_verification = goal_verifier.verify(
            data.question,
            plan,
            tool_results
        )

        print("\nGOAL VERIFICATION AFTER REPLANNING:")
        print(goal_verification)


        if not goal_verification["goal_satisfied"]:

            return {
                "response": (
                    "FATMEE still does not have enough "
                    "information to complete the task."
                ),
                "verification": replan_verification,
                "goal_verification": goal_verification
            }


    # ========================================================
    # 6. CONVERSATIONAL / KNOWLEDGE QUESTION
    # ========================================================

    if not tool_results:

        interaction = client.interactions.create(
            model="gemini-3.6-flash",
            input=data.question,
        )

        return {
            "response": interaction.output_text
        }


    # ========================================================
    # 7. ANALYZE TOOL RESULTS WITH GEMINI
    # ========================================================

    system_instruction = """
You are FATMEE, a desktop AI assistant.

Answer the user's question using the provided tool results.

Rules:

1. Use the tool results for computer-related information.
2. Do not invent computer specifications.
3. Do not make unsupported performance claims.
4. If the available information is insufficient, say so.
5. Keep the answer concise and useful.
6. Explain the result naturally instead of dumping raw JSON.
"""


    analysis_prompt = f"""
USER QUESTION:
{data.question}

PLANNED EXECUTION:
{json.dumps(plan, indent=2)}

TOOL RESULTS:
{json.dumps(tool_results, indent=2)}

Now provide the final answer to the user.
"""


    interaction = client.interactions.create(
        model="gemini-3.6-flash",
        system_instruction=system_instruction,
        input=analysis_prompt,
    )


    # ========================================================
    # 8. FINAL RESPONSE
    # ========================================================

    return {
        "response": interaction.output_text
    }