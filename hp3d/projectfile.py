"""Save and reopen a House Plan 3D project as one .hp3d file.

The file is a zip of the job folder (parameters, polygons, the cropped plan,
previews, and meshes). Names inside the zip are plain file names, so a project
saved on one computer opens on another.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import zipfile
from pathlib import Path

FORMAT = 1
SUFFIXES = {".json", ".png", ".txt", ".stl", ".3mf", ".glb", ".gltf"}
REQUIRED = ("job.json", "polygons.json")
MAX_ENTRY_BYTES = 512 * 1024 * 1024


def _dest_path(dest: Path) -> Path:
    dest = Path(dest)
    if dest.suffix.lower() == ".hp3d":
        return dest
    return dest.parent / f"{dest.name}.hp3d"


def _entry_name(filename: str) -> str:
    if not filename or filename.startswith("/") or filename.startswith("\\"):
        raise ValueError("That project file contains an unsafe path, so I didn't open it.")
    parts = filename.replace("\\", "/").split("/")
    if any(part == ".." for part in parts):
        raise ValueError("That project file contains an unsafe path, so I didn't open it.")
    name = parts[-1]
    if not name or name in (".", ".."):
        raise ValueError("That project file contains an unsafe path, so I didn't open it.")
    return name


def save_project(job: Path, dest: Path) -> Path:
    """Write the job folder to a .hp3d file and return the path that was written."""
    job = Path(job)
    if not (job / "job.json").is_file() or not (job / "polygons.json").is_file():
        raise ValueError("This project isn't ready to save. Build the model first.")
    dest = _dest_path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    try:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("format.json", json.dumps({"format": FORMAT, "kind": "hp3d"}) + "\n")
            for path in sorted(job.iterdir()):
                if path.is_file() and path.suffix.lower() in SUFFIXES and path.name != "format.json":
                    archive.write(path, path.name)
        tmp.replace(dest)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    return dest


def load_project(src: Path, job: Path) -> None:
    """Replace the job folder with the contents of a .hp3d file.

    The job folder is left alone when the file is not a project, so a bad
    open does not wipe the model that is already on screen.
    """
    src = Path(src)
    job = Path(job)
    if not src.is_file():
        raise ValueError(f"I can't find the project file {src.name}.")
    try:
        archive = zipfile.ZipFile(src)
    except zipfile.BadZipFile as exc:
        raise ValueError(f"{src.name} isn't a House Plan 3D project (.hp3d).") from exc
    with archive:
        chosen: list[tuple[zipfile.ZipInfo, str]] = []
        for info in archive.infolist():
            if info.is_dir():
                continue
            name = _entry_name(info.filename)
            if name != "format.json" and Path(name).suffix.lower() not in SUFFIXES:
                continue
            if info.file_size > MAX_ENTRY_BYTES:
                raise ValueError(f"{src.name} has a file that's too large to open.")
            chosen.append((info, name))
        got = {name for _, name in chosen}
        missing = [name for name in REQUIRED if name not in got]
        if missing:
            raise ValueError(
                f"{src.name} is missing {', '.join(missing)}, so it isn't a complete project."
            )
        staging = Path(tempfile.mkdtemp(prefix="hp3d-open-"))
        try:
            for info, name in chosen:
                if name == "format.json":
                    continue
                target = staging / name
                with archive.open(info) as incoming, open(target, "wb") as out:
                    shutil.copyfileobj(incoming, out)
            _check_json_object(staging / "job.json", src.name)
            _check_json_object(staging / "polygons.json", src.name)
            job.mkdir(parents=True, exist_ok=True)
            for path in list(job.iterdir()):
                if path.is_file() and (path.suffix.lower() in SUFFIXES or path.name == "format.json"):
                    path.unlink()
            for path in staging.iterdir():
                shutil.copy2(path, job / path.name)
        finally:
            shutil.rmtree(staging, ignore_errors=True)


def _check_json_object(path: Path, label: str) -> None:
    try:
        data = json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} isn't a House Plan 3D project (.hp3d).") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{label} isn't a House Plan 3D project (.hp3d).")
