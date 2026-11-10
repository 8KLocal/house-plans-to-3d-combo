"""Look for a newer GitHub release and start the Windows installer.

The startup check runs off the UI thread and treats any network problem as
"no update". Tests pass a stand-in for ``urlopen`` so nothing here contacts
the network.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from . import __version__

REPO = "8KLocal/house-plans-to-3d-combo"
LATEST_API = f"https://api.github.com/repos/{REPO}/releases/latest"
SETUP_NAME = "House-Plan-3D-Setup.exe"
SETUP_URL = f"https://github.com/{REPO}/releases/latest/download/{SETUP_NAME}"


def parse_version(text: str) -> tuple[int, ...] | None:
    """``v0.2.0`` or ``0.2`` -> a tuple of ints. Anything else is not a version."""
    raw = str(text).strip()
    if raw[:1] in ("v", "V"):
        raw = raw[1:]
    if not raw:
        return None
    parts = raw.split(".")
    if not all(part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)


def is_newer(remote: str, local: str | None = None) -> bool:
    """True only when ``remote`` is a version strictly after ``local``."""
    got = parse_version(remote)
    have = parse_version(local if local is not None else __version__)
    if got is None or have is None:
        return False
    width = max(len(got), len(have))
    got = got + (0,) * (width - len(got))
    have = have + (0,) * (width - len(have))
    return got > have


def settings_path() -> Path:
    """Outside the install folder, so an upgrade cannot replace this file."""
    if os.name == "nt":
        root = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    return root / "House Plan 3D" / "settings.json"


def load_settings() -> dict:
    """Auto-check is on unless the user turned it off."""
    defaults = {"auto_check_updates": True}
    try:
        data = json.loads(settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return dict(defaults)
    if not isinstance(data, dict):
        return dict(defaults)
    out = dict(defaults)
    if "auto_check_updates" in data:
        out["auto_check_updates"] = bool(data["auto_check_updates"])
    return out


def save_settings(data: dict) -> None:
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"auto_check_updates": bool(data.get("auto_check_updates", True))}, indent=1),
                    encoding="utf-8")


def check_latest(opener=None, timeout: float = 8) -> dict:
    """Ask GitHub which release is latest. Never raises.

    ``status`` is ``newer``, ``current``, or ``offline`` (offline covers
    timeouts, DNS failure, and a response that is not a version).
    """
    local = __version__
    offline = {"status": "offline", "remote": None, "local": local}
    try:
        req = Request(LATEST_API, headers={
            "User-Agent": "House-Plan-3D",
            "Accept": "application/vnd.github+json",
        })
        open_fn = opener or urlopen
        with open_fn(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (OSError, URLError, ValueError, TimeoutError, KeyError, TypeError):
        return offline
    if not isinstance(payload, dict):
        return offline
    tag = str(payload.get("tag_name") or "")
    if parse_version(tag) is None:
        return offline
    if is_newer(tag, local):
        return {"status": "newer", "remote": tag, "local": local, "url": SETUP_URL}
    return {"status": "current", "remote": tag, "local": local, "url": SETUP_URL}


def installer_destination() -> Path:
    return Path(tempfile.gettempdir()) / SETUP_NAME


def download_installer(dest: Path, opener=None, timeout: float = 600) -> Path:
    """Save ``House-Plan-3D-Setup.exe``. Raises if the download fails."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = Request(SETUP_URL, headers={"User-Agent": "House-Plan-3D"})
    open_fn = opener or urlopen
    with open_fn(req, timeout=timeout) as resp, dest.open("wb") as out:
        while True:
            chunk = resp.read(1024 * 256)
            if not chunk:
                break
            out.write(chunk)
    if dest.stat().st_size < 100_000:
        raise OSError("downloaded installer is too small")
    return dest


def launch_update(exe: Path) -> None:
    """Start the installer and return immediately so the app can quit.

    ``/UPDATE=1`` tells the installer to open House Plan 3D again when it
    finishes. ``/SILENT`` still shows progress. Job folders are not part of
    the installer's file list, so they stay.
    """
    if os.name != "nt":
        raise OSError("the House Plan 3D installer runs on Windows")
    subprocess.Popen(  # noqa: S603 - the path is the installer we just downloaded
        [str(exe), "/SILENT", "/CLOSEAPPLICATIONS", "/UPDATE=1"],
        close_fds=True,
    )
