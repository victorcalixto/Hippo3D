#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

OSX_ARCH="${OSX_ARCH:-$(uname -m)}"
VERSION="${VERSION:-0.3.0}"
FLAVOR="${FLAVOR:-wip}"

if [ "$OSX_ARCH" = "arm64" ]; then
    PLATFORM="macos-arm64"
else
    PLATFORM="macos-x86_64"
fi

OUTPUT_DIR="${OUTPUT_DIR:-$PROJECT_ROOT/dist}"

if [ -n "${ZIP_BASE:-}" ]; then
    ZIP_NAME="${ZIP_BASE}.zip"
else
    ZIP_NAME="Hippo3D-v${VERSION}-${FLAVOR}-${PLATFORM}.zip"
fi
ZIP_PATH="$OUTPUT_DIR/$ZIP_NAME"

NATIVE_DIR="$SCRIPT_DIR/$PLATFORM"
if [ ! -d "$NATIVE_DIR" ]; then
    echo "ERROR: Native module folder not found: $NATIVE_DIR"
    echo "Please run build_macos.sh first."
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

# Copy native module (preserve ABI-tagged name)
cp -L "$NATIVE_DIR"/hippo_occ_core*.so "$OUTPUT_DIR/Hippo3D/native/$PLATFORM/"

# Bundle OCCT and 3rdparty .dylibs from the native folder
cp -L "$NATIVE_DIR"/*.dylib "$OUTPUT_DIR/Hippo3D/native/$PLATFORM/" 2>/dev/null || true

# Fix install names so the module and bundled dylibs find each other via @loader_path
BUNDLE_DIR="$OUTPUT_DIR/Hippo3D/native/$PLATFORM"

echo "Fixing install names in $BUNDLE_DIR ..."

# Make every bundled dylib relocatable: consistently use @loader_path/<name>
for lib in "$BUNDLE_DIR"/*.dylib; do
    [ -f "$lib" ] || continue
    name="$(basename "$lib")"
    install_name_tool -id "@loader_path/$name" "$lib" 2>/dev/null || true
done

# Rewrite library references inside the module and between bundled dylibs.
# Any @rpath/... or absolute reference to a library that is present in the
# bundle is replaced with @loader_path/<basename>.
for target in "$BUNDLE_DIR"/hippo_occ_core*.so "$BUNDLE_DIR"/*.dylib; do
    [ -f "$target" ] || continue
    while IFS= read -r ref; do
        [ -n "$ref" ] || continue
        # ref is something like "@rpath/libTKernel.8.0.dylib" or "/usr/local/lib/libTKernel.8.0.dylib"
        name="$(basename "$ref")"
        if [ -f "$BUNDLE_DIR/$name" ]; then
            install_name_tool -change "$ref" "@loader_path/$name" "$target" 2>/dev/null || true
        fi
    done <<< "$(otool -L "$target" 2>/dev/null | awk 'NR>1{print $1}' || true)"
done

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
