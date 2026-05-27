# SPDX-License-Identifier: GPL-3.0-or-later
"""Hippo3D Blender add-on entry point."""

bl_info = {
    "name": "Hippo3D",
    "author": "Victor Calixto",
    "version": (0, 1, 0),
    "blender": (5, 0, 0),
    "location": "3D View > Sidebar > Hippo3D / Toolbar / Ctrl+/ Command",
    "description": "A free and open source Rhino-like CAD modelling plugin for Blender.",
    "category": "3D View",
}

from . import main

classes = getattr(main, "classes", [])


def register():
    main.register()


def unregister():
    main.unregister()
