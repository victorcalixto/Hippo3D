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

pybind11_add_module(hippo_occ_core
    src/hippo_occ_core.cpp
    src/occ_mesh.cpp
    src/occ_primitives.cpp
    src/occ_registry.cpp
)

target_include_directories(hippo_occ_core PRIVATE
    include
    ${OCCT_INCLUDE_DIR}
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
)
