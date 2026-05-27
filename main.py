bl_info = {
    "name": "Hippo3D",
    "author": "Victor Calixto",
    "version": (0, 1, 1),
    "blender": (4, 0, 0),
    "location": "3D View > Sidebar > CAD / Toolbar / Ctrl+/ Command",
    "description": "Hippo3D is a free and open-source Rhino-like CAD modelling plugin for Blender, focused on command-line CAD workflows, CPlanes, curves, NURBS, and surface modelling operations.",
    "category": "3D View",
}

import bpy
import gpu
import blf
import json
from bpy.app.handlers import persistent
from gpu_extras.batch import batch_for_shader
from bpy.types import Operator, Panel, WorkSpaceTool, PropertyGroup, UIList
from bpy.props import StringProperty, BoolProperty, FloatProperty, EnumProperty, CollectionProperty, IntProperty
from mathutils import Vector, Matrix
from bpy_extras import view3d_utils

# Optional Hippo3D native C surface backend.
HIPPO_NATIVE_SURFACE_AVAILABLE = False
HIPPO_NATIVE_SURFACE_ERROR = ""
hippo_surface_native = None

from pathlib import Path

ICON_DIR = Path(__file__).parent / "icons"


def _load_hippo_native_backend():
    global HIPPO_NATIVE_SURFACE_AVAILABLE, HIPPO_NATIVE_SURFACE_ERROR, hippo_surface_native

    import importlib
    import importlib.util
    import sys
    from pathlib import Path as _Path

    candidates = []

    # 1. Normal import, useful if the module is on sys.path.
    try:
        import hippo_surface_native as _native
        hippo_surface_native = _native
        HIPPO_NATIVE_SURFACE_AVAILABLE = True
        HIPPO_NATIVE_SURFACE_ERROR = ""
        return
    except Exception as exc:
        HIPPO_NATIVE_SURFACE_ERROR = f"normal import failed: {exc}"

    # 2. Package-relative import.
    try:
        _native = importlib.import_module(__package__ + ".hippo_surface_native")
        hippo_surface_native = _native
        HIPPO_NATIVE_SURFACE_AVAILABLE = True
        HIPPO_NATIVE_SURFACE_ERROR = ""
        return
    except Exception as exc:
        HIPPO_NATIVE_SURFACE_ERROR += f" | package import failed: {exc}"

    # 3. Load directly from the add-on directory beside __init__.py.
    addon_dir = _Path(__file__).resolve().parent
    for pattern in (
        "hippo_surface_native*.so",
        "hippo_surface_native*.pyd",
        "hippo_surface_native*.dll",
        "hippo_surface_native*.dylib",
    ):
        candidates.extend(addon_dir.glob(pattern))

    if not candidates:
        HIPPO_NATIVE_SURFACE_ERROR += f" | no compiled hippo_surface_native file found in {addon_dir}"
        return

    native_path = candidates[0]

    try:
        spec = importlib.util.spec_from_file_location("hippo_surface_native", str(native_path))
        if spec is None or spec.loader is None:
            HIPPO_NATIVE_SURFACE_ERROR += f" | could not create import spec for {native_path}"
            return

        module = importlib.util.module_from_spec(spec)
        sys.modules["hippo_surface_native"] = module
        spec.loader.exec_module(module)

        hippo_surface_native = module
        HIPPO_NATIVE_SURFACE_AVAILABLE = True
        HIPPO_NATIVE_SURFACE_ERROR = ""
        return

    except Exception as exc:
        HIPPO_NATIVE_SURFACE_ERROR += f" | direct load failed from {native_path}: {exc}"


_load_hippo_native_backend()



# -----------------------------------------------------------------------------
# State
# -----------------------------------------------------------------------------

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




# -----------------------------------------------------------------------------
# CPlane helpers
# -----------------------------------------------------------------------------

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


# -----------------------------------------------------------------------------
# Surface helpers - Phase 2
# -----------------------------------------------------------------------------

def sample_curve_object_points(obj, samples=32):
    """Sample a Curve object into a list of world-space points.

    Phase 2 approximation:
    - Uses evaluated curve mesh when possible.
    - Falls back to spline control points.
    - Resamples to a fixed point count by polyline arc length.
    """
    if obj is None or obj.type != "CURVE":
        return []

    pts = []

    try:
        depsgraph = bpy.context.evaluated_depsgraph_get()
        eval_obj = obj.evaluated_get(depsgraph)
        temp_mesh = eval_obj.to_mesh()

        if temp_mesh and len(temp_mesh.vertices) > 0:
            pts = [obj.matrix_world @ v.co for v in temp_mesh.vertices]

        eval_obj.to_mesh_clear()
    except Exception:
        pts = []

    if not pts:
        for spline in obj.data.splines:
            if spline.type in {"POLY", "NURBS"}:
                for p in spline.points:
                    pts.append(obj.matrix_world @ Vector((p.co.x, p.co.y, p.co.z)))
            elif spline.type == "BEZIER":
                for p in spline.bezier_points:
                    pts.append(obj.matrix_world @ p.co)

    if len(pts) < 2:
        return pts

    return resample_polyline_points(pts, samples)


def resample_polyline_points(points, samples):
    if len(points) <= 1:
        return list(points)

    samples = max(2, int(samples))

    lengths = [0.0]
    total = 0.0

    for a, b in zip(points[:-1], points[1:]):
        total += (b - a).length
        lengths.append(total)

    if total <= 1e-9:
        return [points[0].copy() for _ in range(samples)]

    out = []
    for i in range(samples):
        t = total * (i / (samples - 1))

        j = 0
        while j < len(lengths) - 2 and lengths[j + 1] < t:
            j += 1

        seg_len = lengths[j + 1] - lengths[j]
        if seg_len <= 1e-9:
            out.append(points[j].copy())
        else:
            f = (t - lengths[j]) / seg_len
            out.append(points[j].lerp(points[j + 1], f))

    return out


def selected_curve_objects(context):
    return [obj for obj in context.selected_objects if obj.type == "CURVE"]


def create_loft_surface_from_curves(context, curves=None, samples=32, name="Hippo3D_Loft"):
    """Create a mesh loft through two or more selected curves."""
    if curves is None:
        curves = selected_curve_objects(context)

    curves = [obj for obj in curves if obj and obj.type == "CURVE"]

    if len(curves) < 2:
        return False, "Select at least two curve objects to loft.", None

    samples = max(2, int(samples))

    sections = []
    source_names = []

    for obj in curves:
        pts = sample_curve_object_points(obj, samples=samples)
        if len(pts) < 2:
            return False, f"Curve '{obj.name}' does not have enough points.", None
        sections.append(pts)
        source_names.append(obj.name)

    verts, faces = hippo_build_grid_surface_from_sections(sections, closed_u=False, closed_v=False)

    mesh = bpy.data.meshes.new(name + "_Mesh")
    mesh.from_pydata(verts, [], faces)
    mesh.update()

    obj = bpy.data.objects.new(name, mesh)
    context.collection.objects.link(obj)

    obj["cad_surface_type"] = "loft"
    obj["cad_loft_sources"] = "|".join(source_names)
    obj["cad_loft_samples"] = samples

    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    context.view_layer.objects.active = obj

    return True, f"Created loft surface from {len(curves)} curve(s).", obj


def rebuild_loft_surface(context, obj=None):
    """Rebuild a loft surface from its stored source curve names."""
    if obj is None:
        obj = context.active_object

    if obj is None or obj.get("cad_surface_type") != "loft":
        return False, "Select a loft surface generated by Hippo3D."

    source_names = obj.get("cad_loft_sources", "")
    samples = int(obj.get("cad_loft_samples", getattr(context.scene, "cad_loft_samples", 32)))

    if not source_names:
        return False, "Loft surface has no stored source curves."

    curves = []
    missing = []

    for name in source_names.split("|"):
        source = bpy.data.objects.get(name)
        if source and source.type == "CURVE":
            curves.append(source)
        else:
            missing.append(name)

    if missing:
        return False, "Missing source curve(s): " + ", ".join(missing)

    ok, msg, new_obj = create_loft_surface_from_curves(
        context,
        curves=curves,
        samples=samples,
        name=obj.name + "_Rebuilt",
    )

    if not ok:
        return ok, msg

    # Replace mesh data on existing object and delete temporary object.
    old_mesh = obj.data
    obj.data = new_obj.data
    obj["cad_loft_samples"] = samples
    bpy.data.objects.remove(new_obj, do_unlink=True)

    try:
        if old_mesh and old_mesh.users == 0:
            bpy.data.meshes.remove(old_mesh)
    except Exception:
        pass

    context.view_layer.objects.active = obj
    obj.select_set(True)

    return True, "Rebuilt loft surface."



# -----------------------------------------------------------------------------
# Real Blender modifier backend - Geometry Nodes shell for loft objects
# -----------------------------------------------------------------------------

def ensure_cad_loft_geometry_nodes_group():
    """Create a Geometry Nodes group used by CAD Loft Modifier.

    Note: Blender Python cannot create a new native C/C++ modifier type.
    The real Blender-native path is a Geometry Nodes modifier. This group is kept
    intentionally minimal and attaches to the generated loft object as a true
    modifier in Blender's modifier stack.
    """
    group_name = "Hippo3D_Loft_GeometryNodes_Modifier"

    if group_name in bpy.data.node_groups:
        return bpy.data.node_groups[group_name]

    ng = bpy.data.node_groups.new(group_name, "GeometryNodeTree")

    try:
        # Blender 4/5 interface API
        ng.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
        ng.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    except Exception:
        pass

    try:
        input_node = ng.nodes.new("NodeGroupInput")
        output_node = ng.nodes.new("NodeGroupOutput")
        input_node.location = (-300, 0)
        output_node.location = (300, 0)

        if input_node.outputs and output_node.inputs:
            ng.links.new(input_node.outputs[0], output_node.inputs[0])
    except Exception:
        pass

    return ng


def add_real_geometry_nodes_loft_modifier(obj):
    """Attach a real Geometry Nodes modifier to the loft object."""
    if obj is None:
        return None

    mod_name = "CAD Loft Modifier"

    # Avoid duplicates.
    mod = obj.modifiers.get(mod_name)
    if mod is None:
        mod = obj.modifiers.new(mod_name, "NODES")

    try:
        mod.node_group = ensure_cad_loft_geometry_nodes_group()
    except Exception:
        pass

    obj["cad_has_real_blender_modifier"] = True
    obj["cad_modifier_backend"] = "GEOMETRY_NODES"

    return mod


def create_loft_with_real_modifier(context):
    """Create loft surface and add a real Geometry Nodes modifier to the object.

    The loft geometry is generated once from selected curves, while the resulting
    object receives a real Blender modifier stack entry. This is the safe Phase 2
    bridge before building a full procedural GN node network for true live lofting.
    """
    samples = int(getattr(context.scene, "cad_loft_samples", 32))
    ok, msg, obj = create_loft_surface_from_curves(context, samples=samples, name="Hippo3D_GN_Loft_Surface")

    if not ok:
        return ok, msg

    mod = add_real_geometry_nodes_loft_modifier(obj)

    if mod:
        obj["cad_surface_type"] = "loft_geometry_nodes_modifier"
        return True, "Created loft object with a real Geometry Nodes modifier in the modifier stack."

    return False, "Loft was created, but the Geometry Nodes modifier could not be added."


def run_loft_command(context):
    samples = int(getattr(context.scene, "cad_loft_samples", 32))
    ok, msg, obj = create_loft_surface_from_curves(context, samples=samples)
    return ok, msg


def run_simple_cad_command(context, cmd):
    """Run non-point CAD commands available from command line and UI."""
    raw = cmd.strip().lower()
    compact = raw.replace(" ", "")

    if compact in {"mesh", "tomesh", "convertmesh", "converttomesh", "ctm"}:
        return convert_selected_to_mesh(context)

    if compact in {"join", "j"}:
        return join_selected_objects(context)

    if compact in {"loft", "surface", "srf", "loftsrf"}:
        return run_loft_command(context)

    if compact in {"loftmodifier", "loftmod", "gnloft", "geometrynodesloft"}:
        return create_loft_with_real_modifier(context)

    if parts := raw.split():
        pass

    if parts and parts[0].replace("_", "") in {"extrude", "extrudecrv", "extrudesrf", "extrudecurve"}:
        if len(parts) >= 2:
            try:
                context.scene.cad_extrude_distance = float(parts[1])
            except Exception:
                pass
        return run_extrude_command(context)

    if parts and parts[0].replace("_", "") in {"pipe", "pipecrv"}:
        if len(parts) >= 2:
            try:
                context.scene.cad_pipe_radius = float(parts[1])
            except Exception:
                pass
        return run_pipe_command(context)

    if parts and parts[0].replace("_", "") in {"revolve", "rev", "revolvesrf"}:
        if len(parts) >= 2:
            try:
                context.scene.cad_revolve_angle = float(parts[1])
            except Exception:
                pass
        return run_revolve_command(context)

    if compact in {"clearrevolveaxis", "clearrevaxis"}:
        return clear_revolve_axis(context)
    if compact in {"edgesrf", "edgesurface", "srfedge", "surfacefromedges"}:
        return run_edgesrf_command(context)

    if compact in {"planarsrf", "planesrf", "planarsurface", "surfacefromplanarcurves"}:
        return run_planarsrf_command(context)

    if compact in {"trim"}:
        return run_trim_command(context)

    if compact in {"split"}:
        return run_split_command(context)

    if parts and parts[0].replace("_", "") in {"offset", "offsetcrv", "offsetcurve"}:
        if len(parts) >= 2:
            try:
                context.scene.hippo_offset_distance = float(parts[1])
            except Exception:
                pass
        return run_offset_command(context)

    if compact in {"chamfer"}:
        return run_chamfer_command(context)

    if compact in {"xline", "constructionline", "infiniteline"}:
        return run_xline_command(context)

    if compact in {"explode"}:
        return run_explode_command(context)

    if compact in {"array"}:
        return run_array_command(context)

    if compact in {"project", "projecttocplane"}:
        return run_project_command(context)

    if compact in {"polygon", "poly", "ngon"}:
        return run_polygon_command(context)

    if compact in {"ellipse", "ell"}:
        return run_ellipse_command(context)

    if compact in {"arc"}:
        return False, "Arc is interactive. Type arc, then pick 3 points."

    if compact in {"railrevolve"}:
        return hippo_command_not_ready("RailRevolve")

    parts = raw.split()
    if parts and parts[0] in {"setdegree", "degree", "rebuild", "rebuilddegree"}:
        if len(parts) < 2:
            return False, "Use: setdegree 3"
        try:
            degree = int(parts[1])
        except Exception:
            return False, "Degree must be an integer."
        return set_selected_nurbs_degree(context, degree)

    return None, ""


def parse_point(text, context=None, base_point=None):
    """Parse absolute x,y,z or relative @x,y,z coordinates in active CPlane coordinates.

    Examples:
    10,0,0    -> absolute CPlane coordinate
    @10,0,0   -> relative to the previous point, using CPlane axes
    @5,0      -> relative 2D CPlane coordinate
    """
    if context is None:
        context = bpy.context
    text = text.strip()
    relative = text.startswith("@")
    if relative:
        text = text[1:].strip()
    try:
        vals = [float(v.strip()) for v in text.split(",")]
    except Exception:
        return None

    if len(vals) == 2:
        vals.append(0.0)
    if len(vals) != 3:
        return None

    if relative:
        base = base_point if base_point is not None else (state.points[-1] if state.points else Vector((0, 0, 0)))
        origin, u, v, n = get_cplane_axes(context)
        return base + u * vals[0] + v * vals[1] + n * vals[2]

    return cplane_to_world(context, vals[0], vals[1], vals[2])




def parse_distance_value(text):
    """Parse single numeric distance entry such as 10 or -5.

    Unlike parse_point, this intentionally rejects coordinate strings with commas.
    """
    text = text.strip()
    if not text or "," in text or text.startswith("@"):
        return None
    try:
        return float(text)
    except Exception:
        return None


def point_from_distance_in_mouse_direction(context, distance):
    """Return next point using the last command point + mouse direction.

    This mimics CAD behaviour:
    - start a command
    - pick first point
    - move mouse to indicate direction
    - type a distance
    """
    if not state.points:
        return None

    base = state.points[-1]
    mouse = getattr(state, "mouse_world", None)

    if mouse is None:
        return None

    direction = mouse - base

    # Project direction to the active CPlane for stable CAD behaviour.
    origin, u, v, n = get_cplane_axes(context)
    du = direction.dot(u)
    dv = direction.dot(v)
    dn = direction.dot(n)

    # Most 2D drawing commands should follow the mouse direction on the CPlane.
    if state.command in {
        "line", "l", "polyline", "pline", "pl",
        "rectangle", "rect", "circle", "c",
        "arc", "ellipse", "polygon", "xline",
    }:
        direction = u * du + v * dv
    else:
        direction = u * du + v * dv + n * dn

    if direction.length < 1e-8:
        return None

    direction.normalize()
    return base + direction * float(distance)



def mouse_to_plane(context, event):
    """Project mouse to the active CAD CPlane."""
    region = context.region
    rv3d = context.region_data
    coord = (event.mouse_region_x, event.mouse_region_y)

    ray_origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
    ray_dir = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)

    plane_origin, _u, _v, plane_normal = get_cplane_axes(context)
    denom = ray_dir.dot(plane_normal)
    if abs(denom) < 1e-8:
        return view3d_utils.region_2d_to_location_3d(region, rv3d, coord, plane_origin)

    t = (plane_origin - ray_origin).dot(plane_normal) / denom
    return ray_origin + ray_dir * t


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




def get_active_polyline_snap_data():
    """Return temporary snap points/segments from the command currently being drawn.

    Blender only exposes completed objects to context.visible_objects. During a
    polyline or NURBS curve command, the in-progress vertices are stored only in state.points,
    so the osnap manager needs to include them explicitly.
    """
    points = []
    segments = []
    if state.command in {"polyline", "pline", "pl", "nurbs", "nurbscurve", "curve", "crv"} and state.points:
        points = [p.copy() for p in state.points]
        if len(state.points) >= 2:
            segments = [(state.points[i].copy(), state.points[i + 1].copy()) for i in range(len(state.points) - 1)]
    return points, segments

def get_curve_segments(context):
    """Return line/polyline segments from visible curve objects."""
    segments = []
    for obj in context.visible_objects:
        if obj.type != "CURVE":
            continue
        mw = obj.matrix_world
        for spl in obj.data.splines:
            pts = []
            if spl.type == "POLY":
                pts = [mw @ Vector((p.co.x, p.co.y, p.co.z)) for p in spl.points]
            elif spl.type in {"NURBS", "BEZIER"}:
                # First-pass support: use control points as snap references.
                if spl.type == "NURBS":
                    pts = [mw @ Vector((p.co.x, p.co.y, p.co.z)) for p in spl.points]
                else:
                    pts = [mw @ p.co for p in spl.bezier_points]
            for a, b in zip(pts[:-1], pts[1:]):
                segments.append((a, b))
    return segments


def get_curve_centers(context):
    """Return center snap points from CAD circles/rectangles and curve bounding boxes."""
    centers = []
    for obj in context.visible_objects:
        if obj.type != "CURVE":
            continue
        if "cad_center" in obj:
            try:
                centers.append(Vector(obj["cad_center"]))
                continue
            except Exception:
                pass
        # Fallback: use world-space bounding-box center for closed/simple curve objects.
        try:
            corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
            if corners:
                c = sum(corners, Vector()) / len(corners)
                centers.append(c)
        except Exception:
            pass
    return centers


def closest_point_on_segment(p, a, b):
    ab = b - a
    denom = ab.dot(ab)
    if denom < 1e-12:
        return a.copy()
    t = max(0.0, min(1.0, (p - a).dot(ab) / denom))
    return a + ab * t


def apply_ortho(context, point):
    """Constrain to active CPlane U/V axes from the previous point."""
    if not getattr(context.scene, "cad_ortho", False):
        return point
    if not state.points:
        return point
    base = state.points[-1]
    origin, u, v, n = get_cplane_axes(context)
    delta = point - base
    du = delta.dot(u)
    dv = delta.dot(v)
    # Keep the point on the active drawing plane and choose the dominant CPlane axis.
    if abs(du) >= abs(dv):
        return base + u * du
    return base + v * dv


def resolve_snap(context, event, raw_point):
    """Find the best first-pass osnap candidate near the cursor."""
    mouse_xy = (event.mouse_region_x, event.mouse_region_y)
    radius = getattr(context.scene, "cad_snap_radius", 18.0)
    candidates = []
    active_points, active_segments = get_active_polyline_snap_data()
    segments = active_segments + get_curve_segments(context)

    use_endpoint = getattr(context.scene, "cad_osnap_endpoint", True)
    use_midpoint = getattr(context.scene, "cad_osnap_midpoint", True)
    use_nearest = getattr(context.scene, "cad_osnap_nearest", True)
    use_center = getattr(context.scene, "cad_osnap_center", True)
    use_grid = getattr(context.scene, "cad_osnap_grid", False)

    if use_endpoint:
        # Standalone active polyline vertices, including the first point before a segment exists.
        for p in active_points:
            candidates.append((p, "End", screen_distance(context, p, mouse_xy)))
        for a, b in segments:
            candidates.append((a, "End", screen_distance(context, a, mouse_xy)))
            candidates.append((b, "End", screen_distance(context, b, mouse_xy)))

    if use_midpoint:
        for a, b in segments:
            mid = (a + b) * 0.5
            candidates.append((mid, "Mid", screen_distance(context, mid, mouse_xy)))

    if use_nearest:
        for a, b in segments:
            near = closest_point_on_segment(raw_point, a, b)
            candidates.append((near, "Near", screen_distance(context, near, mouse_xy)))

    if use_center:
        for c in get_curve_centers(context):
            candidates.append((c, "Cen", screen_distance(context, c, mouse_xy)))

    # Grid snap has lower priority because it is always available.
    grid_candidate = None
    if use_grid:
        size = max(0.0001, getattr(context.scene, "cad_grid_size", 1.0))
        lp = world_to_cplane(context, raw_point)
        grid_candidate = cplane_to_world(context, round(lp.x / size) * size, round(lp.y / size) * size, round(lp.z / size) * size)
        candidates.append((grid_candidate, "Grid", screen_distance(context, grid_candidate, mouse_xy) + 4.0))

    if candidates:
        best = min(candidates, key=lambda c: c[2])
        if best[2] <= radius or best[1] == "Grid":
            return best[0], best[1]

    return raw_point, ""


def command_label():
    if state.command:
        return f"Hippo3D[{active_cplane_label(bpy.context)}]> {state.command} {state.input_text}"
    return f"Hippo3D[{active_cplane_label(bpy.context)}]> {state.input_text}"


def finish_command(context):
    state.active = False
    state.command = ""
    state.pending_cplane_name = ""
    state.pending_cplane_mode = ""
    state.points = []
    state.input_text = ""
    state.snap_point = None
    state.snap_label = ""
    context.workspace.status_text_set(None)
    if getattr(state, "cursor_set", False):
        try:
            context.window.cursor_modal_restore()
        except Exception:
            pass
        state.cursor_set = False
    if state.draw_handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(state.draw_handle, "WINDOW")
        state.draw_handle = None
    if state.text_handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(state.text_handle, "WINDOW")
        state.text_handle = None


# -----------------------------------------------------------------------------
# Viewport preview
# -----------------------------------------------------------------------------

def draw_snap_marker(shader, p, label):
    d = 0.10
    if label == "End":
        coords = [
            Vector((p.x - d, p.y - d, p.z)), Vector((p.x + d, p.y - d, p.z)),
            Vector((p.x + d, p.y - d, p.z)), Vector((p.x + d, p.y + d, p.z)),
            Vector((p.x + d, p.y + d, p.z)), Vector((p.x - d, p.y + d, p.z)),
            Vector((p.x - d, p.y + d, p.z)), Vector((p.x - d, p.y - d, p.z))]
    elif label == "Cen":
        coords = []
        steps = 24
        import math
        for i in range(steps):
            a1 = 2 * math.pi * i / steps
            a2 = 2 * math.pi * (i + 1) / steps
            coords.append(Vector((p.x + math.cos(a1) * d, p.y + math.sin(a1) * d, p.z)))
            coords.append(Vector((p.x + math.cos(a2) * d, p.y + math.sin(a2) * d, p.z)))
    elif label == "Mid":
        coords = [
            Vector((p.x, p.y + d, p.z)), Vector((p.x + d, p.y - d, p.z)),
            Vector((p.x + d, p.y - d, p.z)), Vector((p.x - d, p.y - d, p.z)),
            Vector((p.x - d, p.y - d, p.z)), Vector((p.x, p.y + d, p.z))]
    else:
        coords = [
            Vector((p.x - d, p.y, p.z)), Vector((p.x + d, p.y, p.z)),
            Vector((p.x, p.y - d, p.z)), Vector((p.x, p.y + d, p.z))]
    batch = batch_for_shader(shader, "LINES", {"pos": coords})
    shader.bind()
    shader.uniform_float("color", (1.0, 0.35, 0.1, 1.0))
    batch.draw(shader)



# -----------------------------------------------------------------------------
# Persistent CPlane visual representation
# -----------------------------------------------------------------------------

def draw_cplane_axes_and_grid(shader, context, name, origin, u, v, n, active=False, visible=True):
    if not visible:
        return

    scene = context.scene
    grid_size = int(getattr(scene, "cad_cplane_visual_grid_count", 6))
    spacing = float(getattr(scene, "cad_cplane_visual_grid_spacing", getattr(scene, "cad_grid_size", 1.0)))
    axis_len = float(getattr(scene, "cad_cplane_visual_axis_length", 2.0))

    alpha = 0.95 if active else 0.35
    width_axis = 3.0 if active else 1.5

    # Grid
    if getattr(scene, "cad_show_cplane_grid_visuals", True):
        coords = []
        for i in range(-grid_size, grid_size + 1):
            coords.extend([
                origin + u * (i * spacing) + v * (-grid_size * spacing),
                origin + u * (i * spacing) + v * ( grid_size * spacing),
                origin + v * (i * spacing) + u * (-grid_size * spacing),
                origin + v * (i * spacing) + u * ( grid_size * spacing)])

        if coords:
            batch = batch_for_shader(shader, "LINES", {"pos": coords})
            shader.bind()
            shader.uniform_float("color", (0.45, 0.45, 0.45, 0.22 if active else 0.12))
            batch.draw(shader)

    # Axes
    axis_coords = [
        origin, origin + u * axis_len,
        origin, origin + v * axis_len,
        origin, origin + n * (axis_len * 0.65)]

    gpu.state.line_width_set(width_axis)

    batch = batch_for_shader(shader, "LINES", {"pos": axis_coords[0:2]})
    shader.bind()
    shader.uniform_float("color", (1.0, 0.15, 0.15, alpha))
    batch.draw(shader)

    batch = batch_for_shader(shader, "LINES", {"pos": axis_coords[2:4]})
    shader.bind()
    shader.uniform_float("color", (0.15, 1.0, 0.15, alpha))
    batch.draw(shader)

    batch = batch_for_shader(shader, "LINES", {"pos": axis_coords[4:6]})
    shader.bind()
    shader.uniform_float("color", (0.15, 0.35, 1.0, alpha))
    batch.draw(shader)

    gpu.state.line_width_set(1.0)


def draw_cplane_visual_labels(context):
    if not getattr(context.scene, "cad_show_cplane_labels", True):
        return

    active_name = getattr(context.scene, "cad_active_cplane_name", "")
    active_builtin = getattr(context.scene, "cad_cplane", "TOP").upper()

    font_id = 0
    blf.size(font_id, 12)

    def draw_label(text, p, active=False):
        pos = world_to_screen(context, p)
        if pos is None:
            return
        blf.position(font_id, pos.x + 6, pos.y + 6, 0)
        if active:
            blf.color(font_id, 1.0, 1.0, 1.0, 1.0)
            blf.size(font_id, 14)
            blf.draw(font_id, "* " + text)
            blf.size(font_id, 12)
        else:
            blf.color(font_id, 0.8, 0.8, 0.8, 0.65)
            blf.draw(font_id, text)

    # Built-ins
    for mode, label in [("TOP", "Top / XY"), ("FRONT", "Front / XZ"), ("RIGHT", "Right / YZ")]:
        if not is_cplane_visible(context, builtin_mode=mode):
            continue
        origin, u, v, n = _builtin_cplane_axes(mode)
        active = (not active_name and active_builtin == mode)
        draw_label(label, origin, active)

    # Named
    data = load_saved_cplanes(context)
    for name, record in data.items():
        if not is_cplane_visible(context, name=name):
            continue
        origin, u, v, n = cplane_record_to_axes(record)
        draw_label(name, origin, active=(active_name == name))


def draw_cplanes_visual_callback():
    context = bpy.context
    if not context or not context.scene:
        return
    scene = context.scene

    shader = gpu.shader.from_builtin("UNIFORM_COLOR")

    active_name = getattr(scene, "cad_active_cplane_name", "")
    active_builtin = getattr(scene, "cad_cplane", "TOP").upper()

    # Built-in CPlanes
    for mode, label in [("TOP", "Top / XY"), ("FRONT", "Front / XZ"), ("RIGHT", "Right / YZ")]:
        origin, u, v, n = _builtin_cplane_axes(mode)
        active = (not active_name and active_builtin == mode)
        visible = is_cplane_visible(context, builtin_mode=mode)
        draw_cplane_axes_and_grid(shader, context, label, origin, u, v, n, active=active, visible=visible)

    # Named saved CPlanes
    data = load_saved_cplanes(context)
    for name, record in data.items():
        origin, u, v, n = cplane_record_to_axes(record)
        active = active_name == name
        visible = is_cplane_visible(context, name=name)
        draw_cplane_axes_and_grid(shader, context, name, origin, u, v, n, active=active, visible=visible)


def draw_callback():
    if not state.active:
        return

    shader = gpu.shader.from_builtin("UNIFORM_COLOR")

    if state.points:
        coords = []
        d = 0.06
        for p in state.points:
            coords.extend([
                Vector((p.x - d, p.y, p.z)), Vector((p.x + d, p.y, p.z)),
                Vector((p.x, p.y - d, p.z)), Vector((p.x, p.y + d, p.z))])
        batch = batch_for_shader(shader, "LINES", {"pos": coords})
        shader.bind()
        shader.uniform_float("color", (0.1, 0.9, 1.0, 1.0))
        batch.draw(shader)

    if state.command in {"line", "l", "polyline", "pline", "pl"} and len(state.points) >= 1:
        coords = []
        for a, b in zip(state.points[:-1], state.points[1:]):
            coords.extend([a, b])
        coords.extend([state.points[-1], state.mouse_world])
        batch = batch_for_shader(shader, "LINES", {"pos": coords})
        shader.bind()
        shader.uniform_float("color", (1.0, 1.0, 0.0, 1.0))
        batch.draw(shader)

    if state.command in {"rectangle", "rect"} and len(state.points) == 1:
        p1 = state.points[0]
        p2 = state.mouse_world
        l1 = world_to_cplane(bpy.context, p1)
        l2 = world_to_cplane(bpy.context, p2)
        z = l1.z
        pts = [
            cplane_to_world(bpy.context, l1.x, l1.y, z),
            cplane_to_world(bpy.context, l2.x, l1.y, z),
            cplane_to_world(bpy.context, l2.x, l2.y, z),
            cplane_to_world(bpy.context, l1.x, l2.y, z),
            cplane_to_world(bpy.context, l1.x, l1.y, z)]
        coords = []
        for a, b in zip(pts[:-1], pts[1:]):
            coords.extend([a, b])
        batch = batch_for_shader(shader, "LINES", {"pos": coords})
        shader.bind()
        shader.uniform_float("color", (1.0, 1.0, 0.0, 1.0))
        batch.draw(shader)

    if state.command in {"circle", "c"} and len(state.points) == 1:
        center = state.points[0]
        lc = world_to_cplane(bpy.context, center)
        lm = world_to_cplane(bpy.context, state.mouse_world)
        radius = ((lm.x - lc.x) ** 2 + (lm.y - lc.y) ** 2) ** 0.5
        if radius > 1e-8:
            import math
            coords = []
            steps = 64
            for i in range(steps):
                a1 = 2 * math.pi * i / steps
                a2 = 2 * math.pi * (i + 1) / steps
                coords.append(cplane_to_world(bpy.context, lc.x + math.cos(a1) * radius, lc.y + math.sin(a1) * radius, lc.z))
                coords.append(cplane_to_world(bpy.context, lc.x + math.cos(a2) * radius, lc.y + math.sin(a2) * radius, lc.z))
            batch = batch_for_shader(shader, "LINES", {"pos": coords})
            shader.bind()
            shader.uniform_float("color", (1.0, 1.0, 0.0, 1.0))
            batch.draw(shader)

    if state.command in {"nurbs", "nurbscurve", "curve", "crv"} and len(state.points) >= 1:
        preview_points = state.points + [state.mouse_world]
        # Draw control polygon.
        if len(preview_points) >= 2:
            ctrl_coords = []
            for a, b in zip(preview_points[:-1], preview_points[1:]):
                ctrl_coords.extend([a, b])
            batch = batch_for_shader(shader, "LINES", {"pos": ctrl_coords})
            shader.bind()
            shader.uniform_float("color", (0.3, 0.7, 1.0, 0.75))
            batch.draw(shader)

        curve_pts = preview_nurbs_points(preview_points, degree=int(getattr(state, "nurbs_degree", 3)), samples=64)
        if len(curve_pts) >= 2:
            curve_coords = []
            for a, b in zip(curve_pts[:-1], curve_pts[1:]):
                curve_coords.extend([a, b])
            batch = batch_for_shader(shader, "LINES", {"pos": curve_coords})
            shader.bind()
            shader.uniform_float("color", (1.0, 1.0, 0.0, 1.0))
            batch.draw(shader)

    if state.snap_point is not None and state.snap_label:
        draw_snap_marker(shader, state.snap_point, state.snap_label)



def draw_text_callback():
    context = bpy.context
    draw_cplane_visual_labels(context)
    if not state.active:
        return

    font_id = 0
    label = command_label()
    snap = f"Snap: {state.snap_label}" if state.snap_label else "Snap: none"
    hint = "Enter = confirm    Esc = cancel    Ctrl+/ = command    F8 = Ortho    @x,y,z = relative"

    blf.position(font_id, 24, 82, 0)
    blf.size(font_id, 18)
    blf.color(font_id, 1.0, 1.0, 0.2, 1.0)
    blf.draw(font_id, label)

    blf.position(font_id, 24, 58, 0)
    blf.size(font_id, 13)
    blf.color(font_id, 1.0, 0.55, 0.25, 1.0)
    blf.draw(font_id, snap)

    blf.position(font_id, 24, 34, 0)
    blf.size(font_id, 12)
    blf.color(font_id, 0.85, 0.85, 0.85, 1.0)
    blf.draw(font_id, hint)


# -----------------------------------------------------------------------------
# Main modal command operator
# -----------------------------------------------------------------------------

