"""
Research agent: web search (through MCP) + a short, sourced brief.

    research_topic("AI agents in 2026")
      1. search the web with the connected MCP search tool (Tavily)
      2. the AI writes key points from those results only, each
         pointing to its numbered source
      3. returns the points and the source links

It is registered as the tool "research_topic", so the planner can run
several topics at once (one per step, same wave).
"""

import json
import logging
import re
from datetime import datetime

import config
from mcp_client import get_manager


logger = logging.getLogger("fatmee.research")

NOT_SET_UP = (
    "Web search is not set up. Add TAVILY_API_KEY to backend/.env "
    "and restart FATMEE."
)

_TIME_RANGES = {"day", "week", "month", "year"}

_llm = None


def configure(llm) -> None:
    """Give the agent the shared LLM (called once at startup)."""

    global _llm
    _llm = llm


def extract_sources(text: str) -> list:
    """Find title + URL pairs in search results (any common layout)."""

    sources = []
    seen = set()
    title = ""

    for line in text.splitlines():
        line = line.strip()

        match = re.match(r"(?i)title:\s*(.+)", line)
        if match:
            title = match.group(1).strip()
            continue

        for url in re.findall(r"https?://[^\s)\]\"'>,]+", line):
            url = url.rstrip(".")
            if url not in seen:
                seen.add(url)
                sources.append({"title": title or url, "url": url})
                title = ""

    return sources


def parse_results(raw: str, max_sources: int) -> list:
    """
    Turn a search answer into [{"title", "url", "content"}].

    Tavily answers in JSON ({"results": [...]}); other search servers
    may answer in text, which is parsed line by line instead.
    """

    try:
        data = json.loads(raw)

    except (json.JSONDecodeError, TypeError):
        data = None

    if isinstance(data, dict) and isinstance(data.get("results"), list):
        results = []

        for item in data["results"]:
            if isinstance(item, dict) and item.get("url"):
                results.append({
                    "title": str(item.get("title") or item["url"]).strip(),
                    "url": str(item["url"]).strip(),
                    "content": str(item.get("content") or "").strip(),
                })

        return results[:max_sources]

    return [{**source, "content": ""} for source in extract_sources(raw)[:max_sources]]


def _search(manager, topic: str, max_sources: int, time_range: str) -> str:

    name = manager.find("search")

    if name is None:
        raise LookupError(NOT_SET_UP)

    args = {"query": topic, "max_results": max_sources}

    if time_range:
        args["time_range"] = time_range

    # Unsupported arguments are dropped by manager.call().
    return manager.call(name, args)


def research_topic(topic: str = "", max_sources: int = 0, time_range: str = "") -> dict:
    """Research one topic on the web and return a sourced brief."""

    topic = (topic or "").strip()

    if not topic:
        return {"error": "No topic given."}

    manager = get_manager()

    if manager is None or manager.find("search") is None:
        return {"error": NOT_SET_UP}

    if _llm is None:
        return {"error": "Research agent is not configured."}

    max_sources = max(1, min(int(max_sources or config.RESEARCH_SOURCES), 10))
    time_range = (time_range or "").strip().lower()
    time_range = time_range if time_range in _TIME_RANGES else ""

    try:
        raw = _search(manager, topic, max_sources, time_range)

    except LookupError as e:
        return {"error": str(e)}

    except Exception as e:
        logger.warning("Web search failed for %r: %s", topic, type(e).__name__)
        return {"error": f"Web search failed ({type(e).__name__}). Check the internet connection and Tavily key."}

    results = parse_results(raw, max_sources)

    if not results:
        return {"topic": topic, "error": "The web search found nothing for this topic."}

    sources = [{"title": r["title"], "url": r["url"]} for r in results]

    numbered = "\n".join(f"[{i}] {s['title']} - {s['url']}" for i, s in enumerate(sources, start=1))

    if any(r["content"] for r in results):
        # Only the useful text, numbered, split fairly between sources:
        # a smaller prompt is cheaper and faster than the raw answer.
        share = config.RESEARCH_INPUT_CHARS // len(results)
        evidence = "\n\n".join(
            f"[{i}] {r['title']}\n{r['content'][:share]}"
            for i, r in enumerate(results, start=1)
        )

    else:
        evidence = raw[:config.RESEARCH_INPUT_CHARS]

    prompt = f"""
You are FATMEE's research agent. Today is {datetime.now().strftime('%Y-%m-%d')}.

TOPIC:
{topic}

NUMBERED SOURCES:
{numbered}

SEARCH RESULTS:
{evidence}

RULES:
- Use ONLY the search results above. Do not add outside knowledge.
- Write 3 to 6 key points. Each point is one or two sentences and
  ends with the source numbers it comes from, like [1] or [2][3].
- Prefer recent, specific facts (names, numbers, dates).
- If the results disagree, say so.
- If the results do not really cover the topic, say that in "summary".

Return ONLY valid JSON:

{{
    "summary": "one or two sentence overview",
    "key_points": ["point ending with [1]", "point ending with [2][3]"]
}}
"""

    try:
        brief = _llm.json(prompt, component="research")

    except ValueError:
        return {"topic": topic, "error": "Could not summarize the search results.", "sources": sources}

    points = brief.get("key_points") or []

    if not isinstance(points, list):
        points = [str(points)]

    return {
        "topic": topic,
        "summary": str(brief.get("summary", "")),
        "key_points": [str(p) for p in points][:8],
        "sources": [{"n": i, **s} for i, s in enumerate(sources, start=1)],
        "searched_at": datetime.now().isoformat(timespec="minutes"),
    }
