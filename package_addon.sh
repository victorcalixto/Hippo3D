#!/usr/bin/env bash
#
# Package Hippo3D as a self-contained Blender add-on ZIP for Linux.
#
# Usage:
#     ./package_addon.sh [--platform PLATFORM] [--output-dir DIR] [--zip-name NAME]
#
# This collects the Python add-on files and the pre-built native module folder
# (including all bundled OCCT shared libraries) into a ZIP file that can be
# installed directly from Blender:
#
#     Edit > Preferences > Add-ons > Install from Disk...
#
# The output is written to the dist/ folder at the project root.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
PLATFORM="linux-x64"
OUTPUT_DIR="dist"
ZIP_NAME=""

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case $1 in
        --platform)
            PLATFORM="$2"
            shift 2
            ;;
        --output-dir)
            OUTPUT_DIR="$2"
            shift 2
            ;;
        --zip-name)
            ZIP_NAME="$2"
            shift 2
            ;;
        -h|--help)
            cat <<'EOF'
Usage: ./package_addon.sh [OPTIONS]

Options:
  --platform PLATFORM   Target platform folder under native/ (default: linux-x64)
  --output-dir DIR      Output directory for the ZIP (default: dist)
  --zip-name NAME       Base name for the ZIP file (without .zip)
  -h, --help            Show this help message
EOF
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

# ---------------------------------------------------------------------------
# Validate platform folder
# ---------------------------------------------------------------------------
NATIVE_DIR="$SCRIPT_DIR/native/$PLATFORM"
if [[ ! -d "$NATIVE_DIR" ]]; then
    echo "Error: Native module folder not found: $NATIVE_DIR"
    echo "Please build first with native/build_linux.sh"
    exit 1
fi

if ! ls "$NATIVE_DIR"/hippo_occ_core*.so >/dev/null 2>&1; then
    echo "Error: No hippo_occ_core module found in $NATIVE_DIR. Please build first."
    exit 1
fi

if [[ -z "$ZIP_NAME" ]]; then
    ZIP_NAME="Hippo3D-$PLATFORM"
fi

OUT_DIR="$SCRIPT_DIR/$OUTPUT_DIR"
mkdir -p "$OUT_DIR"

STAGING_NAME="Hippo3D"
STAGING_ROOT="$OUT_DIR/$STAGING_NAME"

# Clean and recreate staging
rm -rf "$STAGING_ROOT"
mkdir -p "$STAGING_ROOT"

# ---------------------------------------------------------------------------
# Collect add-on Python files
# ---------------------------------------------------------------------------
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
    LICENSE
    README.md
)

for file in "${ADDON_FILES[@]}"; do
    if [[ -f "$SCRIPT_DIR/$file" ]]; then
        cp "$SCRIPT_DIR/$file" "$STAGING_ROOT/"
    fi
done

# Copy directories
for dir in kernels icons; do
    if [[ -d "$SCRIPT_DIR/$dir" ]]; then
        cp -r "$SCRIPT_DIR/$dir" "$STAGING_ROOT/"
    fi
done

# ---------------------------------------------------------------------------
# Collect native module + bundled dependencies
# ---------------------------------------------------------------------------
STAGING_NATIVE="$STAGING_ROOT/native/$PLATFORM"
mkdir -p "$STAGING_NATIVE"

# Copy the module and all bundled libraries
for f in "$NATIVE_DIR"/*; do
    if [[ -f "$f" ]]; then
        cp "$f" "$STAGING_NATIVE/"
    fi
done

# ---------------------------------------------------------------------------
# Create ZIP
# ---------------------------------------------------------------------------
ZIP_PATH="$OUT_DIR/${ZIP_NAME}.zip"
if [[ -f "$ZIP_PATH" ]]; then
    rm -f "$ZIP_PATH"
fi

# Use zip command if available, otherwise fall back to Python
if command -v zip >/dev/null 2>&1; then
    cd "$OUT_DIR"
    zip -r "${ZIP_NAME}.zip" "$STAGING_NAME" >/dev/null
    cd - >/dev/null
else
    python3 -c "
import shutil
import os
shutil.make_archive('${ZIP_PATH%.zip}', 'zip', root_dir='${OUT_DIR}', base_dir='${STAGING_NAME}')
"
fi

# Clean staging after creating archive
rm -rf "$STAGING_ROOT"

# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
SIZE_BYTES=$(stat -c%s "$ZIP_PATH" 2>/dev/null || stat -f%z "$ZIP_PATH")
SIZE_MB=$(awk "BEGIN {printf \"%.2f\", $SIZE_BYTES/1024/1024}")

echo ""
echo "Packaging complete."
echo "  ZIP: $ZIP_PATH"
echo "  Size: ${SIZE_MB} MB"
echo ""
echo "Install in Blender via:"
echo "  Edit > Preferences > Add-ons > Install from Disk..."
