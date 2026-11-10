"""Customer quote: measured size, typed prices, and the PDF."""
import io
import json
import re

import numpy as np
import pytest
import trimesh
from PIL import Image

from hp3d import editor as E
from hp3d import quote
from hp3d.quote import Rates


def _rates(**kwargs):
    base = dict(filament_usd_per_kg=20.0, hourly_usd=10.0, markup_percent=10.0)
    base.update(kwargs)
    return Rates(**base)


def _expected(volume_in3, mm, filament, hourly, markup_pct):
    """Independent of quote.price_estimate, using the published constants."""
    plastic_mm3 = volume_in3 * mm ** 3
    grams = plastic_mm3 / 1000.0 * quote.PLA_DENSITY_G_CM3
    hours = plastic_mm3 / quote.ESTIMATED_FLOW_MM3_PER_S / 3600.0
    filament_usd = round(grams / 1000.0 * filament + 1e-9, 2)
    time_usd = round(hours * hourly + 1e-9, 2)
    subtotal = round(filament_usd + time_usd + 1e-9, 2)
    markup_usd = round(subtotal * markup_pct / 100.0 + 1e-9, 2)
    total = round(subtotal + markup_usd + 1e-9, 2)
    return grams, hours, filament_usd, time_usd, subtotal, markup_usd, total


def test_price_comes_from_volume_and_typed_rates_only():
    assert quote.PLA_DENSITY_G_CM3 == 1.24
    assert quote.ESTIMATED_FLOW_MM3_PER_S == 8.0
    money = quote.price_estimate(1000.0, 10.0, _rates())
    grams, hours, filament_usd, time_usd, subtotal, markup_usd, total = _expected(1000.0, 10.0, 20.0, 10.0, 10.0)
    assert money["plastic_g"] == pytest.approx(grams)
    assert money["hours"] == pytest.approx(hours)
    assert money["filament_usd"] == filament_usd
    assert money["time_usd"] == time_usd
    assert money["subtotal_usd"] == subtotal
    assert money["markup_usd"] == markup_usd
    assert money["total_usd"] == pytest.approx(409.22, abs=0.001)
    assert total == pytest.approx(409.22, abs=0.001)

    doubled = quote.price_estimate(2000.0, 10.0, _rates(markup_percent=0))
    base = quote.price_estimate(1000.0, 10.0, _rates(markup_percent=0))
    assert doubled["filament_usd"] == pytest.approx(base["filament_usd"] * 2, abs=0.02)
    with pytest.raises(ValueError, match="zero or more"):
        quote.price_estimate(10, 1, _rates(filament_usd_per_kg=-1))
    with pytest.raises(ValueError, match="didn't guess"):
        quote.price_estimate(0, 1, _rates())


def test_size_must_be_measured_before_a_price():
    guessed = E.JobParams(px_per_inch=1.0, scale_source="estimated from the plan")
    assert "estimate" in quote.size_block_reason(guessed)
    unknown = E.JobParams(px_per_inch=None)
    assert "isn't known" in quote.size_block_reason(unknown)
    measured = E.JobParams(px_per_inch=2.0, user_set=["px_per_inch"])
    assert quote.size_block_reason(measured) is None
    from_file = E.JobParams(px_per_inch=1.0, scale_source="the DXF file's own units")
    assert quote.size_block_reason(from_file) is None


def _box_job(tmp_path, params):
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
    return job


def _pdf_text(data: bytes) -> str:
    parts = []
    for raw in re.findall(rb"\((?:\\.|[^\\)])*\) Tj", data):
        inner = raw[1:-4].decode("latin-1")
        parts.append(inner.replace("\\(", "(").replace("\\)", ")").replace("\\\\", "\\"))
    return "\n".join(parts)