class Hippo3D_OT_Command(Operator):
    bl_idname = "cad.command"
    bl_label = "CAD Command"
    bl_options = {"REGISTER"}

    initial_command: StringProperty(default="")

    def start_line(self, context):
        state.command = "line"
        state.points = []
        state.input_text = ""
        self.report({"INFO"}, "Line: click in the viewport or type x,y,z then Enter. Esc to finish.")
        context.workspace.status_text_set(command_label())

    def start_polyline(self, context):
        state.command = "polyline"
        state.points = []
        state.input_text = ""
        self.report({"INFO"}, "Polyline: click/type points. Press Esc to finish. Active vertices can be osnapped.")
        context.workspace.status_text_set(command_label())

    def start_rectangle(self, context):
        state.command = "rectangle"
        state.points = []
        state.input_text = ""
        self.report({"INFO"}, "Rectangle: first corner, then opposite corner.")
        context.workspace.status_text_set(command_label())

    def start_circle(self, context):
        state.command = "circle"
        state.points = []
        state.input_text = ""
        self.report({"INFO"}, "Circle: center point, then radius point.")
        context.workspace.status_text_set(command_label())

    def start_nurbs(self, context, degree=None):
        if degree is None:
            degree = int(getattr(context.scene, "cad_nurbs_degree", 3))

        degree = max(1, min(int(degree), 11))

        context.scene.cad_nurbs_degree = degree
        state.nurbs_degree = degree
        state.command = "nurbs"
        state.points = []
        state.input_text = ""

        self.report({"INFO"}, f"NURBS Curve degree {degree}: click/type control points. Press Esc to finish.")
        context.workspace.status_text_set(command_label())

    def start_xline(self, context):
        state.command = "xline"
        state.points = []
        state.input_text = ""
        self.report({"INFO"}, "XLine: pick first point and second point for direction.")
        context.workspace.status_text_set(command_label())

    def start_polygon(self, context):
        state.command = "polygon"
        state.points = []
        state.input_text = ""
        self.report({"INFO"}, "Polygon: pick centre point and radius point.")
        context.workspace.status_text_set(command_label())

    def start_ellipse(self, context):
        state.command = "ellipse"
        state.points = []
        state.input_text = ""
        self.report({"INFO"}, "Ellipse: pick centre point and radius point.")
        context.workspace.status_text_set(command_label())

    def start_arc(self, context):
        state.command = "arc"
        state.points = []
        state.input_text = ""
        self.report({"INFO"}, "Arc: pick start, point on arc, end.")
        context.workspace.status_text_set(command_label())

    def start_revolve_axis(self, context):
        state.command = "revolve_axis"
        state.points = []
        state.input_text = ""
        self.report({"INFO"}, "Revolve Axis: pick axis start and axis end.")
        context.workspace.status_text_set(command_label())

    def start_cplane_curve_perp(self, context, name):
        state.command = "cplane_curve_perp"
        state.pending_cplane_name = name
        state.pending_cplane_mode = "CURVEPERP"
        state.points = []
        state.input_text = ""
        self.report({"INFO"}, f"CPlane Perp Curve '{name}': select a curve and click point on/near it.")
        context.workspace.status_text_set(command_label())

    def start_cplane_3pt(self, context, name, mode="3PT"):
        mode = (mode or "3PT").upper()

        if mode == "FACE":
            state.command = "cplane_face"
        elif mode == "ROTATE3PT":
            state.command = "cplane_rotate3pt"
        elif mode == "AXISROTATE":
            state.command = "cplane_axisrotate"
        elif mode == "MOVE":
            state.command = "cplane_move"
        else:
            state.command = "cplane3pt"

        state.pending_cplane_name = name
        state.pending_cplane_mode = mode
        state.points = []
        state.input_text = ""

        if mode == "XAXIS":
            msg = f"CPlane X-axis '{name}': origin, X-axis point, Z-direction point."
        elif mode == "ZAXIS":
            msg = f"CPlane Z-axis '{name}': origin, Z-axis point, X-direction point."
        elif mode == "FACE":
            msg = f"CPlane Face '{name}': click a mesh face."
        elif mode == "ROTATE3PT":
            msg = f"CPlane Rotate3Pt '{name}': axis start, axis end, target rotation point."
        elif mode == "AXISROTATE":
            msg = f"CPlane Axis Rotate '{name}': pick axis start and axis end, then use the angle slider/value."
        elif mode == "MOVE":
            msg = f"CPlane Move '{name}': pick start point and end point."
        else:
            msg = f"CPlane 3pt '{name}': origin, X-axis point, Y-direction point."

        self.report({"INFO"}, msg)
        context.workspace.status_text_set(command_label())

    def finalize_current_command(self, context):
        if state.command in {"polyline", "pline", "pl"} and len(state.points) >= 2:
            create_polyline(state.points, name="Hippo3D_Polyline", closed=False)
            self.report({"INFO"}, f"Created polyline with {len(state.points)} points.")
        elif state.command in {"nurbs", "nurbscurve", "curve", "crv"} and len(state.points) >= 2:
            degree = int(getattr(state, "nurbs_degree", getattr(context.scene, "cad_nurbs_degree", 3)))
            obj = create_nurbs_curve(state.points, degree=degree, name="Hippo3D_NURBS_Curve")
            if obj:
                self.report({"INFO"}, f"Created degree-{obj.get('cad_degree', degree)} NURBS curve with {len(state.points)} control points.")

    def add_point(self, context, point):
        point = apply_ortho(context, point)
        if state.command in {"line", "l"}:
            state.points.append(point)
            if len(state.points) >= 2:
                create_line(state.points[-2], state.points[-1])
            context.workspace.status_text_set(command_label())
        elif state.command in {"polyline", "pline", "pl"}:
            state.points.append(point)
            context.workspace.status_text_set(command_label())
        elif state.command in {"rectangle", "rect"}:
            state.points.append(point)
            if len(state.points) >= 2:
                create_rectangle(state.points[0], state.points[1], context)
                self.start_rectangle(context)
            context.workspace.status_text_set(command_label())
        elif state.command in {"circle", "c"}:
            state.points.append(point)
            if len(state.points) >= 2:
                obj = create_circle(state.points[0], state.points[1], context)
                if obj is None:
                    self.report({"WARNING"}, "Circle radius is too small.")
                self.start_circle(context)
            context.workspace.status_text_set(command_label())
        elif state.command in {"nurbs", "nurbscurve", "curve", "crv"}:
            state.points.append(point)
            context.workspace.status_text_set(command_label())
        elif state.command == "cplane_curve_perp":
            ok, msg = create_cplane_perpendicular_to_curve(
                context,
                state.pending_cplane_name or "CPlane Perp Curve",
                point,
            )
            self.report({"INFO" if ok else "WARNING"}, msg)
            state.command = ""
            state.pending_cplane_name = ""
            state.pending_cplane_mode = ""
            state.points = []
            state.input_text = ""
            context.workspace.status_text_set(command_label())
        elif state.command == "cplane3pt":
            state.points.append(point)
            if len(state.points) >= 3:
                ok, msg = create_cplane_from_3_points(
                    context,
                    state.pending_cplane_name or "CPlane",
                    state.points,
                    getattr(state, "pending_cplane_mode", "3PT"),
                )
                self.report({"INFO" if ok else "WARNING"}, msg)
                state.command = ""
                state.pending_cplane_name = ""
                state.pending_cplane_mode = ""
                state.points = []
                state.input_text = ""
            context.workspace.status_text_set(command_label())
        elif state.command == "cplane_rotate3pt":
            state.points.append(point)
            if len(state.points) >= 3:
                ok, msg = rotate_cplane_by_3_points(
                    context,
                    state.pending_cplane_name or "",
                    state.points,
                )
                self.report({"INFO" if ok else "WARNING"}, msg)
                state.command = ""
                state.pending_cplane_name = ""
                state.pending_cplane_mode = ""
                state.points = []
                state.input_text = ""
            context.workspace.status_text_set(command_label())
        elif state.command == "cplane_axisrotate":
            state.points.append(point)
            if len(state.points) >= 2:
                ok, msg = store_cplane_axis_rotation_setup(
                    context,
                    state.pending_cplane_name or "",
                    state.points,
                )
                self.report({"INFO" if ok else "WARNING"}, msg)
                state.command = ""
                state.pending_cplane_name = ""
                state.pending_cplane_mode = ""
                state.points = []
                state.input_text = ""
            context.workspace.status_text_set(command_label())
        elif state.command == "cplane_move":
            state.points.append(point)
            if len(state.points) >= 2:
                ok, msg = move_cplane_by_2_points(
                    context,
                    state.pending_cplane_name or "",
                    state.points,
                )
                self.report({"INFO" if ok else "WARNING"}, msg)
                state.command = ""
                state.pending_cplane_name = ""
                state.pending_cplane_mode = ""
                state.points = []
                state.input_text = ""
            context.workspace.status_text_set(command_label())
        elif state.command == "revolve_axis":
            state.points.append(point)
            if len(state.points) >= 2:
                ok, msg = set_revolve_axis_from_points(context, state.points)
                self.report({"INFO" if ok else "WARNING"}, msg)
                state.command = ""
                state.points = []
                state.input_text = ""
            context.workspace.status_text_set(command_label())
        elif state.command == "arc":
            state.points.append(point)
            if len(state.points) >= 3:
                obj = create_arc_from_3_points(context, state.points[0], state.points[1], state.points[2])
                if obj:
                    self.report({"INFO"}, "Created arc.")
                else:
                    self.report({"WARNING"}, "Arc points are collinear.")
                state.command = ""
                state.points = []
                state.input_text = ""
            context.workspace.status_text_set(command_label())
        elif state.command == "ellipse":
            state.points.append(point)
            if len(state.points) >= 2:
                obj = create_ellipse_from_2_points(context, state.points[0], state.points[1])
                if obj:
                    self.report({"INFO"}, "Created ellipse.")
                else:
                    self.report({"WARNING"}, "Ellipse radius is too small.")
                state.command = ""
                state.points = []
                state.input_text = ""
            context.workspace.status_text_set(command_label())
        elif state.command == "polygon":
            state.points.append(point)
            if len(state.points) >= 2:
                obj = create_polygon_from_2_points(context, state.points[0], state.points[1])
                if obj:
                    self.report({"INFO"}, "Created polygon.")
                else:
                    self.report({"WARNING"}, "Polygon radius is too small.")
                state.command = ""
                state.points = []
                state.input_text = ""
            context.workspace.status_text_set(command_label())
        elif state.command == "xline":
            state.points.append(point)
            if len(state.points) >= 2:
                obj = create_xline_from_2_points(context, state.points[0], state.points[1])
                if obj:
                    self.report({"INFO"}, "Created XLine.")
                else:
                    self.report({"WARNING"}, "XLine points are too close.")
                state.command = ""
                state.points = []
                state.input_text = ""
            context.workspace.status_text_set(command_label())

    def process_enter(self, context):
        txt = state.input_text.strip()
        state.input_text = ""

        if not txt:
            context.workspace.status_text_set(command_label())
            return

        if not state.command:
            cmd = txt.lower()
            if cmd in {"line", "l"}:
                self.start_line(context)
            elif cmd in {"polyline", "pline", "pl"}:
                self.start_polyline(context)
            elif cmd in {"rectangle", "rect"}:
                self.start_rectangle(context)
            elif cmd in {"circle", "c"}:
                self.start_circle(context)
            elif cmd.split() and cmd.split()[0] in {"nurbs", "nurbscurve", "curve", "crv"}:
                degree = None
                parts = cmd.split()
                if len(parts) >= 2:
                    try:
                        if parts[1] in {"degree", "deg", "d"} and len(parts) >= 3:
                            degree = int(parts[2])
                        else:
                            degree = int(parts[1])
                    except Exception:
                        degree = None
                self.start_nurbs(context, degree)
            elif cmd in {"ortho", "f8"}:
                context.scene.cad_ortho = not context.scene.cad_ortho
                self.report({"INFO"}, f"Ortho {'On' if context.scene.cad_ortho else 'Off'}")
            elif cmd in {"arc"}:
                self.start_arc(context)
            elif cmd in {"ellipse", "ell"}:
                self.start_ellipse(context)
            elif cmd in {"polygon", "poly", "ngon"}:
                self.start_polygon(context)
            elif cmd in {"xline", "constructionline", "infiniteline"}:
                self.start_xline(context)
            elif cmd in {"revolveaxis", "revaxis", "setrevolveaxis", "setrevaxis"}:
                self.start_revolve_axis(context)
            elif cmd.startswith("cplane"):
                handled, ok, msg, start_3pt_name = run_cplane_command(context, txt)
                if start_3pt_name:
                    mode = "3PT"
                    name = start_3pt_name
                    if ":" in start_3pt_name:
                        mode, name = start_3pt_name.split(":", 1)
                    if mode == "CURVEPERP":
                        self.start_cplane_curve_perp(context, name)
                    else:
                        self.start_cplane_3pt(context, name, mode)
                elif ok:
                    self.report({"INFO"}, msg)
                else:
                    self.report({"WARNING"}, msg)
            else:
                ok, msg = run_simple_cad_command(context, cmd)
                if ok is None:
                    self.report({"WARNING"}, f"Unknown command: {txt}")
                elif ok:
                    self.report({"INFO"}, msg)
                else:
                    self.report({"WARNING"}, msg)
            return

        if state.command in {"line", "l", "polyline", "pline", "pl", "rectangle", "rect", "circle", "c", "nurbs", "nurbscurve", "curve", "crv", "cplane3pt", "cplane_curve_perp", "cplane_rotate3pt", "cplane_axisrotate", "cplane_move", "revolve_axis", "arc", "ellipse", "polygon", "xline"}:
            pt = parse_point(txt, context, state.points[-1] if state.points else None)

            if pt is None and state.points:
                distance_value = parse_distance_value(txt)
                if distance_value is not None:
                    pt = point_from_distance_in_mouse_direction(context, distance_value)

            if pt is None:
                low = txt.lower()

                if state.command in {"nurbs", "nurbscurve", "curve", "crv"}:
                    parts = low.split()
                    if len(parts) >= 2 and parts[0] in {"degree", "deg", "d"}:
                        try:
                            degree = max(1, min(int(parts[1]), 11))
                            context.scene.cad_nurbs_degree = degree
                            state.nurbs_degree = degree
                            self.report({"INFO"}, f"NURBS degree set to {degree}.")
                            context.workspace.status_text_set(command_label())
                            return
                        except Exception:
                            self.report({"WARNING"}, "Use: degree 3")
                            return
                if low in {"line", "l"}:
                    self.start_line(context)
                elif low in {"polyline", "pline", "pl"}:
                    self.start_polyline(context)
                elif low in {"rectangle", "rect"}:
                    self.start_rectangle(context)
                elif low in {"circle", "c"}:
                    self.start_circle(context)
                elif low in {"nurbs", "nurbscurve", "curve", "crv"}:
                    self.start_nurbs(context)
                elif low in {"ortho", "f8"}:
                    context.scene.cad_ortho = not context.scene.cad_ortho
                    self.report({"INFO"}, f"Ortho {'On' if context.scene.cad_ortho else 'Off'}")
                elif low.startswith("cplane"):
                    handled, ok, msg, start_3pt_name = run_cplane_command(context, txt)
                    if start_3pt_name:
                        self.start_cplane_3pt(context, start_3pt_name)
                    elif ok:
                        self.report({"INFO"}, msg)
                    else:
                        self.report({"WARNING"}, msg)
                else:
                    ok, msg = run_simple_cad_command(context, low)
                    if ok is None:
                        self.report({"WARNING"}, "Type coordinates as x,y,z, click in the viewport, or press Esc to finish the command.")
                    elif ok:
                        self.report({"INFO"}, msg)
                    else:
                        self.report({"WARNING"}, msg)
                return
            self.add_point(context, pt)

    def update_mouse_point(self, context, event):
        raw = mouse_to_plane(context, event)
        state.raw_mouse_world = raw

        # For CPlane creation commands, prioritise object vertex snapping.
        if state.command in {"cplane3pt", "cplane_curve_perp", "cplane_rotate3pt", "cplane_axisrotate", "cplane_move", "revolve_axis", "arc", "ellipse", "polygon", "xline"}:
            mouse_xy = (event.mouse_region_x, event.mouse_region_y)
            vertex_snap = nearest_mesh_vertex_snap(
                context,
                raw,
                mouse_xy,
                radius=float(getattr(context.scene, "cad_snap_radius", 18.0)),
            )
            if vertex_snap is not None:
                snapped = vertex_snap
                label = "Vertex"
            else:
                snapped, label = resolve_snap(context, event, raw)
        else:
            snapped, label = resolve_snap(context, event, raw)

        snapped = apply_ortho(context, snapped)
        state.mouse_world = snapped
        state.snap_point = snapped if label else None
        state.snap_label = label

    def modal(self, context, event):
        if context.area:
            context.area.tag_redraw()

        if event.type == "ESC" and event.value == "PRESS":
            self.finalize_current_command(context)
            finish_command(context)
            return {"CANCELLED"}

        if event.type == "F8" and event.value == "PRESS":
            context.scene.cad_ortho = not context.scene.cad_ortho
            self.report({"INFO"}, f"Ortho {'On' if context.scene.cad_ortho else 'Off'}")
            context.workspace.status_text_set(command_label())
            return {"RUNNING_MODAL"}

        # Ctrl+/ opens or resets the Hippo3D command line without conflicting with Blender's native / shortcut.
        if event.type == "SLASH" and event.ctrl and event.value == "PRESS":
            state.command = ""
            state.points = []
            state.input_text = ""
            state.active = True
            context.workspace.status_text_set(command_label())
            return {"RUNNING_MODAL"}

        if event.type == "MOUSEMOVE":
            self.update_mouse_point(context, event)
            context.workspace.status_text_set(command_label())
            return {"RUNNING_MODAL"}

        if event.type == "LEFTMOUSE" and event.value == "PRESS":
            if state.command == "cplane_face":
                hit = raycast_mesh_face(context, event)
                ok, msg = create_cplane_from_face_hit(
                    context,
                    state.pending_cplane_name or "CPlane",
                    hit,
                )
                self.report({"INFO" if ok else "WARNING"}, msg)
                state.command = ""
                state.pending_cplane_name = ""
                state.pending_cplane_mode = ""
                state.points = []
                state.input_text = ""
                context.workspace.status_text_set(command_label())
                return {"RUNNING_MODAL"}

            if state.command in {"line", "l", "polyline", "pline", "pl", "rectangle", "rect", "circle", "c", "nurbs", "nurbscurve", "curve", "crv", "cplane3pt", "cplane_curve_perp", "cplane_rotate3pt", "cplane_axisrotate", "cplane_move", "revolve_axis", "arc", "ellipse", "polygon", "xline"}:
                self.update_mouse_point(context, event)
                self.add_point(context, state.mouse_world)
                return {"RUNNING_MODAL"}

        if event.type in {"RET", "NUMPAD_ENTER"} and event.value == "PRESS":
            self.process_enter(context)
            return {"RUNNING_MODAL"}

        if event.type == "BACK_SPACE" and event.value == "PRESS":
            state.input_text = state.input_text[:-1]
            context.workspace.status_text_set(command_label())
            return {"RUNNING_MODAL"}

        if event.value == "PRESS" and event.ascii:
            state.input_text += event.ascii
            context.workspace.status_text_set(command_label())
            return {"RUNNING_MODAL"}

        return {"RUNNING_MODAL"}

    def invoke(self, context, event):
        if context.area.type != "VIEW_3D":
            self.report({"WARNING"}, "CAD commands must be started in the 3D View.")
            return {"CANCELLED"}

        state.active = True
        state.command = ""
        state.points = []
        state.input_text = ""
        if event:
            self.update_mouse_point(context, event)
        else:
            state.mouse_world = Vector((0, 0, 0))

        try:
            context.window.cursor_modal_set("CROSSHAIR")
            state.cursor_set = True
        except Exception:
            state.cursor_set = False

        if state.draw_handle is None:
            state.draw_handle = bpy.types.SpaceView3D.draw_handler_add(draw_callback, (), "WINDOW", "POST_VIEW")
        if state.text_handle is None:
            state.text_handle = bpy.types.SpaceView3D.draw_handler_add(draw_text_callback, (), "WINDOW", "POST_PIXEL")

        initial = self.initial_command.lower().strip()
        if initial.startswith("cplane perpcurve") or initial.startswith("cplane curveperp") or initial.startswith("cplane perpendicular"):
            parts = initial.split()
            name = " ".join(parts[2:]) if len(parts) >= 3 else "CPlane Perp Curve"
            self.start_cplane_curve_perp(context, name)
            context.window_manager.modal_handler_add(self)
            return {"RUNNING_MODAL"}

        if initial in {"line", "l"}:
            self.start_line(context)
        elif initial in {"polyline", "pline", "pl"}:
            self.start_polyline(context)
        elif initial in {"rectangle", "rect"}:
            self.start_rectangle(context)
        elif initial in {"circle", "c"}:
            self.start_circle(context)
        elif initial in {"arc"}:
            self.start_arc(context)
        elif initial in {"ellipse", "ell"}:
            self.start_ellipse(context)
        elif initial in {"polygon", "poly", "ngon"}:
            self.start_polygon(context)
        elif initial in {"xline", "constructionline", "infiniteline"}:
            self.start_xline(context)
        elif initial in {"revolveaxis", "revaxis", "setrevolveaxis", "setrevaxis"}:
            self.start_revolve_axis(context)
        elif initial and (initial.split()[0] if initial.split() else '') in {"nurbs", "nurbscurve", "curve", "crv"}:
            degree = None
            parts = initial.split()
            if len(parts) >= 2:
                try:
                    if parts[1] in {"degree", "deg", "d"} and len(parts) >= 3:
                        degree = int(parts[2])
                    else:
                        degree = int(parts[1])
                except Exception:
                    degree = None
            self.start_nurbs(context, degree)
        elif initial.startswith("cplane"):
            handled, ok, msg, start_3pt_name = run_cplane_command(context, self.initial_command)
            if start_3pt_name:
                mode = "3PT"
                name = start_3pt_name
                if ":" in start_3pt_name:
                    mode, name = start_3pt_name.split(":", 1)
                if mode == "CURVEPERP":
                    self.start_cplane_curve_perp(context, name)
                else:
                    self.start_cplane_3pt(context, name, mode)
            elif ok:
                self.report({"INFO"}, msg)
            else:
                self.report({"WARNING"}, msg)
        elif initial:
            ok, msg = run_simple_cad_command(context, initial)
            if ok is None:
                self.report({"WARNING"}, f"Unknown command: {self.initial_command}")
            elif ok:
                self.report({"INFO"}, msg)
            else:
                self.report({"WARNING"}, msg)
            context.workspace.status_text_set(command_label())
        else:
            context.workspace.status_text_set(command_label())

        if not initial:
            state.active = True
            state.command = ""
            state.points = []
            state.input_text = ""
            context.workspace.status_text_set(command_label())

        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}


class Hippo3D_OT_StartLine(Operator):
    bl_idname = "cad.start_line"
    bl_label = "Line"
    bl_description = "Start Rhino-like Line command. Click points or type coordinates."

    def execute(self, context):
        bpy.ops.cad.command("INVOKE_DEFAULT", initial_command="line")
        return {"FINISHED"}


class Hippo3D_OT_StartPolyline(Operator):
    bl_idname = "cad.start_polyline"
    bl_label = "Polyline"
    bl_description = "Start CAD Polyline command. Click/type points; Esc finishes."

    def execute(self, context):
        bpy.ops.cad.command("INVOKE_DEFAULT", initial_command="polyline")
        return {"FINISHED"}


class Hippo3D_OT_StartRectangle(Operator):
    bl_idname = "cad.start_rectangle"
    bl_label = "Rectangle"
    bl_description = "Start CAD Rectangle command from two opposite corners."

    def execute(self, context):
        bpy.ops.cad.command("INVOKE_DEFAULT", initial_command="rectangle")
        return {"FINISHED"}


class Hippo3D_OT_StartCircle(Operator):
    bl_idname = "cad.start_circle"
    bl_label = "Circle"
    bl_description = "Start CAD Circle command from center and radius point."

    def execute(self, context):
        bpy.ops.cad.command("INVOKE_DEFAULT", initial_command="circle")
        return {"FINISHED"}


class Hippo3D_OT_StartNurbs(Operator):
    bl_idname = "cad.start_nurbs"
    bl_label = "NURBS Curve"
    bl_description = "Start degree-3 CAD NURBS/control-point curve command."

    def execute(self, context):
        bpy.ops.cad.command("INVOKE_DEFAULT", initial_command="nurbs")
        return {"FINISHED"}


class Hippo3D_OT_StartCommand(Operator):
    bl_idname = "cad.start_command"
    bl_label = "Command Line"
    bl_description = "Open CAD command mode. Type line, then Enter."

    def execute(self, context):
        bpy.ops.cad.command("INVOKE_DEFAULT")
        return {"FINISHED"}


class Hippo3D_OT_ToggleOrtho(Operator):
    bl_idname = "cad.toggle_ortho"
    bl_label = "Toggle Ortho"
    bl_description = "Toggle Rhino-like ortho constraint. Also available with F8 during a command."

    def execute(self, context):
        context.scene.cad_ortho = not context.scene.cad_ortho
        return {"FINISHED"}


class Hippo3D_OT_ConvertToMesh(Operator):
    bl_idname = "cad.convert_to_mesh"
    bl_label = "Convert to Mesh"
    bl_description = "Convert selected curves/surfaces/fonts/metaballs to mesh. Command aliases: mesh, tomesh, convertmesh, ctm."

    def execute(self, context):
        ok, msg = convert_selected_to_mesh(context)
        self.report({"INFO" if ok else "WARNING"}, msg)
        return {"FINISHED"}


class Hippo3D_OT_Join(Operator):
    bl_idname = "cad.join"
    bl_label = "Join"
    bl_description = "Join selected objects. Command aliases: join, j."

    def execute(self, context):
        ok, msg = join_selected_objects(context)
        self.report({"INFO" if ok else "WARNING"}, msg)
        return {"FINISHED"}








# -----------------------------------------------------------------------------
# Scrollable CPlane list
# -----------------------------------------------------------------------------

class Hippo3D_CPlaneListItem(PropertyGroup):
    name: StringProperty(default="")
    display_name: StringProperty(default="")
    builtin_mode: StringProperty(default="")
    layer_key: StringProperty(default="")
    is_builtin: BoolProperty(default=False)


def cplane_layer_rows(context):
    rows = [
        {"name": "Top / XY", "display": "Top / XY", "builtin": True, "mode": "TOP", "key": "BUILTIN:TOP"},
        {"name": "Front / XZ", "display": "Front / XZ", "builtin": True, "mode": "FRONT", "key": "BUILTIN:FRONT"},
        {"name": "Right / YZ", "display": "Right / YZ", "builtin": True, "mode": "RIGHT", "key": "BUILTIN:RIGHT"}]
    for name in cplane_library_names(context):
        rows.append({"name": name, "display": name, "builtin": False, "mode": "", "key": "NAMED:" + name})
    return rows


def sync_cplane_layer_collection(context):
    """Rebuild the scrollable CPlane list.

    Do not call this from Panel.draw() in Blender 5. UI draw is read-only for
    ID data-block writes, so this function is called from operators/register.
    """
    if not context or not context.scene:
        return

    scene = context.scene
    if not hasattr(scene, "cad_cplane_items"):
        return

    rows = cplane_layer_rows(context)

    scene.cad_cplane_items.clear()
    for row in rows:
        item = scene.cad_cplane_items.add()
        item.name = row["name"]
        item.display_name = row["display"]
        item.is_builtin = row["builtin"]
        item.builtin_mode = row["mode"]
        item.layer_key = row.get("key", "")

    if scene.cad_cplane_index >= len(scene.cad_cplane_items):
        scene.cad_cplane_index = max(0, len(scene.cad_cplane_items) - 1)

def selected_cplane_item(context):
    scene = context.scene
    if not hasattr(scene, "cad_cplane_items"):
        return None
    if len(scene.cad_cplane_items) == 0:
        try:
            sync_cplane_layer_collection(context)
        except Exception:
            pass
    if len(scene.cad_cplane_items) == 0:
        return None
    idx = max(0, min(scene.cad_cplane_index, len(scene.cad_cplane_items) - 1))
    return scene.cad_cplane_items[idx]





class Hippo3D_UL_CPlaneList(UIList):
    bl_idname = "Hippo3D_UL_cplane_list"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)

        if item.is_builtin:
            visible = is_cplane_visible(context, builtin_mode=item.builtin_mode)
            active = (
                not getattr(context.scene, "cad_active_cplane_name", "")
                and getattr(context.scene, "cad_cplane", "TOP").upper() == item.builtin_mode
            )
        else:
            visible = is_cplane_visible(context, name=item.name)
            active = getattr(context.scene, "cad_active_cplane_name", "") == item.name

        eye = row.operator(
            "cad.toggle_cplane_visibility_explicit",
            text="",
            icon=("HIDE_OFF" if visible else "HIDE_ON"),
            emboss=False,
        )
        eye.layer_key = item.layer_key

        act = row.operator(
            "cad.activate_cplane_explicit",
            text="",
            icon=("RADIOBUT_ON" if active else "RADIOBUT_OFF"),
            emboss=False,
        )
        act.layer_key = item.layer_key

        row.label(text=item.display_name)




class Hippo3D_OT_RefreshCPlaneList(Operator):
    bl_idname = "cad.refresh_cplane_list"
    bl_label = "Refresh CPlane List"

    def execute(self, context):
        sync_cplane_layer_collection(context)
        return {"FINISHED"}


class Hippo3D_OT_ToggleSelectedCPlaneVisible(Operator):
    bl_idname = "cad.toggle_selected_cplane_visible"
    bl_label = "Toggle Selected CPlane Visibility"

    def execute(self, context):
        item = selected_cplane_item(context)
        if item is None:
            return {"CANCELLED"}

        if item.is_builtin:
            current = is_cplane_visible(context, builtin_mode=item.builtin_mode)
            set_cplane_visible(context, not current, builtin_mode=item.builtin_mode)
        else:
            current = is_cplane_visible(context, name=item.name)
            set_cplane_visible(context, not current, name=item.name)

        return {"FINISHED"}




class Hippo3D_OT_ActivateSelectedCPlane(Operator):
    bl_idname = "cad.activate_selected_cplane"
    bl_label = "Make Selected CPlane Active"

    def execute(self, context):
        item = selected_cplane_item(context)
        if item is None:
            return {"CANCELLED"}

        if item.is_builtin:
            set_builtin_cplane(context, item.builtin_mode)
            self.report({"INFO"}, f"Active CPlane: {item.display_name}")
        else:
            set_named_cplane(context, item.name)
            self.report({"INFO"}, f"Active CPlane: {item.name}")

        sync_cplane_dropdown(context)
        return {"FINISHED"}


class Hippo3D_OT_DeleteSelectedCPlane(Operator):
    bl_idname = "cad.delete_selected_cplane"
    bl_label = "Delete Selected CPlane"

    def execute(self, context):
        item = selected_cplane_item(context)
        if item is None:
            return {"CANCELLED"}

        if item.is_builtin:
            self.report({"WARNING"}, "Default CPlanes cannot be deleted.")
            return {"CANCELLED"}

        data = load_saved_cplanes(context)
        if item.name in data:
            del data[item.name]
            save_saved_cplanes(context, data)

        vis = load_cplane_visibility(context)
        vis.pop("NAMED:" + item.name, None)
        save_cplane_visibility(context, vis)

        if getattr(context.scene, "cad_active_cplane_name", "") == item.name:
            context.scene.cad_active_cplane_name = ""
            context.scene.cad_cplane = "TOP"

        sync_cplane_dropdown(context)
        sync_cplane_layer_collection(context)
        self.report({"INFO"}, f"Deleted CPlane '{item.name}'.")
        return {"FINISHED"}


class Hippo3D_OT_SetBuiltinCPlane(Operator):
    bl_idname = "cad.set_builtin_cplane"
    bl_label = "Make Built-in CPlane Active"

    mode: StringProperty(default="TOP")

    def execute(self, context):
        set_builtin_cplane(context, self.mode)
        context.scene.cad_show_cplane_visuals = True
        set_cplane_visible(context, True, builtin_mode=self.mode)
        sync_cplane_dropdown(context)
        self.report({"INFO"}, f"Active CPlane: {self.mode.title()}")
        return {"FINISHED"}

class Hippo3D_OT_RestoreCPlaneByName(Operator):
    bl_idname = "cad.restore_cplane_by_name"
    bl_label = "Make CPlane Active"

    name: StringProperty(default="")

    def execute(self, context):
        if set_named_cplane(context, self.name):
            context.scene.cad_show_cplane_visuals = True
            set_cplane_visible(context, True, name=self.name)
            sync_cplane_dropdown(context)
            sync_current_cplane_visibility(context)
            self.report({"INFO"}, f"Active CPlane: {self.name}")
            return {"FINISHED"}
        self.report({"WARNING"}, f"No saved CPlane named '{self.name}'.")
        return {"CANCELLED"}

# -----------------------------------------------------------------------------
# Sidebar UI
# -----------------------------------------------------------------------------

class Hippo3D_OT_SaveCPlane(Operator):
    bl_idname = "cad.save_cplane"
    bl_label = "Save Current CPlane"
    bl_description = "Save the currently active construction plane by name."

    def execute(self, context):
        ok, msg = save_current_cplane_as(context, context.scene.cad_cplane_save_name)
        self.report({"INFO" if ok else "WARNING"}, msg)
        return {"FINISHED"}


class Hippo3D_OT_RestoreCPlane(Operator):
    bl_idname = "cad.restore_cplane"
    bl_label = "Restore CPlane"
    bl_description = "Restore a saved construction plane by name."

    def execute(self, context):
        name = context.scene.cad_cplane_save_name.strip()
        if set_named_cplane(context, name):
            self.report({"INFO"}, f"Restored CPlane '{name}'.")
        else:
            self.report({"WARNING"}, f"No saved CPlane named '{name}'.")
        return {"FINISHED"}


class Hippo3D_OT_StartCPlane3Pt(Operator):
    bl_idname = "cad.start_cplane_3pt"
    bl_label = "Create 3-Point CPlane"
    bl_description = "Create and save a named construction plane from 3 points."

    def execute(self, context):
        name = context.scene.cad_cplane_save_name.strip() or "CPlane"
        bpy.ops.cad.command("INVOKE_DEFAULT", initial_command=f"cplane 3pt {name}")
        return {"FINISHED"}




class Hippo3D_OT_StartCPlaneXAxis(Operator):
    bl_idname = "cad.start_cplane_xaxis"
    bl_label = "Create X-Axis CPlane"
    bl_description = "Create and save a CPlane from origin, X-axis point, and Z-direction point."

    def execute(self, context):
        name = context.scene.cad_cplane_save_name.strip() or "CPlane"
        bpy.ops.cad.command("INVOKE_DEFAULT", initial_command=f"cplane xaxis {name}")
        return {"FINISHED"}


class Hippo3D_OT_StartCPlaneZAxis(Operator):
    bl_idname = "cad.start_cplane_zaxis"
    bl_label = "Create Z-Axis CPlane"
    bl_description = "Create and save a CPlane from origin, Z-axis point, and X-direction point."

    def execute(self, context):
        name = context.scene.cad_cplane_save_name.strip() or "CPlane"
        bpy.ops.cad.command("INVOKE_DEFAULT", initial_command=f"cplane zaxis {name}")
        return {"FINISHED"}


class Hippo3D_OT_StartCPlaneFace(Operator):
    bl_idname = "cad.start_cplane_face"
    bl_label = "Create Face CPlane"
    bl_description = "Create and save a CPlane from a clicked mesh face."

    def execute(self, context):
        name = context.scene.cad_cplane_save_name.strip() or "CPlane"
        bpy.ops.cad.command("INVOKE_DEFAULT", initial_command=f"cplane face {name}")
        return {"FINISHED"}




class Hippo3D_OT_StartCPlaneCurvePerp(Operator):
    bl_idname = "cad.start_cplane_curve_perp"
    bl_label = "Create Curve Perpendicular CPlane"
    bl_description = "Create and save a CPlane perpendicular to a selected curve at a picked point."

    def execute(self, context):
        name = context.scene.cad_cplane_save_name.strip() or "CPlane Perp Curve"
        bpy.ops.cad.command("INVOKE_DEFAULT", initial_command=f"cplane perpcurve {name}")
        return {"FINISHED"}

class Hippo3D_OT_RotateCPlane(Operator):
    bl_idname = "cad.rotate_cplane"
    bl_label = "Rotate CPlane"
    bl_description = "Rotate the active CPlane around local X, Y, or Z axis."

    axis: StringProperty(default="Z")

    def execute(self, context):
        angle = float(getattr(context.scene, "cad_cplane_rotate_angle", 90.0))
        ok, msg = rotate_active_cplane(context, self.axis, angle)
        self.report({"INFO" if ok else "WARNING"}, msg)
        return {"FINISHED"}




class Hippo3D_OT_StartCPlaneRotate3Pt(Operator):
    bl_idname = "cad.start_cplane_rotate3pt"
    bl_label = "Rotate CPlane by 3 Points"
    bl_description = "Rotate the active CPlane by defining an axis with two points and a third target rotation point."

    def execute(self, context):
        name = context.scene.cad_cplane_save_name.strip()
        if name:
            bpy.ops.cad.command("INVOKE_DEFAULT", initial_command=f"cplane rotate3pt {name}")
        else:
            bpy.ops.cad.command("INVOKE_DEFAULT", initial_command="cplane rotate3pt")
        return {"FINISHED"}



class Hippo3D_OT_StartCPlaneAxisRotate(Operator):
    bl_idname = "cad.start_cplane_axisrotate"
    bl_label = "Axis Rotate CPlane"
    bl_description = "Pick two points to define a rotation axis, then control CPlane rotation with the angle slider/value."

    def execute(self, context):
        name = context.scene.cad_cplane_save_name.strip()
        if name:
            bpy.ops.cad.command("INVOKE_DEFAULT", initial_command=f"cplane axisrotate {name}")
        else:
            bpy.ops.cad.command("INVOKE_DEFAULT", initial_command="cplane axisrotate")
        return {"FINISHED"}


class Hippo3D_OT_ApplyCPlaneAxisRotation(Operator):
    bl_idname = "cad.apply_cplane_axis_rotation"
    bl_label = "Apply Axis Rotation"

    def execute(self, context):
        ok, msg = apply_cplane_axis_rotation(context)
        self.report({"INFO" if ok else "WARNING"}, msg)
        return {"FINISHED"}



