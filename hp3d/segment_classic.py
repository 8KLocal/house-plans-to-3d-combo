"""Stage 1c: classical OpenCV wall detection with FloorplanToBlender3d (GPL-3.0).

No weights, no GPU, no Blender: we only call its FloorplanToBlenderLib.detect
functions. Used as a weight-free fallback / cross-check for the UNet walls.
"""
from __future__ import annotations

import cv2
import numpy as np

from ._paths import on_path


def wall_mask(rgb: np.ndarray) -> np.ndarray:
    with on_path("FloorplanToBlender3d"):
        from FloorplanToBlenderLib import detect
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    walls = detect.wall_filter(gray)          # uint8 0/255 wall pixels
    return (walls > 0).astype(np.uint8)


def wall_boxes(rgb: np.ndarray) -> list:
    """FloorplanToBlender3d's own polygonised wall outlines (approxPolyDP boxes)."""
    with on_path("FloorplanToBlender3d"):
        from FloorplanToBlenderLib import detect
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    boxes, _ = detect.precise_boxes(detect.wall_filter(gray))
    return boxes
