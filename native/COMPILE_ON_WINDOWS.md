# Compiling Hippo3D Native Module on Windows

This guide is a step-by-step recipe for building the `hippo_occ_core` native extension on Windows, bundling it with all runtime dependencies, and producing an installable Blender add-on ZIP.

For a more general cross-platform build reference, see [`README_BUILD.md`](./README_BUILD.md).

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [One-Line Build](#one-line-build)
3. [Step-by-Step Build](#step-by-step-build)
4. [What the Build Script Does](#what-the-build-script-does)
5. [Where the Outputs Go](#where-the-outputs-go)
6. [Bundling the Add-on for End Users](#bundling-the-add-on-for-end-users)
7. [Installing the Add-on in Blender](#installing-the-add-on-in-blender)
8. [Troubleshooting](#troubleshooting)

---

## Prerequisites

| Requirement | Why it is needed | How to check |
|-------------|------------------|--------------|
| **Blender 5.x (or 4.2+) for Windows** | The native module must be built against Blender's bundled Python ABI. | Open Blender and check the version in the splash screen or `Help > About`. |
| **Visual Studio 2022 Build Tools or IDE** | Provides the MSVC C++ compiler and CMake toolchain. | Run `cl` in a Developer PowerShell; it should print version info. |
| **CMake >= 3.20** | Generates the build system. | `cmake --version` |
| **OpenCASCADE 8.0.0 (OCCT) Windows installer** | CAD geometry kernel used by the module. | Check that `C:\OCCT\opencascade-8.0.0-vc14-64\inc\BRepPrimAPI_MakeBox.hxx` exists. |
| **pybind11 installed in Blender's Python** | C++/Python binding layer. | See installation step below. |
| **Python 3.13 development files** | Blender's bundled Python has no `include/Python.h` or `.lib`. | A matching standalone Python 3.13 install, or the fallback tree described below. |

### 1. Install Blender

Download the Windows portable or installer version from [blender.org](https://www.blender.org/download/) and note the folder path. This guide uses:

```text
C:\Documents\blender-5.1.2-windows-x64
```

### 2. Install Visual Studio 2022 Build Tools

Download the Build Tools from [visualstudio.microsoft.com](https://visualstudio.microsoft.com/downloads/?q=build+tools) and install the **Desktop development with C++** workload.

### 3. Install OCCT 8.0.0

Download the Windows installer from [OpenCASCADE.org](https://dev.opencascade.org/system/files/resources/OCCT/) and install it to the default location:

```text
C:\OCCT
```

You should end up with:

```text
C:\OCCT\opencascade-8.0.0-vc14-64\inc
C:\OCCT\opencascade-8.0.0-vc14-64\win64\vc14\lib
C:\OCCT\opencascade-8.0.0-vc14-64\win64\vc14\bin
C:\OCCT\3rdparty-vc14-64
```

### 4. Install pybind11 into Blender's Python

Open a **normal** PowerShell (not necessarily a Developer shell) and run:

```powershell
& "C:\Documents\blender-5.1.2-windows-x64\5.1\python\bin\python.exe" -m pip install pybind11
```

Replace the path with your Blender installation path.

### 5. Python 3.13 development files

Blender's bundled Python does **not** include the C headers or `python313.lib` that CMake needs. The build script will try to auto-detect one of:

1. `C:\Users\<you>\AppData\Local\Programs\Python\Python313`
2. `C:\Program Files\Python313`
3. `C:\Python313`
4. A fallback tree at `C:\Users\<you>\AppData\Local\Temp\opencode\py313_dev`

If none are found, install a standalone **Python 3.13.x for Windows** from [python.org](https://www.python.org/downloads/release/python-3139/) (choose the same minor version as Blender's, e.g. 3.13.9). Make sure the **optional features** include the C development headers.

Alternatively, set the path explicitly before building:

```powershell
$env:PYTHON_DEV_DIR = "C:\Path\To\Python313"
```

---

## One-Line Build

Open a **Developer PowerShell for VS 2022**, navigate to the `native` folder, and run:

```powershell
cd "C:\Users\arqvi\github\Hippo3D\native"
.\build_windows.ps1 -PythonExecutable "C:\Documents\blender-5.1.2-windows-x64\5.1\python\bin\python.exe"
```

If Blender is installed in a standard location and you did not move OCCT, the script should auto-detect everything.

---

## Step-by-Step Build

If the one-line build fails or you want to understand each step, do the following.

### Step 1: Open a Developer PowerShell for VS 2022

You can launch it from the Start menu as **"Developer PowerShell for VS 2022"**. This sets up `cl`, `cmake`, and other tools.

### Step 2: Navigate to the project

```powershell
cd "C:\Users\arqvi\github\Hippo3D\native"
```

### Step 3: Verify Python and pybind11

```powershell
$py = "C:\Documents\blender-5.1.2-windows-x64\5.1\python\bin\python.exe"
& $py --version
& $py -m pybind11 --cmakedir
```

The second command should print a path ending in `share\cmake\pybind11`. If not, install pybind11 as shown above.

### Step 4: Verify OCCT

```powershell
Test-Path "C:\OCCT\opencascade-8.0.0-vc14-64\inc\BRepPrimAPI_MakeBox.hxx"
```

It should return `True`. If OCCT is elsewhere, set:

```powershell
$env:OCCT_ROOT = "C:\Your\OCCT\Path"
```

### Step 5: Configure CMake

```powershell
cmake -S . -B build -G "Visual Studio 17 2022" `
  -DPython_EXECUTABLE="C:\Documents\blender-5.1.2-windows-x64\5.1\python\bin\python.exe" `
  -Dpybind11_DIR="C:\Documents\blender-5.1.2-windows-x64\5.1\python\Lib\site-packages\pybind11\share\cmake\pybind11" `
  -DHIPPO_PLATFORM_FOLDER=windows-x64 `
  -DOPENNURBS_BUILD_ON_WINDOWS=ON
```

If you need to point to a Python dev tree:

```powershell
cmake -S . -B build -G "Visual Studio 17 2022" `
  -DPython_EXECUTABLE="C:\Documents\blender-5.1.2-windows-x64\5.1\python\bin\python.exe" `
  -DPython_ROOT_DIR="C:\Path\To\Python313" `
  -Dpybind11_DIR="..." `
  -DHIPPO_PLATFORM_FOLDER=windows-x64 `
  -DOPENNURBS_BUILD_ON_WINDOWS=ON
```

### Step 6: Build OpenNURBS first

The Visual Studio generator does not always respect dependency ordering for sibling sub-projects, so build the static library first:

```powershell
cmake --build build --config Release --target opennurbsStatic
```

### Step 7: Build the Hippo3D module

```powershell
cmake --build build --config Release
```

### Step 8: Copy the module

```powershell
New-Item -ItemType Directory -Force -Path windows-x64 | Out-Null
Copy-Item "build\Release\hippo_occ_core.cp313-win_amd64.pyd" -Destination windows-x64 -Force
```

### Step 9: Bundle OCCT and 3rdparty DLLs

```powershell
& "C:\Documents\blender-5.1.2-windows-x64\5.1\python\bin\python.exe" bundle_occt.py --platform windows-x64
```

### Step 10: Copy Python runtime DLLs

```powershell
$blender = "C:\Documents\blender-5.1.2-windows-x64"
Copy-Item "$blender\python313.dll" windows-x64 -Force
Copy-Item "$blender\python3.dll"   windows-x64 -Force
```

### Step 11: Test

With only the bundled folder on `PATH`:

```powershell
$env:PATH = "C:\Users\arqvi\github\Hippo3D\native\windows-x64"
& "C:\Documents\blender-5.1.2-windows-x64\5.1\python\bin\python.exe" -c `
  "import sys; sys.path.append(r'C:\Users\arqvi\github\Hippo3D\native\windows-x64'); ` 
   import hippo_occ_core; print(hippo_occ_core.make_box_mesh(10, 10, 10).keys())"
```

Expected output:

```text
dict_keys(['vertices', 'faces', 'shape_id', 'edges'])
```

---

## What the Build Script Does

Running `build_windows.ps1` performs the equivalent of Steps 5-10 automatically:

1. Detects Blender's Python (or uses the `-PythonExecutable` you passed).
2. Detects or falls back to a matching Python 3.13 development tree.
3. Verifies `pybind11` and installs it if missing.
4. Auto-detects `C:\OCCT\opencascade-8.0.0-vc14-64` and sibling `3rdparty-vc14-64`.
5. Picks `Visual Studio 17 2022` or `Ninja Multi-Config`.
6. Configures CMake.
7. Builds `opennurbsStatic` first, then `hippo_occ_core`.
8. Copies the `.pyd` to `native/windows-x64/`.
9. Runs `bundle_occt.py` to copy all OCCT and 3rdparty DLLs (including transitive dependencies).
10. Copies `python3.dll` and `python313.dll` from Blender.

---

## Where the Outputs Go

```text
Hippo3D/
├── native/
│   ├── build/                            # CMake build tree (can be deleted)
│   └── windows-x64/                      # Self-contained native module folder
│       ├── hippo_occ_core.cp313-win_amd64.pyd
│       ├── python3.dll
│       ├── python313.dll
│       ├── TKernel.dll
│       ├── TKMath.dll
│       ├── ... (all other TK*.dll)
│       ├── tbb12.dll
│       ├── jemalloc.dll
│       ├── freetype.dll
│       ├── FreeImage.dll
│       ├── avcodec-57.dll
│       ├── avformat-57.dll
│       ├── avutil-55.dll
│       ├── swscale-4.dll
│       ├── zlib.dll
│       ├── openvr_api.dll
│       └── winmm.dll
```

The `kernels/occ_loader.py` script in the add-on automatically adds this folder to `PATH` when the add-on starts, so end users do not need to configure anything.

---

## Bundling the Add-on for End Users

After building, run the packaging script from the project root:

```powershell
cd "C:\Users\arqvi\github\Hippo3D"
.\package_addon.ps1
```

This creates a self-contained ZIP such as:

```text
dist/Hippo3D-windows-x64.zip
```

The ZIP contains:

```text
Hippo3D/
├── blender_manifest.toml
├── __init__.py
├── main.py
├── common.py
├── cplanes.py
├── geometry.py
├── registration.py
├── state.py
├── kernels/
│   ├── __init__.py
│   ├── occ_kernel.py
│   ├── occ_loader.py
│   └── occ_mesh_adapter.py
├── icons/
├── native/
│   └── windows-x64/              # module + all runtime DLLs
└── ...
```

End users install this ZIP directly in Blender via **Edit > Preferences > Add-ons > Install from Disk**. No OCCT, Python, or compiler setup is required on their machine.

---

## Installing the Add-on in Blender

1. In Blender, go to **Edit > Preferences > Add-ons**.
2. Click **Install from Disk...**.
3. Select the generated `Hippo3D-windows-x64.zip`.
4. Enable the **Hippo3D** add-on in the list.
5. The add-on will load `native/windows-x64/hippo_occ_core.cp313-win_amd64.pyd` and all bundled DLLs automatically.

---

## Troubleshooting

### Build script cannot find Blender Python

Pass it explicitly:

```powershell
.\build_windows.ps1 -PythonExecutable "C:\Your\Path\To\Blender\5.1\python\bin\python.exe"
```

### "Python development files not found"

Install a standalone Python 3.13 and point to it:

```powershell
$env:PYTHON_DEV_DIR = "C:\Users\<you>\AppData\Local\Programs\Python\Python313"
.\build_windows.ps1 -PythonExecutable "..."
```

### "Cannot find OpenCASCADE (OCCT)"

Set the OCCT root explicitly:

```powershell
$env:OCCT_ROOT = "C:\OCCT\opencascade-8.0.0-vc14-64"
.\build_windows.ps1 -PythonExecutable "..."
```

### Module fails to import with `DLL load failed`

Run the diagnostic script from the `native` folder:

```powershell
.\diagnose_windows_dll.py windows-x64
```

It will report which DLL is missing. Usually this means `bundle_occt.py` did not copy a transitive dependency; re-run the build script.

### `dumpbin` not found when running `bundle_occt.py` manually

The updated `bundle_occt.py` searches common Visual Studio paths automatically. If it still fails, open a **Developer PowerShell for VS 2022** and try again.

### `opennurbs_public_freetype.lib` linker warning

This is a cosmetic warning from the OpenNURBS sub-project and does **not** prevent the final `.pyd` from linking. It can be ignored.

---

## Summary

```powershell
cd "C:\Users\arqvi\github\Hippo3D\native"
.\build_windows.ps1 -PythonExecutable "C:\Documents\blender-5.1.2-windows-x64\5.1\python\bin\python.exe"
cd ..
.\package_addon.ps1
```

You now have a ready-to-install Blender add-on at `dist/Hippo3D-windows-x64.zip`.
