# SPDX-License-Identifier: GPL-3.0-or-later
"""Sverchok node registration for Hippo3D integration."""

import bpy

# Collect node classes to register
_NODE_CLASSES = []


def _register_node_class(cls):
    _NODE_CLASSES.append(cls)
    return cls


def register_nodes():
    """Register all Hippo3D Sverchok node classes."""
    for cls in _NODE_CLASSES:
        bpy.utils.register_class(cls)


def unregister_nodes():
    """Unregister all Hippo3D Sverchok node classes."""
    for cls in reversed(_NODE_CLASSES):
        bpy.utils.unregister_class(cls)


# ---------------------------------------------------------------------------
# Lazy import to avoid loading heavy dependencies at import time
# ---------------------------------------------------------------------------

from ..dependency_check import (
    SVERCHOK_AVAILABLE,
    SVERCHOK_EXTRA_AVAILABLE,
    FREECAD_AVAILABLE,
    HIPPO3D_OCC_AVAILABLE,
)

if SVERCHOK_AVAILABLE:
    from .get_object import SvHippo3DGetObject
    from .occ_viewer import SvHippo3DOCCViewer

    _register_node_class(SvHippo3DGetObject)
    _register_node_class(SvHippo3DOCCViewer)
