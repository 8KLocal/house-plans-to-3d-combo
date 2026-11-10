"""Customer quote PDF for a built model.

The price uses three numbers the operator types (filament $ per kg, hourly
rate, markup percent) and the solid volume of the meshes already built.
Nothing else is filled in. The building size is the mesh bounds, and only
when that size was measured or read from the file. An estimated scale does
not get a dollar total.

Filament and print time are estimates, and the PDF says so:

- The mesh is already the solid material (rooms are hollow). Filament is that
  volume printed solid, in PLA at ``PLA_DENSITY_G_CM3``.
- The model is in inches. At ``print_mm_per_unit`` (the project's print
  setting) each model inch is that many millimetres in the slicer.
- Print time divides the printed plastic volume by
  ``ESTIMATED_FLOW_MM3_PER_S``. That is not a sliced toolpath.

Views are the same coloured renderer as the on-screen preview (top, and a
3/4 view). Branding is AK Local Solutions / 8K by AK. The SVG logo is used
when Cairo can draw it; the words are always on the page.
"""
from __future__ import annotations

import io
import json
import math
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import trimesh
from PIL import Image

from . import ROOT, colors, preview

try:
    import cairosvg
except Exception:  # text branding still works when Cairo cannot draw the logo
    cairosvg = None

PLA_DENSITY_G_CM3 = 1.24
ESTIMATED_FLOW_MM3_PER_S = 8.0
ASSUMPTION_LINES = (
    "Filament is the solid volume of the mesh, printed solid, in PLA at 1.24 g/cm3.",
    "Print time uses 8 mm3 of plastic per second. It is not a sliced toolpath.",
    "Your filament price, hourly rate, and markup are the only prices used.",
)
LOGO_DIR = ROOT / "assets" / "logo"
LOGO_FILES = ("8K-by-AK-logo.svg", "8K-only.svg")
PAGE_W, PAGE_H = 612.0, 792.0
MARGIN = 48.0
QUOTE_SIZE = (520, 390)


@dataclass(frozen=True)
class Rates:
    filament_usd_per_kg: float
    hourly_usd: float
    markup_percent: float


@dataclass(frozen=True)
class Quote:
    project: str
    extent_in: tuple[float, float, float]
    volume_in3: float
    print_mm: tuple[float, float, float]
    mm_per_unit: float
    plastic_g: float
    hours: float
    filament_usd: float
    time_usd: float
    subtotal_usd: float
    markup_usd: float
    total_usd: float
    size_note: str
    pdf: Path


def format_usd(amount: float) -> str:
    return f"${amount:,.2f}"


def feet_text(inches: float) -> str:
    ft, inch = divmod(round(float(inches), 1), 12)
    if inch < 0.05:
        return f"{int(ft)} ft"
    return f"{int(ft)} ft {inch:g} in" if ft else f"{inch:g} in"


def size_line(extent_in: tuple[float, float, float]) -> str:
    return (
        f"Model size: {feet_text(extent_in[0])} wide, {feet_text(extent_in[1])} deep, "
        f"{feet_text(extent_in[2])} tall ({extent_in[0]:.1f} x {extent_in[1]:.1f} x {extent_in[2]:.1f} in)"
    )


def size_block_reason(params) -> str | None:
    """Why a customer price must be withheld, or None when the size is real."""
    ppi = getattr(params, "px_per_inch", None)
    if not ppi or not math.isfinite(float(ppi)) or float(ppi) <= 0:
        return (
            "Set one real measurement from the plan before making a customer quote. "
            "Without it, the model size isn't known."
        )
    if "px_per_inch" in (getattr(params, "user_set", None) or []):
        return None
    src = (getattr(params, "scale_source", None) or "").lower()
    if "dxf" in src and "unit" in src:
        return None
    return "This size is an estimate. Set one real length from the plan before making a customer quote."


def size_confidence(params) -> str:
    if "px_per_inch" in (getattr(params, "user_set", None) or []):
        return "Size set from a measurement you gave."
    return f"Size read from the file ({params.scale_source})."


def _check_rates(rates: Rates) -> None:
    pairs = (
        ("filament price per kg", rates.filament_usd_per_kg),
        ("hourly rate", rates.hourly_usd),
        ("markup percent", rates.markup_percent),
    )
    for name, value in pairs:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"The {name} has to be zero or more.")
        if not math.isfinite(float(value)) or float(value) < 0:
            raise ValueError(f"The {name} has to be zero or more.")


