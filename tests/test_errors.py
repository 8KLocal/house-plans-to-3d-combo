"""Plain error sentences, including ones the app already writes for people."""
from hp3d.errors import friendly
from hp3d.raster import UnsupportedFile


def test_operator_messages_stay_as_written():
    text = "the measurement must be more than 0"
    assert friendly(ValueError(text)) == text
    assert friendly(UnsupportedFile("I can't read DWG yet (plan.dwg).")) == "I can't read DWG yet (plan.dwg)."


def test_missing_file_memory_and_empty_build():
    assert "plan.png" in friendly(FileNotFoundError(2, "missing", "/tmp/plan.png"))
    assert "memory" in friendly(MemoryError()).lower()
    assert "wall" in friendly(RuntimeError("nothing to build: no wall/window polygons")).lower()


def test_long_unexpected_errors_are_short():
    message = friendly(RuntimeError("x" * 500))
    assert message.startswith("Something went wrong")
    assert len(message) < 320
    assert "Traceback" not in message
