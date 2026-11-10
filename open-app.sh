#!/usr/bin/env bash
# Open the House plans -> 3D desktop app (with in-app model editor) using the repo's venv.
cd "$(dirname "$0")" && exec ./.venv/bin/python -m hp3d.app "$@"
