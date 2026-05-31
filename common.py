# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared imports, constants, and native backend loading for Hippo3D."""

import bpy
import gpu
import blf
import json
from bpy.app.handlers import persistent
from gpu_extras.batch import batch_for_shader
from bpy.types import Operator, Panel, WorkSpaceTool, PropertyGroup, UIList
from bpy.props import StringProperty, BoolProperty, FloatProperty, EnumProperty, CollectionProperty, IntProperty
from mathutils import Vector, Matrix
from bpy_extras import view3d_utils

# Optional Hippo3D native C surface backend.
HIPPO_NATIVE_SURFACE_AVAILABLE = False
HIPPO_NATIVE_SURFACE_ERROR = ""
hippo_surface_native = None

from pathlib import Path

ICON_DIR = Path(__file__).parent / "icons"


def _load_hippo_native_backend():
    global HIPPO_NATIVE_SURFACE_AVAILABLE, HIPPO_NATIVE_SURFACE_ERROR, hippo_surface_native

    import importlib
    import importlib.util
    import sys
    from pathlib import Path as _Path

    candidates = []

    # 1. Normal import, useful if the module is on sys.path.
    try:
        import hippo_surface_native as _native
        hippo_surface_native = _native
        HIPPO_NATIVE_SURFACE_AVAILABLE = True
        HIPPO_NATIVE_SURFACE_ERROR = ""
        return
    except Exception as exc:
        HIPPO_NATIVE_SURFACE_ERROR = f"normal import failed: {exc}"

    # 2. Package-relative import.
    try:
        _native = importlib.import_module(__package__ + ".hippo_surface_native")
        hippo_surface_native = _native
        HIPPO_NATIVE_SURFACE_AVAILABLE = True
        HIPPO_NATIVE_SURFACE_ERROR = ""
        return
    except Exception as exc:
        HIPPO_NATIVE_SURFACE_ERROR += f" | package import failed: {exc}"

    # 3. Load directly from the add-on directory beside __init__.py.
    addon_dir = _Path(__file__).resolve().parent
    for pattern in (
        "hippo_surface_native*.so",
        "hippo_surface_native*.pyd",
        "hippo_surface_native*.dll",
        "hippo_surface_native*.dylib",
    ):
        candidates.extend(addon_dir.glob(pattern))

    if not candidates:
        HIPPO_NATIVE_SURFACE_ERROR += f" | no compiled hippo_surface_native file found in {addon_dir}"
        return

    native_path = candidates[0]

    try:
        spec = importlib.util.spec_from_file_location("hippo_surface_native", str(native_path))
        if spec is None or spec.loader is None:
            HIPPO_NATIVE_SURFACE_ERROR += f" | could not create import spec for {native_path}"
            return

        module = importlib.util.module_from_spec(spec)
        sys.modules["hippo_surface_native"] = module
        spec.loader.exec_module(module)

        hippo_surface_native = module
        HIPPO_NATIVE_SURFACE_AVAILABLE = True
        HIPPO_NATIVE_SURFACE_ERROR = ""
        return

    except Exception as exc:
        HIPPO_NATIVE_SURFACE_ERROR += f" | direct load failed from {native_path}: {exc}"


_load_hippo_native_backend()
