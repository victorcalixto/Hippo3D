# SPDX-License-Identifier: GPL-3.0-or-later
"""Node menu category registration for Hippo3D Sverchok nodes.

Provides a custom category so that Hippo3D nodes appear under:
  Add > Hippo3D
inside the Sverchok node tree.
"""

SVERCHOK_AVAILABLE = False
try:
    from sverchok.ui.nodeview_space_menu import add_node_menu, NodeItem
    SVERCHOK_AVAILABLE = True
except Exception:
    NodeItem = object


def register_menu():
    if not SVERCHOK_AVAILABLE:
        return
    # NodeItem expects bl_idname strings
    from .get_object import SvHippo3DGetObject
    from .occ_viewer import SvHippo3DOCCViewer

    category = add_node_menu.get_category("Hippo3D")
    if category is None:
        add_node_menu.add_category("Hippo3D")

    add_node_menu.nodes_to_add.append((
        "Hippo3D",
        [
            NodeItem(SvHippo3DGetObject.bl_idname),
            NodeItem(SvHippo3DOCCViewer.bl_idname),
        ]
    ))


def unregister_menu():
    if not SVERCHOK_AVAILABLE:
        return
    # Best-effort removal from menu list
    try:
        add_node_menu.nodes_to_add[:] = [
            item for item in add_node_menu.nodes_to_add
            if not (isinstance(item, tuple) and len(item) == 2 and item[0] == "Hippo3D")
        ]
    except Exception:
        pass
