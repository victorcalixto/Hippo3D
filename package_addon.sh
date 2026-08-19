#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR" && pwd)"

PLATFORM="${PLATFORM:-linux-x64}"
VERSION="${VERSION:-0.3.0}"
FLAVOR="${FLAVOR:-wip}"

OUTPUT_DIR="${OUTPUT_DIR:-$PROJECT_ROOT/dist}"
ZIP_NAME="Hippo3D-v${VERSION}-${FLAVOR}-${PLATFORM}.zip"
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

# Copy native module and bundled shared libraries
cp "$NATIVE_DIR"/hippo_occ_core*.so "$OUTPUT_DIR/Hippo3D/native/$PLATFORM/"
cp "$NATIVE_DIR"/*.so* "$OUTPUT_DIR/Hippo3D/native/$PLATFORM/" 2>/dev/null || true

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
