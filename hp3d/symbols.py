"""Classical detection of walls, window symbols, stair runs and door swings.

Works from the ink on the plan (OpenCV only, no network and no model weights).
A mark is kept only when its geometry matches a symbol the sheet actually draws:

- walls: a pair of long parallel lines, joined at a real corner, not a dimension line
- windows: a short glass line with jambs, set in that wall
- stairs: five or more evenly spaced tread lines
- doors: a quarter-circle swing at a gap in a wall

The swing is recorded as an opening. The leaf is not a part. Anything that only
might be a symbol is returned under ``uncertain`` and is not turned into geometry.
"""
from __future__ import annotations

import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None


def ink_mask(rgb: np.ndarray) -> np.ndarray:
    """Dark, unsaturated ink. Coloured markup (the green unit line, blue/red notes) is ignored."""
    arr = np.asarray(rgb)
    if arr.ndim == 2:
        return arr.astype(np.int16) < 128
    chan = arr.astype(np.int16)
    mx = np.maximum(np.maximum(chan[:, :, 0], chan[:, :, 1]), chan[:, :, 2])
    mn = np.minimum(np.minimum(chan[:, :, 0], chan[:, :, 1]), chan[:, :, 2])
    return (mx < 128) & ((mx - mn) < 90)


def _runs(row: np.ndarray, min_len: int, merge_gap: int) -> list[tuple[int, int, float]]:
    xs = np.flatnonzero(row)
    if len(xs) == 0:
        return []
    cuts = np.where(np.diff(xs) > merge_gap + 1)[0]
    starts = np.r_[xs[0], xs[cuts + 1]]
    ends = np.r_[xs[cuts], xs[-1]]
    out = []
    for a, b in zip(starts, ends):
        a, b = int(a), int(b)
        span = b - a + 1
        if span < min_len:
            continue
        cov = float(np.count_nonzero(row[a:b + 1])) / span
        out.append((a, b, cov))
    return out


def _strokes_along_rows(mask: np.ndarray, min_len: int, merge_gap: int = 4) -> list[dict]:
    """Collapse consecutive rows of the same line into one stroke. ``a``/``b`` run along the row."""
    height = mask.shape[0]
    active: list[dict] = []
    done: list[dict] = []

    def finish(s: dict) -> None:
        xs = np.asarray(s["xs"], float)
        a = float(np.median(xs[:, 0]))
        b = float(np.median(xs[:, 1]))
        if b - a + 1 < min_len:
            return
        done.append({
            "a": a, "b": b, "p": (s["y0"] + s["y1"]) / 2.0, "L": b - a + 1,
            "cov": float(np.mean(s["covs"])), "thick": s["y1"] - s["y0"] + 1,
        })

    for y in range(height):
        runs = _runs(mask[y], min_len, merge_gap)
        used = [False] * len(runs)
        nxt: list[dict] = []
        for s in active:
            hit = False
            for i, (a, b, cov) in enumerate(runs):
                if used[i]:
                    continue
                ov = min(s["b0"], b) - max(s["a0"], a) + 1
                existing = s["b0"] - s["a0"] + 1
                incoming = b - a + 1
                # Both runs must cover each other. A short crossing (text, a tick) must not steal a long wall.
                if existing > 0 and incoming > 0 and ov >= 0.6 * existing and ov >= 0.6 * incoming:
                    used[i] = True
                    s["y1"] = y
                    s["a0"], s["b0"] = a, b
                    s["xs"].append((a, b))
                    s["covs"].append(cov)
                    nxt.append(s)
                    hit = True
                    break
            if not hit:
                finish(s)
        for i, (a, b, cov) in enumerate(runs):
            if not used[i]:
                nxt.append({"a0": a, "b0": b, "y0": y, "y1": y, "xs": [(a, b)], "covs": [cov]})
        active = nxt
    for s in active:
        finish(s)
    return done


def _line_strokes(mask: np.ndarray) -> tuple[list[dict], list[dict]]:
    side = min(mask.shape)
    min_len = max(12, int(round(0.012 * side)))
    horiz = _strokes_along_rows(mask, min_len)
    vert_raw = _strokes_along_rows(mask.T, min_len)
    vert = [{"a": s["a"], "b": s["b"], "p": s["p"], "L": s["L"], "cov": s["cov"], "thick": s["thick"]} for s in vert_raw]
    for s in horiz:
        s["axis"] = "h"
    for s in vert:
        s["axis"] = "v"
    return horiz, vert


