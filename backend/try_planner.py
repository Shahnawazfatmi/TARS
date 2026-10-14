"""
Manual check: send one goal to the real planner and print the plan.

Run from the backend folder:  python try_planner.py "your goal"
"""

import json
import logging
import sys

from llm import LLM
from planner import Planner
from tools.registry import tool_specs


logging.basicConfig(level=logging.INFO)

goal = " ".join(sys.argv[1:]) or "Check whether my computer can run a 7B local LLM."

planner = Planner(LLM.from_config())

plan = planner.create_plan(goal, tool_specs())

print("\nGenerated Plan:\n")
print(json.dumps(plan, indent=2))
