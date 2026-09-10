from google import genai
from planner import Planner
from dotenv import load_dotenv
import os

load_dotenv()

client = genai.Client(
    api_key=os.getenv("GOOGLE_API_KEY")
)

planner = Planner(client)

tools = [
    "get_hardware_info",
    "get_cpu_usage",
    "get_ram_usage",
    "get_disk_usage",
]

goal = "Check whether my computer can run a 7B local LLM."

plan = planner.create_plan(goal, tools)

print("\nGenerated Plan:\n")
print(plan)