"""Undo and redo for in-app edits.

The app stores a snapshot of the polygons and parameters from before each
successful change. Undo puts that snapshot back and keeps the edited state
for redo. A new change drops the redo stack.
"""
from __future__ import annotations


class EditHistory:
    def __init__(self, limit: int = 50):
        if limit < 1:
            raise ValueError("history needs room for at least one change")
        self.limit = limit
        self.undo_stack: list[str] = []
        self.redo_stack: list[str] = []

    def can_undo(self) -> bool:
        return bool(self.undo_stack)

    def can_redo(self) -> bool:
        return bool(self.redo_stack)

    def snapshot(self, before: str) -> None:
        """Remember the state from before a change that just succeeded."""
        self.undo_stack.append(before)
        del self.undo_stack[:-self.limit]
        self.redo_stack.clear()

    def undo(self, current: str) -> str:
        if not self.undo_stack:
            raise ValueError("Nothing to undo.")
        previous = self.undo_stack.pop()
        self.redo_stack.append(current)
        return previous

    def redo(self, current: str) -> str:
        if not self.redo_stack:
            raise ValueError("Nothing to redo.")
        nxt = self.redo_stack.pop()
        self.undo_stack.append(current)
        return nxt


def only_history_ops(actions: list[dict]) -> list[str] | None:
    """Undo/redo phrases, or None when the phrase is an ordinary edit.

    Undo and redo are not mixed into a list of other changes.
    """
    ops = [a["op"] for a in actions]
    if not any(op in ("undo", "redo") for op in ops):
        return None
    if any(op not in ("undo", "redo") for op in ops):
        raise ValueError("Type undo or redo on its own line.")
    return ops
