# SPDX-License-Identifier: GPL-3.0-or-later
"""Hippo3D OCC Viewer node for Sverchok.

Receives:
  - Surfaces: SvNurbsSurface (native or geomdl)
  - Solids:   FreeCAD Part.Shape / Part.Solid

Visualises them via GPU preview and can bake them back as Hippo3D OCC objects.

Outputs:
  - Surfaces: Properly wrapped SvNurbsSurface instances for Sverchok nodes
  - Solids:   FreeCAD Part.Shape/Part.Solid objects for Sverchok solid nodes
  - Vertices: Mesh vertices (triangulated representation)
  - Faces:    Mesh face indices (triangulated representation)
  - Names:    Object names

Baking strategy:
  - Surfaces with _hippo_shape_id      -> reuse the original OCC shape.
  - Surfaces that are real NURBS       -> build a new OCC B-surface from the
                                          control-point grid via
                                          make_bspline_surface_from_grid.
  - Surfaces that are not NURBS        -> sample to a mesh and bake via
                                          make_shape_from_mesh.
  - Solids (Part.Shape)                -> export STEP and import into OCC.
  - Mesh-only / no OCC core            -> bake as plain mesh OCC object.
"""

import os
import numpy as np
import bpy
from mathutils import Vector
from bpy.props import BoolProperty, IntProperty, FloatVectorProperty
import gpu
from gpu_extras.batch import batch_for_shader

_SVK_DEBUG = bool(os.environ.get("HIPPO3D_SVK_DEBUG", ""))


def _debug(msg):
    if _SVK_DEBUG:
        print(f"[Hippo3D OCC Viewer] {msg}")


def _debug_exc(msg):
    import traceback
    _debug(f"{msg}\n{traceback.format_exc()}")

# ---------------------------------------------------------------------------
# Guarded sverchok imports
# ---------------------------------------------------------------------------
try:
    from sverchok.node_tree import SverchCustomTreeNode
except Exception:
    SverchCustomTreeNode = object

try:
    from sverchok.data_structure import updateNode, ensure_nesting_level, zip_long_repeat, node_id
except Exception:
    def updateNode(*a, **k):
        pass
    def ensure_nesting_level(data, level, data_types=None):
        return data if data else []
    def zip_long_repeat(*args):
        import itertools
        return zip(*itertools.repeat(args[0]))
    def node_id(node):
        return str(id(node))

try:
    from sverchok.utils.surface.core import SvSurface
except Exception:
    SvSurface = None

try:
    from sverchok.utils.surface.nurbs import SvNurbsSurface, SvNativeNurbsSurface, SvGeomdlSurface
except Exception:
    SvNurbsSurface = None
    SvNativeNurbsSurface = None
    SvGeomdlSurface = None

try:
    from sverchok.utils.nurbs_common import SvNurbsMaths
except Exception:
    SvNurbsMaths = None

try:
    from sverchok.utils.surface.bakery import SurfaceData, make_quad_edges, make_quad_faces
except Exception:
    SurfaceData = None
    def make_quad_edges(ru, rv):
        return []
    def make_quad_faces(ru, rv):
        return []

try:
    from sverchok.ui.bgl_callback_3dview import callback_disable, callback_enable
except Exception:
    def callback_disable(*a, **k):
        pass
    def callback_enable(*a, **k):
        pass

try:
    from sverchok.utils.modules.drawing_abstractions import drawing, shading_3d
except Exception:
    drawing = None
    shading_3d = None

try:
    from sverchok.utils.sv_3dview_tools import Sv3DviewAlign
except Exception:
    Sv3DviewAlign = None

try:
    from sverchok.utils.sv_operator_mixins import SvGenericNodeLocator
except Exception:
    SvGenericNodeLocator = object

# ---------------------------------------------------------------------------
# Hippo3D / FreeCAD availability
# ---------------------------------------------------------------------------
import importlib.util
import sys
from pathlib import Path


def _find_hippo3d_package_root():
    """Return the filesystem root of the Hippo3D add-on package, or None."""
    candidate = Path(__file__).resolve().parents[2]
    if (candidate / "kernels" / "occ_loader.py").exists():
        return candidate
    for ext_name in ["Hippo3D", "bl_ext.blender_development.Hippo3D"]:
        mod = sys.modules.get(ext_name)
        if mod is not None and hasattr(mod, "__file__"):
            candidate = Path(mod.__file__).resolve().parent
            if (candidate / "kernels" / "occ_loader.py").exists():
                return candidate
    return None


