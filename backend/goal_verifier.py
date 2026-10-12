from google import genai
import json


class GoalVerifier:
    """
    Verifies whether the available tool results are sufficient
    to satisfy the user's goal.

    The LLM evaluates the goal dynamically based on:
    - User goal
    - Execution plan
    - Available tool results
    """

    def __init__(self, client: genai.Client):
        self.client = client

    def verify(
        self,
        goal: str,
        plan: dict,
        tool_results: dict
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

USER GOAL:
{goal}

EXECUTION PLAN:
{json.dumps(plan, indent=2)}

AVAILABLE TOOL RESULTS:
{json.dumps(tool_results, indent=2)}

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

Return ONLY valid JSON:

{{
    "goal_satisfied": true,
    "reason": "short explanation",
    "missing_information": []
}}
"""

        # ====================================================
        # CALL LLM
        # ====================================================

        response = self.client.interactions.create(
            model="gemini-3.6-flash",
            input=verification_prompt,
        )

        print("\nRAW GOAL VERIFIER RESPONSE:")
        print(response.output_text)

        # ====================================================
        # PARSE RESPONSE
        # ====================================================

        try:

            text = response.output_text.strip()

            if text.startswith("```json"):
                text = text[7:]

            if text.endswith("```"):
                text = text[:-3]

            result = json.loads(text.strip())

            # Basic validation
            if not isinstance(result, dict):
                raise ValueError("Goal verifier did not return an object.")

            if "goal_satisfied" not in result:
                raise ValueError(
                    "Goal verifier response missing goal_satisfied."
                )

            if "reason" not in result:
                result["reason"] = ""

            if "missing_information" not in result:
                result["missing_information"] = []

            return result

        except (json.JSONDecodeError, TypeError, ValueError):

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