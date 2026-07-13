# SPDX-License-Identifier: GPL-3.0-or-later
"""Dependency checking for Hippo3D Sverchok integration.

Checks whether Sverchok, Sverchok Extra, and FreeCAD (for Solids)
are available in the current Blender Python environment.
"""

import importlib.util
import sys

# ---------------------------------------------------------------------------
# Sverchok core
# ---------------------------------------------------------------------------

def _has_module(name):
    spec = importlib.util.find_spec(name)
    return spec is not None


SVERCHOK_AVAILABLE = _has_module("sverchok")

# ---------------------------------------------------------------------------
# Sverchok Extra (OCC surfaces)
# ---------------------------------------------------------------------------

SVERCHOK_EXTRA_AVAILABLE = False
if SVERCHOK_AVAILABLE:
    try:
        import sverchok
        # sverchok_extra is an optional dependency package
        SVERCHOK_EXTRA_AVAILABLE = _has_module("sverchok_extra")
    except Exception:
        pass

# ---------------------------------------------------------------------------
# FreeCAD / Part (for Solids)
# ---------------------------------------------------------------------------

FREECAD_AVAILABLE = False
if _has_module("FreeCAD"):
    try:
        import FreeCAD
        FREECAD_AVAILABLE = True
    except Exception:
        pass
elif _has_module("Part"):
    # Some distributions expose Part without FreeCAD package prefix
    try:
        import Part
        FREECAD_AVAILABLE = True
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Hippo3D native OCC core
# ---------------------------------------------------------------------------

HIPPO3D_OCC_AVAILABLE = False
try:
    from ..kernels.occ_loader import load_occ_core
    _occ = load_occ_core()
    HIPPO3D_OCC_AVAILABLE = True
except Exception:
    pass

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def sverchok_surface_type():
    """Return the Sverchok Extra Surface class if available."""
    if not SVERCHOK_EXTRA_AVAILABLE:
        return None
    try:
        from sverchok_extra.utils.surface import SvSurface as SurfaceType
        return SurfaceType
    except Exception:
        return None


def sverchok_solid_type():
    """Return the Solids solid type if available."""
    if not FREECAD_AVAILABLE:
        return None
    try:
        import Part
        return Part.Shape
    except Exception:
        return None
