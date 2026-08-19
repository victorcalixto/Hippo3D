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

if [ ! -f "$NATIVE_DIR/hippo_occ_core.so" ]; then
    echo "ERROR: No hippo_occ_core.so found in $NATIVE_DIR"
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

# Copy native module
cp "$NATIVE_DIR/hippo_occ_core.so" "$OUTPUT_DIR/Hippo3D/native/$PLATFORM/"

# Bundle OCCT and 3rdparty .dylibs from the native folder
cp "$NATIVE_DIR"/*.dylib "$OUTPUT_DIR/Hippo3D/native/$PLATFORM/" 2>/dev/null || true

# Fix install names so the module and bundled dylibs find each other via @loader_path
BUNDLE_DIR="$OUTPUT_DIR/Hippo3D/native/$PLATFORM"

echo "Fixing install names in $BUNDLE_DIR ..."

# Make every bundled dylib relocatable: its own id becomes @loader_path/<name>
for lib in "$BUNDLE_DIR"/*.dylib; do
    [ -f "$lib" ] || continue
    name="$(basename "$lib")"
    install_name_tool -id "@loader_path/$name" "$lib" 2>/dev/null || true
    install_name_tool -id "@rpath/$name" "$lib" 2>/dev/null || true
done

# Rewrite library references inside the module and between bundled dylibs.
# Pipelines are wrapped in functions so a grep/awk non-zero exit does not
# trigger pipefail.
for target in "$BUNDLE_DIR"/hippo_occ_core.so "$BUNDLE_DIR"/*.dylib; do
    [ -f "$target" ] || continue
    for lib in "$BUNDLE_DIR"/*.dylib; do
        [ -f "$lib" ] || continue
        name="$(basename "$lib")"
        # Replace absolute references to this library name with @loader_path/<name>
        refs="$(otool -L "$target" 2>/dev/null | grep -E "/$name " | awk '{print $1}' || true)"
        while IFS= read -r oldref; do
            [ -n "$oldref" ] || continue
            install_name_tool -change "$oldref" "@loader_path/$name" "$target" 2>/dev/null || true
        done <<< "$refs"
    done
    # Replace @rpath references inside the module and bundled dylibs with
    # @loader_path so the bundled libraries resolve without extra DYLD setup.
    refs="$(otool -L "$target" 2>/dev/null | grep -E '@rpath/libTK[A-Za-z0-9_]+\.dylib' | awk '{print $1}' || true)"
    while IFS= read -r oldref; do
        [ -n "$oldref" ] || continue
        name="$(basename "$oldref")"
        if [ -f "$BUNDLE_DIR/$name" ]; then
            install_name_tool -change "$oldref" "@loader_path/$name" "$target" 2>/dev/null || true
        fi
    done <<< "$refs"
    # Also rewrite @rpath/libTK*.8.0.dylib references that may use a versioned
    # name different from the bundled real file (e.g. libTKernel.8.0.dylib).
    for lib in "$BUNDLE_DIR"/*.dylib; do
        [ -f "$lib" ] || continue
        soname="$(basename "$lib" | sed -E 's/\.8\.0\.0\.dylib$/.8.0.dylib/')"
        if [ -n "$soname" ]; then
            refs="$(otool -L "$target" 2>/dev/null | grep -E "@rpath/$soname " | awk '{print $1}' || true)"
            while IFS= read -r oldref; do
                [ -n "$oldref" ] || continue
                realname="$(basename "$lib")"
                install_name_tool -change "$oldref" "@loader_path/$realname" "$target" 2>/dev/null || true
            done <<< "$refs"
        fi
    done
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
