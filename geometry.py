# SPDX-License-Identifier: GPL-3.0-or-later
"""Basic curve geometry, snapping, coordinates, and object utilities."""

from .common import *
from .state import *
from .cplanes import *

# -----------------------------------------------------------------------------
# Object snapping helpers for CPlane creation
# -----------------------------------------------------------------------------

def nearest_mesh_vertex_snap(context, raw_point, mouse_xy, radius=18.0):
    """Snap to nearest visible mesh/object vertex in screen space."""
    best = None
    best_dist = 1e18

    for obj in context.scene.objects:
        if obj.type != "MESH" or not obj.visible_get():
            continue

        mw = obj.matrix_world
        mesh = obj.data

        for vert in mesh.vertices:
            p = mw @ vert.co
            d = screen_distance(context, p, mouse_xy)
            if d < best_dist and d <= radius:
                best = p
                best_dist = d

    return best


def raycast_mesh_face(context, event):
    """Return face hit data from viewport raycast against scene objects."""
    region = context.region
    rv3d = context.region_data
    coord = (event.mouse_region_x, event.mouse_region_y)

    origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
    direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)

    depsgraph = context.evaluated_depsgraph_get()
    ok, loc, normal, face_index, obj, matrix = context.scene.ray_cast(
        depsgraph,
        origin,
        direction,
        distance=100000.0,
    )

    if not ok or obj is None:
        return None

    return {
        "location": loc.copy(),
        "normal": normal.normalized(),
        "face_index": face_index,
        "object": obj,
    }


def get_face_cplane_axes_from_hit(hit):
    """Build a CPlane from a mesh face hit using face normal and stable tangent axes."""
    obj = hit["object"]
    normal = hit["normal"].normalized()
    origin = hit["location"].copy()

    # Try to use the first edge of the hit polygon as U axis.
    u = None
    if obj and obj.type == "MESH" and hit["face_index"] >= 0:
        try:
            poly = obj.data.polygons[hit["face_index"]]
            verts = [obj.matrix_world @ obj.data.vertices[i].co for i in poly.vertices]
            if len(verts) >= 2:
                edge = verts[1] - verts[0]
                if edge.length > 1e-8:
                    u = edge.normalized()
        except Exception:
            u = None

    if u is None or u.length < 1e-8 or abs(u.dot(normal)) > 0.98:
        # fallback tangent using global axis least aligned to normal
        base = Vector((1, 0, 0))
        if abs(base.dot(normal)) > 0.9:
            base = Vector((0, 1, 0))
        u = (base - normal * base.dot(normal)).normalized()

    v = normal.cross(u).normalized()
    # Recompute u to guarantee orthogonal frame
    u = v.cross(normal).normalized()

    return origin, u, v, normal

# -----------------------------------------------------------------------------
# Geometry helpers
# -----------------------------------------------------------------------------

def create_line(p1, p2):
    curve = bpy.data.curves.new("Hippo3D_Line", type="CURVE")
    curve.dimensions = "3D"
    curve.resolution_u = 1

    spline = curve.splines.new("POLY")
    spline.points.add(1)
    spline.points[0].co = (p1.x, p1.y, p1.z, 1.0)
    spline.points[1].co = (p2.x, p2.y, p2.z, 1.0)

    obj = bpy.data.objects.new("Hippo3D_Line", curve)
    bpy.context.collection.objects.link(obj)
    return obj





def create_polyline(points, name="Hippo3D_Polyline", closed=False):
    """Create a Blender curve polyline from a list of mathutils.Vector points."""
    if len(points) < 2:
        return None
    curve = bpy.data.curves.new(name, type="CURVE")
    curve.dimensions = "3D"
    curve.resolution_u = 1

    spline = curve.splines.new("POLY")
    count = len(points) + (1 if closed else 0)
    spline.points.add(count - 1)
    out_points = list(points)
    if closed:
        out_points.append(points[0])
    for pnt, co in zip(spline.points, out_points):
        pnt.co = (co.x, co.y, co.z, 1.0)

    obj = bpy.data.objects.new(name, curve)
    bpy.context.collection.objects.link(obj)

    xs = [p.x for p in points]
    ys = [p.y for p in points]
    zs = [p.z for p in points]
    obj["cad_center"] = ((min(xs) + max(xs)) * 0.5, (min(ys) + max(ys)) * 0.5, (min(zs) + max(zs)) * 0.5)
    obj["cad_shape"] = "rectangle" if closed and len(points) == 4 else "polyline"
    return obj


