# SPDX-License-Identifier: GPL-3.0-or-later
"""Construction plane tools and helpers."""

from .common import *
from .state import *

def _builtin_cplane_axes(mode):
    """Built-in Rhino-like construction plane presets."""
    origin = Vector((0.0, 0.0, 0.0))
    mode = (mode or "TOP").upper()
    if mode in {"TOP", "WORLD"}:
        return origin, Vector((1, 0, 0)), Vector((0, 1, 0)), Vector((0, 0, 1))
    if mode == "FRONT":
        return origin, Vector((1, 0, 0)), Vector((0, 0, 1)), Vector((0, -1, 0))
    if mode == "RIGHT":
        return origin, Vector((0, 1, 0)), Vector((0, 0, 1)), Vector((1, 0, 0))
    return origin, Vector((1, 0, 0)), Vector((0, 1, 0)), Vector((0, 0, 1))


def load_saved_cplanes(context):
    """Return the named CPlane library stored on the scene."""
    raw = getattr(context.scene, "cad_cplanes_json", "{}") if context and context.scene else "{}"
    try:
        data = json.loads(raw or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_saved_cplanes(context, data):
    context.scene.cad_cplanes_json = json.dumps(data, sort_keys=True)


def set_builtin_cplane(context, mode):
    context.scene.cad_cplane = mode.upper()
    context.scene.cad_active_cplane_name = ""


def set_named_cplane(context, name):
    data = load_saved_cplanes(context)
    if name not in data:
        return False
    context.scene.cad_active_cplane_name = name
    return True


def cplane_record_to_axes(record):
    origin = Vector(record.get("origin", (0, 0, 0)))
    u = Vector(record.get("u", (1, 0, 0))).normalized()
    v = Vector(record.get("v", (0, 1, 0))).normalized()
    # Re-orthogonalise V and compute normal to avoid drift from saved data.
    v = (v - u * v.dot(u)).normalized()
    n = u.cross(v).normalized()
    if n.length < 1e-8:
        return _builtin_cplane_axes("TOP")
    return origin, u, v, n


def get_cplane_axes(context):
    """Return origin, U axis, V axis and normal for the active CAD construction plane.

    If a named CPlane is active, it comes from the scene-level CPlane library.
    Otherwise, one of the built-in presets is used.
    """
    if context and context.scene:
        active_name = getattr(context.scene, "cad_active_cplane_name", "")
        if active_name:
            data = load_saved_cplanes(context)
            if active_name in data:
                return cplane_record_to_axes(data[active_name])
        mode = getattr(context.scene, "cad_cplane", "TOP")
        return _builtin_cplane_axes(mode)
    return _builtin_cplane_axes("TOP")


def cplane_to_world(context, x=0.0, y=0.0, z=0.0):
    origin, u, v, n = get_cplane_axes(context)
    return origin + u * x + v * y + n * z


def world_to_cplane(context, point):
    origin, u, v, n = get_cplane_axes(context)
    d = point - origin
    return Vector((d.dot(u), d.dot(v), d.dot(n)))


def active_cplane_label(context):
    if context and context.scene:
        active_name = getattr(context.scene, "cad_active_cplane_name", "")
        if active_name:
            return active_name
        return getattr(context.scene, "cad_cplane", "TOP").title()
    return "Top"


def save_current_cplane_as(context, name):
    name = (name or "").strip()
    if not name:
        return False, "Give the CPlane a name."
    origin, u, v, n = get_cplane_axes(context)
    data = load_saved_cplanes(context)
    data[name] = {
        "origin": [origin.x, origin.y, origin.z],
        "u": [u.x, u.y, u.z],
        "v": [v.x, v.y, v.z],
    }
    save_saved_cplanes(context, data)
    try:
        sync_cplane_layer_collection(context)
    except Exception:
        pass
    return True, f"Saved CPlane '{name}'. Use the dropdown or cplane restore to activate it."


def create_cplane_from_3_points(context, name, points, mode="3PT"):
    if len(points) < 3:
        return False, "A CPlane needs 3 points."

    mode = (mode or "3PT").upper()
    origin = points[0]

    if mode in {"3PT", "XY"}:
        # origin, X-axis point, Y-direction point
        xpt, ypt = points[1], points[2]
        u = xpt - origin
        temp_v = ypt - origin

        if u.length < 1e-8 or temp_v.length < 1e-8:
            return False, "CPlane points are too close together."

        u.normalize()
        n = u.cross(temp_v)

        if n.length < 1e-8:
            return False, "CPlane points are collinear."

        n.normalize()
        v = n.cross(u).normalized()

    elif mode == "XAXIS":
        # origin, X-axis point, Z-direction point
        xpt, zpt = points[1], points[2]
        u = xpt - origin
        temp_n = zpt - origin

        if u.length < 1e-8 or temp_n.length < 1e-8:
            return False, "CPlane points are too close together."

        u.normalize()
        n = temp_n - u * temp_n.dot(u)

        if n.length < 1e-8:
            return False, "X-axis and Z-direction are collinear."

        n.normalize()
        v = n.cross(u).normalized()

    elif mode == "ZAXIS":
        # origin, Z-axis point, X-direction point
        zpt, xpt = points[1], points[2]
        n = zpt - origin
        temp_u = xpt - origin

        if n.length < 1e-8 or temp_u.length < 1e-8:
            return False, "CPlane points are too close together."

        n.normalize()
        u = temp_u - n * temp_u.dot(n)

        if u.length < 1e-8:
            return False, "Z-axis and X-direction are collinear."

        u.normalize()
        v = n.cross(u).normalized()

    else:
        return False, f"Unknown CPlane mode: {mode}"

    data = load_saved_cplanes(context)
    data[name] = {
        "origin": [origin.x, origin.y, origin.z],
        "u": [u.x, u.y, u.z],
        "v": [v.x, v.y, v.z],
    }
    save_saved_cplanes(context, data)

    try:
        sync_cplane_layer_collection(context)
    except Exception:
        pass

    return True, f"Created CPlane '{name}' using {mode}. Use the radio icon or cplane restore to activate it."




def create_cplane_from_face_hit(context, name, hit):
    if not hit:
        return False, "No object face selected."

    origin, u, v, n = get_face_cplane_axes_from_hit(hit)

    data = load_saved_cplanes(context)
    data[name] = {
        "origin": [origin.x, origin.y, origin.z],
        "u": [u.x, u.y, u.z],
        "v": [v.x, v.y, v.z],
    }
    save_saved_cplanes(context, data)

    try:
        sync_cplane_layer_collection(context)
    except Exception:
        pass

    obj_name = hit["object"].name if hit.get("object") else "object"
    return True, f"Created CPlane '{name}' from face on {obj_name}."



def save_axes_as_named_cplane(context, name, origin, u, v):
    name = (name or "").strip()
    if not name:
        return False, "Give the CPlane a name."

    data = load_saved_cplanes(context)
    data[name] = {
        "origin": [origin.x, origin.y, origin.z],
        "u": [u.x, u.y, u.z],
        "v": [v.x, v.y, v.z],
    }
    save_saved_cplanes(context, data)

    try:
        sync_cplane_layer_collection(context)
    except Exception:
        pass

    return True, f"Updated CPlane '{name}'."



def nearest_curve_sample_with_tangent(obj, point, samples=128):
    """Return nearest sampled point and tangent on a curve object."""
    pts = sample_curve_object_points(obj, samples=samples)
    if len(pts) < 2:
        return None

    best = None

    for i, p in enumerate(pts):
        d = (p - point).length

        if best is None or d < best[0]:
            if i == 0:
                tangent = pts[1] - pts[0]
            elif i == len(pts) - 1:
                tangent = pts[-1] - pts[-2]
            else:
                tangent = pts[i + 1] - pts[i - 1]

            best = (d, p, tangent, i)

    if best is None:
        return None

    d, p, tangent, i = best

    if tangent.length < 1e-8:
        return None

    tangent.normalize()
    return p, tangent, i


def create_cplane_perpendicular_to_curve(context, name, pick_point):
    """Create a CPlane perpendicular to the active/selected curve.

    The CPlane origin is the nearest sampled point on the curve.
    The CPlane normal is the curve tangent, so the plane is perpendicular
    to the curve direction at that point.
    """
    curves = [obj for obj in context.selected_objects if obj.type == "CURVE"]

    if not curves:
        return False, "Select a curve, then pick a point near it."

    curve = context.active_object if context.active_object in curves else curves[0]
    hit = nearest_curve_sample_with_tangent(curve, pick_point, samples=160)

    if hit is None:
        return False, "Could not evaluate curve tangent."

    origin, n, idx = hit

    # Plane normal = curve tangent.
    # Choose stable U axis perpendicular to tangent.
    world_up = Vector((0, 0, 1))
    if abs(n.dot(world_up)) > 0.95:
        world_up = Vector((0, 1, 0))

    u = world_up.cross(n)

    if u.length < 1e-8:
        u = Vector((1, 0, 0)).cross(n)

    if u.length < 1e-8:
        return False, "Could not build perpendicular CPlane axes."

    u.normalize()
    v = n.cross(u).normalized()

    data = load_saved_cplanes(context)
    data[name] = {
        "origin": [origin.x, origin.y, origin.z],
        "u": [u.x, u.y, u.z],
        "v": [v.x, v.y, v.z],
    }
    save_saved_cplanes(context, data)

    try:
        sync_cplane_layer_collection(context)
    except Exception:
        pass

    return True, f"Created perpendicular CPlane '{name}' on curve '{curve.name}'."


def rotate_axes(origin, u, v, axis_name, angle_degrees):
    axis_name = (axis_name or "Z").upper()
    n = u.cross(v).normalized()

    if axis_name in {"X", "U"}:
        axis = u.normalized()
    elif axis_name in {"Y", "V"}:
        axis = v.normalized()
    else:
        axis = n.normalized()

    import math as _math
    rot = Matrix.Rotation(_math.radians(angle_degrees), 4, axis)

    new_u = (rot @ u).normalized()
    new_v = (rot @ v).normalized()

    new_v = (new_v - new_u * new_v.dot(new_u)).normalized()

    return origin, new_u, new_v


def rotate_cplane_by_name(context, name, axis_name, angle_degrees):
    data = load_saved_cplanes(context)

    if name not in data:
        return False, f"No saved CPlane named '{name}'."

    origin, u, v, n = cplane_record_to_axes(data[name])
    origin, u, v = rotate_axes(origin, u, v, axis_name, angle_degrees)

    return save_axes_as_named_cplane(context, name, origin, u, v)


def rotate_active_cplane(context, axis_name, angle_degrees, save_name=None):
    active_name = getattr(context.scene, "cad_active_cplane_name", "")

    origin, u, v, n = get_cplane_axes(context)
    origin, u, v = rotate_axes(origin, u, v, axis_name, angle_degrees)

    if active_name:
        name = active_name
    else:
        base = getattr(context.scene, "cad_cplane", "TOP").title()
        name = (save_name or f"Rotated {base}").strip()

    ok, msg = save_axes_as_named_cplane(context, name, origin, u, v)

    if ok:
        set_named_cplane(context, name)
        set_cplane_visible(context, True, name=name)
        sync_cplane_dropdown(context)

    return ok, f"Rotated CPlane '{name}' around {axis_name.upper()} by {angle_degrees:g}°."





def project_perpendicular(vec, axis):
    return vec - axis * vec.dot(axis)


def signed_angle_around_axis(a, b, axis):
    a = project_perpendicular(a, axis)
    b = project_perpendicular(b, axis)

    if a.length < 1e-8 or b.length < 1e-8:
        return None

    a.normalize()
    b.normalize()
    axis = axis.normalized()

    import math as _math
    sin_v = axis.dot(a.cross(b))
    cos_v = max(-1.0, min(1.0, a.dot(b)))
    return _math.atan2(sin_v, cos_v)


def rotate_cplane_by_3_points(context, save_name, points):
    """Rotate active CPlane using 3 picked points.

    p0, p1 define the rotation axis.
    p2 defines the target direction around the axis.

    The current CPlane origin is used as the initial reference direction.
    If the origin lies on the axis, the current CPlane U axis is used instead.
    """
    if len(points) < 3:
        return False, "Rotate3Pt needs axis start, axis end, and target point."

    p0, p1, p2 = points[:3]
    axis = p1 - p0

    if axis.length < 1e-8:
        return False, "Rotation axis points are too close together."

    axis.normalize()

    active_name = getattr(context.scene, "cad_active_cplane_name", "")
    origin, u, v, n = get_cplane_axes(context)

    start_vec = project_perpendicular(origin - p0, axis)
    if start_vec.length < 1e-8:
        start_vec = project_perpendicular(u, axis)
    if start_vec.length < 1e-8:
        start_vec = project_perpendicular(v, axis)

    target_vec = project_perpendicular(p2 - p0, axis)

    angle = signed_angle_around_axis(start_vec, target_vec, axis)
    if angle is None:
        return False, "Could not calculate rotation angle. Pick a third point away from the axis."

    rot = Matrix.Rotation(angle, 4, axis)

    new_origin = p0 + (rot @ (origin - p0))
    new_u = (rot @ u).normalized()
    new_v = (rot @ v).normalized()
    new_v = (new_v - new_u * new_v.dot(new_u)).normalized()

    if active_name:
        name = active_name
    else:
        base = getattr(context.scene, "cad_cplane", "TOP").title()
        name = (save_name or f"Rotated {base}").strip()

    ok, msg = save_axes_as_named_cplane(context, name, new_origin, new_u, new_v)

    if ok:
        set_named_cplane(context, name)
        set_cplane_visible(context, True, name=name)
        sync_cplane_dropdown(context)

    import math as _math
    return ok, f"Rotated CPlane '{name}' by {_math.degrees(angle):.2f}° using 3 points."



def get_axis_rotation_record(context):
    raw = getattr(context.scene, "cad_cplane_axis_rotation_json", None)
    if raw is None:
        raw = context.scene.get("cad_cplane_axis_rotation_json", "{}")
    try:
        data = json.loads(raw or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_axis_rotation_record(context, data):
    if not hasattr(context.scene, "cad_cplane_axis_rotation_json"):
        # Fallback for partially registered/old scene states.
        context.scene["cad_cplane_axis_rotation_json"] = json.dumps(data, sort_keys=True)
        return
    context.scene.cad_cplane_axis_rotation_json = json.dumps(data, sort_keys=True)


def store_cplane_axis_rotation_setup(context, name, points):
    """Store a two-point axis and original CPlane frame for slider-controlled rotation."""
    if len(points) < 2:
        return False, "Axis rotation needs two points."

    p0, p1 = points[:2]
    axis = p1 - p0

    if axis.length < 1e-8:
        return False, "Axis points are too close together."

    axis.normalize()

    active_name = getattr(context.scene, "cad_active_cplane_name", "")
    origin, u, v, n = get_cplane_axes(context)

    if active_name:
        target_name = active_name
    else:
        base = getattr(context.scene, "cad_cplane", "TOP").title()
        target_name = (name or f"Axis Rotated {base}").strip()

    record = {
        "target_name": target_name,
        "axis_origin": [p0.x, p0.y, p0.z],
        "axis": [axis.x, axis.y, axis.z],
        "base_origin": [origin.x, origin.y, origin.z],
        "base_u": [u.x, u.y, u.z],
        "base_v": [v.x, v.y, v.z],
    }

    save_axis_rotation_record(context, record)

    context.scene.cad_cplane_axis_rotation_name = target_name
    context.scene.cad_cplane_axis_rotation_angle = 0.0

    # Create/refresh the target CPlane at its original orientation.
    ok, msg = apply_cplane_axis_rotation(context, 0.0)
    if ok:
        set_named_cplane(context, target_name)
        set_cplane_visible(context, True, name=target_name)
        sync_cplane_dropdown(context)

    return ok, f"Axis rotation setup stored for CPlane '{target_name}'. Use the angle slider/value to rotate."


def apply_cplane_axis_rotation(context, angle_degrees=None):
    """Apply slider/value angle to the stored axis-rotation setup."""
    data = get_axis_rotation_record(context)

    if not data:
        return False, "No axis rotation setup. Use CPlane Axis Rotate first."

    if angle_degrees is None:
        angle_degrees = float(getattr(context.scene, "cad_cplane_axis_rotation_angle", 0.0))

    target_name = data.get("target_name", "").strip() or getattr(context.scene, "cad_cplane_axis_rotation_name", "Axis Rotated CPlane")

    p0 = Vector(data.get("axis_origin", (0, 0, 0)))
    axis = Vector(data.get("axis", (0, 0, 1)))
    origin = Vector(data.get("base_origin", (0, 0, 0)))
    u = Vector(data.get("base_u", (1, 0, 0))).normalized()
    v = Vector(data.get("base_v", (0, 1, 0))).normalized()

    if axis.length < 1e-8:
        return False, "Stored rotation axis is invalid."

    axis.normalize()

    import math as _math
    rot = Matrix.Rotation(_math.radians(angle_degrees), 4, axis)

    new_origin = p0 + (rot @ (origin - p0))
    new_u = (rot @ u).normalized()
    new_v = (rot @ v).normalized()
    new_v = (new_v - new_u * new_v.dot(new_u)).normalized()

    ok, msg = save_axes_as_named_cplane(context, target_name, new_origin, new_u, new_v)

    if ok:
        set_named_cplane(context, target_name)
        set_cplane_visible(context, True, name=target_name)
        sync_cplane_dropdown(context)

        for area in context.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()

    return ok, f"Axis-rotated CPlane '{target_name}' to {angle_degrees:g}°."


def cad_cplane_axis_rotation_angle_update(self, context):
    try:
        apply_cplane_axis_rotation(context, self.cad_cplane_axis_rotation_angle)
    except Exception:
        pass



def move_cplane_by_vector(context, save_name, vector):
    """Move active CPlane by a vector.

    If the active CPlane is named, update that saved CPlane.
    If the active CPlane is built-in, create/update a named moved CPlane.
    """
    active_name = getattr(context.scene, "cad_active_cplane_name", "")
    origin, u, v, n = get_cplane_axes(context)
    new_origin = origin + vector

    if active_name:
        name = active_name
    else:
        base = getattr(context.scene, "cad_cplane", "TOP").title()
        name = (save_name or f"Moved {base}").strip()

    ok, msg = save_axes_as_named_cplane(context, name, new_origin, u, v)

    if ok:
        set_named_cplane(context, name)
        set_cplane_visible(context, True, name=name)
        sync_cplane_dropdown(context)

        for area in context.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()

    return ok, f"Moved CPlane '{name}' by vector ({vector.x:.3f}, {vector.y:.3f}, {vector.z:.3f})."


def move_cplane_by_2_points(context, save_name, points):
    if len(points) < 2:
        return False, "Move CPlane needs start point and end point."

    start, end = points[:2]
    vector = end - start

    if vector.length < 1e-8:
        return False, "Move vector is too small."

    return move_cplane_by_vector(context, save_name, vector)




def view_to_active_cplane(context):
    """Align the current 3D viewport to look perpendicular to the active CPlane."""
    area = context.area
    region_3d = None

    if area and area.type == "VIEW_3D":
        for space in area.spaces:
            if space.type == "VIEW_3D":
                region_3d = space.region_3d
                break

    if region_3d is None:
        return False, "Run this command from a 3D View."

    origin, u, v, n = get_cplane_axes(context)

    # Blender view_rotation represents the view orientation. We want to look along
    # the CPlane normal, with local CPlane Y as the view up direction.
    # Build a view matrix whose local -Z points along the normal.
    forward = n.normalized()
    up = v.normalized()
    right = up.cross(forward).normalized()

    if right.length < 1e-8:
        right = u.normalized()

    up = forward.cross(right).normalized()

    rot = Matrix((
        (right.x, up.x, -forward.x),
        (right.y, up.y, -forward.y),
        (right.z, up.z, -forward.z),
    )).to_quaternion()

    region_3d.view_rotation = rot
    region_3d.view_location = origin
    region_3d.view_perspective = "ORTHO"

    if area:
        area.tag_redraw()

    return True, f"View aligned to CPlane '{active_cplane_label(context)}'."


def camera_to_active_cplane(context, distance=20.0):
    """Create or align the scene camera to look at the active CPlane."""
    origin, u, v, n = get_cplane_axes(context)

    scene = context.scene
    cam = scene.camera

    if cam is None:
        cam_data = bpy.data.cameras.new("Hippo3D_CPlane_Camera")
        cam = bpy.data.objects.new("Hippo3D_CPlane_Camera", cam_data)
        context.collection.objects.link(cam)
        scene.camera = cam

    cam.location = origin + n.normalized() * distance

    direction = origin - cam.location
    quat = direction.to_track_quat("-Z", "Y")
    cam.rotation_euler = quat.to_euler()

    cam.data.type = "ORTHO"
    cam.data.ortho_scale = max(10.0, distance)

    for area in context.screen.areas:
        if area.type == "VIEW_3D":
            area.tag_redraw()

    return True, f"Camera aligned to CPlane '{active_cplane_label(context)}'."


def cplane_library_names(context):
    return sorted(load_saved_cplanes(context).keys())


# -----------------------------------------------------------------------------
# CPlane dropdown + visibility helpers
# -----------------------------------------------------------------------------

def cplane_active_key(context):
    if not context or not context.scene:
        return "BUILTIN_TOP"
    active_name = getattr(context.scene, "cad_active_cplane_name", "")
    if active_name:
        names = cplane_library_names(context)
        if active_name in names:
            return "NAMED_%d" % names.index(active_name)
    mode = getattr(context.scene, "cad_cplane", "TOP").upper()
    if mode == "FRONT":
        return "BUILTIN_FRONT"
    if mode == "RIGHT":
        return "BUILTIN_RIGHT"
    return "BUILTIN_TOP"


def cplane_dropdown_items(self, context):
    items = [
        ("BUILTIN_TOP", "Top / XY", "World XY construction plane"),
        ("BUILTIN_FRONT", "Front / XZ", "World XZ construction plane"),
        ("BUILTIN_RIGHT", "Right / YZ", "World YZ construction plane")]

    if context:
        names = cplane_library_names(context)
        for i, name in enumerate(names):
            items.append(("NAMED_%d" % i, name, "Saved CPlane: " + name))

    return items


def cplane_dropdown_update(self, context):
    value = getattr(self, "cad_active_cplane_dropdown", "BUILTIN_TOP")

    if value == "BUILTIN_TOP":
        set_builtin_cplane(context, "TOP")
    elif value == "BUILTIN_FRONT":
        set_builtin_cplane(context, "FRONT")
    elif value == "BUILTIN_RIGHT":
        set_builtin_cplane(context, "RIGHT")
    elif value.startswith("NAMED_"):
        try:
            idx = int(value.split("_", 1)[1])
            names = cplane_library_names(context)
            if 0 <= idx < len(names):
                set_named_cplane(context, names[idx])
        except Exception:
            pass

    sync_current_cplane_visibility(context)


def load_cplane_visibility(context):
    raw = getattr(context.scene, "cad_cplane_visibility_json", "{}") if context and context.scene else "{}"
    try:
        data = json.loads(raw or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_cplane_visibility(context, data):
    context.scene.cad_cplane_visibility_json = json.dumps(data, sort_keys=True)


def cplane_visibility_key_from_name_or_builtin(context, name=None):
    if name:
        return "NAMED:" + name
    mode = getattr(context.scene, "cad_cplane", "TOP").upper()
    return "BUILTIN:" + mode


def current_cplane_visibility_key(context):
    active_name = getattr(context.scene, "cad_active_cplane_name", "")
    return cplane_visibility_key_from_name_or_builtin(context, active_name if active_name else None)


def is_cplane_visible(context, name=None, builtin_mode=None):
    data = load_cplane_visibility(context)
    if name:
        key = "NAMED:" + name
    else:
        key = "BUILTIN:" + (builtin_mode or getattr(context.scene, "cad_cplane", "TOP")).upper()
    return bool(data.get(key, False))


def set_cplane_visible(context, visible, name=None, builtin_mode=None):
    data = load_cplane_visibility(context)
    if name:
        key = "NAMED:" + name
    else:
        key = "BUILTIN:" + (builtin_mode or getattr(context.scene, "cad_cplane", "TOP")).upper()
    data[key] = bool(visible)
    save_cplane_visibility(context, data)


def sync_cplane_dropdown(context):
    if not context or not context.scene:
        return
    key = cplane_active_key(context)
    try:
        context.scene.cad_active_cplane_dropdown = key
    except Exception:
        pass


def sync_current_cplane_visibility(context):
    if not context or not context.scene:
        return
    active_name = getattr(context.scene, "cad_active_cplane_name", "")
    visible = is_cplane_visible(context, name=active_name if active_name else None)
    try:
        context.scene.cad_current_cplane_visible = visible
    except Exception:
        pass


def current_cplane_visibility_update(self, context):
    active_name = getattr(context.scene, "cad_active_cplane_name", "")
    set_cplane_visible(context, bool(self.cad_current_cplane_visible), name=active_name if active_name else None)


class Hippo3D_OT_SetCPlaneVisible(Operator):
    bl_idname = "cad.set_cplane_visible"
    bl_label = "Set CPlane Visibility"

    name: StringProperty(default="")
    builtin_mode: StringProperty(default="")
    visible: BoolProperty(default=True)

    def execute(self, context):
        if self.visible:
            context.scene.cad_show_cplane_visuals = True
        set_cplane_visible(
            context,
            self.visible,
            name=self.name if self.name else None,
            builtin_mode=self.builtin_mode if self.builtin_mode else None,
        )
        sync_current_cplane_visibility(context)
        return {"FINISHED"}


class Hippo3D_OT_DeleteCPlane(Operator):
    bl_idname = "cad.delete_cplane"
    bl_label = "Delete Saved CPlane"

    name: StringProperty(default="")

    def execute(self, context):
        data = load_saved_cplanes(context)
        name = self.name.strip()
        if name not in data:
            self.report({"WARNING"}, f"No saved CPlane named '{name}'.")
            return {"CANCELLED"}
        del data[name]
        save_saved_cplanes(context, data)

        vis = load_cplane_visibility(context)
        vis.pop("NAMED:" + name, None)
        save_cplane_visibility(context, vis)

        if getattr(context.scene, "cad_active_cplane_name", "") == name:
            context.scene.cad_active_cplane_name = ""
            context.scene.cad_cplane = "TOP"

        sync_cplane_dropdown(context)
        sync_current_cplane_visibility(context)
        self.report({"INFO"}, f"Deleted CPlane '{name}'.")
        return {"FINISHED"}


def world_to_screen(context, point):
    if not context.region or not context.region_data:
        return None
    return view3d_utils.location_3d_to_region_2d(context.region, context.region_data, point)
