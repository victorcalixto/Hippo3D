# Compiling Hippo3D Native Module on Linux

This guide is a step-by-step recipe for building the `hippo_occ_core` native extension on Linux, bundling it with all runtime dependencies, and producing an installable Blender add-on ZIP.

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
|-------------|----------------|--------------|
| **Blender 4.2+ for Linux** | The native module must be built against Blender's bundled Python ABI. | Open Blender and check the version in the splash screen or `Help > About`. |
| **CMake >= 3.20** | Generates the build system. | `cmake --version` |
| **Ninja** (recommended) | Fast build tool. | `ninja --version` |
| **GCC or Clang** | C++17 compiler. | `g++ --version` or `clang++ --version` |
| **OpenCASCADE 7.x+ (or build 8.0.0 locally)** | CAD geometry kernel used by the module. | Check that `/usr/include/opencascade/BRepPrimAPI_MakeBox.hxx` exists, or build locally with `./build_occt.sh`. |
| **pybind11** | C++/Python binding layer. | `python -m pybind11 --cmakedir` should print a path. |
| **Git submodules** | The `opennurbs` library is included as a submodule. | `git submodule update --init --recursive` |

### 1. Install System Dependencies

#### Debian / Ubuntu

```bash
sudo apt-get update
sudo apt-get install -y \
    cmake ninja-build \
    build-essential \
    python3-dev python3-pip \
    libocct-dev occt-draw \
    git
```

#### Fedora / RHEL

```bash
sudo dnf install -y \
    cmake ninja-build \
    gcc-c++ \
    python3-devel python3-pip \
    opencascade-devel \
    git
```

#### Arch Linux

```bash
sudo pacman -S \
    cmake ninja \
    gcc \
    python python-pip \
    opencascade \
    git
```

### 2. Initialize Git Submodules

```bash
cd /path/to/Hippo3D
git submodule update --init --recursive
```

### 3. Install pybind11

```bash
python3 -m pip install pybind11
```

Or if you use Blender's bundled Python (recommended for ABI compatibility):

```bash
/path/to/blender/4.2/python/bin/python -m pip install pybind11
```

### 4. (Optional) Build OCCT 8.0.0 Locally

If your system only provides OCCT 7.x or you want a self-contained build:

```bash
cd native
./build_occt.sh
```

This downloads and builds OCCT 8.0.0 into `native/third_party/occt-8.0.0/`. The build scripts will auto-detect it.

---

## One-Line Build

From the `native` folder:

```bash
cd native
./build_linux.sh
```

If you want to use Blender's bundled Python (best ABI match):

```bash
cd native
PYTHON_BIN="/path/to/blender/4.2/python/bin/python3.11" ./build_linux.sh
```

---

## Step-by-Step Build

If the one-line build fails or you want to understand each step:

### Step 1: Verify Python and pybind11

```bash
python3 --version
python3 -m pybind11 --cmakedir
```

The second command should print a path ending in `share/cmake/pybind11`. If not:

```bash
python3 -m pip install pybind11
```

### Step 2: Verify OCCT

```bash
ls /usr/include/opencascade/BRepPrimAPI_MakeBox.hxx
```

If missing and you haven't built locally yet:

```bash
./build_occt.sh
```

### Step 3: Configure CMake

```bash
cd native
rm -rf build

cmake -S . -B build -G Ninja \
    -DPython_EXECUTABLE=$(which python3) \
    -Dpybind11_DIR=$(python3 -m pybind11 --cmakedir) \
    -DHIPPO_PLATFORM_FOLDER=linux-x64
```

If you built OCCT locally:

```bash
export OCCT_ROOT="$(pwd)/third_party/occt-8.0.0"
cmake -S . -B build -G Ninja \
    -DPython_EXECUTABLE=$(which python3) \
    -Dpybind11_DIR=$(python3 -m pybind11 --cmakedir) \
    -DHIPPO_PLATFORM_FOLDER=linux-x64
```

### Step 4: Build

```bash
cmake --build build
```

### Step 5: Copy the module

```bash
mkdir -p linux-x64
cp build/hippo_occ_core*.so linux-x64/hippo_occ_core.so
```

### Step 6: Bundle OCCT and 3rdparty shared libraries

```bash
python3 bundle_occt.py --platform linux-x64
```

This discovers all OCCT and transitive dependencies (`ldd` recursive) and copies them into `native/linux-x64/`, skipping standard system C/C++ runtimes.

