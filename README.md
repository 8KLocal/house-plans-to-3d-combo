# house-plans-to-3d-combo (local, unpublished)

A test bed that installs five open-source projects side by side in **one Python
environment** and wires them into a single pipeline, to find out whether they can
serve as the foundation of a "house plans → 3D model" app.

```
plan PDF / image
  └─ 0 raster           hp3d/raster.py            PyMuPDF / Pillow
  └─ 1a walls/doors/win  hp3d/segment_unet.py      Yytsi/floorplan-to-3d   (ResNet34-UNet)
  └─ 1b rooms + icons    hp3d/segment_cubicasa.py  CubiCasa/CubiCasa5k     (hourglass net)
  └─ 1c classic walls    hp3d/segment_classic.py   grebtsew/FloorplanToBlender3d (OpenCV, no Blender)
  └─ 2 printed dims      hp3d/dimensions.py        jasoncobra3/Floorplan-Dimractor → scale estimate
  └─ 3 3D structure      hp3d/build3d.py           trimesh + shapely extrusion
  └─ 4 repair + export   hp3d/repair.py            pyvista/pymeshfix → STL / 3MF / GLB
```

`hp3d/pipeline.py` runs every stage, keeps going if one fails, and writes
`out/<job>/report.json` that separates **printed** values from **assumptions**.

## Layout

```
hp3d/                 integration layer (this repo's own code)
  pipeline.py         CLI: python -m hp3d.pipeline PLAN --out out/job
  preview.py          headless isometric PNG of an STL
tests/test_smoke.py   7 smoke tests across all five projects
third_party/          git submodules, unmodified upstream code
  FloorplanToBlender3d/   @2da4c95  GPL-3.0
  floorplan-to-3d/        @ccc1972  MIT  (weights on HF tagged MIT)
  CubiCasa5k/             @c344402  CC BY-NC 4.0  (code, dataset and weights)
  Floorplan-Dimractor/    @58090b4  NO LICENSE FILE
  pymeshfix/              @e8b67cb  GPL-3.0  (runtime uses PyPI wheel 0.18.1)
weights/              downloaded by setup.sh, git-ignored
requirements.txt      combined deps;  requirements.lock.txt = exact tested versions
INTEGRATION.md        what was tested, what broke, verdict
```

## Setup (Linux/macOS, CPU only)

On Windows, see [WINDOWS.md](WINDOWS.md): `setup-windows.bat` once, then `open-app.bat`.

