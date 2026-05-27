
cmake_minimum_required(VERSION 3.18)
project(hippo_surface_native C)
find_package(Python3 COMPONENTS Interpreter Development REQUIRED)
Python3_add_library(hippo_surface_native MODULE hippo_surface_native.c)
set_target_properties(hippo_surface_native PROPERTIES PREFIX "")
if (WIN32)
    set_target_properties(hippo_surface_native PROPERTIES SUFFIX ".pyd")
endif()
