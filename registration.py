# SPDX-License-Identifier: GPL-3.0-or-later
"""Registration entry points for Hippo3D.

Blender imports this module from __init__.py. The runtime implementation remains
in main.py so the currently working tool behaviour is preserved.
"""

from . import main


classes = getattr(main, "classes", [])


def register():
    main.register()


def unregister():
    main.unregister()

