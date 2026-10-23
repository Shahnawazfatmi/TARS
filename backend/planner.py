import json
import logging
from datetime import datetime

from llm import LLM, LLMError
from tools.registry import requires_approval


logger = logging.getLogger("fatmee.planner")


def normalize_steps(raw_steps, id_prefix: str = "s") -> list:
    """
    Keep only well-formed steps so later code can trust the shape.

    Every step gets an id, a depends_on list and a needs_approval
    flag. needs_approval comes from the tool registry, never from
    the model, so the AI cannot skip an approval.
    """

    steps = []
    used_ids = set()

    if not isinstance(raw_steps, list):
        return steps

    for i, step in enumerate(raw_steps, start=1):

        if not isinstance(step, dict):
            continue

        tool = step.get("tool")

        if not isinstance(tool, str) or tool.lower() in ("", "null", "none"):
            tool = None

        args = step.get("args")

        step_id = str(step.get("id") or "").strip() or f"{id_prefix}{i}"

        while step_id in used_ids:
            step_id += "_"

        used_ids.add(step_id)

        deps = step.get("depends_on") or []

        if not isinstance(deps, list):
            deps = [deps]

        steps.append({
            "id": step_id,
            "step": step.get("step", i),
            "action": str(step.get("action", "")),
            "tool": tool,
            "args": args if isinstance(args, dict) else {},
            "depends_on": [str(d) for d in deps if isinstance(d, (str, int))],
            "needs_approval": requires_approval(tool),
        })

    return steps


PLAN_FORMAT_RULES = """- Give every step an id: "{prefix}1", "{prefix}2", ...
- In "depends_on", list the ids of the steps whose results this step
  needs. A step that needs nothing from other steps gets [].
- Steps that do not need each other must NOT depend on each other,
  so FATMEE can run them at the same time.
- Put tool parameters in "args". Use {{}} when the tool needs none.
- To use an earlier step's result as an input, write "${prefix}1" (the
  whole result) or "${prefix}1.field" (one field) inside "args", and
  list that id in "depends_on".
- Use the "think" tool to compare, choose, shortlist, summarize or
  write something from earlier results. It sees the results of the
  steps in its "depends_on". If a later step needs its answer as an
  input, end the instruction with "Return only ...".
- Do not add a think step just to answer the user; FATMEE writes the
  final answer itself.
- For a Word document or report, use "create_document" and put the
  steps whose results go in it in its "depends_on". It writes the
  content itself, so do not add a think step to write it first.
- Tools marked "needs_approval" pause and ask the user before they
  run. Plan them normally, with everything they need ready in "args"
  (e.g. "$s2" from a think step that wrote the text), so the user
  sees the real content when asked.
- For a picture, use "create_image" only when the user asks for an
  image or for a LinkedIn post. Put the user's picture details in
  "description". If the picture should show earlier results, list
  those steps in its "depends_on". One create_image step makes one
  picture.
- For a LinkedIn post: research first if the topic needs facts, then
  "write_post" and "create_image" (both depending on the research, so
  they run at the same time; leave out the image only if the user says
  no image), then publish with "linkedin_create_post": text
  "$<write_post id>.text", image_path "$<create_image id>.path",
  alt_text "$<create_image id>.concept". It waits for the user's
  approval. If "linkedin_create_post" is not in the tool list, or the
  user asks for a practice post, use "practice_post" the same way
  (text, image_path).
- Add the publishing step only when the user asks to post, publish or
  share on LinkedIn. To "write" or "draft" a post, use write_post only
  (and create_image if they want a picture)."""


class Planner:
    """
    Converts a user's high-level goal into an executable plan.
    """

    def __init__(self, llm: LLM):
        self.llm = llm

    def create_plan(self, goal: str, available_tools: list, history: str = "") -> dict:

        planner_prompt = f"""
You are the planning component of FATMEE.

Your job is to decompose the user's goal into the minimum
number of executable steps required to accomplish it.

TODAY: {datetime.now().strftime('%A %Y-%m-%d, %H:%M')} (user's local time)

{history}
USER GOAL:
{goal}

AVAILABLE TOOLS (name, description, optional parameters):
{json.dumps(available_tools, indent=1)}

RULES:
- Only create steps that are necessary.
- Prefer existing tools over unnecessary actions.
- Do not invent tools.
- Keep the plan short.
- Order steps logically.
- Do not solve or answer the user's goal.
- If the goal requires information from tools, create the necessary steps.
- Return an empty plan ("steps": []) when FATMEE can answer from its own
  knowledge, with no tool: explanations, definitions, concepts, how-to,
  advice, ideas, writing or rewriting text, math, code help and casual
  chat. Example: "What is an LLM?" or "Explain RAG simply" needs no steps.
- Use "research_topic" only when the answer depends on recent or
  current information (news, "latest", "this week", prices, releases,
  events, a company's or person's current details, statistics), or when
  the user asks to search, research, look something up or give sources.
- Questions about this computer still need the PC tools, and questions
  about earlier tasks need "recall_tasks".
{PLAN_FORMAT_RULES.format(prefix="s")}
- Do not execute tools.

Return ONLY valid JSON:

{{
    "goal": "short description",
    "steps": [
        {{
            "id": "s1",
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
            result = self.llm.json(planner_prompt, component="planner")

        except ValueError as e:
            # A broken plan must not silently turn into a chat answer
            # that guesses computer specs.
            raise LLMError(f"Planner returned invalid JSON: {e}") from e

        return {
            "goal": str(result.get("goal") or goal),
            "steps": normalize_steps(result.get("steps")),
        }
