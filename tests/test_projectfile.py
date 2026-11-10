"""Round-trip of a .hp3d project file."""
import json
import zipfile

import pytest

from hp3d import projectfile as P


def _job(tmp_path):
    job = tmp_path / "cottage"
    job.mkdir()
    (job / "job.json").write_text(json.dumps({"plan": "plan.png", "px_per_inch": 2}))
    (job / "polygons.json").write_text(json.dumps({"wall": [1], "door": []}))
    (job / "source_crop.png").write_bytes(b"\x89PNG\r\nnot-really")
    (job / "quote_rates.json").write_text(json.dumps({"filament_usd_per_kg": 22.5, "hourly_usd": 35, "markup_percent": 10}))
    (job / "notes.txt").write_text("keep me")
    (job / "skip.exe").write_bytes(b"nope")
    return job


def test_save_and_reopen_roundtrip(tmp_path):
    job = _job(tmp_path)
    dest = P.save_project(job, tmp_path / "out" / "cottage")
    assert dest.name == "cottage.hp3d"
    assert dest.is_file()
    other = tmp_path / "reopened"
    P.load_project(dest, other)
    assert json.loads((other / "job.json").read_text())["px_per_inch"] == 2
    assert json.loads((other / "polygons.json").read_text())["wall"] == [1]
    assert (other / "source_crop.png").read_bytes().startswith(b"\x89PNG")
    assert json.loads((other / "quote_rates.json").read_text())["hourly_usd"] == 35
    assert (other / "notes.txt").read_text() == "keep me"
    assert not (other / "skip.exe").exists()
    assert not (other / "format.json").exists()


def test_bad_projects_are_refused_without_wiping_the_job(tmp_path):
    job = _job(tmp_path)
    (job / "job.json").write_text('{"plan": "keep"}')
    bogus = tmp_path / "notes.txt"
    bogus.write_text("hello")
    with pytest.raises(ValueError, match="isn't a House Plan 3D project"):
        P.load_project(bogus, job)
    assert json.loads((job / "job.json").read_text())["plan"] == "keep"

    slipped = tmp_path / "slip.hp3d"
    with zipfile.ZipFile(slipped, "w") as archive:
        archive.writestr("../job.json", "{}")
        archive.writestr("polygons.json", "{}")
    with pytest.raises(ValueError, match="unsafe"):
        P.load_project(slipped, job)
    assert not (tmp_path / "job.json").exists()
    assert json.loads((job / "job.json").read_text())["plan"] == "keep"

    empty = tmp_path / "empty.hp3d"
    with zipfile.ZipFile(empty, "w") as archive:
        archive.writestr("readme.txt", "no")
    with pytest.raises(ValueError, match="missing"):
        P.load_project(empty, job)


def test_save_refuses_an_unbuilt_folder(tmp_path):
    job = tmp_path / "empty"
    job.mkdir()
    with pytest.raises(ValueError, match="Build the model"):
        P.save_project(job, tmp_path / "x.hp3d")
