"""Parametric furniture: real sizes, support-free meshes, phrases, and plan symbols."""
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import cv2
import numpy as np
import pytest
import trimesh
from PIL import Image

from hp3d import build3d, colors as C, editor as E, furniture as F


def _blank(h=400, w=400):
    return np.full((h, w, 3), 255, np.uint8)


def test_catalog_is_realistic_and_support_free():
    assert set(F.kinds()) >= {
        "bed_single", "bed_double", "bed_twins", "table", "chair", "sofa", "counter",
        "sink", "stove", "fridge", "dishwasher", "toilet", "tub", "shower", "vanity",
        "wardrobe", "desk",
    }
    for kind in F.kinds():
        spec = F.SPECS[kind]
        mesh = F.local_mesh(kind)
        ext = mesh.bounds[1] - mesh.bounds[0]
        assert mesh.is_watertight, kind
        assert F.support_risk(mesh) == 0, kind
        assert mesh.bounds[0][2] == pytest.approx(0, abs=0.05)
        assert ext[0] == pytest.approx(spec["w"], abs=1.5), kind
        assert ext[1] == pytest.approx(spec["d"], abs=1.5), kind
        assert ext[2] == pytest.approx(spec["h"], abs=1.0), kind
        assert mesh.volume > 0
    twins = F.local_mesh("bed_twins")
    single = F.local_mesh("bed_single")
    assert len(twins.split(only_watertight=False)) == 2
    assert twins.bounds[1][0] - twins.bounds[0][0] > (single.bounds[1][0] - single.bounds[0][0]) + 20


