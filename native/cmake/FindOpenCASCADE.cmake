# FindOpenCASCADE.cmake
# CMake module to locate the OpenCASCADE (OCCT) development libraries.
#
# This module searches common installation prefixes per platform:
#   Linux:   /usr, /usr/local, and the project-local third_party/occt-8.0.0
#   macOS:   /opt/homebrew/opt/opencascade, /usr/local/opt/opencascade
#   FreeBSD: /usr/local
#   OpenBSD: /usr/local
#   Windows: C:/OpenCASCADE, C:/Program Files/OpenCASCADE,
#            C:/OCCT, OCCT_ROOT environment variable
#
# Variables set:
#   OpenCASCADE_FOUND        – TRUE if all required components were found
#   OpenCASCADE_INCLUDE_DIRS – Include path for opencascade headers
#   OpenCASCADE_LIBRARIES    – List of full paths to all required libraries
#   OpenCASCADE::OpenCASCADE – Imported interface target (convenience)
#
# Usage:
#   find_package(OpenCASCADE REQUIRED)
#   target_link_libraries(mytarget OpenCASCADE::OpenCASCADE)

include(FindPackageHandleStandardArgs)

# ---------------------------------------------------------------------------
# 1. Search paths
# ---------------------------------------------------------------------------
set(_OCCT_SEARCH_PATHS)
set(_3RDPARTY_SEARCH_PATHS)

if(WIN32)
  # Standard OCCT Windows installer / download layout:
  #   C:\OCCT\opencascade-8.0.0-vc14-64   (OCCT headers + libs)
  #   C:\OCCT\3rdparty-vc14-64            (zlib, freetype, tbb, etc.)
  list(APPEND _OCCT_SEARCH_PATHS
    "$ENV{OCCT_ROOT}"
    "C:/OCCT/opencascade-8.0.0-vc14-64"
    "C:/OCCT/opencascade-7.9.0-vc14-64"
    "C:/OCCT/opencascade-7.8.0-vc14-64"
    "C:/OpenCASCADE"
    "C:/OpenCASCADE-8.0.0"
    "C:/OpenCASCADE-7.9.0"
    "C:/OpenCASCADE-7.8.0"
    "C:/OpenCASCADE-7.7.0"
    "C:/Program Files/OpenCASCADE"
    "C:/Program Files (x86)/OpenCASCADE"
    "C:/OCCT"
    "$ENV{OpenCASCADE_DIR}"
  )
  # Derive 3rdparty dir from OCCT root (sibling directory)
  if(DEFINED ENV{OCCT_ROOT})
    get_filename_component(_OCCT_PARENT "$ENV{OCCT_ROOT}" DIRECTORY)
    set(_3RDPARTY_CANDIDATE "${_OCCT_PARENT}/3rdparty-vc14-64")
    if(EXISTS "${_3RDPARTY_CANDIDATE}")
      list(APPEND _3RDPARTY_SEARCH_PATHS "${_3RDPARTY_CANDIDATE}")
    endif()
  endif()
  # Also try hardcoded C:/OCCT/3rdparty-vc14-64
  if(EXISTS "C:/OCCT/3rdparty-vc14-64")
    list(APPEND _3RDPARTY_SEARCH_PATHS "C:/OCCT/3rdparty-vc14-64")
  endif()
elseif(APPLE)
  list(APPEND _OCCT_SEARCH_PATHS
    "/opt/homebrew/opt/opencascade"
    "/usr/local/opt/opencascade"
    "/usr/local"
    "/opt/opencascade"
    "$ENV{OCCT_ROOT}"
  )
else()
  # Linux, FreeBSD, OpenBSD, etc.
  list(APPEND _OCCT_SEARCH_PATHS
    "/usr"
    "/usr/local"
    "/opt/opencascade"
    "$ENV{OCCT_ROOT}"
  )
endif()

# ---------------------------------------------------------------------------
# 2. Find the main header directory
# ---------------------------------------------------------------------------
find_path(OpenCASCADE_INCLUDE_DIR
  NAMES BRepPrimAPI_MakeBox.hxx
  PATHS ${_OCCT_SEARCH_PATHS}
  PATH_SUFFIXES
    include/opencascade
    include/occt
    include
    inc
  DOC "OpenCASCADE include directory"
)

if(NOT OpenCASCADE_INCLUDE_DIR)
  message(STATUS "OpenCASCADE headers (BRepPrimAPI_MakeBox.hxx) not found in searched paths")
endif()

# ---------------------------------------------------------------------------
# 3. Helper to find one OCCT library
# ---------------------------------------------------------------------------
set(_OCCT_LIBS
  TKernel TKMath TKG2d TKG3d TKGeomBase TKGeomAlgo TKBRep TKTopAlgo
  TKPrim TKMesh TKBool TKShHealing TKFillet TKHLR TKOffset
  TKDESTEP TKDEIGES TKXSBase
)

foreach(_lib IN LISTS _OCCT_LIBS)
  find_library(OCCT_${_lib}_LIB
    NAMES ${_lib}
    PATHS ${_OCCT_SEARCH_PATHS}
    PATH_SUFFIXES
      lib
      lib64
      win64/gcc/lib          # Windows MinGW / old OCCT layouts
      win64/vc14/lib
      win64/vc15/lib
      osx/clang/lib            # macOS older packages
    DOC "OpenCASCADE library ${_lib}"
  )
endforeach()

# ---------------------------------------------------------------------------
# 4. Collect results
# ---------------------------------------------------------------------------
set(OpenCASCADE_LIBRARIES)
foreach(_lib IN LISTS _OCCT_LIBS)
  if(OCCT_${_lib}_LIB)
    list(APPEND OpenCASCADE_LIBRARIES ${OCCT_${_lib}_LIB})
  else()
    message(STATUS "OpenCASCADE library ${_lib} not found")
  endif()
endforeach()

# ---------------------------------------------------------------------------
# 5. Handle QUIET/REQUIRED
# ---------------------------------------------------------------------------
find_package_handle_standard_args(OpenCASCADE
  REQUIRED_VARS
    OpenCASCADE_INCLUDE_DIR
    OCCT_TKernel_LIB
    OCCT_TKMath_LIB
    OCCT_TKBRep_LIB
    OCCT_TKTopAlgo_LIB
    OCCT_TKPrim_LIB
  FAIL_MESSAGE "Could not find OpenCASCADE (OCCT). Please install OCCT or set OCCT_ROOT."
)

if(OpenCASCADE_FOUND)
  set(OpenCASCADE_INCLUDE_DIRS ${OpenCASCADE_INCLUDE_DIR})
  if(NOT TARGET OpenCASCADE::OpenCASCADE)
    add_library(OpenCASCADE::OpenCASCADE INTERFACE IMPORTED)
    set_target_properties(OpenCASCADE::OpenCASCADE PROPERTIES
      INTERFACE_INCLUDE_DIRECTORIES "${OpenCASCADE_INCLUDE_DIRS}"
      INTERFACE_LINK_LIBRARIES "${OpenCASCADE_LIBRARIES}"
    )
  endif()
endif()

mark_as_advanced(
  OpenCASCADE_INCLUDE_DIR
  OpenCASCADE_LIBRARIES
)
foreach(_lib IN LISTS _OCCT_LIBS)
  mark_as_advanced(OCCT_${_lib}_LIB)
endforeach()
