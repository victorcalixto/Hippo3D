#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# FreeBSD Python is typically under /usr/local/bin/python3.x
PYTHON_BIN="${PYTHON_BIN:-python3}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    # Fallback to common FreeBSD paths
    for P in /usr/local/bin/python3.11 /usr/local/bin/python3.10 /usr/local/bin/python3.9 /usr/local/bin/python3; do
        if [ -x "$P" ]; then
            PYTHON_BIN="$P"
            break
        fi
    done
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

PLATFORM_FOLDER="freebsd-x64"

# Common FreeBSD OCCT install location
if [ -z "${OCCT_ROOT:-}" ] && [ -d "/usr/local/include/opencascade" ]; then
    export OCCT_ROOT="/usr/local"
    echo "Auto-detected FreeBSD OCCT: $OCCT_ROOT"
fi

cmake -S . -B build -G Ninja \
    -DPython_EXECUTABLE="$PYTHON_BIN" \
    -DPYTHON_EXECUTABLE="$PYTHON_BIN" \
    -Dpybind11_DIR="$PYBIND11_DIR" \
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
echo "Extension module:"
echo "  $SCRIPT_DIR/$PLATFORM_FOLDER/hippo_occ_core.so"
echo
echo "Test in Blender:"
echo "  import sys"
echo "  sys.path.append('$SCRIPT_DIR/build')"
echo "  import hippo_occ_core"
echo "  print(hippo_occ_core.make_box_mesh(10, 10, 10).keys())"
