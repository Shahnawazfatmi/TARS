from google import genai
import json


class Replanner:
    """
    Creates a new recovery plan when the original plan
    cannot satisfy the user's goal.
    """

    def __init__(self, client: genai.Client):
        self.client = client

    def create_replan(
        self,
        goal: str,
        previous_plan: dict,
        tool_results: dict,
        verification: dict,
        goal_verification: dict,
        available_tools: list
    ) -> dict:

        replanner_prompt = f"""
You are the recovery planning component of FATMEE.

The original execution plan did not successfully satisfy
the user's goal.

Your job is to analyze what went wrong and create a new
plan that attempts to complete the goal.

USER GOAL:
{goal}

PREVIOUS PLAN:
{json.dumps(previous_plan, indent=2)}

TOOL RESULTS:
{json.dumps(tool_results, indent=2)}

EXECUTION VERIFICATION:
{json.dumps(verification, indent=2)}

GOAL VERIFICATION:
{json.dumps(goal_verification, indent=2)}

AVAILABLE TOOLS:
{json.dumps(available_tools, indent=2)}

RULES:
- Identify what went wrong.
- Identify what information is missing.
- Do not repeat a failed approach unless necessary.
- Use only available tools.
- Do not invent tools.
- Create the minimum number of new steps required.
- The new plan should attempt to satisfy the original goal.
- Do not answer the user's question.
- Only create executable steps.

Return ONLY valid JSON:

{{
    "reason": "short explanation of why replanning is needed",
    "steps": [
        {{
            "step": 1,
            "action": "short description",
            "tool": "tool_name_or_null"
        }}
    ]
}}
"""

        response = self.client.interactions.create(
            model="gemini-3.6-flash",
            input=replanner_prompt,
        )

        print("\nRAW REPLANNER RESPONSE:")
        print(response.output_text)

        try:
            text = response.output_text.strip()

            if text.startswith("```json"):
                text = text[7:]

            if text.endswith("```"):
                text = text[:-3]

            return json.loads(text.strip())

        except (json.JSONDecodeError, TypeError):

            return {
                "reason": "Replanning failed because the model returned invalid JSON.",
                "steps": []
            }