def _overlap(a: dict, b: dict) -> float:
    return min(a["b"], b["b"]) - max(a["a"], b["a"]) + 1


def _in_ladder(a: dict, b: dict, strokes: list[dict]) -> bool:
    """True when a and b are two rungs of a longer even stack (stair treads), not a wall's two faces."""
    gap = abs(b["p"] - a["p"])
    if gap < 1 or a["L"] > 12 * gap:
        return False
    extra = 0
    for c in strokes:
        if c is a or c is b:
            continue
        dist = min(abs(c["p"] - a["p"]), abs(c["p"] - b["p"]))
        if not (0.6 * gap <= dist <= 2.35 * gap):
            continue
        if abs(c["L"] - a["L"]) > 0.35 * a["L"]:
            continue
        if _overlap(c, a) < 0.55 * min(c["L"], a["L"]):
            continue
        extra += 1
    # One nearby parallel line can be a dimension line. Two or more means a tread or hatch stack.
    return extra >= 2


def _crossings(wall: dict, perps: list[dict]) -> tuple[int, int]:
    """Long crossings are walls meeting this one. Short crossings are ticks or jambs."""
    p0, p1 = sorted((wall["p0"], wall["p1"]))
    gap = max(p1 - p0, 1.0)
    a0, a1 = wall["a0"], wall["a1"]
    long_n = short_n = 0
    for s in perps:
        if s["p"] < a0 - 6 or s["p"] > a1 + 6:
            continue
        if s["a"] > p0 + 2 or s["b"] < p1 - 2:
            continue
        if s["L"] >= max(28.0, 2.4 * gap) and (p0 - s["a"]) + (s["b"] - p1) > 0.8 * gap:
            long_n += 1
        elif s["L"] <= 2.3 * gap:
            short_n += 1
    return long_n, short_n


def _pair(strokes: list[dict], perps: list[dict], shape: tuple[int, int], perp_extent: float) -> tuple[list[dict], list[dict]]:
    side = min(shape)
    max_gap = max(10.0, 0.046 * side)
    min_gap = 3.0
    min_len = max(28.0, 0.04 * side)
    usable = [s for s in strokes if s["L"] >= min_len and s["cov"] >= 0.62 and s["thick"] <= 4]
    usable.sort(key=lambda s: s["p"])
    used: set[int] = set()
    walls: list[dict] = []
    rejected: list[dict] = []
    margin = 0.10 * max(shape)
    for i, a in enumerate(usable):
        if i in used:
            continue
        best = None
        for j in range(i + 1, len(usable)):
            b = usable[j]
            gap = b["p"] - a["p"]
            if gap > max_gap:
                break
            if gap < min_gap or j in used:
                continue
            ov = _overlap(a, b)
            short = min(a["L"], b["L"])
            longer = max(a["L"], b["L"])
            if ov < 0.70 * short or ov < 0.50 * longer:
                continue
            if best is None or gap < best[0]:
                best = (gap, j, ov)
        if best is None:
            continue
        j = best[1]
        b = usable[j]
        used.add(i)
        used.add(j)
        a0 = max(a["a"], b["a"])
        a1 = min(a["b"], b["b"])
        wall = {"axis": a["axis"], "a0": float(a0), "a1": float(a1), "p0": float(a["p"]), "p1": float(b["p"]),
                "gap": float(b["p"] - a["p"])}
        long_n, short_n = _crossings(wall, perps)
        near_edge = min(a["p"], b["p"]) < margin or max(a["p"], b["p"]) > perp_extent - margin
        if _in_ladder(a, b, usable):
            rejected.append({"kind": "wall", "status": "uncertain",
                             "detail": "A run of evenly spaced parallel lines was left as treads or hatching, not turned into a wall."})
            continue
        if long_n == 0 and short_n >= 3 and near_edge:
            rejected.append({"kind": "wall", "status": "uncertain",
                             "detail": "Parallel lines near the edge of the sheet look like dimension lines, not a wall. They were not modelled."})
            continue
        if long_n == 0 and near_edge:
            rejected.append({"kind": "wall", "status": "uncertain",
                             "detail": "A pair of lines at the edge of the sheet does not meet another wall, so it was not used."})
            continue
        wall["confidence"] = 0.9 if long_n else 0.72
        walls.append(wall)
    return walls, rejected


