"""
Result cards for the screen: a small, safe summary of what one tool
produced (a picture, a document, a post, research with sources).

The executor adds a card to each finished step's progress event, and
the final reply carries the cards of the whole task, so the screen
can show previews without reading raw tool results.

Files: a card may point to a file FATMEE made. The screen can only
fetch files FATMEE made in this run, or files in FATMEE's own output
folders (servable), never any other file on the PC.

To give a new tool a card: add one function to _BUILDERS.
"""

import threading
from pathlib import Path

import config
from agents import files


_TEXT_CHARS = 1200

# Files FATMEE made since it started (resolved paths).
_made = set()
_made_lock = threading.Lock()

# File types the screen may preview or open.
_SERVABLE_TYPES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".docx", ".pdf", ".txt", ".md"}


def _cut(text, limit: int = _TEXT_CHARS) -> str:
    text = str(text or "").strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _file(result: dict) -> dict:
    """The file part of a card, remembered so the screen may fetch it."""

    path = result.get("path") or ""

    if result.get("saved") and path:
        remember_file(path)

    return {"path": path, "file_name": result.get("file_name") or Path(path).name, "folder": result.get("folder", "")}


def _research(result: dict) -> dict:
    return {
        "type": "research",
        "title": result.get("topic", ""),
        "summary": _cut(result.get("summary"), 600),
        "points": [_cut(p, 300) for p in (result.get("key_points") or [])[:5]],
        "sources": [{"title": s.get("title", ""), "url": s.get("url", "")} for s in (result.get("sources") or [])[:6]],
    }


def _document(result: dict) -> dict:
    return {
        "type": "document",
        "title": result.get("title", ""),
        "words": result.get("words"),
        "sections": len(result.get("sections") or []),
        **_file(result),
    }


def _image(result: dict) -> dict:
    return {
        "type": "image",
        "title": _cut(result.get("concept"), 200),
        "size": result.get("size", ""),
        "model": result.get("model", ""),
        **_file(result),
    }


def _post(result: dict) -> dict:
    return {"type": "post", "text": _cut(result.get("text"), 3000), "characters": result.get("characters")}


def _linkedin(result: dict) -> dict:
    image = result.get("image_path") or ""

    if image:
        remember_file(image)

    return {
        "type": "linkedin",
        "posted": bool(result.get("posted")),
        "dry_run": bool(result.get("dry_run")),
        "url": result.get("post_url", ""),
        "as": result.get("as", ""),
        "text": _cut(result.get("text"), 3000),
        "image_path": image,
    }


def _practice(result: dict) -> dict:
    return {"type": "practice", "note": result.get("note", ""), **_file(result)}


def _think(result: dict) -> dict:
    return {"type": "text", "text": _cut(result.get("output"))}


def _file_read(result: dict) -> dict:
    path = result.get("path") or ""

    if path:
        # The user asked FATMEE to read it, so the screen may open it.
        remember_file(path)

    unit = result.get("unit", "page")

    return {
        "type": "file",
        "title": result.get("file_name", ""),
        "summary": _cut(result.get("answer") or result.get("summary"), 700),
        "points": [_cut(p, 300) for p in (result.get("key_points") or [])[:5]],
        "coverage": f"Read {result.get('read')} of {result.get('total')} {unit}s",
        "complete": bool(result.get("complete")),
        "path": path,
        "file_name": result.get("file_name", ""),
    }


_BUILDERS = {
    "research_topic": _research,
    "create_document": _document,
    "create_image": _image,
    "write_post": _post,
    "linkedin_create_post": _linkedin,
    "practice_post": _practice,
    "think": _think,
    "read_file": _file_read,
}


def card(tool: str, result) -> dict | None:
    """The card of one finished step, or None (failed, or nothing to show)."""

    if not isinstance(result, dict) or "error" in result:
        return None

    build = _BUILDERS.get(tool)

    if build is None:
        return None

    try:
        return {"tool": tool, **build(result)}

    except Exception:
        # A card is only a preview: never let it break a task.
        return None


def task_cards(results: dict) -> list:
    """Cards of a whole task, in run order. Result keys look like "create_image" or "create_image#2"."""

    cards = []

    for key, result in (results or {}).items():
        made = card(key.split("#")[0], result)

        if made:
            cards.append(made)

    return cards


# ---------------- files the screen may fetch ----------------

def remember_file(path: str) -> None:
    try:
        resolved = str(Path(path).resolve())
    except (OSError, ValueError):
        return

    with _made_lock:
        _made.add(resolved.lower())


def output_folders() -> list:
    """FATMEE's own folders: images, reports and practice posts."""

    folders = []

    for setting, default in (
        (config.IMAGES_DIR, "FATMEE Images"),
        (config.DOCUMENTS_DIR, "FATMEE Reports"),
        (config.PRACTICE_DIR, "FATMEE Practice Posts"),
    ):
        folder = Path(setting).expanduser() if setting else files.desktop_folder(default)
        folders.append(folder.resolve())

    return folders


def servable(path: str) -> Path | None:
    """The file, if the screen may fetch it; otherwise None."""

    try:
        resolved = Path(path).resolve()
    except (OSError, ValueError):
        return None

    if resolved.suffix.lower() not in _SERVABLE_TYPES or not resolved.is_file():
        return None

    with _made_lock:
        made = str(resolved).lower() in _made

    if made or any(resolved.is_relative_to(folder) for folder in output_folders()):
        return resolved

    return None
