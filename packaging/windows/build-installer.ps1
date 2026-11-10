# Build a self-contained per-user Windows installer: House-Plan-3D-Setup.exe
#
# Layout inside the install folder (the parent of the hp3d package):
#   python\          CPython 3.11.9 with tkinter and requirements.lock.txt (CPU torch)
#   hp3d\            the app, launched the same way as open-app.bat: pythonw -m hp3d.app
#   third_party\     CubiCasa5k, FloorplanToBlender3d, Floorplan-Dimractor (not .git)
#   vendor\cairo\    conda-forge Cairo DLLs (same fetch as setup-windows.ps1)
#   weights\         floorplan-to-3d (Hugging Face) and CubiCasa (Google Drive)
#   plans\ assets\   samples and the underside logo
#
# A full CPython prefix is copied as files. It is relocatable (no venv, no
# absolute pyvenv.cfg). PyInstaller is a poor fit for torch + tkinter + Cairo.
# Inno Setup wraps the folder in one Setup.exe that does not ask for admin.
param()
$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $false

$Repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if (-not (Test-Path (Join-Path $Repo "requirements.lock.txt"))) {
  throw "could not find the repo root from $PSScriptRoot"
}

function Say($m) { Write-Host "==> $m" -ForegroundColor Cyan }

function Invoke-Native {
  param([Parameter(Mandatory = $true)][scriptblock]$Command, [Parameter(Mandatory = $true)][string]$What)
  & $Command
  if ($LASTEXITCODE -ne 0) { throw "$What failed (exit $LASTEXITCODE)" }
}

