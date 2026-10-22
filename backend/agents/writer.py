"""
Post writer agent: writes a LinkedIn post from earlier results.

    {"id": "s2", "tool": "write_post", "depends_on": ["s1"],
     "args": {"instruction": "A post about this research for my network"}}

One AI call. The post is plain text (LinkedIn shows no markdown): a
strong first line, short paragraphs, 3-5 hashtags, and, when the
facts came from research, up to two source links. The code cleans it
and checks LinkedIn's length limit. Publishing is a separate step
that always waits for your approval.
"""

import json
import logging
import re

import config
from agents.document import collect_sources


logger = logging.getLogger("fatmee.writer")

_llm = None

# LinkedIn allows 3,000 characters; posts this long are rarely read.
MAX_CHARS = 3000
TARGET_CHARS = 1300


def configure(llm) -> None:
    """Give the agent the shared LLM (called once at startup)."""

    global _llm
    _llm = llm


def clean_post(text: str) -> str:
    """Plain text for LinkedIn: no markdown bold or headings, tidy blank lines."""

    text = (text or "").replace("\r\n", "\n")
    text = text.replace("**", "").replace("__", "")
    text = re.sub(r"^[ \t]*#{1,6}[ \t]+(?=\S)", "", text, flags=re.MULTILINE)   # "## Heading" -> "Heading"
    text = re.sub(r"[ \t]*(\[\d+\])+", "", text)        # research numbers like [1][2] don't belong in a post
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def write_post(instruction: str = "", include_sources=True, inputs: dict | None = None, user_request: str = "") -> dict:
    """Write one LinkedIn post. Returns {"text", "hashtags", "characters", ...}."""

    instruction = (instruction or "").strip()

    if not (instruction or inputs or user_request):
        return {"error": "The post writer needs to know what the post is about."}

    if _llm is None:
        return {"error": "The post writer is not configured."}

    about, sources = collect_sources(inputs or {})
    with_sources = str(include_sources).strip().lower() not in ("false", "0", "no") and bool(sources)

    text = json.dumps(about, ensure_ascii=False, default=str)

    if len(text) > config.DOCUMENT_INPUT_CHARS:
        text = text[:config.DOCUMENT_INPUT_CHARS] + " ...(shortened)"

    numbered = "\n".join(f"[{s['n']}] {s['title']} - {s['url']}" for s in sources) or "(none)"

    prompt = f"""
You are FATMEE's LinkedIn post writer. Write ONE post for the user's
personal LinkedIn profile.

THE USER'S OWN WORDS:
{user_request or "(not given)"}

REQUEST:
{instruction or "(none)"}

WHAT THE POST IS ABOUT (results of earlier steps):
{text if inputs else "(none)"}

SOURCES:
{numbered}

Return JSON only:
{{"text": "the full post", "hashtags": ["#Tag1", "#Tag2", "#Tag3"]}}

RULES:
- First line: a strong hook under 150 characters (it shows before
  "...see more"). Then short paragraphs of 1-2 sentences.
- About {TARGET_CHARS} characters or less. Plain text only: LinkedIn
  shows no markdown, so no **bold**, no # headings, no tables.
- Use only facts from the results above. No invented numbers, dates,
  quotes or names. No [1]-style source numbers in the post.
- Sound like a thoughtful professional, not an advert. At most two
  emojis, or none.
- End with one question or call to action, then 3-5 relevant hashtags
  on the last line (also list them in "hashtags").
- {"After the hashtags, add a line 'Sources:' with the one or two most important links from SOURCES (full URLs)." if with_sources else "Do not add links."}
"""

    try:
        result = _llm.json(prompt, component="post writer")
    except Exception as e:
        logger.warning("Post writing failed: %s", e)
        return {"error": "Could not write the post."}

    post = clean_post(str(result.get("text") or ""))

    if not post:
        return {"error": "The post came out empty."}

    if len(post) > MAX_CHARS:
        post = post[:MAX_CHARS - 3].rsplit(" ", 1)[0] + "..."

    hashtags = [str(h).strip() for h in (result.get("hashtags") or []) if str(h).strip()][:5]

    return {
        "text": post,
        "hashtags": hashtags,
        "characters": len(post),
        "platform": "linkedin",
        "sources": [s["url"] for s in sources][:2] if with_sources else [],
    }