class Hippo3D_OT_StartCPlaneMove(Operator):
    bl_idname = "cad.start_cplane_move"
    bl_label = "Move CPlane"
    bl_description = "Move the active CPlane by picking a start point and endpoint."

    def execute(self, context):
        name = context.scene.cad_cplane_save_name.strip()
        if name:
            bpy.ops.cad.command("INVOKE_DEFAULT", initial_command=f"cplane move {name}")
        else:
            bpy.ops.cad.command("INVOKE_DEFAULT", initial_command="cplane move")
        return {"FINISHED"}



class Hippo3D_OT_ViewToCPlane(Operator):
    bl_idname = "cad.view_to_cplane"
    bl_label = "View to CPlane"
    bl_description = "Align the current 3D View to the active CPlane."

    def execute(self, context):
        ok, msg = view_to_active_cplane(context)
        self.report({"INFO" if ok else "WARNING"}, msg)
        return {"FINISHED"}


class Hippo3D_OT_CameraToCPlane(Operator):
    bl_idname = "cad.camera_to_cplane"
    bl_label = "Camera to CPlane"
    bl_description = "Align the scene camera to the active CPlane."

    def execute(self, context):
        distance = float(getattr(context.scene, "cad_cplane_camera_distance", 20.0))
        ok, msg = camera_to_active_cplane(context, distance)
        self.report({"INFO" if ok else "WARNING"}, msg)
        return {"FINISHED"}



class Hippo3D_OT_SetSelectedNurbsDegree(Operator):
    bl_idname = "cad.set_selected_nurbs_degree"
    bl_label = "Set Selected NURBS Degree"
    bl_description = "Change the degree of selected NURBS curve objects."

    def execute(self, context):
        degree = int(getattr(context.scene, "cad_selected_nurbs_degree", getattr(context.scene, "cad_nurbs_degree", 3)))
        ok, msg = set_selected_nurbs_degree(context, degree)
        self.report({"INFO" if ok else "WARNING"}, msg)
        return {"FINISHED"}



class Hippo3D_OT_Hippo3D_Loft(Operator):
    bl_idname = "cad.loft_surface"
    bl_label = "Loft Surface"
    bl_description = "Create a loft surface from two or more selected curves."

    def execute(self, context):
        ok, msg = run_loft_command(context)
        self.report({"INFO" if ok else "WARNING"}, msg)
        return {"FINISHED"}


class Hippo3D_OT_RebuildHippo3D_Loft(Operator):
    bl_idname = "cad.rebuild_loft_surface"
    bl_label = "Rebuild Loft"
    bl_description = "Rebuild selected CAD loft surface from stored source curves."

    def execute(self, context):
        ok, msg = rebuild_loft_surface(context)
        self.report({"INFO" if ok else "WARNING"}, msg)
        return {"FINISHED"}




# -----------------------------------------------------------------------------
# Rhino-like surface operations - Phase 2A
# Extrude, Pipe, Revolve, Sweep1
# -----------------------------------------------------------------------------


def hippo_build_grid_surface_from_sections(sections, closed_u=False, closed_v=False):
    """Build mesh verts/faces from sampled section point rows, using C if available."""
    if HIPPO_NATIVE_SURFACE_AVAILABLE and hippo_surface_native is not None:
        try:
            tuple_sections = [[(p.x, p.y, p.z) for p in row] for row in sections]
            return hippo_surface_native.build_grid_surface(
                tuple_sections,
                closed_u=bool(closed_u),
                closed_v=bool(closed_v),
            )
        except Exception:
            pass

    rows = len(sections)
    cols = len(sections[0])
    verts = []
    for row in sections:
        verts.extend([(p.x, p.y, p.z) for p in row])

    faces = []
    r_steps = rows if closed_v else rows - 1
    c_steps = cols if closed_u else cols - 1

    for r in range(r_steps):
        rn = (r + 1) % rows
        for c in range(c_steps):
            cn = (c + 1) % cols
            a = r * cols + c
            b = r * cols + cn
            cc = rn * cols + cn
            d = rn * cols + c
            faces.append((a, b, cc, d))

    return verts, faces


def hippo_native_backend_label():
    return "C_NATIVE" if HIPPO_NATIVE_SURFACE_AVAILABLE else "PYTHON_FALLBACK"


def make_mesh_object(context, name, verts, faces, custom_props=None):
    mesh = bpy.data.meshes.new(name + "_Mesh")
    mesh.from_pydata(verts, [], faces)
    mesh.update()

    obj = bpy.data.objects.new(name, mesh)
    context.collection.objects.link(obj)

    if custom_props:
        for key, value in custom_props.items():
            obj[key] = value

    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    context.view_layer.objects.active = obj

    return obj


def cplane_normal(context):
    origin, u, v, n = get_cplane_axes(context)
    return n.normalized()


def create_extrude_surface_from_curves(context, curves=None, distance=None, samples=None, cap=False):
    if curves is None:
        curves = selected_curve_objects(context)
    if distance is None:
        distance = float(getattr(context.scene, "cad_extrude_distance", 5.0))
    if samples is None:
        samples = int(getattr(context.scene, "cad_surface_samples", 32))

    curves = [obj for obj in curves if obj and obj.type == "CURVE"]

    if not curves:
        return False, "Select at least one curve to extrude."

    normal = cplane_normal(context)
    created = 0

    for curve in curves:
        pts = sample_curve_object_points(curve, samples=samples)
        if len(pts) < 2:
            continue

        offset = normal * distance

        verts = []
        for p in pts:
            verts.append((p.x, p.y, p.z))
        for p in pts:
            q = p + offset
            verts.append((q.x, q.y, q.z))

        n = len(pts)
        faces = []

        for i in range(n - 1):
            faces.append((i, i + 1, n + i + 1, n + i))

        if cap and n >= 3:
            faces.append(tuple(range(n)))
            faces.append(tuple(range(2 * n - 1, n - 1, -1)))

        obj = make_mesh_object(
            context,
            "Hippo3D_Extrude_Surface",
            verts,
            faces,
            {
                "cad_surface_type": "extrude",
                "cad_source_curve": curve.name,
                "cad_extrude_distance": distance,
                "cad_surface_samples": samples,
            },
        )
        created += 1

    if created == 0:
        return False, "No valid curves were extruded."

    return True, f"Created {created} extrude surface(s)."


