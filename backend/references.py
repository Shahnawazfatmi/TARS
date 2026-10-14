"""
Let a step use an earlier step's result: "$s1" or "$s1.field".

    {"tool": "list_files", "args": {"path": "$s2"}, "depends_on": ["s2"]}

The executor fills in "$s2" with step s2's result just before the step
runs. This is done in code, not by the AI, so it is exact and free.

- A value that is only a reference keeps the result's type:
  "$s1.free_gb" -> 44.4 (a number), "$s3" -> the whole result.
- A reference inside text becomes text: "Write about $s6" ->
  "Write about AI agents".
- "$" followed by a name that is not a step id is left alone, so
  "$5" or "$USD" in normal text is not touched.
"""

import json
import re


REF = re.compile(r"\$([A-Za-z][A-Za-z0-9_]*)(?:\.([A-Za-z0-9_]+))?")


class MissingInput(ValueError):
    """A referenced result is missing, failed, or has no such field."""


def find_refs(value) -> set:
    """Every step id referenced anywhere in a value (dict, list, text)."""

    if isinstance(value, str):
        return {m.group(1) for m in REF.finditer(value)}

    if isinstance(value, dict):
        return set().union(*(find_refs(v) for v in value.values())) if value else set()

    if isinstance(value, list):
        return set().union(*(find_refs(v) for v in value)) if value else set()

    return set()


def main_output(result):
    """
    The part of a result that "$s1" stands for.

    A think step returns {"output": ...}; that output is what other
    steps want. Any other result is used as it is.
    """

    if isinstance(result, dict) and set(result) == {"output"}:
        return result["output"]

    return result


def as_text(value) -> str:
    if isinstance(value, str):
        return value

    return json.dumps(value, ensure_ascii=False, default=str)


def _lookup(step_id: str, field: str | None, results: dict):

    if step_id not in results:
        raise MissingInput(f"needs the result of {step_id}, which is not available")

    result = results[step_id]

    if isinstance(result, dict) and "error" in result:
        raise MissingInput(f"needs the result of {step_id}, which failed")

    value = main_output(result)

    if field is None:
        return value

    if isinstance(value, dict) and field in value:
        return value[field]

    raise MissingInput(f"{step_id} has no field {field!r}")


def resolve(value, results: dict, step_ids: set):
    """
    Fill in every reference to a known step id.

    `results` maps step id -> result. Raises MissingInput when a needed
    result is missing or failed, so the step is skipped, not run with
    wrong input.
    """

    if isinstance(value, str):

        whole = REF.fullmatch(value.strip())

        if whole and whole.group(1) in step_ids:
            return _lookup(whole.group(1), whole.group(2), results)

        def replace(match):
            if match.group(1) not in step_ids:
                return match.group(0)

            return as_text(_lookup(match.group(1), match.group(2), results))

        return REF.sub(replace, value)

    if isinstance(value, dict):
        return {k: resolve(v, results, step_ids) for k, v in value.items()}

    if isinstance(value, list):
        return [resolve(v, results, step_ids) for v in value]

    return value
