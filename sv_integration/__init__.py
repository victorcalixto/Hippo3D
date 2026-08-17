# SPDX-License-Identifier: GPL-3.0-or-later
"""Sverchok integration module for Hippo3D.

Provides nodes that bridge Hippo3D OCC objects with Sverchok Extra
surfaces and Solids solids.

Nodes are registered only when Sverchok is available.
"""

from . import dependency_check

# Re-export availability flags for convenience
SVERCHOK_AVAILABLE = dependency_check.SVERCHOK_AVAILABLE
SVERCHOK_EXTRA_AVAILABLE = dependency_check.SVERCHOK_EXTRA_AVAILABLE
FREECAD_AVAILABLE = dependency_check.FREECAD_AVAILABLE
HIPPO3D_OCC_AVAILABLE = dependency_check.HIPPO3D_OCC_AVAILABLE


# ---------------------------------------------------------------------------
# Conditional node registration
# ---------------------------------------------------------------------------

def _is_h3d_sverchok_plugin_enabled():
    """Return True only if the standalone plugin is actually loaded."""
    try:
        import bpy
        prefs = getattr(bpy, "context", None)
        if prefs is None:
            return False
        prefs = getattr(prefs, "preferences", None)
        if prefs is None:
            return False
        addons = getattr(prefs, "addons", {})
        if "h3d_sverchok_plugin" in addons:
            addon = addons["h3d_sverchok_plugin"]
            if getattr(addon, "module", None) is not None:
                return True
    except Exception:
        pass
    return False


def register():
    try:
        import sverchok  # noqa: F401
    except ImportError:
        return
    # Avoid duplicate classes if the standalone h3d_sverchok_plugin add-on
    # is also enabled (it registers the same bl_idnames).
    if _is_h3d_sverchok_plugin_enabled():
        print("Hippo3D: h3d_sverchok_plugin add-on is enabled; skipping integrated node registration.")
        return
    from .nodes import register_nodes
    from .nodes.menu import register_menu
    register_nodes()
    register_menu()


def unregister():
    try:
        import sverchok  # noqa: F401
    except ImportError:
        return
    from .nodes.menu import unregister_menu
    from .nodes import unregister_nodes
    unregister_menu()
    unregister_nodes()
