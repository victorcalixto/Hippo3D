#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"

# Target macOS architecture and deployment target.
# Defaults to the native architecture.  Set OSX_ARCH explicitly to cross-compile
# (for example x86_64 on an Apple Silicon machine in CI).
OSX_ARCH="${OSX_ARCH:-$(uname -m)}"
DEPLOYMENT_TARGET="${DEPLOYMENT_TARGET:-13.0}"

if [ "$OSX_ARCH" = "arm64" ]; then
    PLATFORM_FOLDER="${PLATFORM_FOLDER:-macos-arm64}"
else
    PLATFORM_FOLDER="${PLATFORM_FOLDER:-macos-x86_64}"
fi

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

echo "Building for macOS architecture: $OSX_ARCH (deployment target: $DEPLOYMENT_TARGET)"

# Attempt to auto-detect a locally-built OCCT 8.0.0 if OCCT_ROOT is not set.
if [ -z "${OCCT_ROOT:-}" ]; then
    LOCAL_OCCT="${SCRIPT_DIR}/third_party/occt-8.0.0-${OSX_ARCH}"
    if [ -d "${LOCAL_OCCT}/lib/cmake/opencascade" ]; then
        export OCCT_ROOT="${LOCAL_OCCT}"
        echo "Auto-detected local OCCT 8.0.0: $OCCT_ROOT"
    else
        # Fall back to Homebrew OCCT (usually 7.x) for local development.
        if [ -d "/opt/homebrew/opt/opencascade" ]; then
            export OCCT_ROOT="/opt/homebrew/opt/opencascade"
            echo "Auto-detected Homebrew OCCT (Apple Silicon): $OCCT_ROOT"
        elif [ -d "/usr/local/opt/opencascade" ]; then
            export OCCT_ROOT="/usr/local/opt/opencascade"
            echo "Auto-detected Homebrew OCCT (Intel): $OCCT_ROOT"
        fi
    fi
fi

rm -rf build

cmake -S . -B build \
    -DPython_EXECUTABLE="$PYTHON_BIN" \
    -DPYTHON_EXECUTABLE="$PYTHON_BIN" \
    -Dpybind11_DIR="$PYBIND11_DIR" \
    -DCMAKE_OSX_ARCHITECTURES="$OSX_ARCH" \
    -DCMAKE_OSX_DEPLOYMENT_TARGET="$DEPLOYMENT_TARGET" \
    -DHIPPO_PLATFORM_FOLDER="$PLATFORM_FOLDER" \
    -DOCCT_ROOT="${OCCT_ROOT:-}" \
    -DCMAKE_POLICY_VERSION_MINIMUM=3.5

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
echo "  sys.path.append('$SCRIPT_DIR/$PLATFORM_FOLDER')"
echo "  import hippo_occ_core"
echo "  print(hippo_occ_core.make_box_mesh(10, 10, 10).keys())"
