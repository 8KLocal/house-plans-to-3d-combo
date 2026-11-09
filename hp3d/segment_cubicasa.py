"""Stage 1b: room-type + icon segmentation with CubiCasa5k (CC BY-NC 4.0).

Gives what the wall-only UNet cannot: room classes (kitchen, bedroom, bath...)
and icons (doors, windows, toilets, sinks). Used for the room inventory, e.g.
to check a plan really shows two kitchens before the model assumes it.
Requires weights/cubicasa5k/model_best_val_loss_var.pkl (Google Drive, ~200 MB).
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import torch
import torch.nn.functional as F

from . import WEIGHTS
from ._paths import on_path

ROOM_CLASSES = ["Background", "Outdoor", "Wall", "Kitchen", "Living Room", "Bed Room", "Bath",
                "Entry", "Railing", "Storage", "Garage", "Undefined"]
ICON_CLASSES = ["No Icon", "Window", "Door", "Closet", "Electrical Appliance", "Toilet", "Sink",
                "Sauna Bench", "Fire Place", "Bathtub", "Chimney"]
SPLIT = [21, 12, 11]


class CubiCasaSegmenter:
    def __init__(self, weights: Path = WEIGHTS / "cubicasa5k" / "model_best_val_loss_var.pkl",
                 max_side: int = 768):
        with on_path("CubiCasa5k", purge_prefixes=()):
            from floortrans.models.hg_furukawa_original import hg_furukawa_original
        # Build the architecture directly: get_model() calls init_weights(),
        # which loads model_1427.pth via a cwd-relative path. We overwrite
        # every weight from the trained checkpoint anyway.
        m = hg_furukawa_original(n_classes=51)
        n = sum(SPLIT)
        m.conv4_ = torch.nn.Conv2d(256, n, bias=True, kernel_size=1)
        m.upsample = torch.nn.ConvTranspose2d(n, n, kernel_size=4, stride=4)
        ckpt = torch.load(weights, map_location="cpu", weights_only=False)
        m.load_state_dict(ckpt["model_state"])
        self.model = m.eval()
        self.max_side = max_side

    @torch.no_grad()
    def predict(self, rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Return (rooms, icons) class masks at the input's resolution (single pass, no TTA)."""
        h, w = rgb.shape[:2]
        s = min(1.0, self.max_side / max(h, w))
        # Hourglass downsamples 4x several times: keep dims divisible by 32.
        sw, sh = max(32, int(w * s) // 32 * 32), max(32, int(h * s) // 32 * 32)
        small = cv2.resize(rgb, (sw, sh), interpolation=cv2.INTER_AREA)
        t = torch.from_numpy(small).permute(2, 0, 1).float().unsqueeze(0) / 255.0 * 2 - 1
        pred = F.interpolate(self.model(t), size=(sh, sw), mode="bilinear", align_corners=True)[0]
        rooms = pred[SPLIT[0]:SPLIT[0] + SPLIT[1]].argmax(0).to(torch.uint8).numpy()
        icons = pred[SPLIT[0] + SPLIT[1]:].argmax(0).to(torch.uint8).numpy()
        up = lambda m: cv2.resize(m, (w, h), interpolation=cv2.INTER_NEAREST)  # noqa: E731
        return up(rooms), up(icons)


def inventory(rooms: np.ndarray, icons: np.ndarray, min_frac: float = 0.002) -> dict:
    """Count connected regions per room/icon class (tiny speckle ignored)."""
    out: dict = {"rooms": {}, "icons": {}}
    area = rooms.size
    for key, mask, names, skip in (("rooms", rooms, ROOM_CLASSES, {0, 1, 2}),
                                   ("icons", icons, ICON_CLASSES, {0})):
        for cid, name in enumerate(names):
            if cid in skip:
                continue
            n, _, stats, _ = cv2.connectedComponentsWithStats((mask == cid).astype(np.uint8))
            count = int(sum(1 for i in range(1, n) if stats[i, cv2.CC_STAT_AREA] >= min_frac * area))
            if count:
                out[key][name] = count
    return out
