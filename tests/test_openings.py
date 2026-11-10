"""Windows, stairs, door frames, and scale read from dimension labels."""
import json
import sys
import types
from pathlib import Path

import numpy as np
import pytest
import trimesh
from PIL import Image, ImageDraw

from hp3d import build3d, colors, editor as E, quiz
from hp3d.ocr_dims import MEASURED_SOURCE, agree, install_quote_hook, labels_are_measured, parse_dimension, parse_lengths
from hp3d.snapgeom import snap_ring

ROOT = Path(__file__).resolve().parents[1]
LABELED = ROOT / "plans" / "labeled_room.png"


def test_dimension_parser_refuses_a_bare_number():
    assert parse_dimension("12'-6\"") == pytest.approx(150)
    assert parse_dimension("10'") == pytest.approx(120)
    assert parse_dimension("36\"") == pytest.approx(36)
    assert parse_dimension("8 ft 6 in") == pytest.approx(102)
    assert parse_dimension("2.5 m") == pytest.approx(2.5 * 39.3701)
    assert parse_lengths("12'-6\" x 10'-0\"") == pytest.approx([150, 120])
    assert parse_dimension("12") is None
    assert parse_dimension("12-6") is None
    assert parse_lengths("room 12") == []


def test_scale_needs_labels_that_agree():
    one = agree([{"ppi": 4.0, "text": "12'-6\""}])
    assert one["px_per_inch"] is None and one["status"] == "uncertain"
    clash = agree([{"ppi": 4.0, "text": "a"}, {"ppi": 6.0, "text": "b"}])
    assert clash["px_per_inch"] is None and "not agree" in clash["detail"]
    ok = agree([{"ppi": 4.0, "text": "a"}, {"ppi": 4.1, "text": "b"}])
    assert ok["status"] == "measured"
    assert ok["px_per_inch"] == pytest.approx(4.05, abs=0.02)


def test_labeled_plan_image_sets_a_measured_scale():
    """plans/labeled_room.png is a 12'-6\" by 10'-0\" room drawn at 4 px per inch."""
    assert LABELED.is_file()
    from hp3d.ocr_dims import scale_from_image
    rgb = np.array(Image.open(LABELED).convert("RGB"))
    scale = scale_from_image(rgb)
    assert scale["measured"] is True
    assert scale["engine"] == "rapidocr"
    assert scale["px_per_inch"] == pytest.approx(4.0, rel=0.03)
    assert scale["source"] == MEASURED_SOURCE
    texts = " ".join(lab["text"] for lab in scale["labels"])
    assert "12'-6\"" in texts and "10'-0\"" in texts


def test_label_scale_lands_in_job_json_and_skips_the_size_question(tmp_path):
    job = tmp_path / "job"
    params = E.JobParams(plan=str(LABELED))
    rep = E.detect(job, params, log=lambda _s: None)
    assert rep["scale"]["measured"] is True
    assert rep["stages"]["dimensions_ocr[local]"]["status"] == "ok"
    saved = json.loads((job / "job.json").read_text())
    assert saved["px_per_inch"] == pytest.approx(4.0, rel=0.03)
    assert saved["measured"] == ["px_per_inch"]
    assert "dimension label" in saved["scale_source"]
    assert labels_are_measured(E.JobParams.load(job))
    polys = E.load_polys(job)
    assert polys["window"] and not any(p.get("leaf") for p in polys.get("door", []))
    asked = [q["id"] for q in quiz.questions(job, E.JobParams.load(job), polys)]
    assert "size" not in asked
    unknown = E.JobParams(plan=str(LABELED))
    assert "size" in [q["id"] for q in quiz.questions(job, unknown, polys)]