def create_pipe_from_curves(context, curves=None, radius=None, resolution=None, fill_caps=True):
    """Pipe using Blender curve bevel_depth for Phase 2A.

    This keeps the result as a Curve object with bevel settings, which is closer
    to a non-destructive pipe than immediately converting to mesh.
    """
    if curves is None:
        curves = selected_curve_objects(context)
    if radius is None:
        radius = float(getattr(context.scene, "cad_pipe_radius", 0.25))
    if resolution is None:
        resolution = int(getattr(context.scene, "cad_pipe_resolution", 12))

    curves = [obj for obj in curves if obj and obj.type == "CURVE"]

    if not curves:
        return False, "Select at least one curve to pipe."

    created = 0

    for src in curves:
        new_data = src.data.copy()
        new_obj = src.copy()
        new_obj.data = new_data
        new_obj.name = "Hippo3D_Pipe"
        context.collection.objects.link(new_obj)

        new_data.dimensions = "3D"
        new_data.bevel_depth = radius
        new_data.bevel_resolution = max(1, resolution // 4)
        new_data.resolution_u = max(2, resolution)
        new_data.use_fill_caps = bool(fill_caps)

        new_obj["cad_surface_type"] = "pipe"
        new_obj["cad_source_curve"] = src.name
        new_obj["cad_pipe_radius"] = radius
        new_obj["cad_pipe_resolution"] = resolution

        created += 1

    return True, f"Created {created} pipe object(s)."



def get_revolve_axis(context):
    """Return stored revolve axis, or active CPlane Z axis as fallback."""
    raw = getattr(context.scene, "cad_revolve_axis_json", "")
    if raw:
        try:
            data = json.loads(raw)
            origin = Vector(data.get("origin", (0, 0, 0)))
            axis = Vector(data.get("axis", (0, 0, 1)))
            if axis.length > 1e-8:
                return origin, axis.normalized()
        except Exception:
            pass

    origin, u, v, n = get_cplane_axes(context)
    return origin, n.normalized()


def set_revolve_axis_from_points(context, points):
    if len(points) < 2:
        return False, "Revolve axis needs start and end points."

    p0, p1 = points[:2]
    axis = p1 - p0

    if axis.length < 1e-8:
        return False, "Revolve axis points are too close together."

    axis.normalize()

    context.scene.cad_revolve_axis_json = json.dumps({
        "origin": [p0.x, p0.y, p0.z],
        "axis": [axis.x, axis.y, axis.z],
    })

    return True, "Revolve axis set."


def clear_revolve_axis(context):
    context.scene.cad_revolve_axis_json = ""
    return True, "Revolve axis cleared. Using active CPlane Z axis."


def create_revolve_surface_from_curves(context, curves=None, angle_degrees=None, steps=None):
    if curves is None:
        curves = selected_curve_objects(context)
    if angle_degrees is None:
        angle_degrees = float(getattr(context.scene, "cad_revolve_angle", 360.0))
    if steps is None:
        steps = int(getattr(context.scene, "cad_revolve_steps", 48))

    curves = [obj for obj in curves if obj and obj.type == "CURVE"]

    if not curves:
        return False, "Select at least one profile curve to revolve."

    import math as _math
    origin, axis = get_revolve_axis(context)
    angle_rad = _math.radians(angle_degrees)
    steps = max(3, int(steps))
    samples = int(getattr(context.scene, "cad_surface_samples", 32))

    created = 0

    for curve in curves:
        profile = sample_curve_object_points(curve, samples=samples)
        if len(profile) < 2:
            continue

        verts = []
        for s in range(steps + 1):
            t = angle_rad * (s / steps)
            rot = Matrix.Rotation(t, 4, axis)
            for p in profile:
                q = origin + (rot @ (p - origin))
                verts.append((q.x, q.y, q.z))

        n = len(profile)
        faces = []

        for s in range(steps):
            row = s * n
            next_row = (s + 1) * n
            for i in range(n - 1):
                faces.append((row + i, row + i + 1, next_row + i + 1, next_row + i))

        obj = make_mesh_object(
            context,
            "Hippo3D_Revolve_Surface",
            verts,
            faces,
            {
                "cad_surface_type": "revolve",
                "cad_source_curve": curve.name,
                "cad_revolve_angle": angle_degrees,
                "cad_revolve_steps": steps,
            },
        )
        created += 1

    if created == 0:
        return False, "No valid profile curves were revolved."

    return True, f"Created {created} revolve surface(s)."


def frame_from_tangent(tangent):
    """Create an approximate moving frame from a tangent."""
    t = tangent.normalized()
    up = Vector((0, 0, 1))
    if abs(t.dot(up)) > 0.95:
        up = Vector((0, 1, 0))
    x = up.cross(t).normalized()
    y = t.cross(x).normalized()
    return x, y, t


def create_sweep1_surface(context, rail=None, profiles=None, rail_samples=None, profile_samples=None):
    """Simple Sweep1: one rail and one profile.

    Selection rule:
    - Active object = rail
    - Other selected curve = profile
    """
    if rail_samples is None:
        rail_samples = int(getattr(context.scene, "cad_sweep_rail_samples", 32))
    if profile_samples is None:
        profile_samples = int(getattr(context.scene, "cad_sweep_profile_samples", 24))

    active = context.active_object

    if rail is None:
        rail = active if active and active.type == "CURVE" else None

    curves = selected_curve_objects(context)
    if profiles is None:
        profiles = [obj for obj in curves if obj != rail]

    if rail is None or rail.type != "CURVE":
        return False, "Select a rail curve as the active object."

    if not profiles:
        return False, "Select at least one profile curve in addition to the active rail."

    profile = profiles[0]

    rail_pts = sample_curve_object_points(rail, samples=rail_samples)
    profile_pts = sample_curve_object_points(profile, samples=profile_samples)

    if len(rail_pts) < 2 or len(profile_pts) < 2:
        return False, "Rail/profile curve does not have enough points."

    # Use profile centroid as local origin.
    centroid = Vector((0, 0, 0))
    for p in profile_pts:
        centroid += p
    centroid /= len(profile_pts)
    profile_local = [p - centroid for p in profile_pts]

    verts = []

    for i, rp in enumerate(rail_pts):
        if i == 0:
            tangent = rail_pts[1] - rail_pts[0]
        elif i == len(rail_pts) - 1:
            tangent = rail_pts[-1] - rail_pts[-2]
        else:
            tangent = rail_pts[i + 1] - rail_pts[i - 1]

        if tangent.length < 1e-8:
            tangent = Vector((0, 0, 1))

        x, y, t = frame_from_tangent(tangent)

        for lp in profile_local:
            q = rp + x * lp.x + y * lp.y
            verts.append((q.x, q.y, q.z))

    rows = len(rail_pts)
    cols = len(profile_pts)
    faces = []

    for r in range(rows - 1):
        for c in range(cols - 1):
            a = r * cols + c
            b = r * cols + c + 1
            cc = (r + 1) * cols + c + 1
            d = (r + 1) * cols + c
            faces.append((a, b, cc, d))

    obj = make_mesh_object(
        context,
        "Hippo3D_Sweep1_Surface",
        verts,
        faces,
        {
            "cad_surface_type": "sweep1",
            "cad_rail_curve": rail.name,
            "cad_profile_curve": profile.name,
            "cad_sweep_rail_samples": rail_samples,
            "cad_sweep_profile_samples": profile_samples,
        },
    )

    return True, f"Created Sweep1 surface using rail '{rail.name}' and profile '{profile.name}'."



def create_sweep2_surface(context, rail_samples=None):
    """Approximate Sweep2 from two rail curves and one profile curve.

    Selection rule:
    - Select exactly 3 curves.
    - Active curve is treated as profile when possible.
    - Other two curves are rails.
    """
    curves = selected_curve_objects(context)
    if len(curves) < 3:
        return False, "Sweep2: select two rails and one profile curve."

    active = context.active_object
    if active and active.type == "CURVE" and active in curves:
        profile = active
        rails = [c for c in curves if c != profile][:2]
    else:
        rails = curves[:2]
        profile = curves[2]

    if len(rails) < 2:
        return False, "Sweep2 needs two rail curves."

    if rail_samples is None:
        rail_samples = int(getattr(context.scene, "cad_sweep_rail_samples", 32))
    profile_samples = int(getattr(context.scene, "cad_sweep_profile_samples", 24))

    rail_a = sample_curve_object_points(rails[0], samples=rail_samples)
    rail_b = sample_curve_object_points(rails[1], samples=rail_samples)
    profile_pts = sample_curve_object_points(profile, samples=profile_samples)

    if len(rail_a) < 2 or len(rail_b) < 2 or len(profile_pts) < 2:
        return False, "Sweep2 rail/profile curves need more points."

    # Auto-align rail directions. If rail B runs opposite rail A, reverse it.
    same_dir = (rail_a[0] - rail_b[0]).length + (rail_a[-1] - rail_b[-1]).length
    flip_dir = (rail_a[0] - rail_b[-1]).length + (rail_a[-1] - rail_b[0]).length
    if flip_dir < same_dir:
        rail_b = list(reversed(rail_b))

    # Normalise profile across [0,1] between rails using its longest local axis.
    xs = [p.x for p in profile_pts]
    ys = [p.y for p in profile_pts]
    x_span = max(xs) - min(xs)
    y_span = max(ys) - min(ys)

    use_y = y_span > x_span
    min_val = min(ys) if use_y else min(xs)
    max_val = max(ys) if use_y else max(xs)
    span = max(max_val - min_val, 1e-8)

    # Ensure profile parameter direction is consistent from rail A to rail B.
    first_val = profile_pts[0].y if use_y else profile_pts[0].x
    last_val = profile_pts[-1].y if use_y else profile_pts[-1].x
    if last_val < first_val:
        profile_pts = list(reversed(profile_pts))

    sections = []

    prev_yaxis = None
    prev_zaxis = None

    for i in range(rail_samples):
        a = rail_a[i]
        b = rail_b[i]
        row = []

        direction = (b - a)
        if direction.length < 1e-8:
            direction = Vector((1, 0, 0))

        xaxis = direction.normalized()

        # Use rail tangent to stabilise the profile frame.
        if i == 0:
            rail_tangent = rail_a[1] - rail_a[0]
        elif i == rail_samples - 1:
            rail_tangent = rail_a[-1] - rail_a[-2]
        else:
            rail_tangent = rail_a[i + 1] - rail_a[i - 1]

        if rail_tangent.length < 1e-8:
            rail_tangent = Vector((0, 0, 1))

        zaxis = xaxis.cross(rail_tangent).normalized()
        if zaxis.length < 1e-8:
            up = Vector((0, 0, 1))
            if abs(xaxis.dot(up)) > 0.95:
                up = Vector((0, 1, 0))
            zaxis = xaxis.cross(up).normalized()

        yaxis = zaxis.cross(xaxis).normalized()

        # Prevent sudden frame flips along the rail.
        if prev_yaxis is not None and yaxis.dot(prev_yaxis) < 0:
            yaxis.negate()
            zaxis.negate()

        prev_yaxis = yaxis.copy()
        prev_zaxis = zaxis.copy()

        for p in profile_pts:
            param_val = p.y if use_y else p.x
            t = (param_val - min_val) / span
            t = max(0.0, min(1.0, t))

            q = a.lerp(b, t)

            # Use remaining local profile dimension as offset.
            offset_y = p.x if use_y else p.y
            offset_z = p.z
            q = q + yaxis * offset_y + zaxis * offset_z

            row.append(q)

        sections.append(row)

    verts, faces = hippo_build_grid_surface_from_sections(sections, closed_u=False, closed_v=False)

    # Check face orientation against average section normal. Flip winding if inverted.
    try:
        if faces:
            import mathutils
            v0 = Vector(verts[faces[0][0]])
            v1 = Vector(verts[faces[0][1]])
            v2 = Vector(verts[faces[0][2]])
            face_normal = (v1 - v0).cross(v2 - v0).normalized()

            tangent_a = rail_a[-1] - rail_a[0]
            across = rail_b[0] - rail_a[0]
            expected = tangent_a.cross(across)

            if expected.length > 1e-8 and face_normal.dot(expected.normalized()) < 0:
                faces = [(f[0], f[3], f[2], f[1]) for f in faces]
    except Exception:
        pass

    obj = make_mesh_object(
        context,
        "Hippo3D_Sweep2_Surface",
        verts,
        faces,
        {
            "hippo_surface_type": "sweep2",
            "cad_surface_type": "sweep2",
            "hippo_backend": hippo_native_backend_label(),
            "hippo_rail_a": rails[0].name,
            "hippo_rail_b": rails[1].name,
            "hippo_profile": profile.name,
        },
    )

    return True, "Created Sweep2 surface."


def create_edge_srf(context):
    """Create Hippo3D_EdgeSurface from selected boundary curves.

    This version avoids bow-tie/crossed faces by:
    1. sampling each selected edge,
    2. ordering the edges into a continuous perimeter loop,
    3. orienting each edge so the end of one connects to the start of the next,
    4. creating one mesh face from the ordered boundary vertices.

    For 2 curves, it still creates a simple loft-like surface between them.
    For 3 or 4 curves, it behaves like a boundary Hippo3D_EdgeSurface / filled perimeter.
    """
    curves = [obj for obj in context.selected_objects if obj.type == "CURVE"]

    if len(curves) not in {2, 3, 4}:
        return False, "Hippo3D_EdgeSurface: select 2, 3, or 4 boundary curves."

    samples = int(getattr(context.scene, "cad_surface_samples", 32))
    samples = max(2, samples)

    sampled = []
    for obj in curves:
        pts = sample_curve_object_points(obj, samples=samples)
        if len(pts) < 2:
            return False, f"Hippo3D_EdgeSurface: curve '{obj.name}' has insufficient points."
        sampled.append({
            "obj": obj,
            "pts": pts,
            "start": pts[0],
            "end": pts[-1],
        })

    # 2-edge case: keep a loft-style ruled surface.
    if len(sampled) == 2:
        a = sampled[0]["pts"]
        b = sampled[1]["pts"]

        # Align second edge direction to first edge.
        same = (a[0] - b[0]).length + (a[-1] - b[-1]).length
        flip = (a[0] - b[-1]).length + (a[-1] - b[0]).length
        if flip < same:
            b = list(reversed(b))

        verts = []
        for p in a:
            verts.append((p.x, p.y, p.z))
        for p in b:
            verts.append((p.x, p.y, p.z))

        faces = []
        n = min(len(a), len(b))
        for i in range(n - 1):
            faces.append((i, i + 1, n + i + 1, n + i))

        mesh = bpy.data.meshes.new("Hippo3D_EdgeSurface_Mesh")
        mesh.from_pydata(verts, [], faces)
        mesh.update()

        obj = bpy.data.objects.new("Hippo3D_EdgeSurface", mesh)
        context.collection.objects.link(obj)
        obj["hippo_surface_type"] = "edgesrf"
        obj["cad_surface_type"] = "edgesrf"
        obj["hippo_edgesrf_method"] = "two_edge_ruled"

        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        context.view_layer.objects.active = obj

        return True, "Created Hippo3D_EdgeSurface from 2 curves."

    # 3/4-edge case: build a continuous boundary loop.
    remaining = sampled[:]

    # Start with the left-most/lower-most edge start for more stable ordering.
    start_i = min(
        range(len(remaining)),
        key=lambda i: (
            min(remaining[i]["start"].x, remaining[i]["end"].x),
            min(remaining[i]["start"].y, remaining[i]["end"].y),
            min(remaining[i]["start"].z, remaining[i]["end"].z),
        )
    )

    first = remaining.pop(start_i)
    loop_edges = [first["pts"]]

    while remaining:
        current_end = loop_edges[-1][-1]

        best_i = None
        best_reverse = False
        best_dist = 1e30

        for i, item in enumerate(remaining):
            d_start = (current_end - item["start"]).length
            d_end = (current_end - item["end"]).length

            if d_start < best_dist:
                best_dist = d_start
                best_i = i
                best_reverse = False

            if d_end < best_dist:
                best_dist = d_end
                best_i = i
                best_reverse = True

        item = remaining.pop(best_i)
        pts = list(item["pts"])

        if best_reverse:
            pts = list(reversed(pts))

        loop_edges.append(pts)

    # Join edge point lists, removing duplicate connection vertices.
    boundary = []
    for edge_i, pts in enumerate(loop_edges):
        if edge_i == 0:
            boundary.extend(pts)
        else:
            if boundary and (boundary[-1] - pts[0]).length < 1e-5:
                boundary.extend(pts[1:])
            else:
                boundary.extend(pts)

    # Close loop if last point is same as first; do not duplicate it for face.
    if len(boundary) >= 2 and (boundary[0] - boundary[-1]).length < 1e-5:
        boundary = boundary[:-1]

    # Remove accidental duplicate consecutive points.
    clean = []
    for p in boundary:
        if not clean or (p - clean[-1]).length > 1e-6:
            clean.append(p)

    boundary = clean

    if len(boundary) < 3:
        return False, "Hippo3D_EdgeSurface: could not build a valid boundary loop."

    # Ensure face winding is not a bow-tie by sorting only if the ordered chain
    # failed to close plausibly. For normal joined edges, the chain order is used.
    close_gap = (boundary[0] - boundary[-1]).length
    avg_seg = sum((b - a).length for a, b in zip(boundary[:-1], boundary[1:])) / max(1, len(boundary) - 1)

    if close_gap > max(avg_seg * 3.0, 1e-4):
        # Fallback: angular sort in active CPlane around centroid.
        import math as _math
        origin, u, v, nrm = get_cplane_axes(context)
        center = sum(boundary, Vector((0, 0, 0))) / len(boundary)

        def angle_key(p):
            q = p - center
            return _math.atan2(q.dot(v), q.dot(u))

        boundary = sorted(boundary, key=angle_key)

    verts = [(p.x, p.y, p.z) for p in boundary]
    edges = [(i, (i + 1) % len(verts)) for i in range(len(verts))]
    face = tuple(range(len(verts)))

    mesh = bpy.data.meshes.new("Hippo3D_EdgeSurface_Mesh")
    mesh.from_pydata(verts, edges, [face])
    mesh.update()

    obj = bpy.data.objects.new("Hippo3D_EdgeSurface", mesh)
    context.collection.objects.link(obj)

    obj["hippo_surface_type"] = "edgesrf"
    obj["cad_surface_type"] = "edgesrf"
    obj["hippo_edgesrf_method"] = "ordered_boundary_fill"
    obj["hippo_edges"] = "|".join(c.name for c in curves)

    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    context.view_layer.objects.active = obj

    return True, f"Created Hippo3D_EdgeSurface boundary face from {len(curves)} curves."

def create_planar_srf(context):
    """Create planar surface like Blender Edit Mode: select all boundary vertices and press F.

    Behaviour:
    - Select one or more closed curve objects.
    - Hippo3D reads the original curve vertices/control points.
    - Converts the boundary to a Mesh object.
    - Creates a single face using all boundary vertices, like Edit Mode > F.
    - Keeps explicit perimeter edges.
    """
    curves = [obj for obj in context.selected_objects if obj.type == "CURVE"]

    if not curves:
        return False, "Hippo3D_PlanarSurface: select at least one closed curve."

    created = 0

    for curve in curves:
        pts = []

        # Prefer original editable curve vertices so the face follows the same
        # vertex structure that Convert To Mesh / F would use.
        try:
            for spl in curve.data.splines:
                if spl.type in {"POLY", "NURBS"}:
                    pts = [curve.matrix_world @ Vector((p.co.x, p.co.y, p.co.z)) for p in spl.points]
                elif spl.type == "BEZIER":
                    pts = [curve.matrix_world @ p.co for p in spl.bezier_points]

                if pts:
                    break
        except Exception:
            pts = []

        # Fallback to sampled curve points if original vertices are unavailable.
        if not pts:
            pts = sample_curve_object_points(
                curve,
                samples=int(getattr(context.scene, "cad_surface_samples", 64)),
            )

        if len(pts) >= 2 and (pts[0] - pts[-1]).length < 1e-6:
            pts = pts[:-1]

        if len(pts) < 3:
            continue

        verts = [(p.x, p.y, p.z) for p in pts]
        edges = [(i, (i + 1) % len(verts)) for i in range(len(verts))]
        face = tuple(range(len(verts)))

        mesh = bpy.data.meshes.new("Hippo3D_PlanarSurface_Mesh")
        mesh.from_pydata(verts, edges, [face])
        mesh.update()

        obj = bpy.data.objects.new("Hippo3D_PlanarSurface", mesh)
        context.collection.objects.link(obj)

        obj["hippo_surface_type"] = "planarsrf"
        obj["cad_surface_type"] = "planarsrf"
        obj["hippo_source"] = curve.name
        obj["hippo_planarsrf_method"] = "mesh_fill_face_like_edit_mode_F"

        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        context.view_layer.objects.active = obj

        created += 1

    if created == 0:
        return False, "Hippo3D_PlanarSurface failed. Select a closed curve with at least 3 vertices."

    return True, f"Created {created} planar surface(s) using mesh face fill."



def hippo_command_not_ready(name):
    return False, f"{name} command registered as a Rhino-compatible alias, but implementation is scheduled for the next geometry pass."


def run_sweep2_command(context):
    return False, "Sweep2 is temporarily disabled."


def run_edgesrf_command(context):
    return create_edge_srf(context)


def run_planarsrf_command(context):
    return create_planar_srf(context)


def run_extrude_command(context):
    return create_extrude_surface_from_curves(context)


def run_pipe_command(context):
    return create_pipe_from_curves(context)


def run_revolve_command(context):
    return create_revolve_surface_from_curves(context)


def run_sweep1_command(context):
    return False, "Sweep1 is temporarily disabled."



class CAD_OT_LoftSurface(Operator):
    bl_idname = "cad.loft_surface"
    bl_label = "Loft"
    bl_description = "Create a loft surface from selected curves."

    def execute(self, context):
        ok, msg = run_loft_command(context)
        self.report({"INFO" if ok else "WARNING"}, msg)
        return {"FINISHED"}

class CAD_OT_LoftRealModifier(Operator):
    bl_idname = "cad.loft_real_modifier"
    bl_label = "Loft Modifier"
    bl_description = "Create a loft object with a real Geometry Nodes modifier in Blender's modifier stack."

    def execute(self, context):
        ok, msg = create_loft_with_real_modifier(context)
        self.report({"INFO" if ok else "WARNING"}, msg)
        return {"FINISHED"}





class HIPPO_OT_Hippo3D_EdgeSurface(Operator):
    bl_idname = "cad.edgesrf"
    bl_label = "Edge Surface"
    bl_description = "Create a surface from 2, 3, or 4 selected edge curves."

    def execute(self, context):
        ok, msg = run_edgesrf_command(context)
        self.report({"INFO" if ok else "WARNING"}, msg)
        return {"FINISHED"}


class HIPPO_OT_Hippo3D_PlanarSurface(Operator):
    bl_idname = "cad.planarsrf"
    bl_label = "Planar Surface"
    bl_description = "Create planar mesh surface(s) from selected closed planar curves."

    def execute(self, context):
        ok, msg = run_planarsrf_command(context)
        self.report({"INFO" if ok else "WARNING"}, msg)
        return {"FINISHED"}


class HIPPO_OT_NativeStatus(Operator):
    bl_idname = "cad.hippo_native_status"
    bl_label = "Native Backend Status"

    def execute(self, context):
        if HIPPO_NATIVE_SURFACE_AVAILABLE:
            self.report({"INFO"}, "Hippo3D native C surface backend loaded.")
        else:
            self.report({"WARNING"}, "Native backend not loaded. " + str(HIPPO_NATIVE_SURFACE_ERROR))
        return {"FINISHED"}


class CAD_OT_ExtrudeSurface(Operator):
    bl_idname = "cad.extrude_surface"
    bl_label = "Extrude Surface"
    bl_description = "Extrude selected curve(s) along active CPlane normal."

    def execute(self, context):
        ok, msg = run_extrude_command(context)
        self.report({"INFO" if ok else "WARNING"}, msg)
        return {"FINISHED"}


class CAD_OT_PipeSurface(Operator):
    bl_idname = "cad.pipe_surface"
    bl_label = "Pipe"
    bl_description = "Create pipe(s) from selected curve(s)."

    def execute(self, context):
        ok, msg = run_pipe_command(context)
        self.report({"INFO" if ok else "WARNING"}, msg)
        return {"FINISHED"}


class Hippo3D_OT_Hippo3D_Revolve(Operator):
    bl_idname = "cad.revolve_surface"
    bl_label = "Revolve"
    bl_description = "Revolve selected profile curve(s) around active CPlane Z axis."

    def execute(self, context):
        ok, msg = run_revolve_command(context)
        self.report({"INFO" if ok else "WARNING"}, msg)
        return {"FINISHED"}



class Hippo3D_OT_SetRevolveAxis(Operator):
    bl_idname = "cad.set_revolve_axis"
    bl_label = "Set Revolve Axis"
    bl_description = "Pick two points to define the Revolve axis."

    def execute(self, context):
        bpy.ops.cad.command("INVOKE_DEFAULT", initial_command="revolveaxis")
        return {"FINISHED"}

class Hippo3D_OT_ClearRevolveAxis(Operator):
    bl_idname = "cad.clear_revolve_axis"
    bl_label = "Clear Revolve Axis"

    def execute(self, context):
        ok, msg = clear_revolve_axis(context)
        self.report({"INFO" if ok else "WARNING"}, msg)
        return {"FINISHED"}




class HIPPO_OT_Polygon(Operator):
    bl_idname = "cad.polygon"
    bl_label = "Polygon"

    def execute(self, context):
        bpy.ops.cad.command("INVOKE_DEFAULT", initial_command="polygon")
        return {"FINISHED"}

class HIPPO_OT_Trim(Operator):
    bl_idname = "cad.trim"
    bl_label = "Trim"

    def execute(self, context):
        ok, msg = run_trim_command(context)
        self.report({"INFO" if ok else "WARNING"}, msg)
        return {"FINISHED"}




# -----------------------------------------------------------------------------
# NURBS preservation helpers
# -----------------------------------------------------------------------------

def make_nurbs_curve_from_points(
    context,
    points,
    name="Hippo3D_NURBS_Curve",
    degree=3,
    cyclic=False,
    resolution_u=24,
):
    """Create a NURBS curve from world-space points.

    This preserves the *curve type* as NURBS for commands such as Offset.
    It is not yet a full mathematical NURBS fitting algorithm, but it keeps
    the result smooth and editable as a NURBS object instead of collapsing to POLY.
    """
    if not points or len(points) < 2:
        return None

    degree = int(max(1, min(int(degree), max(1, len(points) - 1))))

    curve = bpy.data.curves.new(name, type="CURVE")
    curve.dimensions = "3D"
    curve.resolution_u = int(max(1, resolution_u))

    spl = curve.splines.new("NURBS")
    spl.points.add(len(points) - 1)

    for pnt, co in zip(spl.points, points):
        pnt.co = (co.x, co.y, co.z, 1.0)

    spl.order_u = degree + 1
    spl.use_endpoint_u = True
    spl.use_cyclic_u = bool(cyclic)

    obj = bpy.data.objects.new(name, curve)
    context.collection.objects.link(obj)

    obj["hippo_shape"] = "nurbs_curve"
    obj["hippo_degree"] = degree
    obj["hippo_preserved_curve_type"] = "NURBS"

    return obj


def hippo_source_curve_info(obj):
    """Return basic source spline metadata for preservation."""
    info = {
        "type": "UNKNOWN",
        "degree": 3,
        "cyclic": False,
        "resolution_u": 24,
    }

    if obj is None or obj.type != "CURVE":
        return info

    try:
        info["resolution_u"] = int(getattr(obj.data, "resolution_u", 24))
    except Exception:
        pass

    try:
        if obj.data.splines:
            spl = obj.data.splines[0]
            info["type"] = spl.type
            info["cyclic"] = bool(getattr(spl, "use_cyclic_u", False))

            if spl.type == "NURBS":
                info["degree"] = max(1, int(getattr(spl, "order_u", 4)) - 1)
            elif spl.type == "POLY":
                info["degree"] = 1
            else:
                info["degree"] = 3
    except Exception:
        pass

    return info


# -----------------------------------------------------------------------------
# NURBS-aware Offset Fix
# -----------------------------------------------------------------------------

def hippo_curve_points_for_offset(obj, context):
    """Return points for offset.

    - POLY curves: use original editable points to preserve segment count.
    - NURBS / BEZIER: use evaluated sampled points because offsetting control
      points does not offset the actual curve shape.
    """
    if obj is None or obj.type != "CURVE":
        return [], False, "UNKNOWN"

    # Detect first spline type.
    spline_type = "UNKNOWN"
    cyclic = False
    try:
        if obj.data.splines:
            spline_type = obj.data.splines[0].type
            cyclic = bool(getattr(obj.data.splines[0], "use_cyclic_u", False))
    except Exception:
        pass

    if spline_type == "POLY":
        pts = []
        try:
            spl = obj.data.splines[0]
            pts = [obj.matrix_world @ Vector((p.co.x, p.co.y, p.co.z)) for p in spl.points]
            cyclic = bool(getattr(spl, "use_cyclic_u", False))
        except Exception:
            pts = []

        if len(pts) >= 2 and (pts[0] - pts[-1]).length < 1e-6:
            pts = pts[:-1]
            cyclic = True

        return pts, cyclic, "POLY_ORIGINAL"

    # NURBS/Bezier/evaluated curves: sample actual displayed curve.
    samples = int(getattr(context.scene, "cad_surface_samples", 64))
    samples = max(samples, 64)

    pts = sample_curve_object_points(obj, samples=samples)

    if len(pts) >= 2 and (pts[0] - pts[-1]).length < 1e-6:
        pts = pts[:-1]
        cyclic = True

    return pts, cyclic, spline_type


def run_offset_command(context):
    """Offset selected curves while preserving curve representation where possible.

    - POLY source -> POLY offset, original point count preserved.
    - NURBS source -> NURBS offset, rebuilt as a NURBS spline with preserved degree.
    - BEZIER/other -> smooth NURBS approximation from sampled offset points.
    """
    objs = [obj for obj in context.selected_objects if obj.type == "CURVE"]

    if not objs:
        return False, "Offset: select curve objects."

    dist = float(getattr(context.scene, "hippo_offset_distance", 0.2))
    created = 0

    for obj in objs:
        source_info = hippo_source_curve_info(obj)
        pts, cyclic, source_type = hippo_curve_points_for_offset(obj, context)

        if len(pts) < 2:
            continue

        origin, u, v, n = get_cplane_axes(context)
        total = len(pts)
        result = []

        for i, p in enumerate(pts):
            if cyclic:
                p_prev = pts[(i - 1) % total]
                p_next = pts[(i + 1) % total]
            else:
                p_prev = pts[max(i - 1, 0)]
                p_next = pts[min(i + 1, total - 1)]

            tangent = p_next - p_prev

            if tangent.length < 1e-8:
                if i < total - 1:
                    tangent = pts[i + 1] - p
                elif i > 0:
                    tangent = p - pts[i - 1]

            if tangent.length < 1e-8:
                result.append(p.copy())
                continue

            tangent.normalize()
            perp = n.cross(tangent)

            if perp.length < 1e-8:
                result.append(p.copy())
                continue

            perp.normalize()
            result.append(p + perp * dist)

        if len(result) < 2:
            continue

        if source_info["type"] == "POLY":
            new_obj = make_poly_curve_from_points(
                context,
                result,
                name=f"{obj.name}_Offset",
                cyclic=cyclic,
            )
        else:
            # Preserve NURBS-like smooth editable result.
            new_obj = make_nurbs_curve_from_points(
                context,
                result,
                name=f"{obj.name}_Offset",
                degree=source_info.get("degree", 3),
                cyclic=cyclic,
                resolution_u=source_info.get("resolution_u", 24),
            )

        if new_obj:
            new_obj["hippo_command"] = "offset"
            new_obj["hippo_offset_distance"] = dist
            new_obj["hippo_source"] = obj.name
            new_obj["hippo_offset_source_type"] = source_info["type"]
            new_obj["hippo_offset_points"] = len(result)
            created += 1

    if created == 0:
        return False, "Offset failed."

    return True, f"Created {created} offset curve(s), preserving curve type where possible."




# -----------------------------------------------------------------------------
# Strong NURBS-aware Offset Override
# -----------------------------------------------------------------------------

def hippo_get_source_spline_info(obj):
    """Read the real Blender spline type/degree from the selected object."""
    info = {
        "type": "UNKNOWN",
        "degree": 3,
        "order": 4,
        "cyclic": False,
        "resolution_u": 24,
    }

    if obj is None or obj.type != "CURVE":
        return info

    try:
        info["resolution_u"] = int(getattr(obj.data, "resolution_u", 24))
    except Exception:
        pass

    try:
        if not obj.data.splines:
            return info

        spl = obj.data.splines[0]
        info["type"] = spl.type
        info["cyclic"] = bool(getattr(spl, "use_cyclic_u", False))

        if spl.type == "NURBS":
            order = int(getattr(spl, "order_u", 4))
            info["order"] = order
            info["degree"] = max(1, order - 1)
        elif spl.type == "POLY":
            info["degree"] = 1
            info["order"] = 2
        elif spl.type == "BEZIER":
            info["degree"] = 3
            info["order"] = 4

    except Exception:
        pass

    return info


def make_same_degree_nurbs_curve_from_points(context, points, source_info, name="Hippo3D_NURBS_Offset"):
    """Create a NURBS curve using the same degree/order as the source NURBS."""
    if not points or len(points) < 2:
        return None

    source_degree = int(source_info.get("degree", 3))
    degree = max(1, min(source_degree, len(points) - 1))
    order = degree + 1

    curve = bpy.data.curves.new(name, type="CURVE")
    curve.dimensions = "3D"
    curve.resolution_u = int(max(1, source_info.get("resolution_u", 24)))

    spl = curve.splines.new("NURBS")
    spl.points.add(len(points) - 1)

    for pnt, co in zip(spl.points, points):
        pnt.co = (co.x, co.y, co.z, 1.0)

    spl.order_u = order
    spl.use_endpoint_u = True
    spl.use_cyclic_u = bool(source_info.get("cyclic", False))

    obj = bpy.data.objects.new(name, curve)
    context.collection.objects.link(obj)

    obj["hippo_shape"] = "nurbs_curve"
    obj["hippo_preserved_curve_type"] = "NURBS"
    obj["hippo_source_degree"] = source_degree
    obj["hippo_result_degree"] = degree
    obj["hippo_result_order_u"] = order

    return obj


def hippo_points_for_offset_by_source_type(obj, context, source_info):
    """Return offset input points based on real source curve type.

    POLY: original editable vertices, preserving segment count.
    NURBS: evaluated curve samples, because control-point offset is geometrically wrong.
           Result is rebuilt as NURBS with the same degree.
    """
    cyclic = bool(source_info.get("cyclic", False))

    if source_info.get("type") == "POLY":
        try:
            spl = obj.data.splines[0]
            pts = [obj.matrix_world @ Vector((p.co.x, p.co.y, p.co.z)) for p in spl.points]
            if len(pts) >= 2 and (pts[0] - pts[-1]).length < 1e-6:
                pts = pts[:-1]
                cyclic = True
            return pts, cyclic
        except Exception:
            return [], cyclic

    # For NURBS and Bezier, sample the displayed/evaluated curve.
    samples = int(getattr(context.scene, "cad_surface_samples", 64))
    samples = max(samples, 64)

    pts = sample_curve_object_points(obj, samples=samples)

    if len(pts) >= 2 and (pts[0] - pts[-1]).length < 1e-6:
        pts = pts[:-1]
        cyclic = True

    return pts, cyclic


def run_offset_command(context):
    """Offset selected curves with explicit NURBS preservation.

    If source is NURBS:
    - detect source as NURBS from obj.data.splines[0].type
    - read degree from order_u - 1
    - offset sampled evaluated curve points
    - rebuild the result as NURBS with the same degree/order
    """
    objs = [obj for obj in context.selected_objects if obj.type == "CURVE"]

    if not objs:
        return False, "Offset: select curve objects."

    dist = float(getattr(context.scene, "hippo_offset_distance", 0.2))
    created = 0

    for obj in objs:
        source_info = hippo_get_source_spline_info(obj)
        pts, cyclic = hippo_points_for_offset_by_source_type(obj, context, source_info)

        if len(pts) < 2:
            continue

        origin, u, v, n = get_cplane_axes(context)
        total = len(pts)
        result = []

        for i, p in enumerate(pts):
            if cyclic:
                p_prev = pts[(i - 1) % total]
                p_next = pts[(i + 1) % total]
            else:
                p_prev = pts[max(i - 1, 0)]
                p_next = pts[min(i + 1, total - 1)]

            tangent = p_next - p_prev

            if tangent.length < 1e-8:
                if i < total - 1:
                    tangent = pts[i + 1] - p
                elif i > 0:
                    tangent = p - pts[i - 1]

            if tangent.length < 1e-8:
                result.append(p.copy())
                continue

            tangent.normalize()
            perp = n.cross(tangent)

            if perp.length < 1e-8:
                result.append(p.copy())
                continue

            perp.normalize()
            result.append(p + perp * dist)

        if len(result) < 2:
            continue

        if source_info.get("type") == "NURBS":
            new_obj = make_same_degree_nurbs_curve_from_points(
                context,
                result,
                source_info,
                name=f"{obj.name}_NURBS_Offset",
            )
        elif source_info.get("type") == "POLY":
            new_obj = make_poly_curve_from_points(
                context,
                result,
                name=f"{obj.name}_Offset",
                cyclic=cyclic,
            )
        else:
            # Bezier/unknown: keep smooth editable curve as degree-3 NURBS approximation.
            approx_info = dict(source_info)
            approx_info["type"] = "NURBS"
            approx_info["degree"] = 3
            approx_info["order"] = 4
            new_obj = make_same_degree_nurbs_curve_from_points(
                context,
                result,
                approx_info,
                name=f"{obj.name}_Offset",
            )

        if new_obj:
            new_obj["hippo_command"] = "offset"
            new_obj["hippo_offset_distance"] = dist
            new_obj["hippo_source"] = obj.name
            new_obj["hippo_source_curve_type"] = source_info.get("type", "UNKNOWN")
            new_obj["hippo_source_degree"] = int(source_info.get("degree", 3))
            created += 1

    if created == 0:
        return False, "Offset failed."

    return True, f"Created {created} offset curve(s)."



class HIPPO_OT_Offset(Operator):
    bl_idname = "cad.offset"
    bl_label = "Offset"
    def execute(self, context):
        ok, msg = run_offset_command(context)
        self.report({"INFO" if ok else "WARNING"}, msg)
        return {"FINISHED"}

class HIPPO_OT_XLine(Operator):
    bl_idname = "cad.xline"
    bl_label = "XLine"
    def execute(self, context):
        bpy.ops.cad.command("INVOKE_DEFAULT", initial_command="xline")
        return {"FINISHED"}

class HIPPO_OT_Explode(Operator):
    bl_idname = "cad.explode"
    bl_label = "Explode"
    def execute(self, context):
        ok, msg = run_explode_command(context)
        self.report({"INFO" if ok else "WARNING"}, msg)
        return {"FINISHED"}


# -----------------------------------------------------------------------------
# Array command helper fix
# -----------------------------------------------------------------------------

def run_array_command(context):
    """Create a simple linear array of selected objects.

    Uses:
    - hippo_array_count
    - hippo_array_dx
    - hippo_array_dy
    - hippo_array_dz
    """
    objs = list(context.selected_objects)

    if not objs:
        return False, "Array: select one or more objects."

    count = int(getattr(context.scene, "hippo_array_count", 5))
    dx = float(getattr(context.scene, "hippo_array_dx", 2.0))
    dy = float(getattr(context.scene, "hippo_array_dy", 0.0))
    dz = float(getattr(context.scene, "hippo_array_dz", 0.0))

    count = max(1, count)
    offset = Vector((dx, dy, dz))

    created = 0

    for obj in objs:
        for i in range(1, count):
            dup = obj.copy()

            if getattr(obj, "data", None) is not None:
                try:
                    dup.data = obj.data.copy()
                except Exception:
                    dup.data = obj.data

            dup.location = obj.location + offset * i
            dup.name = obj.name + f"_Array_{i:03d}"
            context.collection.objects.link(dup)
            created += 1

    return True, f"Array created {created} copied object(s)."


class HIPPO_OT_Array(Operator):
    bl_idname = "cad.array"
    bl_label = "Array"
    def execute(self, context):
        ok, msg = run_array_command(context)
        self.report({"INFO" if ok else "WARNING"}, msg)
        return {"FINISHED"}

class HIPPO_OT_Project(Operator):
    bl_idname = "cad.project"
    bl_label = "Project"
    def execute(self, context):
        ok, msg = run_project_command(context)
        self.report({"INFO" if ok else "WARNING"}, msg)
        return {"FINISHED"}

class HIPPO_OT_Ellipse(Operator):
    bl_idname = "cad.ellipse"
    bl_label = "Ellipse"
    def execute(self, context):
        bpy.ops.cad.command("INVOKE_DEFAULT", initial_command="ellipse")
        return {"FINISHED"}

class HIPPO_OT_StartArc(Operator):
    bl_idname = "cad.start_arc"
    bl_label = "Arc"
    def execute(self, context):
        bpy.ops.cad.command("INVOKE_DEFAULT", initial_command="arc")
        return {"FINISHED"}



# -----------------------------------------------------------------------------
# Corrected Polygon 2Pt + Segment-Preserving Offset
# -----------------------------------------------------------------------------

def make_poly_curve_from_points(context, points, name="Hippo3D_Curve", cyclic=False):
    if not points or len(points) < 2:
        return None

    curve = bpy.data.curves.new(name, type="CURVE")
    curve.dimensions = "3D"
    curve.resolution_u = 12

    spl = curve.splines.new("POLY")
    spl.points.add(len(points) - 1)

    for pnt, co in zip(spl.points, points):
        pnt.co = (co.x, co.y, co.z, 1.0)

    spl.use_cyclic_u = bool(cyclic)

    obj = bpy.data.objects.new(name, curve)
    context.collection.objects.link(obj)
    obj["hippo_shape"] = "curve"

    return obj


def hippo_original_curve_points(obj):
    if obj is None or obj.type != "CURVE":
        return [], False

    for spl in obj.data.splines:
        pts = []
        cyclic = bool(getattr(spl, "use_cyclic_u", False))

        if spl.type in {"POLY", "NURBS"}:
            pts = [obj.matrix_world @ Vector((p.co.x, p.co.y, p.co.z)) for p in spl.points]
        elif spl.type == "BEZIER":
            pts = [obj.matrix_world @ p.co for p in spl.bezier_points]

        if pts:
            if len(pts) >= 2 and (pts[0] - pts[-1]).length < 1e-6:
                pts = pts[:-1]
                cyclic = True
            return pts, cyclic

    return [], False


def run_offset_command(context):
    """Offset selected curves while preserving the original editable point count."""
    objs = list(context.selected_objects)
    if not objs:
        return False, "Offset: select curve objects."

    dist = float(getattr(context.scene, "hippo_offset_distance", 0.2))
    created = 0

    for obj in objs:
        if obj.type != "CURVE":
            continue

        pts, cyclic = hippo_original_curve_points(obj)

        if len(pts) < 2:
            continue

        origin, u, v, n = get_cplane_axes(context)
        total = len(pts)
        result = []

        for i, p in enumerate(pts):
            if cyclic:
                p_prev = pts[(i - 1) % total]
                p_next = pts[(i + 1) % total]
            else:
                p_prev = pts[max(i - 1, 0)]
                p_next = pts[min(i + 1, total - 1)]

            tangent = p_next - p_prev

            if tangent.length < 1e-8:
                if i < total - 1:
                    tangent = pts[i + 1] - p
                elif i > 0:
                    tangent = p - pts[i - 1]

            if tangent.length < 1e-8:
                result.append(p.copy())
                continue

            tangent.normalize()
            perp = n.cross(tangent)

            if perp.length < 1e-8:
                result.append(p.copy())
                continue

            perp.normalize()
            result.append(p + perp * dist)

        if len(result) < 2:
            continue

        new_obj = make_poly_curve_from_points(
            context,
            result,
            name=f"{obj.name}_Offset",
            cyclic=cyclic,
        )

        if new_obj:
            new_obj["hippo_command"] = "offset"
            new_obj["hippo_offset_distance"] = dist
            new_obj["hippo_source"] = obj.name
            new_obj["hippo_preserved_point_count"] = len(result)
            created += 1

    if created == 0:
        return False, "Offset failed."

    return True, f"Created {created} offset curve(s), preserving source segment count."


def create_polygon_curve(context, center=None, radius=None, sides=None):
    import math as _math

    sides = int(sides if sides is not None else getattr(context.scene, "hippo_polygon_sides", 6))
    radius = float(radius if radius is not None else getattr(context.scene, "hippo_polygon_radius", 2.0))

    sides = max(3, min(sides, 256))
    radius = max(0.001, radius)

    origin, u, v, n = get_cplane_axes(context)
    center = center or origin

    pts = []
    for i in range(sides):
        a = 2.0 * _math.pi * i / sides
        pts.append(center + u * (_math.cos(a) * radius) + v * (_math.sin(a) * radius))

    obj = make_poly_curve_from_points(context, pts, name="Hippo3D_Polygon", cyclic=True)

    if obj:
        obj["hippo_shape"] = "polygon"
        obj["hippo_polygon_sides"] = sides
        obj["hippo_polygon_radius"] = radius

    return obj


def create_polygon_from_2_points(context, p0, p1):
    radius = (p1 - p0).length

    if radius < 1e-8:
        return None

    return create_polygon_curve(context, center=p0, radius=radius)


def run_polygon_command(context):
    return False, "Polygon is interactive. Type Polygon, then pick centre point and radius point."




# -----------------------------------------------------------------------------
# Trim + Fillet first-pass implementations
# -----------------------------------------------------------------------------

def hippo_curve_polyline_points(obj):
    """Return original curve points, preserving simple segment structure."""
    if obj is None or obj.type != "CURVE":
        return [], False

    if "hippo_original_curve_points" in globals():
        try:
            return hippo_original_curve_points(obj)
        except Exception:
            pass

    for spl in obj.data.splines:
        cyclic = bool(getattr(spl, "use_cyclic_u", False))

        if spl.type in {"POLY", "NURBS"}:
            pts = [obj.matrix_world @ Vector((p.co.x, p.co.y, p.co.z)) for p in spl.points]
        elif spl.type == "BEZIER":
            pts = [obj.matrix_world @ p.co for p in spl.bezier_points]
        else:
            pts = []

        if pts:
            if len(pts) >= 2 and (pts[0] - pts[-1]).length < 1e-6:
                pts = pts[:-1]
                cyclic = True
            return pts, cyclic

    return [], False


def hippo_segment_intersection_2d(a, b, c, d, origin, u, v, tol=1e-8):
    """Return intersection point and params for two 3D segments projected to CPlane."""
    def to2(p):
        q = p - origin
        return Vector((q.dot(u), q.dot(v)))

    a2, b2, c2, d2 = to2(a), to2(b), to2(c), to2(d)
    r = b2 - a2
    s = d2 - c2

    denom = r.x * s.y - r.y * s.x
    if abs(denom) < tol:
        return None

    q = c2 - a2
    t = (q.x * s.y - q.y * s.x) / denom
    w = (q.x * r.y - q.y * r.x) / denom

    if -tol <= t <= 1.0 + tol and -tol <= w <= 1.0 + tol:
        p = a.lerp(b, max(0.0, min(1.0, t)))
        return p, t, w

    return None


def run_trim_command(context):
    """First-pass Trim.

    Behaviour:
    - Select 2+ curve objects.
    - Finds curve/curve intersections in active CPlane.
    - Splits curves at intersections.
    - Keeps the longest resulting piece per original curve.
    This is not final Rhino Trim UX yet, but it performs a useful trim-like split/keep operation.
    """
    curves = [obj for obj in context.selected_objects if obj.type == "CURVE"]

    if len(curves) < 2:
        return False, "Trim: select at least two intersecting curves."

    origin, u, v, n = get_cplane_axes(context)
    tol = float(getattr(context.scene, "hippo_trim_tolerance", 0.05))

    created = 0
    removed = []

    for obj in curves:
        pts, cyclic = hippo_curve_polyline_points(obj)
        if len(pts) < 2:
            continue

        split_items = [(0, 0.0, pts[0]), (len(pts) - 2, 1.0, pts[-1])]

        segments = list(zip(pts[:-1], pts[1:]))

        for other in curves:
            if other == obj:
                continue

            other_pts, other_cyclic = hippo_curve_polyline_points(other)
            if len(other_pts) < 2:
                continue

            other_segments = list(zip(other_pts[:-1], other_pts[1:]))

            for i, (a, b) in enumerate(segments):
                for c, d in other_segments:
                    hit = hippo_segment_intersection_2d(a, b, c, d, origin, u, v, tol=1e-8)
                    if hit:
                        p, t, w = hit
                        if tol < (p - a).length and tol < (p - b).length:
                            split_items.append((i, t, p))

        if len(split_items) <= 2:
            continue

        # Sort by segment index + segment t.
        split_items.sort(key=lambda x: (x[0], x[1]))

        # Build split polylines.
        pieces = []
        current = [split_items[0][2]]

        for idx in range(1, len(split_items)):
            prev_seg, prev_t, prev_p = split_items[idx - 1]
            seg_i, seg_t, p = split_items[idx]

            current = [prev_p]

            # Add original internal vertices between split points.
            start_i = prev_seg + 1
            end_i = seg_i + 1
            for vi in range(start_i, end_i):
                if 0 <= vi < len(pts):
                    current.append(pts[vi])

            current.append(p)

            # avoid tiny pieces
            length = sum((b - a).length for a, b in zip(current[:-1], current[1:]))
            if len(current) >= 2 and length > tol:
                pieces.append((length, current))

        if not pieces:
            continue

        # Keep the longest piece for first-pass trim.
        pieces.sort(key=lambda x: x[0], reverse=True)
        keep = pieces[0][1]

        new_obj = make_poly_curve_from_points(context, keep, name=obj.name + "_Trimmed", cyclic=False)
        if new_obj:
            new_obj["hippo_command"] = "trim"
            new_obj["hippo_source"] = obj.name
            created += 1
            removed.append(obj)

    for obj in removed:
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
        except Exception:
            pass

    if created == 0:
        return False, "Trim found no usable intersections."

    return True, f"Trim created {created} trimmed curve(s)."


def hippo_nearest_curve_ends(obj_a, obj_b):
    pts_a, _ = hippo_curve_polyline_points(obj_a)
    pts_b, _ = hippo_curve_polyline_points(obj_b)

    if len(pts_a) < 2 or len(pts_b) < 2:
        return None

    candidates = [
        (pts_a[0], pts_a[1], "a_start", pts_b[0], pts_b[1], "b_start"),
        (pts_a[0], pts_a[1], "a_start", pts_b[-1], pts_b[-2], "b_end"),
        (pts_a[-1], pts_a[-2], "a_end", pts_b[0], pts_b[1], "b_start"),
        (pts_a[-1], pts_a[-2], "a_end", pts_b[-1], pts_b[-2], "b_end")]

    best = None
    for a_end, a_next, a_tag, b_end, b_next, b_tag in candidates:
        dist = (a_end - b_end).length
        if best is None or dist < best[0]:
            best = (dist, a_end, a_next, a_tag, b_end, b_next, b_tag)

    return best


def create_arc_polyline_from_center(context, center, radius, start_vec, end_vec, segments=24):
    import math as _math

    origin, u, v, n = get_cplane_axes(context)

    def angle_of(vec):
        return _math.atan2(vec.dot(v), vec.dot(u))

    a0 = angle_of(start_vec)
    a1 = angle_of(end_vec)

    # choose shorter angular path
    da = a1 - a0
    while da > _math.pi:
        da -= 2 * _math.pi
    while da < -_math.pi:
        da += 2 * _math.pi

    pts = []
    for i in range(segments + 1):
        t = i / segments
        a = a0 + da * t
        pts.append(center + u * (_math.cos(a) * radius) + v * (_math.sin(a) * radius))

    return make_poly_curve_from_points(context, pts, name="Hippo3D_Fillet", cyclic=False)


def run_fillet_command(context):
    """First-pass Fillet for two selected curve ends.

    Select two line/polyline curves. Hippo3D finds the closest pair of ends and
    creates a radius arc tangent approximation between them.
    """
    curves = [obj for obj in context.selected_objects if obj.type == "CURVE"]

    if len(curves) != 2:
        return False, "Fillet: select exactly two curve objects."

    radius = float(getattr(context.scene, "hippo_fillet_radius", 1.0))
    if radius <= 0:
        return False, "Fillet radius must be positive."

    best = hippo_nearest_curve_ends(curves[0], curves[1])
    if best is None:
        return False, "Fillet: could not read selected curve ends."

    _, a_end, a_next, a_tag, b_end, b_next, b_tag = best

    dir_a = (a_next - a_end)
    dir_b = (b_next - b_end)

    if dir_a.length < 1e-8 or dir_b.length < 1e-8:
        return False, "Fillet: curve end direction is invalid."

    dir_a.normalize()
    dir_b.normalize()

    # Tangency points measured away from closest endpoints.
    tan_a = a_end + dir_a * radius
    tan_b = b_end + dir_b * radius

    # Approximate center as midpoint offset from chord.
    chord = tan_b - tan_a
    if chord.length < 1e-8:
        return False, "Fillet: selected curve ends are too close."

    center = (tan_a + tan_b) * 0.5

    # Use average distance from center as arc radius.
    arc_radius = max((tan_a - center).length, (tan_b - center).length)
    if arc_radius < 1e-8:
        return False, "Fillet radius too small."

    obj = create_arc_polyline_from_center(
        context,
        center,
        arc_radius,
        tan_a - center,
        tan_b - center,
        segments=24,
    )

    if not obj:
        return False, "Fillet failed."

    obj["hippo_command"] = "fillet"
    obj["hippo_fillet_radius"] = radius
    obj["hippo_sources"] = curves[0].name + "|" + curves[1].name

    return True, "Created fillet arc."



# -----------------------------------------------------------------------------
# Ellipse 2Pt helper fix
# -----------------------------------------------------------------------------

def create_ellipse_curve(context, center=None, rx=None, ry=None, segments=96, axis_dir=None):
    import math as _math

    origin, u, v, n = get_cplane_axes(context)
    center = center or origin

    rx = float(rx if rx is not None else getattr(context.scene, "hippo_ellipse_rx", 2.0))
    ry = float(ry if ry is not None else getattr(context.scene, "hippo_ellipse_ry", 1.0))

    if axis_dir is not None and axis_dir.length > 1e-8:
        eu = axis_dir.normalized()
        ev = n.cross(eu)

        if ev.length < 1e-8:
            ev = v.copy()
        else:
            ev.normalize()
    else:
        eu = u
        ev = v

    pts = []
    for i in range(segments):
        a = 2.0 * _math.pi * i / segments
        pts.append(center + eu * (_math.cos(a) * rx) + ev * (_math.sin(a) * ry))

    obj = make_poly_curve_from_points(context, pts, name="Hippo3D_Ellipse", cyclic=True)
    if obj:
        obj["hippo_shape"] = "ellipse"
        obj["hippo_ellipse_rx"] = rx
        obj["hippo_ellipse_ry"] = ry

    return obj


def create_ellipse_from_2_points(context, p0, p1):
    axis = p1 - p0
    rx = axis.length

    if rx < 1e-8:
        return None

    # Secondary radius from UI. If not set, use half of primary radius.
    ry = float(getattr(context.scene, "hippo_ellipse_ry", rx * 0.5))

    return create_ellipse_curve(
        context,
        center=p0,
        rx=rx,
        ry=ry,
        axis_dir=axis,
    )


class Hippo3D_PT_MainPanel(Panel):
    bl_label = "Hippo3D"
    bl_idname = "Hippo3D_PT_main_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Hippo3D"

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        col.label(text="Commands")
        col.operator("cad.start_command", text="Hippo Command Line  Ctrl+/", icon="CONSOLE")
        col.separator()
        col.label(text="Curve Creation")
        col.operator("cad.start_line", text="Line", icon="CURVE_PATH")
        col.operator("cad.start_polyline", text="Polyline", icon="IPO_LINEAR")
        col.operator("cad.start_rectangle", text="Rectangle", icon="MESH_PLANE")
        col.operator("cad.start_circle", text="Circle", icon="MESH_CIRCLE")
        col.operator("cad.start_arc", text="Arc")
        col.prop(context.scene, "hippo_ellipse_ry", text="Ellipse Secondary Radius")
        col.operator("cad.ellipse", text="Ellipse")
        col.prop(context.scene, "hippo_polygon_sides", text="Polygon Sides")
        col.operator("cad.polygon", text="Polygon")
        col.operator("cad.start_nurbs", text="NURBS Curve", icon="CURVE_BEZCURVE")
        col.prop(context.scene, "hippo_xline_length", text="XLine Length")
        col.operator("cad.xline", text="XLine")

        col.separator()
        col.label(text="Curve Modification")
        col.prop(context.scene, "hippo_offset_distance", text="Offset Distance")
        col.operator("cad.offset", text="Offset")
        col.prop(context.scene, "hippo_trim_tolerance", text="Trim Tolerance")
        col.operator("cad.trim", text="Trim")
        col.operator("cad.explode", text="Explode")
        col.operator("cad.project", text="Project")
        col.prop(context.scene, "cad_nurbs_degree", text="NURBS Degree")
        col.prop(context.scene, "cad_selected_nurbs_degree", text="Selected Degree")
        col.operator("cad.set_selected_nurbs_degree", text="Set Selected Degree")
        col.operator("cad.convert_to_mesh", text="Convert to Mesh", icon="MESH_DATA")
        col.operator("cad.join", text="Join", icon="AUTOMERGE_OFF")

        col.separator()
        col.label(text="Object Tools")
        col.prop(context.scene, "hippo_array_count", text="Array Count")
        col.prop(context.scene, "hippo_array_dx", text="Array X")
        col.prop(context.scene, "hippo_array_dy", text="Array Y")
        col.prop(context.scene, "hippo_array_dz", text="Array Z")
        col.operator("cad.array", text="Array")
        col.label(text="Surface-Like Operations")
        col.prop(context.scene, "cad_loft_samples", text="Loft Samples")
        col.operator("cad.loft_surface", text="Loft Surface", icon="SURFACE_DATA")
        col.separator()
        col.prop(context.scene, "cad_surface_samples", text="Samples")
        col.prop(context.scene, "cad_extrude_distance", text="Extrude Distance")
        col.operator("cad.extrude_surface", text="Extrude", icon="MOD_SOLIDIFY")
        col.prop(context.scene, "cad_pipe_radius", text="Pipe Radius")
        col.prop(context.scene, "cad_pipe_resolution", text="Pipe Resolution")
        col.operator("cad.pipe_surface", text="Pipe", icon="CURVE_DATA")
        col.prop(context.scene, "cad_revolve_angle", text="Revolve Degree", slider=True)
        col.prop(context.scene, "cad_revolve_steps", text="Revolve Steps")
        col.operator("cad.set_revolve_axis", text="Set Revolve Axis")
        col.operator("cad.clear_revolve_axis", text="Clear Revolve Axis")
        col.operator("cad.revolve_surface", text="Revolve", icon="MOD_SCREW")
        col.operator("cad.edgesrf", text="Edge Surface", icon="SURFACE_DATA")
        col.operator("cad.planarsrf", text="Planar Surface", icon="MESH_PLANE")
        col.operator("cad.hippo_native_status", text="Native C Backend Status")


        layout.separator()
        box = layout.box()
        box.label(text="CPlanes")

        sync_cplane_dropdown(context)
        box.prop(context.scene, "cad_active_cplane_dropdown", text="Active")

        box.prop(context.scene, "cad_cplane_save_name", text="Name")
        row = box.row(align=True)
        row.operator("cad.save_cplane", text="Save Current")
        row.operator("cad.restore_cplane", text="Restore by Name")
        box.operator("cad.start_cplane_3pt", text="Create 3-Point CPlane")
        box.operator("cad.start_cplane_xaxis", text="Create X-Axis CPlane")
        box.operator("cad.start_cplane_zaxis", text="Create Z-Axis CPlane")
        box.operator("cad.start_cplane_face", text="Create Face CPlane")
        box.operator("cad.start_cplane_curve_perp", text="Create Perp Curve CPlane")

        box.separator()
        box.prop(context.scene, "cad_cplane_rotate_angle", text="Rotate Angle")
        row = box.row(align=True)
        op = row.operator("cad.rotate_cplane", text="Rot X")
        op.axis = "X"
        op = row.operator("cad.rotate_cplane", text="Rot Y")
        op.axis = "Y"
        op = row.operator("cad.rotate_cplane", text="Rot Z")
        op.axis = "Z"
        box.operator("cad.start_cplane_rotate3pt", text="Rotate by 3 Points")
        box.operator("cad.start_cplane_axisrotate", text="Axis Rotate + Slider")
        box.operator("cad.start_cplane_move", text="Move CPlane")

        box.separator()
        box.operator("cad.view_to_cplane", text="View to CPlane")
        box.prop(context.scene, "cad_cplane_camera_distance", text="Camera Distance")
        box.operator("cad.camera_to_cplane", text="Camera to CPlane")
        box.prop(context.scene, "cad_cplane_axis_rotation_angle", text="Axis Angle", slider=True)
        box.operator("cad.apply_cplane_axis_rotation", text="Apply Axis Angle")

        box.separator()
        box.label(text="CPlane Layers")

        if hasattr(context.scene, "cad_cplane_items") and hasattr(context.scene, "cad_cplane_index"):
            box.template_list(
                "Hippo3D_UL_cplane_list",
                "",
                context.scene,
                "cad_cplane_items",
                context.scene,
                "cad_cplane_index",
                rows=7,
            )
        else:
            box.label(text="CPlane list not registered")

        row = box.row(align=True)
        row.operator("cad.delete_selected_cplane", text="Delete Selected", icon="TRASH")

        box.label(text="Cmd: cplane 3pt/save/restore/list/delete")
        box.label(text="Relative input: @x,y,z")

        layout.separator()
        box = layout.box()
        box.label(text="Osnaps")
        row = box.row(align=True)
        row.prop(context.scene, "cad_osnap_endpoint", text="End")
        row.prop(context.scene, "cad_osnap_midpoint", text="Mid")
        row = box.row(align=True)
        row.prop(context.scene, "cad_osnap_nearest", text="Near")
        row.prop(context.scene, "cad_osnap_center", text="Cen")
        row = box.row(align=True)
        row.prop(context.scene, "cad_osnap_grid", text="Grid")
        box.prop(context.scene, "cad_grid_size", text="Grid Size")
        box.prop(context.scene, "cad_snap_radius", text="Snap Radius")

        layout.separator()
        row = layout.row(align=True)
        row.prop(context.scene, "cad_ortho", text="Ortho F8", toggle=True)
        row.operator("cad.toggle_ortho", text="Toggle")

        layout.separator()
        box = layout.box()
        box.label(text="How to use")
        box.label(text="Press /, type line, Enter")
        box.label(text="Commands: line, polyline, rectangle, circle, nurbs")
        box.label(text="Click points or type x,y,z")
        box.label(text="F8 toggles Ortho; Esc exits")

        if state.active:
            layout.separator()
            layout.label(text="Active CAD command:", icon="PLAY")
            layout.label(text=command_label())
            layout.label(text=f"Snap: {state.snap_label or 'none'}")



# -----------------------------------------------------------------------------
# Registration
# -----------------------------------------------------------------------------

addon_keymaps = []



class Hippo3D_OT_ActivateCPlaneExplicit(Operator):
    bl_idname = "cad.activate_cplane_explicit"
    bl_label = "Make CPlane Active"

    name: StringProperty(default="")
    builtin_mode: StringProperty(default="")
    layer_key: StringProperty(default="")

    def execute(self, context):
        key = self.layer_key or ""

        if key.startswith("BUILTIN:"):
            mode = key.split(":", 1)[1]
            set_builtin_cplane(context, mode)
            self.report({"INFO"}, f"Active CPlane: {mode.title()}")
        elif key.startswith("NAMED:"):
            name = key.split(":", 1)[1]
            if set_named_cplane(context, name):
                self.report({"INFO"}, f"Active CPlane: {name}")
            else:
                self.report({"WARNING"}, f"No saved CPlane named '{name}'.")
                return {"CANCELLED"}
        elif self.builtin_mode:
            set_builtin_cplane(context, self.builtin_mode)
            self.report({"INFO"}, f"Active CPlane: {self.builtin_mode.title()}")
        elif self.name:
            if set_named_cplane(context, self.name):
                self.report({"INFO"}, f"Active CPlane: {self.name}")
            else:
                self.report({"WARNING"}, f"No saved CPlane named '{self.name}'.")
                return {"CANCELLED"}

        sync_cplane_dropdown(context)

        for area in context.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()

        return {"FINISHED"}



class Hippo3D_OT_ToggleCPlaneVisibilityExplicit(Operator):
    bl_idname = "cad.toggle_cplane_visibility_explicit"
    bl_label = "Toggle CPlane Visibility"

    name: StringProperty(default="")
    builtin_mode: StringProperty(default="")
    layer_key: StringProperty(default="")

    def execute(self, context):
        key = self.layer_key or ""

        if key.startswith("BUILTIN:"):
            mode = key.split(":", 1)[1]
            current = is_cplane_visible(context, builtin_mode=mode)
            set_cplane_visible(context, not current, builtin_mode=mode)
        elif key.startswith("NAMED:"):
            name = key.split(":", 1)[1]
            current = is_cplane_visible(context, name=name)
            set_cplane_visible(context, not current, name=name)
        elif self.builtin_mode:
            current = is_cplane_visible(context, builtin_mode=self.builtin_mode)
            set_cplane_visible(context, not current, builtin_mode=self.builtin_mode)
        elif self.name:
            current = is_cplane_visible(context, name=self.name)
            set_cplane_visible(context, not current, name=self.name)

        for area in context.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()

        return {"FINISHED"}




# -----------------------------------------------------------------------------
# Regression Fix Overrides: Arc / Ellipse
# -----------------------------------------------------------------------------

def create_ellipse_from_2_points(context, p0, p1):
    """Create a CPlane-aware ellipse from centre and radius point."""
    import math as _math

    axis = p1 - p0
    rx = axis.length
    if rx < 1e-8:
        return None

    origin, u, v, n = get_cplane_axes(context)
    eu = axis.normalized()
    ev = n.cross(eu)

    if ev.length < 1e-8:
        ev = v.copy()
    else:
        ev.normalize()

    ry = float(getattr(context.scene, "hippo_ellipse_ry", rx * 0.5))
    if ry <= 1e-8:
        ry = rx * 0.5

    pts = []
    segments = 96
    for i in range(segments):
        a = 2.0 * _math.pi * i / segments
        pts.append(p0 + eu * (_math.cos(a) * rx) + ev * (_math.sin(a) * ry))

    obj = make_poly_curve_from_points(context, pts, name="Hippo3D_Ellipse", cyclic=True)
    if obj:
        obj["hippo_shape"] = "ellipse"
        obj["hippo_ellipse_rx"] = rx
        obj["hippo_ellipse_ry"] = ry

    return obj


def create_arc_from_3_points(context, p0, p1, p2, segments=64):
    """Create a stable 3-point arc in the active CPlane."""
    import math as _math

    origin, u, v, n = get_cplane_axes(context)

    def to2(p):
        q = p - origin
        return Vector((q.dot(u), q.dot(v)))

    a = to2(p0)
    b = to2(p1)
    c = to2(p2)

    d = 2.0 * (a.x * (b.y - c.y) + b.x * (c.y - a.y) + c.x * (a.y - b.y))
    if abs(d) < 1e-10:
        return None

    ux = ((a.x*a.x + a.y*a.y) * (b.y - c.y) +
          (b.x*b.x + b.y*b.y) * (c.y - a.y) +
          (c.x*c.x + c.y*c.y) * (a.y - b.y)) / d

    uy = ((a.x*a.x + a.y*a.y) * (c.x - b.x) +
          (b.x*b.x + b.y*b.y) * (a.x - c.x) +
          (c.x*c.x + c.y*c.y) * (b.x - a.x)) / d

    center = Vector((ux, uy))
    radius = (a - center).length
    if radius < 1e-8:
        return None

    def angle(pt):
        return _math.atan2(pt.y - center.y, pt.x - center.x)

    ang0 = angle(a)
    ang1 = angle(b)
    ang2 = angle(c)

    def normalize(x):
        while x < 0:
            x += 2.0 * _math.pi
        while x >= 2.0 * _math.pi:
            x -= 2.0 * _math.pi
        return x

    s = normalize(ang0)
    m = normalize(ang1)
    e = normalize(ang2)

    if s <= e:
        mid_on_ccw = s <= m <= e
    else:
        mid_on_ccw = m >= s or m <= e

    if mid_on_ccw:
        end = ang2
        while end < ang0:
            end += 2.0 * _math.pi
    else:
        end = ang2
        while end > ang0:
            end -= 2.0 * _math.pi

    pts = []
    steps = max(8, int(segments))
    for i in range(steps + 1):
        t = i / steps
        ang = ang0 + (end - ang0) * t
        pts.append(origin + u * (ux + _math.cos(ang) * radius) + v * (uy + _math.sin(ang) * radius))

    obj = make_poly_curve_from_points(context, pts, name="Hippo3D_Arc", cyclic=False)
    if obj:
        obj["hippo_shape"] = "arc"
        obj["hippo_arc_method"] = "3pt"
        obj["hippo_arc_segments"] = steps

    return obj


classes = [Hippo3D_OT_Command, Hippo3D_OT_StartLine, Hippo3D_OT_StartPolyline, Hippo3D_OT_StartRectangle, Hippo3D_OT_StartCircle, Hippo3D_OT_StartNurbs, Hippo3D_OT_SetSelectedNurbsDegree, Hippo3D_OT_Hippo3D_Loft, CAD_OT_LoftSurface, CAD_OT_LoftRealModifier, HIPPO_OT_NativeStatus, HIPPO_OT_StartArc, HIPPO_OT_Ellipse, HIPPO_OT_Polygon, HIPPO_OT_Project, HIPPO_OT_Array, HIPPO_OT_Explode, HIPPO_OT_XLine, HIPPO_OT_Offset,  HIPPO_OT_Trim, HIPPO_OT_Hippo3D_PlanarSurface, HIPPO_OT_Hippo3D_EdgeSurface, Hippo3D_OT_Hippo3D_Revolve, Hippo3D_OT_ClearRevolveAxis, Hippo3D_OT_SetRevolveAxis, CAD_OT_PipeSurface, CAD_OT_ExtrudeSurface, Hippo3D_OT_StartCommand, Hippo3D_OT_ToggleOrtho, Hippo3D_OT_ConvertToMesh, Hippo3D_OT_Join, Hippo3D_OT_SaveCPlane, Hippo3D_OT_RestoreCPlane, Hippo3D_OT_StartCPlane3Pt, Hippo3D_OT_StartCPlaneFace, Hippo3D_OT_StartCPlaneCurvePerp, Hippo3D_OT_RotateCPlane, Hippo3D_OT_StartCPlaneRotate3Pt, Hippo3D_OT_ApplyCPlaneAxisRotation, Hippo3D_OT_StartCPlaneAxisRotate, Hippo3D_OT_StartCPlaneMove, Hippo3D_OT_CameraToCPlane, Hippo3D_OT_ViewToCPlane, Hippo3D_OT_StartCPlaneZAxis, Hippo3D_OT_StartCPlaneXAxis, Hippo3D_OT_ToggleCPlaneVisibilityExplicit, Hippo3D_OT_ActivateCPlaneExplicit, Hippo3D_OT_RefreshCPlaneList, Hippo3D_OT_DeleteSelectedCPlane, Hippo3D_OT_ActivateSelectedCPlane, Hippo3D_OT_ToggleSelectedCPlaneVisible, Hippo3D_UL_CPlaneList, Hippo3D_CPlaneListItem, Hippo3D_OT_SetBuiltinCPlane, Hippo3D_OT_RestoreCPlaneByName, Hippo3D_OT_SetCPlaneVisible, Hippo3D_OT_DeleteCPlane, Hippo3D_PT_MainPanel]


def _cad_cplane_enum_update(self, context):
    # Choosing a built-in preset from the UI deactivates any restored named CPlane.
    self.cad_active_cplane_name = ""


def register_props():

    bpy.types.Scene.hippo_fillet_radius = FloatProperty(name="Fillet Radius", default=1.0, min=0.001, soft_max=100.0)
    bpy.types.Scene.hippo_trim_tolerance = FloatProperty(name="Trim Tolerance", default=0.05, min=0.0001, soft_max=10.0)

    bpy.types.Scene.hippo_polygon_sides = IntProperty(name="Polygon Sides", default=6, min=3, max=256)
    bpy.types.Scene.hippo_polygon_radius = FloatProperty(name="Polygon Radius", default=2.0, min=0.001, soft_max=100.0)

    bpy.types.Scene.hippo_offset_distance = FloatProperty(name="Offset Distance", default=1.0, soft_min=-100.0, soft_max=100.0)
    bpy.types.Scene.hippo_xline_length = FloatProperty(name="XLine Length", default=1000.0, min=1.0, soft_max=10000.0)
    bpy.types.Scene.hippo_array_count = IntProperty(name="Array Count", default=5, min=1, max=1000)
    bpy.types.Scene.hippo_array_dx = FloatProperty(name="Array X", default=2.0, soft_min=-100.0, soft_max=100.0)
    bpy.types.Scene.hippo_array_dy = FloatProperty(name="Array Y", default=0.0, soft_min=-100.0, soft_max=100.0)
    bpy.types.Scene.hippo_array_dz = FloatProperty(name="Array Z", default=0.0, soft_min=-100.0, soft_max=100.0)
    bpy.types.Scene.hippo_ellipse_rx = FloatProperty(name="Ellipse Radius X", default=2.0, min=0.001, soft_max=100.0)
    bpy.types.Scene.hippo_ellipse_ry = FloatProperty(name="Ellipse Radius Y", default=1.0, min=0.001, soft_max=100.0)
    bpy.types.Scene.cad_osnap_endpoint = BoolProperty(name="Endpoint", default=True)
    bpy.types.Scene.cad_osnap_midpoint = BoolProperty(name="Midpoint", default=True)
    bpy.types.Scene.cad_osnap_nearest = BoolProperty(name="Nearest", default=True)
    bpy.types.Scene.cad_osnap_center = BoolProperty(name="Center", default=True)
    bpy.types.Scene.cad_osnap_grid = BoolProperty(name="Grid", default=False)
    bpy.types.Scene.cad_ortho = BoolProperty(name="Ortho", default=False)
    bpy.types.Scene.cad_grid_size = FloatProperty(name="Grid Size", default=1.0, min=0.001, soft_max=10.0)
    bpy.types.Scene.cad_snap_radius = FloatProperty(name="Snap Radius", default=18.0, min=2.0, soft_max=80.0)
    bpy.types.Scene.cad_nurbs_degree = IntProperty(name="NURBS Degree", default=3, min=1, max=11)
    bpy.types.Scene.cad_selected_nurbs_degree = IntProperty(name="Selected NURBS Degree", default=3, min=1, max=11)
    bpy.types.Scene.cad_loft_samples = IntProperty(name="Loft Samples", default=32, min=2, max=256)
    bpy.types.Scene.cad_surface_samples = IntProperty(name="Surface Samples", default=32, min=2, max=256)
    bpy.types.Scene.cad_extrude_distance = FloatProperty(name="Extrude Distance", default=5.0, soft_min=-100.0, soft_max=100.0)
    bpy.types.Scene.cad_pipe_radius = FloatProperty(name="Pipe Radius", default=0.25, min=0.001, soft_max=10.0)
    bpy.types.Scene.cad_pipe_resolution = IntProperty(name="Pipe Resolution", default=12, min=3, max=64)
    bpy.types.Scene.cad_revolve_angle = FloatProperty(name="Revolve Degree", default=360.0, min=0.0, max=360.0, soft_min=0.0, soft_max=360.0)
    bpy.types.Scene.cad_revolve_steps = IntProperty(name="Revolve Steps", default=48, min=3, max=256)
    bpy.types.Scene.cad_revolve_axis_json = StringProperty(name="Revolve Axis", default="")
    bpy.types.Scene.cad_sweep_rail_samples = IntProperty(name="Sweep Rail Samples", default=32, min=2, max=256)
    bpy.types.Scene.cad_sweep_profile_samples = IntProperty(name="Sweep Profile Samples", default=24, min=2, max=256)
    bpy.types.Scene.cad_active_cplane_name = StringProperty(name="Active Named CPlane", default="")
    bpy.types.Scene.cad_cplane_save_name = StringProperty(name="CPlane Name", default="CPlane 01")
    bpy.types.Scene.cad_cplane_rotate_angle = FloatProperty(name="Rotate Angle", default=90.0, soft_min=-360.0, soft_max=360.0)
    bpy.types.Scene.cad_cplane_camera_distance = FloatProperty(name="Camera Distance", default=20.0, min=0.1, soft_max=100.0)

    bpy.types.Scene.cad_cplane_axis_rotation_angle = FloatProperty(
        name="Axis Angle",
        default=0.0,
        soft_min=-360.0,
        soft_max=360.0,
        update=cad_cplane_axis_rotation_angle_update,
    )
    bpy.types.Scene.cad_cplane_axis_rotation_name = StringProperty(name="Axis Rotation CPlane", default="")
    bpy.types.Scene.cad_cplane_axis_rotation_json = StringProperty(name="Axis Rotation Data", default="{}")
    bpy.types.Scene.cad_cplanes_json = StringProperty(name="Saved CPlanes", default="{}")
    bpy.types.Scene.cad_cplane_visibility_json = StringProperty(name="CPlane Visibility", default="{}")
    bpy.types.Scene.cad_show_cplane_visuals = BoolProperty(name="Show CPlanes", default=True)
    bpy.types.Scene.cad_show_cplane_grid_visuals = BoolProperty(name="Show CPlane Grids", default=True)
    bpy.types.Scene.cad_show_cplane_labels = BoolProperty(name="Show CPlane Labels", default=True)
    bpy.types.Scene.cad_cplane_visual_grid_count = FloatProperty(name="CPlane Grid Count", default=6.0, min=1.0, soft_max=30.0)
    bpy.types.Scene.cad_cplane_visual_grid_spacing = FloatProperty(name="CPlane Grid Spacing", default=1.0, min=0.001, soft_max=10.0)
    bpy.types.Scene.cad_cplane_visual_axis_length = FloatProperty(name="CPlane Axis Length", default=2.0, min=0.1, soft_max=20.0)
    bpy.types.Scene.cad_cplane_items = CollectionProperty(type=Hippo3D_CPlaneListItem)
    bpy.types.Scene.cad_cplane_index = IntProperty(name="CPlane List Index", default=0)
    bpy.types.Scene.cad_active_cplane_dropdown = EnumProperty(
        name="Active CPlane",
        description="Choose the active built-in or saved CPlane",
        items=cplane_dropdown_items,
        update=cplane_dropdown_update,
    )
    bpy.types.Scene.cad_current_cplane_visible = BoolProperty(
        name="Current CPlane Visible",
        description="Show or hide the currently active CPlane",
        default=True,
        update=current_cplane_visibility_update,
    )
    bpy.types.Scene.cad_cplane = EnumProperty(
        name="CPlane",
        description="Active CAD construction plane",
        items=[
            ("TOP", "Top / XY", "Draw on world XY"),
            ("FRONT", "Front / XZ", "Draw on world XZ"),
            ("RIGHT", "Right / YZ", "Draw on world YZ"),
            ("WORLD", "World / XY", "World XY drawing plane")],
        default="TOP",
        update=_cad_cplane_enum_update,
    )


def unregister_props():
    for name in ["cad_osnap_endpoint", "cad_osnap_midpoint", "cad_osnap_nearest", "cad_osnap_center", "cad_osnap_grid", "cad_ortho", "cad_grid_size", "cad_snap_radius", "cad_active_cplane_name", "cad_cplane_save_name", "cad_cplanes_json", "cad_show_cplane_visuals", "cad_show_cplane_grid_visuals", "cad_show_cplane_labels", "cad_cplane_visual_grid_count", "cad_cplane_visual_grid_spacing", "cad_cplane_visual_axis_length", "cad_cplane_visibility_json", "cad_cplane_items", "cad_cplane_index", "cad_active_cplane_dropdown", "cad_current_cplane_visible", "cad_cplane", "cad_cplane_rotate_angle", "cad_cplane_axis_rotation_angle", "cad_cplane_axis_rotation_name", "cad_cplane_axis_rotation_json", "cad_cplane_camera_distance", "cad_nurbs_degree", "cad_selected_nurbs_degree", "cad_loft_samples", "cad_surface_samples", "cad_extrude_distance", "cad_pipe_radius", "cad_pipe_resolution", "cad_revolve_angle", "cad_revolve_steps", "cad_sweep_rail_samples", "cad_sweep_profile_samples", "cad_revolve_axis_json", "hippo_offset_distance", "hippo_xline_length", "hippo_array_count", "hippo_array_dx", "hippo_array_dy", "hippo_array_dz", "hippo_ellipse_rx", "hippo_ellipse_ry", "hippo_polygon_sides", "hippo_polygon_radius", "hippo_fillet_radius", "hippo_trim_tolerance"]:
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)



