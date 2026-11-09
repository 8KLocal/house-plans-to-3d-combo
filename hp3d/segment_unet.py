"""Stage 1a: wall/door/window segmentation with Yytsi/floorplan-to-3d (MIT).

Upstream only accepts CubiCasa-style SVGs (it renders them with cairosvg). We
feed a raster instead, mirroring its letterbox + ImageNet-normalize
preprocessing, then reuse its model builder, checkpoint loader and polygon
extractor unchanged.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import torch
import yaml

from . import WEIGHTS


class UNetSegmenter:
    def __init__(self, run_dir: Path = WEIGHTS / "floorplan-to-3d", device: str = "cpu"):
        from buildingcv.checkpoint import load_inference_checkpoint
        from buildingcv.model import build_model

        self.cfg = yaml.safe_load((run_dir / "config.yaml").read_text())
        self.H, self.W = self.cfg["data"]["image_size"]
        self.device = torch.device(device)
        self.model = build_model(encoder_name=self.cfg["model"]["encoder_name"], encoder_weights=None).to(self.device)
        state, self.epoch = load_inference_checkpoint(run_dir / "best.safetensors", self.device)
        self.model.load_state_dict(state)
        self.model.eval()

    @torch.no_grad()
    def predict(self, rgb: np.ndarray) -> np.ndarray:
        """Return a class mask (0 floor, 1 wall, 2 door, 3 window) at the input's resolution."""
        from buildingcv.data import IMAGENET_MEAN, IMAGENET_STD

        h, w = rgb.shape[:2]
        s = min(self.W / w, self.H / h)
        iw, ih = max(1, round(w * s)), max(1, round(h * s))
        small = cv2.resize(rgb, (iw, ih), interpolation=cv2.INTER_AREA)
        t = torch.from_numpy(small).permute(2, 0, 1).float().div(255.0)
        t = (t - torch.tensor(IMAGENET_MEAN).view(3, 1, 1)) / torch.tensor(IMAGENET_STD).view(3, 1, 1)
        canvas = torch.zeros(3, self.H, self.W)
        top, left = (self.H - ih) // 2, (self.W - iw) // 2
        canvas[:, top:top + ih, left:left + iw] = t
        logits = self.model(canvas.unsqueeze(0).to(self.device))
        mask = logits.argmax(1)[0, top:top + ih, left:left + iw].to(torch.uint8).cpu().numpy()
        return cv2.resize(mask, (w, h), interpolation=cv2.INTER_NEAREST)


def polygons(mask: np.ndarray) -> dict:
    from buildingcv.extract_polygons import mask_to_polygons

    return mask_to_polygons(mask)
