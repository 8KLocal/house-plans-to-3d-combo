"""Part colours: 3D preview, 3MF/GLB exports, door-frame split, colours kept from a coloured source file."""
import numpy as np
import pytest
import trimesh
from PIL import Image

from hp3d import build3d, colors as C, editor as E, mesh_import as MI, samples
from hp3d.preview import render

T = samples.TRUTH


def _mesh_job(tmp_path, src):
    job = tmp_path / "job"
    p = E.JobParams(plan=str(src))
    E.detect(job, p, log=lambda s: None)
    return job, p, E.rebuild(job, p, log=lambda s: None)


def test_door_frames_split_keeps_geometry():
    wall = E.add_rect({}, "wall", 0, 0, 100, 6)
    polys = E.add_rect(wall, "door", 40, -1, 76, 7)
    plain = build3d.build(polys, 1.0)
    split = build3d.build(polys, 1.0, frame_trim=4.0)
    assert "door_frames" not in plain and "door_frames" in split
    total = split["walls"].volume + split["door_frames"].volume
    assert total == pytest.approx(plain["walls"].volume, rel=1e-6)
    assert split["door_frames"].volume == pytest.approx(2 * 4 * 6 * 96, rel=1e-3)   # 4 in jamb each side
    assert all(m.is_watertight for m in split.values())


def test_stl_job_exports_coloured_3mf_and_glb(tmp_path):
    src = samples.write_sample_house(tmp_path / "in" / "house.stl", unit_scale=25.4)
    job, p, rep = _mesh_job(tmp_path, src)
    st = rep["stages"]["editor_rebuild"]
    assert {"walls", "floor", "windows", "door_frames", "furniture"} <= set(st["parts"])
    assert all(st["watertight"].values()) and st["logo"]["status"] == "ok"   # logo still recessed
    assert st["colors_from_file"] == []                                        # STL: no colours in it
    want = {C.PART_LABELS[k]: C.hex_of(C.PART_COLORS[k]).upper() + "FF" for k in st["parts"]}
    assert C.read_3mf_colors(job / "model.3mf") == want
    # 3MF and GLB load back as the same coloured parts, same total geometry as the STL
    stl = trimesh.load(job / "model.stl")
    for f in ("model.3mf", "model.glb"):
        sc = trimesh.load(job / f)
        assert isinstance(sc, trimesh.Scene) and len(sc.geometry) == len(st["parts"])
        assert sc.to_geometry().volume == pytest.approx(stl.volume, rel=1e-3)
    assert stl.is_watertight or all(b.is_watertight for b in stl.split(only_watertight=False))
    glb = trimesh.load(job / "model.glb")
    walls = glb.geometry["walls"].visual.material
    assert np.allclose(np.asarray(walls.baseColorFactor)[:3], np.array(C.PART_COLORS["walls"][:3]), atol=1)


def test_preview_is_coloured_per_part(tmp_path):
    src = samples.write_sample_house(tmp_path / "in" / "house.stl", unit_scale=25.4)
    job, p, rep = _mesh_job(tmp_path, src)
    px = np.asarray(Image.open(job / "preview.png").convert("RGB")).reshape(-1, 3).astype(int)
    for k in ("floor", "walls", "door_frames", "windows", "furniture"):
        c = np.array(C.PART_COLORS[k][:3])
        # some pixel is that hue at any lighting level (flat shading scales the colour)
        ratio = px / np.maximum(c, 1)
        hit = (np.ptp(ratio, axis=1) < 0.06) & (ratio.mean(1) > 0.5) & (ratio.mean(1) <= 1.02)
        assert hit.sum() > 200, k


def test_preview_from_file_and_no_parts(tmp_path):
    m = samples.sample_house_mesh()
    m.export(tmp_path / "h.stl")
    render(tmp_path / "h.stl", tmp_path / "a.png")
    assert Image.open(tmp_path / "a.png").size == (900, 820)
    render({}, tmp_path / "b.png")
    assert Image.open(tmp_path / "b.png").getextrema() == ((255, 255), (255, 255), (255, 255))


def test_colours_kept_from_a_coloured_file(tmp_path):
    m = samples.sample_house_mesh()
    fc = np.tile([200, 200, 200, 255], (len(m.faces), 1))
    c = m.triangles_center
    fc[c[:, 2] < T["floor_t"] - 0.01] = [150, 90, 40, 255]              # brown slab (sides + underside)
    bx = (c[:, 0] > 169.9) & (c[:, 0] < 230.1) & (c[:, 1] > 39.9) & (c[:, 1] < 120.1) & (c[:, 2] > T["floor_t"] + 0.01)
    fc[bx] = [200, 30, 30, 255]                                           # red bed
    m.visual.face_colors = fc
    src = tmp_path / "in" / "house.ply"
    src.parent.mkdir()
    m.export(src)
    job, p, rep = _mesh_job(tmp_path, src)
    sc = p.mesh["source_colors"]
    assert sc["furniture"][:3] == pytest.approx([200, 30, 30], abs=2)
    assert sc["walls"][:3] == pytest.approx([200, 200, 200], abs=2)
    st = rep["stages"]["editor_rebuild"]
    assert st["colors"]["furniture"] == "#c81e1e" and "furniture" in st["colors_from_file"]
    assert C.read_3mf_colors(job / "model.3mf")["Furniture"] == "#C81E1EFF"


def test_one_colour_file_uses_part_colours(tmp_path):
    m = samples.sample_house_mesh()
    m.visual.face_colors = [180, 180, 180, 255]
    src = tmp_path / "in" / "house.ply"
    src.parent.mkdir()
    m.export(src)
    assert MI.color_samples(src) is None
    job, p, rep = _mesh_job(tmp_path, src)
    assert rep["stages"]["editor_rebuild"]["colors"]["walls"] == C.hex_of(C.PART_COLORS["walls"])
