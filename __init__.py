# SPDX-License-Identifier: GPL-3.0-or-later
"""Hippo3D Blender add-on package."""

bl_info = {'name': 'Hippo3D', 'author': 'Victor Calixto', 'version': (0, 1, 1), 'blender': (4, 0, 0), 'location': '3D View > Sidebar > Hippo3D / Toolbar / Ctrl+/ Command', 'description': 'Free and open source CAD-style modelling tools for Blender.', 'category': '3D View'}

from .registration import register, unregister

