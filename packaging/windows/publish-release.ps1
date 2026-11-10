# Publish House-Plan-3D-Setup.exe on the v0.1.0 release (marked latest).
param([Parameter(Mandatory = $true)][string]$Installer)
$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $false

$item = Get-Item -LiteralPath $Installer
if ($item.Name -ne "House-Plan-3D-Setup.exe") {
  throw "asset file must be named House-Plan-3D-Setup.exe (got $($item.Name))"
}
if ($item.Length -ge 2147483648) {
  throw "Installer is $($item.Length) bytes, at or over GitHub's 2 GB release-asset limit. Not splitting it into parts."
}
if (-not $env:GH_TOKEN -and -not $env:GITHUB_TOKEN) {
  throw "GH_TOKEN is not set"
}

$tag = "v0.1.0"
$notes = Join-Path $env:TEMP "house-plan-3d-release-notes.md"
@"
Test build.

Per-user Windows installer for House Plan 3D. This is a test build: the silent install smoke test may not have passed yet.

Download and double-click ``House-Plan-3D-Setup.exe``. It installs for the current user (no administrator password) and adds Desktop and Start menu shortcuts named House Plan 3D. The shortcuts open the app with no command-prompt window.

The file is unsigned. If SmartScreen says Windows protected your PC, choose More info, then Run anyway.

Python 3.11, the locked CPU dependencies, Cairo, sample plans, the logo, and the detection weights are inside this installer. The weights are not in the git repository.

Direct link: https://github.com/8KLocal/house-plans-to-3d-combo/releases/latest/download/House-Plan-3D-Setup.exe
"@ | Set-Content -Path $notes -Encoding utf8

& gh release view $tag --json tagName 2>$null | Out-Null
$exists = ($LASTEXITCODE -eq 0)
$target = if ($env:GITHUB_HEAD_REF) { $env:GITHUB_HEAD_REF } elseif ($env:GITHUB_REF_NAME) { $env:GITHUB_REF_NAME } else { $env:GITHUB_SHA }
if (-not $exists) {
  Write-Host "creating release $tag from $target"
  & gh release create $tag $item.FullName --target $target --title "House Plan 3D 0.1.0 (test build)" --notes-file $notes --latest
} else {
  Write-Host "updating release $tag"
  & gh release upload $tag $item.FullName --clobber
  if ($LASTEXITCODE -ne 0) { throw "gh release upload failed" }
  & gh release edit $tag --latest --title "House Plan 3D 0.1.0 (test build)" --notes-file $notes
}
if ($LASTEXITCODE -ne 0) { throw "gh release command failed" }
Write-Host "published https://github.com/8KLocal/house-plans-to-3d-combo/releases/latest/download/House-Plan-3D-Setup.exe"
