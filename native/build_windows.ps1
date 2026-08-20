<#
.SYNOPSIS
    Build Hippo3D native module on Windows (MSVC).

.DESCRIPTION
    Requires CMake, Ninja or Visual Studio, Blender's bundled Python, pybind11, and OCCT.

    IMPORTANT: The build MUST use Blender's bundled python.exe to guarantee ABI
    compatibility. Building against a standalone Python (pyenv, system, etc.)
    will likely produce a .pyd that crashes or fails to load inside Blender.

    The script auto-discovers Blender Python from standard install paths:
        C:\Program Files\Blender Foundation\Blender\<version>\python\bin\python.exe
        %LOCALAPPDATA%\Blender Foundation\Blender\<version>\python\bin\python.exe

    If auto-detection fails, pass -PythonExecutable explicitly.

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
# ---------------------------------------------------------------------------
# Discover Python - MUST match Blender's bundled Python ABI
# ---------------------------------------------------------------------------
if ($PythonExecutable -eq "") {
    # -----------------------------------------------------------------------
    # PRIORITY 1: Blender-bundled Python (exact ABI match required)
    # -----------------------------------------------------------------------
    # Blender bundles its own Python. Building against any other Python
    # (system, pyenv, etc.) will produce ABI-incompatible .pyd modules.
    #
    # Common Blender install locations on Windows:
    #   C:\Program Files\Blender Foundation\Blender 4.2\4.2\python\bin\python.exe
    #   C:\Program Files\Blender Foundation\Blender\4.2\python\bin\python.exe
    #   %LOCALAPPDATA%\Blender Foundation\Blender\4.2\python\bin\python.exe
    #   C:\Program Files\Blender Foundation\Blender 3.6\3.6\python\bin\python.exe
    #
    # We search for any Blender version and pick the newest one.
    # -----------------------------------------------------------------------
    $blenderRoots = @(
        "${env:ProgramFiles}\Blender Foundation\Blender"
        "${env:LOCALAPPDATA}\Blender Foundation\Blender"
        "${env:ProgramFiles(x86)}\Blender Foundation\Blender"
        "C:\Program Files\Blender Foundation\Blender"
        "C:\Program Files (x86)\Blender Foundation\Blender"
    )

    $blenderPyCandidates = @()
    foreach ($root in $blenderRoots) {
        if (-not (Test-Path $root)) { continue }

        # Look for version subdirectories (e.g. 4.2, 3.6)
        $versions = Get-ChildItem -Directory $root -ErrorAction SilentlyContinue |
                    Where-Object { $_.Name -match '^\d+\.\d+$' } |
                    Sort-Object Name -Descending

        foreach ($v in $versions) {
            # Try both "X.Y\X.Y\python\bin" (installer) and "X.Y\python\bin" (portable)
            $guesses = @(
                (Join-Path $v.FullName "$($v.Name)\python\bin\python.exe")
                (Join-Path $v.FullName "python\bin\python.exe")
            )
            foreach ($g in $guesses) {
                if (Test-Path $g) {
                    $blenderPyCandidates += $g
                }
            }
        }
    }

    if ($blenderPyCandidates.Count -gt 0) {
        $PythonExecutable = $blenderPyCandidates[0]
        Write-Host "Auto-detected Blender Python: $PythonExecutable"
    }

    # -----------------------------------------------------------------------
    # FALLBACK: Only if Blender Python cannot be found
    # -----------------------------------------------------------------------
    if ($PythonExecutable -eq "") {
        Write-Warning "Blender-bundled Python not found in standard locations."
        Write-Warning "Building against a non-Blender Python will likely produce"
        Write-Warning "an ABI-incompatible module that crashes or fails to load."
        Write-Warning ""
        Write-Warning "Please either:"
        Write-Warning "  1. Install Blender and ensure it is in a standard path,"
        Write-Warning "  2. Pass -PythonExecutable with the full path to Blender's python.exe"
        Write-Warning "     (e.g.  'C:\Program Files\Blender Foundation\Blender 4.2\4.2\python\bin\python.exe')"
        Write-Warning ""
        Write-Warning "Continuing with system Python as a last resort..."

        $fallbacks = @("py", "python", "python3")
        foreach ($c in $fallbacks) {
            $found = Get-Command $c -ErrorAction SilentlyContinue
            if ($found) {
                $PythonExecutable = $found.Source
                break
            }
        }
    }
}

