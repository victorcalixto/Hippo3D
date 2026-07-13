# SPDX-License-Identifier: GPL-3.0-or-later
"""Dependency checking for Hippo3D Sverchok plugin.

Checks whether Sverchok core, Sverchok Extra, and FreeCAD are available.
This module intentionally does NOT check for Hippo3D OCC core because
this plugin is meant to be installable independently.
"""

import importlib.util


def _has_module(name):
    spec = importlib.util.find_spec(name)
    return spec is not None


# Sverchok core
SVERCHOK_AVAILABLE = _has_module("sverchok")

# Sverchok Extra (OCC surfaces)
SVERCHOK_EXTRA_AVAILABLE = False
if SVERCHOK_AVAILABLE:
    try:
        SVERCHOK_EXTRA_AVAILABLE = _has_module("sverchok_extra")
    except Exception:
        pass

# FreeCAD / Part (for Solids)
FREECAD_AVAILABLE = False
if _has_module("FreeCAD"):
    try:
        import FreeCAD  # noqa: F401
        FREECAD_AVAILABLE = True
    except Exception:
        pass
elif _has_module("Part"):
    try:
        import Part  # noqa: F401
        FREECAD_AVAILABLE = True
    except Exception:
        pass