@persistent
def cad_cplane_load_post_handler(dummy):
    try:
        sync_cplane_layer_collection(bpy.context)
    except Exception:
        pass


def cad_cplane_init_timer():
    try:
        sync_cplane_layer_collection(bpy.context)
    except Exception:
        pass
    return None




# -----------------------------------------------------------------------------
# FINAL OVERRIDE: NURBS Offset by Control Points
# -----------------------------------------------------------------------------

def hippo_nurbs_control_points_world(obj):
    if obj is None or obj.type != "CURVE":
        return [], False, 3, 4, 24

    try:
        spl = obj.data.splines[0]
    except Exception:
        return [], False, 3, 4, 24

    if spl.type != "NURBS":
        return [], False, 3, 4, 24

    pts = [obj.matrix_world @ Vector((p.co.x, p.co.y, p.co.z)) for p in spl.points]
    cyclic = bool(getattr(spl, "use_cyclic_u", False))
    order = int(getattr(spl, "order_u", 4))
    degree = max(1, order - 1)
    resolution_u = int(getattr(obj.data, "resolution_u", 24))

    if len(pts) >= 2 and (pts[0] - pts[-1]).length < 1e-6:
        pts = pts[:-1]
        cyclic = True

    return pts, cyclic, degree, order, resolution_u


def hippo_offset_control_points_on_cplane(context, pts, cyclic, distance):
    if len(pts) < 2:
        return []

    origin, u, v, n = get_cplane_axes(context)
    result = []
    total = len(pts)

    for i, p in enumerate(pts):
        if cyclic:
            prev_p = pts[(i - 1) % total]
            next_p = pts[(i + 1) % total]
        else:
            prev_p = pts[max(i - 1, 0)]
            next_p = pts[min(i + 1, total - 1)]

        tangent = next_p - prev_p

        if tangent.length < 1e-8:
            if i < total - 1:
                tangent = pts[i + 1] - p
            elif i > 0:
                tangent = p - pts[i - 1]

        if tangent.length < 1e-8:
            result.append(p.copy())
            continue

        tangent.normalize()
        perp = n.cross(tangent)

        if perp.length < 1e-8:
            result.append(p.copy())
            continue

        perp.normalize()
        result.append(p + perp * float(distance))

    return result


def hippo_create_nurbs_from_control_points(context, points, name, degree, cyclic=False, resolution_u=24):
    if len(points) < 2:
        return None

    degree = int(max(1, min(int(degree), len(points) - 1)))
    order = degree + 1

    curve = bpy.data.curves.new(name, type="CURVE")
    curve.dimensions = "3D"
    curve.resolution_u = int(max(1, resolution_u))

    spl = curve.splines.new(type="NURBS")
    spl.points.add(len(points) - 1)

    for pnt, co in zip(spl.points, points):
        pnt.co = (co.x, co.y, co.z, 1.0)

    spl.order_u = order
    spl.use_endpoint_u = True
    spl.use_cyclic_u = bool(cyclic)

    obj = bpy.data.objects.new(name, curve)
    context.collection.objects.link(obj)

    obj["hippo_shape"] = "nurbs_curve"
    obj["hippo_curve_type"] = "NURBS"
    obj["hippo_degree"] = degree
    obj["hippo_order_u"] = order
    obj["hippo_offset_method"] = "control_points"

    return obj


def hippo_read_poly_points_world(obj):
    try:
        spl = obj.data.splines[0]
        pts = [obj.matrix_world @ Vector((p.co.x, p.co.y, p.co.z)) for p in spl.points]
        cyclic = bool(getattr(spl, "use_cyclic_u", False))
        if len(pts) >= 2 and (pts[0] - pts[-1]).length < 1e-6:
            pts = pts[:-1]
            cyclic = True
        return pts, cyclic
    except Exception:
        return [], False


def hippo_create_poly_from_points(context, points, name, cyclic=False):
    if len(points) < 2:
        return None

    curve = bpy.data.curves.new(name, type="CURVE")
    curve.dimensions = "3D"
    curve.resolution_u = 12

    spl = curve.splines.new(type="POLY")
    spl.points.add(len(points) - 1)

    for pnt, co in zip(spl.points, points):
        pnt.co = (co.x, co.y, co.z, 1.0)

    spl.use_cyclic_u = bool(cyclic)

    obj = bpy.data.objects.new(name, curve)
    context.collection.objects.link(obj)
    obj["hippo_curve_type"] = "POLY"
    obj["hippo_offset_method"] = "control_polygon"

    return obj


def run_offset_command(context):
    """Offset selected curves.

    NURBS:
    - offset the source NURBS control points
    - recreate a real NURBS spline
    - preserve degree/order and control point count
    """
    objs = [obj for obj in context.selected_objects if obj.type == "CURVE"]

    if not objs:
        return False, "Offset: select curve objects."

    dist = float(getattr(context.scene, "hippo_offset_distance", 0.2))
    created = 0

    for obj in objs:
        try:
            source_type = obj.data.splines[0].type if obj.data.splines else "UNKNOWN"
        except Exception:
            source_type = "UNKNOWN"

        if source_type == "NURBS":
            pts, cyclic, degree, order, resolution_u = hippo_nurbs_control_points_world(obj)
            if len(pts) < 2:
                continue

            result = hippo_offset_control_points_on_cplane(context, pts, cyclic, dist)

            new_obj = hippo_create_nurbs_from_control_points(
                context,
                result,
                name=f"{obj.name}_NURBS_Offset",
                degree=degree,
                cyclic=cyclic,
                resolution_u=resolution_u,
            )

            if new_obj:
                new_obj["hippo_source"] = obj.name
                new_obj["hippo_source_spline_type"] = "NURBS"
                new_obj["hippo_source_degree"] = degree
                new_obj["hippo_control_point_count"] = len(result)
                new_obj["hippo_offset_distance"] = dist
                created += 1

        elif source_type == "POLY":
            pts, cyclic = hippo_read_poly_points_world(obj)
            if len(pts) < 2:
                continue

            result = hippo_offset_control_points_on_cplane(context, pts, cyclic, dist)

            new_obj = hippo_create_poly_from_points(
                context,
                result,
                name=f"{obj.name}_Offset",
                cyclic=cyclic,
            )

            if new_obj:
                new_obj["hippo_source"] = obj.name
                new_obj["hippo_source_spline_type"] = "POLY"
                new_obj["hippo_offset_distance"] = dist
                created += 1

        else:
            # For Bezier/unknown, use sampled shape but still create NURBS approximation.
            pts = sample_curve_object_points(obj, samples=max(64, int(getattr(context.scene, "cad_surface_samples", 64))))
            if len(pts) < 2:
                continue

            cyclic = False
            if len(pts) >= 2 and (pts[0] - pts[-1]).length < 1e-6:
                pts = pts[:-1]
                cyclic = True

            result = hippo_offset_control_points_on_cplane(context, pts, cyclic, dist)

            new_obj = hippo_create_nurbs_from_control_points(
                context,
                result,
                name=f"{obj.name}_NURBS_Offset",
                degree=3,
                cyclic=cyclic,
                resolution_u=24,
            )

            if new_obj:
                new_obj["hippo_source"] = obj.name
                new_obj["hippo_source_spline_type"] = source_type
                new_obj["hippo_offset_distance"] = dist
                created += 1

    if created == 0:
        return False, "Offset failed."

    return True, f"Created {created} offset curve(s)."



# -----------------------------------------------------------------------------
# Arc 3Pt Helper Fix
# -----------------------------------------------------------------------------

def create_arc_from_3_points(context, p0, p1, p2, segments=48):
    """Create an arc curve from three world-space points using the active CPlane.

    Points:
    - p0 = arc start
    - p1 = point on arc
    - p2 = arc end
    """
    import math as _math

    origin, u, v, n = get_cplane_axes(context)

    def to_2d(p):
        q = p - origin
        return Vector((q.dot(u), q.dot(v)))

    a = to_2d(p0)
    b = to_2d(p1)
    c = to_2d(p2)

    # Circumcircle in 2D.
    d = 2.0 * (
        a.x * (b.y - c.y) +
        b.x * (c.y - a.y) +
        c.x * (a.y - b.y)
    )

    if abs(d) < 1e-10:
        return None

    ux = (
        (a.x * a.x + a.y * a.y) * (b.y - c.y) +
        (b.x * b.x + b.y * b.y) * (c.y - a.y) +
        (c.x * c.x + c.y * c.y) * (a.y - b.y)
    ) / d

    uy = (
        (a.x * a.x + a.y * a.y) * (c.x - b.x) +
        (b.x * b.x + b.y * b.y) * (a.x - c.x) +
        (c.x * c.x + c.y * c.y) * (b.x - a.x)
    ) / d

    center = Vector((ux, uy))
    radius = (a - center).length

    if radius < 1e-8:
        return None

    def angle(pt):
        return _math.atan2(pt.y - center.y, pt.x - center.x)

    ang0 = angle(a)
    ang1 = angle(b)
    ang2 = angle(c)

    def norm(x):
        while x < 0:
            x += 2.0 * _math.pi
        while x >= 2.0 * _math.pi:
            x -= 2.0 * _math.pi
        return x

    s = norm(ang0)
    m = norm(ang1)
    e = norm(ang2)

    # Determine if the CCW arc from start to end passes through middle.
    if s <= e:
        ccw_contains_mid = s <= m <= e
    else:
        ccw_contains_mid = m >= s or m <= e

    if ccw_contains_mid:
        end_angle = ang2
        if end_angle < ang0:
            end_angle += 2.0 * _math.pi
    else:
        end_angle = ang2
        if end_angle > ang0:
            end_angle -= 2.0 * _math.pi

    pts = []
    steps = max(8, int(segments))

    for i in range(steps + 1):
        t = i / steps
        ang = ang0 + (end_angle - ang0) * t
        x = ux + _math.cos(ang) * radius
        y = uy + _math.sin(ang) * radius
        pts.append(origin + u * x + v * y)

    # Create as a curve object. Use POLY approximation for now, but keep as curve.
    curve = bpy.data.curves.new("Hippo3D_Arc", type="CURVE")
    curve.dimensions = "3D"
    curve.resolution_u = 24

    spl = curve.splines.new(type="POLY")
    spl.points.add(len(pts) - 1)

    for bp, p in zip(spl.points, pts):
        bp.co = (p.x, p.y, p.z, 1.0)

    obj = bpy.data.objects.new("Hippo3D_Arc", curve)
    context.collection.objects.link(obj)

    obj["hippo_shape"] = "arc"
    obj["hippo_arc_method"] = "3pt"
    obj["hippo_arc_segments"] = steps

    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    context.view_layer.objects.active = obj

    return obj



# -----------------------------------------------------------------------------
# XLine 2Pt Helper Fix
# -----------------------------------------------------------------------------

def create_xline_from_2_points(context, p0, p1):
    """Create an AutoCAD/Rhino-style construction line from two picked points.

    The first point defines a point on the XLine.
    The second point defines the direction.
    The line is represented as a very long curve segment.
    """
    direction = p1 - p0

    if direction.length < 1e-8:
        return None

    direction.normalize()

    length = float(getattr(context.scene, "hippo_xline_length", 1000.0))
    half = length * 0.5

    a = p0 - direction * half
    b = p0 + direction * half

    curve = bpy.data.curves.new("Hippo3D_XLine", type="CURVE")
    curve.dimensions = "3D"
    curve.resolution_u = 1

    spl = curve.splines.new(type="POLY")
    spl.points.add(1)

    spl.points[0].co = (a.x, a.y, a.z, 1.0)
    spl.points[1].co = (b.x, b.y, b.z, 1.0)

    obj = bpy.data.objects.new("Hippo3D_XLine", curve)
    context.collection.objects.link(obj)

    obj["hippo_shape"] = "xline"
    obj["hippo_command"] = "xline"
    obj["hippo_xline_length"] = length

    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    context.view_layer.objects.active = obj

    return obj


def run_xline_command(context):
    return False, "XLine is interactive. Type XLine, then pick two points to define direction."



# -----------------------------------------------------------------------------
# Project Command Helper Fix
# -----------------------------------------------------------------------------

def hippo_make_project_curve(context, points, name="Hippo3D_Project", cyclic=False):
    if not points or len(points) < 2:
        return None

    curve = bpy.data.curves.new(name, type="CURVE")
    curve.dimensions = "3D"
    curve.resolution_u = 12

    spl = curve.splines.new(type="POLY")
    spl.points.add(len(points) - 1)

    for bp, p in zip(spl.points, points):
        bp.co = (p.x, p.y, p.z, 1.0)

    spl.use_cyclic_u = bool(cyclic)

    obj = bpy.data.objects.new(name, curve)
    context.collection.objects.link(obj)

    obj["hippo_shape"] = "projected_curve"
    obj["hippo_command"] = "project"

    return obj


def hippo_get_curve_points_for_project(obj, context):
    """Read curve points for projection.

    Uses sampled geometry for NURBS/Bezier so the projected result follows the displayed curve.
    Uses original vertices for POLY where possible.
    """
    if obj is None or obj.type != "CURVE":
        return [], False

    try:
        spl = obj.data.splines[0]
        cyclic = bool(getattr(spl, "use_cyclic_u", False))

        if spl.type == "POLY":
            pts = [obj.matrix_world @ Vector((p.co.x, p.co.y, p.co.z)) for p in spl.points]
        else:
            samples = max(64, int(getattr(context.scene, "cad_surface_samples", 64)))
            pts = sample_curve_object_points(obj, samples=samples)

        if len(pts) >= 2 and (pts[0] - pts[-1]).length < 1e-6:
            pts = pts[:-1]
            cyclic = True

        return pts, cyclic
    except Exception:
        return [], False


def run_project_command(context):
    """Project selected curves onto the active CPlane."""
    curves = [obj for obj in context.selected_objects if obj.type == "CURVE"]

    if not curves:
        return False, "Project: select curve object(s)."

    plane_origin, u, v, n = get_cplane_axes(context)
    created = 0

    for obj in curves:
        pts, cyclic = hippo_get_curve_points_for_project(obj, context)

        if len(pts) < 2:
            continue

        projected = []

        for p in pts:
            d = (p - plane_origin).dot(n)
            projected.append(p - n * d)

        new_obj = hippo_make_project_curve(
            context,
            projected,
            name=f"{obj.name}_Projected",
            cyclic=cyclic,
        )

        if new_obj:
            new_obj["hippo_source"] = obj.name
            created += 1

    if created == 0:
        return False, "Project failed."

    return True, f"Projected {created} curve(s) to active CPlane."



# -----------------------------------------------------------------------------
# Explode Command Helper Fix
# -----------------------------------------------------------------------------

def hippo_make_curve_segment(context, a, b, name="Hippo3D_Explode_Segment"):
    if (b - a).length < 1e-8:
        return None

    curve = bpy.data.curves.new(name, type="CURVE")
    curve.dimensions = "3D"
    curve.resolution_u = 1

    spl = curve.splines.new(type="POLY")
    spl.points.add(1)

    spl.points[0].co = (a.x, a.y, a.z, 1.0)
    spl.points[1].co = (b.x, b.y, b.z, 1.0)

    obj = bpy.data.objects.new(name, curve)
    context.collection.objects.link(obj)

    obj["hippo_shape"] = "curve_segment"
    obj["hippo_command"] = "explode"

    return obj


def hippo_curve_spline_points_for_explode(obj, spline):
    if obj is None or obj.type != "CURVE":
        return []

    pts = []

    try:
        if spline.type in {"POLY", "NURBS"}:
            pts = [obj.matrix_world @ Vector((p.co.x, p.co.y, p.co.z)) for p in spline.points]
        elif spline.type == "BEZIER":
            pts = [obj.matrix_world @ p.co for p in spline.bezier_points]
    except Exception:
        pts = []

    return pts


def run_explode_command(context):
    """Explode selected curve objects into individual line-segment curve objects.

    Behaviour:
    - POLY/NURBS: explodes by control polygon/control points.
    - BEZIER: explodes by Bezier edit points.
    - Cyclic splines add the final closing segment.
    - Original curve objects are removed after successful explosion.
    """
    objs = [obj for obj in context.selected_objects if obj.type == "CURVE"]

    if not objs:
        return False, "Explode: select curve object(s)."

    created = 0
    to_remove = []

    for obj in objs:
        obj_created = 0

        try:
            splines = list(obj.data.splines)
        except Exception:
            splines = []

        for spline in splines:
            pts = hippo_curve_spline_points_for_explode(obj, spline)

            if len(pts) < 2:
                continue

            pairs = list(zip(pts[:-1], pts[1:]))

            if bool(getattr(spline, "use_cyclic_u", False)) and len(pts) > 2:
                pairs.append((pts[-1], pts[0]))

            for a, b in pairs:
                seg = hippo_make_curve_segment(
                    context,
                    a,
                    b,
                    name=f"{obj.name}_Exploded",
                )

                if seg:
                    seg["hippo_source"] = obj.name
                    created += 1
                    obj_created += 1

        if obj_created > 0:
            to_remove.append(obj)

    for obj in to_remove:
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
        except Exception:
            pass

    if created == 0:
        return False, "Explode failed. No curve segments were created."

    return True, f"Exploded into {created} segment(s)."



# -----------------------------------------------------------------------------
# CPlane Perpendicular to Curve - Working Override
# -----------------------------------------------------------------------------

def hippo_nearest_curve_point_tangent(obj, pick_point, samples=160):
    if obj is None or obj.type != "CURVE":
        return None

    pts = sample_curve_object_points(obj, samples=max(16, samples))

    if len(pts) < 2:
        return None

    best_i = min(range(len(pts)), key=lambda i: (pts[i] - pick_point).length)
    origin = pts[best_i]

    if best_i == 0:
        tangent = pts[1] - pts[0]
    elif best_i == len(pts) - 1:
        tangent = pts[-1] - pts[-2]
    else:
        tangent = pts[best_i + 1] - pts[best_i - 1]

    if tangent.length < 1e-8:
        return None

    tangent.normalize()
    return origin, tangent


def create_cplane_perpendicular_to_curve(context, name, pick_point):
    curves = [obj for obj in context.selected_objects if obj.type == "CURVE"]

    if not curves:
        return False, "CPlane Perp Curve: select a curve first, then click near it."

    curve = context.active_object if context.active_object in curves else curves[0]

    result = hippo_nearest_curve_point_tangent(curve, pick_point)
    if result is None:
        return False, "CPlane Perp Curve: could not read curve/tangent."

    origin, n = result

    # CPlane normal is curve tangent. This makes the CPlane perpendicular to the curve.
    up = Vector((0, 0, 1))
    if abs(n.dot(up)) > 0.95:
        up = Vector((0, 1, 0))

    u = up.cross(n)
    if u.length < 1e-8:
        u = Vector((1, 0, 0)).cross(n)

    if u.length < 1e-8:
        return False, "CPlane Perp Curve: could not build axes."

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



# -----------------------------------------------------------------------------
# Stable Sweep1 / Sweep2 Override
# -----------------------------------------------------------------------------

def hippo_curve_points(obj, samples=64):
    pts = sample_curve_object_points(obj, samples=max(2, int(samples)))
    return pts if pts and len(pts) >= 2 else []


def hippo_align_curve_direction(reference_pts, target_pts):
    """Reverse target if its endpoints better match the reference direction."""
    if not reference_pts or not target_pts:
        return target_pts

    same = (reference_pts[0] - target_pts[0]).length + (reference_pts[-1] - target_pts[-1]).length
    flip = (reference_pts[0] - target_pts[-1]).length + (reference_pts[-1] - target_pts[0]).length

    if flip < same:
        return list(reversed(target_pts))

    return target_pts


def hippo_make_mesh_from_sections(context, name, sections, props=None, flip_faces=False):
    if len(sections) < 2 or len(sections[0]) < 2:
        return None

    rows = len(sections)
    cols = min(len(row) for row in sections)
    sections = [row[:cols] for row in sections]

    verts = []
    for row in sections:
        verts.extend([(p.x, p.y, p.z) for p in row])

    faces = []
    for r in range(rows - 1):
        for c in range(cols - 1):
            a = r * cols + c
            b = r * cols + c + 1
            cc = (r + 1) * cols + c + 1
            d = (r + 1) * cols + c

            if flip_faces:
                faces.append((a, d, cc, b))
            else:
                faces.append((a, b, cc, d))

    mesh = bpy.data.meshes.new(name + "_Mesh")
    mesh.from_pydata(verts, [], faces)
    mesh.update()

    obj = bpy.data.objects.new(name, mesh)
    context.collection.objects.link(obj)

    if props:
        for k, v in props.items():
            obj[k] = v

    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    context.view_layer.objects.active = obj

    return obj


def hippo_rail_frame(rail_pts, i, prev_y=None, prev_z=None):
    """Stable rail frame with minimal flipping."""
    count = len(rail_pts)

    if i == 0:
        tangent = rail_pts[1] - rail_pts[0]
    elif i == count - 1:
        tangent = rail_pts[-1] - rail_pts[-2]
    else:
        tangent = rail_pts[i + 1] - rail_pts[i - 1]

    if tangent.length < 1e-8:
        tangent = Vector((1, 0, 0))

    xaxis = tangent.normalized()

    up = Vector((0, 0, 1))
    if abs(xaxis.dot(up)) > 0.95:
        up = Vector((0, 1, 0))

    yaxis = up.cross(xaxis)
    if yaxis.length < 1e-8:
        yaxis = Vector((1, 0, 0)).cross(xaxis)

    if yaxis.length < 1e-8:
        yaxis = Vector((0, 1, 0))

    yaxis.normalize()
    zaxis = xaxis.cross(yaxis).normalized()

    # Prevent sudden 180-degree frame flips.
    if prev_y is not None and yaxis.dot(prev_y) < 0:
        yaxis.negate()
        zaxis.negate()

    return xaxis, yaxis, zaxis





def _hippo_sample_curve(obj, samples):
    """Ordered curve sampler for Sweep commands.

    Important:
    Do NOT use evaluated mesh vertices here. Blender evaluated curve meshes can
    return vertices in an order that is not safe for sweep section building,
    which creates spikes/twists. This function reads spline points directly in
    spline order.
    """
    if obj is None or obj.type != "CURVE":
        return []

    try:
        if not obj.data.splines:
            return []

        spl = obj.data.splines[0]

        if spl.type in {"POLY", "NURBS"}:
            pts = [obj.matrix_world @ Vector((p.co.x, p.co.y, p.co.z)) for p in spl.points]
        elif spl.type == "BEZIER":
            pts = [obj.matrix_world @ p.co for p in spl.bezier_points]
        else:
            pts = []

        if len(pts) >= 2 and (pts[0] - pts[-1]).length < 1e-6:
            pts = pts[:-1]

        return pts if len(pts) >= 2 else []

    except Exception:
        return []


