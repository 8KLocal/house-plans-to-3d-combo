"""End-to-end CLI: plan (PDF/image) -> segmentation -> dimensions -> 3D -> repaired export.

    python -m hp3d.pipeline PLAN.pdf --out out/job1
    python -m hp3d.pipeline plan.png --out out/job2 --px-per-inch 0.9

Every stage is reported in out/<job>/report.json with what was printed vs
assumed. Nothing is invented silently: a missing scale or an unavailable
stage is recorded as such.
"""
from __future__ import annotations

import argparse
import json
import time
import traceback
from pathlib import Path

import cv2
import numpy as np

from . import WEIGHTS
from . import furniture
from .raster import load_plan

COLORS = {0: (240, 240, 235), 1: (40, 40, 45), 2: (230, 120, 50), 3: (60, 150, 220)}  # UNet mask: floor/wall/door/window


def _save_mask(path: Path, mask: np.ndarray, colors: dict | None = None) -> None:
    if colors:
        rgb = np.zeros((*mask.shape, 3), np.uint8)
        for k, c in colors.items():
            rgb[mask == k] = c
    else:
        rgb = cv2.applyColorMap((mask.astype(np.float32) * (255 / max(1, mask.max()))).astype(np.uint8), cv2.COLORMAP_JET)[..., ::-1]
    cv2.imwrite(str(path), rgb[..., ::-1])


