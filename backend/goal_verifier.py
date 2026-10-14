import json
import logging

from llm import LLM


logger = logging.getLogger("fatmee.goal_verifier")


def _as_bool(value) -> bool:
    """Read true/false safely; the string "false" must not count as True."""

    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1")

    return bool(value)


class GoalVerifier:
    """
    Verifies whether the available tool results are sufficient
    to satisfy the user's goal.

    The LLM evaluates the goal dynamically based on:
    - User goal
    - Executed steps
    - Available tool results
    """

    def __init__(self, llm: LLM):
        self.llm = llm

    def verify(
        self,
        goal: str,
        plan: dict,
        tool_results: dict,
        history: str = "",
    ) -> dict:

        # ====================================================
        # NO TOOL RESULTS
        # ====================================================

        if not tool_results:

            return {
                "goal_satisfied": True,
                "reason": (
                    "No tool results are required; "
                    "the goal can be handled conversationally."
                ),
                "missing_information": []
            }

        # ====================================================
        # GOAL VERIFICATION PROMPT
        # ====================================================

        verification_prompt = f"""
You are the Goal Verification component of FATMEE.

Your job is to determine whether the user's goal can be
properly answered using the information currently available.

{history}
USER GOAL:
{goal}

EXECUTED STEPS:
{json.dumps(plan.get("steps", []), separators=(",", ":"))}

AVAILABLE TOOL RESULTS:
{json.dumps(tool_results, separators=(",", ":"), default=str)}

RULES:

1. Understand what information is actually required to satisfy
   the user's goal.

2. Check whether the available tool results contain that
   information.

3. Do not require information that is unnecessary for the goal.

4. Do not assume that a specific tool must be called if the
   required information is already available from another tool.

5. Do not invent missing information.

6. If the available information is sufficient, return
   goal_satisfied as true.

7. If important information is missing, return
   goal_satisfied as false and clearly identify what is missing.

8. Do not decide to execute tools yourself.
   Your job is only to verify the goal.

9. A negative result is a complete answer. If a tool shows that
   something does not exist, is not installed, is not connected,
   or has zero matches, the goal IS satisfied: the answer is "no".

Return ONLY valid JSON:

{{
    "goal_satisfied": true,
    "reason": "short explanation",
    "missing_information": []
}}
"""

        try:
            result = self.llm.json(verification_prompt, component="goal_verifier")

            if "goal_satisfied" not in result:
                raise ValueError("Goal verifier response missing goal_satisfied.")

        except ValueError as e:
            logger.warning("Goal verifier returned invalid output: %s", e)

            return {
                "goal_satisfied": False,
                "reason": (
                    "Goal verification failed because "
                    "the verifier returned invalid output."
                ),
                "missing_information": [
                    "valid goal verification result"
                ]
            }

        missing = result.get("missing_information") or []

        if not isinstance(missing, list):
            missing = [str(missing)]

        return {
            "goal_satisfied": _as_bool(result["goal_satisfied"]),
            "reason": str(result.get("reason", "")),
            "missing_information": missing,
        }
