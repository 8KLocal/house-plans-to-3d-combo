"""Orbit, zoom, and part colours in the 3D preview."""
import numpy as np
import trimesh
from PIL import Image

from hp3d.colors import PART_COLORS
from hp3d.preview import HOME_VIEW, THREE_QUARTER_VIEW, TOP_VIEW, render, render_painter


def _parts():
    floor = trimesh.creation.box(extents=(12, 10, 1))
    floor.apply_translation((0, 0, 0.5))
    wall = trimesh.creation.box(extents=(12, 1, 8))
    wall.apply_translation((0, 4.5, 5))
    return {"floor": floor, "walls": wall}


def _has_hue(image: Image.Image, rgb, minimum=30) -> bool:
    px = np.asarray(image.convert("RGB")).reshape(-1, 3).astype(int)
    colour = np.array(rgb[:3])
    ratio = px / np.maximum(colour, 1)
    hit = (np.ptp(ratio, axis=1) < 0.08) & (ratio.mean(1) > 0.45) & (ratio.mean(1) <= 1.05)
    return int(hit.sum()) > minimum


def test_orbit_zoom_and_part_colours_change_the_picture():
    parts = _parts()
    home = render_painter(parts, elev=HOME_VIEW["elev"], azim=HOME_VIEW["azim"], zoom=1, size=(280, 210), legend=False)
    turned = render_painter(parts, elev=HOME_VIEW["elev"], azim=HOME_VIEW["azim"] - 70, zoom=1, size=(280, 210), legend=False)
    closer = render_painter(parts, elev=HOME_VIEW["elev"], azim=HOME_VIEW["azim"], zoom=2.2, size=(280, 210), legend=False)
    assert not np.array_equal(np.asarray(home), np.asarray(turned))
    assert not np.array_equal(np.asarray(home), np.asarray(closer))
    assert _has_hue(home, PART_COLORS["floor"])
    assert _has_hue(home, PART_COLORS["walls"])


def test_top_and_three_quarter_views_are_different(tmp_path):
    parts = _parts()
    top = tmp_path / "top.png"
    three = tmp_path / "three.png"
    render(parts, top, size=(180, 140), elev=TOP_VIEW["elev"], azim=TOP_VIEW["azim"], legend=False, ss=1)
    render(parts, three, size=(180, 140), elev=THREE_QUARTER_VIEW["elev"], azim=THREE_QUARTER_VIEW["azim"], legend=False, ss=1)
    top_im, three_im = Image.open(top), Image.open(three)
    assert top_im.size == (180, 140)
    assert not np.array_equal(np.asarray(top_im), np.asarray(three_im))
    assert _has_hue(top_im, PART_COLORS["floor"])
    assert _has_hue(three_im, PART_COLORS["walls"])