def _cents(amount: float) -> float:
    return round(float(amount) + 1e-9, 2)


def price_estimate(volume_in3: float, mm_per_unit: float, rates: Rates) -> dict:
    """Filament, time, and USD from a measured solid volume and typed rates."""
    _check_rates(rates)
    if not math.isfinite(volume_in3) or volume_in3 <= 1e-9:
        raise ValueError("I couldn't measure the model's solid volume, so I didn't guess a filament amount.")
    if not math.isfinite(mm_per_unit) or mm_per_unit <= 0:
        raise ValueError("The print scale on this project isn't a real number, so I didn't guess a filament amount.")
    plastic_mm3 = volume_in3 * (mm_per_unit ** 3)
    grams = plastic_mm3 / 1000.0 * PLA_DENSITY_G_CM3
    hours = plastic_mm3 / ESTIMATED_FLOW_MM3_PER_S / 3600.0
    filament_usd = _cents(grams / 1000.0 * rates.filament_usd_per_kg)
    time_usd = _cents(hours * rates.hourly_usd)
    subtotal = _cents(filament_usd + time_usd)
    markup_usd = _cents(subtotal * rates.markup_percent / 100.0)
    total = _cents(subtotal + markup_usd)
    return {
        "plastic_g": grams,
        "hours": hours,
        "filament_usd": filament_usd,
        "time_usd": time_usd,
        "subtotal_usd": subtotal,
        "markup_usd": markup_usd,
        "total_usd": total,
    }


def write_rates(job: Path, rates: Rates) -> None:
    _check_rates(rates)
    payload = {
        "filament_usd_per_kg": rates.filament_usd_per_kg,
        "hourly_usd": rates.hourly_usd,
        "markup_percent": rates.markup_percent,
    }
    (Path(job) / "quote_rates.json").write_text(json.dumps(payload, indent=1) + "\n")


def read_rates(job: Path) -> Rates | None:
    path = Path(job) / "quote_rates.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
        rates = Rates(
            filament_usd_per_kg=float(data["filament_usd_per_kg"]),
            hourly_usd=float(data["hourly_usd"]),
            markup_percent=float(data["markup_percent"]),
        )
        _check_rates(rates)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return rates


def load_job_meshes(job: Path) -> dict:
    job = Path(job)
    glb_error: Exception | None = None
    glb = job / "model.glb"
    if glb.is_file():
        try:
            parts = {k: m for k, m in preview._load_parts(glb).items() if len(getattr(m, "faces", []))}
        except Exception as exc:
            glb_error = exc
            parts = {}
        if parts:
            return parts
    out = {}
    for path in sorted(job.glob("part_*.stl")):
        key = path.stem[len("part_"):]
        try:
            mesh = trimesh.load(path, force="mesh")
        except Exception as exc:
            raise ValueError(f"I couldn't read {path.name} for the quote.") from exc
        if len(mesh.faces):
            out[key] = mesh
    if not out:
        if glb_error is not None:
            raise ValueError("I couldn't read the 3D model for the quote.") from glb_error
        raise ValueError("The 3D model isn't here yet. Build it, then make the quote.")
    return out


def measure_parts(parts: dict) -> tuple[tuple[float, float, float], float, list[str]]:
    """(extent xyz in model inches, solid volume in cubic inches, extra notes)."""
    if not parts:
        raise ValueError("The 3D model isn't here yet. Build it, then make the quote.")
    lows, highs = [], []
    volume = 0.0
    open_parts = []
    for name, mesh in parts.items():
        if mesh is None or len(getattr(mesh, "faces", [])) == 0:
            continue
        vol = float(mesh.volume)
        if not math.isfinite(vol):
            raise ValueError(
                f"I couldn't measure the volume of the {name} part, so I didn't guess a filament amount."
            )
        volume += abs(vol)
        lows.append(np.asarray(mesh.bounds[0], float))
        highs.append(np.asarray(mesh.bounds[1], float))
        try:
            closed = bool(mesh.is_watertight)
        except Exception:
            closed = False
        if not closed:
            open_parts.append(str(name))
    if not lows:
        raise ValueError("The 3D model isn't here yet. Build it, then make the quote.")
    extent = tuple(float(v) for v in (np.maximum.reduce(highs) - np.minimum.reduce(lows)))
    if any(not math.isfinite(v) or v < 0 for v in extent):
        raise ValueError("I couldn't measure the model size from the mesh, so I didn't guess one.")
    if volume <= 1e-9:
        raise ValueError("I couldn't measure the model's solid volume, so I didn't guess a filament amount.")
    notes = []
    if open_parts:
        notes.append("One part wasn't a closed solid, so the volume is only as good as that mesh.")
    return extent, volume, notes


