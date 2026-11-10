"""Short one-question-at-a-time check of what the plan or 3D file did not say.

After every build the app asks the first open question; each answer (or Skip)
is saved in job.json under ``quiz`` and the next one is asked. A job is only
marked done in report.json (``review.status == "done"``) when every question
is answered or skipped. Skipped values stay listed as assumptions; nothing a
question offers as "for now" is ever reported as measured.

Questions are rebuilt from the job's current state each time, so answering
the size first can make later questions go away (e.g. heights measured in a
3D file become real once the size is known).
"""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from . import editor as E

UNIT_WORDS = {"mm": 1 / 25.4, "millimetres": 1 / 25.4, "millimeters": 1 / 25.4, "cm": 1 / 2.54,
              "centimetres": 1 / 2.54, "centimeters": 1 / 2.54, "m": 39.3701, "metres": 39.3701, "meters": 39.3701,
              "in": 1.0, "inch": 1.0, "inches": 1.0, "ft": 12.0, "feet": 12.0, "foot": 12.0}


def ft(inches: float) -> str:
    f, i = divmod(round(float(inches), 1), 12)
    if i < 0.05:
        return f"{int(f)} ft"
    return f"{int(f)} ft {i:g} in" if f else f"{i:g} in"


def _width_px(polys: dict) -> float | None:
    pts = [xy for k in ("wall", "window", "floor") for p in polys.get(k, []) for xy in p["outer"]]
    if not pts:
        return None
    x = np.asarray(pts, float)[:, 0]
    return float(x.max() - x.min())


def questions(job: Path, params: E.JobParams, polys: dict | None = None) -> list[dict]:
    """Every question that applies to this job right now, in asking order."""
    polys = E.load_polys(job) if polys is None else polys
    mesh = params.source_kind == "mesh" and params.mesh
    us, meas = set(params.user_set), set(params.measured)
    qs: list[dict] = []
    wpx = _width_px(polys)
    if "px_per_inch" not in us and wpx:
        now = f"right now it comes out {ft(wpx / params.px_per_inch)} wide" if params.px_per_inch else "right now the size is unknown"
        if mesh:
            g = params.mesh.get("unit_guess")
            text = ("How wide is the house in real life, outside wall to outside wall, left to right?\n"
                    f"The 3D file has no units ({now}" + (f", reading it as {g}" if g else "") + ").\n"
                    "Type a length like 32 ft, or the file's unit: mm, cm, m, in or ft.")
        else:
            text = ("How wide is the house, outside wall to outside wall, left to right on the plan?\n"
                    f"Use a dimension printed on the plan ({now}).\n"
                    "Or skip and use \"Show me where on the plan\" in step 1.")
        qs.append({"id": "size", "kind": "length", "text": text, "default": "keep the current size (a guess)"})
    if "wall_h" not in us and "wall_h" not in meas:
        qs.append({"id": "wall_h", "kind": "length", "default": ft(params.wall_h),
                   "text": f"How tall are the walls, floor to ceiling?\nThe plan doesn't say; I'm using {ft(params.wall_h)} for now."})
    if "floor_t" not in us and "floor_t" not in meas:
        lead = "The 3D file has no floor, so I added a base." if mesh else "The plan doesn't show the floor thickness."
        qs.append({"id": "floor_t", "kind": "length", "default": ft(params.floor_t),
                   "text": f"{lead}\nHow thick should the base be? I'm using {ft(params.floor_t)} for now."})
    if polys.get("window"):
        if "sill" not in us and "sill" not in meas:
            qs.append({"id": "sill", "kind": "length", "default": ft(params.sill),
                       "text": f"How high off the floor is the bottom of the windows?\nNot on the plan; I'm using {ft(params.sill)}."})
        if "lintel" not in us and "lintel" not in meas:
            qs.append({"id": "lintel", "kind": "length", "default": ft(params.lintel),
                       "text": f"How high off the floor is the top of the windows?\nNot on the plan; I'm using {ft(params.lintel)}."})
    plan_furn = [p for p in polys.get("furniture", []) if not p.get("from_mesh") and not p.get("added_in_app")]
    if plan_furn:
        names = sorted({p.get("label", "fixture") for p in plan_furn})
        qs.append({"id": "fixtures", "kind": "yesno", "default": "yes",
                   "text": f"I found {len(plan_furn)} fixture(s) drawn on the plan ({', '.join(names)}).\n"
                           "Keep them as simple blocks in the model?"})
    if any(not p.get("height_px") for p in polys.get("furniture", [])) and "furniture_h" not in us:
        qs.append({"id": "furniture_h", "kind": "length", "default": ft(params.furniture_h),
                   "text": f"How tall should the furniture blocks be?\nThe plan doesn't say; I'm using {ft(params.furniture_h)}."})
    nd = len(polys.get("door", []))
    qs.append({"id": "doors", "kind": "yesno", "default": "yes",
               "text": (f"I found {nd} doorway(s) and left them as open door frames (no solid doors).\nIs that right?"
                        if nd else "I didn't find any doorways.\nIs that right?")})
    above = mesh and params.mesh.get("above_walls")
    qs.append({"id": "floors", "kind": "yesno", "default": "yes",
               "text": (f"The 3D file has {above} above the walls. I only read the lowest floor.\nIs that OK for now?"
                        if above else "Does this file show just one floor?")})
    return qs


