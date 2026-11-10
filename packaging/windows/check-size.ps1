# GitHub release assets must be strictly under 2 GiB. Do not split the file.
param([Parameter(Mandatory = $true)][string]$Installer)
$ErrorActionPreference = "Stop"
$item = Get-Item -LiteralPath $Installer
$limit = 2147483648
Write-Host ("Installer size: {0} bytes ({1:N1} MB)" -f $item.Length, ($item.Length / 1MB))
if ($env:GITHUB_STEP_SUMMARY) {
  "Installer size: $($item.Length) bytes ($([math]::Round($item.Length / 1MB, 1)) MB). GitHub release limit is 2 GiB." | Add-Content -Path $env:GITHUB_STEP_SUMMARY
}
if ($item.Length -ge $limit) {
  throw "Installer is $($item.Length) bytes, at or over GitHub's 2 GB release-asset limit. Not splitting it into parts."
}
Write-Host "size is under the 2 GB release limit"