def test_real_quote_pdf_shows_a_price_for_a_label_scale(tmp_path, monkeypatch):
    """PR #4 prices a job only when the size is a real measurement. Labels count."""
    from hp3d import quote
    from hp3d.quote import Rates
    monkeypatch.setattr(quote, "brand_logo_png", lambda: None)
    params = E.JobParams(plan="plan.png", px_per_inch=4.0, scale_source=MEASURED_SOURCE,
                         measured=["px_per_inch"], print_mm_per_unit=1.0)
    assert quote.size_block_reason(params) is None
    job = tmp_path / "cottage"
    job.mkdir()
    floor = trimesh.creation.box(extents=(12, 10, 1))
    floor.apply_translation((0, 0, 0.5))
    wall = trimesh.creation.box(extents=(12, 1, 8))
    wall.apply_translation((0, 4.5, 5))
    scene = trimesh.Scene()
    scene.add_geometry(floor, node_name="floor", geom_name="floor")
    scene.add_geometry(wall, node_name="walls", geom_name="walls")
    scene.export(job / "model.glb")
    params.save(job)
    pdf = tmp_path / "quote.pdf"
    built = quote.build_quote(job, params, Rates(20.0, 10.0, 10.0), pdf)
    assert built.total_usd > 0
    raw = pdf.read_bytes().decode("latin-1", errors="ignore")
    assert "dimension labels" in raw
    guessed = E.JobParams(px_per_inch=1.0, scale_source="estimated from the plan", print_mm_per_unit=1.0)
    with pytest.raises(ValueError, match="estimate"):
        quote.build_quote(job, guessed, Rates(20.0, 10.0, 10.0), tmp_path / "no.pdf")


def test_quote_prices_a_label_scale_and_still_blocks_a_guess():
    mod = types.SimpleNamespace()

    def block(_params):
        return "This size is an estimate."

    def note(_params):
        return "estimate"

    mod.size_block_reason = block
    mod.size_confidence = note
    sys.modules["hp3d.quote"] = mod
    try:
        install_quote_hook()
        measured = E.JobParams(px_per_inch=4.0, scale_source=MEASURED_SOURCE, measured=["px_per_inch"])
        guessed = E.JobParams(px_per_inch=1.0, scale_source="estimated from the plan")
        assert mod.size_block_reason(measured) is None
        assert "estimate" in mod.size_block_reason(guessed)
        assert "dimension labels" in mod.size_confidence(measured)
        assert labels_are_measured(measured)
        assert not labels_are_measured(guessed)
    finally:
        sys.modules.pop("hp3d.quote", None)


def test_snap_straightens_a_near_axis_and_leaves_a_diagonal():
    rect = np.array([[0, 0], [120, 0], [120, 50], [0, 50]], float)
    theta = np.radians(4.0)
    center = rect.mean(0)
    rotate = np.array([[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]])
    skew = (rect - center) @ rotate.T + center
    ring, notes = snap_ring(skew.tolist())
    pts = np.array(ring, float)
    edges = np.diff(np.vstack([pts, pts[:1]]), axis=0)
    angles = np.abs(np.degrees(np.arctan2(edges[:, 1], edges[:, 0]))) % 180
    off = np.minimum(np.minimum(angles, 180 - angles), np.abs(angles - 90))
    assert off.max() < 1.0
    assert any(n["action"] == "snapped" for n in notes)
    diagonal = [[0, 0], [80, 60], [80, 100], [0, 40]]  # 37 deg, not a right-angle plan
    kept, notes = snap_ring(diagonal)
    assert any(n["action"] == "left" for n in notes)
    assert np.allclose(np.array(kept, float)[:4], diagonal, atol=1.0)


def _faces_in(mesh, x0, x1, y0, y1, z0, z1) -> bool:
    """A triangle centre lies in this image-space box (y grows down, as on the plan)."""
    if mesh is None or len(getattr(mesh, "faces", [])) == 0:
        return False
    c = np.asarray(mesh.triangles_center)
    y = -c[:, 1]
    return bool(np.any((c[:, 0] > x0) & (c[:, 0] < x1) & (y > y0) & (y < y1) & (c[:, 2] > z0) & (c[:, 2] < z1)))


def test_window_is_a_cutout_with_a_sill_and_the_door_has_no_leaf():
    wall = E.add_rect({}, "wall", 0, 0, 200, 10)
    wall = E.add_rect(wall, "wall", 0, 100, 200, 110)
    polys = E.add_rect(wall, "window", 60, 0, 120, 10)
    # Door opening is cut. A leaf rectangle drawn in the room must not become a solid.
    polys = E.add_rect(polys, "door", 40, 100, 80, 110)
    polys["door"][0]["cut"] = True
    polys = E.add_rect(polys, "door", 40, 60, 80, 98)  # the leaf, inside the room
    parts = build3d.build(polys, 1.0, wall_h=96, floor_t=4, sill=36, lintel=80, frame_trim=4)
    assert "door_frames" in parts and "windows" in parts
    assert _faces_in(parts["windows"], 70, 110, 1, 9, 4 + 30, 4 + 40)   # top of the sill
    assert not _faces_in(parts["walls"], 70, 110, 1, 9, 45, 75)         # opening is cut out of the wall
    assert not _faces_in(parts["windows"], 70, 110, 1, 9, 45, 75)       # nothing fills the opening
    assert _faces_in(parts["walls"], 5, 55, 0.5, 9.5, 90, 110)          # wall beside the window still reaches the ceiling
    assert not any(_faces_in(m, 45, 75, 65, 95, 20, 80) for m in parts.values())  # no door leaf
    assert all(m.is_watertight for m in parts.values())


