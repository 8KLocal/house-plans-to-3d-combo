"""Printer-bed splitting: pieces fit, and they carry numbers and dowels."""
import json

import numpy as np
import pytest
import trimesh

from hp3d.bedsplit import number_mesh, split_parts, write_bed


def _slab(width, depth, thick=10):
    box = trimesh.creation.box(bounds=[[0, 0, 0], [width, depth, thick]])
    return {"floor": box}


def test_a_model_that_fits_is_not_cut():
    info = split_parts(_slab(200, 100), (256, 256), mm_per_unit=1)
    assert info["split"] is False
    assert info["pieces"] == []
    assert any("not split" in s for s in info["assumptions"])


def test_wide_model_splits_into_numbered_pieces_that_fit():
    info = split_parts(_slab(400, 80), (256, 256), mm_per_unit=1)
    assert info["split"] is True
    assert len(info["pieces"]) == 2
    assert [p["number"] for p in info["pieces"]] == [1, 2]
    for piece in info["pieces"]:
        whole = trimesh.util.concatenate(list(piece["parts"].values()))
        size = (whole.bounds[1] - whole.bounds[0])
        assert size[0] <= 256 + 0.6
        assert size[1] <= 256 + 0.6
        # The raised number makes the piece taller than the original 10.
        assert size[2] > 10
    # Piece 2 has a dowel sticking out of its low-X face, so it starts before the cut.
    assert info["pieces"][1]["parts"]["floor"].bounds[0][0] < 252


def test_write_bed_exports_stl_and_3mf(tmp_path):
    info = write_bed(_slab(400, 80), tmp_path / "bed", [256, 256], 1)
    assert (tmp_path / "bed" / "piece-01.stl").is_file()
    assert (tmp_path / "bed" / "piece-02.3mf").is_file()
    manifest = json.loads((tmp_path / "bed" / "manifest.json").read_text())
    assert manifest["split"] is True and len(manifest["pieces"]) == 2
    assert info["files"]


def test_number_mesh_grows_with_digits():
    one = number_mesh(7, height=10, depth=1)
    twelve = number_mesh(12, height=10, depth=1)
    assert twelve.bounds[1][0] > one.bounds[1][0]
    assert np.isfinite(twelve.volume)


def test_tiny_bed_is_refused():
    with pytest.raises(ValueError, match="dowel"):
        split_parts(_slab(100, 100), (2, 2), mm_per_unit=1)
