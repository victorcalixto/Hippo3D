import sys
import bpy

sys.path.append("/home/klx/github/blender-development/Hippo3D/native/build")

import hippo_occ_core


def create_mesh_object(name, data, location):
    mesh = bpy.data.meshes.new(name + "_Mesh")
    mesh.from_pydata(data["vertices"], [], data["faces"])
    mesh.update()

    obj = bpy.data.objects.new(name, mesh)
    obj.location = location
    obj["hippo_kernel"] = "occ"

    bpy.context.collection.objects.link(obj)
    return obj


create_mesh_object("Hippo3D_OCC_Box", hippo_occ_core.make_box_mesh(4, 4, 4), (-10, 0, 0))
create_mesh_object("Hippo3D_OCC_Sphere", hippo_occ_core.make_sphere_mesh(2), (-5, 0, 0))
create_mesh_object("Hippo3D_OCC_Cylinder", hippo_occ_core.make_cylinder_mesh(2, 5), (0, 0, 0))
create_mesh_object("Hippo3D_OCC_Cone", hippo_occ_core.make_cone_mesh(2, 0, 5), (5, 0, 0))
create_mesh_object("Hippo3D_OCC_Torus", hippo_occ_core.make_torus_mesh(2.5, 0.6), (10, 0, 0))
