import json
import logging

from llm import LLM
from planner import PLAN_FORMAT_RULES, normalize_steps


logger = logging.getLogger("fatmee.replanner")


class Replanner:
    """
    Creates a new recovery plan when the original plan
    cannot satisfy the user's goal.
    """

    def __init__(self, llm: LLM):
        self.llm = llm

    def create_replan(
        self,
        goal: str,
        previous_plan: dict,
        tool_results: dict,
        verification: dict,
        goal_verification: dict,
        available_tools: list,
        history: str = "",
    ) -> dict:

        replanner_prompt = f"""
You are the recovery planning component of FATMEE.

The original execution plan did not successfully satisfy
the user's goal.

Your job is to analyze what went wrong and create a new
plan that attempts to complete the goal.

{history}
USER GOAL:
{goal}

PREVIOUS PLAN:
{json.dumps(previous_plan, separators=(",", ":"))}

TOOL RESULTS:
{json.dumps(tool_results, separators=(",", ":"), default=str)}

EXECUTION VERIFICATION:
{json.dumps(verification, separators=(",", ":"))}

GOAL VERIFICATION:
{json.dumps(goal_verification, separators=(",", ":"))}

AVAILABLE TOOLS (name, description, optional parameters):
{json.dumps(available_tools, indent=1)}

RULES:
- Identify what went wrong.
- Identify what information is missing.
- Do not repeat a failed approach unless necessary.
- Use only available tools.
- Do not invent tools.
- Create the minimum number of new steps required.
- The new plan should attempt to satisfy the original goal.
{PLAN_FORMAT_RULES.format(prefix="r")}
- Only add a step if its tool can actually provide the missing
  information. If no available tool can, return an empty "steps"
  list and say so in "reason". Never add unrelated tools.
- If a tool error is itself the answer (e.g. "Folder not found"
  when the user asked about that folder), return an empty "steps"
  list.
- Do not answer the user's question.
- Only create executable steps.

Return ONLY valid JSON:

{{
    "reason": "short explanation of why replanning is needed",
    "steps": [
        {{
            "id": "r1",
            "step": 1,
            "action": "short description",
            "tool": "tool_name_or_null",
            "args": {{}},
            "depends_on": []
        }}
    ]
}}
"""

        try:
            result = self.llm.json(replanner_prompt, component="replanner")

        except ValueError:
            logger.warning("Replanner returned invalid JSON.")

            return {
                "reason": "Replanning failed because the model returned invalid JSON.",
                "steps": []
            }

        return {
            "reason": str(result.get("reason", "")),
            "steps": normalize_steps(result.get("steps"), id_prefix="r"),
        }
