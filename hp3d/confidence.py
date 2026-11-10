"""What the plan actually showed, and what was left unspecified on purpose.

Every item is one of: shown (taken from the drawing), unspecified (the plan does
not say, and any number used is a named placeholder), uncertain (something was
seen but not clear enough to build), not_found, or unavailable. Uncertain and
unspecified items are not filled in with a guess presented as a measurement.
"""
from __future__ import annotations


def build_report(polys: dict | None, detected: dict | None, snap: dict | None, ocr: dict | None,
                 scale: dict | None, wall_h: float, sill: float, lintel: float) -> dict:
    polys = polys or {}
    detected = detected or {}
    snap = snap or {}
    ocr = ocr or {}
    scale = scale or {}
    items: list[dict] = []

    walls = polys.get("wall") or []
    windows = polys.get("window") or []
    doors = polys.get("door") or []
    stairs = polys.get("stair") or []
    items.append({
        "id": "walls", "kind": "wall",
        "status": "shown" if walls else "not_found",
        "count": len(walls),
        "detail": (f"{len(walls)} wall piece(s) traced from the drawing."
                   if walls else "No walls were traced. None were invented."),
    })
    if snap:
        left = int(snap.get("left_edges") or 0)
        items.append({
            "id": "snap", "kind": "snap",
            "status": "shown" if snap.get("snapped_edges") else ("uncertain" if left else "shown"),
            "snapped_edges": int(snap.get("snapped_edges") or 0),
            "left_edges": left,
            "reverted_rings": int(snap.get("reverted_rings") or 0),
            "detail": (f"Straightened {snap.get('snapped_edges', 0)} edge(s) that were already nearly straight or square. "
                       f"{left} edge(s) were left as drawn because a straight line or a right angle did not fit."
                       + (f" {snap.get('reverted_rings', 0)} outline(s) were kept because snapping would have changed their shape."
                          if snap.get("reverted_rings") else "")),
            "samples": (snap.get("samples") or [])[:8],
        })
    if windows:
        items.append({
            "id": "windows", "kind": "window", "status": "shown", "count": len(windows),
            "detail": f"{len(windows)} window symbol(s) cut out of the walls.",
        })
    else:
        items.append({
            "id": "windows", "kind": "window", "status": "not_found", "count": 0,
            "detail": "No window symbol was clear enough to cut an opening.",
        })
    items.append({
        "id": "sill", "kind": "sill", "status": "unspecified", "placeholder_in": sill,
        "detail": f"The plan view does not give the window-sill height. The model uses {sill:g} in as a placeholder, not as a measured height.",
    })
    items.append({
        "id": "lintel", "kind": "lintel", "status": "unspecified", "placeholder_in": lintel,
        "detail": f"The plan view does not give the window-head height. The model uses {lintel:g} in as a placeholder, not as a measured height.",
    })
    items.append({
        "id": "wall_height", "kind": "wall_h", "status": "unspecified", "placeholder_in": wall_h,
        "detail": f"Wall height {wall_h:g} in is a placeholder. The plan does not print it.",
    })
    items.append({
        "id": "door_leaves", "kind": "door", "status": "not_modeled", "count": len(doors),
        "detail": ("Door leaves are not built. "
                   + (f"{len(doors)} opening(s) stay as frames only." if doors else
                      "No doorway was clear enough to cut; none was added.")),
    })
    if stairs:
        for i, s in enumerate(stairs):
            items.append({
                "id": f"stair-{i}", "kind": "stair", "status": "shown",
                "n_treads": s.get("n_treads"), "dashed": bool(s.get("dashed")),
                "confidence": s.get("confidence"),
                "detail": (f"Stair {i + 1}: {s.get('n_treads')} treads counted from the drawing"
                           + (" (dashed lines)." if s.get("dashed") else ".")),
            })
            items.append({
                "id": f"stair-{i}-rise", "kind": "stair_rise", "status": "unspecified",
                "n_treads": s.get("n_treads"),
                "detail": (f"The riser height of stair {i + 1} is not printed. "
                           f"The {s.get('n_treads')} treads are stacked so the top step is at the placeholder wall height "
                           f"({wall_h:g} in). That rise was not measured. Which end is up was not read."),
            })
    else:
        items.append({"id": "stairs", "kind": "stair", "status": "not_found",
                      "detail": "No stair run (five or more even tread lines) was found, so no steps were built."})

    for note in detected.get("uncertain") or []:
        items.append({"id": f"uncertain-{len(items)}", "kind": note.get("kind", "unknown"),
                      "status": note.get("status", "uncertain"), "detail": note.get("detail", "")})

    if scale.get("measured"):
        scale_status = "shown"
        scale_detail = ocr.get("detail") or scale.get("source")
    elif scale.get("source") == "user-supplied":
        scale_status = "shown"
        scale_detail = "Scale was supplied for this job."
    elif ocr.get("status") == "unavailable" or ocr.get("scale_status") == "unavailable":
        scale_status = "unavailable"
        scale_detail = ocr.get("detail") or "Dimension text could not be read."
    elif scale.get("px_per_inch"):
        scale_status = "estimated"
        scale_detail = str(scale.get("source") or "Scale was estimated.")
    else:
        scale_status = ocr.get("scale_status") or ocr.get("status") or "not_found"
        scale_detail = ocr.get("detail") or "No scale was read from the plan."
    items.append({
        "id": "scale", "kind": "scale", "status": scale_status,
        "px_per_inch": scale.get("px_per_inch"),
        "detail": scale_detail,
    })
    for sample in (ocr.get("unused") or [])[:8]:
        items.append({"id": f"ocr-unused-{len(items)}", "kind": "dimension", "status": "uncertain",
                      "detail": f"Ignored text {sample.get('text')!r}: {sample.get('reason')}"})
    for sample in ocr.get("samples") or []:
        if sample.get("ppi") is None and sample.get("reason"):
            items.append({"id": f"ocr-line-{len(items)}", "kind": "dimension", "status": "uncertain",
                          "detail": f"{sample.get('text')!r}: {sample.get('reason')}"})

    not_modeled = [it["detail"] for it in items if it["status"] in {"uncertain", "unspecified", "not_found", "unavailable", "not_modeled"}]
    modeled = [it["detail"] for it in items if it["status"] in {"shown", "estimated"}]
    return {"items": items, "modeled": modeled, "not_modeled": not_modeled}


def refresh_defaults(report: dict, user_set: list, measured: list, values: dict) -> dict:
    """After the user answers a height, stop calling that value unspecified."""
    report = dict(report)
    items = []
    known = set(user_set or []) | set(measured or [])
    names = {"sill": "sill", "lintel": "lintel", "wall_h": "wall height"}
    for it in report.get("items") or []:
        kind = it.get("kind")
        if kind in names and kind in known and it.get("status") == "unspecified":
            it = dict(it)
            it["status"] = "shown" if kind in set(measured or []) else "set_by_user"
            it["detail"] = f"{names[kind].capitalize()} {values.get(kind):g} in was " + (
                "measured from the file." if kind in set(measured or []) else "set by you, not read from the plan.")
            it.pop("placeholder_in", None)
        items.append(it)
    report["items"] = items
    report["not_modeled"] = [it["detail"] for it in items if it["status"] in {"uncertain", "unspecified", "not_found", "unavailable", "not_modeled"}]
    report["modeled"] = [it["detail"] for it in items if it["status"] in {"shown", "estimated", "set_by_user"}]
    return report