### Step 7: Test

```bash
python3 -c "
import sys
sys.path.append('native/linux-x64')
import hippo_occ_core
print(hippo_occ_core.make_box_mesh(10, 10, 10).keys())
"
```

Expected output:

```text
dict_keys(['vertices', 'faces', 'shape_id', 'edges'])
```

---

## What the Build Script Does

Running `build_linux.sh` performs the equivalent of Steps 3-6 automatically:

1. Detects Python (honours `PYTHON_BIN` env var, supports `pyenv`).
2. Verifies `pybind11` and installs it if missing.
3. Configures CMake with `Ninja` and `HIPPO_PLATFORM_FOLDER=linux-x64`.
4. Builds `hippo_occ_core*.so`.
5. Copies the `.so` to `native/linux-x64/hippo_occ_core.so`.
6. Runs `bundle_occt.py` to copy all OCCT and transitive shared libraries (unless `BUNDLE_AUTO=0`).

---

## Where the Outputs Go

```text
Hippo3D/
├── native/
│   ├── build/                            # CMake build tree (can be deleted)
│   └── linux-x64/                        # Self-contained native module folder
│       ├── hippo_occ_core.so
│       ├── libTKernel.so.8.0
│       ├── libTKMath.so.8.0
│       ├── ... (all other libTK*.so*)
│       ├── libfreetype.so.6
│       ├── libpng16.so.16
│       └── ... (transitive 3rdparty .so)
└── ...
```

The `kernels/occ_loader.py` script in the add-on automatically adds this folder to `LD_LIBRARY_PATH` when the add-on starts, so end users do not need to configure anything.

---

## Bundling the Add-on for End Users

After building, run the packaging script from the project root:

```bash
cd ..
./package_addon.sh
```

This creates a self-contained ZIP such as:

```text
dist/Hippo3D-linux-x64.zip
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
│   └── linux-x64/              # module + all runtime .so files
└── ...
```

End users install this ZIP directly in Blender via **Edit > Preferences > Add-ons > Install from Disk**. No OCCT, Python, or compiler setup is required on their machine.

### Packaging Options

```bash
# Default (linux-x64)
./package_addon.sh

# Custom platform (e.g. freebsd-x64)
./package_addon.sh --platform freebsd-x64

# Custom output name
./package_addon.sh --zip-name Hippo3D-v0.2.0-linux

# Custom output directory
./package_addon.sh --output-dir release
```

---

## Installing the Add-on in Blender

1. In Blender, go to **Edit > Preferences > Add-ons**.
2. Click **Install from Disk...**.
3. Select the generated `Hippo3D-linux-x64.zip`.
4. Enable the **Hippo3D** add-on in the list.
5. The add-on will load `native/linux-x64/hippo_occ_core.so` and all bundled libraries automatically.

---

## Troubleshooting

### "Could not find OpenCASCADE (OCCT)"

Set the environment variable pointing to your OCCT installation root:

```bash
export OCCT_ROOT=/usr/lib/opencascade   # Debian/Ubuntu example
export OCCT_ROOT=/usr/local             # Fedora example
./build_linux.sh
```

Or build OCCT locally:

```bash
./build_occt.sh
```

### "pybind11 is not installed for this Python"

```bash
python3 -m pip install pybind11
```

### CMake cannot find Python / pybind11

Pass the paths explicitly:

```bash
cmake -S . -B build \
    -DPython_EXECUTABLE=/full/path/to/python3 \
    -Dpybind11_DIR=$(/full/path/to/python3 -m pybind11 --cmakedir)
```

### Module fails to import with `cannot open shared object file`

Run `ldd` on the module to see which library is missing:

```bash
ldd native/linux-x64/hippo_occ_core.so
```

Then re-run `bundle_occt.py` or install the missing system package.

### `bundle_occt.py` copies system libraries I don't want

The script skips standard C/C++ runtimes (`libc`, `libm`, `libstdc++`, `libgcc_s`, etc.). If it still bundles something you consider system-level, set `LD_LIBRARY_PATH` to only your OCCT directory before running it:

```bash
export LD_LIBRARY_PATH=/path/to/your/occt/lib
python3 bundle_occt.py --platform linux-x64
```

---

## Summary

```bash
cd native
./build_linux.sh
cd ..
./package_addon.sh
```

You now have a ready-to-install Blender add-on at `dist/Hippo3D-linux-x64.zip`.
