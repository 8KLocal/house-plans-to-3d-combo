"""Plain sentences for errors the desktop app shows to a person.

ValueError messages written for the operator are kept as they are. Everything
else is shortened so a traceback never lands in the window.
"""
from __future__ import annotations

from pathlib import Path

from .raster import UnsupportedFile

_SWAPS = (
    ("nothing to build", "There's nothing to build yet. I need at least one wall or window on the plan."),
    ("segmentation produced no polygons", "I couldn't find walls in that file. Try a clearer picture, or export a PDF or DXF."),
    ("could not read a length", "I need a length with a unit, such as 8 feet or 96 inches."),
    ("isn't a house plan 3d project", "That file isn't a House Plan 3D project. Choose a .hp3d file."),
)


def _short(text: str) -> str:
    line = " ".join(text.split())
    if len(line) > 240:
        return line[:237] + "..."
    return line


def friendly(exc: BaseException) -> str:
    """One sentence for a message box or the What happened list."""
    if isinstance(exc, UnsupportedFile):
        return str(exc)
    if isinstance(exc, MemoryError):
        return "That file is too big for the memory on this computer."
    if isinstance(exc, PermissionError):
        return "I can't read or save that file. Pick a different folder, or check that you're allowed to use it."
    if isinstance(exc, FileNotFoundError):
        name = Path(exc.filename).name if exc.filename else "that file"
        return f"I can't find {name}. Choose the file again."
    text = _short(str(exc).strip() or "something failed")
    low = text.lower()
    for key, msg in _SWAPS:
        if key in low:
            return msg
    if isinstance(exc, ValueError):
        return text
    if isinstance(exc, OSError):
        return f"I couldn't use that file. {text}"
    return f"Something went wrong, and the model was left as it was. {text}"
