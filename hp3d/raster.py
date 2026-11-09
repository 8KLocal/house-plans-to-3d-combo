"""Stage 0: load a plan (PDF page or image) as an RGB numpy array."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image


@dataclass
class Plan:
    path: Path
    rgb: np.ndarray            # H x W x 3 uint8
    is_pdf: bool
    page: int = 0
    dpi: float | None = None   # set for PDFs: px = pt * dpi / 72

    @property
    def pt_to_px(self) -> float | None:
        return None if self.dpi is None else self.dpi / 72.0


def load_plan(path: str | Path, page: int = 0, dpi: float = 100.0) -> Plan:
    path = Path(path)
    if path.suffix.lower() == ".pdf":
        import pymupdf

        doc = pymupdf.open(path)
        pix = doc[page].get_pixmap(dpi=int(dpi), alpha=False)
        rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)[..., :3]
        return Plan(path, rgb.copy(), True, page, float(int(dpi)))
    rgb = np.array(Image.open(path).convert("RGB"))
    return Plan(path, rgb, False)
