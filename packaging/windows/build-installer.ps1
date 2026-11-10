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

function Say($m) { Write-Host ("[{0:HH:mm:ss}] ==> {1}" -f (Get-Date), $m) -ForegroundColor Cyan }

function Invoke-Timed {
  param(
    [Parameter(Mandatory = $true)][string]$File,
    [string[]]$ArgumentList = @(),
    [Parameter(Mandatory = $true)][string]$What,
    [Parameter(Mandatory = $true)][int]$Seconds,
    [int[]]$OkExit = @(0),
    [string]$WorkingDirectory = "",
    [switch]$Soft
  )
  Say "$What (limit ${Seconds}s)"
  $sp = @{ FilePath = $File; ArgumentList = $ArgumentList; PassThru = $true; NoNewWindow = $true }
  if ($WorkingDirectory) { $sp.WorkingDirectory = $WorkingDirectory }
  $p = Start-Process @sp
  if (-not $p.WaitForExit($Seconds * 1000)) {
    try { & taskkill.exe /F /T /PID $p.Id | Out-Null } catch {}
    if ($Soft) { Say "WARNING: $What timed out; continuing"; return 124 }
    throw "$What timed out after $Seconds seconds"
  }
  if ($p.ExitCode -notin $OkExit) {
    if ($Soft) { Say "WARNING: $What exit $($p.ExitCode); continuing"; return $p.ExitCode }
    throw "$What failed (exit $($p.ExitCode))"
  }
  return 0
}

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
  & curl.exe -L --fail --retry 5 --retry-all-errors --retry-delay 5 --connect-timeout 30 --max-time 600 -A "house-plan-3d-setup" -o $Dest $Url
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
  Invoke-Timed -File $pySetup -Seconds 600 -OkExit @(0, 3010) -What "install CPython 3.11.9" -ArgumentList @(
    "/quiet", "InstallAllUsers=0", "Include_launcher=0", "Include_test=0",
    "Include_doc=0", "Include_dev=0", "Include_debug=0", "Include_symbols=0",
    "Include_pip=1", "Include_tcltk=1", "Shortcuts=0", "AssociateFiles=0",
    "PrependPath=0", "CompileAll=0", "TargetDir=$PyDir"
  )
}
$checkPy = Join-Path $Work "check_py.py"
Set-Content -Path $checkPy -Encoding ascii -Value "import sys`r`nraise SystemExit(0 if sys.version_info[:2] == (3, 11) else 1)`r`n"
$checkTk = Join-Path $Work "check_tk.py"
Set-Content -Path $checkTk -Encoding ascii -Value "import tkinter`r`nprint('tkinter', tkinter.TkVersion)`r`n"
Invoke-Timed -File $PyExe -Seconds 60 -What "check Python 3.11" -ArgumentList @($checkPy)
Invoke-Timed -File $PyExe -Seconds 60 -What "check tkinter" -ArgumentList @($checkTk)
Say "using $PyExe"

# ---- packages: same lock and CPU index as setup-windows.ps1 ----
Invoke-Timed -File $PyExe -Seconds 300 -What "pip upgrade" -ArgumentList @("-m", "pip", "install", "--upgrade", "pip")
Invoke-Timed -File $PyExe -Seconds 1200 -What "pip install requirements.lock.txt" -ArgumentList @(
  "-m", "pip", "install", "-r", (Join-Path $Repo "requirements.lock.txt"),
  "--extra-index-url", "https://download.pytorch.org/whl/cpu"
)
# Non-editable: an editable install would point at this build machine.
Invoke-Timed -File $PyExe -Seconds 300 -What "pip install buildingcv" -ArgumentList @(
  "-m", "pip", "install", (Join-Path $Repo "third_party\floorplan-to-3d"), "--no-deps"
)

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
Invoke-Timed -File $mm -Seconds 600 -What "micromamba create cairo" -ArgumentList @("create", "-y", "-p", $cairo, "-c", "conda-forge", "cairo")
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
  $code = Invoke-Timed -File $PyExe -Seconds 300 -Soft -What "CubiCasa weights attempt $i" -ArgumentList @("-m", "gdown", "1gRB7ez1e4H7a9Y09lLqRuna0luZO5VRK", "-O", $pkl)
  if ($code -eq 0 -and (Test-Path $pkl) -and ((Get-Item $pkl).Length -gt 50MB)) { $gotPkl = $true; break }
  if (Test-Path $pkl) { Remove-Item $pkl -Force }
  $code = Invoke-Timed -File $PyExe -Seconds 300 -Soft -What "CubiCasa weights fuzzy attempt $i" -ArgumentList @("-m", "gdown", "--fuzzy", "https://drive.google.com/uc?id=1gRB7ez1e4H7a9Y09lLqRuna0luZO5VRK", "-O", $pkl)
  if ($code -eq 0 -and (Test-Path $pkl) -and ((Get-Item $pkl).Length -gt 50MB)) { $gotPkl = $true; break }
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

# A Cairo or torch import can sit forever on a missing DLL. Cap it and still compile:
# the release is published before the real smoke test.
$pre = Join-Path $Work "preflight.py"
@"
import pathlib
import hp3d, tkinter, torch, trimesh, fitz, cv2, buildingcv, cairosvg
cairosvg.svg2png(bytestring=b'<svg xmlns="http://www.w3.org/2000/svg" width="4" height="4"><rect width="4" height="4"/></svg>')
root = pathlib.Path(r'$Payload').resolve()
here = pathlib.Path(hp3d.__file__).resolve()
if root not in here.parents:
    raise SystemExit('hp3d loaded from %s, expected under %s' % (here, root))
print('preflight ok', here)
"@ | Set-Content -Path $pre -Encoding ascii
Invoke-Timed -File $PyExe -Seconds 180 -Soft -WorkingDirectory $env:TEMP -What "preflight import" -ArgumentList @($pre) | Out-Null

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
  Invoke-Timed -File $innoSetup -Seconds 300 -OkExit @(0, 3010) -What "install Inno Setup" -ArgumentList @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/DIR=$innoDir")
}
if (-not (Test-Path $iscc)) { throw "ISCC.exe not found at $iscc" }
$defs = Join-Path $PSScriptRoot "build-defines.iss"
@(
  "#define Payload `"$($Payload.Replace('\', '\\'))`""
  "#define OutputDir `"$($Dist.Replace('\', '\\'))`""
) | Set-Content -Path $defs -Encoding ascii
Invoke-Timed -File $iscc -Seconds 1200 -What "compile installer" -ArgumentList @((Join-Path $PSScriptRoot "house-plan-3d.iss"))
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