def create_rectangle(p1, p2, context=None):
    """Create a rectangle on the active CPlane from two opposite corners."""
    if context is None:
        context = bpy.context
    l1 = world_to_cplane(context, p1)
    l2 = world_to_cplane(context, p2)
    z = l1.z
    pts = [
        cplane_to_world(context, l1.x, l1.y, z),
        cplane_to_world(context, l2.x, l1.y, z),
        cplane_to_world(context, l2.x, l2.y, z),
        cplane_to_world(context, l1.x, l2.y, z)]
    obj = create_polyline(pts, name="Hippo3D_Rectangle", closed=True)
    if obj:
        obj["cad_cplane"] = active_cplane_label(context)
    return obj


def create_circle(center, edge, context=None):
    """Create a simple CPlane-oriented circle as a closed poly curve."""
    if context is None:
        context = bpy.context
    lc = world_to_cplane(context, center)
    le = world_to_cplane(context, edge)
    radius = ((le.x - lc.x) ** 2 + (le.y - lc.y) ** 2) ** 0.5
    if radius < 1e-8:
        return None
    import math
    pts = []
    steps = 96
    for i in range(steps):
        a = 2 * math.pi * i / steps
        pts.append(cplane_to_world(context, lc.x + math.cos(a) * radius, lc.y + math.sin(a) * radius, lc.z))
    obj = create_polyline(pts, name="Hippo3D_Circle", closed=True)
    if obj:
        obj["cad_center"] = (center.x, center.y, center.z)
        obj["cad_radius"] = radius
        obj["cad_shape"] = "circle"
        obj["cad_cplane"] = active_cplane_label(context)
    return obj


def create_nurbs_curve(points, degree=3, name="Hippo3D_NURBS_Curve"):
    """Create a Blender NURBS curve from control points.

    Rhino-style degree-3 curves need at least 4 control points. For 2-3 points,
    Blender uses the highest possible order so the command remains forgiving while
    drawing early test curves.
    """
    if len(points) < 2:
        return None

    curve = bpy.data.curves.new(name, type="CURVE")
    curve.dimensions = "3D"
    curve.resolution_u = 24

    spline = curve.splines.new("NURBS")
    spline.points.add(len(points) - 1)

    for pnt, co in zip(spline.points, points):
        pnt.co = (co.x, co.y, co.z, 1.0)

    effective_degree = max(1, min(int(degree), len(points) - 1))
    spline.order_u = effective_degree + 1
    spline.use_endpoint_u = True

    obj = bpy.data.objects.new(name, curve)
    bpy.context.collection.objects.link(obj)
    obj["cad_shape"] = "nurbs_curve"
    obj["cad_degree"] = effective_degree
    return obj




def set_selected_nurbs_degree(context, degree):
    """Set degree/order of selected Blender NURBS curves.

    Phase 1 behaviour:
    - Works on selected Curve objects with NURBS splines.
    - Clamps degree to available control points - 1.
    - Preserves existing control points and endpoint mode.
    """
    try:
        degree = int(degree)
    except Exception:
        return False, "Degree must be an integer."

    degree = max(1, min(degree, 11))

    changed = 0
    skipped = 0

    for obj in context.selected_objects:
        if obj.type != "CURVE":
            skipped += 1
            continue

        any_changed = False

        for spline in obj.data.splines:
            if spline.type != "NURBS":
                skipped += 1
                continue

            point_count = len(spline.points)
            if point_count < 2:
                skipped += 1
                continue

            effective_degree = max(1, min(degree, point_count - 1))
            spline.order_u = effective_degree + 1
            spline.use_endpoint_u = True
            obj["cad_degree"] = effective_degree
            obj["cad_shape"] = "nurbs_curve"
            any_changed = True

        if any_changed:
            obj.data.update_tag()
            changed += 1

    if changed == 0:
        return False, "No selected NURBS curves were updated."

    return True, f"Updated {changed} selected NURBS curve(s) to degree {degree}."