def test_plain_language_add_move_rotate_resize_recolor_remove(tmp_path):
    phrases = {
        "single bed": "bed_single", "double bed": "bed_double", "two single beds": "bed_twins",
        "table": "table", "chair": "chair", "sofa": "sofa", "kitchen counter": "counter",
        "sink": "sink", "stove": "stove", "fridge": "fridge", "dishwasher": "dishwasher",
        "toilet": "toilet", "tub": "tub", "shower": "shower", "vanity": "vanity",
        "wardrobe": "wardrobe", "desk": "desk",
    }
    for name, kind in phrases.items():
        act = E.parse_command(f"add a {name} at 12, 34")[0]
        assert act["op"] == "add_furniture" and act["furn_type"] == kind
    assert E.parse_command("add two singles side by side at 1, 2")[0]["furn_type"] == "bed_twins"
    assert E.parse_command("add a bed at 1, 2 facing north")[0]["rotation"] == 180
    assert E.parse_command("rotate selected 90 degrees") == [{"op": "rotate", "degrees": 90.0}]
    assert E.parse_command("turn selected left")[0]["degrees"] == -90
    assert E.parse_command("recolor selected red")[0]["rgb"] == [200, 48, 48]
    assert E.parse_command("paint selected #336699")[0]["name"] == "#336699"
    assert E.parse_command("resize selected to 30 inches by 50 inches")[0] == {
        "op": "resize_to", "w": 30.0, "d": 50.0}
    assert E.parse_command("make selected 20 inches tall")[0]["height"] == 20
    with pytest.raises(ValueError, match="colour"):
        E.parse_command("recolor selected plaid")
    with pytest.raises(ValueError, match="didn't understand"):
        E.parse_command("make it look nicer")
    # a plain block is still the old rectangle command
    assert E.parse_command("add furniture 10,10 to 40,30")[0]["kind"] == "furniture"

    job = tmp_path / "job"
    job.mkdir()
    Image.fromarray(np.full((160, 220, 3), 255, np.uint8)).save(job / "source_crop.png")
    walls = [E.add_rect({"wall": []}, "wall", *r)["wall"][0] for r in
             [(0, 0, 220, 4), (0, 156, 220, 160), (0, 0, 4, 160), (216, 0, 220, 160)]]
    E.save_polys(job, {"wall": walls, "door": [], "window": [], "furniture": []})
    p = E.JobParams(plan="x.png", px_per_inch=2.0, user_set=["px_per_inch"])
    p.save(job)

    with pytest.raises(ValueError, match="scale"):
        E.apply_actions(job, E.JobParams(plan="x.png"), E.parse_command("add a chair at 10, 10"), None)

    msgs, need, sel = E.apply_actions(job, p, E.parse_command("add a single bed 40,20 to 100,90"), None)
    assert sel == ("furniture", 0) and "single bed" in msgs[0]
    bed = E.load_polys(job)["furniture"][0]
    assert bed["furn_type"] == "bed_single" and bed["added_in_app"]

    E.apply_actions(job, p, E.parse_command("move selected 6 inches right"), sel)
    moved = E.load_polys(job)["furniture"][0]["outer"]
    assert min(x for x, _ in moved) == pytest.approx(52)  # 6 in * 2 px/in

    E.apply_actions(job, p, E.parse_command("rotate selected 90 degrees"), sel)
    rot = np.asarray(E.load_polys(job)["furniture"][0]["outer"], float)
    assert (rot[:, 0].max() - rot[:, 0].min()) == pytest.approx(70, abs=1.5)  # the old 70px depth

    E.apply_actions(job, p, E.parse_command("resize selected to 30 inches by 40 inches"), sel)
    box = np.asarray(E.load_polys(job)["furniture"][0]["outer"], float)
    span = box.max(0) - box.min(0)
    assert sorted(span.round(0)) == [60, 80]  # 30 and 40 in at 2 px/in, either axis

    E.apply_actions(job, p, E.parse_command("make selected 20 inches tall"), sel)
    E.apply_actions(job, p, E.parse_command("recolor selected red"), sel)
    rep = E.rebuild(job, p, log=lambda s: None)
    st = rep["stages"]["editor_rebuild"]
    assert "furniture" in st["parts"] and st["watertight"]["furniture"]
    furn = trimesh.load(job / "model.glb").geometry["furniture"]
    assert furn.bounds[1][2] - 0 == pytest.approx(p.floor_t + 20, abs=0.8)
    assert C.read_3mf_colors(job / "model.3mf")["Furniture"] == "#C83030FF"
    factor = np.asarray(furn.visual.material.baseColorFactor)[:3].astype(float)
    if factor.max() > 1.5:
        factor = factor / 255.0
    assert np.allclose(factor, [200 / 255, 48 / 255, 48 / 255], atol=0.02)
    assert C.read_3mf_colors(job / "model.3mf")["Walls"].upper().startswith("#686B76")

    E.apply_actions(job, p, E.parse_command("remove selected"), ("furniture", 0))
    assert E.load_polys(job)["furniture"] == []
    rep = E.rebuild(job, p, log=lambda s: None)
    assert "furniture" not in rep["stages"]["editor_rebuild"]["parts"]


def test_two_pieces_keep_separate_colours_in_one_furniture_part(tmp_path):
    job = tmp_path / "job"
    job.mkdir()
    Image.fromarray(np.full((180, 240, 3), 255, np.uint8)).save(job / "source_crop.png")
    wall = E.add_rect({}, "wall", 0, 0, 240, 6)["wall"]
    E.save_polys(job, {"wall": wall, "door": [], "window": [], "furniture": []})
    p = E.JobParams(plan="x.png", px_per_inch=2.0, user_set=["px_per_inch"])
    p.save(job)
    _, _, sel = E.apply_actions(job, p, E.parse_command("add a chair 20,20 to 50,50"), None)
    E.apply_actions(job, p, E.parse_command("recolor selected blue"), sel)
    _, _, sel = E.apply_actions(job, p, E.parse_command("add a desk 80,20 to 160,60"), None)
    E.apply_actions(job, p, E.parse_command("recolor selected wood"), sel)
    E.rebuild(job, p, log=lambda s: None)
    glb = trimesh.load(job / "model.glb")
    assert set(glb.geometry) >= {"walls", "furniture"}
    colors = {tuple(int(x) for x in c[:3]) for c in np.asarray(glb.geometry["furniture"].visual.face_colors)}
    assert (60, 120, 200) in colors and (176, 122, 74) in colors
    # Still one Furniture object in the 3MF, with both colours available to the slicer.
    with zipfile.ZipFile(job / "model.3mf") as z:
        root = ET.fromstring(z.read("3D/3dmodel.model"))
    ns = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}
    names = [o.get("name") for o in root.iterfind(".//m:object", ns)]
    assert names.count("Furniture") == 1
    bases = [b.get("displaycolor").upper() for b in root.iterfind(".//m:basematerials/m:base", ns)]
    assert "#3C78C8FF" in bases and "#B07A4AFF" in bases


