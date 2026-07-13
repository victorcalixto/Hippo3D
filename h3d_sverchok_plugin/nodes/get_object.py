# SPDX-License-Identifier: GPL-3.0-or-later
"""Hippo3D Get Object node for Sverchok.

Reads Blender objects tagged as Hippo3D OCC objects and outputs
their geometry as mesh vertices/faces and optionally as Sverchok Extra
surfaces or Solids solids.
"""

import bpy

from sverchok.node_tree import SverchCustomTreeNode
from sverchok.data_structure import updateNode, match_long_repeat

from ..dependency_check import SVERCHOK_EXTRA_AVAILABLE, FREECAD_AVAILABLE


# ---------------------------------------------------------------------------
# Surface / Solid wrappers
# ---------------------------------------------------------------------------

def _try_wrap_as_surface(obj):
    """Wrap a Hippo3D OCC mesh object as a Sverchok Extra surface dict."""
    if not SVERCHOK_EXTRA_AVAILABLE:
        return None
    try:
        mesh = obj.data
        if mesh is None or obj.type != "MESH":
            return None
        verts = [tuple(v.co) for v in mesh.vertices]
        faces = [list(p.vertices) for p in mesh.polygons]
        return {"type": "hippo3d_surface", "vertices": verts, "faces": faces, "name": obj.name}
    except Exception:
        return None


def _try_wrap_as_solid(obj):
    """Wrap a Hippo3D OCC mesh object as a FreeCAD Part solid."""
    if not FREECAD_AVAILABLE:
        return None
    try:
        import Part
        mesh = obj.data
        if mesh is None or obj.type != "MESH":
            return None
        verts = [tuple(v.co) for v in mesh.vertices]
        faces = [list(p.vertices) for p in mesh.polygons]
        triangles = []
        for f in faces:
            if len(f) >= 3:
                for i in range(1, len(f) - 1):
                    triangles.append((f[0], f[i], f[i + 1]))
        fc_faces = []
        for a, b, c in triangles:
            v0 = verts[a]
            v1 = verts[b]
            v2 = verts[c]
            fc_faces.append(Part.Face(Part.makePolygon([v0, v1, v2, v0])))
        if not fc_faces:
            return None
        shell = Part.Shell(fc_faces)
        solid = Part.Solid(shell)
        return solid
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Node definition
# ---------------------------------------------------------------------------

class SvHippo3DGetObject(bpy.types.Node, SverchCustomTreeNode):
    bl_idname = 'SvHippo3DGetObject'
    bl_label = 'Hippo3D Get Object'
    bl_icon = 'MESH_DATA'

    def sv_init(self, context):
        self.inputs.new('SvStringsSocket', "Objects")
        self.outputs.new('SvVerticesSocket', "Vertices")
        self.outputs.new('SvStringsSocket', "Faces")
        self.outputs.new('SvStringsSocket', "Surfaces")
        self.outputs.new('SvStringsSocket', "Solids")

    def process(self):
        if not self.outputs['Vertices'].is_linked and not self.outputs['Faces'].is_linked \
                and not self.outputs['Surfaces'].is_linked and not self.outputs['Solids'].is_linked:
            return

        objects_socket = self.inputs['Objects']
        if not objects_socket.is_linked:
            return

        object_names_nested = objects_socket.sv_get(deepcopy=False, default=[])

        out_verts = []
        out_faces = []
        out_surfaces = []
        out_solids = []

        for object_names in object_names_nested:
            verts_sub = []
            faces_sub = []
            surfaces_sub = []
            solids_sub = []

            for name in object_names:
                if isinstance(name, bpy.types.Object):
                    obj = name
                else:
                    obj = bpy.data.objects.get(str(name))

                if obj is None:
                    continue

                if obj.get("hippo_kernel") != "occ":
                    continue

                mesh = obj.data
                if mesh is None or obj.type != "MESH":
                    continue

                local_verts = [tuple(v.co) for v in mesh.vertices]
                local_faces = [list(p.vertices) for p in mesh.polygons]

                verts_sub.append(local_verts)
                faces_sub.append(local_faces)

                if self.outputs['Surfaces'].is_linked:
                    surf = _try_wrap_as_surface(obj)
                    if surf is not None:
                        surfaces_sub.append(surf)

                if self.outputs['Solids'].is_linked:
                    solid = _try_wrap_as_solid(obj)
                    if solid is not None:
                        solids_sub.append(solid)

            out_verts.append(verts_sub)
            out_faces.append(faces_sub)
            out_surfaces.append(surfaces_sub)
            out_solids.append(solids_sub)

        self.outputs['Vertices'].sv_set(out_verts)
        self.outputs['Faces'].sv_set(out_faces)
        self.outputs['Surfaces'].sv_set(out_surfaces)
        self.outputs['Solids'].sv_set(out_solids)


# ---------------------------------------------------------------------------
# Module registration
# ---------------------------------------------------------------------------

classes = [SvHippo3DGetObject]


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