def _hippo_make_surface_mesh(context, name, sections, props=None):
    if not sections or len(sections) < 2:
        return None

    cols = min(len(row) for row in sections)
    if cols < 2:
        return None

    sections = [row[:cols] for row in sections]

    verts = []
    for row in sections:
        verts.extend([(p.x, p.y, p.z) for p in row])

    faces = []
    rows = len(sections)

    for r in range(rows - 1):
        for c in range(cols - 1):
            a = r * cols + c
            b = r * cols + c + 1
            cc = (r + 1) * cols + c + 1
            d = (r + 1) * cols + c
            faces.append((a, b, cc, d))

    mesh = bpy.data.meshes.new(name + "_Mesh")
    mesh.from_pydata(verts, [], faces)
    mesh.update()

    obj = bpy.data.objects.new(name, mesh)
    context.collection.objects.link(obj)

    if props:
        for k, v in props.items():
            obj[k] = v

    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    context.view_layer.objects.active = obj

    return obj


def create_sweep1_surface(context):
    """Restarted simple Sweep1.

    Selection:
    - Select rail + profile.
    - Active selected curve is treated as profile.
    Behaviour:
    - The profile is copied along the rail by translation.
    - No rotation/frame transport is applied, avoiding spikes/twist.
    """
    curves = [o for o in context.selected_objects if o.type == "CURVE"]

    if len(curves) < 2:
        return False, "Sweep1: select one rail and one profile curve."

    active = context.active_object

    if active in curves and len(curves) >= 2 and bool(getattr(context.scene, True)):
        profile = active
        rails = [c for c in curves if c != profile]
        rail = rails[0]
    else:
        rail = active if active in curves else curves[0]
        others = [c for c in curves if c != rail]
        profile = others[0] if others else curves[1]

    rail_pts = _hippo_sample_curve(
        rail,
        int(getattr(context.scene, "cad_sweep_rail_samples", 48)),
    )
    profile_pts = _hippo_sample_curve(
        profile,
        int(getattr(context.scene, "cad_sweep_profile_samples", 24)),
    )

    if len(rail_pts) < 2 or len(profile_pts) < 2:
        return False, "Sweep1: rail/profile has insufficient points."

    profile_center = sum(profile_pts, Vector((0, 0, 0))) / len(profile_pts)
    local_profile = [p - profile_center for p in profile_pts]

    sections = []

    for rail_p in rail_pts:
        row = [rail_p + local for local in local_profile]
        sections.append(row)

    obj = _hippo_make_surface_mesh(
        context,
        "Hippo3D_Sweep1",
        sections,
        props={
            "hippo_surface_type": "sweep1",
            "cad_surface_type": "sweep1",
            "hippo_sweep_method": "restart_translate_profile",
            "hippo_rail": rail.name,
            "hippo_profile": profile.name,
        },
    )

    if not obj:
        return False, "Sweep1 failed."

    return True, "Sweep1 created."


def create_sweep2_surface(context, rail_samples=None):
    """Restarted simple Sweep2.

    Selection:
    - Select two rails and one profile.
    - Active selected curve is treated as profile.
    Behaviour:
    - Rails are auto-aligned by endpoint distance.
    - Rows interpolate cleanly from rail A to rail B.
    - Profile is used only for number/distribution of section samples.
    """
    curves = [o for o in context.selected_objects if o.type == "CURVE"]

    if len(curves) < 3:
        return False, "Sweep2: select two rails and one profile curve."

    active = context.active_object

    if active in curves and bool(getattr(context.scene, True)):
        profile = active
        rails = [c for c in curves if c != profile][:2]
    else:
        profile = curves[-1]
        rails = curves[:2]

    if len(rails) < 2:
        return False, "Sweep2: select two rail curves."

    if rail_samples is None:
        rail_samples = int(getattr(context.scene, "cad_sweep_rail_samples", 48))

    rail_a = _hippo_sample_curve(rails[0], rail_samples)
    rail_b = _hippo_sample_curve(rails[1], rail_samples)
    profile_pts = _hippo_sample_curve(
        profile,
        int(getattr(context.scene, "cad_sweep_profile_samples", 24)),
    )

    if len(rail_a) < 2 or len(rail_b) < 2 or len(profile_pts) < 2:
        return False, "Sweep2: rails/profile have insufficient points."

    same = (rail_a[0] - rail_b[0]).length + (rail_a[-1] - rail_b[-1]).length
    flip = (rail_a[0] - rail_b[-1]).length + (rail_a[-1] - rail_b[0]).length
    if flip < same:
        rail_b = list(reversed(rail_b))

    count = min(len(rail_a), len(rail_b))
    profile_count = len(profile_pts)

    # Use equal profile parameters. This avoids twisted/upsidedown geometry.
    params = [i / max(profile_count - 1, 1) for i in range(profile_count)]

    sections = []

    for i in range(count):
        a = rail_a[i]
        b = rail_b[i]
        row = [a.lerp(b, t) for t in params]
        sections.append(row)

    obj = _hippo_make_surface_mesh(
        context,
        "Hippo3D_Sweep2",
        sections,
        props={
            "hippo_surface_type": "sweep2",
            "cad_surface_type": "sweep2",
            "hippo_sweep_method": "restart_ruled_two_rail",
            "hippo_rail_a": rails[0].name,
            "hippo_rail_b": rails[1].name,
            "hippo_profile": profile.name,
        },
    )

    if not obj:
        return False, "Sweep2 failed."

    return True, "Sweep2 created."

def create_edge_srf(context):
    """Create Hippo3D_EdgeSurface from selected boundary curves.

    This version avoids bow-tie/crossed faces by:
    1. sampling each selected edge,
    2. ordering the edges into a continuous perimeter loop,
    3. orienting each edge so the end of one connects to the start of the next,
    4. creating one mesh face from the ordered boundary vertices.

    For 2 curves, it still creates a simple loft-like surface between them.
    For 3 or 4 curves, it behaves like a boundary Hippo3D_EdgeSurface / filled perimeter.
    """
    curves = [obj for obj in context.selected_objects if obj.type == "CURVE"]

    if len(curves) not in {2, 3, 4}:
        return False, "Hippo3D_EdgeSurface: select 2, 3, or 4 boundary curves."

    samples = int(getattr(context.scene, "cad_surface_samples", 32))
    samples = max(2, samples)

    sampled = []
    for obj in curves:
        pts = sample_curve_object_points(obj, samples=samples)
        if len(pts) < 2:
            return False, f"Hippo3D_EdgeSurface: curve '{obj.name}' has insufficient points."
        sampled.append({
            "obj": obj,
            "pts": pts,
            "start": pts[0],
            "end": pts[-1],
        })

    # 2-edge case: keep a loft-style ruled surface.
    if len(sampled) == 2:
        a = sampled[0]["pts"]
        b = sampled[1]["pts"]

        # Align second edge direction to first edge.
        same = (a[0] - b[0]).length + (a[-1] - b[-1]).length
        flip = (a[0] - b[-1]).length + (a[-1] - b[0]).length
        if flip < same:
            b = list(reversed(b))

        verts = []
        for p in a:
            verts.append((p.x, p.y, p.z))
        for p in b:
            verts.append((p.x, p.y, p.z))

        faces = []
        n = min(len(a), len(b))
        for i in range(n - 1):
            faces.append((i, i + 1, n + i + 1, n + i))

        mesh = bpy.data.meshes.new("Hippo3D_EdgeSurface_Mesh")
        mesh.from_pydata(verts, [], faces)
        mesh.update()

        obj = bpy.data.objects.new("Hippo3D_EdgeSurface", mesh)
        context.collection.objects.link(obj)
        obj["hippo_surface_type"] = "edgesrf"
        obj["cad_surface_type"] = "edgesrf"
        obj["hippo_edgesrf_method"] = "two_edge_ruled"

        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        context.view_layer.objects.active = obj

        return True, "Created Hippo3D_EdgeSurface from 2 curves."

    # 3/4-edge case: build a continuous boundary loop.
    remaining = sampled[:]

    # Start with the left-most/lower-most edge start for more stable ordering.
    start_i = min(
        range(len(remaining)),
        key=lambda i: (
            min(remaining[i]["start"].x, remaining[i]["end"].x),
            min(remaining[i]["start"].y, remaining[i]["end"].y),
            min(remaining[i]["start"].z, remaining[i]["end"].z),
        )
    )

    first = remaining.pop(start_i)
    loop_edges = [first["pts"]]

    while remaining:
        current_end = loop_edges[-1][-1]

        best_i = None
        best_reverse = False
        best_dist = 1e30

        for i, item in enumerate(remaining):
            d_start = (current_end - item["start"]).length
            d_end = (current_end - item["end"]).length

            if d_start < best_dist:
                best_dist = d_start
                best_i = i
                best_reverse = False

            if d_end < best_dist:
                best_dist = d_end
                best_i = i
                best_reverse = True

        item = remaining.pop(best_i)
        pts = list(item["pts"])

        if best_reverse:
            pts = list(reversed(pts))

        loop_edges.append(pts)

    # Join edge point lists, removing duplicate connection vertices.
    boundary = []
    for edge_i, pts in enumerate(loop_edges):
        if edge_i == 0:
            boundary.extend(pts)
        else:
            if boundary and (boundary[-1] - pts[0]).length < 1e-5:
                boundary.extend(pts[1:])
            else:
                boundary.extend(pts)

    # Close loop if last point is same as first; do not duplicate it for face.
    if len(boundary) >= 2 and (boundary[0] - boundary[-1]).length < 1e-5:
        boundary = boundary[:-1]

    # Remove accidental duplicate consecutive points.
    clean = []
    for p in boundary:
        if not clean or (p - clean[-1]).length > 1e-6:
            clean.append(p)

    boundary = clean

    if len(boundary) < 3:
        return False, "Hippo3D_EdgeSurface: could not build a valid boundary loop."

    # Ensure face winding is not a bow-tie by sorting only if the ordered chain
    # failed to close plausibly. For normal joined edges, the chain order is used.
    close_gap = (boundary[0] - boundary[-1]).length
    avg_seg = sum((b - a).length for a, b in zip(boundary[:-1], boundary[1:])) / max(1, len(boundary) - 1)

    if close_gap > max(avg_seg * 3.0, 1e-4):
        # Fallback: angular sort in active CPlane around centroid.
        import math as _math
        origin, u, v, nrm = get_cplane_axes(context)
        center = sum(boundary, Vector((0, 0, 0))) / len(boundary)

        def angle_key(p):
            q = p - center
            return _math.atan2(q.dot(v), q.dot(u))

        boundary = sorted(boundary, key=angle_key)

    verts = [(p.x, p.y, p.z) for p in boundary]
    edges = [(i, (i + 1) % len(verts)) for i in range(len(verts))]
    face = tuple(range(len(verts)))

    mesh = bpy.data.meshes.new("Hippo3D_EdgeSurface_Mesh")
    mesh.from_pydata(verts, edges, [face])
    mesh.update()

    obj = bpy.data.objects.new("Hippo3D_EdgeSurface", mesh)
    context.collection.objects.link(obj)

    obj["hippo_surface_type"] = "edgesrf"
    obj["cad_surface_type"] = "edgesrf"
    obj["hippo_edgesrf_method"] = "ordered_boundary_fill"
    obj["hippo_edges"] = "|".join(c.name for c in curves)

    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    context.view_layer.objects.active = obj

    return True, f"Created Hippo3D_EdgeSurface boundary face from {len(curves)} curves."

def create_planar_srf(context):
    """Create planar surface like Blender Edit Mode: select all boundary vertices and press F.

    Behaviour:
    - Select one or more closed curve objects.
    - Hippo3D reads the original curve vertices/control points.
    - Converts the boundary to a Mesh object.
    - Creates a single face using all boundary vertices, like Edit Mode > F.
    - Keeps explicit perimeter edges.
    """
    curves = [obj for obj in context.selected_objects if obj.type == "CURVE"]

    if not curves:
        return False, "Hippo3D_PlanarSurface: select at least one closed curve."

    created = 0

    for curve in curves:
        pts = []

        # Prefer original editable curve vertices so the face follows the same
        # vertex structure that Convert To Mesh / F would use.
        try:
            for spl in curve.data.splines:
                if spl.type in {"POLY", "NURBS"}:
                    pts = [curve.matrix_world @ Vector((p.co.x, p.co.y, p.co.z)) for p in spl.points]
                elif spl.type == "BEZIER":
                    pts = [curve.matrix_world @ p.co for p in spl.bezier_points]

                if pts:
                    break
        except Exception:
            pts = []

        # Fallback to sampled curve points if original vertices are unavailable.
        if not pts:
            pts = sample_curve_object_points(
                curve,
                samples=int(getattr(context.scene, "cad_surface_samples", 64)),
            )

        if len(pts) >= 2 and (pts[0] - pts[-1]).length < 1e-6:
            pts = pts[:-1]

        if len(pts) < 3:
            continue

        verts = [(p.x, p.y, p.z) for p in pts]
        edges = [(i, (i + 1) % len(verts)) for i in range(len(verts))]
        face = tuple(range(len(verts)))

        mesh = bpy.data.meshes.new("Hippo3D_PlanarSurface_Mesh")
        mesh.from_pydata(verts, edges, [face])
        mesh.update()

        obj = bpy.data.objects.new("Hippo3D_PlanarSurface", mesh)
        context.collection.objects.link(obj)

        obj["hippo_surface_type"] = "planarsrf"
        obj["cad_surface_type"] = "planarsrf"
        obj["hippo_source"] = curve.name
        obj["hippo_planarsrf_method"] = "mesh_fill_face_like_edit_mode_F"

        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        context.view_layer.objects.active = obj

        created += 1

    if created == 0:
        return False, "Hippo3D_PlanarSurface failed. Select a closed curve with at least 3 vertices."

    return True, f"Created {created} planar surface(s) using mesh face fill."



def hippo_command_not_ready(name):
    return False, f"{name} command registered as a Rhino-compatible alias, but implementation is scheduled for the next geometry pass."


def run_sweep2_command(context):
    return False, "Sweep2 is temporarily disabled."


def run_edgesrf_command(context):
    return create_edge_srf(context)


def run_planarsrf_command(context):
    return create_planar_srf(context)


def run_extrude_command(context):
    return create_extrude_surface_from_curves(context)


def run_pipe_command(context):
    return create_pipe_from_curves(context)


def run_revolve_command(context):
    return create_revolve_surface_from_curves(context)


def run_sweep1_command(context):
    return False, "Sweep1 is temporarily disabled."


class CAD_OT_LoftRealModifier(Operator):
    bl_idname = "cad.loft_real_modifier"
    bl_label = "Loft Modifier"
    bl_description = "Create a loft object with a real Geometry Nodes modifier in Blender's modifier stack."

    def execute(self, context):
        ok, msg = create_loft_with_real_modifier(context)
        self.report({"INFO" if ok else "WARNING"}, msg)
        return {"FINISHED"}





class HIPPO_OT_Hippo3D_EdgeSurface(Operator):
    bl_idname = "cad.edgesrf"
    bl_label = "Edge Surface"
    bl_description = "Create a surface from 2, 3, or 4 selected edge curves."

    def execute(self, context):
        ok, msg = run_edgesrf_command(context)
        self.report({"INFO" if ok else "WARNING"}, msg)
        return {"FINISHED"}


class HIPPO_OT_Hippo3D_PlanarSurface(Operator):
    bl_idname = "cad.planarsrf"
    bl_label = "Planar Surface"
    bl_description = "Create planar mesh surface(s) from selected closed planar curves."

    def execute(self, context):
        ok, msg = run_planarsrf_command(context)
        self.report({"INFO" if ok else "WARNING"}, msg)
        return {"FINISHED"}


class HIPPO_OT_NativeStatus(Operator):
    bl_idname = "cad.hippo_native_status"
    bl_label = "Native Backend Status"

    def execute(self, context):
        if HIPPO_NATIVE_SURFACE_AVAILABLE:
            self.report({"INFO"}, "Hippo3D native C surface backend loaded.")
        else:
            self.report({"WARNING"}, "Native backend not loaded. " + str(HIPPO_NATIVE_SURFACE_ERROR))
        return {"FINISHED"}


class CAD_OT_ExtrudeSurface(Operator):
    bl_idname = "cad.extrude_surface"
    bl_label = "Extrude Surface"
    bl_description = "Extrude selected curve(s) along active CPlane normal."

    def execute(self, context):
        ok, msg = run_extrude_command(context)
        self.report({"INFO" if ok else "WARNING"}, msg)
        return {"FINISHED"}


class CAD_OT_PipeSurface(Operator):
    bl_idname = "cad.pipe_surface"
    bl_label = "Pipe"
    bl_description = "Create pipe(s) from selected curve(s)."

    def execute(self, context):
        ok, msg = run_pipe_command(context)
        self.report({"INFO" if ok else "WARNING"}, msg)
        return {"FINISHED"}


class Hippo3D_OT_Hippo3D_Revolve(Operator):
    bl_idname = "cad.revolve_surface"
    bl_label = "Revolve"
    bl_description = "Revolve selected profile curve(s) around active CPlane Z axis."

    def execute(self, context):
        ok, msg = run_revolve_command(context)
        self.report({"INFO" if ok else "WARNING"}, msg)
        return {"FINISHED"}



class Hippo3D_OT_SetRevolveAxis(Operator):
    bl_idname = "cad.set_revolve_axis"
    bl_label = "Set Revolve Axis"
    bl_description = "Pick two points to define the Revolve axis."

    def execute(self, context):
        bpy.ops.cad.command("INVOKE_DEFAULT", initial_command="revolveaxis")
        return {"FINISHED"}

class Hippo3D_OT_ClearRevolveAxis(Operator):
    bl_idname = "cad.clear_revolve_axis"
    bl_label = "Clear Revolve Axis"

    def execute(self, context):
        ok, msg = clear_revolve_axis(context)
        self.report({"INFO" if ok else "WARNING"}, msg)
        return {"FINISHED"}




class HIPPO_OT_Polygon(Operator):
    bl_idname = "cad.polygon"
    bl_label = "Polygon"

    def execute(self, context):
        bpy.ops.cad.command("INVOKE_DEFAULT", initial_command="polygon")
        return {"FINISHED"}

class HIPPO_OT_Trim(Operator):
    bl_idname = "cad.trim"
    bl_label = "Trim"

    def execute(self, context):
        ok, msg = run_trim_command(context)
        self.report({"INFO" if ok else "WARNING"}, msg)
        return {"FINISHED"}




# -----------------------------------------------------------------------------
# NURBS preservation helpers
# -----------------------------------------------------------------------------

def make_nurbs_curve_from_points(
    context,
    points,
    name="Hippo3D_NURBS_Curve",
    degree=3,
    cyclic=False,
    resolution_u=24,
):
    """Create a NURBS curve from world-space points.

    This preserves the *curve type* as NURBS for commands such as Offset.
    It is not yet a full mathematical NURBS fitting algorithm, but it keeps
    the result smooth and editable as a NURBS object instead of collapsing to POLY.
    """
    if not points or len(points) < 2:
        return None

    degree = int(max(1, min(int(degree), max(1, len(points) - 1))))

    curve = bpy.data.curves.new(name, type="CURVE")
    curve.dimensions = "3D"
    curve.resolution_u = int(max(1, resolution_u))

    spl = curve.splines.new("NURBS")
    spl.points.add(len(points) - 1)

    for pnt, co in zip(spl.points, points):
        pnt.co = (co.x, co.y, co.z, 1.0)

    spl.order_u = degree + 1
    spl.use_endpoint_u = True
    spl.use_cyclic_u = bool(cyclic)

    obj = bpy.data.objects.new(name, curve)
    context.collection.objects.link(obj)

    obj["hippo_shape"] = "nurbs_curve"
    obj["hippo_degree"] = degree
    obj["hippo_preserved_curve_type"] = "NURBS"

    return obj


def hippo_source_curve_info(obj):
    """Return basic source spline metadata for preservation."""
    info = {
        "type": "UNKNOWN",
        "degree": 3,
        "cyclic": False,
        "resolution_u": 24,
    }

    if obj is None or obj.type != "CURVE":
        return info

    try:
        info["resolution_u"] = int(getattr(obj.data, "resolution_u", 24))
    except Exception:
        pass

    try:
        if obj.data.splines:
            spl = obj.data.splines[0]
            info["type"] = spl.type
            info["cyclic"] = bool(getattr(spl, "use_cyclic_u", False))

            if spl.type == "NURBS":
                info["degree"] = max(1, int(getattr(spl, "order_u", 4)) - 1)
            elif spl.type == "POLY":
                info["degree"] = 1
            else:
                info["degree"] = 3
    except Exception:
        pass

    return info


# -----------------------------------------------------------------------------
# NURBS-aware Offset Fix
# -----------------------------------------------------------------------------

def hippo_curve_points_for_offset(obj, context):
    """Return points for offset.

    - POLY curves: use original editable points to preserve segment count.
    - NURBS / BEZIER: use evaluated sampled points because offsetting control
      points does not offset the actual curve shape.
    """
    if obj is None or obj.type != "CURVE":
        return [], False, "UNKNOWN"

    # Detect first spline type.
    spline_type = "UNKNOWN"
    cyclic = False
    try:
        if obj.data.splines:
            spline_type = obj.data.splines[0].type
            cyclic = bool(getattr(obj.data.splines[0], "use_cyclic_u", False))
    except Exception:
        pass

    if spline_type == "POLY":
        pts = []
        try:
            spl = obj.data.splines[0]
            pts = [obj.matrix_world @ Vector((p.co.x, p.co.y, p.co.z)) for p in spl.points]
            cyclic = bool(getattr(spl, "use_cyclic_u", False))
        except Exception:
            pts = []

        if len(pts) >= 2 and (pts[0] - pts[-1]).length < 1e-6:
            pts = pts[:-1]
            cyclic = True

        return pts, cyclic, "POLY_ORIGINAL"

    # NURBS/Bezier/evaluated curves: sample actual displayed curve.
    samples = int(getattr(context.scene, "cad_surface_samples", 64))
    samples = max(samples, 64)

    pts = sample_curve_object_points(obj, samples=samples)

    if len(pts) >= 2 and (pts[0] - pts[-1]).length < 1e-6:
        pts = pts[:-1]
        cyclic = True

    return pts, cyclic, spline_type


def run_offset_command(context):
    """Offset selected curves while preserving curve representation where possible.

    - POLY source -> POLY offset, original point count preserved.
    - NURBS source -> NURBS offset, rebuilt as a NURBS spline with preserved degree.
    - BEZIER/other -> smooth NURBS approximation from sampled offset points.
    """
    objs = [obj for obj in context.selected_objects if obj.type == "CURVE"]

    if not objs:
        return False, "Offset: select curve objects."

    dist = float(getattr(context.scene, "hippo_offset_distance", 0.2))
    created = 0

    for obj in objs:
        source_info = hippo_source_curve_info(obj)
        pts, cyclic, source_type = hippo_curve_points_for_offset(obj, context)

        if len(pts) < 2:
            continue

        origin, u, v, n = get_cplane_axes(context)
        total = len(pts)
        result = []

        for i, p in enumerate(pts):
            if cyclic:
                p_prev = pts[(i - 1) % total]
                p_next = pts[(i + 1) % total]
            else:
                p_prev = pts[max(i - 1, 0)]
                p_next = pts[min(i + 1, total - 1)]

            tangent = p_next - p_prev

            if tangent.length < 1e-8:
                if i < total - 1:
                    tangent = pts[i + 1] - p
                elif i > 0:
                    tangent = p - pts[i - 1]

            if tangent.length < 1e-8:
                result.append(p.copy())
                continue

            tangent.normalize()
            perp = n.cross(tangent)

            if perp.length < 1e-8:
                result.append(p.copy())
                continue

            perp.normalize()
            result.append(p + perp * dist)

        if len(result) < 2:
            continue

        if source_info["type"] == "POLY":
            new_obj = make_poly_curve_from_points(
                context,
                result,
                name=f"{obj.name}_Offset",
                cyclic=cyclic,
            )
        else:
            # Preserve NURBS-like smooth editable result.
            new_obj = make_nurbs_curve_from_points(
                context,
                result,
                name=f"{obj.name}_Offset",
                degree=source_info.get("degree", 3),
                cyclic=cyclic,
                resolution_u=source_info.get("resolution_u", 24),
            )

        if new_obj:
            new_obj["hippo_command"] = "offset"
            new_obj["hippo_offset_distance"] = dist
            new_obj["hippo_source"] = obj.name
            new_obj["hippo_offset_source_type"] = source_info["type"]
            new_obj["hippo_offset_points"] = len(result)
            created += 1

    if created == 0:
        return False, "Offset failed."

    return True, f"Created {created} offset curve(s), preserving curve type where possible."




# -----------------------------------------------------------------------------
# Strong NURBS-aware Offset Override
# -----------------------------------------------------------------------------

def hippo_get_source_spline_info(obj):
    """Read the real Blender spline type/degree from the selected object."""
    info = {
        "type": "UNKNOWN",
        "degree": 3,
        "order": 4,
        "cyclic": False,
        "resolution_u": 24,
    }

    if obj is None or obj.type != "CURVE":
        return info

    try:
        info["resolution_u"] = int(getattr(obj.data, "resolution_u", 24))
    except Exception:
        pass

    try:
        if not obj.data.splines:
            return info

        spl = obj.data.splines[0]
        info["type"] = spl.type
        info["cyclic"] = bool(getattr(spl, "use_cyclic_u", False))

        if spl.type == "NURBS":
            order = int(getattr(spl, "order_u", 4))
            info["order"] = order
            info["degree"] = max(1, order - 1)
        elif spl.type == "POLY":
            info["degree"] = 1
            info["order"] = 2
        elif spl.type == "BEZIER":
            info["degree"] = 3
            info["order"] = 4

    except Exception:
        pass

    return info


def make_same_degree_nurbs_curve_from_points(context, points, source_info, name="Hippo3D_NURBS_Offset"):
    """Create a NURBS curve using the same degree/order as the source NURBS."""
    if not points or len(points) < 2:
        return None

    source_degree = int(source_info.get("degree", 3))
    degree = max(1, min(source_degree, len(points) - 1))
    order = degree + 1

    curve = bpy.data.curves.new(name, type="CURVE")
    curve.dimensions = "3D"
    curve.resolution_u = int(max(1, source_info.get("resolution_u", 24)))

    spl = curve.splines.new("NURBS")
    spl.points.add(len(points) - 1)

    for pnt, co in zip(spl.points, points):
        pnt.co = (co.x, co.y, co.z, 1.0)

    spl.order_u = order
    spl.use_endpoint_u = True
    spl.use_cyclic_u = bool(source_info.get("cyclic", False))

    obj = bpy.data.objects.new(name, curve)
    context.collection.objects.link(obj)

    obj["hippo_shape"] = "nurbs_curve"
    obj["hippo_preserved_curve_type"] = "NURBS"
    obj["hippo_source_degree"] = source_degree
    obj["hippo_result_degree"] = degree
    obj["hippo_result_order_u"] = order

    return obj


def hippo_points_for_offset_by_source_type(obj, context, source_info):
    """Return offset input points based on real source curve type.

    POLY: original editable vertices, preserving segment count.
    NURBS: evaluated curve samples, because control-point offset is geometrically wrong.
           Result is rebuilt as NURBS with the same degree.
    """
    cyclic = bool(source_info.get("cyclic", False))

    if source_info.get("type") == "POLY":
        try:
            spl = obj.data.splines[0]
            pts = [obj.matrix_world @ Vector((p.co.x, p.co.y, p.co.z)) for p in spl.points]
            if len(pts) >= 2 and (pts[0] - pts[-1]).length < 1e-6:
                pts = pts[:-1]
                cyclic = True
            return pts, cyclic
        except Exception:
            return [], cyclic

    # For NURBS and Bezier, sample the displayed/evaluated curve.
    samples = int(getattr(context.scene, "cad_surface_samples", 64))
    samples = max(samples, 64)

    pts = sample_curve_object_points(obj, samples=samples)

    if len(pts) >= 2 and (pts[0] - pts[-1]).length < 1e-6:
        pts = pts[:-1]
        cyclic = True

    return pts, cyclic


def run_offset_command(context):
    """Offset selected curves with explicit NURBS preservation.

    If source is NURBS:
    - detect source as NURBS from obj.data.splines[0].type
    - read degree from order_u - 1
    - offset sampled evaluated curve points
    - rebuild the result as NURBS with the same degree/order
    """
    objs = [obj for obj in context.selected_objects if obj.type == "CURVE"]

    if not objs:
        return False, "Offset: select curve objects."

    dist = float(getattr(context.scene, "hippo_offset_distance", 0.2))
    created = 0

    for obj in objs:
        source_info = hippo_get_source_spline_info(obj)
        pts, cyclic = hippo_points_for_offset_by_source_type(obj, context, source_info)

        if len(pts) < 2:
            continue

        origin, u, v, n = get_cplane_axes(context)
        total = len(pts)
        result = []

        for i, p in enumerate(pts):
            if cyclic:
                p_prev = pts[(i - 1) % total]
                p_next = pts[(i + 1) % total]
            else:
                p_prev = pts[max(i - 1, 0)]
                p_next = pts[min(i + 1, total - 1)]

            tangent = p_next - p_prev

            if tangent.length < 1e-8:
                if i < total - 1:
                    tangent = pts[i + 1] - p
                elif i > 0:
                    tangent = p - pts[i - 1]

            if tangent.length < 1e-8:
                result.append(p.copy())
                continue

            tangent.normalize()
            perp = n.cross(tangent)

            if perp.length < 1e-8:
                result.append(p.copy())
                continue

            perp.normalize()
            result.append(p + perp * dist)

        if len(result) < 2:
            continue

        if source_info.get("type") == "NURBS":
            new_obj = make_same_degree_nurbs_curve_from_points(
                context,
                result,
                source_info,
                name=f"{obj.name}_NURBS_Offset",
            )
        elif source_info.get("type") == "POLY":
            new_obj = make_poly_curve_from_points(
                context,
                result,
                name=f"{obj.name}_Offset",
                cyclic=cyclic,
            )
        else:
            # Bezier/unknown: keep smooth editable curve as degree-3 NURBS approximation.
            approx_info = dict(source_info)
            approx_info["type"] = "NURBS"
            approx_info["degree"] = 3
            approx_info["order"] = 4
            new_obj = make_same_degree_nurbs_curve_from_points(
                context,
                result,
                approx_info,
                name=f"{obj.name}_Offset",
            )

        if new_obj:
            new_obj["hippo_command"] = "offset"
            new_obj["hippo_offset_distance"] = dist
            new_obj["hippo_source"] = obj.name
            new_obj["hippo_source_curve_type"] = source_info.get("type", "UNKNOWN")
            new_obj["hippo_source_degree"] = int(source_info.get("degree", 3))
            created += 1

    if created == 0:
        return False, "Offset failed."

    return True, f"Created {created} offset curve(s)."



class HIPPO_OT_Offset(Operator):
    bl_idname = "cad.offset"
    bl_label = "Offset"
    def execute(self, context):
        ok, msg = run_offset_command(context)
        self.report({"INFO" if ok else "WARNING"}, msg)
        return {"FINISHED"}

class HIPPO_OT_XLine(Operator):
    bl_idname = "cad.xline"
    bl_label = "XLine"
    def execute(self, context):
        bpy.ops.cad.command("INVOKE_DEFAULT", initial_command="xline")
        return {"FINISHED"}

class HIPPO_OT_Explode(Operator):
    bl_idname = "cad.explode"
    bl_label = "Explode"
    def execute(self, context):
        ok, msg = run_explode_command(context)
        self.report({"INFO" if ok else "WARNING"}, msg)
        return {"FINISHED"}


# -----------------------------------------------------------------------------
# Array command helper fix
# -----------------------------------------------------------------------------

def run_array_command(context):
    """Create a simple linear array of selected objects.

    Uses:
    - hippo_array_count
    - hippo_array_dx
    - hippo_array_dy
    - hippo_array_dz
    """
    objs = list(context.selected_objects)

    if not objs:
        return False, "Array: select one or more objects."

    count = int(getattr(context.scene, "hippo_array_count", 5))
    dx = float(getattr(context.scene, "hippo_array_dx", 2.0))
    dy = float(getattr(context.scene, "hippo_array_dy", 0.0))
    dz = float(getattr(context.scene, "hippo_array_dz", 0.0))

    count = max(1, count)
    offset = Vector((dx, dy, dz))

    created = 0

    for obj in objs:
        for i in range(1, count):
            dup = obj.copy()

            if getattr(obj, "data", None) is not None:
                try:
                    dup.data = obj.data.copy()
                except Exception:
                    dup.data = obj.data

            dup.location = obj.location + offset * i
            dup.name = obj.name + f"_Array_{i:03d}"
            context.collection.objects.link(dup)
            created += 1

    return True, f"Array created {created} copied object(s)."


class HIPPO_OT_Array(Operator):
    bl_idname = "cad.array"
    bl_label = "Array"
    def execute(self, context):
        ok, msg = run_array_command(context)
        self.report({"INFO" if ok else "WARNING"}, msg)
        return {"FINISHED"}

class HIPPO_OT_Project(Operator):
    bl_idname = "cad.project"
    bl_label = "Project"
    def execute(self, context):
        ok, msg = run_project_command(context)
        self.report({"INFO" if ok else "WARNING"}, msg)
        return {"FINISHED"}

class HIPPO_OT_Ellipse(Operator):
    bl_idname = "cad.ellipse"
    bl_label = "Ellipse"
    def execute(self, context):
        bpy.ops.cad.command("INVOKE_DEFAULT", initial_command="ellipse")
        return {"FINISHED"}

class HIPPO_OT_StartArc(Operator):
    bl_idname = "cad.start_arc"
    bl_label = "Arc"
    def execute(self, context):
        bpy.ops.cad.command("INVOKE_DEFAULT", initial_command="arc")
        return {"FINISHED"}



# -----------------------------------------------------------------------------
# Corrected Polygon 2Pt + Segment-Preserving Offset
# -----------------------------------------------------------------------------

def make_poly_curve_from_points(context, points, name="Hippo3D_Curve", cyclic=False):
    if not points or len(points) < 2:
        return None

    curve = bpy.data.curves.new(name, type="CURVE")
    curve.dimensions = "3D"
    curve.resolution_u = 12

    spl = curve.splines.new("POLY")
    spl.points.add(len(points) - 1)

    for pnt, co in zip(spl.points, points):
        pnt.co = (co.x, co.y, co.z, 1.0)

    spl.use_cyclic_u = bool(cyclic)

    obj = bpy.data.objects.new(name, curve)
    context.collection.objects.link(obj)
    obj["hippo_shape"] = "curve"

    return obj


def hippo_original_curve_points(obj):
    if obj is None or obj.type != "CURVE":
        return [], False

    for spl in obj.data.splines:
        pts = []
        cyclic = bool(getattr(spl, "use_cyclic_u", False))

        if spl.type in {"POLY", "NURBS"}:
            pts = [obj.matrix_world @ Vector((p.co.x, p.co.y, p.co.z)) for p in spl.points]
        elif spl.type == "BEZIER":
            pts = [obj.matrix_world @ p.co for p in spl.bezier_points]

        if pts:
            if len(pts) >= 2 and (pts[0] - pts[-1]).length < 1e-6:
                pts = pts[:-1]
                cyclic = True
            return pts, cyclic

    return [], False


def run_offset_command(context):
    """Offset selected curves while preserving the original editable point count."""
    objs = list(context.selected_objects)
    if not objs:
        return False, "Offset: select curve objects."

    dist = float(getattr(context.scene, "hippo_offset_distance", 0.2))
    created = 0

    for obj in objs:
        if obj.type != "CURVE":
            continue

        pts, cyclic = hippo_original_curve_points(obj)

        if len(pts) < 2:
            continue

        origin, u, v, n = get_cplane_axes(context)
        total = len(pts)
        result = []

        for i, p in enumerate(pts):
            if cyclic:
                p_prev = pts[(i - 1) % total]
                p_next = pts[(i + 1) % total]
            else:
                p_prev = pts[max(i - 1, 0)]
                p_next = pts[min(i + 1, total - 1)]

            tangent = p_next - p_prev

            if tangent.length < 1e-8:
                if i < total - 1:
                    tangent = pts[i + 1] - p
                elif i > 0:
                    tangent = p - pts[i - 1]

            if tangent.length < 1e-8:
                result.append(p.copy())
                continue

            tangent.normalize()
            perp = n.cross(tangent)

            if perp.length < 1e-8:
                result.append(p.copy())
                continue

            perp.normalize()
            result.append(p + perp * dist)

        if len(result) < 2:
            continue

        new_obj = make_poly_curve_from_points(
            context,
            result,
            name=f"{obj.name}_Offset",
            cyclic=cyclic,
        )

        if new_obj:
            new_obj["hippo_command"] = "offset"
            new_obj["hippo_offset_distance"] = dist
            new_obj["hippo_source"] = obj.name
            new_obj["hippo_preserved_point_count"] = len(result)
            created += 1

    if created == 0:
        return False, "Offset failed."

    return True, f"Created {created} offset curve(s), preserving source segment count."


