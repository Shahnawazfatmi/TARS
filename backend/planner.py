from google import genai
from dotenv import load_dotenv
import os
import json

load_dotenv()


class Planner:
    """
    Converts a user's high-level goal into an executable plan.
    """

    def __init__(self, client: genai.Client):
        self.client = client

    def create_plan(self, goal: str, available_tools: list) -> list:

        planner_prompt = f"""
You are the planning component of FATMEE.

Your job is to decompose the user's goal into the minimum
number of executable steps required to accomplish it.

USER GOAL:
{goal}

AVAILABLE TOOLS:
{json.dumps(available_tools, indent=2)}

RULES:
- Only create steps that are necessary.
- Prefer existing tools over unnecessary actions.
- Do not invent tools.
- Keep the plan short.
- Order steps logically.
- Do not solve or answer the user's goal.
- If the goal requires information from tools, create the necessary steps.
- Only return an empty plan for genuinely conversational or knowledge questions.
- Do not execute tools.

Return ONLY valid JSON:

{{
    "goal": "short description",
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
            input=planner_prompt,
        )
        print("\nRAW PLANNER RESPONSE:")
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
                "goal": goal,
                "steps": []
            }