# Building Hippo3D Native Module

This document covers building the `hippo_occ_core` native extension for **Linux**, **macOS**, **Windows**, **FreeBSD**, and **OpenBSD**.

> If you are only interested in the **Linux** build, see [`README_BUILD_LINUX.md`](./README_BUILD_LINUX.md) for a concise Linux-only guide.

---

## Table of Contents

1. [Prerequisites (All Platforms)](#prerequisites-all-platforms)
2. [Linux](#linux)
3. [macOS](#macos)
4. [Windows](#windows)
5. [FreeBSD](#freebsd)
6. [OpenBSD](#openbsd)
7. [Bundling OCCT (Self-Contained ZIP)](#bundling-occt-self-contained-zip)
8. [Packaging for Blender Extensions](#packaging-for-blender-extensions)
9. [Troubleshooting](#troubleshooting)

---

## Prerequisites (All Platforms)

- **CMake** ≥ 3.20
- **Ninja** (optional but recommended)
- **Python** 3.11+ with **pybind11** installed
- **OpenCASCADE (OCCT)** 7.6+ development libraries
- A C++17-capable compiler (GCC, Clang, or MSVC)

### Installing pybind11

```bash
python -m pip install pybind11
```

### Installing OCCT

| OS | Command / Notes |
|---|---|
| **Debian/Ubuntu** | `sudo apt-get install libocct-dev occt-draw` |
| **Fedora/RHEL** | `sudo dnf install opencascade-devel` |
| **Arch** | `sudo pacman -S opencascade` |
| **macOS (Homebrew)** | `brew install opencascade` |
| **FreeBSD** | `pkg install opencascade` |
| **OpenBSD** | `pkg_add opencascade` |
| **Windows** | Download installer from [OpenCASCADE.org](https://dev.opencascade.org/system/files/resources/OCCT/) or use `vcpkg install opencascade` |

If OCCT is installed in a non-standard prefix, set the environment variable:

```bash
export OCCT_ROOT=/path/to/occt
```

On Windows (PowerShell):

```powershell
$env:OCCT_ROOT = "C:\OpenCASCADE"
```

---

## Linux

### Quick Start

```bash
cd native
./build_linux.sh
```

### With pyenv

```bash
cd native
pyenv local 3.11
./build_linux.sh
```

### Manual CMake

```bash
cd native
rm -rf build
cmake -S . -B build -G Ninja \
  -DPython_EXECUTABLE=$(which python) \
  -Dpybind11_DIR=$(python -m pybind11 --cmakedir) \
  -DHIPPO_PLATFORM_FOLDER=linux-x64
cmake --build build
```

**Output:**

- Development module: `native/build/hippo_occ_core*.so`
- Extension module:   `native/linux-x64/hippo_occ_core.so`

### Testing in Blender

```python
import sys
sys.path.append('/path/to/Hippo3D/native/build')
import hippo_occ_core
print(hippo_occ_core.make_box_mesh(10, 10, 10).keys())
```

---

## macOS

### Quick Start

```bash
cd native
./build_macos.sh
```

The script auto-detects **Apple Silicon (arm64)** vs **Intel (x86_64)** and sets the correct output folder (`macos-arm64` or `macos-x64`).

### Manual CMake

```bash
cd native
rm -rf build
ARCH=$(uname -m)
if [ "$ARCH" = "arm64" ]; then
  OSX_ARCH="arm64"; PLATFORM="macos-arm64"
else
  OSX_ARCH="x86_64"; PLATFORM="macos-x64"
fi

cmake -S . -B build \
  -DPython_EXECUTABLE=$(which python) \
  -Dpybind11_DIR=$(python -m pybind11 --cmakedir) \
  -DCMAKE_OSX_ARCHITECTURES=$OSX_ARCH \
  -DHIPPO_PLATFORM_FOLDER=$PLATFORM
cmake --build build
```

### Notes

- Ensure **Xcode Command Line Tools** are installed: `xcode-select --install`
- If using **Homebrew OCCT** on Apple Silicon, the script auto-detects `/opt/homebrew/opt/opencascade`.
- On Intel Macs, it auto-detects `/usr/local/opt/opencascade`.

---

## Windows

### Requirements

- **Visual Studio 2022** (Community edition is fine)
- **CMake** ≥ 3.20
- **Python** 3.11+ (matching the version bundled with Blender)
- **pybind11**

### Quick Start (PowerShell)

Open a **Developer PowerShell for VS 2022** and run:

```powershell
cd native
.\build_windows.ps1
```

The script will:

1. Detect or install `pybind11`.
2. Search common OCCT installation directories.
3. Configure with `Visual Studio 17 2022` or `Ninja Multi-Config`.
4. Build a **Release** configuration.
5. Copy the `.pyd` module to `native/windows-x64/`.

### With Custom Python Path

```powershell
.\build_windows.ps1 -PythonExecutable "C:\Python311\python.exe"
```

### With Custom OCCT Root

```powershell
$env:OCCT_ROOT = "C:\OpenCASCADE"
.\build_windows.ps1
```

### Using Visual Studio GUI

If you prefer the IDE:

```powershell
cmake -S . -B build -G "Visual Studio 17 2022" -A x64 `
  -DPython_EXECUTABLE=(py -3.11) `
  -Dpybind11_DIR=(py -3.11 -m pybind11 --cmakedir) `
  -DHIPPO_PLATFORM_FOLDER=windows-x64
```

Open `native/build/hippo_occ_core.sln` in Visual Studio and build the `Release` configuration.

### Notes

- **MinGW** is listed as a future target in the PowerShell script but is currently experimental.
- The resulting binary is `hippo_occ_core.pyd` (a Python extension DLL).
- Make sure to build with the **same Python version** that ships with Blender to avoid ABI mismatches.

---

## FreeBSD

### Quick Start

```bash
cd native
./build_freebsd.sh
```

The script searches for Python under `/usr/local/bin/python3*` and auto-detects OCCT in `/usr/local`.

### Manual CMake

```bash
cd native
rm -rf build
cmake -S . -B build -G Ninja \
  -DPython_EXECUTABLE=/usr/local/bin/python3.11 \
  -Dpybind11_DIR=$(/usr/local/bin/python3.11 -m pybind11 --cmakedir) \
  -DHIPPO_PLATFORM_FOLDER=freebsd-x64
cmake --build build
```

### Notes

- Install prerequisites: `pkg install cmake ninja py311-pybind11 opencascade`
- The legacy `opennurbs/makefile` also works on FreeBSD with `gmake`.

---

## OpenBSD

### Quick Start

```bash
cd native
./build_openbsd.sh
```

The script searches for Python under `/usr/local/bin/python3*` and auto-detects OCCT in `/usr/local`.

### Manual CMake

```bash
cd native
rm -rf build
cmake -S . -B build -G Ninja \
  -DPython_EXECUTABLE=/usr/local/bin/python3.11 \
  -Dpybind11_DIR=$(/usr/local/bin/python3.11 -m pybind11 --cmakedir) \
  -DHIPPO_PLATFORM_FOLDER=openbsd-x64
cmake --build build
```

### Notes

- Install prerequisites: `pkg_add cmake ninja py3-pybind11 opencascade`
- OpenBSD bundles `uuid` support in `libc`, so no extra UUID library flags are needed.
- The legacy `opennurbs/makefile` also works on OpenBSD with `gmake`.

---

## Bundling OCCT (Self-Contained ZIP)

To distribute Hippo3D without requiring users to install OCCT, you can bundle the OCCT shared libraries alongside the native module.

After building:

```bash
cd native
python bundle_occt.py
```

The script:

1. Discovers the OCCT libraries your built module links to (`ldd` / `otool -L` / `dumpbin`).
2. Copies them into `native/<platform>/`.
3. Resolves symbolic links so the shipped files are real binaries.

### Platform-Specific Library Names

| OS | Libraries |
|---|---|
| Linux | `libTKernel.so.7`, `libTKMath.so.7`, … |
| macOS | `libTKernel.dylib`, `libTKMath.dylib`, … |
| Windows | `TKernel.dll`, `TKMath.dll`, … |
| FreeBSD | `libTKernel.so.7`, … |
| OpenBSD | `libTKernel.so.X.Y`, … |

> **Future improvement:** The bundling script currently requires manual execution. A future release will integrate this as an automatic post-build step and also handle transitive dependencies (TBB, FreeImage, etc.) more robustly.

---

## Packaging for Blender Extensions

Once you have built and optionally bundled OCCT, the folder layout for a platform-specific release ZIP is:

```
Hippo3D/
├── blender_manifest.toml
├── main.py
├── kernels/
│   └── occ_loader.py
├── native/
│   ├── linux-x64/
│   │   ├── hippo_occ_core.so
│   │   └── (libTK*.so*  ← if bundled)
│   ├── macos-arm64/
│   │   ├── hippo_occ_core.so
│   │   └── (libTK*.dylib  ← if bundled)
│   ├── macos-x64/
│   ├── windows-x64/
│   │   ├── hippo_occ_core.pyd
│   │   └── (TK*.dll  ← if bundled)
│   ├── freebsd-x64/
│   └── openbsd-x64/
└── ...
```

The add-on loader (`kernels/occ_loader.py`) picks the correct folder at runtime based on `platform.system()` and `platform.machine()`.

---

## Troubleshooting

### "Could not find OpenCASCADE (OCCT)"

Set the environment variable pointing to your OCCT installation root:

```bash
export OCCT_ROOT=/usr/local/opt/opencascade   # macOS Homebrew example
```

### "pybind11 is not installed for this Python"

```bash
python -m pip install pybind11
```

### CMake cannot find Python / pybind11

Pass the paths explicitly:

```bash
cmake -S . -B build \
  -DPython_EXECUTABLE=/full/path/to/python \
  -Dpybind11_DIR=$(/full/path/to/python -m pybind11 --cmakedir)
```

### Windows: "cannot open file 'TKernel.lib'"

Ensure you are building inside a **Visual Studio Developer Command Prompt** so CMake can find the MSVC toolchain and the OCCT library paths.

### macOS: `clang` warnings about inconsistent missing overrides

These are harmless warnings from OpenNURBS headers. The build scripts suppress them via `-Wno-inconsistent-missing-override`.

### FreeBSD / OpenBSD: `ldd` not found

Install it via the base system or packages (`pkg install elfutils`). The bundling script gracefully skips if `ldd` is absent.

---

## Contributing

If you encounter build issues on a platform not covered here, please open an issue with:

- OS version
- Compiler version
- CMake version
- Python version
- Relevant error logs

---

*Happy building!*
