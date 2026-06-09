cmake_minimum_required(VERSION 3.20)

project(hippo_occ_core LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

find_package(pybind11 REQUIRED)

find_path(OCCT_INCLUDE_DIR
    NAMES BRepPrimAPI_MakeBox.hxx
    PATHS /usr/include/opencascade
)

find_library(OCCT_TKernel TKernel)
find_library(OCCT_TKMath TKMath)
find_library(OCCT_TKG2d TKG2d)
find_library(OCCT_TKG3d TKG3d)
find_library(OCCT_TKGeomBase TKGeomBase)
find_library(OCCT_TKGeomAlgo TKGeomAlgo)
find_library(OCCT_TKBRep TKBRep)
find_library(OCCT_TKTopAlgo TKTopAlgo)
find_library(OCCT_TKPrim TKPrim)
find_library(OCCT_TKMesh TKMesh)

find_library(OCCT_TKBool TKBool)
find_library(OCCT_TKShHealing TKShHealing)
find_library(OCCT_TKFillet TKFillet)
find_library(OCCT_TKHLR TKHLR)
find_library(OCCT_TKOffset TKOffset)

# Data Exchange libraries
find_library(OCCT_TKDESTEP TKDESTEP)
find_library(OCCT_TKDEIGES TKDEIGES)
find_library(OCCT_TKXSBase TKXSBase)

# OpenNURBS — use static library.
# OpenNURBS is built with a prefixed internal zlib (z_inflate, z_deflate, etc.).
# The system libz.so exports the unprefixed names (inflate, deflate, ...), so
# -lz alone does NOT resolve the z_* symbols.
# Therefore we must also link the OpenNURBS built-in zlib static archive.
# We still load -lz last so that any unprefixed zlib calls from other code use
# the system zlib, and we use -Bsymbolic-functions / --exclude-libs,ALL to
# keep everything self-contained.
set(OPENNURBS_DIR "${CMAKE_SOURCE_DIR}/third_party/opennurbs")
find_library(OPENNURBS_STATIC_LIB opennurbsStatic
    PATHS ${OPENNURBS_DIR}/build
)
set(OPENNURBS_ZLIB_A "${OPENNURBS_DIR}/build/zlib/libzlib.a")
if(NOT EXISTS ${OPENNURBS_ZLIB_A})
    message(FATAL_ERROR "OpenNURBS built-in zlib archive not found: ${OPENNURBS_ZLIB_A}")
endif()

set(OPENNURBS_ANDROID_UUID_A "${OPENNURBS_DIR}/build/android_uuid/libandroid_uuid.a")
if(NOT EXISTS ${OPENNURBS_ANDROID_UUID_A})
    message(FATAL_ERROR "OpenNURBS android_uuid archive not found: ${OPENNURBS_ANDROID_UUID_A}")
endif()

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
    ${OCCT_INCLUDE_DIR}
    ${OPENNURBS_DIR}
)

target_link_libraries(hippo_occ_core PRIVATE
    ${OCCT_TKernel}
    ${OCCT_TKMath}
    ${OCCT_TKG2d}
    ${OCCT_TKG3d}
    ${OCCT_TKGeomBase}
    ${OCCT_TKGeomAlgo}
    ${OCCT_TKBRep}
    ${OCCT_TKTopAlgo}
    ${OCCT_TKPrim}
    ${OCCT_TKMesh}
    ${OCCT_TKBool}
    ${OCCT_TKShHealing}
    ${OCCT_TKFillet}
    ${OCCT_TKHLR}
    ${OCCT_TKOffset}
    ${OCCT_TKDESTEP}
    ${OCCT_TKDEIGES}
    ${OCCT_TKXSBase}
    ${OPENNURBS_STATIC_LIB}
    ${OPENNURBS_ZLIB_A}
    ${OPENNURBS_ANDROID_UUID_A}
    z
)

# -Bsymbolic-functions: resolve function symbols locally within this DSO.
# We include OpenNURBS's own prefixed zlib (libzlib.a) so its z_* calls resolve
# inside the DSO and are never hijacked by Blender's bundled zlib copy.
# --exclude-libs,ALL: keep internal archive symbols from leaking into the dynamic table.
set_target_properties(hippo_occ_core PROPERTIES
    LINK_FLAGS "-Wl,-Bsymbolic-functions -Wl,--exclude-libs,ALL"
)
