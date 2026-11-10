"""Undo/redo stack and the undo/redo phrases."""
import pytest

from hp3d import editor as E
from hp3d.history import EditHistory, only_history_ops


def test_undo_redo_roundtrip_and_limit():
    history = EditHistory(limit=2)
    history.snapshot("a")
    history.snapshot("b")
    history.snapshot("c")
    assert history.undo("now") == "c"
    assert history.undo("c") == "b"
    with pytest.raises(ValueError, match="Nothing to undo"):
        history.undo("b")
    assert history.redo("b") == "c"
    history.snapshot("fresh")
    assert history.can_redo() is False


def test_redo_phrase_is_separate_from_edits():
    assert E.parse_command("redo") == [{"op": "redo"}]
    assert E.parse_command("undo it") == [{"op": "undo"}]
    assert only_history_ops(E.parse_command("redo")) == ["redo"]
    assert only_history_ops(E.parse_command("make the walls 8 feet")) is None
    with pytest.raises(ValueError, match="own line"):
        only_history_ops(E.parse_command("undo and make the walls 8 feet"))


def test_apply_redo_does_not_need_a_selection(tmp_path):
    params = E.JobParams()
    msgs, need, selected = E.apply_actions(tmp_path / "job", params, [{"op": "redo"}], None)
    assert need is False and selected is None
    assert "redo" in msgs[0].lower()
    msgs, need, _ = E.apply_actions(tmp_path / "job", params, [{"op": "undo"}], None)
    assert need is False and "undo" in msgs[0].lower()
