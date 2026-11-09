"""hp3d: thin integration layer wiring five third-party projects into one
plans -> segmentation -> dimensions -> 3D -> repaired export pipeline.

Each stage lives in its own module and imports its upstream project lazily,
so a missing optional dependency (e.g. CubiCasa weights) only disables that
stage instead of breaking the whole app.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
THIRD_PARTY = ROOT / "third_party"
WEIGHTS = ROOT / "weights"