def brand_logo_png(width_px: int = 420) -> bytes | None:
    """Raster of the 8K by AK logo, or None when the file or Cairo is missing."""
    if cairosvg is None:
        return None
    for name in LOGO_FILES:
        svg = LOGO_DIR / name
        if not svg.is_file():
            continue
        try:
            return cairosvg.svg2png(url=str(svg), output_width=width_px)
        except Exception:
            continue
    return None


def build_quote(job: Path, params, rates: Rates, pdf_path: Path) -> Quote:
    """Write the customer PDF. Raises ValueError when a price would be a guess."""
    job = Path(job)
    pdf_path = Path(pdf_path)
    reason = size_block_reason(params)
    if reason:
        raise ValueError(reason)
    mm_value = getattr(params, "print_mm_per_unit", None)
    if mm_value is None:
        raise ValueError("The print scale on this project isn't set, so I didn't guess a filament amount.")
    mm = float(mm_value)
    parts = load_job_meshes(job)
    extent, volume, extra = measure_parts(parts)
    money = price_estimate(volume, mm, rates)
    note = size_confidence(params)
    pal = colors.palette((getattr(params, "mesh", None) or {}).get("source_colors"))
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="hp3d-quote-") as tmp:
        images = _view_jpegs(parts, pal, Path(tmp))
        _write_pdf(
            pdf_path,
            project=job.name,
            images=images,
            logo=_logo_jpeg(),
            extent=extent,
            volume=volume,
            mm=mm,
            rates=rates,
            money=money,
            size_note=note,
            extra=extra,
        )
    print_mm = tuple(float(v) * mm for v in extent)
    return Quote(
        project=job.name,
        extent_in=extent,
        volume_in3=volume,
        print_mm=print_mm,
        mm_per_unit=mm,
        plastic_g=money["plastic_g"],
        hours=money["hours"],
        filament_usd=money["filament_usd"],
        time_usd=money["time_usd"],
        subtotal_usd=money["subtotal_usd"],
        markup_usd=money["markup_usd"],
        total_usd=money["total_usd"],
        size_note=note,
        pdf=pdf_path,
    )


def _qty(value: float) -> str:
    if value >= 100:
        return f"{value:,.0f}"
    if value >= 10:
        return f"{value:,.1f}"
    return f"{value:,.2f}"


def _hours(hours: float) -> str:
    if hours >= 1:
        return f"{hours:.2f} h"
    minutes = hours * 60.0
    if minutes >= 1:
        return f"{minutes:.1f} min"
    return f"{hours * 3600.0:.0f} s"


def _view_jpegs(parts: dict, pal: dict, tmp: Path) -> dict:
    out = {}
    for key, view in (("top", preview.TOP_VIEW), ("three_quarter", preview.THREE_QUARTER_VIEW)):
        png = tmp / f"{key}.png"
        preview.render(
            parts, png, pal, size=QUOTE_SIZE,
            elev=view["elev"], azim=view["azim"], zoom=view["zoom"], legend=True, ss=1,
        )
        image = Image.open(png).convert("RGB")
        buf = io.BytesIO()
        image.save(buf, "JPEG", quality=82)
        out[key] = {"jpeg": buf.getvalue(), "w": image.width, "h": image.height}
    return out


def _logo_jpeg():
    data = brand_logo_png()
    if not data:
        return None
    try:
        image = Image.open(io.BytesIO(data)).convert("RGBA")
    except Exception:
        return None
    plate = Image.new("RGB", image.size, (255, 255, 255))
    plate.paste(image, mask=image.getchannel("A"))
    plate.thumbnail((420, 120), Image.LANCZOS)
    buf = io.BytesIO()
    plate.save(buf, "JPEG", quality=85)
    return buf.getvalue(), plate.size