def test_symbols_only_when_the_plan_shows_them():
    rooms = np.zeros((200, 200), np.uint8)
    rooms[10:180, 10:180] = 5  # one bedroom
    icons = np.zeros_like(rooms)
    white = _blank(200, 200)
    assert F.from_icon_mask(icons, rooms, white) == []
    rooms2 = np.zeros_like(rooms)
    rooms2[0:80, 0:90] = 3
    rooms2[110:190, 110:190] = 3  # two kitchens, nothing drawn
    assert F.from_icon_mask(icons, rooms2, white) == []

    img = _blank(320, 320)
    cv2.rectangle(img, (20, 20), (100, 180), (0, 0, 0), 2)
    cv2.line(img, (28, 40), (92, 40), (0, 0, 0), 3)
    cv2.rectangle(img, (160, 20), (250, 180), (0, 0, 0), 2)
    cv2.line(img, (168, 40), (242, 40), (0, 0, 0), 3)
    bed_room = np.zeros(img.shape[:2], np.uint8)
    bed_room[5:300, 5:300] = 5
    beds = F.from_icon_mask(np.zeros_like(bed_room), bed_room, img)
    assert len(beds) == 2
    assert {b["furn_type"] for b in beds} <= {"bed_single", "bed_double"}

    img = _blank()
    cv2.rectangle(img, (40, 40), (200, 175), (0, 0, 0), 2)
    cv2.line(img, (55, 58), (110, 58), (0, 0, 0), 2)
    cv2.line(img, (130, 58), (185, 58), (0, 0, 0), 2)
    cv2.line(img, (118, 52), (118, 165), (0, 0, 0), 2)
    assert [p["furn_type"] for p in F.drawn_symbols(img)] == ["bed_twins"]

    img = _blank()
    cv2.rectangle(img, (30, 50), (320, 110), (0, 0, 0), 2)
    cv2.line(img, (40, 64), (310, 64), (0, 0, 0), 2)
    assert F.drawn_symbols(img)[0]["furn_type"] == "sofa"

    img = _blank()
    cv2.rectangle(img, (80, 70), (210, 160), (0, 0, 0), 2)
    cv2.rectangle(img, (120, 168), (152, 200), (0, 0, 0), 2)
    cv2.line(img, (124, 176), (148, 176), (0, 0, 0), 2)
    assert set(p["furn_type"] for p in F.drawn_symbols(img)) == {"table", "chair"}

    img = _blank()
    cv2.rectangle(img, (30, 40), (220, 100), (0, 0, 0), 2)
    cv2.rectangle(img, (100, 108), (132, 140), (0, 0, 0), 2)
    cv2.line(img, (104, 116), (128, 116), (0, 0, 0), 2)
    assert set(p["furn_type"] for p in F.drawn_symbols(img)) == {"desk", "chair"}

    img = _blank()
    cv2.rectangle(img, (20, 40), (340, 95), (0, 0, 0), 2)
    cv2.rectangle(img, (150, 55), (200, 80), (0, 0, 0), 2)
    assert [p["furn_type"] for p in F.drawn_symbols(img)] == ["counter"]

    # An empty outline, a filled wall and a blank page add nothing.
    img = _blank()
    cv2.rectangle(img, (40, 40), (200, 200), (0, 0, 0), 2)
    assert F.drawn_symbols(img) == []
    img = _blank()
    cv2.rectangle(img, (40, 40), (200, 200), (0, 0, 0), -1)
    assert F.drawn_symbols(img) == []