if (-not (Test-Path $PythonExecutable)) {
    throw "Python executable not found. Please pass -PythonExecutable."
}

Write-Host "Using Python: $PythonExecutable"
& $PythonExecutable --version

# ---------------------------------------------------------------------------
# Discover matching Python development files for Windows
# ---------------------------------------------------------------------------
# Blender's bundled Python has no include/Python.h or python313.lib, which
# CMake's FindPython needs.  If PYTHON_DEV_DIR is not set, try to auto-detect
# a standalone Python install whose major.minor version matches Blender's.
if (-not $env:PYTHON_DEV_DIR) {
    $pyVersionOutput = & $PythonExecutable --version 2>&1
    if ($pyVersionOutput -match "Python (\d+)\.(\d+)") {
        $pyMajor = $matches[1]
        $pyMinor = $matches[2]
        $pyVerTag = "$pyMajor$pyMinor"

        # Candidate roots to search for a matching Python dev install
        $pyDevCandidates = @(
            "C:\Users\$env:USERNAME\AppData\Local\Programs\Python\Python$pyVerTag"
            "C:\Program Files\Python$pyVerTag"
            "C:\Program Files (x86)\Python$pyVerTag"
            "C:\Python$pyVerTag"
        )

        foreach ($candidate in $pyDevCandidates) {
            $lib = Join-Path $candidate "libs\python$pyVerTag.lib"
            $hdr = Join-Path $candidate "include\Python.h"
            if ((Test-Path $lib) -and (Test-Path $hdr)) {
                $env:PYTHON_DEV_DIR = $candidate
                Write-Host "Auto-detected Python development install: $candidate"
                break
            }
        }

        # Last-resort fallback to the temp tree we may have downloaded earlier
        if (-not $env:PYTHON_DEV_DIR) {
            $fallback = "C:\Users\$env:USERNAME\AppData\Local\Temp\opencode\py$pyVerTag`_dev"
            $lib = Join-Path $fallback "libs\python$pyVerTag.lib"
            $hdr = Join-Path $fallback "include\Python.h"
            if ((Test-Path $lib) -and (Test-Path $hdr)) {
                $env:PYTHON_DEV_DIR = $fallback
                Write-Host "Auto-detected fallback Python development tree: $fallback"
            }
        }
    }
}

if ($env:PYTHON_DEV_DIR) {
    Write-Host "PYTHON_DEV_DIR: $env:PYTHON_DEV_DIR"
}
$pybindOk = & $PythonExecutable -m pybind11 --cmakedir 2>$null
if ($LASTEXITCODE -ne 0 -or -not $pybindOk) {
    Write-Host "pybind11 missing - installing..."
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
    "C:\OCCT\opencascade-8.0.0-vc14-64",
    "C:\OCCT\opencascade-7.9.0-vc14-64",
    "C:\OCCT\opencascade-7.8.0-vc14-64",
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
    # Standard layout: include/opencascade or inc
    if (Test-Path "$p\include\opencascade\BRepPrimAPI_MakeBox.hxx") {
        $autoOcct = $p
        break
    }
    if (Test-Path "$p\include\BRepPrimAPI_MakeBox.hxx") {
        $autoOcct = $p
        break
    }
    if (Test-Path "$p\inc\BRepPrimAPI_MakeBox.hxx") {
        $autoOcct = $p
        break
    }
    # OCCT Windows installer layout: opencascade-X.X.X-vc14-64 / inc
    foreach ($occtVersion in @("8.0.0", "8.0.1", "7.9.0", "7.8.0", "7.7.0")) {
        $sub = "$p\opencascade-$occtVersion-vc14-64"
        if (Test-Path "$sub\inc\BRepPrimAPI_MakeBox.hxx") {
            $autoOcct = $sub
            break
        }
    }
    if ($autoOcct) { break }
}

