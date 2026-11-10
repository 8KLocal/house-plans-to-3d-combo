"""Upload-anything path: file types, 3D-file import, one-at-a-time questions (no model weights needed)."""
import json
from pathlib import Path

import numpy as np
import pytest
import trimesh
from PIL import Image

from hp3d import build3d, editor as E, mesh_import as MI, quiz as Q, samples
from hp3d.raster import UnsupportedFile, file_kind, load_plan

T = samples.TRUTH


def _mesh_job(tmp_path, unit_scale=25.4, name="house.stl", **kw):
    src = samples.write_sample_house(tmp_path / "in" / name, unit_scale=unit_scale, **kw)
    job = tmp_path / "job"
    p = E.JobParams(plan=str(src))
    E.detect(job, p, log=lambda s: None)
    rep = E.rebuild(job, p, log=lambda s: None)
    return job, p, rep


def test_file_kinds_and_clear_refusals(tmp_path):
    Image.new("RGB", (40, 30), "white").save(tmp_path / "plan.png")
    Image.new("RGB", (40, 30), "white").save(tmp_path / "scan.weird", format="PNG")   # odd name, real picture
    samples.write_sample_house(tmp_path / "h.stl")
    assert file_kind(tmp_path / "plan.png") == "image"
    assert file_kind(tmp_path / "scan.weird") == "image"
    assert file_kind(tmp_path / "h.stl") == "mesh"
    (tmp_path / "a.dwg").write_bytes(b"AC1032 not really")
    with pytest.raises(UnsupportedFile, match="AutoCAD.*PDF"):
        file_kind(tmp_path / "a.dwg")
    (tmp_path / "broken.png").write_bytes(b"not a png")
    with pytest.raises(UnsupportedFile, match="couldn't open the picture"):
        load_plan(tmp_path / "broken.png")
    (tmp_path / "broken.stl").write_bytes(b"solid x\nendsolid x\n")
    with pytest.raises(UnsupportedFile):
        load_plan(tmp_path / "broken.stl")
    with pytest.raises(UnsupportedFile, match="can't find"):
        file_kind(tmp_path / "missing.pdf")


def test_stl_import_measures_what_the_file_has(tmp_path):
    job, p, rep = _mesh_job(tmp_path)              # sample house saved in millimetres
    assert p.source_kind == "mesh" and p.mesh["unit_guess"] == "millimetres"
    assert p.wall_h == pytest.approx(T["wall_h"], abs=0.2)
    assert p.floor_t == pytest.approx(T["floor_t"], abs=0.2)
    assert p.sill == pytest.approx(T["sill"], abs=0.5) and p.lintel == pytest.approx(T["lintel"], abs=0.5)
    assert set(p.measured) == {"wall_h", "floor_t", "sill", "lintel"}
    polys = E.load_polys(job)
    assert len(polys["door"]) == 1 and len(polys["window"]) == 1 and len(polys["furniture"]) == 1
    head_in = polys["door"][0]["head_px"] / p.px_per_inch
    assert head_in == pytest.approx(T["door_h"], abs=0.5)                     # header kept above the frame
    bed_h = polys["furniture"][0]["height_px"] / p.px_per_inch
    assert bed_h == pytest.approx(T["bed"][2], abs=0.5)
    st = rep["stages"]["editor_rebuild"]
    assert st["extent_in"] == pytest.approx([T["width"], T["depth"], T["floor_t"] + T["wall_h"]], abs=0.6)
    assert all(st["watertight"].values()) and st["logo"]["status"] == "ok"
    assert {"walls", "floor", "windows", "furniture"} <= set(st["parts"])
    assert (job / "original.stl").exists() and (job / "source_crop.png").exists()
    mi = rep["stages"]["mesh_import"]
    assert any("roofs, stairs" in s for s in mi["not_inferred"])
    assert rep["review"]["status"] == "questions left" and rep["review"]["pending"][0] == "size"
    assert any("Size GUESSED" in a for a in rep["assumptions"])


def test_no_slab_no_furniture_gets_base_and_logo(tmp_path):
    m = samples.sample_house_mesh(with_bed=False)
    walls_only = trimesh.boolean.difference([m, trimesh.creation.box(bounds=[[-1, -1, -1], [241, 181, T["floor_t"]]])],
                                            engine="manifold")
    src = tmp_path / "walls.obj"
    walls_only.export(src)
    job = tmp_path / "job"
    p = E.JobParams(plan=str(src))
    E.detect(job, p, log=lambda s: None)
    rep = E.rebuild(job, p, log=lambda s: None)
    assert "floor_t" not in p.measured and not E.load_polys(job)["furniture"]
    assert rep["stages"]["editor_rebuild"]["logo"]["status"] == "ok"
    assert any("no floor slab" in a for a in rep["assumptions"])
    assert "floor_t" in [q["id"] for q in Q.pending(job, p)]


def test_quiz_one_at_a_time_until_done(tmp_path):
    job, p, _ = _mesh_job(tmp_path, unit_scale=0.5)    # scaled model: 48-unit walls fit no unit
    assert p.mesh["unit_guess"] is None
    first = Q.pending(job, p)[0]
    assert first["id"] == "size" and "no units" in first["text"]
    with pytest.raises(ValueError, match="needs a unit"):
        Q.answer(job, p, "size", "20")                  # bare number refused, not guessed
    msg, need = Q.answer(job, p, "size", "20 ft")
    assert need and "px_per_inch" in p.user_set
    assert p.wall_h == pytest.approx(96, abs=0.3)       # measured heights follow the size
    with pytest.raises(ValueError, match="yes or no"):
        Q.answer(job, p, "doors", "maybe")
    Q.answer(job, p, "doors", "yes")
    Q.skip(job, p, "floors")
    rep = E.rebuild(job, p, log=lambda s: None)
    assert rep["review"]["status"] == "done" and rep["review"]["answers"]["floors"]["how"] == "skipped"
    assert any("'floors' was skipped" in a for a in rep["assumptions"])
    assert rep["stages"]["editor_rebuild"]["extent_in"][0] == pytest.approx(240, abs=0.6)