def _merge_colinear(walls: list[dict]) -> list[dict]:
    left = walls[:]
    changed = True
    while changed:
        changed = False
        nxt: list[dict] = []
        used = [False] * len(left)
        for i, a in enumerate(left):
            if used[i]:
                continue
            for j in range(i + 1, len(left)):
                if used[j]:
                    continue
                b = left[j]
                if a["axis"] != b["axis"]:
                    continue
                if abs(a["p0"] - b["p0"]) > 2.5 or abs(a["p1"] - b["p1"]) > 2.5:
                    continue
                gap = max(b["a0"], a["a0"]) - min(a["a1"], b["a1"])
                thick = max(a["gap"], b["gap"])
                if 0 <= gap <= max(8.0, 1.1 * thick):
                    a = dict(a)
                    a["a0"] = min(a["a0"], b["a0"])
                    a["a1"] = max(a["a1"], b["a1"])
                    used[j] = True
                    changed = True
            nxt.append(a)
            used[i] = True
        left = nxt
    return left


def _join_corners(walls: list[dict]) -> list[dict]:
    """Extend a wall a short distance so it meets a wall it already almost touches. Door gaps are wider than this."""
    horiz = [w for w in walls if w["axis"] == "h"]
    vert = [w for w in walls if w["axis"] == "v"]
    for h in horiz:
        hy = (h["p0"] + h["p1"]) / 2.0
        for v in vert:
            reach = max(8.0, 1.3 * max(h["gap"], v["gap"]))
            vx = (v["p0"] + v["p1"]) / 2.0
            if abs(vx - h["a0"]) > reach and abs(vx - h["a1"]) > reach:
                continue
            if abs(v["a0"] - hy) <= reach:
                v["a0"] = hy
            elif abs(v["a1"] - hy) <= reach:
                v["a1"] = hy
            else:
                continue
            if abs(h["a0"] - vx) <= abs(h["a1"] - vx):
                h["a0"] = vx
            else:
                h["a1"] = vx
    for w in walls:
        if w["a1"] < w["a0"]:
            w["a0"], w["a1"] = w["a1"], w["a0"]
    return [w for w in walls if w["a1"] - w["a0"] >= 16]


def _rect(axis: str, a0: float, a1: float, p0: float, p1: float, **extra) -> dict:
    if axis == "h":
        x0, x1, y0, y1 = a0, a1, min(p0, p1), max(p0, p1)
    else:
        x0, x1, y0, y1 = min(p0, p1), max(p0, p1), a0, a1
    return {"outer": [[round(x0, 1), round(y0, 1)], [round(x1, 1), round(y0, 1)],
                      [round(x1, 1), round(y1, 1)], [round(x0, 1), round(y1, 1)]],
            "holes": [], **extra}


