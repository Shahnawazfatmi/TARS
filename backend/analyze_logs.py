"""
Summarize FATMEE's task logs (backend/logs/tasks.jsonl).

    python analyze_logs.py                       # the default log file
    python analyze_logs.py path/to/tasks.jsonl   # another file
    python analyze_logs.py --json                # machine-readable

Only totals, rates, tool names and failure categories are shown, never
questions, answers, arguments or results. Broken lines and missing
fields are counted and skipped, not fatal.
"""

import argparse
import json
import os
import statistics
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config  # noqa: E402


def load(path: str) -> tuple:
    """(records, malformed line count). A missing file is just empty."""

    records, malformed = [], 0

    if not os.path.exists(path):
        return records, malformed

    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:

            if not line.strip():
                continue

            try:
                record = json.loads(line)
            except ValueError:
                malformed += 1
                continue

            if isinstance(record, dict):
                records.append(record)
            else:
                malformed += 1

    return records, malformed


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _list(value) -> list:
    return value if isinstance(value, list) else []


def _average(values: list):
    return round(statistics.fmean(values), 3) if values else None


def _median(values: list):
    return round(statistics.median(values), 3) if values else None


def summarize(records: list, malformed: int = 0) -> dict:

    statuses = Counter()
    categories = Counter()
    failing_tools = Counter()
    active, wall, tool_calls, recoveries = [], [], [], []
    checked = satisfied = executed = executed_ok = 0
    tokens = {"input_tokens": [], "output_tokens": [], "total_tokens": []}
    costs = []
    llm_calls = image_calls = 0

    for record in records:

        statuses[record.get("task_status") if record.get("task_status") in
                 ("success", "partial", "failed", "incomplete") else "unknown"] += 1

        if record.get("goal_satisfied") in (True, False):
            checked += 1
            satisfied += record["goal_satisfied"] is True

        if record.get("execution_success") in (True, False):
            executed += 1
            executed_ok += record["execution_success"] is True

        for category in set(_list(record.get("failure_categories"))):
            if isinstance(category, str):
                categories[category] += 1

        for call in _list(record.get("tool_calls")):
            if isinstance(call, dict) and call.get("ok") is False and isinstance(call.get("tool"), str):
                failing_tools[call["tool"]] += 1

        timings = _dict(record.get("timings"))

        for values, field in ((active, "active_s"), (wall, "wall_s")):
            if _number(timings.get(field)) is not None:
                values.append(timings[field])

        counts = _dict(record.get("counts"))

        if _number(counts.get("tool_calls")) is not None:
            tool_calls.append(counts["tool_calls"])

        if _number(counts.get("recovery_attempts")) is not None:
            recoveries.append(counts["recovery_attempts"])

        llm_calls += _number(counts.get("llm_calls")) or 0
        image_calls += _number(counts.get("image_calls")) or 0

        usage = _dict(record.get("usage"))

        for field, values in tokens.items():
            if _number(usage.get(field)) is not None:
                values.append(usage[field])

        if _number(usage.get("estimated_cost_usd")) is not None:
            costs.append(usage["estimated_cost_usd"])

    return {
        "log_file_tasks": len(records),
        "malformed_lines": malformed,
        "task_status": {s: statuses.get(s, 0) for s in ("success", "partial", "failed", "incomplete", "unknown")},
        "goal_satisfaction_rate": round(satisfied / checked, 3) if checked else None,
        "tasks_with_goal_check": checked,
        "execution_success_rate": round(executed_ok / executed, 3) if executed else None,
        "top_failure_categories": categories.most_common(10),
        "top_failing_tools": failing_tools.most_common(10),
        "active_latency_s": {"average": _average(active), "median": _median(active)},
        "wall_latency_s": {"average": _average(wall), "median": _median(wall)},
        "average_tool_calls": _average(tool_calls),
        "average_recovery_attempts": _average(recoveries),
        "llm_calls": llm_calls,
        "image_calls": image_calls,
        "tokens": {
            field: {"total": sum(values) if values else None, "tasks_reporting": len(values)}
            for field, values in tokens.items()
        },
        "estimated_cost_usd": {
            "total": round(sum(costs), 6) if costs else None,
            "tasks_fully_priced": len(costs),
        },
    }


def format_summary(summary: dict, path: str) -> str:

    def show(value):
        return "n/a" if value is None else value

    lines = [
        f"FATMEE task log: {path}",
        f"Tasks: {summary['log_file_tasks']}   (malformed lines skipped: {summary['malformed_lines']})",
        "Status: " + ", ".join(f"{k} {v}" for k, v in summary["task_status"].items() if v or k != "unknown"),
        f"Goal satisfied: {show(summary['goal_satisfaction_rate'])} "
        f"(of {summary['tasks_with_goal_check']} tasks with a goal check)",
        f"Execution success: {show(summary['execution_success_rate'])}",
        f"Latency (active)  avg {show(summary['active_latency_s']['average'])} s, "
        f"median {show(summary['active_latency_s']['median'])} s",
        f"Latency (wall)    avg {show(summary['wall_latency_s']['average'])} s, "
        f"median {show(summary['wall_latency_s']['median'])} s",
        f"Avg tool calls: {show(summary['average_tool_calls'])}   "
        f"avg recovery attempts: {show(summary['average_recovery_attempts'])}",
        f"LLM calls: {summary['llm_calls']}   image calls: {summary['image_calls']}",
        "Tokens: " + ", ".join(
            f"{field} {show(t['total'])} ({t['tasks_reporting']} tasks)" for field, t in summary["tokens"].items()
        ),
        f"Estimated cost: {show(summary['estimated_cost_usd']['total'])} USD "
        f"({summary['estimated_cost_usd']['tasks_fully_priced']} fully priced tasks)",
        "",
        "Top failure categories:",
        *([f"  {name}: {count}" for name, count in summary["top_failure_categories"]] or ["  none"]),
        "Top failing tools:",
        *([f"  {name}: {count}" for name, count in summary["top_failing_tools"]] or ["  none"]),
    ]

    return "\n".join(lines)


def main(argv=None) -> int:

    parser = argparse.ArgumentParser(description="Summarize FATMEE's task logs.")
    parser.add_argument("path", nargs="?", default=os.path.join(config.TASK_LOG_DIR, "tasks.jsonl"))
    parser.add_argument("--json", action="store_true", help="print the summary as JSON")
    args = parser.parse_args(argv)

    records, malformed = load(args.path)
    summary = summarize(records, malformed)

    print(json.dumps(summary, indent=2) if args.json else format_summary(summary, args.path))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