def _bspline_basis(i, k, t, knots):
    """Cox-de Boor basis for preview only."""
    if k == 0:
        if knots[i] <= t < knots[i + 1] or (t == knots[-1] and knots[i] <= t <= knots[i + 1]):
            return 1.0
        return 0.0
    denom1 = knots[i + k] - knots[i]
    denom2 = knots[i + k + 1] - knots[i + 1]
    term1 = 0.0
    term2 = 0.0
    if denom1 > 1e-12:
        term1 = (t - knots[i]) / denom1 * _bspline_basis(i, k - 1, t, knots)
    if denom2 > 1e-12:
        term2 = (knots[i + k + 1] - t) / denom2 * _bspline_basis(i + 1, k - 1, t, knots)
    return term1 + term2


def preview_nurbs_points(points, degree=3, samples=48):
    """Return sampled points for an open, clamped NURBS/B-spline preview."""
    n = len(points)
    if n < 2:
        return []
    d = max(1, min(int(degree), n - 1))
    # Open clamped knot vector: repeated ends, uniform interior.
    interior_count = n - d - 1
    knots = [0.0] * (d + 1)
    if interior_count > 0:
        for j in range(1, interior_count + 1):
            knots.append(j / (interior_count + 1))
    knots += [1.0] * (d + 1)

    out = []
    for si in range(samples + 1):
        t = si / samples
        p = Vector((0.0, 0.0, 0.0))
        total = 0.0
        for i, cp in enumerate(points):
            b = _bspline_basis(i, d, t, knots)
            p += cp * b
            total += b
        if total > 1e-12:
            p /= total
        out.append(p)
    return out


def convert_selected_to_mesh(context):
    """Convert selected objects to meshes using Blender's built-in conversion."""
    selected = list(context.selected_objects)
    if not selected:
        return False, "Select at least one object to convert."

    # Conversion requires Object Mode and an active selected object.
    if context.object and context.object.mode != "OBJECT":
        try:
            bpy.ops.object.mode_set(mode="OBJECT")
        except Exception:
            pass

    convertable = [o for o in selected if o.type in {"CURVE", "FONT", "SURFACE", "META", "MESH"}]
    if not convertable:
        return False, "No selected curve/surface/font/meta/mesh objects to convert."

    bpy.ops.object.select_all(action="DESELECT")
    for obj in convertable:
        obj.select_set(True)
    context.view_layer.objects.active = convertable[0]

    try:
        bpy.ops.object.convert(target="MESH")
    except Exception as exc:
        return False, f"Convert to mesh failed: {exc}"

    return True, f"Converted {len(convertable)} object(s) to mesh."


def join_selected_objects(context):
    """Join selected objects using Blender's built-in join operator."""
    selected = list(context.selected_objects)
    if len(selected) < 2:
        return False, "Select at least two objects to join."

    if context.object and context.object.mode != "OBJECT":
        try:
            bpy.ops.object.mode_set(mode="OBJECT")
        except Exception:
            pass

    # Blender's Join works best when the active object is one of the selected objects.
    context.view_layer.objects.active = selected[0]
    for obj in selected:
        obj.select_set(True)

    try:
        bpy.ops.object.join()
    except Exception as exc:
        return False, f"Join failed: {exc}"

    return True, f"Joined {len(selected)} object(s)."



