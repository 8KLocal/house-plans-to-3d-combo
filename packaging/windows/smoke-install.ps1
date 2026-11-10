# Silently install House-Plan-3D-Setup.exe and run a headless smoke test
# against that install (not the build tree).
param([Parameter(Mandatory = $true)][string]$Installer)
$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $false

function Say($m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Fail($m) { throw $m }

if (-not (Test-Path -LiteralPath $Installer)) { Fail "installer not found: $Installer" }
$Installer = (Resolve-Path -LiteralPath $Installer).Path
Say "installer $Installer ($((Get-Item $Installer).Length) bytes)"

$log = Join-Path $env:TEMP "house-plan-3d-setup.log"
if (Test-Path $log) { Remove-Item $log -Force }
Say "silent per-user install"
$p = Start-Process -FilePath $Installer -Wait -PassThru -ArgumentList @(
  "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/LOG=$log"
)
if ($p.ExitCode -ne 0) {
  if (Test-Path $log) { Get-Content $log -Tail 80 | Write-Host }
  Fail "installer exit $($p.ExitCode)"
}

$app = Join-Path $env:LOCALAPPDATA "House Plan 3D"
$py = Join-Path $app "python\python.exe"
$pyw = Join-Path $app "python\pythonw.exe"
if (-not (Test-Path $py)) { Fail "installed python missing: $py" }
if (-not (Test-Path $pyw)) { Fail "installed pythonw missing: $pyw" }
Say "installed to $app"

function Assert-Shortcut($path) {
  if (-not (Test-Path -LiteralPath $path)) {
    Write-Host "shortcuts found:"
    Get-ChildItem -Path $env:USERPROFILE, $env:APPDATA -Filter "*.lnk" -Recurse -ErrorAction SilentlyContinue |
      Where-Object { $_.Name -like "*House*" } |
      ForEach-Object { Write-Host $_.FullName }
    Fail "missing shortcut $path"
  }
  $sh = New-Object -ComObject WScript.Shell
  $s = $sh.CreateShortcut($path)
  Say "shortcut $path -> $($s.TargetPath) $($s.Arguments) wd=$($s.WorkingDirectory)"
  if ($s.TargetPath -notmatch "pythonw\.exe$") { Fail "shortcut does not use pythonw.exe: $($s.TargetPath)" }
  if ($s.Arguments -notmatch "-m hp3d\.app") { Fail "shortcut does not launch -m hp3d.app: $($s.Arguments)" }
  if (-not $s.WorkingDirectory) { Fail "shortcut has no working directory: $path" }
  $wd = (Resolve-Path -LiteralPath $s.WorkingDirectory.TrimEnd("\")).Path.TrimEnd("\")
  $root = (Resolve-Path -LiteralPath $app).Path.TrimEnd("\")
  if ($wd -ne $root) { Fail "shortcut working directory is '$wd', expected '$root'" }
}

$desktop = [Environment]::GetFolderPath("Desktop")
Assert-Shortcut (Join-Path $desktop "House Plan 3D.lnk")
Assert-Shortcut (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\House Plan 3D.lnk")

Say "import the GUI module with the console interpreter"
Push-Location $env:TEMP
try {
  & $py -s -c @"
import pathlib
import hp3d.app
root = pathlib.Path(r'$app').resolve()
here = pathlib.Path(hp3d.app.__file__).resolve()
if root not in here.parents:
    raise SystemExit('gui imported from %s, expected under %s' % (here, root))
print('gui import ok', here)
"@
  if ($LASTEXITCODE -ne 0) { Fail "GUI import failed" }

  Say "import the GUI module with pythonw (no console)"
  # pythonw has no console, and Start-Process splits "-c <code with spaces>".
  # The script writes a marker file so a failure is visible in the log.
  $marker = Join-Path $env:TEMP "hp3d-pythonw.txt"
  $script = Join-Path $env:TEMP "hp3d_pythonw_import.py"
  if (Test-Path $marker) { Remove-Item $marker -Force }
  @"
import traceback
path = r'$marker'
try:
    import hp3d.app
    open(path, 'w', encoding='utf-8').write('ok ' + hp3d.app.__file__)
except Exception:
    open(path, 'w', encoding='utf-8').write(traceback.format_exc())
    raise
"@ | Set-Content -Path $script -Encoding ascii
  $proc = Start-Process -FilePath $pyw -WorkingDirectory $env:TEMP -PassThru -WindowStyle Hidden -ArgumentList @("-s", $script)
  if (-not $proc.WaitForExit(120000)) {
    try { $proc.Kill() } catch {}
    Fail "pythonw import timed out"
  }
  $markerText = if (Test-Path $marker) { Get-Content -Raw $marker } else { "" }
  if ($proc.ExitCode -ne 0 -or $markerText -notlike "ok *") {
    Write-Host $markerText
    Fail "pythonw import failed (exit $($proc.ExitCode))"
  }
  Say ($markerText.Trim())

  Say "core libraries and Cairo"
  & $py -s -c @"
import hp3d, tkinter, torch, trimesh, fitz, cv2, buildingcv, cairosvg
cairosvg.svg2png(bytestring=b'<svg xmlns="http://www.w3.org/2000/svg" width="4" height="4"><rect width="4" height="4"/></svg>')
print('core ok', torch.__version__)
"@
  if ($LASTEXITCODE -ne 0) { Fail "core/Cairo import failed" }

  Say "load floorplan-to-3d weights"
  & $py -s -c "from hp3d.segment_unet import UNetSegmenter; UNetSegmenter(); print('unet weights ok')"
  if ($LASTEXITCODE -ne 0) { Fail "UNet weight load failed" }

  Say "load CubiCasa weights"
  & $py -s -c "from hp3d.segment_cubicasa import CubiCasaSegmenter; CubiCasaSegmenter(); print('cubicasa weights ok')"
  if ($LASTEXITCODE -ne 0) { Fail "CubiCasa weight load failed" }

  $stl = Join-Path $app "plans\sample_house_mm.stl"
  $out = Join-Path $app "out\ci-smoke"
  if (Test-Path $out) { Remove-Item $out -Recurse -Force }
  Say "ingest sample mesh"
  & $py -s -m hp3d.ingest $stl --out $out --answer "size=20 ft" --answer "doors=yes" --skip-rest
  if ($LASTEXITCODE -ne 0) { Fail "hp3d.ingest exit $LASTEXITCODE" }
  & $py -s -c @"
import json, pathlib
out = pathlib.Path(r'$out')
rep = json.loads((out / 'report.json').read_text(encoding='utf-8'))
st = rep['stages']['editor_rebuild']
if st['status'] != 'ok':
    raise SystemExit('editor_rebuild %s' % st)
logo = st.get('logo') or {}
if logo.get('status') != 'ok':
    raise SystemExit('logo %s' % logo)
stl = out / 'model.stl'
if not stl.is_file() or stl.stat().st_size < 1000:
    raise SystemExit('model.stl missing or tiny')
for name in ('model.glb', 'model.3mf', 'preview.png'):
    if not (out / name).is_file():
        raise SystemExit('missing %s' % name)
print('ingest ok', logo.get('variant'), logo.get('width_mm'))
"@
  if ($LASTEXITCODE -ne 0) { Fail "ingest output check failed" }
} finally {
  Pop-Location
}

Say "SMOKE PASS"
if ($env:GITHUB_STEP_SUMMARY) {
  "### Smoke test`n`nPassed: silent install, shortcuts, ``hp3d.app`` import (python and pythonw), Cairo, both weight files, and ``hp3d.ingest`` on ``plans/sample_house_mm.stl``." | Add-Content -Path $env:GITHUB_STEP_SUMMARY
}