def test_quote_pdf_uses_mesh_size_and_branding(tmp_path, monkeypatch):
    monkeypatch.setattr(quote, "brand_logo_png", lambda: None)
    params = E.JobParams(plan="plan.png", px_per_inch=2.0, user_set=["px_per_inch"], print_mm_per_unit=1.0)
    job = _box_job(tmp_path, params)
    loaded = quote.load_job_meshes(job)
    assert set(loaded) >= {"floor", "walls"}
    pdf = tmp_path / "quote.pdf"
    built = quote.build_quote(job, params, _rates(hourly_usd=30, markup_percent=25), pdf)
    assert built.volume_in3 == pytest.approx(12 * 10 * 1 + 12 * 1 * 8)
    assert built.extent_in == pytest.approx((12, 10, 9))
    assert built.print_mm == pytest.approx((12, 10, 9))
    text = _pdf_text(pdf.read_bytes())
    assert "AK Local Solutions" in text
    assert "8K by AK" in text
    assert "Customer quote" in text
    assert "Top view" in text
    assert "3/4 view" in text
    assert quote.size_line(built.extent_in) in text
    assert quote.format_usd(built.total_usd) in text
    for line in quote.ASSUMPTION_LINES:
        assert line in text
    assert pdf.read_bytes().count(b"/Subtype /Image") == 2

    refused = tmp_path / "no.pdf"
    params.user_set = []
    params.scale_source = "estimated from the plan"
    with pytest.raises(ValueError, match="estimate"):
        quote.build_quote(job, params, _rates(), refused)
    assert not refused.exists()


def test_logo_is_embedded_when_present(tmp_path, monkeypatch):
    buf = io.BytesIO()
    Image.new("RGBA", (40, 16), (11, 42, 91, 255)).save(buf, "PNG")
    monkeypatch.setattr(quote, "brand_logo_png", lambda: buf.getvalue())
    params = E.JobParams(px_per_inch=2.0, user_set=["px_per_inch"], print_mm_per_unit=1.0)
    job = _box_job(tmp_path, params)
    pdf = tmp_path / "with-logo.pdf"
    quote.build_quote(job, params, _rates(), pdf)
    raw = pdf.read_bytes()
    assert raw.count(b"/Subtype /Image") == 3
    assert b"(AK Local Solutions) Tj" in raw
    assert b"(8K by AK) Tj" in raw


def test_logo_helper_uses_the_svg_when_cairo_can_draw_it():
    svg = quote.LOGO_DIR / "8K-by-AK-logo.svg"
    if not svg.is_file() or quote.cairosvg is None:
        pytest.skip("logo svg or cairo not available")
    data = quote.brand_logo_png(width_px=80)
    assert data is not None and data.startswith(b"\x89PNG")
    assert Image.open(io.BytesIO(data)).size[0] == 80


def test_rates_roundtrip_and_ignore_garbage(tmp_path):
    quote.write_rates(tmp_path, _rates())
    assert quote.read_rates(tmp_path) == _rates()
    (tmp_path / "quote_rates.json").write_text("{")
    assert quote.read_rates(tmp_path) is None


def test_rebuild_keeps_logo_and_colour_exports(tmp_path):
    """A quote reads the built model and leaves the underside logo and colour files in place."""
    job = tmp_path / "job"
    job.mkdir()
    Image.fromarray(np.full((120, 200, 3), 255, np.uint8)).save(job / "source_crop.png")
    walls = [E.add_rect({"wall": []}, "wall", *r)["wall"][0] for r in
             [(0, 0, 200, 8), (0, 112, 200, 120), (0, 0, 8, 120), (192, 0, 200, 120)]]
    E.save_polys(job, {"wall": walls, "door": [], "window": []})
    params = E.JobParams(plan="x.png", px_per_inch=2.0, user_set=["px_per_inch"])
    params.save(job)
    E.rebuild(job, params, log=lambda _s: None)
    before = {p.name for p in job.iterdir() if p.is_file()}
    assert "model.3mf" in before and "model.glb" in before
    assert "preview_underside.png" in before
    logo = json.loads((job / "report.json").read_text())["stages"]["editor_rebuild"]["logo"]
    pdf = tmp_path / "customer.pdf"
    built = quote.build_quote(job, params, _rates(), pdf)
    after = {p.name for p in job.iterdir() if p.is_file()}
    assert before <= after
    assert built.total_usd > 0
    again = json.loads((job / "report.json").read_text())["stages"]["editor_rebuild"]["logo"]
    assert again == logo
    assert "AK Local Solutions" in _pdf_text(pdf.read_bytes())