if ($autoOcct) {
    $env:OCCT_ROOT = $autoOcct
    # Also set 3RDPARTY_DIR if the 3rdparty folder exists next to the OCCT root
    $occtParent = Split-Path $autoOcct -Parent
    $thirdPartyDir = Join-Path $occtParent "3rdparty-vc14-64"
    if (Test-Path $thirdPartyDir) {
        $env:3RDPARTY_DIR = $thirdPartyDir
        Write-Host "Auto-detected Windows OCCT: $autoOcct"
        Write-Host "3rdparty dir: $thirdPartyDir"
    }
    else {
        # Also try a sibling 3rdparty-vc14-64 directly under the same root as OCCT
        $thirdPartyDir2 = Join-Path $autoOcct "..\3rdparty-vc14-64"
        if (Test-Path $thirdPartyDir2) {
            $env:3RDPARTY_DIR = (Resolve-Path $thirdPartyDir2).Path
            Write-Host "Auto-detected Windows OCCT: $autoOcct"
            Write-Host "3rdparty dir: $($env:3RDPARTY_DIR)"
        }
        else {
            Write-Host "Auto-detected Windows OCCT: $autoOcct"
        }
    }
}

# ---------------------------------------------------------------------------
# Pick CMake generator
# ---------------------------------------------------------------------------
$generator = ""
if ($Toolchain -eq "MSVC") {
    # Prefer Ninja if available, otherwise Visual Studio
    $ninja = Get-Command ninja -ErrorAction SilentlyContinue
    $cl = Get-Command cl -ErrorAction SilentlyContinue
    if ($ninja -and $cl) {
        $generator = "Ninja Multi-Config"
    } else {
        $vsWhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
        if (Test-Path $vsWhere) {
            $generator = "Visual Studio 17 2022"
        } else {
            # No VS installed; try a full MinGW build instead of failing.
            $Toolchain = "MinGW"
        }
    }
}
if ($Toolchain -eq "MinGW") {
    $generator = "MinGW Makefiles"
}

# ---------------------------------------------------------------------------
# Patch OpenNURBS for Windows
# ---------------------------------------------------------------------------
# The bundled opennurbs submodule tries to build android_uuid and freetype263
# on both ANDROID and LINUX, but the LINUX branch is also taken under MSVC
# because opennurbs' WIN32 detection is missing here. android_uuid requires
# POSIX headers (unistd.h) that do not exist on Windows, so the build fails.
# We patch the one-line condition before CMake configures. The patch is applied
# locally every build; the upstream submodule commit is left unchanged.
$onCmake = Join-Path $scriptDir "third_party" "opennurbs" "CMakeLists.txt"
if (Test-Path $onCmake) {
    $content = Get-Content $onCmake -Raw
    if ($content -match "if \(ANDROID OR LINUX\)") {
        $content = $content -replace "if \(ANDROID OR LINUX\)", "if ((ANDROID OR LINUX) AND NOT WIN32)"
        Set-Content $onCmake $content -NoNewline
        Write-Host "Patched third_party/opennurbs/CMakeLists.txt for Windows build."
    }
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
    "-DHIPPO_PLATFORM_FOLDER=$PlatformFolder",
    "-DOPENNURBS_BUILD_ON_WINDOWS=ON"
)

if ($env:PYTHON_DEV_DIR) {
    $cmakeArgs += "-DPython_ROOT_DIR=$env:PYTHON_DEV_DIR"
}

& cmake @cmakeArgs

# ---------------------------------------------------------------------------
# Build (Release)
# ---------------------------------------------------------------------------
# OpenNURBS static lib must be built before hippo_occ_core is linked, otherwise
# Visual Studio generator reports LNK1181 because the dependency ordering in
# the generated .sln is not fully respected for sibling subdirectories.
& cmake --build build --config Release --target opennurbsStatic
if ($generator -like "Visual Studio*") {
    & cmake --build build --config Release
} else {
    & cmake --build build --config Release
}

