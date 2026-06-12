cmake_minimum_required(VERSION 3.20)

project(hippo_occ_core LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# ---------------------------------------------------------------------------
# Prefer locally built OCCT 8.0.0 over system OCCT
# ---------------------------------------------------------------------------
set(LOCAL_OCCT "${CMAKE_CURRENT_SOURCE_DIR}/third_party/occt-8.0.0")
if(EXISTS "${LOCAL_OCCT}/lib/cmake/opencascade/OpenCASCADEConfig.cmake")
    list(APPEND CMAKE_PREFIX_PATH "${LOCAL_OCCT}")
    set(OpenCASCADE_DIR "${LOCAL_OCCT}/lib/cmake/opencascade")
    message(STATUS "Using local OCCT 8.0.0: ${LOCAL_OCCT}")
else()
    message(STATUS "Local OCCT 8.0.0 not found, falling back to system OCCT")
endif()

# ---------------------------------------------------------------------------
# Allow our custom Find modules to be located
# ---------------------------------------------------------------------------
list(APPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_SOURCE_DIR}/cmake")

# Use modern FindPython instead of deprecated FindPythonInterp/FindPythonLibs
set(PYBIND11_FINDPYTHON ON)
find_package(pybind11 REQUIRED)
find_package(OpenCASCADE REQUIRED)

# ---------------------------------------------------------------------------
# OpenNURBS — use static library.
# OpenNURBS is built with a prefixed internal zlib (z_inflate, z_deflate, etc.).
# The system libz.so exports the unprefixed names (inflate, deflate, ...), so
# -lz alone does NOT resolve the z_* symbols.
# Therefore we must also link the OpenNURBS built-in zlib static archive.
# ---------------------------------------------------------------------------
set(OPENNURBS_DIR "${CMAKE_SOURCE_DIR}/third_party/opennurbs")

find_library(OPENNURBS_STATIC_LIB
    NAMES opennurbsStatic
    PATHS ${OPENNURBS_DIR}/build
    NO_DEFAULT_PATH
)
if(NOT OPENNURBS_STATIC_LIB)
    message(FATAL_ERROR "OpenNURBS static library not found in ${OPENNURBS_DIR}/build")
endif()

set(OPENNURBS_ZLIB_A "${OPENNURBS_DIR}/build/zlib/${CMAKE_STATIC_LIBRARY_PREFIX}zlib${CMAKE_STATIC_LIBRARY_SUFFIX}")
if(NOT EXISTS ${OPENNURBS_ZLIB_A})
    message(FATAL_ERROR "OpenNURBS built-in zlib archive not found: ${OPENNURBS_ZLIB_A}")
endif()

set(OPENNURBS_ANDROID_UUID_A "${OPENNURBS_DIR}/build/android_uuid/${CMAKE_STATIC_LIBRARY_PREFIX}android_uuid${CMAKE_STATIC_LIBRARY_SUFFIX}")
if(NOT EXISTS ${OPENNURBS_ANDROID_UUID_A})
    message(FATAL_ERROR "OpenNURBS android_uuid archive not found: ${OPENNURBS_ANDROID_UUID_A}")
endif()

# ---------------------------------------------------------------------------
# System zlib
# ---------------------------------------------------------------------------
find_package(ZLIB REQUIRED)

# ---------------------------------------------------------------------------
# Platform-specific adjustments
# ---------------------------------------------------------------------------
if(WIN32)
    # Disable iterator-debug assertion mismatch when linking release-mode OCCT
    # against a debug build (common with prebuilt OCCT on Windows).
    add_compile_definitions($<$<CONFIG:Debug>:_ITERATOR_DEBUG_LEVEL=0>)
endif()

# ---------------------------------------------------------------------------
# PyBind11 module
# ---------------------------------------------------------------------------
pybind11_add_module(hippo_occ_core
    src/hippo_occ_core.cpp
    src/occ_mesh.cpp
    src/occ_primitives.cpp
    src/occ_registry.cpp
    src/occ_step.cpp
    src/occ_3dm.cpp
    src/occ_curves.cpp
    src/occ_surface_ops.cpp
    src/occ_boolean.cpp
    src/occ_trim.cpp
)

target_include_directories(hippo_occ_core PRIVATE
    include
    ${OPENNURBS_DIR}
)

target_link_libraries(hippo_occ_core PRIVATE
    OpenCASCADE::OpenCASCADE
    ${OPENNURBS_STATIC_LIB}
    ${OPENNURBS_ZLIB_A}
    ${OPENNURBS_ANDROID_UUID_A}
    ZLIB::ZLIB
)

# ---------------------------------------------------------------------------
# RPATH / RUNPATH so the built module finds OCCT when bundled.
# ---------------------------------------------------------------------------
if(APPLE)
    set_target_properties(hippo_occ_core PROPERTIES
        INSTALL_RPATH "@loader_path"
        BUILD_WITH_INSTALL_RPATH TRUE
    )
elseif(UNIX)
    set_target_properties(hippo_occ_core PROPERTIES
        INSTALL_RPATH "$ORIGIN"
        BUILD_WITH_INSTALL_RPATH TRUE
    )
endif()

# ---------------------------------------------------------------------------
# Platform-specific linker options
# ---------------------------------------------------------------------------
if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
    # -Bsymbolic-functions: resolve function symbols locally within this DSO.
    # --exclude-libs,ALL: keep internal archive symbols from leaking into the dynamic table.
    target_link_options(hippo_occ_core PRIVATE
        "LINKER:-Bsymbolic-functions"
        "LINKER:--exclude-libs,ALL"
    )
elseif(CMAKE_SYSTEM_NAME STREQUAL "Darwin")
    # macOS: hide symbols from static archives, strip dead code
    target_link_options(hippo_occ_core PRIVATE
        "LINKER:-dead_strip_dylibs"
        "LINKER:-undefined,dynamic_lookup"
    )
elseif(CMAKE_SYSTEM_NAME MATCHES "^(FreeBSD|OpenBSD)$")
    # BSDs use GNU-compatible ld
    target_link_options(hippo_occ_core PRIVATE
        "LINKER:-Bsymbolic-functions"
        "LINKER:--exclude-libs,ALL"
    )
elseif(WIN32 AND MSVC)
    # MSVC: suppress harmless iterator debug warnings, enable big obj
    target_compile_options(hippo_occ_core PRIVATE /bigobj)
    target_link_options(hippo_occ_core PRIVATE /INCREMENTAL:NO)
elseif(WIN32 AND CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
    # Windows MinGW (best-effort / future-proofing)
    target_link_options(hippo_occ_core PRIVATE
        "LINKER:-Bsymbolic-functions"
        "LINKER:--exclude-libs,ALL"
    )
endif()

# ---------------------------------------------------------------------------
# Post-build: copy resulting module into the platform output folder
# ---------------------------------------------------------------------------
set(HIPPO_OUTDIR "${CMAKE_SOURCE_DIR}/${HIPPO_PLATFORM_FOLDER}")
if(HIPPO_PLATFORM_FOLDER)
    add_custom_command(TARGET hippo_occ_core POST_BUILD
        COMMAND ${CMAKE_COMMAND} -E make_directory "${HIPPO_OUTDIR}"
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
            "$<TARGET_FILE:hippo_occ_core>"
            "${HIPPO_OUTDIR}/"
        COMMENT "Copying $<TARGET_FILE_NAME:hippo_occ_core> to ${HIPPO_OUTDIR}/"
    )
endif()
