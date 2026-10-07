# Hatchtower – neue Version auf einen Server ausrollen (Entwicklung).
# Aufruf:  powershell -ExecutionPolicy Bypass -File tools\deploy.ps1 [-Target hatchtower]
param([string]$Target = "hatchtower")
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

$package = (& powershell -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "build-release.ps1")) | Select-Object -Last 1
$file = Split-Path -Leaf $package
scp -q $package "${Target}:/tmp/$file"
ssh $Target "hatchtower update /tmp/$file && rm -f /tmp/$file"
if ($LASTEXITCODE -ne 0) { throw "Update auf $Target fehlgeschlagen" }