Needs `git`, [`uv`](https://docs.astral.sh/uv/), and the system Cairo library
(`apt install libcairo2` / `brew install cairo`).

```bash
./setup.sh                     # submodules, .venv (Python 3.11), deps, ~300 MB weights
. .venv/bin/activate
python -m pytest -q tests      # 7 smoke tests, ~15 s on CPU
```

## Minimal combined run

```bash
# Vector PDF: all stages run, scale is estimated from the printed room sizes
python -m hp3d.pipeline third_party/Floorplan-Dimractor/data/input/Floorplan_20250928_001404.pdf --out out/sample_pdf
# Raster image: no text layer, so the dimension stage reports an error and scale is unknown
python -m hp3d.pipeline third_party/FloorplanToBlender3d/Images/Examples/example.png --out out/sample_png --px-per-inch 1.0
python -m hp3d.preview out/sample_pdf/model.stl out/sample_pdf/preview.png
```

Outputs per job: `input.png`, `unet_mask.png`, `classic_walls.png`,
`cubicasa_rooms.png`, `cubicasa_icons.png`, `polygons.json`, `dimensions.json`,
`model.stl`, `model.3mf`, `model.glb`, `part_{walls,windows,floor}.stl`, `report.json`.

### Colours

One table (`hp3d/colors.py`) colours the 2D plan, the in-app 3D preview and the exports:
floor light warm `#e8dac0`, walls dark slate `#686b76`, door frames orange `#e67832`
(the wall within 4 in of each doorway plus any header, split out as its own part by
`build3d.build(..., frame_trim=FRAME_TRIM)`; doors stay open), windows blue `#3c96dc`,
furniture green `#5aa050`.

- `model.3mf`: one object per part, each with a 3MF base-material display colour, grouped
  as one multi-part model so a slicer shows the colours and can assign one filament per part.
- `model.glb`: one named mesh per part with a PBR base colour.
- `model.stl` / `part_*.stl`: geometry only (STL has no colour); in `model.stl` the door
  frames are fused back into the walls so it stays watertight.
- `preview.png`: software z-buffer render (no see-through faces), flat-shaded part colours, legend.
- A 3D source file that is really coloured (2+ colours, face/vertex/material) keeps its main
  colour per part (`job.json` `mesh.source_colors`, `report.json` `colors_from_file`); a
  one-colour or uncoloured file gets the standard colours.

Options: `--px-per-inch` (override scale), `--dpi` (PDF raster DPI, default 100),
`--wall-height-in` (default 96), `--skip "segment_rooms[CubiCasa5k]"`.

## Licenses (checked in each repo on 2026-11-09)

| Project | License file | Implication |
|---|---|---|
| FloorplanToBlender3d | `license`: **GPL-3.0** | Distributing an app that links it makes the whole app GPL-3.0 (source must be offered). Fine for private, in-house use. |
| floorplan-to-3d | `LICENSE`: **MIT** (HF weights card: MIT) | Permissive. But the weights were **trained on CubiCasa5K (CC BY-NC)**, so commercial use of the weights is legally unclear. |
| CubiCasa5k | `LICENSE`: **CC BY-NC 4.0** (not MIT) | **No commercial use** of the code, dataset or weights without a deal with CubiCasa. |
| Floorplan-Dimractor | **none** (GitHub API: no license) | Default copyright, all rights reserved: no legal right to copy, modify or redistribute. Ask the author or rewrite. |
| pymeshfix (+ MeshFix C++) | `LICENSE`: **GPL-3.0** | Same as FloorplanToBlender3d. |
| PyMuPDF (dependency) | **AGPL-3.0** or commercial | Also copyleft; `pdfplumber` (MIT) or `pypdfium2` can replace it. |

**Together:** the combined app is GPL-3.0 at best, **non-commercial** because of
CubiCasa5k, and has an unlicensed component. That is fine for testing on your own
machine. It is **not** cleanly sellable as-is. See INTEGRATION.md for a
commercial-safe swap list.

This repo is local only: no remote is configured and nothing has been pushed.

## Desktop app with in-app model editor (Linux box)

    ./open-app.sh

Tabs: **1 Plan & Run** (plan, job name, px-per-inch, wall height, crop / green-outline prefill, Run),
**2 Adjust** (wall height, floor thickness, sill, lintel, scale -> rebuild), **3 Parts** (select a
wall/door/window from the list or by clicking the 2D view; move, lengthen/shorten, delete, add a
rectangle by numbers or by dragging; Undo; Reset to detected), **4 Say it** (rule-based phrases such as
"make the walls 8 feet", "sills at 36 inches", "move selected 12 inches left"; unknown phrases are
refused). Logic lives in `hp3d/editor.py` (tested in `tests/test_editor.py`); the GUI is `hp3d/app.py`.
Detection only re-runs when the plan or crop changes; other edits rebuild from `out/<job>/polygons.json`.
Edits and flagged assumptions are appended to `out/<job>/report.json`. Private testing build only
(licence limits above).

## Maker's mark (underside logo)

Every build (`hp3d.pipeline` and every in-app rebuild) recesses the **8K by AK**
mark into the underside of the floor slab (`hp3d/brand.py`, assets in
`assets/logo/`). Full lockup (8K / bar / "by AK", ~75 mm) when the base fits it
with 4 mm margin, shrunk to no less than 50 mm; otherwise the 8K-only mark
(~47 mm down to 15 mm); smaller bases are left unmarked and say so in
`report.json`. Mirrored so it reads correctly when the print is turned over,
1.0 mm deep (or 40% of a thinner slab), the bottom stays flat, and the slab stays
watertight. Sizes assume the export is printed as-is (1 model inch = 1 mm in
the slicer); pass `--print-mm-per-inch` (or `print_mm_per_unit` in `job.json`)
if you print at another scale. `preview_underside.png` shows the result.

## Upload anything: picture, PDF, DXF or 3D model (no AI)

Step 1 in the app is now **Pick a file**. One button takes any of:

| Type | Extensions | How it is read |
|---|---|---|
| Picture | png jpg jpeg tif tiff bmp webp gif (or any real picture with an odd name) | existing detection pipeline (walls/doors/windows), one measured length or a quiz answer sets the size; green-unit crop still optional |
| PDF blueprint | pdf (first page) | same pipeline; scale estimated from printed room sizes when the PDF has a text layer |
| DXF drawing | dxf | `hp3d/raster.py` draws it (closed outlines filled, so outlined walls read as solid walls) and runs the same pipeline; the file's `$INSUNITS` gives the scale when present |
| 3D model | stl obj ply off 3mf glb gltf | `hp3d/mesh_import.py` slices the mesh (no detection nets): floor slab, walls, windows (sill + lintel), door frames (opening with a header, header kept), and low parts that are really in the file (furniture/counters/half walls, measured heights). Saved as the same editable job, so plain-language edits, Undo and Rebuild work. |

Anything else (DWG, SKP, HEIC, Word, broken files...) is refused with a plain
message that says which types to export instead; the app does not crash.

**Sizes.** STL has no units. The importer guesses the unit only when the
measured wall height fits a normal ceiling in mm/cm/m/in/ft, otherwise uses a
1 unit = 1 inch placeholder; either way it is flagged. Heights measured in the
file follow the size, so answering one length fixes all of them.

**Quick questions.** After each build a short quiz appears under step 2, one
question at a time (Yes/No or a length with a unit, or *Skip (keep my guess)*):
real width (size), wall height, base thickness, window bottom/top, furniture
height, "doorways kept as open frames?", "just one floor?". Only questions the
file did not answer are asked. Answers go to `job.json` (`quiz`); the job is
marked done in `report.json` (`review.status: "done"`) only when every question
is answered or skipped, and skipped values stay listed as assumptions.

**Doors** are always frames (openings), never solid doors. A door added in the
app (`add door x0,y0 to x1,y1`) now cuts its opening through the wall.
**Furniture** appears only when the 3D file contains it, when added by hand
(`add furniture ...`), or, opt-in under More options, from the CubiCasa icon
net ("often wrong": in tests it marked sofa cushions as appliances). The
underside 8K by AK mark is still added on every build (a base is added when a
3D file has no floor slab).

CLI, same steps as the app:

    python -m hp3d.ingest plans/sample_house_mm.stl --out out/house --answer "size=20 ft" --answer doors=yes --skip-rest

`plans/sample_house_mm.stl` and `plans/sample_plan_inches.dxf` are synthetic
test inputs (`hp3d/samples.py`). Tests: `tests/test_upload.py`.

Limits: one floor per file (upper floors / roofs in a mesh are reported and
ignored); rebuild re-extrudes straight walls, so roofs, stairs and curved or
sloped parts in a mesh are not carried over; openings that run the full wall
height are plain gaps; Z-up assumed except glTF (Y-up); PDFs use page 1 only;
DXF layers, blocks, hatches and text are not interpreted.