def _load_hippo_occ_core():
    """Load the Hippo3D OCC core regardless of how the add-on is packaged."""
    root = _find_hippo3d_package_root()
    if root is None:
        raise ImportError("Could not locate Hippo3D package root")
    loader_path = root / "kernels" / "occ_loader.py"
    spec = importlib.util.spec_from_file_location("h3d_shared_kernels.occ_loader", str(loader_path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not create spec for {loader_path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["h3d_shared_kernels.occ_loader"] = mod
    spec.loader.exec_module(mod)
    return mod.load_occ_core()


HIPPO3D_OCC_AVAILABLE = False
try:
    _occ = _load_hippo_occ_core()
    HIPPO3D_OCC_AVAILABLE = True
except Exception:
    pass

FREECAD_AVAILABLE = False
try:
    import Part  # noqa: F401
    FREECAD_AVAILABLE = True
except Exception:
    pass


# ---------------------------------------------------------------------------
# Drawing helpers
# ---------------------------------------------------------------------------
def draw_edges(shader, points, edges, line_width, color):
    if drawing is None:
        return
    drawing.set_line_width(line_width)
    batch = batch_for_shader(shader, 'LINES', {"pos": points}, indices=edges)
    shader.bind()
    shader.uniform_float('color', color)
    batch.draw(shader)
    drawing.reset_line_width()


def draw_points(shader, points, size, color):
    if drawing is None:
        return
    drawing.set_point_size(size)
    batch = batch_for_shader(shader, 'POINTS', {"pos": points})
    shader.bind()
    shader.uniform_float('color', color)
    batch.draw(shader)
    drawing.reset_point_size()


def draw_polygons(shader, points, tris, vertex_colors):
    batch = batch_for_shader(shader, 'TRIS', {"pos": points, 'color': vertex_colors}, indices=tris)
    shader.bind()
    batch.draw(shader)


def _apply_matrix_to_points(pts, matrix):
    """Apply a 4x4 mathutils.Matrix to a list of (x,y,z) tuples."""
    if matrix is None:
        return pts
    try:
        return [tuple(matrix @ Vector(p)) for p in pts]
    except Exception as e:
        _debug_exc(f"Failed to transform points: {e}")
        return pts


def _get_item_world_matrix(item):
    """Return the source object's current world matrix if available."""
    matrix = getattr(item, '_hippo_world_matrix', None)
    obj_name = getattr(item, '_hippo_source_object', None)
    if obj_name and obj_name in bpy.data.objects:
        try:
            matrix = bpy.data.objects[obj_name].matrix_world
        except Exception:
            pass
    return matrix


def draw_hippo_surfaces(context, args):
    node, draw_inputs, v_shader, e_shader, p_shader = args
    _debug(f"draw_hippo_surfaces called with {len(draw_inputs)} items")
    if drawing is None:
        _debug("drawing abstraction unavailable")
        return
    drawing.enable_depth_test()
    drawing.enable_blendmode()
    drawing.set_polygonmode_fill()
    drawn = 0
    for item in draw_inputs:
        try:
            pts = getattr(item, 'points_list', [])
            tris = getattr(item, 'tris', [])
            tri_colors = getattr(item, 'tri_colors', [])
            edges = getattr(item, 'edges', [])
            _debug(f"item: {len(pts)} pts, {len(tris)} tris, {len(edges)} edges")
            matrix = _get_item_world_matrix(item)
            if matrix is not None:
                pts = _apply_matrix_to_points(pts, matrix)
                _debug(f"applied source object matrix to {len(pts)} points")
            if node.draw_surface and tris and tri_colors:
                draw_polygons(p_shader, pts, tris, tri_colors)
                drawn += 1
            if node.draw_edges and edges:
                draw_edges(e_shader, pts, edges, node.edges_line_width, node.edges_color)
                drawn += 1
            if node.draw_verts and pts:
                draw_points(v_shader, pts, node.verts_size, node.verts_color)
                drawn += 1
        except Exception as e:
            _debug_exc(f"Error drawing item: {e}")
    _debug(f"draw_hippo_surfaces finished, drew {drawn} items")
    drawing.disable_blendmode()
    drawing.disable_depth_test()


# ---------------------------------------------------------------------------
# Solid mesh wrapper for drawing
# ---------------------------------------------------------------------------
class SolidMeshData:
    def __init__(self, verts, faces, color, light_vector, matrix=None, source_object=None):
        self.points_list = verts
        self.edges = []
        self.tris = []
        self.tri_colors = []
        self._hippo_world_matrix = matrix
        self._hippo_source_object = source_object
        for f in faces:
            if len(f) == 3:
                self.tris.append(f)
            elif len(f) == 4:
                self.tris.append([f[0], f[2], f[1]])
                self.tris.append([f[0], f[3], f[2]])
            elif len(f) > 4:
                for i in range(1, len(f) - 1):
                    self.tris.append([f[0], f[i + 1], f[i]])
        import numpy as np
        main_color = np.array(color)
        self.tri_colors = np.tile(main_color, (len(self.points_list), 1)).tolist()


# ---------------------------------------------------------------------------
# Solid-to-mesh helper
# ---------------------------------------------------------------------------
def _solid_to_mesh(shape):
    """Convert a FreeCAD Part.Shape to mesh vertices and faces."""
    if not FREECAD_AVAILABLE:
        return None, None
    try:
        import Part
        if not isinstance(shape, Part.Shape):
            return None, None
        tessellation = shape.tessellate(0.1)
        if tessellation is None or len(tessellation) != 2:
            return None, None
        raw_verts, raw_faces = tessellation
        vertices = [(float(v.x), float(v.y), float(v.z)) for v in raw_verts]
        faces = [list(f) for f in raw_faces]
        return vertices, faces
    except Exception:
        return None, None


# ---------------------------------------------------------------------------
# Helpers to classify incoming surfaces
# ---------------------------------------------------------------------------
def _is_nurbs_surface(surface):
    """True for real Sverchok NURBS surfaces (native or geomdl)."""
    if SvNurbsSurface is not None and isinstance(surface, SvNurbsSurface):
        return True
    # Check for geomdl surface
    if hasattr(surface, 'get_degree_u') and hasattr(surface, 'get_control_points'):
        return True
    return False


def _ensure_sverchok_surface(surface):
    """Ensure a surface is wrapped as a proper Sverchok SvNurbsSurface.
    
    If the surface is already a SvNurbsSurface, return it.
    Otherwise, try to wrap it appropriately.
    """
    if surface is None:
        return None
    
    # Already a proper Sverchok NURBS surface
    if SvNurbsSurface is not None and isinstance(surface, SvNurbsSurface):
        return surface
    
    # If it has the right attributes, try to wrap it
    if hasattr(surface, 'get_degree_u') and hasattr(surface, 'get_control_points'):
        # Try to get as SvNurbsSurface using the get method
        if SvNurbsSurface is not None:
            try:
                nurbs = SvNurbsSurface.get(surface)
                if nurbs is not None:
                    return nurbs
            except Exception:
                pass
    
    return surface


def _surface_to_occ_shape(surface):
    """Convert a Sverchok surface to an OCC shape_id.

    Returns the shape_id or None if conversion fails.
    """
    if not HIPPO3D_OCC_AVAILABLE:
        return None

    # Ensure surface is properly wrapped
    surface = _ensure_sverchok_surface(surface)
    if surface is None:
        return None

    # Round-trip: surface originated from Hippo3D.
    shape_id = getattr(surface, '_hippo_shape_id', None)
    if shape_id is not None:
        try:
            occ = _load_hippo_occ_core()
            if occ.has_shape(shape_id):
                return int(shape_id)
        except Exception:
            pass

    if not _is_nurbs_surface(surface):
        return None

    try:
        degree_u = int(surface.get_degree_u())
        degree_v = int(surface.get_degree_v())
        kv_u = surface.get_knotvector_u()
        kv_v = surface.get_knotvector_v()
        ctrlpts = surface.get_control_points()
        n_u, n_v, _ = ctrlpts.shape
    except Exception:
        return None

    try:
        # Rebuild full knot vectors with multiplicities from the expanded
        # Sverchok knotvectors.
        def _kv_to_unique_knots_mults(kv):
            if not len(kv):
                return [], []
            knots = []
            mults = []
            prev = float(kv[0])
            count = 1
            for k in kv[1:]:
                fk = float(k)
                if abs(fk - prev) < 1e-12:
                    count += 1
                else:
                    knots.append(prev)
                    mults.append(count)
                    prev = fk
                    count = 1
            knots.append(prev)
            mults.append(count)
            return knots, mults

        knots_u, mults_u = _kv_to_unique_knots_mults(kv_u)
        knots_v, mults_v = _kv_to_unique_knots_mults(kv_v)

        poles = []
        for i in range(n_u):
            for j in range(n_v):
                poles.append((float(ctrlpts[i, j, 0]),
                              float(ctrlpts[i, j, 1]),
                              float(ctrlpts[i, j, 2])))

        weights = []
        is_rational = False
        if hasattr(surface, 'is_rational'):
            try:
                is_rational = surface.is_rational()
            except Exception:
                pass
        if is_rational and hasattr(surface, 'get_weights'):
            try:
                w = surface.get_weights()
                weights = [float(w[i, j]) for i in range(n_u) for j in range(n_v)]
            except Exception:
                weights = []

        occ = _load_hippo_occ_core()
        if not hasattr(occ, 'make_nurbs_surface'):
            # Fallback for older native modules without make_nurbs_surface.
            return int(occ.make_bspline_surface_from_grid(n_u, n_v, poles))

        periodic_u = getattr(surface, 'is_u_periodic', lambda: False)()
        periodic_v = getattr(surface, 'is_v_periodic', lambda: False)()

        return int(occ.make_nurbs_surface(
            degree_u, degree_v,
            knots_u, knots_v,
            mults_u, mults_v,
            poles, weights,
            bool(periodic_u), bool(periodic_v)
        ))
    except Exception:
        # Last-resort fallback: approximate through the control-point grid.
        try:
            occ = _load_hippo_occ_core()
            grid = []
            for i in range(n_u):
                for j in range(n_v):
                    grid.append((float(ctrlpts[i, j, 0]),
                                 float(ctrlpts[i, j, 1]),
                                 float(ctrlpts[i, j, 2])))
            return int(occ.make_bspline_surface_from_grid(n_u, n_v, grid))
        except Exception:
            return None


def _solid_to_occ_shape(solid):
    """Convert a FreeCAD Part.Solid/Shape to an OCC shape_id via STEP round-trip."""
    if not FREECAD_AVAILABLE or not HIPPO3D_OCC_AVAILABLE:
        return None

    try:
        import tempfile
        import os
        import Part

        with tempfile.NamedTemporaryFile(suffix=".step", delete=False) as tf:
            step_path = tf.name
        try:
            if hasattr(solid, 'exportStep'):
                solid.exportStep(step_path)
            else:
                Part.export([solid], step_path)

            occ = _load_hippo_occ_core()
            shape_ids = occ.import_step(step_path)
            if shape_ids:
                return int(shape_ids[0])
        finally:
            try:
                os.remove(step_path)
            except Exception:
                pass
    except Exception:
        return None


# ---------------------------------------------------------------------------
# OCC baking helpers
# ---------------------------------------------------------------------------
_HIPPO_PRIMITIVE_DIMS = {
    "box": ["hippo_occ_width", "hippo_occ_depth", "hippo_occ_height"],
    "sphere": ["hippo_occ_radius"],
    "cylinder": ["hippo_occ_radius", "hippo_occ_height"],
    "cone": ["hippo_occ_radius1", "hippo_occ_radius2", "hippo_occ_height"],
    "torus": ["hippo_occ_major_radius", "hippo_occ_minor_radius"],
}


def _is_solid_like_shape(occ, shape_id):
    """Return True if the OCC shape topology is solid-like."""
    try:
        if hasattr(occ, "get_shape_type"):
            return occ.get_shape_type(shape_id) in {"solid", "compsolid", "compound"}
    except Exception:
        pass
    return False


def _is_surface_like_shape(occ, shape_id):
    """Return True if the OCC shape topology is a single face or shell."""
    try:
        if hasattr(occ, "get_shape_type"):
            return occ.get_shape_type(shape_id) in {"face", "shell"}
    except Exception:
        pass
    return False


def _recognize_freecad_primitive(solid):
    """Try to recognize a FreeCAD primitive solid and return dimension props.

    Returns (occ_type, props_dict) or (None, None).
    """
    if not FREECAD_AVAILABLE:
        return None, None
    try:
        import Part
        if not isinstance(solid, Part.Shape):
            return None, None

        shape_type = getattr(solid, "ShapeType", "")
        if shape_type != "Solid":
            return None, None

        # Helper: count face types
        faces = solid.Faces
        n_planar = sum(1 for f in faces if str(f.Surface).startswith("Plane"))
        n_cyl = sum(1 for f in faces if str(f.Surface).startswith("Cylinder"))
        n_sphere = sum(1 for f in faces if str(f.Surface).startswith("Sphere"))
        n_cone = sum(1 for f in faces if str(f.Surface).startswith("Cone"))
        n_torus = sum(1 for f in faces if str(f.Surface).startswith("Torus"))

        # Box: 6 planar faces
        if len(faces) == 6 and n_planar == 6:
            bbox = solid.BoundBox
            return "box", {
                "hippo_occ_width": float(bbox.XLength),
                "hippo_occ_depth": float(bbox.YLength),
                "hippo_occ_height": float(bbox.ZLength),
            }

        # Sphere: 1 spherical face
        if len(faces) == 1 and n_sphere == 1:
            r = float(faces[0].Surface.Radius)
            return "sphere", {"hippo_occ_radius": r}

        # Cylinder: 3 faces (2 planar + 1 cylindrical)
        if len(faces) == 3 and n_planar == 2 and n_cyl == 1:
            cyl = faces[n_planar].Surface
            h = float(solid.BoundBox.ZLength)
            return "cylinder", {
                "hippo_occ_radius": float(cyl.Radius),
                "hippo_occ_height": h,
            }

        # Cone: 3 faces (2 planar + 1 conical)
        if len(faces) == 3 and n_planar == 2 and n_cone == 1:
            cone = faces[n_planar].Surface
            h = float(solid.BoundBox.ZLength)
            return "cone", {
                "hippo_occ_radius1": float(cone.Radius),
                "hippo_occ_radius2": 0.0,
                "hippo_occ_height": h,
            }

        # Torus: 1 toroidal face
        if len(faces) == 1 and n_torus == 1:
            tor = faces[0].Surface
            return "torus", {
                "hippo_occ_major_radius": float(tor.MajorRadius),
                "hippo_occ_minor_radius": float(tor.MinorRadius),
            }
    except Exception:
        pass
    return None, None


def _apply_primitive_props(obj, occ_type, props):
    """Set primitive type and dimension properties on a baked object."""
    obj["hippo_occ_type"] = occ_type
    for key, value in props.items():
        obj[key] = float(value)


def _create_hippo_object(context, name, occ_shape_id, location, nurbs_shape_id=None, source_solid=None):
    """Create/update a Hippo3D OCC mesh object from an OCC shape_id."""
    try:
        occ = _load_hippo_occ_core()
        if occ.has_shape(occ_shape_id):
            data = occ.remesh_shape(occ_shape_id, 0.1)
            mesh = bpy.data.meshes.new(name + "_Mesh")
            mesh.from_pydata(data.get("vertices", []), [], data.get("faces", []))
            mesh.update()
            for polygon in mesh.polygons:
                polygon.use_smooth = True
            obj = bpy.data.objects.new(name, mesh)
            obj.location = Vector(location or (0, 0, 0))
            context.collection.objects.link(obj)
            obj["hippo_kernel"] = "occ"
            obj["hippo_occ_preview"] = True
            obj["hippo_occ_display_cache"] = True
            obj["hippo_occ_edit_locked"] = True
            obj["hippo_occ_shape_id"] = int(occ_shape_id)
            if nurbs_shape_id is not None:
                obj["hippo_occ_nurbs_shape_id"] = int(nurbs_shape_id)

            shape_type = "unknown"
            if hasattr(occ, "get_shape_type"):
                try:
                    shape_type = occ.get_shape_type(occ_shape_id)
                except Exception:
                    pass

            # 1. Solid-like shape: prefer primitive handles, then generic solid face editing.
            if shape_type in {"solid", "compsolid", "compound"}:
                # Try metadata from Hippo3D source first.
                primitive_type = getattr(source_solid, "_hippo_occ_type", None)
                primitive_props = None
                if primitive_type:
                    primitive_props = {}
                    for key in _HIPPO_PRIMITIVE_DIMS.get(primitive_type, []):
                        val = getattr(source_solid, key, None)
                        if val is None:
                            primitive_props = None
                            break
                        primitive_props[key] = float(val)

                # Fallback: geometric recognition of FreeCAD primitives.
                if primitive_type is None or primitive_props is None:
                    primitive_type, primitive_props = _recognize_freecad_primitive(source_solid)

                if primitive_type and primitive_props:
                    _apply_primitive_props(obj, primitive_type, primitive_props)
                else:
                    obj["hippo_occ_type"] = "sverchok_baked_solid"
                return obj

            # 2. Surface/shell: keep surface editing behavior.
            obj["hippo_occ_type"] = "sverchok_baked"
            try:
                if hasattr(occ, "extract_bsurf_control_points"):
                    info = occ.extract_bsurf_control_points(occ_shape_id)
                    u_count = int(info.get("u_count", 0))
                    v_count = int(info.get("v_count", 0))
                    if u_count >= 2 and v_count >= 2:
                        obj["hippo_occ_surface_editable"] = True
            except Exception:
                pass
            return obj
    except Exception:
        pass
    return None


def _bake_hippo3d_occ_object(context, name, vertices, faces, location=None, shape_id=None):
    """Bake raw mesh data as a Hippo3D OCC object.

    If *shape_id* is available in the OCC registry, reuse that shape's mesh.
    Otherwise fall back to building an OCC shape from the mesh data.
    """
    if shape_id is not None and HIPPO3D_OCC_AVAILABLE:
        obj = _create_hippo_object(context, name, shape_id, location)
        if obj is not None:
            return obj

    if HIPPO3D_OCC_AVAILABLE:
        try:
            occ = _load_hippo_occ_core()
            mesh_shape_id = occ.make_shape_from_mesh(vertices, faces)
            obj = _create_hippo_object(context, name, mesh_shape_id, location)
            if obj is not None:
                return obj
        except Exception:
            pass

    # Fallback: plain mesh object tagged as Hippo3D OCC.
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


def _sample_surface_for_display(surface, ru, rv, color):
    """Create a simple display mesh wrapper for a Sverchok surface.

    Used as a fallback when SurfaceData is unavailable or incompatible.
    """
    import numpy as np
    from mathutils import Vector

    class SimpleSurfaceMesh:
        def __init__(self):
            self.points_list = []
            self.tris = []
            self.edges = []
            self.tri_colors = []

    mesh = SimpleSurfaceMesh()
    u0, u1, v0, v1 = surface.get_u_min(), surface.get_u_max(), surface.get_v_min(), surface.get_v_max()
    us = np.linspace(u0, u1, ru)
    vs = np.linspace(v0, v1, rv)
    pts = np.zeros((ru, rv, 3), dtype=float)
    for i, u in enumerate(us):
        for j, v in enumerate(vs):
            pts[i, j] = surface.evaluate(u, v)
    mesh.points_list = [tuple(pts[i, j]) for i in range(ru) for j in range(rv)]
    mesh.edges = make_quad_edges(ru, rv)
    faces = make_quad_faces(ru, rv)
    for f in faces:
        if len(f) == 4:
            mesh.tris.append([f[0], f[2], f[1]])
            mesh.tris.append([f[0], f[3], f[2]])
        else:
            mesh.tris.append(f)
    main_color = np.array(color)
    mesh.tri_colors = np.tile(main_color, (len(mesh.points_list), 1)).tolist()
    return mesh


def _bake_surface(context, name, surface, location=None):
    """Bake a Sverchok surface as a Hippo3D OCC object."""
    _debug(f"_bake_surface called for {name}")
    # Ensure surface is properly wrapped
    surface = _ensure_sverchok_surface(surface)
    if surface is None:
        _debug("surface is None after wrapping")
        return None

    # 1. Real NURBS surface -> build exact OCC B-surface.
    occ_shape_id = _surface_to_occ_shape(surface)
    _debug(f"_surface_to_occ_shape returned {occ_shape_id}")
    if occ_shape_id is not None:
        # For Hippo3D-generated surfaces the precise NURBS id is the same as
        # the returned face id; otherwise the returned id is already the best
        # precise surface we have.
        nurbs_shape_id = getattr(surface, '_hippo_shape_id', occ_shape_id)
        obj = _create_hippo_object(context, name, occ_shape_id, location, nurbs_shape_id=nurbs_shape_id)
        _debug(f"_create_hippo_object returned {obj}")
        if obj is not None:
            return obj

    # 2. Generic surface -> sample to mesh and bake.
    if hasattr(surface, 'evaluate'):
        try:
            ru = rv = 50
            sdata = _sample_surface_for_display(surface, ru, rv, (1, 1, 1, 1))
            _debug(f"fallback surface sampling created with {len(sdata.points_list)} pts")
            return _bake_hippo3d_occ_object(
                context, name, sdata.points_list, make_quad_faces(ru, rv),
                location=location
            )
        except Exception as e:
            _debug_exc(f"fallback surface bake failed: {e}")

    _debug("_bake_surface returning None")
    return None


def _bake_solid(context, name, solid, location=None):
    """Bake a FreeCAD Part.Solid as a Hippo3D OCC object."""
    _debug(f"_bake_solid called for {name}")
    # 1. Try STEP round-trip for clean BRep geometry.
    occ_shape_id = _solid_to_occ_shape(solid)
    _debug(f"_solid_to_occ_shape returned {occ_shape_id}")
    if occ_shape_id is not None:
        obj = _create_hippo_object(context, name, occ_shape_id, location, source_solid=solid)
        _debug(f"_create_hippo_object returned {obj}")
        if obj is not None:
            return obj

    # 2. Fallback: tessellate and bake as OCC mesh shape.
    verts, faces = _solid_to_mesh(solid)
    if verts and faces:
        return _bake_hippo3d_occ_object(context, name, verts, faces, location=location)

    _debug("_bake_solid returning None")
    return None


# ---------------------------------------------------------------------------
# Bake operator
# ---------------------------------------------------------------------------
class SvBakeHippoSurfaceOp(bpy.types.Operator, SvGenericNodeLocator):
    bl_idname = "node.hippo3d_surface_baker"
    bl_label = "Bake Hippo3D Surfaces"
    bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}

    def sv_execute(self, context, node):
        data = node.get_geometry()
        ctx = bpy.context
        for item in data.get('bake_items', []):
            _bake_item(ctx, item, location=node.location_offset)
        return {'FINISHED'}


def _bake_item(context, item, location=None):
    name = item.get('name', 'Hippo3D_Svk')
    obj_type = item.get('type')

    if obj_type == 'surface':
        surface = item.get('surface')
        if surface is not None:
            _bake_surface(context, name, surface, location=location)
    elif obj_type == 'solid':
        solid = item.get('solid')
        if solid is not None:
            _bake_solid(context, name, solid, location=location)
    elif obj_type == 'mesh':
        verts = item.get('verts', [])
        faces = item.get('faces', [])
        shape_id = item.get('shape_id')
        if verts and faces:
            _bake_hippo3d_occ_object(
                context, name, verts, faces,
                location=location, shape_id=shape_id
            )


# ---------------------------------------------------------------------------
# Safe socket creation helper
# ---------------------------------------------------------------------------
def _safe_socket(node, in_out, typed_name, fallback_name, label):
    try:
        return in_out.new(typed_name, label)
    except Exception:
        return in_out.new(fallback_name, label)


# ---------------------------------------------------------------------------
# Node definition
# ---------------------------------------------------------------------------
class SvHippo3DOCCViewer(bpy.types.Node, SverchCustomTreeNode):
    bl_idname = 'SvHippo3DOCCViewer'
    bl_label = 'Hippo3D OCC Viewer'
    bl_icon = 'MESH_CUBE'
    sv_icon = 'SV_DRAW_VIEWER'

    resolution_u: IntProperty(name="Resolution U", min=1, default=50, update=updateNode)
    resolution_v: IntProperty(name="Resolution V", min=1, default=50, update=updateNode)

    activate: BoolProperty(name='Show', default=True, update=updateNode)

    draw_surface: BoolProperty(name="Display Surface", default=True, update=updateNode)
    surface_color: FloatVectorProperty(
        name="Surface Color", default=(1.0, 0.5, 0.0, 0.6),
        size=4, min=0.0, max=1.0, subtype='COLOR', update=updateNode)

    draw_edges: BoolProperty(name="Display Edges", default=False, update=updateNode)
    edges_line_width: IntProperty(name="Edges Line Width", min=1, default=1, update=updateNode)
    edges_color: FloatVectorProperty(
        name="Edges Color", default=(0.22, 0.22, 0.27, 1.0),
        size=4, min=0.0, max=1.0, subtype='COLOR', update=updateNode)

    draw_verts: BoolProperty(name="Display Vertices", default=False, update=updateNode)
    verts_size: IntProperty(name="Vertices Size", min=1, default=3, update=updateNode)
    verts_color: FloatVectorProperty(
        name="Vertices Color", default=(0.9, 0.9, 0.95, 1.0),
        size=4, min=0.0, max=1.0, subtype='COLOR', update=updateNode)

    line_width: IntProperty(name="Line Width", min=1, default=1, update=updateNode)
    point_size: IntProperty(name="Point Size", min=1, default=3, update=updateNode)

    bake: BoolProperty(
        name="Bake", default=False,
        description="Bake incoming surfaces/solids as Hippo3D OCC objects",
        update=updateNode)

    base_name: bpy.props.StringProperty(
        name="Base Name", default="Hippo3D_Svk",
        description="Base name for baked objects")

    location_offset: FloatVectorProperty(
        name="Location", default=(0.0, 0.0, 0.0), size=3,
        description="Offset location for baked objects")

    light_vector: FloatVectorProperty(
        name='Light Direction', subtype='DIRECTION', min=0, max=1, size=3,
        default=(0.2, 0.6, 0.4), update=updateNode)

    def sv_init(self, context):
        _safe_socket(self, self.inputs, 'SvSurfaceSocket', 'SvStringsSocket', 'Surfaces')
        _safe_socket(self, self.inputs, 'SvSolidSocket', 'SvStringsSocket', 'Solids')
        self.inputs.new('SvStringsSocket', 'ResolutionU').prop_name = 'resolution_u'
        self.inputs.new('SvStringsSocket', 'ResolutionV').prop_name = 'resolution_v'

        _safe_socket(self, self.outputs, 'SvSurfaceSocket', 'SvStringsSocket', 'Surfaces')
        _safe_socket(self, self.outputs, 'SvSolidSocket', 'SvStringsSocket', 'Solids')

        self.outputs.new('SvVerticesSocket', "Vertices")
        self.outputs.new('SvStringsSocket', "Faces")
        self.outputs.new('SvStringsSocket', "Names")

    def draw_buttons(self, context, layout):
        layout.prop(self, "activate", icon="HIDE_" + ("OFF" if self.activate else "ON"))
        grid = layout.column(align=True)
        row = grid.row(align=True)
        row.prop(self, 'draw_surface', icon='OUTLINER_OB_SURFACE', text='')
        row.prop(self, 'surface_color', text="")
        row = grid.row(align=True)
        row.prop(self, 'draw_edges', icon='UV_EDGESEL', text='')
        row.prop(self, 'edges_color', text="")
        row.prop(self, 'edges_line_width', text="px")
        row = grid.row(align=True)
        row.prop(self, 'draw_verts', icon='UV_VERTEXSEL', text='')
        row.prop(self, 'verts_color', text="")
        row.prop(self, 'verts_size', text="px")
        row = layout.row(align=True)
        row.scale_y = 4.0 if getattr(self, 'prefs_over_sized_buttons', False) else 1
        self.wrapper_tracked_ui_draw_op(row, SvBakeHippoSurfaceOp.bl_idname,
                                         icon='OUTLINER_OB_MESH', text="B A K E")
        row.separator()
        if Sv3DviewAlign is not None:
            self.wrapper_tracked_ui_draw_op(row, Sv3DviewAlign.bl_idname, icon='CURSOR', text='')

    def draw_buttons_ext(self, context, layout):
        layout.prop(self, 'light_vector')
        layout.prop(self, 'bake', toggle=True)
        if self.bake:
            layout.prop(self, 'base_name')
            layout.prop(self, 'location_offset')

    # -----------------------------------------------------------------------
    def get_geometry(self):
        surfaces_s = []
        solids_s = []
        res_u_s = []
        res_v_s = []

        _debug(f"get_geometry: Surfaces linked={self.inputs['Surfaces'].is_linked}, Solids linked={self.inputs['Solids'].is_linked}")

        try:
            if self.inputs['Surfaces'].is_linked:
                surfaces_s = self.inputs['Surfaces'].sv_get(deepcopy=False)
                _debug(f"got surfaces_s with {len(surfaces_s)} top-level items")
        except Exception as e:
            _debug_exc(f"failed to get Surfaces input: {e}")
        try:
            if self.inputs['Solids'].is_linked:
                solids_s = self.inputs['Solids'].sv_get(deepcopy=False)
                _debug(f"got solids_s with {len(solids_s)} top-level items")
        except Exception as e:
            _debug_exc(f"failed to get Solids input: {e}")
        try:
            if self.inputs['ResolutionU'].is_linked:
                res_u_s = self.inputs['ResolutionU'].sv_get(deepcopy=False)
        except Exception:
            pass
        try:
            if self.inputs['ResolutionV'].is_linked:
                res_v_s = self.inputs['ResolutionV'].sv_get(deepcopy=False)
        except Exception:
            pass

        if not res_u_s:
            res_u_s = [[self.resolution_u]]
        if not res_v_s:
            res_v_s = [[self.resolution_v]]

        # Ensure proper nesting for surfaces (list of lists)
        if surfaces_s:
            surfaces_s = ensure_nesting_level(
                surfaces_s, 2,
                data_types=(SvSurface,) if SvSurface else ()
            )
        else:
            surfaces_s = []
            
        # Ensure proper nesting for solids (list of lists)
        if solids_s:
            solids_s = ensure_nesting_level(solids_s, 2)
        else:
            solids_s = []
            
        res_u_s = ensure_nesting_level(res_u_s, 2)
        res_v_s = ensure_nesting_level(res_v_s, 2)

        draw_inputs = []
        out_surfaces = []
        out_solids = []
        out_verts = []
        out_faces = []
        out_names = []
        bake_items = []
        idx = 0

        # --- Surfaces ------------------------------------------------------
        for params in zip_long_repeat(surfaces_s, res_u_s, res_v_s):
            for surface, ru, rv in zip_long_repeat(*params):
                if surface is None:
                    continue

                # Ensure surface is properly wrapped as Sverchok type
                surface = _ensure_sverchok_surface(surface)
                if surface is None:
                    continue

                is_valid_surface = False
                if SvSurface is not None and isinstance(surface, SvSurface):
                    is_valid_surface = True
                if _is_nurbs_surface(surface):
                    is_valid_surface = True

                if not is_valid_surface:
                    _debug(f"surface rejected as invalid")
                    continue

                # Add to output - ensure it's a proper SvNurbsSurface
                out_surfaces.append(surface)

                # Collect display mesh data and bake metadata.
                sdata = None
                if SurfaceData is not None:
                    try:
                        sdata = SurfaceData(self, surface, ru, rv)
                        _debug(f"SurfaceData created with {len(sdata.points_list)} pts")
                    except Exception as e:
                        _debug_exc(f"SurfaceData failed: {e}")
                if sdata is None:
                    # Fallback: sample the surface ourselves on a regular grid.
                    try:
                        sdata = _sample_surface_for_display(surface, ru, rv, self.surface_color)
                        _debug(f"Fallback surface sampling created with {len(sdata.points_list)} pts")
                    except Exception as e:
                        _debug_exc(f"Fallback surface sampling failed: {e}")
                if sdata is not None:
                    # Preserve source object transform so the preview appears at the
                    # same location as the original Hippo3D object.
                    sdata._hippo_world_matrix = getattr(surface, '_hippo_world_matrix', None)
                    sdata._hippo_source_object = getattr(surface, '_hippo_source_object', None)
                    draw_inputs.append(sdata)
                    out_verts.append(sdata.points_list)
                    out_faces.append(make_quad_faces(ru, rv))
                    out_names.append(f"{self.base_name}_S{idx:03d}")
                    _debug(f"surface draw data ready, color={self.surface_color}")
                    bake_items.append({
                        'name': f"{self.base_name}_S{idx:03d}",
                        'type': 'surface',
                        'surface': surface,
                    })
                    idx += 1

        # --- Solids --------------------------------------------------------
        for params in zip_long_repeat(solids_s, res_u_s, res_v_s):
            for solid, ru, rv in zip_long_repeat(*params):
                if solid is None:
                    continue
                if not FREECAD_AVAILABLE:
                    continue
                try:
                    import Part
                    if not isinstance(solid, Part.Shape):
                        continue
                except Exception:
                    continue

                verts, faces = _solid_to_mesh(solid)
                if verts and faces:
                    out_solids.append(solid)
                    matrix = getattr(solid, '_hippo_world_matrix', None)
                    source_obj = getattr(solid, '_hippo_source_object', None)
                    sdata = SolidMeshData(verts, faces, self.surface_color, self.light_vector, matrix=matrix, source_object=source_obj)
                    draw_inputs.append(sdata)
                    out_verts.append(verts)
                    out_faces.append(faces)
                    out_names.append(f"{self.base_name}_V{idx:03d}")
                    bake_items.append({
                        'name': f"{self.base_name}_V{idx:03d}",
                        'type': 'solid',
                        'solid': solid,
                    })
                    idx += 1

        _debug(f"get_geometry: {len(draw_inputs)} draw inputs, {len(out_surfaces)} surfaces, {len(out_solids)} solids")
        return {
            'draw': draw_inputs,
            'surfaces': out_surfaces,
            'solids': out_solids,
            'verts': out_verts,
            'faces': out_faces,
            'names': out_names,
            'bake_items': bake_items,
        }

    # -----------------------------------------------------------------------
    def draw_all(self, draw_inputs):
        if shading_3d is None:
            _debug("shading_3d unavailable, cannot register draw callback")
            return
        v_shader = gpu.shader.from_builtin(shading_3d.UNIFORM_COLOR)
        e_shader = gpu.shader.from_builtin(shading_3d.UNIFORM_COLOR)
        p_shader = gpu.shader.from_builtin(shading_3d.SMOOTH_COLOR)

        draw_data = {
            'tree_name': self.id_data.name[:],
            'custom_function': draw_hippo_surfaces,
            'args': (self, draw_inputs, v_shader, e_shader, p_shader)
        }
        _debug(f"Registering draw callback for node {node_id(self)}")
        callback_enable(node_id(self), draw_data)

    # -----------------------------------------------------------------------
    def process(self):
        if bpy.app.background:
            _debug("background mode, skipping viewer process")
            return
        if not (self.id_data.sv_show and self.activate):
            _debug(f"node disabled: sv_show={self.id_data.sv_show}, activate={self.activate}")
            callback_disable(node_id(self))
            return

        n_id = node_id(self)
        callback_disable(n_id)

        if not self.activate:
            return

        _debug(f"process node {n_id}")
        data = self.get_geometry()
        draw_inputs = data['draw']

        # Output flat lists (matching Sverchok conventions)
        if self.outputs['Surfaces'].is_linked:
            self.outputs['Surfaces'].sv_set(data['surfaces'])
        if self.outputs['Solids'].is_linked:
            self.outputs['Solids'].sv_set(data['solids'])
        if self.outputs['Vertices'].is_linked:
            self.outputs['Vertices'].sv_set(data['verts'])
        if self.outputs['Faces'].is_linked:
            self.outputs['Faces'].sv_set(data['faces'])
        if self.outputs['Names'].is_linked:
            self.outputs['Names'].sv_set(data['names'])

        if self.bake and data.get('bake_items'):
            context = bpy.context
            for item in data['bake_items']:
                _bake_item(context, item, location=self.location_offset)

        if draw_inputs:
            self.draw_all(draw_inputs)
        else:
            _debug("no draw inputs produced")

    # -----------------------------------------------------------------------
    def show_viewport(self, is_show: bool):
        if not self.activate:
            pass
        else:
            if is_show:
                self.process()
            else:
                callback_disable(node_id(self))

    def sv_free(self):
        callback_disable(node_id(self))


# ---------------------------------------------------------------------------
# Module registration
# ---------------------------------------------------------------------------
classes = [SvHippo3DOCCViewer, SvBakeHippoSurfaceOp]
register, unregister = bpy.utils.register_classes_factory(classes)
