"""Smoke tests: each upstream project imports and runs inside the one venv.

Model-dependent tests are skipped (not faked) when weights are absent.
"""
from pathlib import Path

import numpy as np
import pytest
import trimesh

from hp3d import THIRD_PARTY, WEIGHTS

SAMPLE_PDF = THIRD_PARTY / "Floorplan-Dimractor/data/input/Floorplan_20250928_001404.pdf"
SAMPLE_PNG = THIRD_PARTY / "FloorplanToBlender3d/Images/Examples/example.png"
need_unet = pytest.mark.skipif(not (WEIGHTS / "floorplan-to-3d/best.safetensors").exists(), reason="UNet weights missing")
need_cc = pytest.mark.skipif(not (WEIGHTS / "cubicasa5k/model_best_val_loss_var.pkl").exists(), reason="CubiCasa weights missing")


def test_floorplantoblender3d_classic_walls():
    from hp3d import raster, segment_classic
    w = segment_classic.wall_mask(raster.load_plan(SAMPLE_PNG).rgb)
    assert 0.01 < w.mean() < 0.5


def test_dimractor_reads_room_labels():
    from hp3d import dimensions
    page = dimensions.extract(SAMPLE_PDF)["pages"][0]
    pairs = dimensions.room_pairs(page)
    assert any(p["raw"] == ["12'6\"", "12'0\""] for p in pairs)  # OFFICE 12'6" x 12'0"
    import sys
    assert "src" not in sys.modules  # Dimractor's generic package name must not leak


def test_pymeshfix_closes_a_hole():
    from hp3d import repair
    box = trimesh.creation.box((10, 10, 10))
    broken = trimesh.Trimesh(box.vertices, box.faces[:-2], process=False)  # drop one side
    assert not broken.is_watertight
    assert repair.repair(broken).is_watertight


@need_unet
def test_unet_segments_walls():
    from hp3d import raster, segment_unet
    m = segment_unet.UNetSegmenter().predict(raster.load_plan(SAMPLE_PDF).rgb)
    assert 0.01 < (m == 1).mean() < 0.3


@need_cc
def test_cubicasa_finds_rooms():
    from hp3d import raster, segment_cubicasa
    rooms, icons = segment_cubicasa.CubiCasaSegmenter().predict(raster.load_plan(SAMPLE_PDF).rgb)
    assert (rooms == 2).mean() > 0.01  # class 2 = Wall


@need_unet
def test_full_pipeline_pdf(tmp_path: Path):
    from hp3d.pipeline import run
    rep = run(SAMPLE_PDF, tmp_path)
    bad = {k: v for k, v in rep["stages"].items() if v["status"] != "ok"}
    assert not bad, bad
    m = trimesh.load(tmp_path / "model.stl")
    assert m.is_watertight or all(b.is_watertight for b in m.split(only_watertight=False))
    ppi = rep["scale"]["px_per_inch"]
    assert ppi and 0.8 < ppi < 1.1  # 1/8" = 1' sheet at 100 dpi is ~1.04 px/in


@need_unet
def test_full_pipeline_png_has_no_dimension_stage(tmp_path: Path):
    from hp3d.pipeline import run
    rep = run(SAMPLE_PNG, tmp_path, skip={"segment_rooms[CubiCasa5k]"})
    assert rep["stages"]["dimensions[Floorplan-Dimractor]"]["status"] == "error"  # raster: no text layer
    assert rep["stages"]["dimensions_ocr[local]"]["status"] == "ok"
    # A scale is set only when several dimension labels agree. Otherwise it stays unknown.
    if rep["scale"]["px_per_inch"] is not None:
        assert rep["scale"]["measured"] is True
    assert (tmp_path / "model.stl").exists()
