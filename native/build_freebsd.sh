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

PLATFORM_FOLDER="${HIPPO_PLATFORM_FOLDER:-freebsd-x64}"

# Common FreeBSD OCCT install location
if [ -z "${OCCT_ROOT:-}" ] && [ -d "/usr/local/include/opencascade" ]; then
    export OCCT_ROOT="/usr/local"
    echo "Auto-detected FreeBSD OCCT: $OCCT_ROOT"
fi

# OpenNURBS assumes any non-Apple/Android/WASM platform has fcloseall().
# FreeBSD/OpenBSD/NetBSD do not, so treat them the same as Apple/Android/WASM
# for ON::CloseAllFiles().
ON_DEFINES="$SCRIPT_DIR/third_party/opennurbs/opennurbs_defines.cpp"
if [ -f "$ON_DEFINES" ]; then
    sed -i.bak \
        -e 's/#elif defined(ON_RUNTIME_APPLE) || defined(ON_RUNTIME_ANDROID) || defined(ON_RUNTIME_WASM)/#elif defined(ON_RUNTIME_APPLE) || defined(ON_RUNTIME_ANDROID) || defined(ON_RUNTIME_WASM) || defined(__FreeBSD__) || defined(__OpenBSD__) || defined(__NetBSD__)/' \
        "$ON_DEFINES"
    rm -f "$ON_DEFINES.bak"
fi

cmake -S . -B build -G Ninja \
    -DPython_EXECUTABLE="$PYTHON_BIN" \
    -DPYTHON_EXECUTABLE="$PYTHON_BIN" \
    -Dpybind11_DIR="$PYBIND11_DIR" \
    -DHIPPO_PLATFORM_FOLDER="$PLATFORM_FOLDER"

cmake --build build

MODULE_PATH="$(find build -maxdepth 1 -name 'hippo_occ_core*.so' | sort | tail -n 1)"

if [ -z "$MODULE_PATH" ]; then
    echo "Build finished, but hippo_occ_core*.so was not found in native/build."
    exit 1
fi

mkdir -p "$PLATFORM_FOLDER"
cp "$MODULE_PATH" "$PLATFORM_FOLDER/"

# Clean up stale plain-name .so to prevent ABI mismatch
rm -f "$PLATFORM_FOLDER/hippo_occ_core.so"

# ---------------------------------------------------------------------------
# Auto-bundle OCCT libraries for standalone distribution
# ---------------------------------------------------------------------------
BUNDLE_AUTO="${BUNDLE_AUTO:-1}"
if [ "$BUNDLE_AUTO" = "1" ]; then
    echo
    echo "Auto-bundling OCCT shared libraries..."
    python3 "$SCRIPT_DIR/bundle_occt.py" --platform "$PLATFORM_FOLDER" || echo "Warning: bundle_occt.py failed. Continuing anyway."
fi

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
