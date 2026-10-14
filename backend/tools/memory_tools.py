"""Tools that read FATMEE's own memory (read-only)."""

from memory import EPISODE_STATUSES, get_episode_store, resolve_period


# Characters of each answer shown to the model; short rows keep the
# prompt small, so the final answer comes back faster.
_SUMMARY_IN_RESULT = 150


def recall_tasks(
    day: str = "",
    days_back: int = 0,
    keyword: str = "",
    status: str = "",
    limit: int = 20,
) -> dict:
    """Look up past tasks in FATMEE's diary."""

    try:
        start, end, label = resolve_period(day, days_back)

    except ValueError as e:
        return {"error": str(e)}

    status = (status or "").strip().lower()

    if status and status not in EPISODE_STATUSES:
        return {"error": f"Unknown status {status!r}. Use one of: {', '.join(EPISODE_STATUSES)}."}

    limit = max(1, min(int(limit), 50))

    entries, total = get_episode_store().search(
        start=start,
        end=end,
        keyword=keyword,
        status=status,
        limit=limit,
    )

    tasks = []

    for e in entries:
        summary = e["summary"] or ""

        if len(summary) > _SUMMARY_IN_RESULT:
            summary = summary[:_SUMMARY_IN_RESULT].rstrip() + "..."

        task = {
            "when": e["started_at"].replace("T", " ")[:16],
            "question": e["question"],
            "status": e["status"],
            "tools": e["tools"],
            "summary": summary,
        }

        if e["problems"]:
            task["problems"] = e["problems"]

        tasks.append(task)

    return {
        "period": label,
        "found": total,
        "shown": len(tasks),
        "tasks": tasks,
    }
