
# Hippo3D Native C Backend

This folder provides a native C Python extension for fast surface topology generation.

Build from this folder:

```bash
cmake -S . -B build -G Ninja
cmake --build build --config Release
cp build/hippo_surface_native*.so ../
```

Restart Blender after copying the compiled module beside `__init__.py`.

If the compiled module is not present, Hippo3D automatically falls back to Python.