def create_polygon_curve(context, center=None, radius=None, sides=None):
    import math as _math

    sides = int(sides if sides is not None else getattr(context.scene, "hippo_polygon_sides", 6))
    radius = float(radius if radius is not None else getattr(context.scene, "hippo_polygon_radius", 2.0))

    sides = max(3, min(sides, 256))
    radius = max(0.001, radius)

    origin, u, v, n = get_cplane_axes(context)
    center = center or origin

    pts = []
    for i in range(sides):
        a = 2.0 * _math.pi * i / sides
        pts.append(center + u * (_math.cos(a) * radius) + v * (_math.sin(a) * radius))

    obj = make_poly_curve_from_points(context, pts, name="Hippo3D_Polygon", cyclic=True)

    if obj:
        obj["hippo_shape"] = "polygon"
        obj["hippo_polygon_sides"] = sides
        obj["hippo_polygon_radius"] = radius

    return obj


def create_polygon_from_2_points(context, p0, p1):
    radius = (p1 - p0).length

    if radius < 1e-8:
        return None

    return create_polygon_curve(context, center=p0, radius=radius)


def run_polygon_command(context):
    return False, "Polygon is interactive. Type Polygon, then pick centre point and radius point."




# -----------------------------------------------------------------------------
# Trim + Fillet first-pass implementations
# -----------------------------------------------------------------------------

def hippo_curve_polyline_points(obj):
    """Return original curve points, preserving simple segment structure."""
    if obj is None or obj.type != "CURVE":
        return [], False

    if "hippo_original_curve_points" in globals():
        try:
            return hippo_original_curve_points(obj)
        except Exception:
            pass

    for spl in obj.data.splines:
        cyclic = bool(getattr(spl, "use_cyclic_u", False))

        if spl.type in {"POLY", "NURBS"}:
            pts = [obj.matrix_world @ Vector((p.co.x, p.co.y, p.co.z)) for p in spl.points]
        elif spl.type == "BEZIER":
            pts = [obj.matrix_world @ p.co for p in spl.bezier_points]
        else:
            pts = []

        if pts:
            if len(pts) >= 2 and (pts[0] - pts[-1]).length < 1e-6:
                pts = pts[:-1]
                cyclic = True
            return pts, cyclic

    return [], False


def hippo_segment_intersection_2d(a, b, c, d, origin, u, v, tol=1e-8):
    """Return intersection point and params for two 3D segments projected to CPlane."""
    def to2(p):
        q = p - origin
        return Vector((q.dot(u), q.dot(v)))

    a2, b2, c2, d2 = to2(a), to2(b), to2(c), to2(d)
    r = b2 - a2
    s = d2 - c2

    denom = r.x * s.y - r.y * s.x
    if abs(denom) < tol:
        return None

    q = c2 - a2
    t = (q.x * s.y - q.y * s.x) / denom
    w = (q.x * r.y - q.y * r.x) / denom

    if -tol <= t <= 1.0 + tol and -tol <= w <= 1.0 + tol:
        p = a.lerp(b, max(0.0, min(1.0, t)))
        return p, t, w

    return None


def run_trim_command(context):
    """First-pass Trim.

    Behaviour:
    - Select 2+ curve objects.
    - Finds curve/curve intersections in active CPlane.
    - Splits curves at intersections.
    - Keeps the longest resulting piece per original curve.
    This is not final Rhino Trim UX yet, but it performs a useful trim-like split/keep operation.
    """
    curves = [obj for obj in context.selected_objects if obj.type == "CURVE"]

    if len(curves) < 2:
        return False, "Trim: select at least two intersecting curves."

    origin, u, v, n = get_cplane_axes(context)
    tol = float(getattr(context.scene, "hippo_trim_tolerance", 0.05))

    created = 0
    removed = []

    for obj in curves:
        pts, cyclic = hippo_curve_polyline_points(obj)
        if len(pts) < 2:
            continue

        split_items = [(0, 0.0, pts[0]), (len(pts) - 2, 1.0, pts[-1])]

        segments = list(zip(pts[:-1], pts[1:]))

        for other in curves:
            if other == obj:
                continue

            other_pts, other_cyclic = hippo_curve_polyline_points(other)
            if len(other_pts) < 2:
                continue

            other_segments = list(zip(other_pts[:-1], other_pts[1:]))

            for i, (a, b) in enumerate(segments):
                for c, d in other_segments:
                    hit = hippo_segment_intersection_2d(a, b, c, d, origin, u, v, tol=1e-8)
                    if hit:
                        p, t, w = hit
                        if tol < (p - a).length and tol < (p - b).length:
                            split_items.append((i, t, p))

        if len(split_items) <= 2:
            continue

        # Sort by segment index + segment t.
        split_items.sort(key=lambda x: (x[0], x[1]))

        # Build split polylines.
        pieces = []
        current = [split_items[0][2]]

        for idx in range(1, len(split_items)):
            prev_seg, prev_t, prev_p = split_items[idx - 1]
            seg_i, seg_t, p = split_items[idx]

            current = [prev_p]

            # Add original internal vertices between split points.
            start_i = prev_seg + 1
            end_i = seg_i + 1
            for vi in range(start_i, end_i):
                if 0 <= vi < len(pts):
                    current.append(pts[vi])

            current.append(p)

            # avoid tiny pieces
            length = sum((b - a).length for a, b in zip(current[:-1], current[1:]))
            if len(current) >= 2 and length > tol:
                pieces.append((length, current))

        if not pieces:
            continue

        # Keep the longest piece for first-pass trim.
        pieces.sort(key=lambda x: x[0], reverse=True)
        keep = pieces[0][1]

        new_obj = make_poly_curve_from_points(context, keep, name=obj.name + "_Trimmed", cyclic=False)
        if new_obj:
            new_obj["hippo_command"] = "trim"
            new_obj["hippo_source"] = obj.name
            created += 1
            removed.append(obj)

    for obj in removed:
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
        except Exception:
            pass

    if created == 0:
        return False, "Trim found no usable intersections."

    return True, f"Trim created {created} trimmed curve(s)."


def hippo_nearest_curve_ends(obj_a, obj_b):
    pts_a, _ = hippo_curve_polyline_points(obj_a)
    pts_b, _ = hippo_curve_polyline_points(obj_b)

    if len(pts_a) < 2 or len(pts_b) < 2:
        return None

    candidates = [
        (pts_a[0], pts_a[1], "a_start", pts_b[0], pts_b[1], "b_start"),
        (pts_a[0], pts_a[1], "a_start", pts_b[-1], pts_b[-2], "b_end"),
        (pts_a[-1], pts_a[-2], "a_end", pts_b[0], pts_b[1], "b_start"),
        (pts_a[-1], pts_a[-2], "a_end", pts_b[-1], pts_b[-2], "b_end")]

    best = None
    for a_end, a_next, a_tag, b_end, b_next, b_tag in candidates:
        dist = (a_end - b_end).length
        if best is None or dist < best[0]:
            best = (dist, a_end, a_next, a_tag, b_end, b_next, b_tag)

    return best


def create_arc_polyline_from_center(context, center, radius, start_vec, end_vec, segments=24):
    import math as _math

    origin, u, v, n = get_cplane_axes(context)

    def angle_of(vec):
        return _math.atan2(vec.dot(v), vec.dot(u))

    a0 = angle_of(start_vec)
    a1 = angle_of(end_vec)

    # choose shorter angular path
    da = a1 - a0
    while da > _math.pi:
        da -= 2 * _math.pi
    while da < -_math.pi:
        da += 2 * _math.pi

    pts = []
    for i in range(segments + 1):
        t = i / segments
        a = a0 + da * t
        pts.append(center + u * (_math.cos(a) * radius) + v * (_math.sin(a) * radius))

    return make_poly_curve_from_points(context, pts, name="Hippo3D_Fillet", cyclic=False)


def run_fillet_command(context):
    """First-pass Fillet for two selected curve ends.

    Select two line/polyline curves. Hippo3D finds the closest pair of ends and
    creates a radius arc tangent approximation between them.
    """
    curves = [obj for obj in context.selected_objects if obj.type == "CURVE"]

    if len(curves) != 2:
        return False, "Fillet: select exactly two curve objects."

    radius = float(getattr(context.scene, "hippo_fillet_radius", 1.0))
    if radius <= 0:
        return False, "Fillet radius must be positive."

    best = hippo_nearest_curve_ends(curves[0], curves[1])
    if best is None:
        return False, "Fillet: could not read selected curve ends."

    _, a_end, a_next, a_tag, b_end, b_next, b_tag = best

    dir_a = (a_next - a_end)
    dir_b = (b_next - b_end)

    if dir_a.length < 1e-8 or dir_b.length < 1e-8:
        return False, "Fillet: curve end direction is invalid."

    dir_a.normalize()
    dir_b.normalize()

    # Tangency points measured away from closest endpoints.
    tan_a = a_end + dir_a * radius
    tan_b = b_end + dir_b * radius

    # Approximate center as midpoint offset from chord.
    chord = tan_b - tan_a
    if chord.length < 1e-8:
        return False, "Fillet: selected curve ends are too close."

    center = (tan_a + tan_b) * 0.5

    # Use average distance from center as arc radius.
    arc_radius = max((tan_a - center).length, (tan_b - center).length)
    if arc_radius < 1e-8:
        return False, "Fillet radius too small."

    obj = create_arc_polyline_from_center(
        context,
        center,
        arc_radius,
        tan_a - center,
        tan_b - center,
        segments=24,
    )

    if not obj:
        return False, "Fillet failed."

    obj["hippo_command"] = "fillet"
    obj["hippo_fillet_radius"] = radius
    obj["hippo_sources"] = curves[0].name + "|" + curves[1].name

    return True, "Created fillet arc."



# -----------------------------------------------------------------------------
# Ellipse 2Pt helper fix
# -----------------------------------------------------------------------------

def create_ellipse_curve(context, center=None, rx=None, ry=None, segments=96, axis_dir=None):
    import math as _math

    origin, u, v, n = get_cplane_axes(context)
    center = center or origin

    rx = float(rx if rx is not None else getattr(context.scene, "hippo_ellipse_rx", 2.0))
    ry = float(ry if ry is not None else getattr(context.scene, "hippo_ellipse_ry", 1.0))

    if axis_dir is not None and axis_dir.length > 1e-8:
        eu = axis_dir.normalized()
        ev = n.cross(eu)

        if ev.length < 1e-8:
            ev = v.copy()
        else:
            ev.normalize()
    else:
        eu = u
        ev = v

    pts = []
    for i in range(segments):
        a = 2.0 * _math.pi * i / segments
        pts.append(center + eu * (_math.cos(a) * rx) + ev * (_math.sin(a) * ry))

    obj = make_poly_curve_from_points(context, pts, name="Hippo3D_Ellipse", cyclic=True)
    if obj:
        obj["hippo_shape"] = "ellipse"
        obj["hippo_ellipse_rx"] = rx
        obj["hippo_ellipse_ry"] = ry

    return obj


def create_ellipse_from_2_points(context, p0, p1):
    axis = p1 - p0
    rx = axis.length

    if rx < 1e-8:
        return None

    # Secondary radius from UI. If not set, use half of primary radius.
    ry = float(getattr(context.scene, "hippo_ellipse_ry", rx * 0.5))

    return create_ellipse_curve(
        context,
        center=p0,
        rx=rx,
        ry=ry,
        axis_dir=axis,
    )


class Hippo3D_PT_MainPanel(Panel):
    bl_label = "Hippo3D"
    bl_idname = "Hippo3D_PT_main_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Hippo3D"

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        col.label(text="Commands")
        col.operator("cad.start_command", text="Hippo Command Line  Ctrl+/", icon="CONSOLE")
        col.separator()
        col.label(text="Curve Creation")
        col.operator("cad.start_line", text="Line", icon="CURVE_PATH")
        col.operator("cad.start_polyline", text="Polyline", icon="IPO_LINEAR")
        col.operator("cad.start_rectangle", text="Rectangle", icon="MESH_PLANE")
        col.operator("cad.start_circle", text="Circle", icon="MESH_CIRCLE")
        col.operator("cad.start_arc", text="Arc")
        col.prop(context.scene, "hippo_ellipse_ry", text="Ellipse Secondary Radius")
        col.operator("cad.ellipse", text="Ellipse")
        col.prop(context.scene, "hippo_polygon_sides", text="Polygon Sides")
        col.operator("cad.polygon", text="Polygon")
        col.operator("cad.start_nurbs", text="NURBS Curve", icon="CURVE_BEZCURVE")
        col.prop(context.scene, "hippo_xline_length", text="XLine Length")
        col.operator("cad.xline", text="XLine")

        col.separator()
        col.label(text="Curve Modification")
        col.prop(context.scene, "hippo_offset_distance", text="Offset Distance")
        col.operator("cad.offset", text="Offset")
        col.prop(context.scene, "hippo_trim_tolerance", text="Trim Tolerance")
        col.operator("cad.trim", text="Trim")
        col.operator("cad.explode", text="Explode")
        col.operator("cad.project", text="Project")
        col.prop(context.scene, "cad_nurbs_degree", text="NURBS Degree")
        col.prop(context.scene, "cad_selected_nurbs_degree", text="Selected Degree")
        col.operator("cad.set_selected_nurbs_degree", text="Set Selected Degree")
        col.operator("cad.convert_to_mesh", text="Convert to Mesh", icon="MESH_DATA")
        col.operator("cad.join", text="Join", icon="AUTOMERGE_OFF")

        col.separator()
        col.label(text="Object Tools")
        col.prop(context.scene, "hippo_array_count", text="Array Count")
        col.prop(context.scene, "hippo_array_dx", text="Array X")
        col.prop(context.scene, "hippo_array_dy", text="Array Y")
        col.prop(context.scene, "hippo_array_dz", text="Array Z")
        col.operator("cad.array", text="Array")
        col.label(text="Surface-Like Operations")
        col.prop(context.scene, "cad_loft_samples", text="Loft Samples")
        col.operator("cad.loft_surface", text="Loft Surface", icon="SURFACE_DATA")
        col.separator()
        col.prop(context.scene, "cad_surface_samples", text="Samples")
        col.prop(context.scene, "cad_extrude_distance", text="Extrude Distance")
        col.operator("cad.extrude_surface", text="Extrude", icon="MOD_SOLIDIFY")
        col.prop(context.scene, "cad_pipe_radius", text="Pipe Radius")
        col.prop(context.scene, "cad_pipe_resolution", text="Pipe Resolution")
        col.operator("cad.pipe_surface", text="Pipe", icon="CURVE_DATA")
        col.prop(context.scene, "cad_revolve_angle", text="Revolve Degree", slider=True)
        col.prop(context.scene, "cad_revolve_steps", text="Revolve Steps")
        col.operator("cad.set_revolve_axis", text="Set Revolve Axis")
        col.operator("cad.clear_revolve_axis", text="Clear Revolve Axis")
        col.operator("cad.revolve_surface", text="Revolve", icon="MOD_SCREW")
        col.operator("cad.edgesrf", text="Edge Surface", icon="SURFACE_DATA")
        col.operator("cad.planarsrf", text="Planar Surface", icon="MESH_PLANE")
        col.operator("cad.hippo_native_status", text="Native C Backend Status")


        layout.separator()
        box = layout.box()
        box.label(text="CPlanes")

        sync_cplane_dropdown(context)
        box.prop(context.scene, "cad_active_cplane_dropdown", text="Active")

        box.prop(context.scene, "cad_cplane_save_name", text="Name")
        row = box.row(align=True)
        row.operator("cad.save_cplane", text="Save Current")
        row.operator("cad.restore_cplane", text="Restore by Name")
        box.operator("cad.start_cplane_3pt", text="Create 3-Point CPlane")
        box.operator("cad.start_cplane_xaxis", text="Create X-Axis CPlane")
        box.operator("cad.start_cplane_zaxis", text="Create Z-Axis CPlane")
        box.operator("cad.start_cplane_face", text="Create Face CPlane")
        box.operator("cad.start_cplane_curve_perp", text="Create Perp Curve CPlane")

        box.separator()
        box.prop(context.scene, "cad_cplane_rotate_angle", text="Rotate Angle")
        row = box.row(align=True)
        op = row.operator("cad.rotate_cplane", text="Rot X")
        op.axis = "X"
        op = row.operator("cad.rotate_cplane", text="Rot Y")
        op.axis = "Y"
        op = row.operator("cad.rotate_cplane", text="Rot Z")
        op.axis = "Z"
        box.operator("cad.start_cplane_rotate3pt", text="Rotate by 3 Points")
        box.operator("cad.start_cplane_axisrotate", text="Axis Rotate + Slider")
        box.operator("cad.start_cplane_move", text="Move CPlane")

        box.separator()
        box.operator("cad.view_to_cplane", text="View to CPlane")
        box.prop(context.scene, "cad_cplane_camera_distance", text="Camera Distance")
        box.operator("cad.camera_to_cplane", text="Camera to CPlane")
        box.prop(context.scene, "cad_cplane_axis_rotation_angle", text="Axis Angle", slider=True)
        box.operator("cad.apply_cplane_axis_rotation", text="Apply Axis Angle")

        box.separator()
        box.label(text="CPlane Layers")

        if hasattr(context.scene, "cad_cplane_items") and hasattr(context.scene, "cad_cplane_index"):
            box.template_list(
                "Hippo3D_UL_cplane_list",
                "",
                context.scene,
                "cad_cplane_items",
                context.scene,
                "cad_cplane_index",
                rows=7,
            )
        else:
            box.label(text="CPlane list not registered")

        row = box.row(align=True)
        row.operator("cad.delete_selected_cplane", text="Delete Selected", icon="TRASH")

        box.label(text="Cmd: cplane 3pt/save/restore/list/delete")
        box.label(text="Relative input: @x,y,z")

        layout.separator()
        box = layout.box()
        box.label(text="Osnaps")
        row = box.row(align=True)
        row.prop(context.scene, "cad_osnap_endpoint", text="End")
        row.prop(context.scene, "cad_osnap_midpoint", text="Mid")
        row = box.row(align=True)
        row.prop(context.scene, "cad_osnap_nearest", text="Near")
        row.prop(context.scene, "cad_osnap_center", text="Cen")
        row = box.row(align=True)
        row.prop(context.scene, "cad_osnap_grid", text="Grid")
        box.prop(context.scene, "cad_grid_size", text="Grid Size")
        box.prop(context.scene, "cad_snap_radius", text="Snap Radius")

        layout.separator()
        row = layout.row(align=True)
        row.prop(context.scene, "cad_ortho", text="Ortho F8", toggle=True)
        row.operator("cad.toggle_ortho", text="Toggle")

        layout.separator()
        box = layout.box()
        box.label(text="How to use")
        box.label(text="Press /, type line, Enter")
        box.label(text="Commands: line, polyline, rectangle, circle, nurbs")
        box.label(text="Click points or type x,y,z")
        box.label(text="F8 toggles Ortho; Esc exits")

        if state.active:
            layout.separator()
            layout.label(text="Active CAD command:", icon="PLAY")
            layout.label(text=command_label())
            layout.label(text=f"Snap: {state.snap_label or 'none'}")


# -----------------------------------------------------------------------------
# Toolbar tool
# -----------------------------------------------------------------------------

class Hippo3D_WST_LineTool(WorkSpaceTool):
    bl_space_type = "VIEW_3D"
    bl_context_mode = "OBJECT"
    bl_idname = "cad_blender.line_tool"
    bl_label = "Line"
    bl_description = "Start Line command"
    bl_icon = (ICON_DIR / "line").as_posix()
    bl_widget = None
    bl_keymap = (("cad.start_line", {"type": "LEFTMOUSE", "value": "PRESS"}, None),)


class Hippo3D_WST_PolylineTool(WorkSpaceTool):
    bl_space_type = "VIEW_3D"
    bl_context_mode = "OBJECT"
    bl_idname = "cad_blender.polyline_tool"
    bl_label = "Polyline"
    bl_description = "Start Polyline command"
    bl_icon = (ICON_DIR / "polyline").as_posix()
    bl_widget = None
    bl_keymap = (("cad.start_polyline", {"type": "LEFTMOUSE", "value": "PRESS"}, None),)


class Hippo3D_WST_RectangleTool(WorkSpaceTool):
    bl_space_type = "VIEW_3D"
    bl_context_mode = "OBJECT"
    bl_idname = "cad_blender.rectangle_tool"
    bl_label = "Rectangle"
    bl_description = "Start Rectangle command"
    bl_icon = (ICON_DIR / "rectangle").as_posix()
    bl_widget = None
    bl_keymap = (("cad.start_rectangle", {"type": "LEFTMOUSE", "value": "PRESS"}, None),)


class Hippo3D_WST_CircleTool(WorkSpaceTool):
    bl_space_type = "VIEW_3D"
    bl_context_mode = "OBJECT"
    bl_idname = "cad_blender.circle_tool"
    bl_label = "Circle"
    bl_description = "Start Circle command"
    bl_icon = (ICON_DIR / "circle").as_posix()
    bl_widget = None
    bl_keymap = (("cad.start_circle", {"type": "LEFTMOUSE", "value": "PRESS"}, None),)


class Hippo3D_WST_NurbsTool(WorkSpaceTool):
    bl_space_type = "VIEW_3D"
    bl_context_mode = "OBJECT"
    bl_idname = "cad_blender.nurbs_tool"
    bl_label = "NURBS Curve"
    bl_description = "Start NURBS Curve command"
    bl_icon = (ICON_DIR / "nurbs").as_posix()
    bl_widget = None
    bl_keymap = (("cad.start_nurbs", {"type": "LEFTMOUSE", "value": "PRESS"}, None),)




class Hippo3D_WST_ArcTool(WorkSpaceTool):
    bl_space_type = "VIEW_3D"
    bl_context_mode = "OBJECT"
    bl_idname = "hippo3d.arc_tool"
    bl_label = "Arc"
    bl_description = "Start Arc command"
    bl_icon = (ICON_DIR / "arc").as_posix()
    bl_widget = None
    bl_keymap = (("cad.start_arc", {"type": "LEFTMOUSE", "value": "PRESS"}, None),)


class Hippo3D_WST_EllipseTool(WorkSpaceTool):
    bl_space_type = "VIEW_3D"
    bl_context_mode = "OBJECT"
    bl_idname = "hippo3d.ellipse_tool"
    bl_label = "Ellipse"
    bl_description = "Start Ellipse command"
    bl_icon = (ICON_DIR / "ellipse").as_posix()
    bl_widget = None
    bl_keymap = (("cad.ellipse", {"type": "LEFTMOUSE", "value": "PRESS"}, None),)


class Hippo3D_WST_PolygonTool(WorkSpaceTool):
    bl_space_type = "VIEW_3D"
    bl_context_mode = "OBJECT"
    bl_idname = "hippo3d.polygon_tool"
    bl_label = "Polygon"
    bl_description = "Create Polygon"
    bl_icon = (ICON_DIR / "polygon").as_posix()
    bl_widget = None
    bl_keymap = (("cad.polygon", {"type": "LEFTMOUSE", "value": "PRESS"}, None),)


class Hippo3D_WST_XLineTool(WorkSpaceTool):
    bl_space_type = "VIEW_3D"
    bl_context_mode = "OBJECT"
    bl_idname = "hippo3d.xline_tool"
    bl_label = "XLine"
    bl_description = "Start XLine command"
    bl_icon =  (ICON_DIR / "xline").as_posix()
    bl_widget = None
    bl_keymap = (("cad.xline", {"type": "LEFTMOUSE", "value": "PRESS"}, None),)


# -----------------------------------------------------------------------------
# Registration
# -----------------------------------------------------------------------------

addon_keymaps = []



class Hippo3D_OT_ActivateCPlaneExplicit(Operator):
    bl_idname = "cad.activate_cplane_explicit"
    bl_label = "Make CPlane Active"

    name: StringProperty(default="")
    builtin_mode: StringProperty(default="")
    layer_key: StringProperty(default="")

    def execute(self, context):
        key = self.layer_key or ""

        if key.startswith("BUILTIN:"):
            mode = key.split(":", 1)[1]
            set_builtin_cplane(context, mode)
            self.report({"INFO"}, f"Active CPlane: {mode.title()}")
        elif key.startswith("NAMED:"):
            name = key.split(":", 1)[1]
            if set_named_cplane(context, name):
                self.report({"INFO"}, f"Active CPlane: {name}")
            else:
                self.report({"WARNING"}, f"No saved CPlane named '{name}'.")
                return {"CANCELLED"}
        elif self.builtin_mode:
            set_builtin_cplane(context, self.builtin_mode)
            self.report({"INFO"}, f"Active CPlane: {self.builtin_mode.title()}")
        elif self.name:
            if set_named_cplane(context, self.name):
                self.report({"INFO"}, f"Active CPlane: {self.name}")
            else:
                self.report({"WARNING"}, f"No saved CPlane named '{self.name}'.")
                return {"CANCELLED"}

        sync_cplane_dropdown(context)

        for area in context.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()

        return {"FINISHED"}



class Hippo3D_OT_ToggleCPlaneVisibilityExplicit(Operator):
    bl_idname = "cad.toggle_cplane_visibility_explicit"
    bl_label = "Toggle CPlane Visibility"

    name: StringProperty(default="")
    builtin_mode: StringProperty(default="")
    layer_key: StringProperty(default="")

    def execute(self, context):
        key = self.layer_key or ""

        if key.startswith("BUILTIN:"):
            mode = key.split(":", 1)[1]
            current = is_cplane_visible(context, builtin_mode=mode)
            set_cplane_visible(context, not current, builtin_mode=mode)
        elif key.startswith("NAMED:"):
            name = key.split(":", 1)[1]
            current = is_cplane_visible(context, name=name)
            set_cplane_visible(context, not current, name=name)
        elif self.builtin_mode:
            current = is_cplane_visible(context, builtin_mode=self.builtin_mode)
            set_cplane_visible(context, not current, builtin_mode=self.builtin_mode)
        elif self.name:
            current = is_cplane_visible(context, name=self.name)
            set_cplane_visible(context, not current, name=self.name)

        for area in context.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()

        return {"FINISHED"}


classes = [Hippo3D_OT_Command, Hippo3D_OT_StartLine, Hippo3D_OT_StartPolyline, Hippo3D_OT_StartRectangle, Hippo3D_OT_StartCircle, Hippo3D_OT_StartNurbs, Hippo3D_OT_SetSelectedNurbsDegree, Hippo3D_OT_Hippo3D_Loft, CAD_OT_LoftRealModifier, HIPPO_OT_NativeStatus, HIPPO_OT_StartArc, HIPPO_OT_Ellipse, HIPPO_OT_Polygon, HIPPO_OT_Project, HIPPO_OT_Array, HIPPO_OT_Explode, HIPPO_OT_XLine, HIPPO_OT_Offset,  HIPPO_OT_Trim, HIPPO_OT_Hippo3D_PlanarSurface, HIPPO_OT_Hippo3D_EdgeSurface, Hippo3D_OT_Hippo3D_Revolve, Hippo3D_OT_ClearRevolveAxis, Hippo3D_OT_SetRevolveAxis, CAD_OT_PipeSurface, CAD_OT_ExtrudeSurface, Hippo3D_OT_StartCommand, Hippo3D_OT_ToggleOrtho, Hippo3D_OT_ConvertToMesh, Hippo3D_OT_Join, Hippo3D_OT_SaveCPlane, Hippo3D_OT_RestoreCPlane, Hippo3D_OT_StartCPlane3Pt, Hippo3D_OT_StartCPlaneFace, Hippo3D_OT_StartCPlaneCurvePerp, Hippo3D_OT_RotateCPlane, Hippo3D_OT_StartCPlaneRotate3Pt, Hippo3D_OT_ApplyCPlaneAxisRotation, Hippo3D_OT_StartCPlaneAxisRotate, Hippo3D_OT_StartCPlaneMove, Hippo3D_OT_CameraToCPlane, Hippo3D_OT_ViewToCPlane, Hippo3D_OT_StartCPlaneZAxis, Hippo3D_OT_StartCPlaneXAxis, Hippo3D_OT_ToggleCPlaneVisibilityExplicit, Hippo3D_OT_ActivateCPlaneExplicit, Hippo3D_OT_RefreshCPlaneList, Hippo3D_OT_DeleteSelectedCPlane, Hippo3D_OT_ActivateSelectedCPlane, Hippo3D_OT_ToggleSelectedCPlaneVisible, Hippo3D_UL_CPlaneList, Hippo3D_CPlaneListItem, Hippo3D_OT_SetBuiltinCPlane, Hippo3D_OT_RestoreCPlaneByName, Hippo3D_OT_SetCPlaneVisible, Hippo3D_OT_DeleteCPlane, Hippo3D_PT_MainPanel]


def _cad_cplane_enum_update(self, context):
    # Choosing a built-in preset from the UI deactivates any restored named CPlane.
    self.cad_active_cplane_name = ""


def register_props():

    bpy.types.Scene.hippo_fillet_radius = FloatProperty(name="Fillet Radius", default=1.0, min=0.001, soft_max=100.0)
    bpy.types.Scene.hippo_trim_tolerance = FloatProperty(name="Trim Tolerance", default=0.05, min=0.0001, soft_max=10.0)

    bpy.types.Scene.hippo_polygon_sides = IntProperty(name="Polygon Sides", default=6, min=3, max=256)
    bpy.types.Scene.hippo_polygon_radius = FloatProperty(name="Polygon Radius", default=2.0, min=0.001, soft_max=100.0)

    bpy.types.Scene.hippo_offset_distance = FloatProperty(name="Offset Distance", default=1.0, soft_min=-100.0, soft_max=100.0)
    bpy.types.Scene.hippo_xline_length = FloatProperty(name="XLine Length", default=1000.0, min=1.0, soft_max=10000.0)
    bpy.types.Scene.hippo_array_count = IntProperty(name="Array Count", default=5, min=1, max=1000)
    bpy.types.Scene.hippo_array_dx = FloatProperty(name="Array X", default=2.0, soft_min=-100.0, soft_max=100.0)
    bpy.types.Scene.hippo_array_dy = FloatProperty(name="Array Y", default=0.0, soft_min=-100.0, soft_max=100.0)
    bpy.types.Scene.hippo_array_dz = FloatProperty(name="Array Z", default=0.0, soft_min=-100.0, soft_max=100.0)
    bpy.types.Scene.hippo_ellipse_rx = FloatProperty(name="Ellipse Radius X", default=2.0, min=0.001, soft_max=100.0)
    bpy.types.Scene.hippo_ellipse_ry = FloatProperty(name="Ellipse Radius Y", default=1.0, min=0.001, soft_max=100.0)
    bpy.types.Scene.cad_osnap_endpoint = BoolProperty(name="Endpoint", default=True)
    bpy.types.Scene.cad_osnap_midpoint = BoolProperty(name="Midpoint", default=True)
    bpy.types.Scene.cad_osnap_nearest = BoolProperty(name="Nearest", default=True)
    bpy.types.Scene.cad_osnap_center = BoolProperty(name="Center", default=True)
    bpy.types.Scene.cad_osnap_grid = BoolProperty(name="Grid", default=False)
    bpy.types.Scene.cad_ortho = BoolProperty(name="Ortho", default=False)
    bpy.types.Scene.cad_grid_size = FloatProperty(name="Grid Size", default=1.0, min=0.001, soft_max=10.0)
    bpy.types.Scene.cad_snap_radius = FloatProperty(name="Snap Radius", default=18.0, min=2.0, soft_max=80.0)
    bpy.types.Scene.cad_nurbs_degree = IntProperty(name="NURBS Degree", default=3, min=1, max=11)
    bpy.types.Scene.cad_selected_nurbs_degree = IntProperty(name="Selected NURBS Degree", default=3, min=1, max=11)
    bpy.types.Scene.cad_loft_samples = IntProperty(name="Loft Samples", default=32, min=2, max=256)
    bpy.types.Scene.cad_surface_samples = IntProperty(name="Surface Samples", default=32, min=2, max=256)
    bpy.types.Scene.cad_extrude_distance = FloatProperty(name="Extrude Distance", default=5.0, soft_min=-100.0, soft_max=100.0)
    bpy.types.Scene.cad_pipe_radius = FloatProperty(name="Pipe Radius", default=0.25, min=0.001, soft_max=10.0)
    bpy.types.Scene.cad_pipe_resolution = IntProperty(name="Pipe Resolution", default=12, min=3, max=64)
    bpy.types.Scene.cad_revolve_angle = FloatProperty(name="Revolve Degree", default=360.0, min=0.0, max=360.0, soft_min=0.0, soft_max=360.0)
    bpy.types.Scene.cad_revolve_steps = IntProperty(name="Revolve Steps", default=48, min=3, max=256)
    bpy.types.Scene.cad_revolve_axis_json = StringProperty(name="Revolve Axis", default="")
    bpy.types.Scene.cad_sweep_rail_samples = IntProperty(name="Sweep Rail Samples", default=32, min=2, max=256)
    bpy.types.Scene.cad_sweep_profile_samples = IntProperty(name="Sweep Profile Samples", default=24, min=2, max=256)
    bpy.types.Scene.cad_active_cplane_name = StringProperty(name="Active Named CPlane", default="")
    bpy.types.Scene.cad_cplane_save_name = StringProperty(name="CPlane Name", default="CPlane 01")
    bpy.types.Scene.cad_cplane_rotate_angle = FloatProperty(name="Rotate Angle", default=90.0, soft_min=-360.0, soft_max=360.0)
    bpy.types.Scene.cad_cplane_camera_distance = FloatProperty(name="Camera Distance", default=20.0, min=0.1, soft_max=100.0)

    bpy.types.Scene.cad_cplane_axis_rotation_angle = FloatProperty(
        name="Axis Angle",
        default=0.0,
        soft_min=-360.0,
        soft_max=360.0,
        update=cad_cplane_axis_rotation_angle_update,
    )
    bpy.types.Scene.cad_cplane_axis_rotation_name = StringProperty(name="Axis Rotation CPlane", default="")
    bpy.types.Scene.cad_cplane_axis_rotation_json = StringProperty(name="Axis Rotation Data", default="{}")
    bpy.types.Scene.cad_cplanes_json = StringProperty(name="Saved CPlanes", default="{}")
    bpy.types.Scene.cad_cplane_visibility_json = StringProperty(name="CPlane Visibility", default="{}")
    bpy.types.Scene.cad_show_cplane_visuals = BoolProperty(name="Show CPlanes", default=True)
    bpy.types.Scene.cad_show_cplane_grid_visuals = BoolProperty(name="Show CPlane Grids", default=True)
    bpy.types.Scene.cad_show_cplane_labels = BoolProperty(name="Show CPlane Labels", default=True)
    bpy.types.Scene.cad_cplane_visual_grid_count = FloatProperty(name="CPlane Grid Count", default=6.0, min=1.0, soft_max=30.0)
    bpy.types.Scene.cad_cplane_visual_grid_spacing = FloatProperty(name="CPlane Grid Spacing", default=1.0, min=0.001, soft_max=10.0)
    bpy.types.Scene.cad_cplane_visual_axis_length = FloatProperty(name="CPlane Axis Length", default=2.0, min=0.1, soft_max=20.0)
    bpy.types.Scene.cad_cplane_items = CollectionProperty(type=Hippo3D_CPlaneListItem)
    bpy.types.Scene.cad_cplane_index = IntProperty(name="CPlane List Index", default=0)
    bpy.types.Scene.cad_active_cplane_dropdown = EnumProperty(
        name="Active CPlane",
        description="Choose the active built-in or saved CPlane",
        items=cplane_dropdown_items,
        update=cplane_dropdown_update,
    )
    bpy.types.Scene.cad_current_cplane_visible = BoolProperty(
        name="Current CPlane Visible",
        description="Show or hide the currently active CPlane",
        default=True,
        update=current_cplane_visibility_update,
    )
    bpy.types.Scene.cad_cplane = EnumProperty(
        name="CPlane",
        description="Active CAD construction plane",
        items=[
            ("TOP", "Top / XY", "Draw on world XY"),
            ("FRONT", "Front / XZ", "Draw on world XZ"),
            ("RIGHT", "Right / YZ", "Draw on world YZ"),
            ("WORLD", "World / XY", "World XY drawing plane")],
        default="TOP",
        update=_cad_cplane_enum_update,
    )


