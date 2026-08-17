# SPDX-License-Identifier: GPL-3.0-or-later
"""Hippo3D Sverchok node package.

Each sub-module registers its own node class(es) via register()/unregister().
The parent __init__.py calls these during add-on enable/disable.
"""

# Re-export node classes so Sverchok's auto_gather_node_classes can discover them
try:
    from .get_object import SvHippo3DGetObject
    from .occ_viewer import SvHippo3DOCCViewer
except Exception:
    pass
