"""Version compare and the offline update check. No network."""
import json
import os
from pathlib import Path
from urllib.error import URLError

from hp3d import __version__
from hp3d import update as U


def test_version_compare_ignores_a_leading_v():
    assert U.parse_version("v0.2.0") == (0, 2, 0)
    assert U.parse_version("0.2") == (0, 2)
    assert U.parse_version("nope") is None
    assert U.parse_version("") is None
    assert U.is_newer("v0.2.0", "0.1.9")
    assert U.is_newer("v0.10.0", "0.9.0")
    assert not U.is_newer("v0.2.0", "0.2.0")
    assert not U.is_newer("0.2", "0.2.0")
    assert not U.is_newer("0.1.0", "0.2.0")
    assert not U.is_newer("latest", "0.2.0")
    assert not U.is_newer("v0.2.0-beta", "0.2.0")


def test_offline_check_does_not_raise():
    def opener(req, timeout=0):
        raise URLError("offline")

    result = U.check_latest(opener=opener)
    assert result["status"] == "offline"
    assert result["remote"] is None
    assert result["local"] == __version__


def test_github_tag_is_newer_or_current_without_a_network():
    class Body:
        def __init__(self, tag):
            self.tag = tag

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return json.dumps({"tag_name": self.tag}).encode()

    def opener_for(tag):
        def opener(req, timeout=0):
            assert "releases/latest" in req.full_url
            return Body(tag)
        return opener

    newer = U.check_latest(opener=opener_for("v9.0.0"))
    assert newer["status"] == "newer" and newer["remote"] == "v9.0.0"
    same = U.check_latest(opener=opener_for("v" + __version__))
    assert same["status"] == "current"
    broken = U.check_latest(opener=opener_for("not-a-version"))
    assert broken["status"] == "offline"


def test_settings_default_on_and_live_outside_the_install(tmp_path, monkeypatch):
    if os.name == "nt":
        monkeypatch.setenv("APPDATA", str(tmp_path))
    else:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert U.load_settings()["auto_check_updates"] is True
    U.save_settings({"auto_check_updates": False})
    assert U.load_settings()["auto_check_updates"] is False
    path = U.settings_path()
    assert path.is_file()
    assert "out" not in path.parts
    assert path.suffix == ".json"


def test_installer_keeps_jobs_and_relaunches_only_on_update():
    text = Path("packaging/windows/house-plan-3d.iss").read_text(encoding="utf-8")
    assert "CloseApplications=yes" in text
    assert "RestartApplications=no" in text
    assert "skipifsilent" in text
    assert "{param:UPDATE|0}" in text
    assert "InstallDelete" not in text
    assert "out\\" in text
