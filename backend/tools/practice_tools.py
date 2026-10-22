"""
Practice tools: rehearse actions that need your approval, safely.

practice_post works like a real post will (Step 13: LinkedIn): FATMEE
pauses, shows you the text, and runs only after you approve. Instead
of publishing, it saves the post in a text file, so the approval flow
can be tested without any account and without publishing anything.
"""

import os
import re
from pathlib import Path

import config
from agents import files


def _default_folder() -> Path:
    if config.PRACTICE_DIR:
        return Path(os.path.expanduser(config.PRACTICE_DIR))

    return files.desktop_folder("FATMEE Practice Posts")


def practice_post(text: str = "", title: str = "", image_path: str = "") -> dict:
    """Save a post's text (and its image path) to a file instead of publishing it."""

    text = (text or "").strip()

    if not text:
        return {"error": "The practice post needs its text."}

    try:
        folder = files.resolve_folder("", _default_folder())
    except ValueError as e:
        return {"error": str(e)}

    first_words = " ".join(re.findall(r"[\w'-]+", text)[:6])
    path = files.unique_path(folder, files.safe_file_name(title or first_words, ".txt", "Practice post"))

    body = text

    if image_path:
        body += f"\n\n[Image: {image_path}]"

    try:
        folder.mkdir(parents=True, exist_ok=True)
        path.write_text(body + "\n", encoding="utf-8")
    except OSError as e:
        return {"error": f"Could not save the practice post ({type(e).__name__})."}

    return {
        "saved": True,
        "practice": True,
        "note": "Practice only: nothing was published.",
        "path": str(path),
        "file_name": path.name,
        "folder": str(path.parent),
        "text": text,
        "characters": len(text),
        "image_path": image_path or None,
    }