def test_stairs_are_stepped_and_coloured_and_the_logo_still_fits(tmp_path):
    walls = [E.add_rect({}, "wall", *r)["wall"][0] for r in
             [(0, 0, 240, 8), (0, 160, 240, 168), (0, 0, 8, 168), (232, 0, 240, 168)]]
    stair = {"outer": [[40, 30], [100, 30], [100, 130], [40, 130]], "holes": [],
             "n_treads": 8, "travel": "y", "rise_in": None, "rise_source": "not on plan"}
    polys = {"wall": walls, "door": [], "window": [], "stair": [stair]}
    parts = build3d.build(polys, 1.0, wall_h=96, floor_t=4)
    assert "stairs" in parts and "floor" in parts and "walls" in parts
    zs = np.unique(np.round(parts["stairs"].vertices[:, 2], 1))
    assert len(zs[zs > 5]) >= 8
    assert parts["stairs"].bounds[1][2] == pytest.approx(4 + 96, abs=0.5)
    from hp3d import brand, repair
    from hp3d.colors import export_3mf
    fixed = {k: repair.repair(m) for k, m in parts.items()}
    logo = brand.apply_underside_logo(fixed)
    assert logo["status"] == "ok"
    path = tmp_path / "model.3mf"
    export_3mf(fixed, path, name="house")
    colours = colors.read_3mf_colors(path)
    assert colours["Stairs"].upper().startswith(colors.hex_of(colors.PART_COLORS["stairs"]).upper())
    assert {"Floor", "Walls"} <= set(colours)
    glb = tmp_path / "model.glb"
    colors.export_glb(fixed, glb)
    scene = trimesh.load(glb)
    assert "stairs" in scene.geometry


def test_detected_window_stair_and_door_on_a_drawn_plan():
    img = Image.new("RGB", (420, 320), "white")
    dr = ImageDraw.Draw(img)
    for y in (40, 52):
        dr.line([(30, y), (380, y)], fill=(0, 0, 0), width=2)
    for y in (250, 262):
        dr.line([(30, y), (120, y)], fill=(0, 0, 0), width=2)
        dr.line([(190, y), (380, y)], fill=(0, 0, 0), width=2)
    for x in (30, 42):
        dr.line([(x, 40), (x, 262)], fill=(0, 0, 0), width=2)
    for x in (368, 380):
        dr.line([(x, 40), (x, 262)], fill=(0, 0, 0), width=2)
    dr.line([(90, 46), (170, 46)], fill=(0, 0, 0), width=2)
    dr.line([(90, 40), (90, 52)], fill=(0, 0, 0), width=2)
    dr.line([(170, 40), (170, 52)], fill=(0, 0, 0), width=2)
    for y in range(80, 210, 16):
        dr.line([(230, y), (300, y)], fill=(0, 0, 0), width=1)
    arr = np.array(img)
    # quarter-circle swing into the room from the bottom gap
    import cv2
    cv2.ellipse(arr, (120, 250), (50, 50), 0, 180, 270, (0, 0, 0), 2)
    from hp3d.symbols import detect
    found = detect(arr)
    assert len(found["windows"]) == 1
    assert found["stairs"] and found["stairs"][0]["n_treads"] >= 5
    assert found["doors"] and found["doors"][0]["leaf"] is False and found["doors"][0]["cut"] is True
    box = found["windows"][0]["outer"]
    xs = [p[0] for p in box]
    assert min(xs) < 100 and max(xs) > 160
    from hp3d.confidence import build_report
    report = build_report({"wall": found["walls"], "window": found["windows"], "door": found["doors"],
                           "stair": found["stairs"]}, found, {}, {}, {}, 96, 36, 80)
    text = " ".join(i["detail"] for i in report["items"])
    assert "placeholder" in text and "not measured" in text
    assert "Door leaves are not built" in text
