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

from ..dependency_check import HIPPO3D_OCC_AVAILABLE


# ---------------------------------------------------------------------------
# OCC baking helper
# ---------------------------------------------------------------------------

def _bake_hippo3d_occ_object(context, name, vertices, faces, location=None):
    """Create or update a Hippo3D OCC object from raw mesh data.

    Uses the hippo_load_occ_core native wrapper to build an OCC shape
    and then creates the standard Hippo3D OCC preview mesh object.
    """
    if not HIPPO3D_OCC_AVAILABLE:
        # Fallback: create a plain mesh object without OCC backend
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

    try:
        # Import Hippo3D OCC helpers from the main add-on
        from ...main import hippo_load_occ_core, hippo_create_occ_mesh_object
        occ = hippo_load_occ_core()
    except Exception as exc:
        # Fallback to plain mesh
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

    # Build an OCC shape from the mesh and remesh it for display
    try:
        sid = occ.make_shape_from_mesh(vertices, faces)
        data = occ.remesh_shape(sid, 0.1)
        obj = hippo_create_occ_mesh_object(context, name, data, location=Vector(location or (0, 0, 0)))
        obj["hippo_occ_type"] = "sverchok_baked"
        return obj
    except Exception:
        # Final fallback
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

            # Flatten outer list if nested by object count
            # Sverchok typically sends [[obj_verts], [obj_verts], ...]
            # where each obj_verts is itself a list of (x, y, z) tuples.
            def flatten_if_nested(data, depth=3):
                """Try to flatten to a consistent structure of depth-3 nested lists."""
                if not data:
                    return []
                # Heuristic: if first element is a number, wrap in lists
                if isinstance(data[0], (int, float)):
                    return [[data]]
                # If first element is a list of numbers, wrap once
                if isinstance(data[0], (list, tuple)) and len(data[0]) > 0 and isinstance(data[0][0], (int, float)):
                    return [data]
                return data

            verts_objects = flatten_if_nested(verts_nested)
            faces_objects = flatten_if_nested(faces_nested)
            loc_objects = flatten_if_nested(loc_nested)

            # Match list lengths
            max_count = max(len(verts_objects), len(faces_objects), len(loc_objects))

            for i in range(max_count):
                v_obj = verts_objects[i % len(verts_objects)]
                f_obj = faces_objects[i % len(faces_objects)] if faces_objects else []
                l_obj = loc_objects[i % len(loc_objects)] if loc_objects else [[[0.0, 0.0, 0.0]]]

                # Expect v_obj is list of (x,y,z) tuples or lists
                # f_obj is list of face vertex index lists
                # l_obj is list of (x,y,z) location — take the first one
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
