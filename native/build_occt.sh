#!/usr/bin/env bash
# Build OCCT 8.0.0 from source for Hippo3D (dev-native-occt-4)
# This script is idempotent: if OCCT is already built, it skips.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OCCT_VERSION="8.0.0"
OCCT_SRC_DIR="${SCRIPT_DIR}/third_party/occt-8.0.0-src"
OCCT_INSTALL_DIR="${SCRIPT_DIR}/third_party/occt-8.0.0"
OCCT_BUILD_DIR="${OCCT_SRC_DIR}/build"
OCCT_TARBALL="/tmp/opencascade-${OCCT_VERSION}.tar.gz"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
info()  { echo "[OCCT-BUILD] $*"; }
warn()  { echo "[OCCT-BUILD] WARNING: $*" >&2; }
error() { echo "[OCCT-BUILD] ERROR: $*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Check if already installed
# ---------------------------------------------------------------------------
if [ -f "${OCCT_INSTALL_DIR}/include/opencascade/Standard_Version.hxx" ]; then
    INSTALLED_VERSION=$(grep "OCC_VERSION_COMPLETE" "${OCCT_INSTALL_DIR}/include/opencascade/Standard_Version.hxx" | head -1 | sed 's/.*"\(.*\)".*/\1/')
    if [ "${INSTALLED_VERSION}" = "${OCCT_VERSION}" ]; then
        info "OCCT ${OCCT_VERSION} already installed at ${OCCT_INSTALL_DIR}. Skipping build."
        exit 0
    fi
    warn "Installed version (${INSTALLED_VERSION}) does not match ${OCCT_VERSION}. Rebuilding..."
fi

# ---------------------------------------------------------------------------
# Download source
# ---------------------------------------------------------------------------
if [ ! -f "${OCCT_TARBALL}" ]; then
    info "Downloading OpenCASCADE ${OCCT_VERSION} source..."
    curl -L -o "${OCCT_TARBALL}" \
        "https://github.com/Open-Cascade-SAS/OCCT/archive/refs/tags/V8_0_0.tar.gz"
else
    info "Using cached tarball: ${OCCT_TARBALL}"
fi

# ---------------------------------------------------------------------------
# Extract source
# ---------------------------------------------------------------------------
if [ ! -d "${OCCT_SRC_DIR}" ]; then
    info "Extracting source to ${OCCT_SRC_DIR}..."
    mkdir -p "${SCRIPT_DIR}/third_party"
    tar -xzf "${OCCT_TARBALL}" -C "${SCRIPT_DIR}/third_party"
    mv "${SCRIPT_DIR}/third_party/OCCT-8_0_0" "${OCCT_SRC_DIR}"
else
    info "Source already extracted at ${OCCT_SRC_DIR}"
fi

# ---------------------------------------------------------------------------
# Configure
# ---------------------------------------------------------------------------
info "Configuring OCCT ${OCCT_VERSION} (minimal build)..."

mkdir -p "${OCCT_BUILD_DIR}"
cd "${OCCT_BUILD_DIR}"

cmake -S "${OCCT_SRC_DIR}" -B "${OCCT_BUILD_DIR}" -G Ninja \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="${OCCT_INSTALL_DIR}" \
    -DBUILD_LIBRARY_TYPE=Shared \
    -DBUILD_MODULE_Draw=OFF \
    -DBUILD_MODULE_Visualization=OFF \
    -DBUILD_MODULE_DataExchange=ON \
    -DBUILD_MODULE_FoundationClasses=ON \
    -DBUILD_MODULE_ModelingData=ON \
    -DBUILD_MODULE_ModelingAlgorithms=ON \
    -DBUILD_MODULE_ShapeHealing=ON \
    -DBUILD_MODULE_Mesh=ON \
    -DBUILD_ADDITIONAL_TOOLKITS="" \
    -DBUILD_DOC_Overview=OFF \
    -DBUILD_DOC_RefMan=OFF \
    -DUSE_TKCAF=OFF \
    -DUSE_TKOpenGl=OFF \
    -DUSE_VTK=OFF \
    -DBUILD_ENABLE_FPE_SIGNAL_HANDLER=OFF \
    -DBUILD_USE_PCH=OFF \
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
    -DCMAKE_CXX_STANDARD=17

# ---------------------------------------------------------------------------
# Build & install
# ---------------------------------------------------------------------------
NPROC=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)

info "Building OCCT ${OCCT_VERSION} with ${NPROC} parallel jobs..."
cmake --build "${OCCT_BUILD_DIR}" --parallel "${NPROC}"

info "Installing OCCT ${OCCT_VERSION} to ${OCCT_INSTALL_DIR}..."
cmake --install "${OCCT_BUILD_DIR}"

# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------
if [ -f "${OCCT_INSTALL_DIR}/include/opencascade/Standard_Version.hxx" ]; then
    info "SUCCESS: OCCT ${OCCT_VERSION} installed at ${OCCT_INSTALL_DIR}"
    echo ""
    echo "  Include: ${OCCT_INSTALL_DIR}/include/opencascade/"
    echo "  Library: ${OCCT_INSTALL_DIR}/lib/"
    echo ""
    echo "To use in Hippo3D build:"
    echo "  export OCCT_ROOT=${OCCT_INSTALL_DIR}"
    echo "  ./build_linux.sh"
else
    error "Installation failed: Standard_Version.hxx not found"
fi
