"""Build the Windows hand-off folder + zip from this repo (run on any OS).

    python3 make_windows_package.py [DEST_DIR] [ZIP_PATH]

Copies the app, tests, samples, logo, weights, vendor/cairo and third_party sources,
leaving out venvs, outputs, caches, git data and the 218 MB FloorplanToBlender3d demo GIFs.
"""
import os, shutil, sys, zipfile
from pathlib import Path

SRC = Path(__file__).resolve().parent
DEST = Path(sys.argv[1] if len(sys.argv) > 1 else "/workspace/house-plan-3d-windows")
ZIP = Path(sys.argv[2] if len(sys.argv) > 2 else "/workspace/House-Plan-3D-windows.zip")
TOP = "House-Plan-3D"  # folder name inside the zip

SKIP_NAMES = {".venv", "venv", "out", ".pytest_cache", "__pycache__", ".git", ".mypy_cache",
              ".ruff_cache", "node_modules", "micromamba"}
SKIP_REL = {"third_party/FloorplanToBlender3d/Images/Demos"}
SKIP_SUFFIX = {".pyc", ".pyo"}
INCLUDE = ["hp3d", "tests", "plans", "assets", "weights", "vendor", "third_party",
           "requirements.txt", "requirements.lock.txt", "README.md", "INTEGRATION.md", "WINDOWS.md",
           "open-app.bat", "setup-windows.bat", "setup-windows.ps1", "open-app.sh", "setup.sh",
           ".gitmodules", "make_windows_package.py"]


def keep(p: Path) -> bool:
    rel = p.relative_to(SRC).as_posix()
    return not (p.name in SKIP_NAMES or rel in SKIP_REL or p.suffix in SKIP_SUFFIX)


def copy(src: Path, dst: Path):
    if not keep(src):
        return
    if src.is_dir():
        dst.mkdir(parents=True, exist_ok=True)
        for c in sorted(src.iterdir()):
            copy(c, dst / c.name)
    else:
        shutil.copy2(src, dst)


if DEST.exists():
    shutil.rmtree(DEST)
DEST.mkdir(parents=True)
for name in INCLUDE:
    s = SRC / name
    if not s.exists():
        sys.exit(f"missing {s}")
    copy(s, DEST / name)
for req in ["vendor/cairo/bin/cairo.dll", "vendor/cairo/bin/libcairo-2.dll",
            "weights/floorplan-to-3d/best.safetensors", "weights/cubicasa5k/model_best_val_loss_var.pkl"]:
    assert (DEST / req).is_file(), req
assert not (DEST / ".venv").exists()

if ZIP.exists():
    ZIP.unlink()
with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as z:
    for root, dirs, files in os.walk(DEST):
        dirs.sort()
        for f in sorted(files):
            p = Path(root, f)
            z.write(p, f"{TOP}/{p.relative_to(DEST).as_posix()}")
print("folder:", DEST)
print("zip:", ZIP, f"{ZIP.stat().st_size / 2**20:.0f} MB")
