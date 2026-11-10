# House Plan 3D on Windows

Runs on your own PC, no chat needed. 64-bit Windows 10 or 11. No admin rights needed.

## Download

https://github.com/8KLocal/house-plans-to-3d-combo/releases/latest/download/House-Plan-3D-Setup.exe

Double-click that file. It installs House Plan 3D for your user only (Python is
inside the installer) and opens from the **House Plan 3D** Desktop or Start menu
shortcut. You do not install Python, unzip a folder, or run a setup script.

The file is unsigned. If Windows SmartScreen says "Windows protected your PC",
click **More info → Run anyway**.

The sections below are the manual setup, for a source checkout instead of the installer.

## 1. Python 3.11 (one time)

Open a Command Prompt and type `py -3.11 --version`. If it prints `Python 3.11.x`, skip to step 2.

Otherwise install **Python 3.11** (3.11.9 is the tested one) from
https://www.python.org/downloads/release/python-3119/ → "Windows installer (64-bit)".
In the installer tick **Add python.exe to PATH** and keep **tcl/tk** selected (it is by default).
"Install for me only" is fine.

## 2. Unzip and set up (one time)

1. Right-click `House-Plan-3D-windows.zip` → **Extract All**, to a short path such as
   `C:\Users\<you>\Documents\house-plan-3d`. Don't run it from inside the zip.
2. Double-click **`setup-windows.bat`**. It makes a `.venv` folder in that folder and downloads
   the Python packages (about 1 GB, 10–20 minutes, needs internet this one time).
   It ends with "Setup finished". Running it again is safe; `setup-windows.bat -Force` rebuilds from scratch.

If Windows SmartScreen says "Windows protected your PC", click **More info → Run anyway**
(the scripts are unsigned).

## 3. Use it

Double-click **`open-app.bat`**. Pick a plan (picture, PDF, DXF, or STL/OBJ/3MF/GLB…), build,
then edit in plain language. Results go in the `out\` folder next to the app.

## What's already inside

- Detection weights (`weights\`, ~293 MB), so picture and PDF builds work offline.
- Cairo for Windows (`vendor\cairo\`, from conda-forge). The wall-detection code needs it
  for every picture/PDF build (and SVG). If it ever goes missing, `setup-windows.bat`
  downloads it again with micromamba.
- Sample plans in `plans\`, the 8K by AK logo in `assets\logo\`.

## Licences (short version)

Fine for your own private testing, but **not cleanly sellable as-is**: it bundles GPL-3.0
code (FloorplanToBlender3d, pymeshfix) and AGPL PyMuPDF, CubiCasa5k code and weights are
non-commercial only (CC BY-NC 4.0), and Floorplan-Dimractor has no licence at all.
Before selling, those parts need replacing or licensing.

## Troubleshooting

- "House Plan 3D is not set up on this computer yet" → run `setup-windows.bat`.
- "Python 3.11 was not found" → do step 1, then run setup again.
- "no tkinter" → re-run the Python installer → Modify → tick "tcl/tk and IDLE".
- Never copy a `.venv` folder from another computer (Linux/Mac venvs don't work on Windows);
  setup removes one if it finds it.
