"""Underside maker's mark (hp3d.brand): placed on every build, watertight, small-base fallback."""
import numpy as np
import pytest
import trimesh

from hp3d import brand, build3d, repair
from hp3d import editor as E


def _parts(w_in: float, h_in: float, floor_t: float = 4.0) -> dict:
    t = 4
    rects = [(0, 0, w_in, t), (0, h_in - t, w_in, h_in), (0, 0, t, h_in), (w_in - t, 0, w_in, h_in)]
    polys = {"wall": [E.add_rect({}, "wall", *r)["wall"][0] for r in rects], "window": [], "door": []}
    parts = build3d.build(polys, 1.0, floor_t=floor_t)
    return {k: repair.repair(m) for k, m in parts.items()}


def test_logo_assets_are_positive_marks():
    # Shipped relief STLs are plates with letter holes; brand._positive must return the letters.
    for name in brand.VARIANTS:
        m, hull = brand._logo(name)
        assert m.is_watertight and m.volume > 0
        lo, hi = m.bounds
        assert not brand._covered(m, [lo[0] + 0.5, lo[1] + 0.5])      # corner is empty, not plate
    full, _ = brand._logo("8K-by-AK")
    assert len(full.split()) >= 4                                     # 8K, divider bar, "by AK" letters


def test_parts_have_outward_normals():
    for m in _parts(120, 90).values():
        assert m.volume > 0


def test_large_base_gets_full_logo_recessed_and_watertight():
    parts = _parts(300, 200)                         # 300 x 200 mm slab when printed 1 in = 1 mm
    v0, b0 = parts["floor"].volume, parts["floor"].bounds.copy()
    rep = brand.apply_underside_logo(parts)
    f = parts["floor"]
    assert rep["status"] == "ok" and rep["variant"] == "8K-by-AK" and rep["width_mm"] == pytest.approx(75.3, abs=0.5)
    assert f.is_watertight and f.volume > 0 and f.volume < v0
    assert np.allclose(f.bounds, b0)                 # recess only: no bump below the bed, no cut-through
    assert rep["mirrored"] and rep["depth_mm"] == pytest.approx(1.0)
    # The recess faces sit exactly at depth, inside the slab.
    z = f.vertices[:, 2]
    assert np.any(np.isclose(z, 1.0, atol=1e-4)) and z.min() == pytest.approx(0) and z.max() == pytest.approx(4)


def test_small_base_uses_8k_only():
    parts = _parts(55, 40)                           # too small for a >= 50 mm full lockup + margin
    rep = brand.apply_underside_logo(parts)
    assert rep["status"] == "ok" and rep["variant"] == "8K-only"
    assert brand.MINI_MIN_MM <= rep["width_mm"] <= 47.1
    assert parts["floor"].is_watertight


def test_tiny_base_or_thin_slab_skips_without_damage():
    parts = _parts(16, 12)
    v0 = parts["floor"].volume
    assert brand.apply_underside_logo(parts)["status"] == "skipped"
    assert parts["floor"].volume == pytest.approx(v0)
    thin = _parts(300, 200, floor_t=0.5)
    assert brand.apply_underside_logo(thin)["status"] == "skipped"


def test_print_scale_changes_choice():
    # Same 300 x 200 in house printed at 0.15 mm per inch is a 45 x 30 mm base -> 8K-only mark.
    assert brand.choose(brand.underside(_parts(300, 200)["floor"])[0], 0.15)["variant"] == "8K-only"


def test_editor_rebuild_marks_every_build(tmp_path):
    job = tmp_path / "job"; job.mkdir()
    from PIL import Image
    Image.fromarray(np.full((200, 300, 3), 255, np.uint8)).save(job / "source_crop.png")
    rects = [(0, 0, 300, 4), (0, 196, 300, 200), (0, 0, 4, 200), (296, 0, 300, 200)]
    E.save_polys(job, {"wall": [E.add_rect({}, "wall", *r)["wall"][0] for r in rects], "door": [], "window": []})
    p = E.JobParams(plan="x.png", px_per_inch=1.0, user_set=["px_per_inch"])
    for wall_h in (96.0, 120.0):                     # rebuild after an edit is marked again
        p.wall_h = wall_h
        rep = E.rebuild(job, p, log=lambda s: None)
        logo = rep["stages"]["editor_rebuild"]["logo"]
        assert logo["status"] == "ok" and logo["variant"] == "8K-by-AK"
        assert rep["stages"]["editor_rebuild"]["watertight"]["floor"]
        assert trimesh.load(job / "part_floor.stl").is_watertight
        assert (job / "preview_underside.png").exists()
