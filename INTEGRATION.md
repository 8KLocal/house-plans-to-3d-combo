# Integration report — 2026-11-09

Environment: Linux box, 8 CPU, ~15 GB RAM, **no GPU, no Blender**, Python 3.11.18
(uv venv), torch 2.15.0+cpu, numpy 2.4.6, opencv-python-headless 5.0. Full
versions: `requirements.lock.txt`. Everything below was actually run; nothing is mocked.

## What was set up

| Stage | Project (submodule commit) | How it is used | Weights |
|---|---|---|---|
| 1a walls/doors/windows | floorplan-to-3d @ccc1972 | `pip install -e --no-deps` → `buildingcv` model builder, checkpoint loader, `mask_to_polygons` | HF `Yytsi/floorplan-to-3d-walls` 98 MB |
| 1b rooms + icons | CubiCasa5k @c344402 | `floortrans` imported from the submodule via a sys.path shim; architecture built directly, trained checkpoint loaded on CPU | Google Drive `model_best_val_loss_var.pkl` 209 MB |
| 1c classic walls | FloorplanToBlender3d @2da4c95 | `FloorplanToBlenderLib.detect.wall_filter / precise_boxes` (pure OpenCV) as weight-free fallback + cross-check | none |
| 2 printed dimensions | Floorplan-Dimractor @58090b4 | `src.pdf_processor.PDFProcessor` in-process (shim purges its generic `src` package afterwards) | none |
| 3 3D structure | own code (trimesh, shapely) | extrude wall polygons 96 in, windows as sill+lintel, floor slab; door openings left open | — |
| 4 repair/export | pymeshfix @e8b67cb (runtime: PyPI 0.18.1 wheel) | `clean_from_arrays` per body → STL, 3MF, GLB | — |

Wiring added on top of the upstream code (all in `hp3d/`, upstream untouched):
- floorplan-to-3d only accepts CubiCasa **SVGs**. `segment_unet.py` feeds a raster
  instead, reproducing its letterbox-to-512 + ImageNet normalisation.
- Dimractor only lists dimension strings. `dimensions.py` groups the two values in
  a room label ("12'6" X 12'0"") into a pair, finds the segmented room under the
  label, and estimates **pixels per inch**. It rejects rooms that merge with others
  or whose long and short sides disagree, then takes the median.
- `pipeline.py` isolates each stage. If one fails, the error goes into the report
  and the rest still run.

## Smoke-test results

| Test | Result |
|---|---|
| `tests/test_smoke.py` (our combined tests) | **7/7 passed** (~11 s CPU) |
| FloorplanToBlender3d `Testing/` (run from `Testing/`) | 40 passed, 2 failed: both `cv2.imshow` GUI calls. Headless OpenCV has no GUI, so this is expected. Run from the repo root, 2 config tests also fail because their paths depend on cwd. |
| pymeshfix upstream `tests/` vs PyPI wheel | 13/13 passed (needed `pyvista`, installed as a test-only dependency) |
| CubiCasa5k `get_model()` incl. `model_1427.pth` init | OK when cwd = repo; inference with trained weights OK |
| Dimractor upstream `tests/` | 2 passed; CLI processed all 7 bundled PDFs |

### End-to-end: `Floorplan_20250928_001404.pdf` (Dimractor sample, 1-storey house)
- All 6 stages `ok`, ~10 s total on CPU (UNet ~7 s, CubiCasa ~1 s single pass).
- UNet: 11 wall, 36 door, 8 window polygons. Walls look clean. **Most windows
  come out as "door"**, and the caption box is also tagged "door" (domain gap: it was
  trained on CubiCasa renders, not real CAD PDFs).
- Classic OpenCV walls vs UNet walls: IoU 0.42. That is usable as a sanity check, not a
  replacement.
- CubiCasa rooms: Garage and Kitchen right, walls very clean, but **11 regions
  "Undefined"** and no bedrooms detected (same domain gap). Icons: none found.
- Dimractor: 32 dimensions, 16 room-label pairs. Scale estimate **0.927 px/in**
  from 4 agreeing rooms (Patio, Foyer, Office, Entry; spread 2.8%). A 1/8"=1'
  sheet at 100 dpi would be 1.04 px/in. Ours reads ~11% low because the free
  area inside the walls is smaller than the printed (usually centre-line) size.
  **Expect about ±10% scale error. Confirm one known wall before printing.**
- 3D: 71.7 × 56.5 × 8.3 ft shell. All three parts watertight before and after
  pymeshfix. With clean extrusions MeshFix only checks the mesh and does not
  need to repair it. (It prints "could not fix everything" for some tiny bodies;
  the stats in report.json show they are still watertight.) In
  `test_pymeshfix_closes_a_hole`, it does close a deliberately broken mesh.

### End-to-end: `FloorplanToBlender3d/Images/Examples/example.png` (raster)
- Segmentation stages ok. CubiCasa recognised Kitchen, Living Room, Bed Room, Bath, Entry, 7 windows.
- Dimension stage reports an error, as designed: a raster has no text layer, and OCR is not wired
  (Dimractor ships `pytesseract` in requirements but does not use it).
- Scale unknown → model is in pixel units and the report says so.

## Issues found

1. **Licensing is the biggest blocker, not the code.** CubiCasa5k is CC BY-NC 4.0
   (the brief assumed MIT). Floorplan-Dimractor has no license at all.
   FloorplanToBlender3d, pymeshfix and PyMuPDF are copyleft (GPL/AGPL). The
   floorplan-to-3d weights are tagged MIT but were trained on the non-commercial
   CubiCasa5K data.
2. **Domain gap.** Both neural models were trained on CubiCasa5K (Finnish
   real-estate drawings). On a US CAD-style PDF the walls are good, but windows,
   room types and icons are weak. Fine-tuning on your own labelled plans would be
   needed for "right the first time".
3. **Input-format mismatch.** floorplan-to-3d is SVG-only upstream (we bypass with raster input).
   Dimractor is vector-PDF-only (scanned plans and photos need OCR, e.g. Tesseract or a vision LLM).
4. **FloorplanToBlender3d's 3D path needs Blender** (`/usr/local/blender/blender`
   on Linux) and writes .blend files. Blender is not installed here, so only its
   OpenCV detection library was used. trimesh replaces the Blender step and needs no GUI.
