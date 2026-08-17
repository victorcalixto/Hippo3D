"""
Run inside Blender 5.0 background:
    blender --background --python /home/klx/github/blender-development/Hippo3D/test_sverchok_pipe.py

Builds two minimal Sverchok node trees:
  Tree 1: Box -> Hippo3D Get Object -> Solids socket.
  Tree 2: OCC Loft -> Hippo3D Get Object -> Surfaces socket.

Uses sverchok.core.update_system.UpdateTree.main_update to process the tree.
"""
import sys
import os
import bpy

addon_dir = "/home/klx/github/blender-development/Hippo3D"
if addon_dir not in sys.path:
    sys.path.insert(0, addon_dir)

# Ensure Sverchok + Hippo3D add-ons are enabled
def ensure_addon(name):
    if name not in bpy.context.preferences.addons:
        bpy.ops.preferences.addon_enable(module=name)
        print(f"Enabled {name}")
    else:
        print(f"{name} already enabled")

ensure_addon("sverchok-master")
ensure_addon("h3d_sverchok_plugin")
ensure_addon("Hippo3D")

import sverchok
from sverchok.core.update_system import UpdateTree

# Hippo3D OCC core import
sys.path.append(os.path.join(addon_dir, "native", "build"))
import hippo_occ_core

box_name = "Hippo3D_OCC_Box_Pipe"
loft_name = "Hippo3D_OCC_Loft_Pipe"

# ---------------------------------------------------------------------------
# Create test objects
# ---------------------------------------------------------------------------
if box_name not in bpy.data.objects:
    data = hippo_occ_core.make_box_mesh(4, 4, 4)
    mesh = bpy.data.meshes.new(box_name + "_Mesh")
    mesh.from_pydata(data["vertices"], [], data["faces"])
    mesh.update()
    obj = bpy.data.objects.new(box_name, mesh)
    obj["hippo_kernel"] = "occ"
    obj["hippo_occ_preview"] = True
    obj["hippo_occ_shape_id"] = int(data.get("shape_id", 0))
    bpy.context.collection.objects.link(obj)
else:
    obj = bpy.data.objects[box_name]
    obj["hippo_kernel"] = "occ"
    obj["hippo_occ_preview"] = True

if loft_name not in bpy.data.objects:
    wires = []
    for z, size in [(0, 2.0), (2, 3.0), (4, 2.5)]:
        pts = [
            (-size, -size, z), (size, -size, z),
            (size, size, z), (-size, size, z),
            (-size, -size, z)
        ]
        wires.append(hippo_occ_core.make_polyline_wire(pts, closed=True))
    loft_id = hippo_occ_core.occ_loft(wires, closed=False, solid=False)
    data = hippo_occ_core.remesh_shape(loft_id, 0.1)
    mesh = bpy.data.meshes.new(loft_name + "_Mesh")
    mesh.from_pydata(data["vertices"], [], data["faces"])
    mesh.update()
    lobj = bpy.data.objects.new(loft_name, mesh)
    lobj["hippo_kernel"] = "occ"
    lobj["hippo_occ_preview"] = True
    lobj["hippo_occ_shape_id"] = int(loft_id)
    lobj["hippo_occ_nurbs_shape_id"] = int(loft_id)
    bpy.context.collection.objects.link(lobj)
else:
    lobj = bpy.data.objects[loft_name]
    lobj["hippo_kernel"] = "occ"
    lobj["hippo_occ_preview"] = True

# ---------------------------------------------------------------------------
# Helper to build a minimal tree and inspect outputs
# ---------------------------------------------------------------------------
def run_tree_for_object(target_obj, link_output_name):
    tree_name = f"Hippo3D_Pipe_Test_{target_obj.name}"
    if tree_name in bpy.data.node_groups:
        tree = bpy.data.node_groups[tree_name]
        tree.nodes.clear()
    else:
        tree = bpy.data.node_groups.new(type="SverchCustomTreeType", name=tree_name)

    links = tree.links

    node_get = tree.nodes.new("SvGetObjectsDataMK5")
    node_get.object_names.clear()
    item = node_get.object_names.add()
    item.pointer_type = 'OBJECT'
    item.object_pointer = target_obj
    item.name = target_obj.name

    node_hippo = tree.nodes.new("SvHippo3DGetObject")
    node_hippo.location = (200, 0)
    links.new(node_get.outputs["objects"], node_hippo.inputs["Objects"])

    out_sock = node_hippo.outputs.get(link_output_name)
    if out_sock is None:
        print(f"Output socket {link_output_name} not found on node")
        return {}, node_hippo

    node_sink = tree.nodes.new("NodeReroute")
    node_sink.location = (400, 0)
    links.new(out_sock, node_sink.inputs[0])

    UpdateTree.reset_tree(tree)
    gen = UpdateTree.main_update(tree)
    try:
        while True:
            next(gen)
    except StopIteration:
        pass

    results = {}
    for sock_name in ["Vertices", "Faces", "Surfaces", "Solids"]:
        sock = node_hippo.outputs.get(sock_name)
        if sock is None:
            continue
        try:
            results[sock_name] = sock.sv_get(deepcopy=False)
        except Exception as e:
            results[sock_name] = f"ERROR: {e}"
    return results, node_hippo


print("\n=== Box Solid Test Results ===")
box_results, _ = run_tree_for_object(bpy.data.objects[box_name], "Solids")
for sock_name, data in box_results.items():
    print(f"Output {sock_name}: {data}")

print("\n=== Loft Surface Test Results ===")
loft_results, _ = run_tree_for_object(bpy.data.objects[loft_name], "Surfaces")
for sock_name, data in loft_results.items():
    print(f"Output {sock_name}: {data}")
