"""Headless PNG previews: coloured isometric 3D view (numpy z-buffer) and underside view (matplotlib)."""
from __future__ import annotations

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import trimesh  # noqa: E402


def _load_parts(src) -> dict:
    """A dict of part meshes, or a mesh file (a 3MF/GLB written by hp3d gives its named parts back)."""
    if isinstance(src, dict):
        return src
    loaded = trimesh.load(Path(src))
    if isinstance(loaded, trimesh.Scene):
        from .colors import PART_LABELS
        by_label = {v.lower(): k for k, v in PART_LABELS.items()}
        out = {}
        for node in loaded.graph.nodes_geometry:
            T, gname = loaded.graph[node]
            g = loaded.geometry[gname].copy()
            g.apply_transform(T)
            key = by_label.get(str(node).lower(), str(node).lower())
            out[key] = trimesh.util.concatenate([out[key], g]) if key in out else g
        return out
    return {"model": loaded}


def render(src, png: Path, pal: dict | None = None, size: tuple[int, int] = (900, 820),
           elev: float = 52.0, azim: float = -60.0, legend: bool = True, ss: int = 2) -> None:
    """Coloured isometric preview (software z-buffer, so no see-through faces or scribbles).

    ``src`` is a dict ``{part name: mesh}`` (coloured by ``hp3d.colors``) or a mesh file.
    Each part is flat-shaded in its colour, back faces are culled, and creases are
    outlined a little darker. A legend lists the parts that are present.
    """
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont

    from .colors import PART_LABELS, color_of, ordered
    parts = {k: m for k, m in _load_parts(src).items() if len(m.faces)}
    W, H = size[0] * ss, size[1] * ss
    img = np.full((H, W, 3), 255.0)
    if not parts:
        Image.fromarray(img.astype(np.uint8)).save(png)
        return
    a, e = np.radians(azim), np.radians(elev)
    # camera basis: view direction from the viewer toward the scene
    view = -np.array([np.cos(e) * np.cos(a), np.cos(e) * np.sin(a), np.sin(e)])
    right = np.array([-np.sin(a), np.cos(a), 0.0])
    up = np.cross(right, view)
    allv = np.vstack([m.vertices for m in parts.values()])
    ctr = (allv.min(0) + allv.max(0)) / 2

    def proj(v):
        d = v - ctr
        return np.stack([d @ right, d @ up, d @ view], axis=-1)

    P = proj(allv)
    lo, hi = P[:, :2].min(0), P[:, :2].max(0)
    leg_h = (2 * (max(14, size[0] // 30) + 10) + 8) * ss if legend else 0
    sc = min((W * 0.94) / max(hi[0] - lo[0], 1e-9), (H * 0.94 - leg_h) / max(hi[1] - lo[1], 1e-9))
    ox = W / 2 - sc * (lo[0] + hi[0]) / 2
    oy = (H - leg_h) / 2 + sc * (lo[1] + hi[1]) / 2

    def to_px(q):
        return np.stack([ox + sc * q[..., 0], oy - sc * q[..., 1], q[..., 2]], axis=-1)

    zbuf = np.full((H, W), np.inf)
    light = np.array([-0.35, -0.55, 0.76]); light /= np.linalg.norm(light)
    edges = []
    span = float(np.ptp(P[:, 2])) or 1.0
    for k in ordered(parts):
        m = parts[k]
        base = np.array(color_of(k, pal)[:3], float)
        n = m.face_normals
        front = n @ view < -1e-6
        front_i = np.flatnonzero(front)
        tri = to_px(proj(m.triangles[front]))
        shade = 0.62 + 0.38 * np.clip(n[front] @ light, 0, 1)
        if isinstance(m.metadata, dict) and m.metadata.get("hp3d_use_face_colors"):
            fc = np.asarray(m.visual.face_colors)[front_i][:, :3].astype(float)
            cols = np.clip(fc * shade[:, None], 0, 255)
        else:
            cols = np.clip(base[None, :] * shade[:, None], 0, 255)
        for t, c in zip(tri, cols):
            x0, y0 = np.floor(t[:, :2].min(0)).astype(int)
            x1, y1 = np.ceil(t[:, :2].max(0)).astype(int)
            x0, y0 = max(x0, 0), max(y0, 0)
            x1, y1 = min(x1, W - 1), min(y1, H - 1)
            if x1 < x0 or y1 < y0:
                continue
            xs, ys = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
            (ax_, ay), (bx, by), (cx, cy) = t[0, :2], t[1, :2], t[2, :2]
            den = (by - cy) * (ax_ - cx) + (cx - bx) * (ay - cy)
            if abs(den) < 1e-12:
                continue
            w0 = ((by - cy) * (xs - cx) + (cx - bx) * (ys - cy)) / den
            w1 = ((cy - ay) * (xs - cx) + (ax_ - cx) * (ys - cy)) / den
            w2 = 1 - w0 - w1
            inside = (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6)
            if not inside.any():
                continue
            z = w0 * t[0, 2] + w1 * t[1, 2] + w2 * t[2, 2]
            zb = zbuf[y0:y1 + 1, x0:x1 + 1]
            win = inside & (z < zb)
            zb[win] = z[win]
            img[y0:y1 + 1, x0:x1 + 1][win] = c
        # creases (> 25 deg) and open boundaries, outlined later with a depth test
        if len(m.face_adjacency):
            sharp = m.face_adjacency_edges[m.face_adjacency_angles > np.radians(25)]
            if len(sharp):
                edges.append((m.vertices[sharp], base * 0.45))
    eps = 0.004 * span
    for seg, c in edges:
        q = to_px(proj(seg.reshape(-1, 3))).reshape(-1, 2, 3)
        L = np.ceil(np.linalg.norm(q[:, 1, :2] - q[:, 0, :2], axis=1)).astype(int) + 1
        for s0, n_ in zip(q, L):
            tt = np.linspace(0, 1, n_)[:, None]
            pts = s0[0] * (1 - tt) + s0[1] * tt
            xi = np.clip(pts[:, 0].astype(int), 0, W - 1)
            yi = np.clip(pts[:, 1].astype(int), 0, H - 1)
            vis = pts[:, 2] <= zbuf[yi, xi] + eps
            for dx in range(ss):
                img[np.clip(yi[vis], 0, H - 1), np.clip(xi[vis] + dx, 0, W - 1)] = c
    im = Image.fromarray(img.astype(np.uint8)).resize(size, Image.LANCZOS)
    if legend:
        d = ImageDraw.Draw(im)
        fs = max(14, size[0] // 30)       # stays readable when the app shrinks the picture
        try:
            font = ImageFont.truetype("DejaVuSans.ttf", fs)
        except OSError:
            font = ImageFont.load_default()
        rows, row, x = [], [], 10
        for k in ordered(parts):
            w = fs + 8 + int(d.textlength(PART_LABELS.get(k, k), font=font)) + fs
            if row and x + w > size[0] - 10:
                rows.append(row); row, x = [], 10
            row.append((k, x)); x += w
        rows.append(row)
        y = size[1] - len(rows) * (fs + 10) - 4
        for row in rows:
            for k, x in row:
                d.rectangle([x, y, x + fs, y + fs], fill=tuple(color_of(k, pal)[:3]), outline=(60, 60, 60))
                d.text((x + fs + 8, y - fs // 8), PART_LABELS.get(k, k), fill=(40, 40, 40), font=font)
            y += fs + 10
    im.save(png)


if __name__ == "__main__":
    render(Path(sys.argv[1]), Path(sys.argv[2]))


def render_underside(mesh_path: Path, png: Path, title: str | None = None) -> None:
    """Bottom view as you see it after turning the print over (rotate 180 deg about Y).

    Orthographic 2D painter's render: faces are drawn low-to-high; the base
    surface is light, anything recessed up to 3 units deep (the logo) is dark.
    A second panel zooms on the recess when there is one.
    """
    import numpy as np
    from matplotlib.collections import PolyCollection

    m = trimesh.load(mesh_path, force="mesh")
    m.apply_transform(np.diag([-1, 1, -1, 1]))          # flip over: x -> -x, z -> -z
    up = m.face_normals[:, 2] > 1e-6
    tri = m.triangles[up]
    zc = tri[:, :, 2].mean(axis=1)
    order = np.argsort(zc)
    tri, zc = tri[order], zc[order]
    top = zc.max()
    d = top - zc
    colors = np.where((d > 0.05)[:, None] & (d < 3.0)[:, None], [[0.30, 0.20, 0.14, 1]], [[0.86, 0.74, 0.58, 1]])
    colors[d >= 3.0] = [0.55, 0.55, 0.55, 1]
    rec = tri[(d > 0.05) & (d < 3.0)]
    panels = 2 if len(rec) else 1
    fig, axes = plt.subplots(1, panels, figsize=(8 * panels, 6.5))
    axes = np.atleast_1d(axes)
    lo, hi = m.bounds
    for i, ax in enumerate(axes):
        ax.add_collection(PolyCollection(tri[:, :, :2], facecolors=colors, edgecolors=colors, linewidths=0.4))
        if i == 0:
            ax.set_xlim(lo[0], hi[0]); ax.set_ylim(lo[1], hi[1])
            ax.set_title(title or "Underside (print turned over)", fontsize=11)
        else:
            rlo, rhi = rec[:, :, :2].reshape(-1, 2).min(0), rec[:, :, :2].reshape(-1, 2).max(0)
            pad = 0.15 * (rhi - rlo).max()
            ax.set_xlim(rlo[0] - pad, rhi[0] + pad); ax.set_ylim(rlo[1] - pad, rhi[1] + pad)
            ax.set_title(f"Logo recess, zoomed ({rhi[0] - rlo[0]:.0f} x {rhi[1] - rlo[1]:.0f} units)", fontsize=11)
        ax.set_aspect("equal"); ax.set_axis_off()
    fig.savefig(png, dpi=130, bbox_inches="tight")
    plt.close(fig)