5. **Dependency pins are ancient upstream** (CubiCasa: torch 1.0, numpy 1.15;
   FloorplanToBlender3d: opencv 4.2, numpy 1.22). We ignored the pins. Everything
   imported and ran on modern versions, with only a SyntaxWarning in CubiCasa
   (`is 4`). `torch.load` needs `weights_only=False` for the CubiCasa pickle, which
   is a trust risk: only load the official file.
6. Dimractor gives one bbox per text span, so the two numbers in a room label share
   a bbox. We use that on purpose for pairing. Its `src` package name collides
   with anything else called `src` (handled by the shim).
7. CubiCasa `get_model()` loads `floortrans/models/model_1427.pth` by a
   cwd-relative path (we avoid this by building the model directly).
8. Disk/RAM: venv 1.6 GB (2.4 GB with pyvista + vtk, which only the upstream pymeshfix tests need), weights 0.3 GB, peak RAM
   well under 2 GB on CPU. CPU only is fine: ~10 s per plan.

## Verdict

**Technically workable as a private prototype foundation: yes.** All five projects
install into one Python 3.11 environment on CPU with no GPU and no Blender. The
wired pipeline goes from a PDF to an estimated-scale, watertight STL/3MF/GLB in
about 10 seconds. Accuracy is good enough for walls. It is not yet "right the first
time" for windows, room types and fixtures. It needs a human/AI review step and
fine-tuning on real plans.

**As a foundation for an app you sell: not as-is.** CubiCasa5k (non-commercial)
and Dimractor (unlicensed) must be replaced or licensed. GPL/AGPL parts are OK
only if you accept releasing the app's source, or keep it as an internal service
you never distribute. (Note: AGPL also covers network use.)

Commercial-safe swap list for v2:
- CubiCasa5k → train our own room classifier on our own labelled plans, or license CubiCasa.
- floorplan-to-3d weights → retrain the MIT code on data we own.
- Floorplan-Dimractor → our own ~100-line regex parser on `pdfplumber` (MIT) words plus OCR for scans.
- FloorplanToBlender3d → not needed (trimesh does the 3D). Its OpenCV filter is easy to rewrite.
- pymeshfix → `manifold3d` (Apache-2.0, already installed) for boolean union and watertight output.
- PyMuPDF → `pypdfium2` (Apache/BSD) for rasterising.
