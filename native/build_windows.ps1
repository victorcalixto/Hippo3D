<#
.SYNOPSIS
    Build Hippo3D native module on Windows (MSVC).

.DESCRIPTION
    Requires CMake, Ninja or Visual Studio, Python 3.11+, pybind11, and OCCT.

.PARAMETER Toolchain
    Build tool: 'MSVC' (default) or 'MinGW'. Currently MSVC is fully supported.

.PARAMETER PythonExecutable
    Full path to the Python executable to use.

.PARAMETER PlatformFolder
    Output subfolder name, default 'windows-x64'.

.EXAMPLE
    .\build_windows.ps1
    .\build_windows.ps1 -Toolchain MSVC -PythonExecutable "C:\Python311\python.exe"
#>
param(
    [ValidateSet("MSVC","MinGW")]
    [string]$Toolchain = "MSVC",
    [string]$PythonExecutable = "",
    [string]$PlatformFolder = "windows-x64"
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
Push-Location $scriptDir

# ---------------------------------------------------------------------------
# Discover Python
# ---------------------------------------------------------------------------
if ($PythonExecutable -eq "") {
    # Try "py" launcher first, then bare python
    $candidates = @("py", "python", "python3")
    foreach ($c in $candidates) {
        $found = Get-Command $c -ErrorAction SilentlyContinue
        if ($found) {
            $PythonExecutable = $found.Source
            break
        }
    }
}

if (-not (Test-Path $PythonExecutable)) {
    throw "Python executable not found. Please pass -PythonExecutable."
}

Write-Host "Using Python: $PythonExecutable"
& $PythonExecutable --version

# ---------------------------------------------------------------------------
# Ensure pybind11
# ---------------------------------------------------------------------------
$pybindOk = & $PythonExecutable -m pybind11 --cmakedir 2>$null
if ($LASTEXITCODE -ne 0 -or -not $pybindOk) {
    Write-Host "pybind11 missing — installing..."
    & $PythonExecutable -m pip install --upgrade pip pybind11
    $pybindOk = & $PythonExecutable -m pybind11 --cmakedir
}

$PYBIND11_DIR = $pybindOk.Trim()
Write-Host "pybind11 CMake dir: $PYBIND11_DIR"

# ---------------------------------------------------------------------------
# Clean previous build
# ---------------------------------------------------------------------------
if (Test-Path build) {
    Remove-Item -Recurse -Force build
}

# ---------------------------------------------------------------------------
# Auto-detect OCCT on Windows
# ---------------------------------------------------------------------------
$occtHints = @()
if ($env:OCCT_ROOT) { $occtHints += $env:OCCT_ROOT }
if ($env:OpenCASCADE_DIR) { $occtHints += $env:OpenCASCADE_DIR }

$occtSearchPaths = @(
    "${scriptDir}\third_party\occt-8.0.0",
    "C:\OpenCASCADE-8.0.0",
    "C:\OpenCASCADE-7.9.0",
    "C:\OpenCASCADE-7.8.0",
    "C:\OpenCASCADE-7.7.0",
    "C:\OpenCASCADE",
    "C:\Program Files\OpenCASCADE",
    "C:\Program Files (x86)\OpenCASCADE",
    "C:\OCCT"
) + $occtHints

$autoOcct = ""
foreach ($p in $occtSearchPaths) {
    # Check standard layout (include/opencascade or inc)
    if (Test-Path "$p\include\opencascade\BRepPrimAPI_MakeBox.hxx") {
        $autoOcct = $p
        break
    }
    if (Test-Path "$p\inc\BRepPrimAPI_MakeBox.hxx") {
        $autoOcct = $p
        break
    }
    # Check prebuilt Windows OCCT packages (win64/vc14 layout)
    if (Test-Path "$p\win64\vc14\inc\BRepPrimAPI_MakeBox.hxx") {
        $autoOcct = $p
        break
    }
    if (Test-Path "$p\win64\vc15\inc\BRepPrimAPI_MakeBox.hxx") {
        $autoOcct = $p
        break
    }
}

if ($autoOcct) {
    $env:OCCT_ROOT = $autoOcct
    Write-Host "Auto-detected Windows OCCT: $autoOcct"
}

# ---------------------------------------------------------------------------
# Pick CMake generator
# ---------------------------------------------------------------------------
$generator = ""
if ($Toolchain -eq "MSVC") {
    # Prefer Ninja if available, otherwise Visual Studio
    $ninja = Get-Command ninja -ErrorAction SilentlyContinue
    if ($ninja) {
        $generator = "Ninja Multi-Config"
    } else {
        $generator = "Visual Studio 17 2022"
    }
} else {
    $generator = "MinGW Makefiles"
}

# ---------------------------------------------------------------------------
# Configure
# ---------------------------------------------------------------------------
$cmakeArgs = @(
    "-S", ".",
    "-B", "build",
    "-G", $generator,
    "-DPython_EXECUTABLE=$PythonExecutable",
    "-DPYTHON_EXECUTABLE=$PythonExecutable",
    "-Dpybind11_DIR=$PYBIND11_DIR",
    "-DHIPPO_PLATFORM_FOLDER=$PlatformFolder"
)

& cmake @cmakeArgs

# ---------------------------------------------------------------------------
# Build (Release)
# ---------------------------------------------------------------------------
& cmake --build build --config Release

# ---------------------------------------------------------------------------
# Locate and copy artifact
# ---------------------------------------------------------------------------
$pydName = "hippo_occ_core.pyd"
$artifact = Get-ChildItem -Recurse -Filter $pydName build | Select-Object -First 1

if (-not $artifact) {
    # Fallback: look for any hippo_occ_core.*
    $artifact = Get-ChildItem -Recurse -Filter "hippo_occ_core.*" build | Select-Object -First 1
}

if (-not $artifact) {
    throw "Build finished, but $pydName was not found in native/build."
}

$outDir = "$PlatformFolder"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
Copy-Item $artifact.FullName -Destination "$outDir\$pydName" -Force

Write-Host ""
Write-Host "Build complete."
Write-Host "Extension module:"
Write-Host "  $($scriptDir)\$outDir\$pydName"
Write-Host ""
Write-Host "Test in Blender:"
Write-Host '  import sys'
Write-Host "  sys.path.append('$($scriptDir)\build')"
Write-Host '  import hippo_occ_core'
Write-Host '  print(hippo_occ_core.make_box_mesh(10, 10, 10).keys())'

Pop-Location