def pending(job: Path, params: E.JobParams, polys: dict | None = None) -> list[dict]:
    done = params.quiz or {}
    return [q for q in questions(job, params, polys) if q["id"] not in done]


def review(job: Path, params: E.JobParams, polys: dict | None = None) -> dict:
    left = pending(job, params, polys)
    return {"status": "done" if not left else "questions left", "pending": [q["id"] for q in left],
            "answers": params.quiz or {}}


def _record(params: E.JobParams, qid: str, answer: str, how: str) -> None:
    params.quiz = dict(params.quiz or {})
    params.quiz[qid] = {"answer": answer, "how": how, "time": time.strftime("%Y-%m-%d %H:%M:%S %Z")}
    params.log(f"quiz {qid}: {how} ({answer})")


def skip(job: Path, params: E.JobParams, qid: str) -> str:
    q = next((q for q in questions(job, params) if q["id"] == qid), None)
    _record(params, qid, q["default"] if q else "", "skipped")
    params.save(job)
    return f"Skipped - keeping {q['default'] if q else 'the guess'} (still marked as a guess)."


def answer(job: Path, params: E.JobParams, qid: str, text: str, selected=None) -> tuple[str, bool]:
    """Apply one answer. Returns (plain message, needs_rebuild). Raises ValueError if it can't be read."""
    q = next((q for q in questions(job, params) if q["id"] == qid), None)
    if q is None:
        raise ValueError("that question no longer applies")
    t = text.strip().lower()
    polys = E.load_polys(job)
    if q["kind"] == "yesno":
        if t not in ("y", "yes", "n", "no"):
            raise ValueError("please answer yes or no")
        yes = t.startswith("y")
        _record(params, qid, "yes" if yes else "no", "answered")
        msg, rebuild = "Thanks.", False
        if qid == "fixtures" and not yes:
            polys["furniture"] = [p for p in polys.get("furniture", []) if p.get("from_mesh") or p.get("added_in_app")]
            E.save_polys(job, polys)
            params.log("removed plan fixtures (operator said no)")
            msg, rebuild = "Removed the fixtures from the model.", True
        elif qid == "doors" and not yes:
            msg = ("OK - click a wrong doorway and press Delete selected, or say 'add door x0,y0 to x1,y1' for a "
                   "missing one (it cuts a door-frame opening). Solid doors aren't built yet.")
        elif qid == "floors" and not yes:
            msg = ("This version builds one floor per file. Build each floor from its own file for now; "
                   "stacking floors comes later.")
            params.quiz[qid]["note"] = "operator says more than one floor; only one is modelled"
        params.save(job)
        return msg, rebuild
    # length questions
    if qid == "size":
        if params.source_kind == "mesh" and t in UNIT_WORDS and params.mesh:
            ppi = params.mesh["px_per_unit"] / UNIT_WORDS[t]
            how = f"file unit is {t}"
        else:
            inches = _length(text)
            ppi = _width_px(polys) / inches
            how = f"house is {ft(inches)} wide"
        E.apply_actions(job, params, [{"op": "set", "param": "px_per_inch", "value": round(ppi, 6)}], selected)
        _record(params, qid, how, "answered")
        params.save(job)
        return f"Got it: {how}. Sizes updated.", True
    inches = _length(text)
    E.apply_actions(job, params, [{"op": "set", "param": qid, "value": round(inches, 3)}], selected)
    _record(params, qid, ft(inches), "answered")
    params.save(job)
    return f"Set to {ft(inches)}.", True


def _length(text: str) -> float:
    """'9 ft' -> 108 in; 8'6", 36 in, 240 cm also work. A bare number is refused, never guessed."""
    s = text.strip()
    try:
        float(s)
    except ValueError:
        v, _ = E.parse_length(s)
    else:
        raise ValueError(f"'{s}' needs a unit - e.g. '{s} ft' or '{s} in'")
    if v <= 0:
        raise ValueError("the length must be more than 0")
    return v
