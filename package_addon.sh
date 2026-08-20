#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR" && pwd)"

PLATFORM="${PLATFORM:-linux-x64}"
VERSION="${VERSION:-0.3.0}"
FLAVOR="${FLAVOR:-wip}"
PYTHON_VERSION="${PYTHON_VERSION:-}"

OUTPUT_DIR="${OUTPUT_DIR:-$PROJECT_ROOT/dist}"

if [ -n "${ZIP_BASE:-}" ]; then
    ZIP_NAME="${ZIP_BASE}.zip"
elif [ -n "$PYTHON_VERSION" ]; then
    PY_TAG="${PYTHON_VERSION%%.*}${PYTHON_VERSION#*.}"
    ZIP_NAME="Hippo3D-v${VERSION}-${FLAVOR}-${PLATFORM}-python${PY_TAG}.zip"
else
    ZIP_NAME="Hippo3D-v${VERSION}-${FLAVOR}-${PLATFORM}.zip"
fi
ZIP_PATH="$OUTPUT_DIR/$ZIP_NAME"

NATIVE_DIR="$PROJECT_ROOT/native/$PLATFORM"
if [ ! -d "$NATIVE_DIR" ]; then
    echo "ERROR: Native module folder not found: $NATIVE_DIR"
    echo "Please build first."
    exit 1
fi

if [ ! -f "$NATIVE_DIR"/hippo_occ_core*.so ]; then
    echo "ERROR: No hippo_occ_core*.so found in $NATIVE_DIR"
    exit 1
fi

rm -rf "$OUTPUT_DIR/Hippo3D"
mkdir -p "$OUTPUT_DIR/Hippo3D/native/$PLATFORM"

# Collect add-on Python files
ADDON_FILES=(
    blender_manifest.toml
    __init__.py
    main.py
    common.py
    cplanes.py
    geometry.py
    registration.py
    state.py
    test_in_blender.py
    test_serpentine_bridge.py
    LICENSE
    README.md
)

for f in "${ADDON_FILES[@]}"; do
    src="$PROJECT_ROOT/$f"
    if [ -f "$src" ]; then
        cp "$src" "$OUTPUT_DIR/Hippo3D/"
    fi
done

# Copy directories that belong to the add-on
for d in kernels icons; do
    if [ -d "$PROJECT_ROOT/$d" ]; then
        cp -R "$PROJECT_ROOT/$d" "$OUTPUT_DIR/Hippo3D/"
    fi
done

# Remove stale bytecode from the build host so it cannot cause wrong-ABI .pyc
# errors inside Blender.
find "$OUTPUT_DIR/Hippo3D" -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

# Copy native module and bundled shared libraries.
# Use -L so symlinks are followed and real files land in the package.
cp -L "$NATIVE_DIR"/hippo_occ_core*.so "$OUTPUT_DIR/Hippo3D/native/$PLATFORM/"
cp -L "$NATIVE_DIR"/*.so* "$OUTPUT_DIR/Hippo3D/native/$PLATFORM/" 2>/dev/null || true

# Ensure the native module and all bundled .so files have $ORIGIN RPATH/RUNPATH
# set, because modifying LD_LIBRARY_PATH inside Blender is too late for the
# dynamic linker.  This makes the package self-contained.
if command -v patchelf >/dev/null 2>&1; then
    for lib in "$OUTPUT_DIR/Hippo3D/native/$PLATFORM"/*.so*; do
        [ -f "$lib" ] || continue
        # Only touch files that are real ELF libraries (skip symlinks which were
        # already resolved above).
        file "$lib" | grep -q "ELF" || continue
        patchelf --set-rpath '$ORIGIN' "$lib" 2>/dev/null || true
    done
fi

rm -f "$ZIP_PATH"
(cd "$OUTPUT_DIR" && zip -r "$ZIP_NAME" Hippo3D)
rm -rf "$OUTPUT_DIR/Hippo3D"

echo ""
echo "Packaging complete."
echo "  ZIP: $ZIP_PATH"
echo "  Size: $(du -h "$ZIP_PATH" | cut -f1)"
echo ""
echo "Install in Blender via:"
echo "  Edit > Preferences > Add-ons > Install from Disk..."
