# One-time setup for House Plan 3D on Windows (no admin needed).
#  1. finds Python 3.11
#  2. creates .venv in this folder (replaces a copied Linux/macOS venv if it finds one)
#  3. installs the exact tested packages from requirements.lock.txt (CPU-only torch)
#  4. installs third_party\floorplan-to-3d (the "buildingcv" module) with --no-deps
#  5. makes sure Cairo (for SVG/PDF plans) is in vendor\cairo; downloads it with micromamba only if missing
#  6. checks the detection weights and runs a quick import test
# Safe to run again; it skips what is already done. Use -Force to rebuild .venv from scratch.
param([switch]$Force)
$ErrorActionPreference = "Continue"   # we check $LASTEXITCODE after every step
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root
function Say($m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Fail($m) { Write-Host ""; Write-Host "SETUP STOPPED: $m" -ForegroundColor Red; exit 1 }

# Files from a downloaded zip carry a "blocked" mark; clear it so scripts and DLLs load.
try { Get-ChildItem -Path $Root -Recurse -File -ErrorAction SilentlyContinue | Unblock-File -ErrorAction SilentlyContinue } catch {}

# ---- 1. Python 3.11 --------------------------------------------------------
function Test-Py311($exe, $pre) {
  try {
    $v = & $exe @pre -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
    return ($LASTEXITCODE -eq 0 -and "$v".Trim() -eq "3.11")
  } catch { return $false }
}
$Py = $null; $PyPre = @()
$cands = @(
  @{exe = "py"; pre = @("-3.11")},
  @{exe = "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe"; pre = @()},
  @{exe = "C:\Program Files\Python311\python.exe"; pre = @()},
  @{exe = "python"; pre = @()},
  @{exe = "python3.11"; pre = @()}
)
foreach ($c in $cands) {
  if ((Get-Command $c.exe -ErrorAction SilentlyContinue) -and (Test-Py311 $c.exe $c.pre)) { $Py = $c.exe; $PyPre = $c.pre; break }
}
if (-not $Py) {
  Fail ("Python 3.11 was not found.`n" +
        "  Install it from https://www.python.org/downloads/release/python-3119/ (Windows installer 64-bit),`n" +
        "  tick 'Add python.exe to PATH', keep 'tcl/tk' selected, then run setup-windows.bat again.`n" +
        "  (Or, in a terminal:  winget install -e --id Python.Python.3.11 --scope user )")
}
Say "Using Python 3.11: $Py $PyPre"

# ---- 2. .venv ---------------------------------------------------------------
$VPy = Join-Path $Root ".venv\Scripts\python.exe"
if (Test-Path (Join-Path $Root ".venv")) {
  $bad = $Force -or (Test-Path (Join-Path $Root ".venv\bin")) -or -not (Test-Path $VPy) -or -not (Test-Py311 $VPy @())
  if ($bad) { Say "Removing old/incompatible .venv"; Remove-Item -Recurse -Force (Join-Path $Root ".venv") }
}
if (-not (Test-Path $VPy)) {
  Say "Creating .venv"
  & $Py @PyPre -m venv (Join-Path $Root ".venv")
  if ($LASTEXITCODE -ne 0) { Fail "could not create .venv" }
}
& $VPy -c "import tkinter" 2>$null
if ($LASTEXITCODE -ne 0) { Fail "this Python has no tkinter. Re-run the Python 3.11 installer, choose Modify, and tick 'tcl/tk and IDLE'." }

# ---- 3-4. packages ------------------------------------------------------------
Say "Installing packages (first time downloads about 1 GB; takes a while)"
& $VPy -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { Fail "pip upgrade failed (check the internet connection)" }
& $VPy -m pip install -r (Join-Path $Root "requirements.lock.txt") --extra-index-url https://download.pytorch.org/whl/cpu
if ($LASTEXITCODE -ne 0) { Fail "installing requirements.lock.txt failed (see the pip messages above)" }
& $VPy -m pip install -e (Join-Path $Root "third_party\floorplan-to-3d") --no-deps
if ($LASTEXITCODE -ne 0) { Fail "installing third_party\floorplan-to-3d failed" }

# ---- 5. Cairo -----------------------------------------------------------------
$CairoBin = Join-Path $Root "vendor\cairo\bin"
$CairoAlt = Join-Path $Root "vendor\cairo\Library\bin"
if ((Test-Path (Join-Path $CairoBin "cairo.dll")) -or (Test-Path (Join-Path $CairoAlt "cairo.dll"))) {
  Say "Cairo found in vendor\cairo"
} else {
  Say "Cairo not bundled; downloading it with micromamba (conda-forge, no admin)"
  $mmDir = Join-Path $Root "vendor\micromamba"
  New-Item -ItemType Directory -Force $mmDir | Out-Null
  $mm = Join-Path $mmDir "micromamba.exe"
  if (-not (Test-Path $mm)) {
    $tmp = Join-Path $env:TEMP "micromamba-win64.exe"
    try {
      [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
      Invoke-WebRequest -UseBasicParsing "https://github.com/mamba-org/micromamba-releases/releases/latest/download/micromamba-win-64" -OutFile $tmp -ErrorAction Stop
      Move-Item -Force $tmp $mm
    } catch { Write-Host "WARNING: could not download micromamba: $_" -ForegroundColor Yellow }
  }
  $env:MAMBA_ROOT_PREFIX = Join-Path $mmDir "root"
  if (Test-Path $mm) { & $mm create -y -p (Join-Path $Root "vendor\cairo") -c conda-forge cairo
  } else { $global:LASTEXITCODE = 1 }
  if ($LASTEXITCODE -ne 0) { Write-Host "WARNING: Cairo download failed. Wall detection on pictures and PDFs needs Cairo, so those will fail; DXF and 3D files (STL, OBJ, 3MF...) still work." -ForegroundColor Yellow }
  elseif (-not (Test-Path (Join-Path $CairoAlt "libcairo-2.dll"))) { Copy-Item (Join-Path $CairoAlt "cairo.dll") (Join-Path $CairoAlt "libcairo-2.dll") }
}

# ---- 6. weights + smoke check ---------------------------------------------------
$w1 = Join-Path $Root "weights\floorplan-to-3d\best.safetensors"
$w2 = Join-Path $Root "weights\cubicasa5k\model_best_val_loss_var.pkl"
if (-not (Test-Path $w1)) { Write-Host "WARNING: missing $w1 - wall detection on pictures/PDFs will not work." -ForegroundColor Yellow }
if (-not (Test-Path $w2)) { Write-Host "WARNING: missing $w2 - room/furniture recognition will be skipped." -ForegroundColor Yellow }

Say "Checking the install"
& $VPy -c "import hp3d, tkinter, torch, trimesh, fitz, cv2, buildingcv; print('core OK, torch', torch.__version__)"
if ($LASTEXITCODE -ne 0) { Fail "the quick import check failed (see above)" }
$chk = Join-Path $env:TEMP "hp3d_cairo_check.py"
Set-Content -Path $chk -Encoding ASCII -Value @'
import hp3d, cairosvg
cairosvg.svg2png(bytestring=b'<svg xmlns="http://www.w3.org/2000/svg" width="4" height="4"><rect width="4" height="4"/></svg>')
print("Cairo OK")
'@
& $VPy $chk
if ($LASTEXITCODE -ne 0) { Write-Host "WARNING: Cairo check failed. Wall detection on pictures and PDFs needs Cairo, so those will fail; DXF and 3D files (STL, OBJ, 3MF...) still work." -ForegroundColor Yellow }

Write-Host ""
Write-Host "Setup finished. Double-click open-app.bat to start House Plan 3D." -ForegroundColor Green
exit 0
