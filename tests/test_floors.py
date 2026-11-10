"""Multi-floor stacking and the optional roof. No detection weights."""
import numpy as np
import pytest
import trimesh
from PIL import Image

from hp3d import colors, editor as E
from hp3d.build3d import build
from hp3d.floors import stack_levels


def _storey(width=80, depth=50, wall=4):
    polys = {"wall": [], "door": [], "window": [], "furniture": []}
    for rect in ((0, 0, width, wall), (0, depth - wall, width, depth),
                 (0, 0, wall, depth), (width - wall, 0, width, depth)):
        polys = E.add_rect(polys, "wall", *rect)
    return build(polys, 1.0, wall_h=96, floor_t=8, sill=36, lintel=80)


def test_second_floor_stacks_on_the_wall_tops_with_pegs():
    one = _storey()
    two = _storey()
    bare = two["floor"].volume
    out = stack_levels(
        [{"parts": one, "wall_h": 96, "floor_t": 8, "label": "L1"},
         {"parts": two, "wall_h": 90, "floor_t": 8, "label": "L2"}],
        mm_per_unit=1.0, apply_logo=False)
    assert out["storeys"] == 2
    assert out["parts"]["L2_floor"].bounds[0][2] == pytest.approx(104, abs=0.2)  # 8 in slab + 96 in walls
    assert out["parts"]["L1_walls"].bounds[1][2] > 104  # pegs stand above the wall top
    assert out["parts"]["L2_floor"].volume < bare
    assert any("alignment pegs" in s for s in out["assumptions"])
    assert colors.color_of("L2_walls") == colors.PART_COLORS["walls"]
    assert colors.color_of("L2_floor") == colors.PART_COLORS["floor"]


def test_missing_roof_size_builds_nothing():
    one = _storey()
    out = stack_levels([{"parts": one, "wall_h": 96, "floor_t": 8}],
                       roof="gable", roof_thickness_in=1, roof_rise_in=None, apply_logo=False)
    assert "roof" not in out["parts"]
    assert any("no roof was built" in s.lower() for s in out["assumptions"])


def test_flat_and_gable_roofs_use_only_the_given_sizes():
    one = _storey()
    flat = stack_levels([{"parts": one, "wall_h": 96, "floor_t": 8}],
                        roof="flat", roof_thickness_in=5, apply_logo=False)
    assert flat["parts"]["roof"].bounds[0][2] == pytest.approx(104, abs=0.2)
    assert flat["parts"]["roof"].bounds[1][2] == pytest.approx(109, abs=0.2)
    assert "no overhang" in " ".join(flat["assumptions"]).lower()

    gable = stack_levels([{"parts": one, "wall_h": 96, "floor_t": 8}],
                         roof="gable", roof_thickness_in=2, roof_rise_in=24, apply_logo=False)
    roof = gable["parts"]["roof"]
    assert roof.is_watertight
    assert roof.bounds[1][2] == pytest.approx(104 + 2 + 24, abs=0.3)
    assert "no overhang" in " ".join(gable["assumptions"]).lower()


def test_floor_and_roof_commands_do_not_guess():
    assert E.parse_command('add floor "Upstairs Plan.png"') == [{"op": "add_floor", "plan": "Upstairs Plan.png"}]
    assert E.parse_command("roof flat 4 inches")[0]["thickness"] == 4
    gable = E.parse_command("roof gable rise 4 feet thickness 1 inch")[0]
    assert gable["style"] == "gable" and gable["rise"] == 48 and gable["thickness"] == 1
    assert E.parse_command("bed 220 x 180 mm") == [{"op": "bed", "x": 220.0, "y": 180.0}]
    assert E.parse_command("no roof") == [{"op": "roof", "style": "none"}]
    with pytest.raises(ValueError, match="thickness"):
        E.parse_command("roof flat")
    with pytest.raises(ValueError, match="rise"):
        E.parse_command("roof gable rise 4 feet")


def test_rebuild_exports_a_requested_roof_and_bed_pieces(tmp_path):
    job = tmp_path / "job"
    job.mkdir()
    Image.fromarray(np.full((80, 400, 3), 255, np.uint8)).save(job / "source_crop.png")
    walls = []
    for rect in ((0, 0, 400, 4), (0, 76, 400, 80), (0, 0, 4, 80), (396, 0, 400, 80)):
        walls.append(E.add_rect({"wall": []}, "wall", *rect)["wall"][0])
    E.save_polys(job, {"wall": walls, "door": [], "window": [], "furniture": []})
    (job / "polygons_detected.json").write_text((job / "polygons.json").read_text())
    p = E.JobParams(plan="x.png", px_per_inch=1.0, user_set=["px_per_inch"], bed_mm=[256, 256])
    E.apply_actions(job, p, E.parse_command("roof flat 3 inches"), None)
    rep = E.rebuild(job, p, log=lambda s: None)
    assert (job / "part_roof.stl").is_file()
    assert "roof" in rep["stages"]["editor_rebuild"]["parts"]
    assert (job / "bed" / "piece-01.stl").is_file()
    assert (job / "bed" / "piece-02.stl").is_file()
    assert any("Split into" in s for s in rep["assumptions"])


def test_apply_floor_command_does_not_copy_floor_one(tmp_path):
    job = tmp_path / "job"
    job.mkdir()
    E.save_polys(job, {"wall": [], "door": [], "window": [], "furniture": []})
    p = E.JobParams(plan="down.png")
    msgs, _, _ = E.apply_actions(job, p, E.parse_command("add floor upstairs.png"), None)
    assert len(p.levels) == 1 and p.levels[0]["plan"] == "upstairs.png"
    assert "not copied" in msgs[0].lower() or "nothing is copied" in msgs[0].lower()
    E.apply_actions(job, p, E.parse_command("clear floors"), None)
    assert p.levels == []
