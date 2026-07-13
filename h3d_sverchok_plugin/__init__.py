# SPDX-License-Identifier: GPL-3.0-or-later
"""Hippo3D Sverchok Plugin — external node set for Sverchok.

This is a standalone Blender add-on that registers Hippo3D nodes inside
Sverchok's node tree.  Enable it after both **Sverchok** and **Hippo3D**
are installed.
"""

bl_info = {
    "name": "Hippo3D Sverchok Nodes",
    "author": "Victor Calixto",
    "version": (0, 1, 0),
    "blender": (4, 0, 0),
    "location": "Node Editor > Sverchok > Add > Hippo3D",
    "description": "Hippo3D nodes for Sverchok: Get Object, OCC Viewer",
    "category": "Node",
}

import bpy
import logging

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Node class cache (filled once inside register())
# ---------------------------------------------------------------------------

_CLASSES = []


def _get_classes():
    if _CLASSES:
        return _CLASSES
    try:
        from .nodes.get_object import SvHippo3DGetObject
        from .nodes.occ_viewer import SvHippo3DOCCViewer
        _CLASSES.extend([SvHippo3DGetObject, SvHippo3DOCCViewer])
    except Exception as exc:
        _logger.warning("Hippo3D Sverchok plugin — cannot import nodes: %s", exc)
    return _CLASSES


# ---------------------------------------------------------------------------
# Sverchok-compatible registration helpers
# ---------------------------------------------------------------------------

def _sverchok_register_node(cls):
    """Try multiple ways to register a node class with Sverchok."""
    # Method 1: sverchok.utils.register_node_class (most common)
    try:
        import sverchok.utils as sv_utils
        if hasattr(sv_utils, "register_node_class"):
            sv_utils.register_node_class(cls)
            return
    except Exception:
        pass

    # Method 2: bpy.utils.register_class
    try:
        bpy.utils.register_class(cls)
    except ValueError:
        pass  # already registered


def _sverchok_unregister_node(cls):
    """Undo _sverchok_register_node."""
    try:
        import sverchok.utils as sv_utils
        if hasattr(sv_utils, "unregister_node_class"):
            sv_utils.unregister_node_class(cls)
            return
    except Exception:
        pass

    try:
        bpy.utils.unregister_class(cls)
    except RuntimeError:
        pass


# ---------------------------------------------------------------------------
# Menu registration helpers
# ---------------------------------------------------------------------------

def _register_menu_legacy(add_node_menu, node_classes):
    """Register using the legacy nodes_to_add list."""
    try:
        from sverchok.ui.nodeview_space_menu import NodeItem
    except Exception:
        return False

    items = [NodeItem(cls.bl_idname) for cls in node_classes]
    # Prevent duplicates
    existing = [entry for entry in add_node_menu.nodes_to_add
                if isinstance(entry, tuple) and entry[0] == "Hippo3D"]
    if not existing:
        add_node_menu.nodes_to_add.append(("Hippo3D", items))
    return True


def _register_menu_modern(add_node_menu, node_classes):
    """Register using the modern add_node() API."""
    success = False
    for cls in node_classes:
        try:
            add_node_menu.add_node(cls, category="Hippo3D")
            success = True
        except Exception:
            pass
    return success


def _unregister_menu_legacy(add_node_menu):
    """Remove Hippo3D entries from nodes_to_add."""
    add_node_menu.nodes_to_add[:] = [
        entry for entry in add_node_menu.nodes_to_add
        if not (isinstance(entry, tuple) and entry[0] == "Hippo3D")
    ]


def _unregister_menu_modern(add_node_menu, node_classes):
    for cls in node_classes:
        try:
            if hasattr(add_node_menu, "remove_node"):
                add_node_menu.remove_node(cls, category="Hippo3D")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Addon entry points
# ---------------------------------------------------------------------------

def register():
    classes = _get_classes()
    if not classes:
        _logger.warning("Hippo3D Sverchok plugin — no nodes to register.")
        return

    # Register node classes
    for cls in classes:
        _sverchok_register_node(cls)

    # Register menu entries
    try:
        from sverchok.ui.nodeview_space_menu import add_node_menu
        ok = _register_menu_modern(add_node_menu, classes)
        if not ok:
            _register_menu_legacy(add_node_menu, classes)
    except Exception as exc:
        _logger.debug("Hippo3D menu registration skipped: %s", exc)

    _logger.info("Hippo3D Sverchok nodes registered: %s", [c.bl_idname for c in classes])


def unregister():
    classes = list(_CLASSES)
    if not classes:
        return

    # Remove menu entries
    try:
        from sverchok.ui.nodeview_space_menu import add_node_menu
        _unregister_menu_modern(add_node_menu, classes)
        _unregister_menu_legacy(add_node_menu)
    except Exception:
        pass

    # Unregister node classes
    for cls in reversed(classes):
        _sverchok_unregister_node(cls)

    _CLASSES.clear()
