"""One command for any supported file: picture, PDF blueprint or 3D model.

    python -m hp3d.ingest plans/living.png --out out/living
    python -m hp3d.ingest house.stl --out out/house --answer size=mm --answer doors=yes --answer floors=yes

Same steps as the app: read the file (detection for plans, slicing for 3D
files), build the 3D model with the underside logo, then list the open
questions. ``--answer id=value`` answers one (repeatable), ``--skip-rest``
skips the others so their guesses stay flagged. Exit code 2 = file type not
readable (message printed, nothing crashes).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import editor as E
from . import quiz
from .raster import UnsupportedFile, file_kind


def ingest(path: Path, out: Path, answers: dict | None = None, skip_rest: bool = False,
           px_per_inch: float | None = None, log=print) -> dict:
    kind = file_kind(path)                      # raises UnsupportedFile with a plain message
    p = E.JobParams.load(out) if (out / "job.json").exists() else E.JobParams()
    if p.plan != str(path):
        p = E.JobParams(plan=str(path))
    if px_per_inch:
        p.set("px_per_inch", px_per_inch)
    log(f"{path.name}: {kind}")
    if E.needs_detection(out, p):
        E.detect(out, p, log=log)
    E.rebuild(out, p, log=log)
    for qid, val in (answers or {}).items():
        msg, _ = quiz.answer(out, p, qid, val)
        log(f"answer {qid}={val}: {msg}")
    if skip_rest:
        for q in quiz.pending(out, p):
            log(quiz.skip(out, p, q["id"]))
    return E.rebuild(out, p, log=log)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", type=Path)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--px-per-inch", type=float, default=None)
    ap.add_argument("--answer", action="append", default=[], help="question=value, e.g. size=32 ft, doors=yes")
    ap.add_argument("--skip-rest", action="store_true")
    a = ap.parse_args()
    out = a.out or Path("out") / a.file.stem
    answers = dict(x.split("=", 1) for x in a.answer)
    try:
        rep = ingest(a.file, out, answers, a.skip_rest, a.px_per_inch)
    except UnsupportedFile as e:
        print(f"Can't use this file: {e}")
        sys.exit(2)
    except RuntimeError as e:
        print(f"Read the file but couldn't build a model: {e}")
        sys.exit(3)
    print("review:", rep["review"]["status"])
    p = E.JobParams.load(out)
    for q in quiz.pending(out, p):
        print(f"\n[{q['id']}] {q['text']}")
    print("\nassumptions:\n  " + "\n  ".join(rep["assumptions"]))
    print("report:", out / "report.json")


if __name__ == "__main__":
    main()