def test_unit_word_answer_and_plain_language_on_mesh_job(tmp_path):
    job, p, _ = _mesh_job(tmp_path, unit_scale=2.54, name="house_cm.ply")
    assert p.mesh["unit_guess"] == "centimetres"
    Q.answer(job, p, "size", "cm")
    E.apply_actions(job, p, E.parse_command("set scale to %g" % (p.px_per_inch * 2)), None)
    assert p.wall_h == pytest.approx(48, abs=0.2)       # twice the px per inch = half the size
    E.apply_actions(job, p, E.parse_command("make the walls 9 feet"), None)
    furn = next(x for x in E.list_parts(E.load_polys(job)) if x["kind"] == "furniture")
    E.apply_actions(job, p, E.parse_command("delete selected"), ("furniture", furn["index"]))
    rep = E.rebuild(job, p, log=lambda s: None)
    assert rep["stages"]["editor_rebuild"]["extent_in"][2] == pytest.approx(p.floor_t + 108, abs=0.3)
    assert "furniture" not in rep["stages"]["editor_rebuild"]["parts"]
    assert rep["stages"]["editor_rebuild"]["logo"]["status"] == "ok"


@pytest.mark.parametrize("ext", [".glb", ".3mf"])
def test_glb_and_3mf(tmp_path, ext):
    m = samples.sample_house_mesh()
    if ext == ".glb":   # glTF files are Y-up
        m.apply_transform(trimesh.transformations.rotation_matrix(-np.pi / 2, [1, 0, 0]))
    src = tmp_path / f"house{ext}"
    m.export(src)
    a = MI.read(src)[2]
    assert a.slab and a.wall_top - a.floor_top == pytest.approx(T["wall_h"], abs=0.3)
    assert len(a.windows) == 1 and len(a.doors) == 1


def test_added_door_cuts_a_frame_opening():
    wall = E.add_rect({}, "wall", 0, 0, 100, 6)
    solid = build3d.build(wall, 1.0)["walls"].volume
    cut = build3d.build(E.add_rect(wall, "door", 40, -1, 76, 7), 1.0)["walls"].volume
    assert cut == pytest.approx(solid - 36 * 6 * 96, rel=1e-3)
    assert E.parse_command("add furniture 10,10 to 40,30")[0]["kind"] == "furniture"
    assert E.parse_command("furniture 30 inches") == [{"op": "set", "param": "furniture_h", "value": 30.0}]


def test_image_job_asks_for_unspecified_heights(tmp_path):
    job = tmp_path / "job"; job.mkdir()
    Image.fromarray(np.full((120, 200, 3), 255, np.uint8)).save(job / "source_crop.png")
    polys = {"wall": [E.add_rect({}, "wall", *r)["wall"][0] for r in [(0, 0, 200, 4), (0, 116, 200, 120)]],
             "door": [], "window": [E.add_rect({}, "window", 60, 0, 100, 4)["window"][0]]}
    E.save_polys(job, polys)
    p = E.JobParams(plan="x.png")
    ids = [q["id"] for q in Q.pending(job, p)]
    assert ids == ["size", "wall_h", "floor_t", "sill", "lintel", "doors", "floors"]
    Q.answer(job, p, "size", "200 in")                  # walls are 200 px wide -> 1 px/in
    assert p.px_per_inch == pytest.approx(1.0)
    Q.answer(job, p, "wall_h", "9 ft")
    assert p.wall_h == 108 and "wall_h" in p.user_set
    rep = E.rebuild(job, p, log=lambda s: None)
    assert rep["review"]["pending"][0] == "floor_t"
    assert json.loads((job / "job.json").read_text())["quiz"]["wall_h"]["how"] == "answered"


def _dxf(path: Path, units_code: int | None) -> Path:
    """Minimal DXF of the sample house cut at window height, walls as closed outlines."""
    sec = samples.sample_house_mesh().section(plane_origin=[0, 0, 50], plane_normal=[0, 0, 1])
    out = ["0", "SECTION", "2", "HEADER"] + (["9", "$INSUNITS", "70", str(units_code)] if units_code else []) + \
        ["0", "ENDSEC", "0", "SECTION", "2", "ENTITIES"]
    for loop in sec.discrete:
        for a, b in zip(loop[:-1], loop[1:]):
            out += ["0", "LINE", "8", "0", "10", f"{a[0]}", "20", f"{a[1]}", "30", "0",
                    "11", f"{b[0]}", "21", f"{b[1]}", "31", "0"]
    path.write_text("\n".join(out + ["0", "ENDSEC", "0", "EOF"]) + "\n")
    return path


def test_dxf_drawing_reads_scale_from_units(tmp_path):
    assert file_kind(_dxf(tmp_path / "a.dxf", 1)) == "cad"
    pl = load_plan(tmp_path / "a.dxf")
    assert pl.px_per_inch and pl.rgb.shape[1] == pytest.approx(240 * pl.px_per_inch + 40, abs=2)
    assert (pl.rgb[..., 0] < 50).mean() > 0.02                       # walls drawn solid, not hairlines
    assert load_plan(_dxf(tmp_path / "b.dxf", None)).px_per_inch is None   # no units: scale stays unknown