def _write_pdf(path, project, images, logo, extent, volume, mm, rates, money, size_note, extra) -> None:
    page = _Page()
    y = PAGE_H - MARGIN
    text_x = MARGIN
    if logo is not None:
        jpeg, (pw, ph) = logo
        max_w, max_h = 150.0, 46.0
        scale = min(max_w / pw, max_h / ph)
        lw, lh = pw * scale, ph * scale
        page.image(MARGIN, y - lh, lw, lh, jpeg, pw, ph)
        text_x = MARGIN + lw + 14
    page.text(text_x, y - 16, "AK Local Solutions", size=16, bold=True)
    page.text(text_x, y - 32, "8K by AK", size=12, bold=True, color=(0.04, 0.16, 0.36))
    y -= max(50.0, 46.0) + 8
    page.text(MARGIN, y - 16, "Customer quote", size=18, bold=True)
    y -= 28
    page.text(MARGIN, y - 11, f"Project: {_one_line(project)}", size=11)
    page.text(PAGE_W - MARGIN - 110, y - 11, time.strftime("Date: %Y-%m-%d"), size=11)
    y -= 22
    page.fill_rect(MARGIN, y - 0.6, PAGE_W - 2 * MARGIN, 0.8, (0.75, 0.75, 0.78))
    y -= 14

    gap = 14.0
    img_w = (PAGE_W - 2 * MARGIN - gap) / 2
    aspect = images["top"]["h"] / max(images["top"]["w"], 1)
    img_h = img_w * aspect
    y -= img_h
    page.image(MARGIN, y, img_w, img_h, images["top"]["jpeg"], images["top"]["w"], images["top"]["h"])
    page.image(MARGIN + img_w + gap, y, img_w, img_h, images["three_quarter"]["jpeg"],
               images["three_quarter"]["w"], images["three_quarter"]["h"])
    y -= 16
    page.text(MARGIN, y, "Top view", size=10, bold=True)
    page.text(MARGIN + img_w + gap, y, "3/4 view", size=10, bold=True)
    y -= 20

    print_mm = tuple(v * mm for v in extent)
    ratio = 25.4 / mm
    lines = [
        size_line(extent),
        size_note,
        (
            f"Print size: {print_mm[0]:.1f} x {print_mm[1]:.1f} x {print_mm[2]:.1f} mm "
            f"(1 model inch = {mm:g} mm in the slicer, about 1:{ratio:.4g} of the building)."
        ),
        f"Solid volume: {_qty(volume)} cubic inches ({_qty(volume * mm ** 3)} mm3 at this print scale).",
        (
            f"Prices you entered: filament ${rates.filament_usd_per_kg:.2f}/kg, "
            f"shop time ${rates.hourly_usd:.2f}/hour, markup {rates.markup_percent:g}%."
        ),
    ]
    for line in lines:
        y -= 14
        page.text(MARGIN, y, line, size=10)
    y -= 8
    for line in list(ASSUMPTION_LINES) + list(extra):
        y -= 12
        page.text(MARGIN, y, line, size=8, color=(0.25, 0.25, 0.28))

    y -= 16
    box_h = 92.0
    box_bottom = y - box_h
    page.fill_rect(MARGIN, box_bottom, PAGE_W - 2 * MARGIN, box_h, (0.96, 0.95, 0.92))
    rows = [
        (f"Filament: {_qty(money['plastic_g'])} g at ${rates.filament_usd_per_kg:.2f}/kg",
         format_usd(money["filament_usd"]), False),
        (f"Print time: {_hours(money['hours'])} at ${rates.hourly_usd:.2f}/hour",
         format_usd(money["time_usd"]), False),
        ("Subtotal", format_usd(money["subtotal_usd"]), False),
        (f"Markup {rates.markup_percent:g}%", format_usd(money["markup_usd"]), False),
        ("Total", format_usd(money["total_usd"]), True),
    ]
    row_y = y - 16
    right = PAGE_W - MARGIN - 12
    for label, amount, bold in rows:
        page.text(MARGIN + 12, row_y, label, size=11 if bold else 10, bold=bold)
        page.text(right - len(amount) * (6.4 if bold else 5.6), row_y, amount, size=12 if bold else 10, bold=bold)
        row_y -= 16 if bold else 14

    page.text(MARGIN, 36, "AK Local Solutions  |  8K by AK  |  House Plan 3D", size=9, color=(0.2, 0.2, 0.24))
    page.text(MARGIN, 24, "Customer quote. Filament and print time are estimates from the model volume.", size=8,
              color=(0.35, 0.35, 0.38))
    _save_pdf(path, page)


