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

cmake -S . -B build -G Ninja \
    -DPython_EXECUTABLE="$PYTHON_BIN" \
    -DPYTHON_EXECUTABLE="$PYTHON_BIN" \
    -Dpybind11_DIR="$PYBIND11_DIR" \
    -DHIPPO_PLATFORM_FOLDER="linux-x64"

cmake --build build

MODULE_PATH="$(find build -maxdepth 1 -name 'hippo_occ_core*.so' | sort | tail -n 1)"

if [ -z "$MODULE_PATH" ]; then
    echo "Build finished, but hippo_occ_core*.so was not found in native/build."
    exit 1
fi

mkdir -p linux-x64
cp "$MODULE_PATH" linux-x64/

# Clean up stale plain-name .so to prevent ABI mismatch
rm -f linux-x64/hippo_occ_core.so

# ---------------------------------------------------------------------------
# Auto-bundle OCCT libraries for standalone distribution
# ---------------------------------------------------------------------------
BUNDLE_AUTO="${BUNDLE_AUTO:-1}"
if [ "$BUNDLE_AUTO" = "1" ]; then
    echo
    echo "Auto-bundling OCCT shared libraries..."
    python3 "$SCRIPT_DIR/bundle_occt.py" --platform linux-x64 || echo "Warning: bundle_occt.py failed. Continuing anyway."
fi

echo
echo "Build complete."
echo "Development module:"
echo "  $MODULE_PATH"
echo "Extension module:"
echo "  $SCRIPT_DIR/linux-x64/hippo_occ_core.so"
echo
echo "Test in Blender:"
echo "  import sys"
echo "  sys.path.append('$SCRIPT_DIR/build')"
echo "  import hippo_occ_core"
echo "  print(hippo_occ_core.make_box_mesh(10, 10, 10).keys())"