def test_icon_mask_maps_symbols_and_does_not_invent_a_kitchen():
    icons = np.zeros((200, 200), np.uint8)
    icons[40:85, 30:70] = 5
    got = F.from_icon_mask(icons)
    assert len(got) == 1 and got[0]["furn_type"] == "toilet"

    icons[:] = 0
    icons[30:70, 15:150] = 9
    assert F.from_icon_mask(icons)[0]["furn_type"] == "tub"
    icons[:] = 0
    icons[40:100, 40:105] = 9
    assert F.from_icon_mask(icons)[0]["furn_type"] == "shower"

    icons[:] = 0
    icons[40:80, 15:130] = 4
    assert F.from_icon_mask(icons)[0]["furn_type"] == "stove"
    icons[:] = 0
    icons[40:110, 40:112] = 4
    assert F.from_icon_mask(icons)[0]["furn_type"] == "fridge"
    icons[:] = 0
    icons[40:90, 30:100] = 4  # 50 by 70, aspect about 1.4 → dishwasher
    assert F.from_icon_mask(icons)[0]["furn_type"] == "dishwasher"

    icons[:] = 0
    icons[50:85, 15:130] = 6
    rooms = np.full((200, 200), 6, np.uint8)  # bath
    assert F.from_icon_mask(icons, rooms)[0]["furn_type"] == "vanity"
    assert F.from_icon_mask(icons, None)[0]["furn_type"] == "sink"

    icons[:] = 0
    icons[40:90, 40:85] = 3
    assert F.from_icon_mask(icons)[0]["furn_type"] == "wardrobe"
    icons[:] = 0
    icons[0:160, 0:160] = 3  # a whole storage zone, not a wardrobe symbol
    assert F.from_icon_mask(icons) == []

    icons[:] = 0
    icons[30:70, 30:70] = 8
    fire = F.from_icon_mask(icons)
    assert len(fire) == 1 and fire[0].get("furn_type") is None and fire[0]["label"] == "fireplace"

    # Two kitchen rooms and one stove symbol: one stove, not a second kitchen's worth of fixtures.
    icons[:] = 0
    icons[20:55, 10:80] = 4
    rooms = np.zeros((200, 200), np.uint8)
    rooms[0:90, 0:90] = 3
    rooms[110:190, 110:190] = 3
    pieces = F.from_icon_mask(icons, rooms, _blank(200, 200))
    assert [p["furn_type"] for p in pieces] == ["stove"]


def test_icon_ids_match_cubicasa_source():
    import hp3d
    text = (Path(hp3d.__file__).parent / "segment_cubicasa.py").read_text()
    assert '"Closet", "Electrical Appliance", "Toilet", "Sink"' in text
    assert '"Sauna Bench", "Fire Place", "Bathtub"' in text


def test_parametric_piece_is_its_own_part_on_the_slab():
    poly = {"outer": F.quad(80, 60, 39, 75, 0), "holes": [], "furn_type": "bed_single", "label": "single bed"}
    wall = E.add_rect({}, "wall", 0, 0, 160, 6)
    parts = build3d.build({"wall": wall["wall"], "furniture": [poly]}, 1.0, floor_t=4, wall_h=96)
    assert "furniture" in parts and parts["furniture"].is_watertight
    assert parts["furniture"].bounds[0][2] == pytest.approx(4, abs=0.2)
    # Headboard height is the catalog 44 in, standing on the 4 in slab.
    assert parts["furniture"].bounds[1][2] == pytest.approx(48, abs=0.8)
    # A plain block with no type is still a block, and a typed bed is not fused into the wall.
    block = E.add_rect({}, "furniture", 10, 20, 30, 40)["furniture"][0]
    plain = build3d.build({"wall": wall["wall"], "furniture": [block]}, 1.0, furniture_h=30, floor_t=4)
    assert plain["furniture"].volume == pytest.approx(20 * 20 * 30, rel=0.05)
