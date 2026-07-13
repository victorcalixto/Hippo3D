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

def register():
    if not SVERCHOK_AVAILABLE:
        return
    from .nodes import register_nodes
    register_nodes()


def unregister():
    if not SVERCHOK_AVAILABLE:
        return
    from .nodes import unregister_nodes
    unregister_nodes()
