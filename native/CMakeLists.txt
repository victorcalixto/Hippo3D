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
elseif(DEFINED ENV{OCCT_ROOT})
    # Windows / user-defined OCCT prefix
    set(ENV_OCCT_ROOT "$ENV{OCCT_ROOT}")
    if(EXISTS "${ENV_OCCT_ROOT}/lib/cmake/opencascade/OpenCASCADEConfig.cmake")
        list(APPEND CMAKE_PREFIX_PATH "${ENV_OCCT_ROOT}")
        set(OpenCASCADE_DIR "${ENV_OCCT_ROOT}/lib/cmake/opencascade")
        message(STATUS "Using OCCT from OCCT_ROOT: ${ENV_OCCT_ROOT}")
    else()
        # Fallback: old layout without CMake config — rely on Find module
        list(APPEND CMAKE_PREFIX_PATH "${ENV_OCCT_ROOT}")
        message(STATUS "OCCT_ROOT set but no CMake config found; using Find module: ${ENV_OCCT_ROOT}")
    endif()
else()
    message(STATUS "Local OCCT 8.0.0 not found, falling back to system OCCT")
endif()

# ---------------------------------------------------------------------------
# Allow our custom Find modules to be located
# ---------------------------------------------------------------------------
list(APPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_SOURCE_DIR}/cmake")

# Use modern FindPython instead of deprecated FindPythonInterp/FindPythonLibs
set(PYBIND11_FINDPYTHON ON)

# Blender's bundled Python on Windows does not ship the development headers or
# .lib files needed by CMake's FindPython.  If the user points us at them via
# PYTHON_DEV_DIR, prefer that installation for development artifacts while
# still using Blender's python.exe as the interpreter.
if(WIN32 AND DEFINED ENV{PYTHON_DEV_DIR})
    set(Python_ROOT_DIR "$ENV{PYTHON_DEV_DIR}" CACHE PATH "" FORCE)
    set(Python_INCLUDE_DIR "$ENV{PYTHON_DEV_DIR}/include" CACHE PATH "" FORCE)
    set(Python_LIBRARY "$ENV{PYTHON_DEV_DIR}/libs/python${Python_VERSION_MAJOR}${Python_VERSION_MINOR}.lib" CACHE FILEPATH "" FORCE)
endif()

find_package(Python 3.8 REQUIRED COMPONENTS Interpreter OPTIONAL_COMPONENTS Development.Module Development.Embed)

# After FindPython runs, force the variables pybind11 will use if the user
# explicitly supplied PYTHON_DEV_DIR.  This prevents CMake from falling back
# to a different Python install found via registry/PATH (e.g. Python 3.11).
if(WIN32 AND DEFINED ENV{PYTHON_DEV_DIR})
    set(Python_FOUND TRUE)
    set(Python_Development_FOUND TRUE)
    set(Python_Development_Module_FOUND TRUE)
    set(Python_INCLUDE_DIRS "$ENV{PYTHON_DEV_DIR}/include")
    set(Python_LIBRARIES "$ENV{PYTHON_DEV_DIR}/libs/python${Python_VERSION_MAJOR}${Python_VERSION_MINOR}.lib")
    set(Python_LIBRARY_RELEASE "$ENV{PYTHON_DEV_DIR}/libs/python${Python_VERSION_MAJOR}${Python_VERSION_MINOR}.lib")
    set(Python_LIBRARY_DEBUG "$ENV{PYTHON_DEV_DIR}/libs/python${Python_VERSION_MAJOR}${Python_VERSION_MINOR}.lib")
endif()