def run_cplane_command(context, cmd):
    """Handle CPlane commands. Returns (handled, ok, message, start_3pt_name)."""
    raw = cmd.strip()
    low = raw.lower()
    if not low.startswith("cplane"):
        return False, None, "", ""
    parts = raw.replace("_", " ").split()
    if len(parts) == 1:
        return True, True, "Use: cplane top/front/right/world/save/restore/3pt/world, cplane save <name>, cplane restore <name>, cplane 3pt <name>, cplane xaxis <name>, cplane zaxis <name>, cplane face <name>, cplane perpcurve <name>, cplane rotate <x|y|z> <angle> [name], cplane rotate3pt [name], cplane axisrotate [name], cplane move [name], cplane view, cplane camera [distance], cplane list, cplane delete <name>.", ""
    action = parts[1].lower()
    if action in {"top", "world", "front", "right"}:
        set_builtin_cplane(context, action.upper())
        return True, True, f"CPlane set to {action.title()}.", ""
    if action in {"save", "s"}:
        if len(parts) < 3:
            return True, False, "Use: cplane save <name>", ""
        ok, msg = save_current_cplane_as(context, " ".join(parts[2:]))
        return True, ok, msg, ""
    if action in {"restore", "use", "set", "r"}:
        if len(parts) < 3:
            return True, False, "Use: cplane restore <name>", ""
        name = " ".join(parts[2:])
        if set_named_cplane(context, name):
            return True, True, f"Restored CPlane '{name}'.", ""
        return True, False, f"No saved CPlane named '{name}'.", ""
    if action in {"xaxis", "x", "x-axis"}:
        if len(parts) < 3:
            return True, False, "Use: cplane xaxis <name>", ""
        return True, True, f"CPlane X-axis mode started.", "XAXIS:" + " ".join(parts[2:])
    if action in {"zaxis", "z", "z-axis"}:
        if len(parts) < 3:
            return True, False, "Use: cplane zaxis <name>", ""
        return True, True, f"CPlane Z-axis mode started.", "ZAXIS:" + " ".join(parts[2:])
    if action in {"face", "objectface", "objface"}:
        if len(parts) < 3:
            return True, False, "Use: cplane face <name>", ""
        return True, True, f"CPlane face mode started.", "FACE:" + " ".join(parts[2:])
    if action in {"perpcurve", "curveperp", "perpendicularcurve", "perptocurve", "perpendicular"}:
        name = " ".join(parts[2:]) if len(parts) >= 3 else "CPlane Perp Curve"
        return True, True, "CPlane perpendicular-to-curve mode started.", "CURVEPERP:" + name
    if action in {"view", "viewto", "viewtocplane"}:
        ok, msg = view_to_active_cplane(context)
        return True, ok, msg, ""
    if action in {"camera", "cam", "cameratocplane", "camera_to_cplane"}:
        distance = 20.0
        if len(parts) >= 3:
            try:
                distance = float(parts[2])
            except Exception:
                distance = 20.0
        ok, msg = camera_to_active_cplane(context, distance)
        return True, ok, msg, ""
    if action in {"move", "mv"}:
        name = " ".join(parts[2:]) if len(parts) >= 3 else ""
        return True, True, "CPlane Move mode started.", "MOVE:" + name
    if action in {"axisrotate", "rotateaxis", "rotaxis", "axisrot"}:
        name = " ".join(parts[2:]) if len(parts) >= 3 else ""
        return True, True, "CPlane Axis Rotate mode started.", "AXISROTATE:" + name
    if action in {"rotate3pt", "rot3pt", "rotate3", "rot3"}:
        name = " ".join(parts[2:]) if len(parts) >= 3 else ""
        return True, True, "CPlane Rotate3Pt mode started.", "ROTATE3PT:" + name
    if action in {"rotate", "rot"}:
        if len(parts) < 4:
            return True, False, "Use: cplane rotate <x|y|z> <angle> [name]", ""

        axis_name = parts[2].upper()
        try:
            angle = float(parts[3])
        except Exception:
            return True, False, "Angle must be a number in degrees.", ""

        if len(parts) >= 5:
            name = " ".join(parts[4:])
            ok, msg = rotate_cplane_by_name(context, name, axis_name, angle)
        else:
            ok, msg = rotate_active_cplane(context, axis_name, angle)

        return True, ok, msg, ""
    if action in {"delete", "del"}:
        if len(parts) < 3:
            return True, False, "Use: cplane delete <name>", ""
        name = " ".join(parts[2:])
        data = load_saved_cplanes(context)
        if name not in data:
            return True, False, f"No saved CPlane named '{name}'.", ""
        del data[name]
        save_saved_cplanes(context, data)
        if getattr(context.scene, "cad_active_cplane_name", "") == name:
            context.scene.cad_active_cplane_name = ""
        return True, True, f"Deleted CPlane '{name}'.", ""
    if action in {"list", "ls"}:
        names = cplane_library_names(context)
        return True, True, "Saved CPlanes: " + (", ".join(names) if names else "none"), ""
    if action in {"3pt", "3point", "threepoint"}:
        if len(parts) < 3:
            return True, False, "Use: cplane 3pt <name>", ""
        return True, True, "Pick/type 3 points: origin, X-axis point, then Y-direction point.", " ".join(parts[2:])
    return True, False, "Unknown CPlane command. Use: cplane top/front/right/world/save/restore/3pt/world, save, restore, 3pt, list, delete.", ""


def world_to_screen(context, point):
    if not context.region or not context.region_data:
        return None
    return view3d_utils.location_3d_to_region_2d(context.region, context.region_data, point)


def screen_distance(context, point, mouse_xy):
    p2d = world_to_screen(context, point)
    if p2d is None:
        return 1e18
    dx = p2d.x - mouse_xy[0]
    dy = p2d.y - mouse_xy[1]
    return (dx * dx + dy * dy) ** 0.5
