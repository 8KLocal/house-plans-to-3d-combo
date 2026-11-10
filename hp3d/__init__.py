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


def _windows_cairo() -> None:
    """On Windows, point cairocffi/cairosvg at the bundled Cairo DLLs.

    setup-windows.bat leaves them in vendor/cairo/bin (copied from conda-forge
    via micromamba). Must run before anything imports cairocffi, which is why
    it lives here: every entry point imports the hp3d package first.
    Set HP3D_CAIRO_DIR to use a different folder that holds cairo.dll.
    """
    import os
    if os.name != "nt":
        return
    cands = [os.environ.get("HP3D_CAIRO_DIR", ""),
             str(ROOT / "vendor" / "cairo" / "bin"),
             str(ROOT / "vendor" / "cairo" / "Library" / "bin")]
    for d in cands:
        if not d or not (Path(d, "cairo.dll").is_file() or Path(d, "libcairo-2.dll").is_file()):
            continue
        os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
        prev = os.environ.get("CAIROCFFI_DLL_DIRECTORIES")
        os.environ["CAIROCFFI_DLL_DIRECTORIES"] = d + (";" + prev if prev else "")
        try:
            os.add_dll_directory(d)
        except (AttributeError, OSError):
            pass
        fonts = Path(d).parent / "etc" / "fonts"
        if fonts.is_dir():
            os.environ.setdefault("FONTCONFIG_PATH", str(fonts))
        return


_windows_cairo()
