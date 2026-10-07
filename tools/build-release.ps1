# Hatchtower – Installationspaket bauen (Windows).
# Aufruf im Projektordner:  powershell -ExecutionPolicy Bypass -File tools\build-release.ps1
# Ergebnis:                 dist\hatchtower-<version>.tar.gz
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$version = ([regex]::Match((Get-Content app\hatchtower\__init__.py -Raw), '__version__ = "([^"]+)"')).Groups[1].Value
if (-not $version) { throw "Version nicht gefunden" }
[IO.File]::WriteAllText((Join-Path $root "VERSION"), "$version`n", (New-Object System.Text.UTF8Encoding $false))

$name = "hatchtower-$version"
New-Item -ItemType Directory -Force dist | Out-Null
$out = Join-Path $root "dist\$name.tar.gz"
if (Test-Path $out) { [IO.File]::Delete($out) }

# Im Bauordner unter hatchtower-<version>/ zusammenstellen und packen
$build = Join-Path $root "dist\.build"
if (Test-Path $build) { [IO.Directory]::Delete($build, $true) }
$stage = Join-Path $build $name
New-Item -ItemType Directory -Force $stage | Out-Null
foreach ($item in "app", "installer", "tools", "README.md", "VERSION") {
    Copy-Item -Recurse -Path (Join-Path $root $item) -Destination $stage
}
Get-ChildItem -Path $stage -Recurse -Directory -Filter __pycache__ | ForEach-Object { [IO.Directory]::Delete($_.FullName, $true) }
tar -czf $out -C $build $name
if ($LASTEXITCODE -ne 0) { throw "tar fehlgeschlagen" }
[IO.Directory]::Delete($build, $true)
Write-Output $out
