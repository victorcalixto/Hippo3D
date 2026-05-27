#!/usr/bin/env bash
set -e
cmake -S . -B build -G Ninja
cmake --build build --config Release
cp build/hippo_surface_native*.so ../
echo "Copied hippo_surface_native to addon root."
