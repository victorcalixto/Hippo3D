# SPDX-License-Identifier: GPL-3.0-or-later
"""Sverchok node registration for Hippo3D integration.

Eagerly imports node modules at load time so import errors are visible in
Blender's console.  Each node module handles its own register()/unregister().
"""

import bpy
import logging
import traceback

logger = logging.getLogger('sverchok.hippo3d')

_NODE_CLASSES = []


def _register_node_class(cls):
    _NODE_CLASSES.append(cls)
    return cls


def register_nodes():
    """Register all collected Hippo3D Sverchok node classes."""
    for cls in _NODE_CLASSES:
        try:
            bpy.utils.register_class(cls)
        except Exception as e:
            logger.error("Failed to register %s: %s", cls.__name__, e)
            traceback.print_exc()


def unregister_nodes():
    """Unregister all collected Hippo3D Sverchok node classes."""
    for cls in reversed(_NODE_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Eager import so failures are visible (not silently swallowed)
# ---------------------------------------------------------------------------
try:
    from .get_object import SvHippo3DGetObject
    _register_node_class(SvHippo3DGetObject)
except Exception as e:
    logger.error("Hippo3D: failed to import Get Object node: %s", e)
    traceback.print_exc()

try:
    from .occ_viewer import SvHippo3DOCCViewer
    _register_node_class(SvHippo3DOCCViewer)
except Exception as e:
    logger.error("Hippo3D: failed to import OCC Viewer node: %s", e)
    traceback.print_exc()
