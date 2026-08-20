#!/usr/bin/env bash
# Delete stale release assets for the current platform+flavor, keeping only
# the asset whose Python tag matches the current build.
#
# Usage:
#   cleanup_release_assets.sh <VERSION> <FLAVOR> <PLATFORM> <PY_TAG>
#
# Example:
#   cleanup_release_assets.sh 0.3.0 wip linux-x64 311

set -euo pipefail

VERSION="${1:-}"
FLAVOR="${2:-}"
PLATFORM="${3:-}"
PY_TAG="${4:-}"

if [ -z "$VERSION" ] || [ -z "$FLAVOR" ] || [ -z "$PLATFORM" ] || [ -z "$PY_TAG" ]; then
    echo "Usage: $0 <VERSION> <FLAVOR> <PLATFORM> <PY_TAG>" >&2
    exit 1
fi

TAG="${FLAVOR}-${VERSION}"
PREFIX="Hippo3D-v${VERSION}-${FLAVOR}-${PLATFORM}-python"
CURRENT="${PREFIX}${PY_TAG}.zip"

# Ensure the release exists before trying to list assets.
if ! gh release view "$TAG" >/dev/null 2>&1; then
    echo "Release $TAG does not exist; nothing to clean up."
    exit 0
fi

echo "Cleaning up release assets for $TAG (keeping $CURRENT)"

gh release view "$TAG" --json assets -q '.assets[].name' 2>/dev/null | while read -r name; do
    case "$name" in
        "${PREFIX}"*)
            suffix="${name#"$PREFIX"}"
            if [ "$name" != "$CURRENT" ]; then
                echo "  deleting stale asset: $name"
                gh release delete-asset "$TAG" "$name" --yes </dev/null || true
            else
                echo "  keeping current asset: $name"
            fi
            ;;
        *)
            # Other platform/assets are left alone; each workflow cleans up its own.
            ;;
    esac
done
