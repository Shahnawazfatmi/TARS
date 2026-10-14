"""
Safe saving for every agent that makes a file (Word documents,
images, and later anything else).

One place decides where files may go, so all agents follow the
same rules:

- Only inside the user's own folders (home, Desktop, Documents,
  Downloads). Anything else, like C:\\Windows, is refused.
- File names are cleaned of characters Windows does not allow.
- Never overwrite: "report.docx" becomes "report (2).docx".
"""

import os
import re
from pathlib import Path


# Characters Windows does not allow in file names.
_BAD_NAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

KNOWN_FOLDERS = ("desktop", "documents", "downloads", "pictures")

# Registry value names of the folders above in "User Shell Folders".
_WINDOWS_VALUE_NAMES = {
    "desktop": "Desktop",
    "documents": "Personal",
    "downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
    "pictures": "My Pictures",
}


def known_folder(name: str) -> Path | None:
    """The real Desktop / Documents / Downloads / Pictures folder (it may be moved, e.g. to OneDrive)."""

    name = name.lower()

    if os.name == "nt" and name in _WINDOWS_VALUE_NAMES:
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
            ) as key:
                value, _ = winreg.QueryValueEx(key, _WINDOWS_VALUE_NAMES[name])
                path = Path(os.path.expandvars(value))

                if path.is_dir():
                    return path

        except OSError:
            pass

    path = Path.home() / name.capitalize()

    return path if path.is_dir() else None


def allowed_roots() -> list:
    """Folders FATMEE may save into (and anything inside them)."""

    roots = [Path.home()]

    for name in KNOWN_FOLDERS:
        folder = known_folder(name)

        if folder:
            roots.append(folder)

    return [root.resolve() for root in roots]


def desktop_folder(subfolder: str) -> Path:
    """A folder on the user's Desktop, e.g. "FATMEE Reports"."""

    return (known_folder("desktop") or Path.home()) / subfolder


def resolve_folder(folder: str, default: Path) -> Path:
    """
    Turn the folder the user asked for into a safe path.

    "" is the agent's default. "desktop", "documents", "downloads" and
    "pictures" are the real Windows folders. Other relative paths are
    inside the user's home folder. Anything outside the user's own
    folders is refused (ValueError).
    """

    folder = (folder or "").strip().strip('"')
    short = folder.lower().strip("~/\\ ")

    if not folder:
        path = default

    elif short in KNOWN_FOLDERS:
        path = known_folder(short) or Path.home()

    else:
        path = Path(os.path.expanduser(folder))

        if not path.is_absolute():
            path = Path.home() / path

    path = Path(path).resolve()

    if not any(path == root or path.is_relative_to(root) for root in allowed_roots()):
        raise ValueError(f"FATMEE only saves files inside your own folders, not in {path}.")

    return path


def safe_file_name(name: str, extension: str, fallback: str) -> str:
    """A valid Windows file name ending in the given extension (".docx", ".png")."""

    name = (name or "").strip()

    if name.lower().endswith(extension.lower()):
        name = name[:-len(extension)]

    name = _BAD_NAME_CHARS.sub(" ", name)
    name = re.sub(r"\s+", " ", name).strip(" .")[:80].strip(" .")

    return f"{name or fallback}{extension}"


def unique_path(folder: Path, file_name: str) -> Path:
    """Never overwrite: "report.docx" becomes "report (2).docx" if taken."""

    path = folder / file_name
    stem, suffix = path.stem, path.suffix
    number = 2

    while path.exists():
        path = folder / f"{stem} ({number}){suffix}"
        number += 1

    return path


def as_bool(value) -> bool:
    """true / "true" / "yes" / 1 -> True (models send booleans as text too)."""

    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1")

    return bool(value)


def open_file(path: Path) -> None:
    """Open a file with its usual program (Windows only, best effort)."""

    if hasattr(os, "startfile"):
        try:
            os.startfile(str(path))
        except OSError:
            pass
