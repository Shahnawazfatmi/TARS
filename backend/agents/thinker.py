"""
The "think" step: the AI works on results of earlier steps.

    {"id": "s6", "tool": "think", "depends_on": ["s1", "s2", "s3"],
     "args": {"instruction": "Pick the best topic. Return only the topic."}}

It reads the results of the steps it waits for and does one job with
them: compare, choose, shortlist, summarize or write. Its answer can
then be used by later steps as "$s6".
"""

import json
import logging

import config


logger = logging.getLogger("fatmee.thinker")

_llm = None


def configure(llm) -> None:
    """Give the step the shared LLM (called once at startup)."""

    global _llm
    _llm = llm


def think(instruction: str = "", inputs: dict | None = None) -> dict:
    """Do one reasoning job on earlier results. Returns {"output": text}."""

    instruction = (instruction or "").strip()

    if not instruction:
        return {"error": "The think step needs an instruction."}

    if _llm is None:
        return {"error": "The think step is not configured."}

    text = json.dumps(inputs or {}, ensure_ascii=False, default=str)

    if len(text) > config.THINK_INPUT_CHARS:
        text = text[:config.THINK_INPUT_CHARS] + " ...(shortened)"

    prompt = f"""
You are FATMEE's thinking step. You work only with the results of
earlier steps, shown below by step id.

INSTRUCTION:
{instruction}

RESULTS OF EARLIER STEPS:
{text}

RULES:
- Use only these results. Do not invent facts.
- Do exactly what the instruction asks, nothing more.
- If the instruction says "return only" something, return only that,
  with no extra words, quotes or explanation, because a later step
  will use your answer directly.
- If the results are not enough to do the job, say so in one sentence.
"""

    output = _llm.text(prompt).strip()

    return {"output": output}
