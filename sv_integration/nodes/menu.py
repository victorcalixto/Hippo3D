# SPDX-License-Identifier: GPL-3.0-or-later
"""Node menu category registration for Hippo3D Sverchok nodes.

Uses YAML config when sverchok.utils.yaml_parser is available,
otherwise falls back to manual category insertion.
"""

import bpy
from pathlib import Path

# ---------------------------------------------------------------------------
# Guarded sverchok imports
# ---------------------------------------------------------------------------

try:
    from sverchok.ui.nodeview_space_menu import add_node_menu
except Exception:
    add_node_menu = None

try:
    from sverchok.utils import yaml_parser
except Exception:
    yaml_parser = None

# Path to the YAML menu definition (used when yaml_parser exists)
config_file = Path(__file__).parents[0] / "index.yaml"


# ---------------------------------------------------------------------------
# Cleanup helpers
# ---------------------------------------------------------------------------

def _cleanup_hippo3d_menu():
    if add_node_menu is None:
        return
    # Remove from draw_data
    try:
        for cat in list(add_node_menu.menu_cls.draw_data):
            if getattr(cat, 'name', '') == 'Hippo3D':
                try:
                    cat.unregister()
                except Exception:
                    pass
                add_node_menu.menu_cls.draw_data.remove(cat)
    except Exception:
        pass

    # Force-unregister lingering menu classes
    try:
        for attr in [a for a in dir(bpy.types) if a.startswith('NODEVIEW_MT_SvCategoryHippo3d')]:
            try:
                cls = getattr(bpy.types, attr)
                if isinstance(cls, type):
                    bpy.utils.unregister_class(cls)
            except Exception:
                pass
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_menu():
    if add_node_menu is None:
        return

    _cleanup_hippo3d_menu()

    # --- prefer YAML when available ----------------------------------------
    if yaml_parser is not None and config_file.exists():
        try:
            add_node_menu.append_from_config(yaml_parser.load(config_file))
            add_node_menu.register()
            return
        except Exception:
            pass

    # --- fallback: manual category insertion -------------------------------
    try:
        from sverchok.menu import SverchNodeItem, SverchNodeCategory
        from sverchok.utils import get_node_class_reference
    except Exception:
        return

    node_items = []
    for node_name in ('SvHippo3DGetObject', 'SvHippo3DOCCViewer'):
        rna = get_node_class_reference(node_name)
        if rna:
            node_items.append(SverchNodeItem.new(node_name))

    if node_items:
        cat = SverchNodeCategory("HIPPO3D", "Hippo3D", items=node_items)
        try:
            add_node_menu.add_category("Hippo3D")
        except Exception:
            pass
        add_node_menu.nodes_to_add.append(("Hippo3D", node_items))
        add_node_menu.register()


def unregister_menu():
    _cleanup_hippo3d_menu()
    # Best-effort removal from menu list
    if add_node_menu is not None:
        try:
            add_node_menu.nodes_to_add[:] = [
                item for item in add_node_menu.nodes_to_add
                if not (isinstance(item, tuple) and len(item) == 2 and item[0] == "Hippo3D")
            ]
        except Exception:
            pass