def run(plan_path: Path, out: Path, px_per_inch: float | None = None, dpi: float = 100.0,
        wall_height_in: float = 96.0, skip: set[str] = frozenset(), mm_per_unit: float = 1.0,
        fixtures: bool = True) -> dict:
    from .raster import file_kind
    if file_kind(plan_path) == "mesh":
        raise ValueError(f"{Path(plan_path).name} is a 3D model, not a plan drawing: use python -m hp3d.ingest "
                         "(or the app), which slices it instead of running plan detection")
    out.mkdir(parents=True, exist_ok=True)
    rep: dict = {"input": str(plan_path), "stages": {}, "assumptions": []}

    def stage(name):
        def deco(fn):
            t = time.time()
            if name in skip:
                rep["stages"][name] = {"status": "skipped"}
                return None
            try:
                res = fn()
                rep["stages"][name] = {"status": "ok", "seconds": round(time.time() - t, 2), **(res or {})}
            except Exception as e:  # keep going: one stage failing must not hide the others
                rep["stages"][name] = {"status": "error", "error": f"{type(e).__name__}: {e}",
                                       "trace": traceback.format_exc(limit=3)}
            return None
        return deco

    plan = load_plan(plan_path, dpi=dpi)
    cv2.imwrite(str(out / "input.png"), plan.rgb[..., ::-1])
    rep["raster"] = {"shape": list(plan.rgb.shape), "is_pdf": plan.is_pdf, "dpi": plan.dpi}
    ctx: dict = {}

    @stage("segment_unet[floorplan-to-3d]")
    def _():
        from . import segment_unet
        seg = segment_unet.UNetSegmenter()
        m = seg.predict(plan.rgb)
        ctx["mask"] = m
        ctx["polys"] = segment_unet.polygons(m)
        _save_mask(out / "unet_mask.png", m, COLORS)
        (out / "polygons.json").write_text(json.dumps(ctx["polys"]))
        return {"epoch": seg.epoch, "pixel_frac": {n: round(float((m == i).mean()), 4) for i, n in enumerate(["floor", "wall", "door", "window"])},
                "polygon_counts": {k: len(v) for k, v in ctx["polys"].items()}}

    @stage("segment_classic[FloorplanToBlender3d]")
    def _():
        from . import segment_classic
        w = segment_classic.wall_mask(plan.rgb)
        ctx["classic_walls"] = w
        _save_mask(out / "classic_walls.png", w, {0: (255, 255, 255), 1: (0, 0, 0)})
        res = {"wall_pixel_frac": round(float(w.mean()), 4), "wall_boxes": len(segment_classic.wall_boxes(plan.rgb))}
        if "mask" in ctx:
            a, b = ctx["mask"] == 1, w > 0
            res["iou_vs_unet_walls"] = round(float((a & b).sum() / max(1, (a | b).sum())), 3)
        return res

    @stage("segment_rooms[CubiCasa5k]")
    def _():
        from . import segment_cubicasa
        if not (WEIGHTS / "cubicasa5k" / "model_best_val_loss_var.pkl").exists():
            raise FileNotFoundError("CubiCasa weights missing; see README 'weights'")
        rooms, icons = segment_cubicasa.CubiCasaSegmenter().predict(plan.rgb)
        _save_mask(out / "cubicasa_rooms.png", rooms)
        _save_mask(out / "cubicasa_icons.png", icons)
        inv = segment_cubicasa.inventory(rooms, icons)
        ctx["inventory"] = inv
        res = {"inventory": inv}
        ctx["icons"] = icons
        ctx["rooms"] = rooms
        return res

    @stage("furniture[symbols]")
    def _():
        if not fixtures:
            return {"placed": 0, "uncertain": [], "detail": "Furniture symbols were skipped."}
        if "polys" not in ctx:
            ctx["polys"] = {}
        icons = ctx.get("icons")
        if icons is None:
            icons = np.zeros(plan.rgb.shape[:2], np.uint8)
        found = furniture.from_icon_mask(icons, ctx.get("rooms"), plan.rgb)
        placed, uncertain = [], []
        for piece in found:
            if piece.get("furn_type"):
                placed.append(piece)
            else:
                uncertain.append({"label": piece.get("label") or "fixture",
                                  "detail": "This mark is on the plan, but it is not a clear toilet, sink, tub, "
                                            "stove, bed, table, or other library piece, so it was not placed."})
        kept = [p for p in ctx["polys"].get("furniture", []) if p.get("added_in_app") or p.get("from_mesh")]
        ctx["polys"]["furniture"] = kept + placed
        (out / "polygons.json").write_text(json.dumps(ctx["polys"]))
        ctx["furniture_uncertain"] = uncertain
        if uncertain:
            rep["assumptions"].append(
                f"{len(uncertain)} fixture mark(s) were unclear, so they were not placed.")
        if placed:
            rep["assumptions"].append(
                f"Placed {len(placed)} piece(s) of furniture that are drawn on the plan. Nothing else was added.")
        return {"placed": len(placed), "uncertain": [u["label"] for u in uncertain]}

    @stage("dimensions[Floorplan-Dimractor]")
    def _():
        from . import dimensions
        if not plan.is_pdf:
            raise ValueError("Dimractor reads vector PDF text only; a picture or scan is read by dimensions_ocr[local]")
        d = dimensions.extract(plan.path)
        page = d["pages"][plan.page]
        (out / "dimensions.json").write_text(json.dumps(page, indent=1))
        pairs = dimensions.room_pairs(page)
        res = {"dimensions_found": len(page["dimensions"]), "codes": page["codes"], "room_label_pairs": len(pairs)}
        # Barrier = everything the UNet calls structure (walls+doors+windows),
        # so rooms separate at openings; fall back to classic walls.
        walls = ctx.get("mask")
        walls = (walls != 0) if walls is not None else ctx.get("classic_walls")
        if walls is not None and pairs:
            sc = dimensions.estimate_scale(pairs, walls.astype(np.uint8), plan.pt_to_px)
            ctx["auto_ppi"] = sc["px_per_inch"]
            res["scale_estimate"] = sc
        return res

    @stage("structure[openings]")
    def _():
        from .structure import apply
        res = apply(plan.rgb, ctx.get("polys"))
        ctx["polys"] = res["polys"]
        ctx["detected"] = res["detected"]
        ctx["snap"] = res["snap"]
        (out / "polygons.json").write_text(json.dumps(ctx["polys"]))
        if res["summary"].get("wall_note"):
            rep["assumptions"].append(res["summary"]["wall_note"])
        return res["summary"]

    @stage("dimensions_ocr[local]")
    def _():
        from .ocr_dims import MEASURED_SOURCE, scale_from_image
        result = scale_from_image(plan.rgb)
        if result.get("measured"):
            new = float(result["px_per_inch"])
            prev = ctx.get("auto_ppi")
            if prev and abs(prev - new) / new > 0.10:
                # The PDF text layer already produced a scale. A disagreeing OCR
                # reading must not throw that away or replace it with a guess.
                result = dict(result)
                result["measured"] = False
                result["scale_status"] = "uncertain"
                result["detail"] = ("Dimension labels and the PDF text layer disagree, so the size was not "
                                    "marked as measured.")
            else:
                ctx["auto_ppi"] = prev or new
                ctx["scale_source"] = MEASURED_SOURCE
                result = dict(result)
                result["scale_status"] = "measured"
        else:
            result = dict(result)
            result["scale_status"] = result.get("status")
        ctx["ocr_scale"] = result
        keep = ("scale_status", "px_per_inch", "engine", "detail", "measured", "relative_spread", "source")
        return {k: result.get(k) for k in keep}

    ppi = px_per_inch or ctx.get("auto_ppi")
    ocr_scale = ctx.get("ocr_scale") or {}
    if px_per_inch:
        rep["scale"] = {"px_per_inch": px_per_inch, "source": "user-supplied", "measured": False}
    elif ocr_scale.get("measured") and ppi:
        rep["scale"] = {"px_per_inch": ppi, "source": ocr_scale.get("source"), "measured": True,
                        "engine": ocr_scale.get("engine")}
        rep["assumptions"].append("Scale read from agreeing dimension labels on the plan.")
    elif ppi:
        rep["scale"] = {"px_per_inch": ppi, "source": "estimated from printed room dimensions", "measured": False}
        rep["assumptions"].append("Scale estimated from room labels; verify one known wall before printing.")
    else:
        ppi = 1.0
        rep["scale"] = {"px_per_inch": None, "source": "UNKNOWN - 1 px = 1 inch placeholder"}
        rep["assumptions"].append("No scale: model units are pixels. Supply --px-per-inch.")
    rep["assumptions"].append(f"Wall height {wall_height_in} in is a default, not read from the plan.")

    @stage("build3d[trimesh]")
    def _():
        from . import build3d
        if "polys" not in ctx:
            raise RuntimeError("no polygons (UNet stage failed)")
        parts = build3d.build(ctx["polys"], 1.0 / ppi, wall_h=wall_height_in, frame_trim=build3d.FRAME_TRIM)
        ctx["parts"] = parts
        import trimesh
        allm = trimesh.util.concatenate(list(parts.values()))
        ext = (allm.bounds[1] - allm.bounds[0]).round(1).tolist()
        return {"parts": list(parts), "extent_in": ext, "extent_ft": [round(v / 12, 1) for v in ext]}

    @stage("repair_export[pymeshfix]")
    def _():
        import trimesh
        from . import repair
        if "parts" not in ctx:
            raise RuntimeError("nothing to repair (build3d failed)")
        before, after, fixed = {}, {}, {}
        for k, m in ctx["parts"].items():
            before[k] = repair.stats(m)
            fixed[k] = repair.repair(m)
        from . import brand
        logo = brand.apply_underside_logo(fixed, mm_per_unit=mm_per_unit)  # maker's mark on every build
        if logo["status"] != "ok":
            rep["assumptions"].append(f"Underside logo not added: {logo.get('reason')}")
        for k, m in fixed.items():
            after[k] = repair.stats(m)
            m.export(out / f"part_{k}.stl")
        from . import colors as C
        C.export_glb(fixed, out / "model.glb")
        from .build3d import merged_for_stl
        merged = merged_for_stl(fixed)   # STL: one colour, door frames fused back into the walls
        merged.export(out / "model.stl")
        files = ["model.stl", "model.glb"] + [f"part_{k}.stl" for k in fixed]
        try:
            C.export_3mf(fixed, out / "model.3mf", name=out.name)
            files.append("model.3mf")
        except Exception as e:
            rep["assumptions"].append(f"3MF export unavailable: {e}")
        try:
            from .preview import render
            render(fixed, out / "preview.png")
            files.append("preview.png")
        except Exception as e:  # noqa: BLE001
            rep["assumptions"].append(f"3D preview failed: {e}")
        try:
            from .preview import render_underside
            render_underside(out / "model.stl", out / "preview_underside.png")
            files.append("preview_underside.png")
        except Exception as e:  # noqa: BLE001
            rep["assumptions"].append(f"underside preview failed: {e}")
        return {"before": before, "after": after, "files": files, "logo": logo,
                "colors": {k: C.hex_of(C.color_of(k)) for k in C.ordered(fixed)}}

    from .confidence import build_report
    rep["confidence"] = build_report(ctx.get("polys"), ctx.get("detected"), ctx.get("snap"), ctx.get("ocr_scale"),
                                     rep.get("scale"), wall_height_in, 36.0, 80.0)
    (out / "report.json").write_text(json.dumps(rep, indent=1, default=str))
    return rep


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("plan", type=Path)
    ap.add_argument("--out", type=Path, default=Path("out/job"))
    ap.add_argument("--px-per-inch", type=float, default=None)
    ap.add_argument("--dpi", type=float, default=100.0, help="PDF rasterisation DPI")
    ap.add_argument("--wall-height-in", type=float, default=96.0)
    ap.add_argument("--print-mm-per-inch", type=float, default=1.0,
                    help="printed mm per model inch, used to size the underside logo (default 1.0 = STL read as mm)")
    ap.add_argument("--skip", nargs="*", default=[], help="stage names to skip, e.g. segment_rooms[CubiCasa5k]")
    a = ap.parse_args()
    rep = run(a.plan, a.out, a.px_per_inch, a.dpi, a.wall_height_in, set(a.skip), a.print_mm_per_inch)
    for k, v in rep["stages"].items():
        print(f"{v['status']:>7}  {k}" + (f"  -> {v.get('error')}" if v["status"] == "error" else ""))
    print("scale:", rep["scale"])
    print("report:", a.out / "report.json")


if __name__ == "__main__":
    main()