def unregister_props():
    for name in ["cad_osnap_endpoint", "cad_osnap_midpoint", "cad_osnap_nearest", "cad_osnap_center", "cad_osnap_grid", "cad_ortho", "cad_grid_size", "cad_snap_radius", "cad_active_cplane_name", "cad_cplane_save_name", "cad_cplanes_json", "cad_show_cplane_visuals", "cad_show_cplane_grid_visuals", "cad_show_cplane_labels", "cad_cplane_visual_grid_count", "cad_cplane_visual_grid_spacing", "cad_cplane_visual_axis_length", "cad_cplane_visibility_json", "cad_cplane_items", "cad_cplane_index", "cad_active_cplane_dropdown", "cad_current_cplane_visible", "cad_cplane", "cad_cplane_rotate_angle", "cad_cplane_axis_rotation_angle", "cad_cplane_axis_rotation_name", "cad_cplane_axis_rotation_json", "cad_cplane_camera_distance", "cad_nurbs_degree", "cad_selected_nurbs_degree", "cad_loft_samples", "cad_surface_samples", "cad_extrude_distance", "cad_pipe_radius", "cad_pipe_resolution", "cad_revolve_angle", "cad_revolve_steps", "cad_sweep_rail_samples", "cad_sweep_profile_samples", "cad_revolve_axis_json", "hippo_offset_distance", "hippo_xline_length", "hippo_array_count", "hippo_array_dx", "hippo_array_dy", "hippo_array_dz", "hippo_ellipse_rx", "hippo_ellipse_ry", "hippo_polygon_sides", "hippo_polygon_radius", "hippo_fillet_radius", "hippo_trim_tolerance"]:
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)



@persistent
def cad_cplane_load_post_handler(dummy):
    try:
        sync_cplane_layer_collection(bpy.context)
    except Exception:
        pass


def cad_cplane_init_timer():
    try:
        sync_cplane_layer_collection(bpy.context)
    except Exception:
        pass
    return None




# -----------------------------------------------------------------------------
# FINAL OVERRIDE: NURBS Offset by Control Points
# -----------------------------------------------------------------------------

def hippo_nurbs_control_points_world(obj):
    if obj is None or obj.type != "CURVE":
        return [], False, 3, 4, 24

    try:
        spl = obj.data.splines[0]
    except Exception:
        return [], False, 3, 4, 24

    if spl.type != "NURBS":
        return [], False, 3, 4, 24

    pts = [obj.matrix_world @ Vector((p.co.x, p.co.y, p.co.z)) for p in spl.points]
    cyclic = bool(getattr(spl, "use_cyclic_u", False))
    order = int(getattr(spl, "order_u", 4))
    degree = max(1, order - 1)
    resolution_u = int(getattr(obj.data, "resolution_u", 24))

    if len(pts) >= 2 and (pts[0] - pts[-1]).length < 1e-6:
        pts = pts[:-1]
        cyclic = True

    return pts, cyclic, degree, order, resolution_u


def hippo_offset_control_points_on_cplane(context, pts, cyclic, distance):
    if len(pts) < 2:
        return []

    origin, u, v, n = get_cplane_axes(context)
    result = []
    total = len(pts)

    for i, p in enumerate(pts):
        if cyclic:
            prev_p = pts[(i - 1) % total]
            next_p = pts[(i + 1) % total]
        else:
            prev_p = pts[max(i - 1, 0)]
            next_p = pts[min(i + 1, total - 1)]

        tangent = next_p - prev_p

        if tangent.length < 1e-8:
            if i < total - 1:
                tangent = pts[i + 1] - p
            elif i > 0:
                tangent = p - pts[i - 1]

        if tangent.length < 1e-8:
            result.append(p.copy())
            continue

        tangent.normalize()
        perp = n.cross(tangent)

        if perp.length < 1e-8:
            result.append(p.copy())
            continue

        perp.normalize()
        result.append(p + perp * float(distance))

    return result


def hippo_create_nurbs_from_control_points(context, points, name, degree, cyclic=False, resolution_u=24):
    if len(points) < 2:
        return None

    degree = int(max(1, min(int(degree), len(points) - 1)))
    order = degree + 1

    curve = bpy.data.curves.new(name, type="CURVE")
    curve.dimensions = "3D"
    curve.resolution_u = int(max(1, resolution_u))

    spl = curve.splines.new(type="NURBS")
    spl.points.add(len(points) - 1)

    for pnt, co in zip(spl.points, points):
        pnt.co = (co.x, co.y, co.z, 1.0)

    spl.order_u = order
    spl.use_endpoint_u = True
    spl.use_cyclic_u = bool(cyclic)

    obj = bpy.data.objects.new(name, curve)
    context.collection.objects.link(obj)

    obj["hippo_shape"] = "nurbs_curve"
    obj["hippo_curve_type"] = "NURBS"
    obj["hippo_degree"] = degree
    obj["hippo_order_u"] = order
    obj["hippo_offset_method"] = "control_points"

    return obj


def hippo_read_poly_points_world(obj):
    try:
        spl = obj.data.splines[0]
        pts = [obj.matrix_world @ Vector((p.co.x, p.co.y, p.co.z)) for p in spl.points]
        cyclic = bool(getattr(spl, "use_cyclic_u", False))
        if len(pts) >= 2 and (pts[0] - pts[-1]).length < 1e-6:
            pts = pts[:-1]
            cyclic = True
        return pts, cyclic
    except Exception:
        return [], False


def hippo_create_poly_from_points(context, points, name, cyclic=False):
    if len(points) < 2:
        return None

    curve = bpy.data.curves.new(name, type="CURVE")
    curve.dimensions = "3D"
    curve.resolution_u = 12

    spl = curve.splines.new(type="POLY")
    spl.points.add(len(points) - 1)

    for pnt, co in zip(spl.points, points):
        pnt.co = (co.x, co.y, co.z, 1.0)

    spl.use_cyclic_u = bool(cyclic)

    obj = bpy.data.objects.new(name, curve)
    context.collection.objects.link(obj)
    obj["hippo_curve_type"] = "POLY"
    obj["hippo_offset_method"] = "control_polygon"

    return obj


def run_offset_command(context):
    """Offset selected curves.

    NURBS:
    - offset the source NURBS control points
    - recreate a real NURBS spline
    - preserve degree/order and control point count
    """
    objs = [obj for obj in context.selected_objects if obj.type == "CURVE"]

    if not objs:
        return False, "Offset: select curve objects."

    dist = float(getattr(context.scene, "hippo_offset_distance", 0.2))
    created = 0

    for obj in objs:
        try:
            source_type = obj.data.splines[0].type if obj.data.splines else "UNKNOWN"
        except Exception:
            source_type = "UNKNOWN"

        if source_type == "NURBS":
            pts, cyclic, degree, order, resolution_u = hippo_nurbs_control_points_world(obj)
            if len(pts) < 2:
                continue

            result = hippo_offset_control_points_on_cplane(context, pts, cyclic, dist)

            new_obj = hippo_create_nurbs_from_control_points(
                context,
                result,
                name=f"{obj.name}_NURBS_Offset",
                degree=degree,
                cyclic=cyclic,
                resolution_u=resolution_u,
            )

            if new_obj:
                new_obj["hippo_source"] = obj.name
                new_obj["hippo_source_spline_type"] = "NURBS"
                new_obj["hippo_source_degree"] = degree
                new_obj["hippo_control_point_count"] = len(result)
                new_obj["hippo_offset_distance"] = dist
                created += 1

        elif source_type == "POLY":
            pts, cyclic = hippo_read_poly_points_world(obj)
            if len(pts) < 2:
                continue

            result = hippo_offset_control_points_on_cplane(context, pts, cyclic, dist)

            new_obj = hippo_create_poly_from_points(
                context,
                result,
                name=f"{obj.name}_Offset",
                cyclic=cyclic,
            )

            if new_obj:
                new_obj["hippo_source"] = obj.name
                new_obj["hippo_source_spline_type"] = "POLY"
                new_obj["hippo_offset_distance"] = dist
                created += 1

        else:
            # For Bezier/unknown, use sampled shape but still create NURBS approximation.
            pts = sample_curve_object_points(obj, samples=max(64, int(getattr(context.scene, "cad_surface_samples", 64))))
            if len(pts) < 2:
                continue

            cyclic = False
            if len(pts) >= 2 and (pts[0] - pts[-1]).length < 1e-6:
                pts = pts[:-1]
                cyclic = True

            result = hippo_offset_control_points_on_cplane(context, pts, cyclic, dist)

            new_obj = hippo_create_nurbs_from_control_points(
                context,
                result,
                name=f"{obj.name}_NURBS_Offset",
                degree=3,
                cyclic=cyclic,
                resolution_u=24,
            )

            if new_obj:
                new_obj["hippo_source"] = obj.name
                new_obj["hippo_source_spline_type"] = source_type
                new_obj["hippo_offset_distance"] = dist
                created += 1

    if created == 0:
        return False, "Offset failed."

    return True, f"Created {created} offset curve(s)."



# -----------------------------------------------------------------------------
# Arc 3Pt Helper Fix
# -----------------------------------------------------------------------------

def create_arc_from_3_points(context, p0, p1, p2, segments=48):
    """Create an arc curve from three world-space points using the active CPlane.

    Points:
    - p0 = arc start
    - p1 = point on arc
    - p2 = arc end
    """
    import math as _math

    origin, u, v, n = get_cplane_axes(context)

    def to_2d(p):
        q = p - origin
        return Vector((q.dot(u), q.dot(v)))

    a = to_2d(p0)
    b = to_2d(p1)
    c = to_2d(p2)

    # Circumcircle in 2D.
    d = 2.0 * (
        a.x * (b.y - c.y) +
        b.x * (c.y - a.y) +
        c.x * (a.y - b.y)
    )

    if abs(d) < 1e-10:
        return None

    ux = (
        (a.x * a.x + a.y * a.y) * (b.y - c.y) +
        (b.x * b.x + b.y * b.y) * (c.y - a.y) +
        (c.x * c.x + c.y * c.y) * (a.y - b.y)
    ) / d

    uy = (
        (a.x * a.x + a.y * a.y) * (c.x - b.x) +
        (b.x * b.x + b.y * b.y) * (a.x - c.x) +
        (c.x * c.x + c.y * c.y) * (b.x - a.x)
    ) / d

    center = Vector((ux, uy))
    radius = (a - center).length

    if radius < 1e-8:
        return None

    def angle(pt):
        return _math.atan2(pt.y - center.y, pt.x - center.x)

    ang0 = angle(a)
    ang1 = angle(b)
    ang2 = angle(c)

    def norm(x):
        while x < 0:
            x += 2.0 * _math.pi
        while x >= 2.0 * _math.pi:
            x -= 2.0 * _math.pi
        return x

    s = norm(ang0)
    m = norm(ang1)
    e = norm(ang2)

    # Determine if the CCW arc from start to end passes through middle.
    if s <= e:
        ccw_contains_mid = s <= m <= e
    else:
        ccw_contains_mid = m >= s or m <= e

    if ccw_contains_mid:
        end_angle = ang2
        if end_angle < ang0:
            end_angle += 2.0 * _math.pi
    else:
        end_angle = ang2
        if end_angle > ang0:
            end_angle -= 2.0 * _math.pi

    pts = []
    steps = max(8, int(segments))

    for i in range(steps + 1):
        t = i / steps
        ang = ang0 + (end_angle - ang0) * t
        x = ux + _math.cos(ang) * radius
        y = uy + _math.sin(ang) * radius
        pts.append(origin + u * x + v * y)

    # Create as a curve object. Use POLY approximation for now, but keep as curve.
    curve = bpy.data.curves.new("Hippo3D_Arc", type="CURVE")
    curve.dimensions = "3D"
    curve.resolution_u = 24

    spl = curve.splines.new(type="POLY")
    spl.points.add(len(pts) - 1)

    for bp, p in zip(spl.points, pts):
        bp.co = (p.x, p.y, p.z, 1.0)

    obj = bpy.data.objects.new("Hippo3D_Arc", curve)
    context.collection.objects.link(obj)

    obj["hippo_shape"] = "arc"
    obj["hippo_arc_method"] = "3pt"
    obj["hippo_arc_segments"] = steps

    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    context.view_layer.objects.active = obj

    return obj



# -----------------------------------------------------------------------------
# XLine 2Pt Helper Fix
# -----------------------------------------------------------------------------

def create_xline_from_2_points(context, p0, p1):
    """Create an AutoCAD/Rhino-style construction line from two picked points.

    The first point defines a point on the XLine.
    The second point defines the direction.
    The line is represented as a very long curve segment.
    """
    direction = p1 - p0

    if direction.length < 1e-8:
        return None

    direction.normalize()

    length = float(getattr(context.scene, "hippo_xline_length", 1000.0))
    half = length * 0.5

    a = p0 - direction * half
    b = p0 + direction * half

    curve = bpy.data.curves.new("Hippo3D_XLine", type="CURVE")
    curve.dimensions = "3D"
    curve.resolution_u = 1

    spl = curve.splines.new(type="POLY")
    spl.points.add(1)

    spl.points[0].co = (a.x, a.y, a.z, 1.0)
    spl.points[1].co = (b.x, b.y, b.z, 1.0)

    obj = bpy.data.objects.new("Hippo3D_XLine", curve)
    context.collection.objects.link(obj)

    obj["hippo_shape"] = "xline"
    obj["hippo_command"] = "xline"
    obj["hippo_xline_length"] = length

    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    context.view_layer.objects.active = obj

    return obj


def run_xline_command(context):
    return False, "XLine is interactive. Type XLine, then pick two points to define direction."



# -----------------------------------------------------------------------------
# Project Command Helper Fix
# -----------------------------------------------------------------------------

def hippo_make_project_curve(context, points, name="Hippo3D_Project", cyclic=False):
    if not points or len(points) < 2:
        return None

    curve = bpy.data.curves.new(name, type="CURVE")
    curve.dimensions = "3D"
    curve.resolution_u = 12

    spl = curve.splines.new(type="POLY")
    spl.points.add(len(points) - 1)

    for bp, p in zip(spl.points, points):
        bp.co = (p.x, p.y, p.z, 1.0)

    spl.use_cyclic_u = bool(cyclic)

    obj = bpy.data.objects.new(name, curve)
    context.collection.objects.link(obj)

    obj["hippo_shape"] = "projected_curve"
    obj["hippo_command"] = "project"

    return obj


def hippo_get_curve_points_for_project(obj, context):
    """Read curve points for projection.

    Uses sampled geometry for NURBS/Bezier so the projected result follows the displayed curve.
    Uses original vertices for POLY where possible.
    """
    if obj is None or obj.type != "CURVE":
        return [], False

    try:
        spl = obj.data.splines[0]
        cyclic = bool(getattr(spl, "use_cyclic_u", False))

        if spl.type == "POLY":
            pts = [obj.matrix_world @ Vector((p.co.x, p.co.y, p.co.z)) for p in spl.points]
        else:
            samples = max(64, int(getattr(context.scene, "cad_surface_samples", 64)))
            pts = sample_curve_object_points(obj, samples=samples)

        if len(pts) >= 2 and (pts[0] - pts[-1]).length < 1e-6:
            pts = pts[:-1]
            cyclic = True

        return pts, cyclic
    except Exception:
        return [], False


def run_project_command(context):
    """Project selected curves onto the active CPlane."""
    curves = [obj for obj in context.selected_objects if obj.type == "CURVE"]

    if not curves:
        return False, "Project: select curve object(s)."

    plane_origin, u, v, n = get_cplane_axes(context)
    created = 0

    for obj in curves:
        pts, cyclic = hippo_get_curve_points_for_project(obj, context)

        if len(pts) < 2:
            continue

        projected = []

        for p in pts:
            d = (p - plane_origin).dot(n)
            projected.append(p - n * d)

        new_obj = hippo_make_project_curve(
            context,
            projected,
            name=f"{obj.name}_Projected",
            cyclic=cyclic,
        )

        if new_obj:
            new_obj["hippo_source"] = obj.name
            created += 1

    if created == 0:
        return False, "Project failed."

    return True, f"Projected {created} curve(s) to active CPlane."



# -----------------------------------------------------------------------------
# Explode Command Helper Fix
# -----------------------------------------------------------------------------

def hippo_make_curve_segment(context, a, b, name="Hippo3D_Explode_Segment"):
    if (b - a).length < 1e-8:
        return None

    curve = bpy.data.curves.new(name, type="CURVE")
    curve.dimensions = "3D"
    curve.resolution_u = 1

    spl = curve.splines.new(type="POLY")
    spl.points.add(1)

    spl.points[0].co = (a.x, a.y, a.z, 1.0)
    spl.points[1].co = (b.x, b.y, b.z, 1.0)

    obj = bpy.data.objects.new(name, curve)
    context.collection.objects.link(obj)

    obj["hippo_shape"] = "curve_segment"
    obj["hippo_command"] = "explode"

    return obj


def hippo_curve_spline_points_for_explode(obj, spline):
    if obj is None or obj.type != "CURVE":
        return []

    pts = []

    try:
        if spline.type in {"POLY", "NURBS"}:
            pts = [obj.matrix_world @ Vector((p.co.x, p.co.y, p.co.z)) for p in spline.points]
        elif spline.type == "BEZIER":
            pts = [obj.matrix_world @ p.co for p in spline.bezier_points]
    except Exception:
        pts = []

    return pts


def run_explode_command(context):
    """Explode selected curve objects into individual line-segment curve objects.

    Behaviour:
    - POLY/NURBS: explodes by control polygon/control points.
    - BEZIER: explodes by Bezier edit points.
    - Cyclic splines add the final closing segment.
    - Original curve objects are removed after successful explosion.
    """
    objs = [obj for obj in context.selected_objects if obj.type == "CURVE"]

    if not objs:
        return False, "Explode: select curve object(s)."

    created = 0
    to_remove = []

    for obj in objs:
        obj_created = 0

        try:
            splines = list(obj.data.splines)
        except Exception:
            splines = []

        for spline in splines:
            pts = hippo_curve_spline_points_for_explode(obj, spline)

            if len(pts) < 2:
                continue

            pairs = list(zip(pts[:-1], pts[1:]))

            if bool(getattr(spline, "use_cyclic_u", False)) and len(pts) > 2:
                pairs.append((pts[-1], pts[0]))

            for a, b in pairs:
                seg = hippo_make_curve_segment(
                    context,
                    a,
                    b,
                    name=f"{obj.name}_Exploded",
                )

                if seg:
                    seg["hippo_source"] = obj.name
                    created += 1
                    obj_created += 1

        if obj_created > 0:
            to_remove.append(obj)

    for obj in to_remove:
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
        except Exception:
            pass

    if created == 0:
        return False, "Explode failed. No curve segments were created."

    return True, f"Exploded into {created} segment(s)."



# -----------------------------------------------------------------------------
# CPlane Perpendicular to Curve - Working Override
# -----------------------------------------------------------------------------

def hippo_nearest_curve_point_tangent(obj, pick_point, samples=160):
    if obj is None or obj.type != "CURVE":
        return None

    pts = sample_curve_object_points(obj, samples=max(16, samples))

    if len(pts) < 2:
        return None

    best_i = min(range(len(pts)), key=lambda i: (pts[i] - pick_point).length)
    origin = pts[best_i]

    if best_i == 0:
        tangent = pts[1] - pts[0]
    elif best_i == len(pts) - 1:
        tangent = pts[-1] - pts[-2]
    else:
        tangent = pts[best_i + 1] - pts[best_i - 1]

    if tangent.length < 1e-8:
        return None

    tangent.normalize()
    return origin, tangent


def create_cplane_perpendicular_to_curve(context, name, pick_point):
    curves = [obj for obj in context.selected_objects if obj.type == "CURVE"]

    if not curves:
        return False, "CPlane Perp Curve: select a curve first, then click near it."

    curve = context.active_object if context.active_object in curves else curves[0]

    result = hippo_nearest_curve_point_tangent(curve, pick_point)
    if result is None:
        return False, "CPlane Perp Curve: could not read curve/tangent."

    origin, n = result

    # CPlane normal is curve tangent. This makes the CPlane perpendicular to the curve.
    up = Vector((0, 0, 1))
    if abs(n.dot(up)) > 0.95:
        up = Vector((0, 1, 0))

    u = up.cross(n)
    if u.length < 1e-8:
        u = Vector((1, 0, 0)).cross(n)

    if u.length < 1e-8:
        return False, "CPlane Perp Curve: could not build axes."

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



# -----------------------------------------------------------------------------
# Stable Sweep1 / Sweep2 Override
# -----------------------------------------------------------------------------

def hippo_curve_points(obj, samples=64):
    pts = sample_curve_object_points(obj, samples=max(2, int(samples)))
    return pts if pts and len(pts) >= 2 else []


def hippo_align_curve_direction(reference_pts, target_pts):
    """Reverse target if its endpoints better match the reference direction."""
    if not reference_pts or not target_pts:
        return target_pts

    same = (reference_pts[0] - target_pts[0]).length + (reference_pts[-1] - target_pts[-1]).length
    flip = (reference_pts[0] - target_pts[-1]).length + (reference_pts[-1] - target_pts[0]).length

    if flip < same:
        return list(reversed(target_pts))

    return target_pts


def hippo_make_mesh_from_sections(context, name, sections, props=None, flip_faces=False):
    if len(sections) < 2 or len(sections[0]) < 2:
        return None

    rows = len(sections)
    cols = min(len(row) for row in sections)
    sections = [row[:cols] for row in sections]

    verts = []
    for row in sections:
        verts.extend([(p.x, p.y, p.z) for p in row])

    faces = []
    for r in range(rows - 1):
        for c in range(cols - 1):
            a = r * cols + c
            b = r * cols + c + 1
            cc = (r + 1) * cols + c + 1
            d = (r + 1) * cols + c

            if flip_faces:
                faces.append((a, d, cc, b))
            else:
                faces.append((a, b, cc, d))

    mesh = bpy.data.meshes.new(name + "_Mesh")
    mesh.from_pydata(verts, [], faces)
    mesh.update()

    obj = bpy.data.objects.new(name, mesh)
    context.collection.objects.link(obj)

    if props:
        for k, v in props.items():
            obj[k] = v

    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    context.view_layer.objects.active = obj

    return obj


def hippo_rail_frame(rail_pts, i, prev_y=None, prev_z=None):
    """Stable rail frame with minimal flipping."""
    count = len(rail_pts)

    if i == 0:
        tangent = rail_pts[1] - rail_pts[0]
    elif i == count - 1:
        tangent = rail_pts[-1] - rail_pts[-2]
    else:
        tangent = rail_pts[i + 1] - rail_pts[i - 1]

    if tangent.length < 1e-8:
        tangent = Vector((1, 0, 0))

    xaxis = tangent.normalized()

    up = Vector((0, 0, 1))
    if abs(xaxis.dot(up)) > 0.95:
        up = Vector((0, 1, 0))

    yaxis = up.cross(xaxis)
    if yaxis.length < 1e-8:
        yaxis = Vector((1, 0, 0)).cross(xaxis)

    if yaxis.length < 1e-8:
        yaxis = Vector((0, 1, 0))

    yaxis.normalize()
    zaxis = xaxis.cross(yaxis).normalized()

    # Prevent sudden 180-degree frame flips.
    if prev_y is not None and yaxis.dot(prev_y) < 0:
        yaxis.negate()
        zaxis.negate()

    return xaxis, yaxis, zaxis


def create_sweep1_surface(context):
    """Stable Sweep1.

    Selection:
    - select one rail and one profile curve
    - active curve is treated as profile when possible
    """
    curves = [obj for obj in context.selected_objects if obj.type == "CURVE"]

    if len(curves) < 2:
        return False, "Sweep1: select rail curve and profile curve."

    active = context.active_object
    if active in curves:
        profile = active
        rail_candidates = [c for c in curves if c != profile]
        rail = rail_candidates[0] if rail_candidates else curves[0]
    else:
        rail = curves[0]
        profile = curves[1]

    rail_samples = int(getattr(context.scene, "cad_sweep_rail_samples", 48))
    profile_samples = int(getattr(context.scene, "cad_sweep_profile_samples", 24))

    rail_pts = hippo_curve_points(rail, rail_samples)
    profile_pts = hippo_curve_points(profile, profile_samples)

    if len(rail_pts) < 2 or len(profile_pts) < 2:
        return False, "Sweep1: rail/profile has insufficient points."

    # Use profile local coordinates relative to first profile point.
    profile_origin = profile_pts[0]
    local_profile = [p - profile_origin for p in profile_pts]

    sections = []
    prev_y = None
    prev_z = None

    for i, rail_p in enumerate(rail_pts):
        xaxis, yaxis, zaxis = hippo_rail_frame(rail_pts, i, prev_y, prev_z)

        # Direction correction: previous implementation swept to the opposite side.
        if bool(getattr(context.scene, True)):
            yaxis.negate()
            zaxis.negate()

        prev_y = yaxis.copy()
        prev_z = zaxis.copy()

        row = []
        for lp in local_profile:
            # Map profile local XY/YZ-like offsets into stable rail frame.
            q = rail_p + yaxis * lp.x + zaxis * lp.y
            row.append(q)
        sections.append(row)

    obj = hippo_make_mesh_from_sections(
        context,
        "Hippo3D_Sweep1_Surface",
        sections,
        props={
            "hippo_surface_type": "sweep1",
            "cad_surface_type": "sweep1",
            "hippo_rail": rail.name,
            "hippo_profile": profile.name,
            "hippo_sweep_method": "stable_frame",
        },
        flip_faces=False,
    )

    if not obj:
        return False, "Sweep1 failed."

    return True, "Created stable Sweep1 surface."


def create_sweep2_surface(context, rail_samples=None):
    """Stable Sweep2.

    Selection:
    - select two rail curves and one profile curve
    - active curve is treated as profile when possible
    """
    curves = [obj for obj in context.selected_objects if obj.type == "CURVE"]

    if len(curves) < 3:
        return False, "Sweep2: select two rails and one profile curve."

    active = context.active_object
    if active in curves:
        profile = active
        rails = [c for c in curves if c != profile][:2]
    else:
        rails = curves[:2]
        profile = curves[2]

    if len(rails) < 2:
        return False, "Sweep2: needs two rail curves."

    if rail_samples is None:
        rail_samples = int(getattr(context.scene, "cad_sweep_rail_samples", 48))

    profile_samples = int(getattr(context.scene, "cad_sweep_profile_samples", 24))

    rail_a = hippo_curve_points(rails[0], rail_samples)
    rail_b = hippo_curve_points(rails[1], rail_samples)
    profile_pts = hippo_curve_points(profile, profile_samples)

    if len(rail_a) < 2 or len(rail_b) < 2 or len(profile_pts) < 2:
        return False, "Sweep2: rails/profile have insufficient points."

    # Align rails in same direction.
    rail_b = hippo_align_curve_direction(rail_a, rail_b)

    # Use profile parameter along its longest local dimension.
    p0 = profile_pts[0]
    local = [p - p0 for p in profile_pts]

    xs = [p.x for p in local]
    ys = [p.y for p in local]
    use_y = (max(ys) - min(ys)) > (max(xs) - min(xs))

    vals = ys if use_y else xs
    vmin = min(vals)
    vmax = max(vals)
    span = max(vmax - vmin, 1e-8)

    # Ensure profile order goes from rail A to rail B.
    first_val = vals[0]
    last_val = vals[-1]
    if last_val < first_val:
        local = list(reversed(local))
        vals = list(reversed(vals))

    sections = []
    prev_y = None
    prev_z = None

    for i in range(min(len(rail_a), len(rail_b))):
        a = rail_a[i]
        b = rail_b[i]
        across = b - a

        if across.length < 1e-8:
            continue

        xaxis = across.normalized()

        # Tangent along average rail direction.
        if i == 0:
            tan = ((rail_a[1] - rail_a[0]) + (rail_b[1] - rail_b[0])) * 0.5
        elif i == min(len(rail_a), len(rail_b)) - 1:
            tan = ((rail_a[-1] - rail_a[-2]) + (rail_b[-1] - rail_b[-2])) * 0.5
        else:
            tan = ((rail_a[i + 1] - rail_a[i - 1]) + (rail_b[i + 1] - rail_b[i - 1])) * 0.5

        if tan.length < 1e-8:
            tan = Vector((0, 0, 1))

        zaxis = xaxis.cross(tan)
        if zaxis.length < 1e-8:
            up = Vector((0, 0, 1))
            if abs(xaxis.dot(up)) > 0.95:
                up = Vector((0, 1, 0))
            zaxis = xaxis.cross(up)

        if zaxis.length < 1e-8:
            zaxis = Vector((0, 0, 1))

        zaxis.normalize()
        yaxis = zaxis.cross(xaxis).normalized()

        # Prevent frame flip.
        if prev_y is not None and yaxis.dot(prev_y) < 0:
            yaxis.negate()
            zaxis.negate()

        prev_y = yaxis.copy()
        prev_z = zaxis.copy()

        row = []

        for lp in local:
            param_val = lp.y if use_y else lp.x
            t = (param_val - vmin) / span
            t = max(0.0, min(1.0, t))

            base = a.lerp(b, t)

            # Remaining dimensions are used as section offsets.
            off_y = lp.x if use_y else lp.y
            off_z = lp.z

            q = base + yaxis * off_y + zaxis * off_z
            row.append(q)

        sections.append(row)

    if len(sections) < 2:
        return False, "Sweep2 failed to generate sections."

    # Correct face winding if first face normal points against expected direction.
    flip_faces = False
    try:
        s0 = sections[0]
        s1 = sections[1]
        if len(s0) >= 2 and len(s1) >= 2:
            normal = (s0[1] - s0[0]).cross(s1[0] - s0[0])
            expected = (rail_b[0] - rail_a[0]).cross(rail_a[1] - rail_a[0])
            if normal.length > 1e-8 and expected.length > 1e-8:
                if normal.normalized().dot(expected.normalized()) < 0:
                    flip_faces = True
    except Exception:
        flip_faces = False

    obj = hippo_make_mesh_from_sections(
        context,
        "Hippo3D_Sweep2_Surface",
        sections,
        props={
            "hippo_surface_type": "sweep2",
            "cad_surface_type": "sweep2",
            "hippo_rail_a": rails[0].name,
            "hippo_rail_b": rails[1].name,
            "hippo_profile": profile.name,
            "hippo_sweep_method": "stable_two_rail_frame",
        },
        flip_faces=(not flip_faces) if bool(getattr(context.scene, True)) else flip_faces,
    )

    if not obj:
        return False, "Sweep2 failed."

    return True, "Created stable Sweep2 surface."


def register():
    # Register classes first because Scene CollectionProperty depends on Hippo3D_CPlaneListItem.
    for cls in classes:
        try:
            bpy.utils.register_class(cls)
        except ValueError:
            pass

    register_props()

    try:
        bpy.context.scene.cad_show_cplane_visuals = True
        bpy.context.scene.cad_show_cplane_grid_visuals = True
        bpy.context.scene.cad_show_cplane_labels = True
    except Exception:
        pass

    try:
        if state.cplane_draw_handle is None:
            state.cplane_draw_handle = bpy.types.SpaceView3D.draw_handler_add(
                draw_cplanes_visual_callback,
                (),
                "WINDOW",
                "POST_VIEW",
            )
    except Exception:
        pass

    try:
        bpy.app.timers.register(cad_cplane_init_timer, first_interval=0.1)
        if cad_cplane_load_post_handler not in bpy.app.handlers.load_post:
            bpy.app.handlers.load_post.append(cad_cplane_load_post_handler)
    except Exception:
        pass

    try:
        bpy.utils.register_tool(Hippo3D_WST_LineTool, after={"builtin.select_box"}, separator=True, group=True)
        bpy.utils.register_tool(Hippo3D_WST_PolylineTool, after={"cad_blender.line_tool"}, group=True)
        bpy.utils.register_tool(Hippo3D_WST_RectangleTool, after={"cad_blender.polyline_tool"}, group=True)
        bpy.utils.register_tool(Hippo3D_WST_CircleTool, after={"cad_blender.rectangle_tool"}, group=True)
        bpy.utils.register_tool(Hippo3D_WST_ArcTool, after={"cad_blender.circle_tool"}, group=True)
        bpy.utils.register_tool(Hippo3D_WST_EllipseTool, after={"hippo3d.arc_tool"}, group=True)
        bpy.utils.register_tool(Hippo3D_WST_PolygonTool, after={"hippo3d.ellipse_tool"}, group=True)
        bpy.utils.register_tool(Hippo3D_WST_NurbsTool, after={"hippo3d.polygon_tool"}, group=True)
        bpy.utils.register_tool(Hippo3D_WST_XLineTool, after={"cad_blender.nurbs_tool"}, group=True)
    except Exception:
        pass

    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon
    if kc:
        km = kc.keymaps.new(name="3D View", space_type="VIEW_3D")
        kmi = km.keymap_items.new("cad.start_command", type="SLASH", value="PRESS", ctrl=True)
        kmi.active = True
        addon_keymaps.append((km, kmi))


def unregister():
    finish_context = bpy.context if bpy.context else None
    if finish_context:
        try:
            finish_command(finish_context)
        except Exception:
            pass

    for km, kmi in addon_keymaps:
        km.keymap_items.remove(kmi)
    addon_keymaps.clear()

    try:
        if cad_cplane_load_post_handler in bpy.app.handlers.load_post:
            bpy.app.handlers.load_post.remove(cad_cplane_load_post_handler)
    except Exception:
        pass

    try:
        bpy.utils.unregister_tool(Hippo3D_WST_XLineTool)
        bpy.utils.unregister_tool(Hippo3D_WST_NurbsTool)
        bpy.utils.unregister_tool(Hippo3D_WST_PolygonTool)
        bpy.utils.unregister_tool(Hippo3D_WST_EllipseTool)
        bpy.utils.unregister_tool(Hippo3D_WST_ArcTool)
        bpy.utils.unregister_tool(Hippo3D_WST_CircleTool)
        bpy.utils.unregister_tool(Hippo3D_WST_RectangleTool)
        bpy.utils.unregister_tool(Hippo3D_WST_PolylineTool)
        bpy.utils.unregister_tool(Hippo3D_WST_LineTool)
    except Exception:
        pass

    if state.cplane_draw_handle is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(state.cplane_draw_handle, "WINDOW")
        except Exception:
            pass
        state.cplane_draw_handle = None

    unregister_props()

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()

# -----------------------------------------------------------------------------
# Hippo3D_EdgeSurface robust boundary ordering fix
# -----------------------------------------------------------------------------

def hippo_curve_endpoints(obj):
    pts = sample_curve_object_points(obj, samples=32)

    if len(pts) < 2:
        return None

    return pts[0], pts[-1], pts


def hippo_chain_boundary_curves(curves):
    """Sort and orient curves into a continuous boundary chain."""
    remaining = []

    for obj in curves:
        data = hippo_curve_endpoints(obj)
        if data:
            remaining.append((obj, *data))

    if not remaining:
        return []

    ordered = []

    obj, start, end, pts = remaining.pop(0)
    ordered.append((pts, start, end))

    while remaining:
        last_pts, last_start, last_end = ordered[-1]

        best_i = None
        best_reverse = False
        best_dist = 1e18

        for i, (obj, s, e, p) in enumerate(remaining):
            d1 = (last_end - s).length
            d2 = (last_end - e).length

            if d1 < best_dist:
                best_dist = d1
                best_i = i
                best_reverse = False

            if d2 < best_dist:
                best_dist = d2
                best_i = i
                best_reverse = True

        if best_i is None:
            break

        obj, s, e, p = remaining.pop(best_i)

        if best_reverse:
            p = list(reversed(p))
            s, e = e, s

        ordered.append((p, s, e))

    final_pts = []

    for i, (pts, s, e) in enumerate(ordered):
        if i == 0:
            final_pts.extend(pts)
        else:
            final_pts.extend(pts[1:])

    return final_pts


def create_edge_surface(context):
    """Create planar edge surface with robust edge ordering."""
    curves = [obj for obj in context.selected_objects if obj.type == "CURVE"]

    if len(curves) < 2:
        return False, "Hippo3D_EdgeSurface: select 2-4 boundary curves."

    boundary = hippo_chain_boundary_curves(curves)

    if len(boundary) < 3:
        return False, "Hippo3D_EdgeSurface failed to build boundary."

    # Remove duplicated closing point.
    cleaned = [boundary[0]]

    for p in boundary[1:]:
        if (p - cleaned[-1]).length > 1e-6:
            cleaned.append(p)

    boundary = cleaned

    verts = [(p.x, p.y, p.z) for p in boundary]
    edges = [(i, (i + 1) % len(verts)) for i in range(len(verts))]
    face = tuple(range(len(verts)))

    mesh = bpy.data.meshes.new("Hippo3D_EdgeSurface")
    mesh.from_pydata(verts, edges, [face])
    mesh.update()

    obj = bpy.data.objects.new("Hippo3D_EdgeSurface", mesh)
    context.collection.objects.link(obj)

    obj["hippo_surface_type"] = "edgesrf"

    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    context.view_layer.objects.active = obj

    return True, "Edge surface created."

