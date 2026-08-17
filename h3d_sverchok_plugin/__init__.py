# SPDX-License-Identifier: GPL-3.0-or-later
"""Hippo3D Sverchok Plugin.

A standalone Blender add-on that registers Hippo3D nodes inside the
Sverchok node tree.

Requirements:
  - Sverchok (mandatory for the nodes to actually appear)
  - Hippo3D (recommended so baked objects are tagged correctly)

Optional runtime dependencies:
  - Sverchok Extra … enables Surfaces output on Get Object node
  - FreeCAD / Part … enables Solids  output on Get Object node
"""

bl_info = {
    "name": "Hippo3D Sverchok",
    "author": "Victor Calixto",
    "version": (0, 1, 0),
    "blender": (4, 0, 0),
    "location": "Node Editor > Sverchok > Add > Hippo3D",
    "description": "Hippo3D nodes for Sverchok",
    "category": "Node",
}

import sys

# Fix module name when the folder is not named exactly "h3d_sverchok_plugin"
if __name__ != "h3d_sverchok_plugin":
    sys.modules["h3d_sverchok_plugin"] = sys.modules[__name__]

import bpy
import importlib


# ---------------------------------------------------------------------------
# Node index
# ---------------------------------------------------------------------------

def nodes_index():
    return [
        ("Hippo3D", [
            ("get_object", "SvHippo3DGetObject"),
            ("occ_viewer", "SvHippo3DOCCViewer"),
        ]),
    ]


# ---------------------------------------------------------------------------
# Build module list once (mirrors sverchok-extra pattern)
# ---------------------------------------------------------------------------

def make_node_list():
    modules = []
    base_name = "h3d_sverchok_plugin.nodes"
    for category, items in nodes_index():
        for module_name, node_name in items:
            try:
                module = importlib.import_module(f".{module_name}", base_name)
                modules.append(module)
            except Exception:
                pass
    return modules


imported_modules = make_node_list()


# ---------------------------------------------------------------------------
# Node registration (called from register())
# ---------------------------------------------------------------------------

def register_nodes():
    for module in imported_modules:
        if hasattr(module, "register"):
            module.register()


def unregister_nodes():
    for module in reversed(imported_modules):
        if hasattr(module, "unregister"):
            module.unregister()


# ---------------------------------------------------------------------------
# Menu helpers (uses the same API as sverchok-extra)
# ---------------------------------------------------------------------------

def make_menu_config():
    """Return the add-node menu config in the format append_from_config expects.

    Format: [ {category_name: [node_bl_idname, node_bl_idname, ...]} ]
    """
    config = []
    for category_name, items in nodes_index():
        node_names = [node_name for module_name, node_name in items]
        config.append({category_name: node_names})
    return config


our_menu_classes = []


# ---------------------------------------------------------------------------
# Blender add-on entry points
# ---------------------------------------------------------------------------

def register():
    global our_menu_classes

    # 1. Verify Sverchok is installed
    try:
        import sverchok  # noqa: F401
    except ImportError:
        print("Hippo3D Sverchok Plugin: Sverchok not installed; cannot register nodes.")
        return

    # 1b. Avoid duplicate node classes when the main Hippo3D add-on already
    # registered the same SvHippo3DGetObject / SvHippo3DOCCViewer nodes.
    try:
        if ("Hippo3D" in getattr(getattr(bpy, "context", None), "preferences", {}).get("addons", {})
                and bpy.types.Node.bl_rna_get_subclass_py("SvHippo3DGetObject") is not None):
            print("Hippo3D Sverchok Plugin: main Hippo3D add-on already provides these nodes; skipping registration.")
            return
    except Exception:
        pass

    # 2. Register node classes (each module has its own register())
    register_nodes()

    # 3. Register the Hippo3D category in the Shift+A menu.
    #    This uses the same API as sverchok-extra: append_from_config on
    #    sverchok.ui.nodeview_space_menu.add_node_menu.
    try:
        from sverchok.ui.nodeview_space_menu import add_node_menu
        config = make_menu_config()
        add_node_menu.append_from_config(config)
        add_node_menu.register()
    except Exception as e:
        print(f"Hippo3D Sverchok Plugin: menu registration failed: {e}")
        import traceback
        traceback.print_exc()


def unregister():
    # Unregister node modules
    unregister_nodes()


# F8 / script.reload support
if "bpy" in locals():
    try:
        for module in imported_modules:
            importlib.reload(module)
    except Exception:
        pass
