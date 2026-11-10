"""Walls are not left as scraps, and drawn furniture is placed on the first build."""
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from hp3d import editor as E
from hp3d.furniture import from_icon_mask
from hp3d.structure import apply, choose_walls, wall_area

ROOT = Path(__file__).resolve().parents[1]
LIVING = ROOT / "plans" / "living.png"


def _tiny_wall():
    return {"outer": [[0, 0], [4, 0], [4, 2], [0, 2]], "holes": []}


def test_tiny_segmented_walls_fall_back_to_the_line_tracing():
    rgb = np.array(Image.open(LIVING).convert("RGB"))
    crop = E.find_green_outline(rgb)
    assert crop is not None
    x0, y0, x1, y1 = crop
    unit = rgb[y0:y1, x0:x1]
    result = apply(unit, {"wall": [_tiny_wall()], "door": [], "window": [], "furniture": []})
    walls = result["polys"]["wall"]
    area = wall_area(walls)
    assert len(walls) >= 8
    assert area > 0.01 * unit.shape[0] * unit.shape[1]
    assert result["summary"]["wall_source"] == "classical-lines"
    assert "almost empty" in (result["summary"]["wall_note"] or "").lower()
    # The whole sheet, with no segmented walls, is also a real set of walls.
    whole = apply(rgb, None)
    assert wall_area(whole["polys"]["wall"]) > 0.02 * rgb.shape[0] * rgb.shape[1]


def test_a_solid_wall_mask_is_not_replaced():
    big = {"outer": [[0, 0], [200, 0], [200, 12], [0, 12]], "holes": []}
    chosen, source, note = choose_walls([big], [_tiny_wall()], (100, 300))
    assert source == "segmentation" and note is None
    assert chosen == [big]


def test_sample_plan_symbols_are_placed_without_a_manual_add():
    """A toilet icon and a drawn bed land in the furniture list on their own."""
    icons = np.zeros((320, 320), np.uint8)
    icons[30:70, 220:260] = 5  # toilet
    img = np.full((320, 320, 3), 255, np.uint8)
    cv2.rectangle(img, (20, 20), (100, 180), (0, 0, 0), 2)
    cv2.line(img, (28, 40), (92, 40), (0, 0, 0), 3)
    pieces = from_icon_mask(icons, None, img)
    kinds = {p.get("furn_type") for p in pieces}
    assert "toilet" in kinds
    assert kinds & {"bed_single", "bed_double"}
    # The first build asks for fixtures unless the user turns that off.
    assert E.JobParams().find_fixtures is True
