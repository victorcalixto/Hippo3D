# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared runtime state for Hippo3D commands and viewport drawing."""

from .common import Vector

class CADState:
    active = False
    command = ""
    points = []
    input_text = ""
    mouse_world = Vector((0.0, 0.0, 0.0))
    raw_mouse_world = Vector((0.0, 0.0, 0.0))
    snap_point = None
    snap_label = ""
    draw_handle = None
    text_handle = None
    cplane_draw_handle = None
    cursor_set = False
    pending_cplane_name = ""
    pending_cplane_mode = ""
    nurbs_degree = 3


state = CADState()