if(WIN32 AND DEFINED ENV{PYTHON_DEV_DIR} AND NOT Python_Development.Module_FOUND)
    # On Windows with Blender's bundled Python, CMake's FindPython may refuse to
    # believe PYTHON_DEV_DIR because the interpreter reports a different prefix.
    # We know the user supplied PYTHON_DEV_DIR is correct, so force the variables
    # that pybind11 needs and then load pybind11 without running its own Python
    # search.
    message(STATUS "Forcing Python development artifacts from PYTHON_DEV_DIR: $ENV{PYTHON_DEV_DIR}")
    # pybind11NewTools.cmake uses Python_add_library, which FindPython only defines
    # when a development component is found. Provide a minimal shim here.
    if(NOT COMMAND Python_add_library)
        function(Python_add_library target)
            cmake_parse_arguments(ARG "" "" "" ${ARGN})
            add_library(${target} MODULE ${ARG_UNPARSED_ARGUMENTS})
            target_include_directories(${target} PRIVATE ${Python_INCLUDE_DIRS})
            target_link_libraries(${target} PRIVATE ${Python_LIBRARIES})
        endfunction()
    endif()
    find_package(pybind11 REQUIRED HINTS "$ENV{PYTHON_DEV_DIR}/Lib/site-packages/pybind11/share/cmake/pybind11" "${Python_EXECUTABLE}/../../Lib/site-packages/pybind11/share/cmake/pybind11" NO_DEFAULT_PATH)
else()
    find_package(pybind11 REQUIRED)
endif()

find_package(OpenCASCADE REQUIRED)

# ---------------------------------------------------------------------------
# OpenNURBS — use static library.
# OpenNURBS is built with a prefixed internal zlib (z_inflate, z_deflate, etc.).
# The system libz.so exports the unprefixed names (inflate, deflate, ...), so
# -lz alone does NOT resolve the z_* symbols.
# Therefore we must also link the OpenNURBS built-in zlib static archive.
# ---------------------------------------------------------------------------
set(OPENNURBS_DIR "${CMAKE_SOURCE_DIR}/third_party/opennurbs")

# On Windows the upstream opennurbs CMakeLists only builds android_uuid/freetype263
# for (ANDROID OR LINUX), so on WIN32 we must force them on as well or the link
# step will be missing those static archives.
if(WIN32)
    set(OPENNURBS_BUILD_ON_WINDOWS ON CACHE BOOL "Build android_uuid/freetype263 on Windows" FORCE)
endif()
if(OPENNURBS_BUILD_ON_WINDOWS)
    set(_orig_ANDROID "${ANDROID}")
    set(_orig_LINUX "${LINUX}")
    set(ANDROID ON CACHE BOOL "" FORCE)
    set(LINUX ON CACHE BOOL "" FORCE)
endif()

add_subdirectory(${OPENNURBS_DIR} ${CMAKE_BINARY_DIR}/opennurbs)

if(OPENNURBS_BUILD_ON_WINDOWS)
    set(ANDROID "${_orig_ANDROID}" CACHE BOOL "" FORCE)
    set(LINUX "${_orig_LINUX}" CACHE BOOL "" FORCE)
endif()

# On Windows we don't actually need android_uuid: opennurbs_uuid.cpp uses
# Rpcrt4.lib / UuidCreate when ON_COMPILER_MSC is defined.  The android_uuid
# subdir only builds cleanly on Unix, so skip linking it on Windows.
if(WIN32)
    set(OPENNURBS_ANDROID_UUID_A "")
endif()

find_library(OPENNURBS_STATIC_LIB
    NAMES opennurbsStatic
    PATHS
        ${OPENNURBS_DIR}/build
        ${OPENNURBS_DIR}/build/Release
        ${CMAKE_BINARY_DIR}/opennurbs
        ${CMAKE_BINARY_DIR}/opennurbs/Release
        ${CMAKE_BINARY_DIR}/opennurbs/Debug
    NO_DEFAULT_PATH
)
if(NOT OPENNURBS_STATIC_LIB)
    message(STATUS "OpenNURBS static library not yet built; configuring build step")
    set(OPENNURBS_STATIC_LIB "${CMAKE_BINARY_DIR}/opennurbs/Release/${CMAKE_STATIC_LIBRARY_PREFIX}opennurbsStatic${CMAKE_STATIC_LIBRARY_SUFFIX}")
endif()

set(OPENNURBS_ZLIB_A "${OPENNURBS_DIR}/build/zlib/${CMAKE_STATIC_LIBRARY_PREFIX}zlib${CMAKE_STATIC_LIBRARY_SUFFIX}")
if(NOT EXISTS ${OPENNURBS_ZLIB_A})
    set(OPENNURBS_ZLIB_A "${OPENNURBS_DIR}/build/zlib/Release/${CMAKE_STATIC_LIBRARY_PREFIX}zlib${CMAKE_STATIC_LIBRARY_SUFFIX}")