function Clear-RunnerJunk {
  if (-not $env:GITHUB_ACTIONS) { return }
  $ws = $env:GITHUB_WORKSPACE
  foreach ($t in @(
      $env:AGENT_TOOLSDIRECTORY,
      "C:\hostedtoolcache",
      "C:\Program Files (x86)\Android",
      "C:\Android"
    )) {
    if (-not $t) { continue }
    if ($ws -and $t.StartsWith($ws, [StringComparison]::OrdinalIgnoreCase)) { continue }
    if (Test-Path -LiteralPath $t) {
      Say "removing $t to free disk"
      & cmd.exe /c "rmdir /s /q `"$t`""
    }
  }
}

function Select-WorkRoot {
  Get-PSDrive -PSProvider FileSystem | ForEach-Object {
    Write-Host ("drive {0}: free {1:N1} GB" -f $_.Name, ($_.Free / 1GB))
  }
  $best = Get-PSDrive -PSProvider FileSystem |
    Where-Object { $_.Free -gt 15GB } |
    Sort-Object Free -Descending |
    Select-Object -First 1
  if (-not $best) { throw "need a drive with more than 15 GB free" }
  $root = Join-Path "$($best.Name):\" "hp3d"
  Say "work directory $root"
  return $root
}

function Get-File {
  param([string]$Url, [string]$Dest, [int64]$MinBytes = 1)
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Dest) | Out-Null
  if ((Test-Path -LiteralPath $Dest) -and ((Get-Item -LiteralPath $Dest).Length -ge $MinBytes)) {
    Say "already have $Dest"
    return
  }
  if (Test-Path -LiteralPath $Dest) { Remove-Item -LiteralPath $Dest -Force }
  Say "download $Url"
  & curl.exe -L --fail --retry 5 --retry-all-errors --retry-delay 5 -A "house-plan-3d-setup" -o $Dest $Url
  if ($LASTEXITCODE -ne 0) { throw "download failed: $Url" }
  $n = (Get-Item -LiteralPath $Dest).Length
  if ($n -lt $MinBytes) { throw "download too small ($n bytes): $Dest" }
}

function Copy-Robo {
  param([string]$Src, [string]$Dst, [string[]]$ExcludeDirs)
  New-Item -ItemType Directory -Force -Path $Dst | Out-Null
  # Submodule checkouts have a .git file, not a directory. /XD does not skip files.
  $robo = @($Src, $Dst, "/E", "/NFL", "/NDL", "/NJH", "/NJS", "/nc", "/ns", "/np", "/XF", ".git")
  if ($ExcludeDirs) { $robo += @("/XD") + $ExcludeDirs }
  & robocopy @robo
  if ($LASTEXITCODE -ge 8) { throw "robocopy $Src -> $Dst failed (exit $LASTEXITCODE)" }
}

Clear-RunnerJunk
try {
  New-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" -Name LongPathsEnabled -Value 1 -PropertyType DWORD -Force | Out-Null
} catch {
  Write-Host "could not enable long paths: $_"
}

$Work = Select-WorkRoot
$Payload = Join-Path $Work "payload"
$PyDir = Join-Path $Payload "python"
$Dist = Join-Path $Work "dist"
$PyExe = Join-Path $PyDir "python.exe"
New-Item -ItemType Directory -Force -Path $Payload, $Dist | Out-Null
if (-not $env:PIP_CACHE_DIR) { $env:PIP_CACHE_DIR = Join-Path $Work "pip-cache" }
New-Item -ItemType Directory -Force -Path $env:PIP_CACHE_DIR | Out-Null

foreach ($rel in @(
    "third_party\floorplan-to-3d\pyproject.toml",
    "third_party\CubiCasa5k\floortrans",
    "third_party\FloorplanToBlender3d\FloorplanToBlenderLib",
    "third_party\Floorplan-Dimractor\src"
  )) {
  if (-not (Test-Path (Join-Path $Repo $rel))) { throw "missing $rel (checkout submodules)" }
}

# ---- Python 3.11 with tcl/tk (not the embeddable zip: that zip has no tkinter) ----
$pySetup = Join-Path $Work "python-3.11.9-amd64.exe"
Get-File "https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe" $pySetup 20MB
if (-not (Test-Path $PyExe)) {
  Say "installing CPython 3.11.9 into $PyDir"
  $p = Start-Process -FilePath $pySetup -Wait -PassThru -ArgumentList @(
    "/quiet", "InstallAllUsers=0", "Include_launcher=0", "Include_test=0",
    "Include_doc=0", "Include_dev=0", "Include_debug=0", "Include_symbols=0",
    "Include_pip=1", "Include_tcltk=1", "Shortcuts=0", "AssociateFiles=0",
    "PrependPath=0", "CompileAll=0", "TargetDir=$PyDir"
  )
  if ($p.ExitCode -notin 0, 3010) { throw "Python installer exit $($p.ExitCode)" }
}
$ver = (& $PyExe -c "import sys; print('%d.%d' % sys.version_info[:2])").Trim()
if ($ver -ne "3.11") { throw "expected Python 3.11, got $ver" }
Invoke-Native { & $PyExe -c "import tkinter; print('tkinter', tkinter.TkVersion)" } "tkinter import"
Say "using $PyExe"

# ---- packages: same lock and CPU index as setup-windows.ps1 ----
Say "installing requirements.lock.txt (CPU torch)"
Invoke-Native { & $PyExe -m pip install --upgrade pip } "pip upgrade"
Invoke-Native {
  & $PyExe -m pip install -r (Join-Path $Repo "requirements.lock.txt") --extra-index-url https://download.pytorch.org/whl/cpu
} "pip install requirements.lock.txt"
# Non-editable: an editable install would point at this build machine.
Invoke-Native { & $PyExe -m pip install (Join-Path $Repo "third_party\floorplan-to-3d") --no-deps } "pip install buildingcv"

# site-packages\..\..\.. is the folder that contains both python\ and hp3d\
$pth = Join-Path $PyDir "Lib\site-packages\hp3d-app.pth"
Set-Content -Path $pth -Encoding ascii -Value "..\..\.."

# ---- Cairo, same tool as setup-windows.ps1 ----
Say "fetching Cairo with micromamba"
$mm = Join-Path $Work "micromamba.exe"
Get-File "https://github.com/mamba-org/micromamba-releases/releases/latest/download/micromamba-win-64" $mm 1MB
$env:MAMBA_ROOT_PREFIX = Join-Path $Work "mamba-root"
$env:CONDA_PKGS_DIRS = Join-Path $Work "conda-pkgs"
$cairo = Join-Path $Payload "vendor\cairo"
New-Item -ItemType Directory -Force -Path (Split-Path $cairo) | Out-Null
Invoke-Native { & $mm create -y -p $cairo -c conda-forge cairo } "micromamba create cairo"
$binList = @()
foreach ($dllName in @("cairo.dll", "libcairo-2.dll")) {
  $binList += Get-ChildItem -Path $cairo -Recurse -Filter $dllName -File -ErrorAction SilentlyContinue |
    ForEach-Object { $_.DirectoryName }
}
$bins = @($binList | Select-Object -Unique)
if (-not $bins) { throw "cairo.dll not found under $cairo" }
foreach ($b in $bins) {
  $cairoDll = Join-Path $b "cairo.dll"
  $libDll = Join-Path $b "libcairo-2.dll"
  if ((Test-Path $cairoDll) -and -not (Test-Path $libDll)) { Copy-Item $cairoDll $libDll }
  if ((Test-Path $libDll) -and -not (Test-Path $cairoDll)) { Copy-Item $libDll $cairoDll }
  Say "Cairo DLLs in $b"
}

# ---- weights (not committed; downloaded the same way as setup.sh) ----
Say "downloading detection weights"
$wRoot = Join-Path $Payload "weights"
$unet = Join-Path $wRoot "floorplan-to-3d"
$cc = Join-Path $wRoot "cubicasa5k"
New-Item -ItemType Directory -Force -Path $unet, $cc | Out-Null
Get-File "https://huggingface.co/Yytsi/floorplan-to-3d-walls/resolve/main/best.safetensors" (Join-Path $unet "best.safetensors") 50MB
Get-File "https://huggingface.co/Yytsi/floorplan-to-3d-walls/resolve/main/config.yaml" (Join-Path $unet "config.yaml") 20
$cfg = Get-Content -Raw (Join-Path $unet "config.yaml")
if ($cfg -notmatch "encoder_name" -or $cfg -notmatch "image_size") { throw "floorplan-to-3d config.yaml does not look like the model config" }
$pkl = Join-Path $cc "model_best_val_loss_var.pkl"
$gotPkl = $false
for ($i = 1; $i -le 4; $i++) {
  if (Test-Path $pkl) { Remove-Item $pkl -Force }
  Say "CubiCasa weights attempt $i"
  & $PyExe -m gdown 1gRB7ez1e4H7a9Y09lLqRuna0luZO5VRK -O $pkl
  if ($LASTEXITCODE -eq 0 -and (Test-Path $pkl) -and ((Get-Item $pkl).Length -gt 50MB)) { $gotPkl = $true; break }
  if (Test-Path $pkl) { Remove-Item $pkl -Force }
  & $PyExe -m gdown --fuzzy "https://drive.google.com/uc?id=1gRB7ez1e4H7a9Y09lLqRuna0luZO5VRK" -O $pkl
  if ($LASTEXITCODE -eq 0 -and (Test-Path $pkl) -and ((Get-Item $pkl).Length -gt 50MB)) { $gotPkl = $true; break }
  Start-Sleep -Seconds (15 * $i)
}
if (-not $gotPkl) { throw "CubiCasa weights download failed (model_best_val_loss_var.pkl)" }

# ---- app files ----
Say "copying the app"
$skipDirs = @(".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache")
Copy-Robo (Join-Path $Repo "hp3d") (Join-Path $Payload "hp3d") $skipDirs
Copy-Robo (Join-Path $Repo "plans") (Join-Path $Payload "plans") $skipDirs
Copy-Robo (Join-Path $Repo "assets") (Join-Path $Payload "assets") $skipDirs
$demos = Join-Path $Repo "third_party\FloorplanToBlender3d\Images\Demos"
Copy-Robo (Join-Path $Repo "third_party") (Join-Path $Payload "third_party") ($skipDirs + @($demos))
foreach ($name in @("README.md", "INTEGRATION.md", "WINDOWS.md", "requirements.txt", "requirements.lock.txt")) {
  Copy-Item (Join-Path $Repo $name) (Join-Path $Payload $name)
}
foreach ($must in @(
    "plans\sample_house_mm.stl",
    "assets\logo\8K-by-AK-relief-80mm.stl",
    "assets\logo\8K-only-relief-50mm.stl",
    "python\pythonw.exe"
  )) {
  if (-not (Test-Path (Join-Path $Payload $must))) { throw "payload missing $must" }
}
if (Test-Path (Join-Path $Payload "third_party\CubiCasa5k\.git")) { throw "payload contains a third_party .git directory" }
if (-not (Test-Path (Join-Path $Payload "weights\floorplan-to-3d\best.safetensors"))) { throw "weights were not staged in the payload" }

Say "preflight import from the payload (cwd is not the app folder)"
Push-Location $env:TEMP
try {
  & $PyExe -c @"
import pathlib
import hp3d, tkinter, torch, trimesh, fitz, cv2, buildingcv, cairosvg
cairosvg.svg2png(bytestring=b'<svg xmlns="http://www.w3.org/2000/svg" width="4" height="4"><rect width="4" height="4"/></svg>')
root = pathlib.Path(r'$Payload').resolve()
here = pathlib.Path(hp3d.__file__).resolve()
if root not in here.parents:
    raise SystemExit('hp3d loaded from %s, expected under %s' % (here, root))
print('preflight ok', here)
"@
  if ($LASTEXITCODE -ne 0) { throw "preflight import failed" }
} finally {
  Pop-Location
}

Say "removing __pycache__ from the payload"
Get-ChildItem -Path $Payload -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
  ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction SilentlyContinue }
Get-ChildItem -Path $Payload -Recurse -Include *.pyc, *.pyo -File -ErrorAction SilentlyContinue |
  Remove-Item -Force -ErrorAction SilentlyContinue

$payloadBytes = (Get-ChildItem -Path $Payload -Recurse -File | Measure-Object -Property Length -Sum).Sum
Say ("payload {0:N1} MB" -f ($payloadBytes / 1MB))

# ---- Inno Setup ----
$innoDir = Join-Path $Work "innosetup"
$iscc = Join-Path $innoDir "ISCC.exe"
if (-not (Test-Path $iscc)) {
  $innoSetup = Join-Path $Work "innosetup-7.1.1-x64.exe"
  Get-File "https://github.com/jrsoftware/issrc/releases/download/is-7_1_1/innosetup-7.1.1-x64.exe" $innoSetup 5MB
  Say "installing Inno Setup"
  $p = Start-Process -FilePath $innoSetup -Wait -PassThru -ArgumentList @("/VERYSILENT", "/NORESTART", "/DIR=$innoDir")
  if ($p.ExitCode -notin 0, 3010) { throw "Inno Setup installer exit $($p.ExitCode)" }
}
if (-not (Test-Path $iscc)) {
  $found = Get-ChildItem -Path "C:\Program Files", "C:\Program Files (x86)", $Work -Filter ISCC.exe -Recurse -ErrorAction SilentlyContinue |
    Select-Object -First 1
  if (-not $found) { throw "ISCC.exe not found" }
  $iscc = $found.FullName
}
Say "compiling installer with $iscc"
$defs = Join-Path $PSScriptRoot "build-defines.iss"
@(
  "#define Payload `"$($Payload.Replace('\', '\\'))`""
  "#define OutputDir `"$($Dist.Replace('\', '\\'))`""
) | Set-Content -Path $defs -Encoding ascii
Invoke-Native { & $iscc (Join-Path $PSScriptRoot "house-plan-3d.iss") } "ISCC"
$exe = Join-Path $Dist "House-Plan-3D-Setup.exe"
if (-not (Test-Path $exe)) { throw "installer was not written to $exe" }
$n = (Get-Item $exe).Length
Say ("installer {0} bytes ({1:N1} MB)" -f $n, ($n / 1MB))
if ($env:GITHUB_STEP_SUMMARY) {
  @"
### Installer
- File: ``$exe``
- Size: $n bytes ($([math]::Round($n / 1MB, 1)) MB)
- Payload: $([math]::Round($payloadBytes / 1MB, 1)) MB
"@ | Add-Content -Path $env:GITHUB_STEP_SUMMARY
}
if ($env:GITHUB_OUTPUT) {
  "installer=$exe" | Add-Content -Path $env:GITHUB_OUTPUT
}
Write-Host "INSTALLER $exe"
