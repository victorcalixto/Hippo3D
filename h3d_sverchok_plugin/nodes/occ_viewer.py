# SPDX-License-Identifier: GPL-3.0-or-later
"""Hippo3D OCC Viewer node for Sverchok.

Receives mesh vertices/faces from the Sverchok node tree and bakes them
as Hippo3D OCC objects in the Blender scene.
"""

import bpy
from mathutils import Vector

SVERCHOK_AVAILABLE = False
try:
    from sverchok.node_tree import SverchCustomTreeNode
    from sverchok.data_structure import updateNode
    SVERCHOK_AVAILABLE = True
except Exception:
    SverchCustomTreeNode = object


# ---------------------------------------------------------------------------
# OCC baking helper (tries native Hippo3D core, then falls back to plain mesh)
# ---------------------------------------------------------------------------

def _bake_hippo3d_occ_object(context, name, vertices, faces, location=None):
    """Create a Hippo3D OCC object from raw mesh data."""
    # --- Attempt 1: native Hippo3D OCC backend ---------------------------------
    try:
        # hippo_load_occ_core is defined in the main Hippo3D add-on.
        # We import from bpy.context because the add-on may not be in sys.path.
        import importlib
        import sys
        # find the module that defines hippo_load_occ_core
        _main_mod = sys.modules.get("Hippo3D.main")
        if _main_mod is None:
            # Try to discover via blender add-on modules
            for mod_name, mod in sys.modules.items():
                if hasattr(mod, "hippo_load_occ_core"):
                    _main_mod = mod
                    break
        if _main_mod is None:
            raise ImportError("Hippo3D.main not found in sys.modules")

        hippo_load_occ_core = getattr(_main_mod, "hippo_load_occ_core")
        hippo_create_occ_mesh_object = getattr(_main_mod, "hippo_create_occ_mesh_object")

        occ = hippo_load_occ_core()
        sid = occ.make_shape_from_mesh(vertices, faces)
        data = occ.remesh_shape(sid, 0.1)
        obj = hippo_create_occ_mesh_object(
            context, name, data,
            location=Vector(location or (0, 0, 0))
        )
        obj["hippo_occ_type"] = "sverchok_baked"
        return obj
    except Exception:
        pass

    # --- Fallback: plain Blender mesh object ----------------------------------
    mesh = bpy.data.meshes.new(name + "_Mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    if location is not None:
        obj.location = Vector(location)
    context.collection.objects.link(obj)
    obj["hippo_kernel"] = "occ"
    obj["hippo_occ_type"] = "sverchok_baked"
    return obj


# ---------------------------------------------------------------------------
# Node definition
# ---------------------------------------------------------------------------

if SVERCHOK_AVAILABLE:
    class SvHippo3DOCCViewer(bpy.types.Node, SverchCustomTreeNode):
        bl_idname = 'SvHippo3DOCCViewer'
        bl_label = 'Hippo3D OCC Viewer'
        bl_icon = 'MESH_CUBE'

        base_name: bpy.props.StringProperty(
            name="Base Name",
            default="Hippo3D_Svk",
            description="Base name for baked objects. An index is appended.",
        )

        def sv_init(self, context):
            self.inputs.new('SvVerticesSocket', "Vertices")
            self.inputs.new('SvStringsSocket', "Faces")
            self.inputs.new('SvVerticesSocket', "Location").sv_set([[0.0, 0.0, 0.0]])

        def draw_buttons(self, context, layout):
            layout.prop(self, "base_name")

        def process(self):
            verts_socket = self.inputs['Vertices']
            faces_socket = self.inputs['Faces']
            loc_socket = self.inputs['Location']

            if not verts_socket.is_linked:
                return

            verts_nested = verts_socket.sv_get(deepcopy=False, default=[])
            faces_nested = faces_socket.sv_get(deepcopy=False, default=[])
            loc_nested = loc_socket.sv_get(deepcopy=False, default=[[[0.0, 0.0, 0.0]]])

            context = bpy.context

            def flatten_if_nested(data):
                if not data:
                    return []
                if isinstance(data[0], (int, float)):
                    return [[data]]
                if isinstance(data[0], (list, tuple)) and len(data[0]) > 0 and isinstance(data[0][0], (int, float)):
                    return [data]
                return data

            verts_objects = flatten_if_nested(verts_nested)
            faces_objects = flatten_if_nested(faces_nested)
            loc_objects = flatten_if_nested(loc_nested)

            max_count = max(len(verts_objects), len(faces_objects), len(loc_objects))

            for i in range(max_count):
                v_obj = verts_objects[i % len(verts_objects)]
                f_obj = faces_objects[i % len(faces_objects)] if faces_objects else []
                l_obj = loc_objects[i % len(loc_objects)] if loc_objects else [[[0.0, 0.0, 0.0]]]

                vertices = []
                for v in v_obj:
                    if isinstance(v, (list, tuple)) and len(v) >= 3:
                        vertices.append((float(v[0]), float(v[1]), float(v[2])))
                    elif isinstance(v, Vector):
                        vertices.append((v.x, v.y, v.z))

                faces = []
                for f in f_obj:
                    if isinstance(f, (list, tuple)):
                        faces.append([int(idx) for idx in f])

                location = (0.0, 0.0, 0.0)
                if l_obj and len(l_obj) > 0:
                    first = l_obj[0]
                    if isinstance(first, (list, tuple)) and len(first) >= 3:
                        location = (float(first[0]), float(first[1]), float(first[2]))
                    elif isinstance(first, Vector):
                        location = (first.x, first.y, first.z)

                if not vertices or not faces:
                    continue

                name = f"{self.base_name}_{i:03d}"
                _bake_hippo3d_occ_object(context, name, vertices, faces, location=location)