endif()
if(NOT EXISTS ${OPENNURBS_ZLIB_A})
    set(OPENNURBS_ZLIB_A "${CMAKE_BINARY_DIR}/opennurbs/zlib/${CMAKE_STATIC_LIBRARY_PREFIX}zlib${CMAKE_STATIC_LIBRARY_SUFFIX}")
endif()
if(NOT EXISTS ${OPENNURBS_ZLIB_A})
    set(OPENNURBS_ZLIB_A "${CMAKE_BINARY_DIR}/opennurbs/zlib/Release/${CMAKE_STATIC_LIBRARY_PREFIX}zlib${CMAKE_STATIC_LIBRARY_SUFFIX}")
endif()
if(NOT EXISTS ${OPENNURBS_ZLIB_A})
    set(OK_OPENNURBS_ZLIB_A ON)
endif()

set(OPENNURBS_ANDROID_UUID_A "${OPENNURBS_DIR}/build/android_uuid/${CMAKE_STATIC_LIBRARY_PREFIX}android_uuid${CMAKE_STATIC_LIBRARY_SUFFIX}")
if(NOT EXISTS ${OPENNURBS_ANDROID_UUID_A})
    set(OPENNURBS_ANDROID_UUID_A "${CMAKE_BINARY_DIR}/opennurbs/android_uuid/${CMAKE_STATIC_LIBRARY_PREFIX}android_uuid${CMAKE_STATIC_LIBRARY_SUFFIX}")
endif()
if(NOT EXISTS ${OPENNURBS_ANDROID_UUID_A})
    set(OPENNURBS_ANDROID_UUID_A "${CMAKE_BINARY_DIR}/opennurbs/android_uuid/Release/${CMAKE_STATIC_LIBRARY_PREFIX}android_uuid${CMAKE_STATIC_LIBRARY_SUFFIX}")
endif()
if(NOT EXISTS ${OPENNURBS_ANDROID_UUID_A})
    set(OPENNURBS_ANDROID_UUID_A "")
endif()

# ---------------------------------------------------------------------------
# System zlib
# ---------------------------------------------------------------------------
# On Windows, OCCT's 3rdparty package often bundles zlib in a subfolder
# like 3rdparty-vc14-64/zlib-1.2.11/.
if(WIN32 AND DEFINED ENV{OCCT_ROOT})
  # OCCT's prebuilt package uses a sibling 3rdparty-vc14-64 folder.
  # If it exists, set ZLIB_ROOT from it so find_package(ZLIB) locates the
  # bundled headers/library instead of a stray system copy.
  get_filename_component(_OCCT_PARENT "$ENV{OCCT_ROOT}" DIRECTORY)
  set(_3RDPARTY_DIR "$ENV{3RDPARTY_DIR}")
  if(NOT _3RDPARTY_DIR AND EXISTS "${_OCCT_PARENT}/3rdparty-vc14-64")
    set(_3RDPARTY_DIR "${_OCCT_PARENT}/3rdparty-vc14-64")
  endif()
  if(_3RDPARTY_DIR)
    file(GLOB _3RDPARTY_DIRS "${_3RDPARTY_DIR}/*")
    foreach(_dir IN LISTS _3RDPARTY_DIRS)
      if(IS_DIRECTORY "${_dir}")
        if(EXISTS "${_dir}/include/zlib.h" AND EXISTS "${_dir}/lib/zlib.lib")
          set(ZLIB_ROOT "${_dir}")
          list(APPEND CMAKE_PREFIX_PATH "${_dir}")
          message(STATUS "Found bundled zlib in 3rdparty: ${_dir}")
          break()
        endif()
      endif()
    endforeach()
    if(EXISTS "${_3RDPARTY_DIR}")
      list(APPEND CMAKE_PREFIX_PATH "${_3RDPARTY_DIR}")
      message(STATUS "Added 3rdparty prefix: ${_3RDPARTY_DIR}")
    endif()
  endif()
endif()
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
    $<$<BOOL:${WIN32}>:Rpcrt4>
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
    # OpenNURBS on Windows needs shlwapi.lib for PathFileExists/PathIsDirectory
    target_link_libraries(hippo_occ_core PRIVATE shlwapi)
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
