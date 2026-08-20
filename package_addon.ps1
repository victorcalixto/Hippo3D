#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Package Hippo3D as a self-contained Blender add-on ZIP.

.DESCRIPTION
    This script collects the Python add-on files and the pre-built native
    module folder (including all bundled OCCT / 3rdparty / Python runtime DLLs)
    into a ZIP file that can be installed directly from Blender:

        Edit > Preferences > Add-ons > Install from Disk...

    The output is written to the dist/ folder at the project root.

.PARAMETER Platform
    Platform folder name under native/ to include. Default: auto-detected.

.PARAMETER OutputDir
    Directory for the generated ZIP. Default: dist/

.PARAMETER ZipName
    Base name for the ZIP file (without .zip). Default: Hippo3D-<platform>.

.EXAMPLE
    .\package_addon.ps1
    .\package_addon.ps1 -Platform windows-x64 -ZipName Hippo3D-v0.2.0-windows
#>
param(
    [string]$Platform = "",
    [string]$OutputDir = "dist",
    [string]$ZipName = "",
    [string]$PythonVersion = ""
)

$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# Determine project root and platform
# ---------------------------------------------------------------------------
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$projectRoot = Resolve-Path $scriptDir

if ($Platform -eq "") {
    $system = [System.Environment]::OSVersion.Platform
    if ($system -eq "Win32NT") {
        $Platform = "windows-x64"
    } else {
        throw "Auto-detection on non-Windows is not implemented. Pass -Platform explicitly."
    }
}

$nativeDir = Join-Path $projectRoot "native" $Platform
if (-not (Test-Path $nativeDir)) {
    throw "Native module folder not found: $nativeDir`nPlease build first with build_windows.ps1"
}

$pydCandidates = Get-ChildItem -Path $nativeDir -Filter "hippo_occ_core*.pyd" -ErrorAction SilentlyContinue
$soCandidates = Get-ChildItem -Path $nativeDir -Filter "hippo_occ_core*.so" -ErrorAction SilentlyContinue
if ((-not $pydCandidates) -and (-not $soCandidates)) {
    throw "No hippo_occ_core module found in $nativeDir. Please build first."
}

if ($ZipName -eq "") {
    $pyTag = ""
    if ($PythonVersion -ne "") {
        $parts = $PythonVersion.Split(".")
        if ($parts.Length -ge 2) {
            $pyTag = "-python" + $parts[0] + $parts[1]
        }
    }
    $ZipName = "Hippo3D-$Platform$pyTag"
}

$outDir = Join-Path $projectRoot $OutputDir
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

# Use a deterministic temp staging folder
$stagingName = "Hippo3D"
$stagingRoot = Join-Path $outDir $stagingName
if (Test-Path $stagingRoot) {
    Remove-Item -Recurse -Force $stagingRoot
}
New-Item -ItemType Directory -Force -Path $stagingRoot | Out-Null

# ---------------------------------------------------------------------------
# Collect add-on Python files
# ---------------------------------------------------------------------------
$addonFiles = @(
    "blender_manifest.toml",
    "__init__.py",
    "main.py",
    "common.py",
    "cplanes.py",
    "geometry.py",
    "registration.py",
    "state.py",
    "test_in_blender.py",
    "test_serpentine_bridge.py",
    "LICENSE",
    "README.md"
)

foreach ($file in $addonFiles) {
    $src = Join-Path $projectRoot $file
    if (Test-Path $src) {
        Copy-Item $src -Destination $stagingRoot -Force
    }
}

# Copy directories that belong to the add-on
$addonDirs = @("kernels", "icons")
foreach ($dir in $addonDirs) {
    $src = Join-Path $projectRoot $dir
    if (Test-Path $src) {
        Copy-Item -Recurse $src -Destination $stagingRoot -Force
    }
}

# Remove stale bytecode from the build host so it cannot cause wrong-ABI .pyc
# errors inside Blender.
Get-ChildItem -Path $stagingRoot -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue | ForEach-Object {
    Remove-Item -Recurse -Force $_.FullName
}

# ---------------------------------------------------------------------------
# Collect native module + bundled dependencies
# ---------------------------------------------------------------------------
$stagingNative = Join-Path $stagingRoot "native" $Platform
New-Item -ItemType Directory -Force -Path $stagingNative | Out-Null

$nativeFiles = Get-ChildItem -Path $nativeDir -File
foreach ($f in $nativeFiles) {
    # Skip temporary diagnostic files if any are left behind
    if ($f.Name -match "^(chain_|deps_|mods_).*\.(txt|json)$") { continue }
    Copy-Item $f.FullName -Destination $stagingNative -Force
}

# ---------------------------------------------------------------------------
# Create ZIP
# ---------------------------------------------------------------------------
$zipBase = Join-Path $outDir $ZipName
$zipPath = "$zipBase.zip"
if (Test-Path $zipPath) {
    Remove-Item -Force $zipPath
}

Compress-Archive -Path "$stagingRoot\*" -DestinationPath $zipPath -Force

# Clean staging folder after creating the archive
Remove-Item -Recurse -Force $stagingRoot

# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
$zipItem = Get-Item $zipPath
Write-Host ""
Write-Host "Packaging complete."
Write-Host "  ZIP: $zipPath"
Write-Host "  Size: $([math]::Round($zipItem.Length / 1MB, 2)) MB"
Write-Host ""
Write-Host "Install in Blender via:"
Write-Host "  Edit > Preferences > Add-ons > Install from Disk..."