def _one_line(text: str) -> str:
    return " ".join(str(text).replace("\n", " ").split())


class _Page:
    def __init__(self):
        self.cmds: list[tuple] = []
        self.images: list[dict] = []

    def text(self, x, y, text, size=11, bold=False, color=(0.1, 0.1, 0.12)):
        self.cmds.append(("t", float(x), float(y), str(text), float(size), bool(bold), color))

    def image(self, x, y, w, h, jpeg: bytes, pw: int, ph: int):
        name = f"Im{len(self.images) + 1}"
        self.images.append({"name": name, "jpeg": jpeg, "pw": int(pw), "ph": int(ph)})
        self.cmds.append(("i", name, float(x), float(y), float(w), float(h)))

    def fill_rect(self, x, y, w, h, color):
        self.cmds.append(("r", float(x), float(y), float(w), float(h), color))

    def stream(self) -> bytes:
        lines = []
        for cmd in self.cmds:
            kind = cmd[0]
            if kind == "r":
                _, x, y, w, h, col = cmd
                lines.append(
                    f"{col[0]:.3f} {col[1]:.3f} {col[2]:.3f} rg {x:.2f} {y:.2f} {w:.2f} {h:.2f} re f"
                )
            elif kind == "t":
                _, x, y, text, size, bold, col = cmd
                font = "F2" if bold else "F1"
                lines.append(
                    f"{col[0]:.3f} {col[1]:.3f} {col[2]:.3f} rg BT /{font} {size:.2f} Tf "
                    f"1 0 0 1 {x:.2f} {y:.2f} Tm ({_pdf_text(text)}) Tj ET"
                )
            else:
                _, name, x, y, w, h = cmd
                lines.append(f"q {w:.2f} 0 0 {h:.2f} {x:.2f} {y:.2f} cm /{name} Do Q")
        return ("\n".join(lines) + "\n").encode("latin-1")


def _pdf_text(text: str) -> str:
    raw = str(text).encode("latin-1", "replace").decode("latin-1")
    return raw.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _save_pdf(path: Path, page: _Page) -> None:
    content = page.stream()
    content_obj = f"<< /Length {len(content)} >>\nstream\n".encode("latin-1") + content + b"endstream"
    font_regular = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    font_bold = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>"
    image_objs = []
    xobj = []
    for image in page.images:
        jpeg = image["jpeg"]
        header = (
            f"<< /Type /XObject /Subtype /Image /Width {image['pw']} /Height {image['ph']} "
            f"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length {len(jpeg)} >>\nstream\n"
        ).encode("latin-1")
        image_objs.append(header + jpeg + b"\nendstream")
        xobj.append(f"/{image['name']} {6 + len(image_objs)} 0 R")
    resources = f"<< /Font << /F1 5 0 R /F2 6 0 R >> /XObject << {' '.join(xobj)} >> >>"
    page_obj = (
        f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_W:.0f} {PAGE_H:.0f}] "
        f"/Contents 4 0 R /Resources {resources} >>"
    ).encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        page_obj,
        content_obj,
        font_regular,
        font_bold,
        *image_objs,
    ]
    chunks = [b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"]
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(sum(len(chunk) for chunk in chunks))
        chunks.append(f"{index} 0 obj\n".encode("latin-1") + obj + b"\nendobj\n")
    xref_at = sum(len(chunk) for chunk in chunks)
    size = len(objects) + 1
    xref = [f"xref\n0 {size}\n".encode("latin-1"), b"0000000000 65535 f \n"]
    for offset in offsets[1:]:
        xref.append(f"{offset:010d} 00000 n \n".encode("latin-1"))
    trailer = f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n".encode("latin-1")
    Path(path).write_bytes(b"".join(chunks + xref + [trailer]))
