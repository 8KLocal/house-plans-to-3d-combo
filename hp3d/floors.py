"""Stack one plan per floor, with alignment pegs, and an optional removable roof.

Nothing is copied from floor 1 onto another floor. A floor with no polygons is
left out and reported. A roof is built only when the user picked a style and
gave the sizes that style needs (thickness, and rise for a gable).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import trimesh

from .build3d import FRAME_TRIM, build, merged_for_stl

_LEVEL_NAME = re.compile(r"^L(\d+)_")
PEG_DIAMETER_MM = 6.0
PEG_HEIGHT_MM = 4.0
PEG_INSET_MM = 12.0
MIN_SLAB_FOR_PEG_MM = 3.0


def _shift(mesh: trimesh.Trimesh, z: float) -> trimesh.Trimesh:
    out = mesh.copy()
    if z:
        out.apply_translation([0, 0, z])
    return out


def _prefix(parts: dict[str, trimesh.Trimesh], label: str) -> dict[str, trimesh.Trimesh]:
    return {f"{label}_{k}": m for k, m in parts.items()}


def _footprint_corners(mesh: trimesh.Trimesh, inset: float) -> list[tuple[float, float]]:
    lo, hi = mesh.bounds
    x0, x1 = lo[0] + inset, hi[0] - inset
    y0, y1 = lo[1] + inset, hi[1] - inset
    if x1 <= x0 or y1 <= y0:
        return []
    return [(x0, y0), (x1, y0), (x0, y1), (x1, y1)]


def _cylinder(radius: float, height: float, center) -> trimesh.Trimesh:
    peg = trimesh.creation.cylinder(radius=radius, height=height, sections=16)
    peg.apply_translation(center)
    return peg


def _add_pegs(floor: trimesh.Trimesh, xy: list[tuple[float, float]], peg_h: float, peg_r: float) -> trimesh.Trimesh:
    """Pegs stand on the top of the slab and overlap it slightly so they travel with that floor."""
    if not xy:
        return floor
    z_top = float(floor.bounds[1][2])
    pegs = [_cylinder(peg_r, peg_h + peg_r, (x, y, z_top + peg_h / 2)) for x, y in xy]
    return trimesh.util.concatenate([floor, *pegs])


def _cut_sockets(floor: trimesh.Trimesh, xy: list[tuple[float, float]], peg_h: float, peg_r: float) -> tuple[trimesh.Trimesh, bool]:
    """Holes in the underside of the upper slab. False means the slab was left solid."""
    if not xy:
        return floor, True
    z0 = float(floor.bounds[0][2])
    # Punch from just below the slab up through the peg depth.
    cutters = [_cylinder(peg_r * 1.15, peg_h + peg_r * 2, (x, y, z0 + peg_h / 2)) for x, y in xy]
    try:
        cut = trimesh.boolean.difference([floor, *cutters], engine="manifold")
    except Exception:  # noqa: BLE001
        return floor, False
    if cut is None or len(cut.faces) == 0 or not cut.is_watertight or cut.volume >= floor.volume - 1e-6:
        return floor, False
    return cut, True


def _roof_mesh(floor: trimesh.Trimesh, z0: float, style: str, thickness: float, rise: float | None) -> trimesh.Trimesh:
    lo, hi = floor.bounds
    x0, y0, x1, y1 = float(lo[0]), float(lo[1]), float(hi[0]), float(hi[1])
    if style == "flat":
        box = trimesh.creation.box(bounds=[[x0, y0, z0], [x1, y1, z0 + thickness]])
        return box
    # Solid gable: flat underside, eaves at `thickness`, ridge `rise` above the eaves.
    # No overhang, fascia, or trusses.
    z_eave = z0 + thickness
    z_ridge = z_eave + float(rise)
    if (x1 - x0) >= (y1 - y0):
        ridge = [(x0, (y0 + y1) / 2, z_ridge), (x1, (y0 + y1) / 2, z_ridge)]
    else:
        ridge = [((x0 + x1) / 2, y0, z_ridge), ((x0 + x1) / 2, y1, z_ridge)]
    bottom = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0)]
    eaves = [(x0, y0, z_eave), (x1, y0, z_eave), (x1, y1, z_eave), (x0, y1, z_eave)]
    return trimesh.Trimesh(vertices=bottom + eaves + ridge).convex_hull


def stack_levels(levels: list[dict], *, mm_per_unit: float = 1.0, roof: str = "none",
                 roof_thickness_in: float | None = None, roof_rise_in: float | None = None,
                 apply_logo: bool = True) -> dict:
    """Stack already-built part dicts. Each level needs parts, wall_h, floor_t, and a label.

    Parts are in model inches with z starting at 0 (the same space ``build3d.build`` uses).
    Returns parts, assumptions, and one logo report per floor slab.
    """
    if roof not in ("none", "flat", "gable"):
        raise ValueError(f"roof must be none, flat, or gable (got {roof})")
    assumptions: list[str] = []
    if roof == "flat" and not (roof_thickness_in and roof_thickness_in > 0):
        assumptions.append("Flat roof was requested but no thickness was given, so no roof was built.")
        roof = "none"
    if roof == "gable" and (not (roof_thickness_in and roof_thickness_in > 0) or not (roof_rise_in and roof_rise_in > 0)):
        assumptions.append("Gable roof needs both a thickness and a rise, so no roof was built.")
        roof = "none"

    rename = len(levels) > 1
    z = 0.0
    out: dict[str, trimesh.Trimesh] = {}
    logos: list[dict] = []
    pending_pegs: list[tuple[float, float]] | None = None
    peg_h = peg_r = 0.0

    for i, level in enumerate(levels):
        label = level.get("label") or f"L{i + 1}"
        wall_h = float(level["wall_h"])
        floor_t = float(level["floor_t"])
        parts = {k: _shift(m, z) for k, m in level["parts"].items()}
        floor = parts.get("floor")
        if floor is not None and apply_logo and i > 0:
            from . import brand
            logos.append(brand.apply_underside_logo(parts, mm_per_unit=mm_per_unit))
        elif floor is not None and i == 0 and level.get("logo"):
            logos.append(level["logo"])

        if pending_pegs and floor is not None:
            cut, ok = _cut_sockets(parts["floor"], pending_pegs, peg_h, peg_r)
            parts["floor"] = cut
            if not ok:
                # Keep the storeys at the real floor-to-floor height. The pegs
                # overlap the next slab; say so instead of inventing a gap.
                assumptions.append(f"{label} has no peg sockets (the cut failed). "
                                   "The pegs on the floor below still mark the corners, but they overlap this slab.")
            pending_pegs = None

        key = (lambda name: f"{label}_{name}") if rename else (lambda name: name)
        for name, mesh in parts.items():
            out[key(name)] = mesh

        z_next = z + floor_t + wall_h
        host_name = "walls" if "walls" in parts else ("floor" if floor is not None else None)
        if i < len(levels) - 1 and host_name is not None:
            above = levels[i + 1]["parts"].get("floor")
            peg_h = PEG_HEIGHT_MM / mm_per_unit
            peg_r = (PEG_DIAMETER_MM / 2) / mm_per_unit
            above_mm = float(levels[i + 1]["floor_t"]) * mm_per_unit
            if above is None:
                assumptions.append(f"{label} has no alignment pegs: the next floor has no slab to socket them into.")
            elif above_mm < MIN_SLAB_FOR_PEG_MM:
                assumptions.append(f"{label} has no alignment pegs: the next slab is under {MIN_SLAB_FOR_PEG_MM:g} mm, "
                                   "too thin for a socket without inventing a thicker floor.")
            else:
                inset = PEG_INSET_MM / mm_per_unit
                corners = _footprint_corners(parts[host_name], inset)
                above_lo, above_hi = above.bounds
                shared = []
                for x, y in corners:
                    if above_lo[0] + inset <= x <= above_hi[0] - inset and above_lo[1] + inset <= y <= above_hi[1] - inset:
                        shared.append((x, y))
                if len(shared) < 2:
                    assumptions.append(f"{label} has no alignment pegs: the next floor does not cover two corners of this one.")
                else:
                    peg_h = min(peg_h, 0.6 * above_mm / mm_per_unit)
                    out[key(host_name)] = _add_pegs(out[key(host_name)], shared, peg_h, peg_r)
                    pending_pegs = shared
                    assumptions.append(f"{label} has {len(shared)} alignment pegs on the wall tops "
                                       f"({PEG_DIAMETER_MM:g} mm across, {peg_h * mm_per_unit:.1f} mm tall) "
                                       "for the next floor. Storey heights are the ones you set.")
        z = z_next

    if roof != "none":
        top_floor = levels[-1]["parts"].get("floor")
        if top_floor is None:
            assumptions.append("No roof was built: the top floor has no slab to sit it on.")
        else:
            out["roof"] = _roof_mesh(top_floor, z, roof, float(roof_thickness_in), roof_rise_in)
            if roof == "flat":
                assumptions.append(f"Flat roof, {roof_thickness_in:g} in thick, no overhang. Only the thickness you set.")
            else:
                assumptions.append(f"Simple gable roof, {roof_thickness_in:g} in at the eaves and "
                                   f"{roof_rise_in:g} in of rise. No overhang, fascia, or trusses.")
    return {"parts": out, "assumptions": assumptions, "logos": logos, "storeys": len(levels), "roof": roof}


def _level_polys(job: Path, index: int, spec: dict, params) -> tuple[dict | None, str | None]:
    """Polygons for an extra floor. Missing plans are skipped, never copied from floor 1."""
    dest = job / "levels" / str(index)
    dest.mkdir(parents=True, exist_ok=True)
    plan = Path(spec.get("plan") or "")
    cached = dest / "polygons.json"
    if cached.is_file() and spec.get("detected_plan") == str(plan):
        return json.loads(cached.read_text()), None
    if not plan.is_file():
        return None, f"Floor {index + 1}: '{spec.get('plan')}' was not found, so that floor was not built."
    try:
        from .pipeline import run
        run(plan, dest, params.px_per_inch, params.dpi,
            spec.get("wall_h") or params.wall_h,
            set(params.skip) | {"build3d[trimesh]", "repair_export[pymeshfix]"})
    except Exception as e:  # noqa: BLE001
        return None, f"Floor {index + 1}: could not read {plan.name} ({type(e).__name__}: {e}). It was not built."
    if not cached.is_file():
        return None, f"Floor {index + 1}: {plan.name} produced no polygons, so that floor was not built."
    spec["detected_plan"] = str(plan)
    return json.loads(cached.read_text()), None


def integrate(job: Path, params, base_parts: dict, logo: dict | None = None) -> dict:
    """Add extra floors and a roof on top of an already built ground floor.

    ``changed`` is false when the ground-floor parts should be exported as they are.
    """
    assumptions: list[str] = []
    levels = [{
        "parts": base_parts,
        "wall_h": params.wall_h,
        "floor_t": params.floor_t,
        "label": "L1",
        "logo": logo,
    }]
    inches = 1.0 / (params.px_per_inch or 1.0)
    for n, spec in enumerate(params.levels or [], start=1):
        polys, err = _level_polys(job, n, spec, params)
        if err or not polys:
            assumptions.append(err or f"Floor {n + 1} was not built.")
            continue
        wall_h = spec.get("wall_h") or params.wall_h
        floor_t = spec.get("floor_t") or params.floor_t
        if spec.get("wall_h") in (None, ""):
            assumptions.append(f"Floor {n + 1} wall height was not given, so it uses floor 1's {wall_h:g} in. "
                               "That is not a measurement from this plan.")
        if spec.get("floor_t") in (None, ""):
            assumptions.append(f"Floor {n + 1} slab thickness was not given, so it uses floor 1's {floor_t:g} in.")
        built = build(polys, inches, wall_h=wall_h, floor_t=floor_t, sill=params.sill, lintel=params.lintel,
                      furniture_h=params.furniture_h, frame_trim=FRAME_TRIM)
        if not built:
            assumptions.append(f"Floor {n + 1} has polygons but nothing to extrude, so it was not stacked.")
            continue
        from .repair import repair
        levels.append({"parts": {k: repair(m) for k, m in built.items()},
                       "wall_h": wall_h, "floor_t": floor_t, "label": f"L{n + 1}"})
    want_roof = params.roof != "none"
    if len(levels) == 1 and not want_roof:
        return {"changed": False, "parts": base_parts, "assumptions": assumptions, "logo": logo, "logos": [logo] if logo else []}
    stacked = stack_levels(levels, mm_per_unit=params.print_mm_per_unit, roof=params.roof,
                           roof_thickness_in=params.roof_thickness_in, roof_rise_in=params.roof_rise_in,
                           apply_logo=True)
    first_logo = logo if logo else (stacked["logos"][0] if stacked["logos"] else None)
    return {"changed": True, "parts": stacked["parts"], "assumptions": assumptions + stacked["assumptions"],
            "logo": first_logo, "logos": stacked["logos"], "roof": stacked["roof"], "storeys": stacked["storeys"]}


def stl_mesh(parts: dict[str, trimesh.Trimesh]) -> trimesh.Trimesh:
    """Same fusion as ``merged_for_stl``, including ``L2_walls`` / ``L2_door_frames`` pairs."""
    if not any(_LEVEL_NAME.match(k) for k in parts):
        return merged_for_stl(parts)
    consumed: set[str] = set()
    body: list[trimesh.Trimesh] = []
    for key, mesh in parts.items():
        if key in consumed or key.endswith("door_frames"):
            continue
        if key.endswith("walls"):
            frames_key = key[: -len("walls")] + "door_frames"
            frames = parts.get(frames_key)
            if frames is not None:
                consumed.add(frames_key)
                try:
                    fused = trimesh.boolean.union([mesh, frames], engine="manifold")
                    ok = fused.is_watertight and abs(fused.volume - (mesh.volume + frames.volume)) <= 1e-3 * max(fused.volume, 1e-9)
                except Exception:  # noqa: BLE001
                    ok = False
                body.extend([fused] if ok else [mesh, frames])
                continue
        body.append(mesh)
    return trimesh.util.concatenate(body)
