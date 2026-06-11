#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python}"

if command -v pyenv >/dev/null 2>&1; then
    if [ -f ".python-version" ]; then
        PYTHON_BIN="$(pyenv which python)"
    fi
fi

echo "Using Python: $PYTHON_BIN"
"$PYTHON_BIN" --version

if ! "$PYTHON_BIN" -m pybind11 --cmakedir >/dev/null 2>&1; then
    echo "pybind11 is not installed for this Python."
    echo "Installing pybind11..."
    "$PYTHON_BIN" -m pip install --upgrade pip pybind11
fi

PYBIND11_DIR="$("$PYTHON_BIN" -m pybind11 --cmakedir)"

echo "pybind11 CMake dir: $PYBIND11_DIR"

rm -rf build

# Detect architecture (Intel vs Apple Silicon)
ARCH=$(uname -m)
if [ "$ARCH" = "arm64" ]; then
    PLATFORM_FOLDER="macos-arm64"
    OSX_ARCH="arm64"
else
    PLATFORM_FOLDER="macos-x64"
    OSX_ARCH="x86_64"
fi

echo "Building for macOS architecture: $OSX_ARCH"

# Attempt to auto-detect Homebrew OCCT prefix if not set
if [ -z "${OCCT_ROOT:-}" ]; then
    if [ -d "/opt/homebrew/opt/opencascade" ]; then
        export OCCT_ROOT="/opt/homebrew/opt/opencascade"
        echo "Auto-detected Homebrew OCCT (Apple Silicon): $OCCT_ROOT"
    elif [ -d "/usr/local/opt/opencascade" ]; then
        export OCCT_ROOT="/usr/local/opt/opencascade"
        echo "Auto-detected Homebrew OCCT (Intel): $OCCT_ROOT"
    fi
fi

cmake -S . -B build \
    -DPython_EXECUTABLE="$PYTHON_BIN" \
    -DPYTHON_EXECUTABLE="$PYTHON_BIN" \
    -Dpybind11_DIR="$PYBIND11_DIR" \
    -DCMAKE_OSX_ARCHITECTURES="$OSX_ARCH" \
    -DHIPPO_PLATFORM_FOLDER="$PLATFORM_FOLDER"

cmake --build build

MODULE_PATH="$(find build -maxdepth 1 -name 'hippo_occ_core*.so' | head -n 1)"

if [ -z "$MODULE_PATH" ]; then
    echo "Build finished, but hippo_occ_core*.so was not found in native/build."
    exit 1
fi

mkdir -p "$PLATFORM_FOLDER"
cp "$MODULE_PATH" "$PLATFORM_FOLDER/hippo_occ_core.so"

echo
echo "Build complete."
echo "Development module:"
echo "  $MODULE_PATH"
echo "Extension module:"
echo "  $SCRIPT_DIR/$PLATFORM_FOLDER/hippo_occ_core.so"
echo
echo "Test in Blender:"
echo "  import sys"
echo "  sys.path.append('$SCRIPT_DIR/build')"
echo "  import hippo_occ_core"
echo "  print(hippo_occ_core.make_box_mesh(10, 10, 10).keys())"
