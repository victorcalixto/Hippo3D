# Hippo3D OCC Stage 2 Primitives

Adds native OpenCascade primitive mesh generation:

- Box
- Sphere
- Cylinder
- Cone
- Torus

Build:

```bash
cd native
rm -rf build
cmake -S . -B build -G Ninja \
  -DPython_EXECUTABLE="$(pyenv which python)" \
  -DPYTHON_EXECUTABLE="$(pyenv which python)" \
  -Dpybind11_DIR="$(python -m pybind11 --cmakedir)"
cmake --build build
```
