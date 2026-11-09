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
