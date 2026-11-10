"""Edit/rebuild helper used by the in-app editor (no model weights needed)."""
import json
from pathlib import Path

import numpy as np
import pytest
import trimesh
from PIL import Image

from hp3d import editor as E


def _job(tmp_path: Path) -> tuple[Path, E.JobParams]:
    job = tmp_path / "job"
    job.mkdir()
    Image.fromarray(np.full((120, 200, 3), 255, np.uint8)).save(job / "source_crop.png")
    walls = [E.add_rect({"wall": []}, "wall", *r)["wall"][0] for r in
             [(0, 0, 200, 4), (0, 116, 200, 120), (0, 0, 4, 120), (196, 0, 200, 120)]]
    polys = {"wall": walls, "door": [], "window": [E.add_rect({}, "window", 60, 0, 100, 4)["window"][0]]}
    E.save_polys(job, polys)
    (job / "polygons_detected.json").write_text(json.dumps(polys))
    p = E.JobParams(plan="x.png", px_per_inch=2.0, user_set=["px_per_inch"])
    p.save(job)
    return job, p


def test_parse_phrases():
    assert E.parse_command("make the walls 8 feet") == [{"op": "set", "param": "wall_h", "value": 96.0}]
    assert E.parse_command("wall height 96 inches")[0]["value"] == 96.0
    assert E.parse_command("set scale to 1.0") == [{"op": "set", "param": "px_per_inch", "value": 1.0}]
    assert E.parse_command("sills at 36 inches")[0] == {"op": "set", "param": "sill", "value": 36.0}
    assert E.parse_command("move selected 12 inches left") == [{"op": "move", "dx": -12.0, "dy": 0.0, "unit": "in"}]
    assert [a["param"] for a in E.parse_command("floor 3 in and lintels at 7 feet")] == ["floor_t", "lintel"]
    with pytest.raises(ValueError, match="unit"):
        E.parse_command("walls 96")          # bare number: refused, not guessed
    with pytest.raises(ValueError, match="didn't understand"):
        E.parse_command("make it look nicer")


def test_green_outline_crop():
    rgb = np.full((100, 150, 3), 255, np.uint8)
    rgb[20, 30:121] = rgb[80, 30:121] = (0, 200, 0)
    rgb[20:81, 30] = rgb[20:81, 120] = (0, 200, 0)
    assert E.find_green_outline(rgb, margin=5, min_run=30) == [25, 15, 126, 86]
    assert E.find_green_outline(np.full((50, 50, 3), 255, np.uint8)) is None


def test_edit_and_rebuild(tmp_path):
    job, p = _job(tmp_path)
    E.rebuild(job, p, log=lambda s: None)
    h0 = trimesh.load(job / "model.stl").bounds[1][2]
    assert h0 == pytest.approx(p.floor_t + p.wall_h, abs=0.5)

    msgs, need, sel = E.apply_actions(job, p, E.parse_command("make the walls 10 feet"), None)
    assert need and p.wall_h == 120.0
    _, _, sel = E.apply_actions(job, p, E.parse_command("add wall 100,4 to 104,116"), None)
    assert sel == ("wall", 4)
    E.apply_actions(job, p, E.parse_command("move selected 6 inches right"), sel)   # 6 in * 2 px/in = 12 px
    moved = E.load_polys(job)["wall"][4]["outer"]
    assert min(x for x, _ in moved) == pytest.approx(112)
    E.apply_actions(job, p, [{"op": "delete"}], ("window", 0))
    with pytest.raises(ValueError):
        E.apply_actions(job, p, E.parse_command("sills at 9 feet"), None)        # above lintel: refused

    rep = E.rebuild(job, p, log=lambda s: None)
    m = trimesh.load(job / "model.stl")
    assert m.bounds[1][2] == pytest.approx(p.floor_t + 120, abs=0.5)
    assert rep["polygon_counts"] == {"wall": 5, "door": 0, "window": 0}
    assert any("added wall" in e["edit"] for e in rep["in_app_edits"])
    assert any("Window sill" in a and "default" in a for a in rep["assumptions"])
    assert {"model.stl", "model.glb", "preview.png", "plan_overlay.png"} <= set(rep["stages"]["editor_rebuild"]["files"])
    assert json.loads((job / "polygons_detected.json").read_text())["window"]   # original kept


def test_move_inches_needs_scale(tmp_path):
    job, p = _job(tmp_path)
    p.px_per_inch = None
    with pytest.raises(ValueError, match="scale"):
        E.apply_actions(job, p, E.parse_command("move selected 1 foot up"), ("wall", 0))