# ---------------------------------------------------------------------------
# Locate and copy artifact
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "Searching for built module in native/build ..."

# Possible names: hippo_occ_core.pyd, hippo_occ_core.cp311-win_amd64.pyd,
# or hippo_occ_core.dll if pybind11 suffix detection failed.
$patterns = @("hippo_occ_core*.pyd", "hippo_occ_core*.dll")
$artifact = $null
foreach ($pat in $patterns) {
    $matches = Get-ChildItem -Recurse -Filter $pat build
    if ($matches) {
        # Prefer shortest/simplest name, but any will work
        $artifact = $matches | Sort-Object Name | Select-Object -First 1
        break
    }
}

if (-not $artifact) {
    Write-Host "ERROR: No hippo_occ_core module found in native/build."
    Write-Host "Files present in build directory:"
    Get-ChildItem -Recurse build | Where-Object { $_.Name -like "hippo_occ_core*" -or $_.Name -like "*.pyd" -or $_.Name -like "*.dll" } | ForEach-Object {
        Write-Host "  $($_.FullName)"
    }
    throw "Build finished, but hippo_occ_core module was not found in native/build."
}

$outDir = "$PlatformFolder"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

# Preserve ABI-tagged name (e.g. hippo_occ_core.cp311-win_amd64.pyd) -
# occ_loader.py searches for hippo_occ_core.*.pyd and picks the newest.
$outPyd = Join-Path $outDir $artifact.Name
Copy-Item $artifact.FullName -Destination $outPyd -Force

# ---------------------------------------------------------------------------
# Bundle runtime dependencies next to the module
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "Bundling OCCT/3rdparty DLLs ..."
& $PythonExecutable bundle_occt.py --platform $PlatformFolder

# Blender's bundled Python DLLs are required at runtime but are not part of
# OCCT. Copy them from the Blender install tree next to the module so the
# add-on folder is self-contained. Derive the versioned DLL name from the
# interpreter used for the build (e.g. python311.dll for Python 3.11).
$pyVersionOutput = & $PythonExecutable --version 2>&1
$pyMajor = $pyMinor = 0
if ($pyVersionOutput -match "Python (\d+)\.(\d+)") {
    $pyMajor = $matches[1]
    $pyMinor = $matches[2]
}
$pythonDllDir = Split-Path $PythonExecutable -Parent
$versionedDll = "python${pyMajor}${pyMinor}.dll"
foreach ($dll in @("python3.dll", $versionedDll)) {
    $src = Join-Path $pythonDllDir $dll
    if (-not (Test-Path $src)) {
        # Portable Blender layout: DLLs live at the top-level Blender folder
        try {
            $candidate = Join-Path (Split-Path $pythonDllDir -Parent) ".." $dll
            $candidate = Resolve-Path $candidate -ErrorAction SilentlyContinue
            if ($candidate -and (Test-Path $candidate)) {
                $src = $candidate
            }
        } catch {
            # Ignore resolution errors and keep the original (non-existent) path.
        }
    }
    if (Test-Path $src) {
        Copy-Item $src -Destination $outDir -Force
        Write-Host "  copied $dll"
    } else {
        Write-Warning "Could not locate $dll next to the build Python. The packaged add-on may fail to load if Blender does not provide it."
    }
}

Write-Host ""
Write-Host "Build complete."
Write-Host "Extension module:"
Write-Host "  $outPyd"
Write-Host ""
Write-Host "To test in Blender, ensure the folder is on PATH, then run:"
Write-Host '  import sys'
Write-Host "  sys.path.append(r'$scriptDir\$outDir')"
Write-Host '  import hippo_occ_core'
Write-Host '  print(hippo_occ_core.make_box_mesh(10, 10, 10).keys())'

Pop-Location

