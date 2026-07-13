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
    "name": "Hippo3D",
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
# Menu helpers (mirrors sverchok-extra pattern exactly)
# ---------------------------------------------------------------------------

def make_menu():
    try:
        from sverchok.menu import SverchNodeItem, SverchNodeCategory
        from sverchok.utils import get_node_class_reference
    except Exception:
        return []

    menu = []
    for category_name, items in nodes_index():
        identifier = "HIPPO3D_" + category_name.replace(" ", "_")
        node_items = []
        for module_name, node_name in items:
            rna = get_node_class_reference(node_name)
            if rna:
                node_item = SverchNodeItem.new(node_name)
                node_items.append(node_item)
        if node_items:
            cat = SverchNodeCategory(identifier, category_name, items=node_items)
            menu.append(cat)
    return menu


try:
    from sverchok.utils.extra_categories import register_extra_category_provider, unregister_extra_category_provider
    _HAS_EXTRA_CATEGORIES = True
except Exception:
    _HAS_EXTRA_CATEGORIES = False


class Hippo3DCategoryProvider:
    def __init__(self, identifier, menu):
        self.identifier = identifier
        self.menu = menu

    def get_categories(self):
        return self.menu


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
        return

    # 2. Register node classes (each module has its own register())
    register_nodes()

    # 3. Auto-gather so Sverchok knows about our node classes
    try:
        from sverchok.utils import auto_gather_node_classes
        h3d_nodes = importlib.import_module(".nodes", "h3d_sverchok_plugin")
        auto_gather_node_classes(h3d_nodes)
    except Exception:
        pass

    # 4. Build and register menu category
    menu = make_menu()
    if menu and _HAS_EXTRA_CATEGORIES:
        provider = Hippo3DCategoryProvider("HIPPO3D", menu)
        register_extra_category_provider(provider)

        try:
            from sverchok.ui.nodeview_space_menu import make_extra_category_menus
            our_menu_classes = make_extra_category_menus()
        except Exception:
            pass


def unregister():
    global our_menu_classes

    # 1. Unregister menu classes
    for clazz in reversed(our_menu_classes):
        try:
            bpy.utils.unregister_class(clazz)
        except Exception:
            pass
    our_menu_classes.clear()

    # 2. Unregister category provider
    if _HAS_EXTRA_CATEGORIES:
        try:
            unregister_extra_category_provider("HIPPO3D")
        except Exception:
            pass

    # 3. Unregister node modules
    unregister_nodes()


# F8 / script.reload support
if "bpy" in locals():
    try:
        for module in imported_modules:
            importlib.reload(module)
    except Exception:
        pass