def _longest(col: np.ndarray) -> int:
    best = cur = 0
    for v in col:
        if v:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def _windows_in_band(mask: np.ndarray, wall: dict) -> tuple[list[dict], list[dict]]:
    """Window symbols inside one wall band. The band is rotated so the wall runs left to right."""
    p0, p1 = sorted((wall["p0"], wall["p1"]))
    a0, a1 = wall["a0"], wall["a1"]
    y0, y1 = int(np.floor(p0)), int(np.ceil(p1))
    x0, x1 = int(np.floor(a0)), int(np.ceil(a1))
    height, width = mask.shape
    if wall["axis"] == "h":
        y0, y1 = max(0, y0), min(height - 1, y1)
        x0, x1 = max(0, x0), min(width - 1, x1)
        if y1 - y0 < 3 or x1 - x0 < 20:
            return [], []
        band = mask[y0:y1 + 1, x0:x1 + 1]
    else:
        y0, y1 = max(0, y0), min(width - 1, y1)
        x0, x1 = max(0, x0), min(height - 1, x1)
        if y1 - y0 < 3 or x1 - x0 < 20:
            return [], []
        band = mask[x0:x1 + 1, y0:y1 + 1].T
    bh, bw = band.shape
    thick = bh
    row_ink = band.mean(axis=1)
    face_rows = set(np.argsort(row_ink)[-2:].tolist())
    found: list[dict] = []
    unsure: list[dict] = []
    min_w = max(12, int(1.4 * thick))
    max_w = int(max(70, 16 * thick))
    for r in range(bh):
        if r in face_rows:
            continue
        runs = _runs(band[r], min_w, 2)
        for a, b, cov in runs:
            length = b - a + 1
            if length > max_w or cov < 0.7:
                continue
            if length > 0.92 * bw:
                continue
            margin = max(4, length // 10)
            if a < margin or b > bw - 1 - margin:
                unsure.append({"kind": "window", "status": "uncertain",
                               "detail": "A short line in a wall stops at the end of the wall, so it was not called a window."})
                continue
            # Text crossing a wall spills out of the band. A window symbol stays inside it.
            outside = []
            if wall["axis"] == "h":
                yy0, yy1 = int(np.floor(min(wall["p0"], wall["p1"]))), int(np.ceil(max(wall["p0"], wall["p1"])))
                xx0, xx1 = int(x0 + a), int(x0 + b)
                if yy0 > 4:
                    outside.append(mask[yy0 - 6:yy0 - 1, xx0:xx1 + 1])
                if yy1 + 6 < mask.shape[0]:
                    outside.append(mask[yy1 + 2:yy1 + 7, xx0:xx1 + 1])
            else:
                xx0, xx1 = int(np.floor(min(wall["p0"], wall["p1"]))), int(np.ceil(max(wall["p0"], wall["p1"])))
                yy0, yy1 = int(x0 + a), int(x0 + b)
                if xx0 > 4:
                    outside.append(mask[yy0:yy1 + 1, xx0 - 6:xx0 - 1])
                if xx1 + 6 < mask.shape[1]:
                    outside.append(mask[yy0:yy1 + 1, xx1 + 2:xx1 + 7])
            outside = [o for o in outside if o.size]
            if outside and max(float(o.mean()) for o in outside) > 0.18:
                unsure.append({"kind": "window", "status": "uncertain",
                               "detail": "Ink crosses the wall and continues outside it, so it was kept as drawing, not a window."})
                continue
            if band[r, max(0, a - margin):a].mean() > 0.35 or band[r, b + 1:b + 1 + margin].mean() > 0.35:
                continue
            span_need = max(3, int(0.55 * thick))
            if not any(_longest(band[:, x]) >= span_need for x in range(max(0, a - 2), min(bw, a + 3))):
                continue
            if not any(_longest(band[:, x]) >= span_need for x in range(max(0, b - 2), min(bw, b + 3))):
                continue
            faces_ok = True
            for fr in face_rows:
                row = band[fr]
                if row[a - margin:a].mean() < 0.45 or row[b + 1:b + 1 + margin].mean() < 0.45:
                    faces_ok = False
                    break
            if not faces_ok:
                unsure.append({"kind": "window", "status": "uncertain",
                               "detail": "A glass line does not sit in a wall that continues on both sides, so no opening was cut."})
                continue
            found.append({"a": a, "b": b, "row": r, "confidence": 0.86})
    # Also a gap window: both faces break and one to three glass lines fill the break.
    if len(face_rows) >= 2:
        gaps = []
        for fr in face_rows:
            row = band[fr]
            holes = _gaps(row)
            gaps.append(holes)
        for g0 in gaps[0]:
            for g1 in gaps[1]:
                ov = min(g0[1], g1[1]) - max(g0[0], g1[0]) + 1
                if ov < min_w or ov > max_w:
                    continue
                aa, bb = max(g0[0], g1[0]), min(g0[1], g1[1])
                glass = 0
                for r in range(bh):
                    if r in face_rows:
                        continue
                    if band[r, aa:bb + 1].mean() > 0.55:
                        glass += 1
                if 1 <= glass <= 3:
                    found.append({"a": aa, "b": bb, "row": -1, "confidence": 0.8})
    merged = _merge_spans(found)
    wins = []
    for m in merged:
        extra = {"source": "window-symbol", "confidence": m["confidence"], "sill_source": "not on plan"}
        if wall["axis"] == "h":
            wins.append(_rect("h", x0 + m["a"], x0 + m["b"], p0, p1, **extra))
        else:
            wins.append(_rect("v", x0 + m["a"], x0 + m["b"], p0, p1, **extra))
    return wins, unsure


def _gaps(row: np.ndarray, min_len: int = 12) -> list[tuple[int, int]]:
    xs = np.flatnonzero(~row)
    if len(xs) == 0:
        return []
    cuts = np.where(np.diff(xs) > 1)[0]
    starts = np.r_[xs[0], xs[cuts + 1]]
    ends = np.r_[xs[cuts], xs[-1]]
    return [(int(a), int(b)) for a, b in zip(starts, ends) if b - a + 1 >= min_len and a > 2 and b < len(row) - 3]


def _merge_spans(found: list[dict]) -> list[dict]:
    found = sorted(found, key=lambda m: m["a"])
    out: list[dict] = []
    for m in found:
        if out and m["a"] <= out[-1]["b"] - 0.5 * (m["b"] - m["a"]):
            if m["confidence"] > out[-1]["confidence"]:
                out[-1] = m
            continue
        out.append(dict(m))
    return out


def _box_of_stair(group: list[dict], axis: str) -> tuple[float, float, float, float]:
    a0 = min(s["a"] for s in group)
    a1 = max(s["b"] for s in group)
    p0 = min(s["p"] for s in group)
    p1 = max(s["p"] for s in group)
    if axis == "h":
        return a0, p0, a1, p1
    return p0, a0, p1, a1


def _stairs(strokes: list[dict], axis: str, shape: tuple[int, int]) -> tuple[list[dict], list[dict]]:
    side = min(shape)
    min_l = max(14.0, 0.02 * side)
    max_l = 0.55 * side
    cands = [s for s in strokes if min_l <= s["L"] <= max_l and s["thick"] <= 3 and s["cov"] >= 0.35]
    cands.sort(key=lambda s: s["p"])
    used = [False] * len(cands)
    found: list[dict] = []
    unsure: list[dict] = []
    for i, s in enumerate(cands):
        if used[i]:
            continue
        grp = [i]
        last = s["p"]
        used[i] = True
        for j in range(i + 1, len(cands)):
            if used[j]:
                continue
            t = cands[j]
            dp = t["p"] - last
            if dp > max(32.0, 0.55 * s["L"]):
                break
            if dp < 5.0:
                continue
            if abs(t["L"] - s["L"]) > 0.30 * s["L"]:
                continue
            if abs(t["a"] - s["a"]) > 0.28 * s["L"] or abs(t["b"] - s["b"]) > 0.28 * s["L"]:
                continue
            grp.append(j)
            used[j] = True
            last = t["p"]
        if len(grp) < 5:
            for k in grp:
                used[k] = False
            if len(grp) >= 3:
                unsure.append({"kind": "stair", "status": "uncertain",
                               "detail": f"{len(grp)} parallel lines are too few to call a stair, so none was built."})
            continue
        members = [cands[k] for k in grp]
        ps = np.array([m["p"] for m in members])
        gaps = np.diff(ps)
        mu = float(np.mean(gaps))
        cv = float(np.std(gaps) / mu) if mu else 1.0
        lens = np.array([m["L"] for m in members])
        span = float(ps[-1] - ps[0])
        dashed = float(np.mean([m["cov"] for m in members])) < 0.75
        if cv > 0.18 or span < 0.85 * float(np.median(lens)) or mu < 6:
            for k in grp:
                used[k] = False
            unsure.append({"kind": "stair", "status": "uncertain",
                           "detail": "Parallel lines are uneven or too short to be treads, so they were not built as stairs."})
            continue
        x0, y0, x1, y1 = _box_of_stair(members, axis)
        pad = mu / 2.0
        if axis == "h":
            y0, y1 = y0 - pad, y1 + pad
        else:
            x0, x1 = x0 - pad, x1 + pad
        conf = 0.78 if dashed else 0.9
        conf = max(0.7, conf - cv)
        found.append(_rect("h", x0, x1, y0, y1, source="tread-lines", confidence=round(conf, 2),
                           n_treads=len(members), travel="y" if axis == "h" else "x",
                           dashed=dashed, rise_in=None, rise_source="not on plan",
                           up_end="unspecified"))
    return found, unsure


def _span_deg(ang: np.ndarray) -> float:
    if len(ang) < 3:
        return 0.0
    a = np.sort(ang)
    gaps = np.diff(a)
    gaps = np.r_[gaps, a[0] + 2 * np.pi - a[-1]]
    cut = int(np.argmax(gaps))
    start = a[(cut + 1) % len(a)]
    return float(np.degrees((2 * np.pi - gaps[cut])))


def _circumcircle(p1: np.ndarray, p2: np.ndarray, p3: np.ndarray):
    a = np.array([[p2[0] - p1[0], p2[1] - p1[1]], [p3[0] - p1[0], p3[1] - p1[1]]], float)
    if abs(float(np.linalg.det(a))) < 1e-4:
        return None
    b = 0.5 * np.array([np.dot(p2, p2) - np.dot(p1, p1), np.dot(p3, p3) - np.dot(p1, p1)])
    try:
        c = np.linalg.solve(a, b)
    except np.linalg.LinAlgError:
        return None
    r = float(np.hypot(*(c - p1)))
    return float(c[0]), float(c[1]), r


def _best_circle(pts: np.ndarray):
    """Circle that the most points actually lie on. A quarter-circle's enclosing circle is the wrong one."""
    rng = np.random.default_rng(0)
    best = None
    n = len(pts)
    tries = 48 if n >= 3 else 0
    for _ in range(tries):
        sel = pts[rng.choice(n, 3, replace=False)]
        circ = _circumcircle(sel[0], sel[1], sel[2])
        if circ is None:
            continue
        x, y, r = circ
        if not 12.0 <= r <= 120.0:
            continue
        d = np.hypot(pts[:, 0] - x, pts[:, 1] - y)
        inl = np.abs(d - r) <= max(2.2, 0.07 * r)
        count = int(inl.sum())
        if count < 18:
            continue
        if best is None or count > best[0]:
            best = (count, x, y, r, inl)
    if best is None:
        return None
    _, x, y, r, inl = best
    return x, y, r, inl


def _arc_ends(ang: np.ndarray, pts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The two endpoints of the occupied arc, on either side of the largest angular gap."""
    order = np.argsort(ang)
    a = ang[order]
    gaps = np.diff(a)
    wrap = float(a[0] + 2 * np.pi - a[-1])
    if len(gaps) == 0 or wrap >= float(gaps.max()):
        return pts[order[0]], pts[order[-1]]
    k = int(np.argmax(gaps))
    rel = (ang - a[k + 1]) % (2 * np.pi)
    return pts[int(np.argmin(rel))], pts[int(np.argmax(rel))]


def _strip_lines(mask: np.ndarray, horiz: list[dict], vert: list[dict]) -> np.ndarray:
    """Take long straight strokes off the ink so a door swing is its own piece."""
    clean = mask.copy()
    for s in horiz + vert:
        if s["L"] < 36 or s.get("cov", 1) < 0.7:
            continue
        if s["axis"] == "h":
            y = int(round(s["p"]))
            clean[max(0, y - 1):y + 2, int(s["a"]):int(s["b"]) + 1] = False
        else:
            x = int(round(s["p"]))
            clean[int(s["a"]):int(s["b"]) + 1, max(0, x - 1):x + 2] = False
    return clean


def _door_arcs(mask: np.ndarray) -> list[dict]:
    if cv2 is None:
        return []
    img = mask.astype(np.uint8) * 255
    n, labels, stats, _ = cv2.connectedComponentsWithStats(img, connectivity=8)
    arcs = []
    for i in range(1, n):
        area = int(stats[i, cv2.CC_STAT_AREA])
        if area < 25 or area > 4000:
            continue
        ys, xs = np.nonzero(labels == i)
        if len(xs) < 25:
            continue
        pts = np.stack([xs, ys], 1).astype(float)
        if len(pts) > 600:
            pts = pts[:: max(1, len(pts) // 600)]
        fit = _best_circle(pts)
        if fit is None:
            continue
        x, y, r, inl = fit
        p = pts[inl]
        ang = np.arctan2(p[:, 1] - y, p[:, 0] - x)
        span = _span_deg(ang)
        if not 55.0 <= span <= 140.0:
            continue
        p0, p1 = _arc_ends(ang, p)
        arcs.append({"cx": float(x), "cy": float(y), "r": float(r), "span": span,
                     "p0": (float(p0[0]), float(p0[1])), "p1": (float(p1[0]), float(p1[1])),
                     "confidence": round(float(min(0.95, 0.55 + inl.mean() / 2)), 2)})
    return arcs


def _face_gaps(mask: np.ndarray, wall: dict) -> list[tuple[float, float]]:
    """Breaks that show up on both faces of a wall, in along-wall coordinates."""
    p0, p1 = sorted((float(wall["p0"]), float(wall["p1"])))
    a0, a1 = int(wall["a0"]), int(wall["a1"])
    rows = []
    for p in (p0, p1):
        if wall["axis"] == "h":
            y = int(round(p))
            if y < 0 or y >= mask.shape[0]:
                return []
            row = mask[y, max(0, a0):min(mask.shape[1], a1 + 1)]
        else:
            x = int(round(p))
            if x < 0 or x >= mask.shape[1]:
                return []
            row = mask[max(0, a0):min(mask.shape[0], a1 + 1), x]
        rows.append(row)
    if len(rows) < 2:
        return []
    g0, g1 = _gaps(rows[0], 14), _gaps(rows[1], 14)
    out = []
    for a, b in g0:
        for c, d in g1:
            ov = min(b, d) - max(a, c) + 1
            if ov >= 14 and ov >= 0.6 * min(b - a, d - c):
                out.append((wall["a0"] + max(a, c), wall["a0"] + min(b, d)))
    return out


def _gaps_between(walls: list[dict]) -> list[tuple[dict, float, float]]:
    """A doorway is often the space between two colinear wall pieces, not a hole inside one piece."""
    out = []
    for i, a in enumerate(walls):
        for b in walls[i + 1:]:
            if a["axis"] != b["axis"]:
                continue
            if abs(a["p0"] - b["p0"]) > 3.5 or abs(a["p1"] - b["p1"]) > 3.5:
                continue
            if a["a1"] < b["a0"]:
                g0, g1 = a["a1"], b["a0"]
                host = a
            elif b["a1"] < a["a0"]:
                g0, g1 = b["a1"], a["a0"]
                host = b
            else:
                continue
            width = g1 - g0
            if 16 <= width <= 120:
                out.append((host, g0, g1))
    return out


def _match_doors(arcs: list[dict], walls: list[dict], mask: np.ndarray) -> tuple[list[dict], list[dict]]:
    doors = []
    unsure = []
    used_gaps: set[tuple] = set()
    candidates = [(w, g0, g1) for w in walls for g0, g1 in _face_gaps(mask, w)]
    candidates += _gaps_between(walls)
    for arc in arcs:
        best = None
        for w, g0, g1 in candidates:
            mid = ((g0 + g1) / 2.0, (w["p0"] + w["p1"]) / 2.0) if w["axis"] == "h" else ((w["p0"] + w["p1"]) / 2.0, (g0 + g1) / 2.0)
            for pt in (arc["p0"], arc["p1"], (arc["cx"], arc["cy"])):
                dist = float(np.hypot(pt[0] - mid[0], pt[1] - mid[1]))
                if dist <= max(28.0, arc["r"] * 1.25) and (best is None or dist < best[0]):
                    best = (dist, w, g0, g1)
        if best is None:
            unsure.append({"kind": "door", "status": "uncertain",
                           "detail": "A curved swing was seen but it does not meet a gap in a wall, so no doorway was cut."})
            continue
        _, w, g0, g1 = best
        key = (w["axis"], round(g0, 1), round(g1, 1), round(w["p0"], 1))
        if key in used_gaps:
            continue
        used_gaps.add(key)
        doors.append(_rect(w["axis"], g0, g1, w["p0"], w["p1"], source="door-swing", cut=True,
                           confidence=arc["confidence"], leaf=False))
    return doors, unsure


def _drop_overlaps(windows: list[dict], stairs: list[dict]) -> list[dict]:
    def box(p):
        xs = [v[0] for v in p["outer"]]
        ys = [v[1] for v in p["outer"]]
        return min(xs), min(ys), max(xs), max(ys)

    kept = []
    for w in windows:
        wx0, wy0, wx1, wy1 = box(w)
        cx, cy = (wx0 + wx1) / 2, (wy0 + wy1) / 2
        if any(box(s)[0] <= cx <= box(s)[2] and box(s)[1] <= cy <= box(s)[3] for s in stairs):
            continue
        kept.append(w)
    return kept


def detect(rgb: np.ndarray) -> dict:
    """Detect structural symbols. Uncertain marks are listed and not returned as parts."""
    mask = ink_mask(rgb)
    if mask.size == 0 or int(mask.sum()) < 20:
        return {"walls": [], "windows": [], "doors": [], "stairs": [], "uncertain": [
            {"kind": "wall", "status": "not_found", "detail": "No ink on the plan, so no walls were traced."}],
            "wall_source": "classical"}
    horiz, vert = _line_strokes(mask)
    h_walls, h_rej = _pair(horiz, vert, mask.shape, mask.shape[0])
    v_walls, v_rej = _pair(vert, horiz, mask.shape, mask.shape[1])
    walls = _join_corners(_merge_colinear(h_walls + v_walls))
    windows: list[dict] = []
    win_unsure: list[dict] = []
    for w in walls:
        got, uns = _windows_in_band(mask, w)
        windows.extend(got)
        win_unsure.extend(uns)
    stairs, stair_unsure = [], []
    for strokes, axis in ((horiz, "h"), (vert, "v")):
        got, uns = _stairs(strokes, axis, mask.shape)
        stairs.extend(got)
        stair_unsure.extend(uns)
    stairs = _nms_boxes(stairs)
    windows = _drop_overlaps(_nms_boxes(windows), stairs)
    arcs = _door_arcs(_strip_lines(mask, horiz, vert))
    doors, door_unsure = _match_doors(arcs, walls, mask)
    wall_polys = [_rect(w["axis"], w["a0"], w["a1"], w["p0"], w["p1"], source="classical-lines",
                        confidence=w.get("confidence", 0.75)) for w in walls]
    # Cap repeated uncertain notes so the report stays readable.
    unsure = _unique(h_rej + v_rej + win_unsure + stair_unsure + door_unsure, limit=8)
    if not wall_polys:
        unsure.insert(0, {"kind": "wall", "status": "not_found",
                          "detail": "No wall pair met another wall. Walls were not invented from the rest of the drawing."})
    return {"walls": wall_polys, "windows": windows, "doors": doors, "stairs": stairs,
            "uncertain": unsure, "wall_source": "classical"}


def _nms_boxes(polys: list[dict]) -> list[dict]:
    def box(p):
        xs = [v[0] for v in p["outer"]]
        ys = [v[1] for v in p["outer"]]
        return min(xs), min(ys), max(xs), max(ys)

    def iou(a, b) -> float:
        ax0, ay0, ax1, ay1 = a
        bx0, by0, bx1, by1 = b
        iw, ih = max(0.0, min(ax1, bx1) - max(ax0, bx0)), max(0.0, min(ay1, by1) - max(ay0, by0))
        inter = iw * ih
        if inter <= 0:
            return 0.0
        aa = max(ax1 - ax0, 0) * max(ay1 - ay0, 0)
        bb = max(bx1 - bx0, 0) * max(by1 - by0, 0)
        return inter / max(aa + bb - inter, 1e-6)

    order = sorted(polys, key=lambda p: -float(p.get("confidence", 0)))
    kept: list[dict] = []
    for p in order:
        b = box(p)
        if all(iou(b, box(k)) < 0.35 for k in kept):
            kept.append(p)
    return kept


def _unique(items: list[dict], limit: int) -> list[dict]:
    seen = set()
    out = []
    for it in items:
        key = (it.get("kind"), it.get("detail"))
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
        if len(out) >= limit:
            break
    return out
