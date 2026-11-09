"""sys.path helpers for upstream repos that are not pip-installable packages."""
from __future__ import annotations

import contextlib
import sys
from pathlib import Path

from . import THIRD_PARTY


@contextlib.contextmanager
def on_path(repo: str, purge_prefixes: tuple[str, ...] = ()):
    """Temporarily put third_party/<repo> first on sys.path.

    Floorplan-Dimractor ships a top-level package literally named ``src``; we
    purge it from sys.modules afterwards so it cannot shadow anything else.
    """
    p = str(THIRD_PARTY / repo)
    sys.path.insert(0, p)
    try:
        yield Path(p)
    finally:
        with contextlib.suppress(ValueError):
            sys.path.remove(p)
        for name in list(sys.modules):
            if any(name == pre or name.startswith(pre + ".") for pre in purge_prefixes):
                del sys.modules[name]
