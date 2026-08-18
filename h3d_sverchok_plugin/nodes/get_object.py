# SPDX-License-Identifier: GPL-3.0-or-later
"""Hippo3D Get Object node for Sverchok.

Reads Blender objects tagged as Hippo3D OCC objects and outputs:
  - Vertices -> SvVerticesSocket (mesh vertices)
  - Faces    -> SvStringsSocket  (mesh face indices)
  - Surfaces -> SvSurfaceSocket  (SvNurbsSurface instances)
  - Solids   -> SvSolidSocket    (FreeCAD Part.Shape / Part.Solid)

Translation rules:
  * Surface  -> extract B-spline data from the OCC shape and build a
                Sverchok NURBS surface (Native, falling back to Geomdl).
  * Solid    -> build a FreeCAD Part.Solid from the OCC shape via a robust
                STEP round-trip, so downstream Sverchok Solids sockets receive
                the expected Part.Shape type.
  * Fallback -> mesh vertices/faces are still emitted on the Vertices/Faces
                sockets for backwards compatibility.
"""

import bpy
import numpy as np

# ---------------------------------------------------------------------------
# Diagnostic helpers (always print to System Console)
# ---------------------------------------------------------------------------
def _debug(msg):
    print(f"[Hippo3D GetObject Plugin] {msg}")

def _debug_exc(msg):
    import traceback
    print(f"[Hippo3D GetObject Plugin ERROR] {msg}")
    traceback.print_exc()

# ---------------------------------------------------------------------------
# Guarded sverchok imports
# ---------------------------------------------------------------------------
try:
    from sverchok.node_tree import SverchCustomTreeNode
except Exception as e:
    _debug_exc("Failed to import SverchCustomTreeNode")
    SverchCustomTreeNode = object

try:
    from sverchok.data_structure import updateNode
except Exception:
    def updateNode(*a, **k):
        pass

try:
    from sverchok.core.sv_custom_exceptions import SvNoDataError
except Exception:
    SvNoDataError = Exception

try:
    from sverchok.utils.surface.core import SvSurface
    _debug(f"SvSurface imported: {SvSurface}")
except Exception as e:
    _debug(f"SvSurface import failed: {e}")
    SvSurface = None

try:
    from sverchok.utils.surface.nurbs import SvNurbsSurface, SvNativeNurbsSurface, SvGeomdlSurface
    _debug(f"SvNurbsSurface imported: {SvNurbsSurface}")
except Exception as e:
    _debug(f"SvNurbsSurface import failed: {e}")
    SvNurbsSurface = None
    SvNativeNurbsSurface = None
    SvGeomdlSurface = None

try:
    from sverchok.utils.nurbs_common import SvNurbsMaths
    _debug(f"SvNurbsMaths imported: {SvNurbsMaths}")
except Exception as e:
    _debug(f"SvNurbsMaths import failed: {e}")
    SvNurbsMaths = None

# ---------------------------------------------------------------------------
# FreeCAD availability
# ---------------------------------------------------------------------------
FREECAD_AVAILABLE = False
try:
    import Part  # noqa: F401
    FREECAD_AVAILABLE = True
    _debug("FreeCAD Part module available")
except Exception as e:
    _debug(f"FreeCAD Part module NOT available: {e}")
    pass

# ---------------------------------------------------------------------------
# Hippo3D OCC core availability
#
# The plugin may be installed as a standalone add-on (h3d_sverchok_plugin) or
# as part of the main Hippo3D add-on (possibly under a bl_ext.* module name).
# We therefore try several ways to locate the shared kernels/occ_loader.py.
# ---------------------------------------------------------------------------
import importlib.util
import sys
from pathlib import Path


def _find_hippo3d_package_root():
    """Return the filesystem root of the Hippo3D add-on package, or None."""
    # 1. When the plugin is inside the main Hippo3D add-on, __file__ is at
    #    <repo>/h3d_sverchok_plugin/nodes/... so the repo root is two dirs up.
    candidate = Path(__file__).resolve().parents[2]
    if (candidate / "kernels" / "occ_loader.py").exists():
        _debug(f"Found Hippo3D root at {candidate}")
        return candidate
    # 2. Try known Blender extension / add-on module names already loaded in sys.modules.
    for ext_name in ["Hippo3D", "bl_ext.blender_development.Hippo3D"]:
        mod = sys.modules.get(ext_name)
        if mod is not None and hasattr(mod, "__file__"):
            candidate = Path(mod.__file__).resolve().parent
            if (candidate / "kernels" / "occ_loader.py").exists():
                _debug(f"Found Hippo3D root via sys.modules at {candidate}")
                return candidate
    # 3. Fallback: scan registered Blender add-ons for one that owns kernels/occ_loader.py.
    try:
        import bpy
        for addon_mod in bpy.context.preferences.addons.keys():
            try:
                addon = sys.modules.get(addon_mod)
                if addon is None or not hasattr(addon, "__file__"):
                    continue
                candidate = Path(addon.__file__).resolve().parent
                if (candidate / "kernels" / "occ_loader.py").exists():
                    _debug(f"Found Hippo3D root via Blender add-ons at {candidate}")
                    return candidate
            except Exception:
                pass
    except Exception:
        pass
    # 4. Final fallback: look through sys.path for a Hippo3D package with the loader.
    for p in sys.path:
        candidate = Path(p).resolve()
        if (candidate / "kernels" / "occ_loader.py").exists():
            _debug(f"Found Hippo3D root via sys.path at {candidate}")
            return candidate
        # also check one level below (e.g. <addons>/Hippo3D/kernels)
        for sub in candidate.iterdir():
            if sub.is_dir() and (sub / "kernels" / "occ_loader.py").exists():
                _debug(f"Found Hippo3D root via sys.path subdir at {sub}")
                return sub
    _debug("Could not locate Hippo3D package root")
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
    _debug("Hippo3D OCC core loaded successfully")
except Exception as e:
    _debug_exc(f"Failed to load Hippo3D OCC core: {e}")
    pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _expand_kv(knots, mults):
    """Rebuild a full knotvector from unique knots and multiplicities."""
    kv = []
    for k, m in zip(knots, mults):
        kv.extend([k] * int(m))
    return kv


# ---------------------------------------------------------------------------
# OCC shape restoration fallback
# ---------------------------------------------------------------------------
def _order_mesh_vertices_into_grid(mesh, rows, cols):
    """Attempt to order mesh vertices into a (rows+1) x (cols+1) grid.

    Returns a flat list of vertex indices in row-major order, or None.
    """
    import math
    from collections import defaultdict

    verts_local = [v.co.copy() for v in mesh.vertices]
    adj = defaultdict(set)
    for poly in mesh.polygons:
        v = list(poly.vertices)
        n = len(v)
        for i in range(n):
            a = v[i]
            b = v[(i + 1) % n]
            adj[a].add(b)
            adj[b].add(a)

    corner_candidates = [v for v in range(len(mesh.vertices)) if len(adj[v]) == 2]
    if not corner_candidates:
        return None

    start = corner_candidates[0]
    neighbors = list(adj[start])
    if len(neighbors) != 2:
        return None

    dir_a, dir_b = neighbors[0], neighbors[1]

    def walk_line(begin, direction):
        path = [begin]
        current = direction
        prev = begin
        while current != begin:
            path.append(current)
            candidates = [n for n in adj[current] if n != prev]
            if not candidates:
                break
            if len(candidates) == 1:
                prev, current = current, candidates[0]
            else:
                if len(adj[current]) == 3:
                    non_corner = [n for n in candidates if len(adj[n]) != 2]
                    if len(non_corner) == 1:
                        prev, current = current, non_corner[0]
                    else:
                        break
                elif len(adj[current]) == 4:
                    vec_prev = (verts_local[prev].x - verts_local[current].x,
                                verts_local[prev].y - verts_local[current].y,
                                verts_local[prev].z - verts_local[current].z)
                    best = None
                    best_dot = -2.0
                    for cand in candidates:
                        vec_cand = (verts_local[cand].x - verts_local[current].x,
                                    verts_local[cand].y - verts_local[current].y,
                                    verts_local[cand].z - verts_local[current].z)
                        lp = math.sqrt(vec_prev[0]**2 + vec_prev[1]**2 + vec_prev[2]**2)
                        lc = math.sqrt(vec_cand[0]**2 + vec_cand[1]**2 + vec_cand[2]**2)
                        if lp < 1e-9 or lc < 1e-9:
                            continue
                        dot = (vec_prev[0]*vec_cand[0] + vec_prev[1]*vec_cand[1] + vec_prev[2]*vec_cand[2]) / (lp*lc)
                        if dot > best_dot:
                            best_dot = dot
                            best = cand
                    if best is None:
                        break
                    prev, current = current, best
                else:
                    break
            if len(path) > len(mesh.vertices):
                return None
        return path

    row_path = walk_line(start, dir_a)
    if len(row_path) != cols + 1:
        row_path = walk_line(start, dir_b)
        if len(row_path) != cols + 1:
            return None
        col_dir = dir_a
    else:
        col_dir = dir_b

    grid = []
    for i, row_start in enumerate(row_path):
        if i == 0:
            col_path = walk_line(row_start, col_dir)
        else:
            prev_row_start = row_path[i - 1]
            candidates = [n for n in adj[row_start] if n != prev_row_start]
            if i + 1 < len(row_path):
                next_row_start = row_path[i + 1]
                candidates = [n for n in candidates if n != next_row_start]
            if len(candidates) != 1:
                return None
            col_path = walk_line(row_start, candidates[0])
        if len(col_path) != rows + 1:
            return None
        grid.extend(col_path)

    return grid


def _try_fit_bspline_surface_from_mesh(occ, mesh, verts_local, mw):
    """Detect a clean quad-grid topology and fit a B-spline surface.

    Returns a new OCC shape_id, or None if the mesh is not a clean grid.
    """
    from collections import defaultdict

    polys = mesh.polygons
    n_polys = len(polys)
    if n_polys == 0:
        return None

    for poly in polys:
        if len(poly.vertices) != 4:
            return None

    edge_faces = defaultdict(list)
    for fi, poly in enumerate(polys):
        v = list(poly.vertices)
        n = len(v)
        for i in range(n):
            a = v[i]
            b = v[(i + 1) % n]
            edge = tuple(sorted((a, b)))
            edge_faces[edge].append(fi)

    if any(len(fs) != 1 and len(fs) != 2 for fs in edge_faces.values()):
        return None

    total_verts = len(verts_local)
    best_rows = None
    best_cols = None
    best_err = float('inf')
    for rows in range(1, n_polys + 1):
        if n_polys % rows == 0:
            cols = n_polys // rows
            expected_verts = (rows + 1) * (cols + 1)
            err = abs(expected_verts - total_verts)
            if err < best_err:
                best_err = err
                best_rows = rows
                best_cols = cols

    if best_rows is None or best_err != 0:
        return None

    rows = best_rows
    cols = best_cols
    grid = _order_mesh_vertices_into_grid(mesh, rows, cols)
    if grid is None:
        return None

    grid_world = []
    for vi in grid:
        p = mw @ verts_local[vi]
        grid_world.append([p.x, p.y, p.z])

    try:
        sid = occ.make_bspline_surface_from_grid(rows + 1, cols + 1, grid_world)
        return sid if sid >= 0 else None
    except Exception:
        return None


def _is_hippo3d_occ_object(obj):
    """Return True if the object is a Hippo3D OCC object in any form."""
    if obj is None or obj.type != "MESH":
        return False
    if obj.get("hippo_kernel") == "occ":
        return True
    if obj.get("hippo_occ_preview") is True:
        return True
    for key in obj.keys():
        if isinstance(key, str) and key.startswith("hippo_occ_"):
            return True
    return False


def _restore_occ_shape_from_mesh(obj):
    """Rebuild an OCC shape for a Hippo3D OCC preview object when the in-memory
    shape registry has lost the original shape_id.

    First tries to fit an editable B-spline surface from a quad-grid mesh,
    then falls back to a plain mesh-derived shape.  Updates the object's
    hippo_occ_shape_id property with the new shape_id.

    Returns the new shape_id or -1 on failure.
    """
    if obj is None or obj.type != "MESH" or obj.data is None:
        return -1

    if not _is_hippo3d_occ_object(obj):
        _debug(f"Object {obj.name} is not a Hippo3D OCC preview object, skipping restore")
        return -1

    if not HIPPO3D_OCC_AVAILABLE:
        _debug("OCC core not available, cannot restore shape from mesh")
        return -1

    try:
        occ = _load_hippo_occ_core()
    except Exception as e:
        _debug_exc(f"Failed to load OCC core for restore: {e}")
        return -1

    mesh = obj.data
    mw = obj.matrix_world
    verts_local = [v.co.copy() for v in mesh.vertices]

    # Try editable B-spline surface reconstruction first.
    try:
        sid = _try_fit_bspline_surface_from_mesh(occ, mesh, verts_local, mw)
        if sid is not None and sid >= 0:
            obj["hippo_occ_shape_id"] = int(sid)
            obj["hippo_occ_nurbs_shape_id"] = int(sid)
            _debug(f"Restored shape {sid} for {obj.name} from B-spline grid mesh")
            return sid
    except Exception as e:
        _debug_exc(f"B-spline grid restore for {obj.name} failed: {e}")

    # Fallback: plain mesh-to-shape sewing.
    try:
        vertices = []
        for v in mesh.vertices:
            p = mw @ v.co
            vertices.append([p.x, p.y, p.z])

        faces = []
        for poly in mesh.polygons:
            face = [v for v in poly.vertices]
            if len(face) >= 3:
                faces.append(face)

        if not faces:
            _debug(f"Object {obj.name} has no usable faces for mesh restore")
            return -1

        sid = occ.make_shape_from_mesh(vertices, faces)
        if sid < 0:
            _debug(f"make_shape_from_mesh failed for {obj.name}")
            return -1

        obj["hippo_occ_shape_id"] = int(sid)
        _debug(f"Restored shape {sid} for {obj.name} from mesh (fallback)")
        return sid
    except Exception as e:
        _debug_exc(f"Mesh restore for {obj.name} failed: {e}")
        return -1


def _occ_bsurf_to_sverchok_nurbs(bsurf_data, log_prefix=""):
    """Convert OCC B-surface control-point data to a Sverchok NURBS surface."""
    if bsurf_data is None:
        _debug(f"{log_prefix} No bsurf data received")
        return None

    degree_u = bsurf_data.get("u_deg", 3)
    degree_v = bsurf_data.get("v_deg", 3)
    poles = bsurf_data.get("poles", [])
    uknots = bsurf_data.get("uknots", [])
    vknots = bsurf_data.get("vknots", [])
    umults = bsurf_data.get("umults", [])
    vmults = bsurf_data.get("vmults", [])
    rational = bsurf_data.get("rational", False)
    u_count = bsurf_data.get("u_count", 0)
    v_count = bsurf_data.get("v_count", 0)

    _debug(f"{log_prefix} bsurf keys: {list(bsurf_data.keys())}")
    _debug(f"{log_prefix} degree={degree_u}x{degree_v}, poles={len(poles)}, uknots={len(uknots)}, vknots={len(vknots)}, umults={len(umults)}, vmults={len(vmults)}, u_count={u_count}, v_count={v_count}, rational={rational}")

    if not poles or not uknots or not vknots:
        _debug(f"{log_prefix} Missing poles or knots")
        return None
    if u_count == 0 or v_count == 0:
        _debug(f"{log_prefix} Zero u_count or v_count")
        return None

    try:
        knotvector_u = _expand_kv(uknots, umults)
        knotvector_v = _expand_kv(vknots, vmults)
        _debug(f"{log_prefix} expanded knotvectors: u={len(knotvector_u)}, v={len(knotvector_v)}")
    except Exception as e:
        _debug_exc(f"{log_prefix} Failed to expand knotvectors: {e}")
        return None

    try:
        ctrlpts = np.array(poles).reshape((u_count, v_count, 3))
        _debug(f"{log_prefix} control points reshaped to {ctrlpts.shape}")
    except Exception as e:
        _debug_exc(f"{log_prefix} Failed to reshape control points (len={len(poles)}, u_count={u_count}, v_count={v_count}): {e}")
        return None

    weights = None
    if rational:
        # The OCC core currently does not return per-pole weights, so we fall
        # back to unit weights.  This is geometrically correct only when the
        # original surface was non-rational; true rational surfaces will be
        # approximated (poles are correct, weights are not).
        weights = np.ones((u_count, v_count))
        _debug(f"{log_prefix} Surface is marked rational but weights are not available; using unit weights (approximation)")

    # Build using SvNurbsMaths.build_surface.  Try GEOMDL first because the
    # native Python NURBS evaluator can produce numerically less accurate
    # results for surfaces coming from OpenCASCADE (especially periodic or
    # clamped knots), whereas geomdl is more robust.
    if SvNurbsSurface is not None and SvNurbsMaths is not None:
        _debug(f"{log_prefix} Trying SvNurbsSurface.build with GEOMDL/NATIVE")
        # GEOMDL expects control points as a plain Python list-of-lists.
        ctrlpts_list = ctrlpts.tolist() if hasattr(ctrlpts, 'tolist') else ctrlpts
        for impl_name, impl in (("GEOMDL", SvNurbsMaths.GEOMDL), ("NATIVE", SvNurbsMaths.NATIVE)):
            try:
                cp_in = ctrlpts_list if impl_name == "GEOMDL" else ctrlpts
                surf = SvNurbsSurface.build(
                    impl,
                    degree_u, degree_v,
                    knotvector_u, knotvector_v,
                    cp_in, weights,
                    normalize_knots=False
                )
                if surf is not None:
                    _debug(f"{log_prefix} Created surface with {impl_name}: {type(surf).__name__}, isinstance SvNurbsSurface={isinstance(surf, SvNurbsSurface)}, isinstance SvSurface={isinstance(surf, SvSurface)}")
                    return surf
            except Exception as e:
                _debug_exc(f"{log_prefix} SvNurbsSurface.build({impl_name}) failed: {e}")
                continue

    # Direct geomdl constructor fallback.
    if SvGeomdlSurface is not None:
        try:
            surf = SvGeomdlSurface.build_geomdl(
                degree_u, degree_v,
                knotvector_u, knotvector_v,
                ctrlpts_list, weights,
                normalize_knots=False
            )
            _debug(f"{log_prefix} Created SvGeomdlSurface directly")
            return surf
        except Exception as e:
            _debug_exc(f"{log_prefix} SvGeomdlSurface direct constructor failed: {e}")

    # Direct native constructor fallback.
    if SvNativeNurbsSurface is not None:
        try:
            surf = SvNativeNurbsSurface(
                degree_u, degree_v,
                knotvector_u, knotvector_v,
                ctrlpts, weights,
                normalize_knots=False
            )
            _debug(f"{log_prefix} Created SvNativeNurbsSurface directly")
            return surf
        except Exception as e:
            _debug_exc(f"{log_prefix} SvNativeNurbsSurface direct constructor failed: {e}")

    _debug(f"{log_prefix} All surface creation methods failed")
    return None


# ---------------------------------------------------------------------------
# Surface extraction
# ---------------------------------------------------------------------------
def _extract_occ_surfaces(shape_id):
    """Return a list of Sverchok NURBS surfaces for the given OCC shape_id."""
    if not HIPPO3D_OCC_AVAILABLE:
        _debug("OCC core not available, cannot extract surfaces")
        return []

    surfaces = []
    try:
        occ = _load_hippo_occ_core()
    except Exception as e:
        _debug_exc(f"Failed to load OCC core: {e}")
        return []

    if not occ.has_shape(shape_id):
        _debug(f"OCC shape {shape_id} not found in registry")
        return []

    _debug(f"Extracting surfaces from OCC shape {shape_id}")

    # Always explode the shape to its faces and convert every face.  A shape
    # can be a single face, a shell, a solid, or a compound; exploding gives a
    # uniform list of face IDs and makes sure we don't silently drop faces
    # from multi-face objects.
    try:
        face_ids = occ.explode_shape_to_faces(shape_id)
        _debug(f"Shape {shape_id} exploded into {len(face_ids)} faces")
    except Exception as e:
        _debug_exc(f"Error exploding shape {shape_id} to faces: {e}")
        face_ids = []

    for fid in face_ids:
        try:
            bsurf_data = occ.extract_bsurf_control_points(fid)
            if not bsurf_data:
                _debug(f"Face {fid} of shape {shape_id} has no bsurf data")
                continue
            nurbs = _occ_bsurf_to_sverchok_nurbs(bsurf_data, f"shape {shape_id} face {fid}: ")
            if nurbs is not None:
                nurbs._hippo_shape_id = fid
                surfaces.append(nurbs)
                _debug(f"Converted face {fid} of shape {shape_id} to Sverchok surface")
            else:
                _debug(f"Failed to convert face {fid} of shape {shape_id}")
        except Exception as e:
            _debug_exc(f"Error converting face {fid} of shape {shape_id}: {e}")

    _debug(f"Extracted {len(surfaces)} surfaces from shape {shape_id}")
    return surfaces


def _attach_object_metadata(item, obj):
    """Store source object name/world matrix on a surface or solid for downstream viewers/bakers."""
    try:
        item._hippo_source_object = obj.name
        item._hippo_world_matrix = obj.matrix_world.copy()
    except Exception as e:
        _debug_exc(f"Could not attach object metadata to {type(item).__name__}: {e}")

    # Preserve Hippo3D primitive metadata so baked solids can recover dimension handles.
    try:
        occ_type = obj.get("hippo_occ_type", "")
        if occ_type in {"box", "sphere", "cylinder", "cone", "torus"}:
            item._hippo_occ_type = occ_type
            if occ_type == "box":
                item._hippo_occ_width = float(obj.get("hippo_occ_width", 10.0))
                item._hippo_occ_depth = float(obj.get("hippo_occ_depth", 10.0))
                item._hippo_occ_height = float(obj.get("hippo_occ_height", 10.0))
            elif occ_type == "sphere":
                item._hippo_occ_radius = float(obj.get("hippo_occ_radius", 5.0))
            elif occ_type == "cylinder":
                item._hippo_occ_radius = float(obj.get("hippo_occ_radius", 5.0))
                item._hippo_occ_height = float(obj.get("hippo_occ_height", 10.0))
            elif occ_type == "cone":
                item._hippo_occ_radius1 = float(obj.get("hippo_occ_radius1", 5.0))
                item._hippo_occ_radius2 = float(obj.get("hippo_occ_radius2", 0.0))
                item._hippo_occ_height = float(obj.get("hippo_occ_height", 10.0))
            elif occ_type == "torus":
                item._hippo_occ_major_radius = float(obj.get("hippo_occ_major_radius", 5.0))
                item._hippo_occ_minor_radius = float(obj.get("hippo_occ_minor_radius", 1.25))
    except Exception as e:
        _debug_exc(f"Could not attach primitive metadata to {type(item).__name__}: {e}")


def _resolve_shape_id(obj, prefer_nurbs=False):
    """Return a usable OCC shape id for the object, restoring from mesh if needed.

    When prefer_nurbs is True (e.g. surface extraction), the precise NURBS shape
    id stored before remeshing is used if it is still valid.  Otherwise the
    visible display shape id is used and restored from mesh when necessary.
    """
    shape_id = None
    try:
        shape_id = int(obj.get("hippo_occ_shape_id", -1))
    except Exception as e:
        _debug_exc(f"Object {obj.name} has invalid hippo_occ_shape_id: {e}")
        pass

    nurbs_id = None
    if prefer_nurbs:
        try:
            nurbs_id = int(obj.get("hippo_occ_nurbs_shape_id", -1))
        except Exception as e:
            _debug_exc(f"Object {obj.name} has invalid hippo_occ_nurbs_shape_id: {e}")
            pass

    try:
        occ = _load_hippo_occ_core()
    except Exception as e:
        _debug_exc(f"Failed to load OCC core for shape resolution: {e}")
        return -1

    # Prefer the precise NURBS id when it is still registered.
    if prefer_nurbs and nurbs_id is not None and nurbs_id >= 0 and occ.has_shape(nurbs_id):
        _debug(f"Object {obj.name} -> precise NURBS shape {nurbs_id}")
        return nurbs_id

    if shape_id is None or shape_id < 0:
        _debug(f"No stored shape_id for {obj.name}; attempting restore from mesh")
        return _restore_occ_shape_from_mesh(obj)

    if not occ.has_shape(shape_id):
        _debug(f"OCC shape {shape_id} missing; attempting restore from mesh for {obj.name}")
        return _restore_occ_shape_from_mesh(obj)

    _debug(f"Object {obj.name} -> OCC shape {shape_id}")
    return shape_id


def _wrap_as_surfaces(obj):
    """Extract parametric surfaces from a Hippo3D OCC object."""
    if obj is None:
        _debug("Received None object")
        return []

    _debug(f"Processing object: {obj.name}, type={obj.type}, hippo_kernel={obj.get('hippo_kernel')}, hippo_occ_shape_id={obj.get('hippo_occ_shape_id')}, hippo_occ_nurbs_shape_id={obj.get('hippo_occ_nurbs_shape_id')}")

    mesh = obj.data
    if mesh is None or obj.type != "MESH":
        _debug(f"Object {obj.name} is not a mesh (type={obj.type}, data={mesh})")
        return []

    if not _is_hippo3d_occ_object(obj):
        _debug(f"Object {obj.name} is not a Hippo3D OCC object (hippo_kernel={obj.get('hippo_kernel')})")
        return []

    shape_id = _resolve_shape_id(obj, prefer_nurbs=True)
    if shape_id < 0:
        _debug(f"Could not resolve OCC shape for {obj.name}")
        return []

    return _extract_occ_surfaces(shape_id)


# ---------------------------------------------------------------------------
# Solid extraction
# ---------------------------------------------------------------------------
def _occ_shape_is_solid_like(shape_id):
    """Return True if the OCC shape is a solid, compound of solids, or a shell
    that should be treated as a solid by downstream Sverchok nodes."""
    if not HIPPO3D_OCC_AVAILABLE:
        return False
    try:
        occ = _load_hippo_occ_core()
        if not occ.has_shape(shape_id):
            return False

        # Fast path: ask OCC for the shape topology directly.
        if hasattr(occ, 'get_shape_type'):
            shape_type = occ.get_shape_type(shape_id)
            _debug(f"Shape {shape_id} OCC type = {shape_type}")
            if shape_type in ('solid', 'compsolid', 'compound'):
                return True
            if shape_type in ('shell',):
                # Shell might be a closed solid-like shell; fall through to
                # check whether FreeCAD can seal it into a solid.
                return True
            return False

        # Fallback for older native modules without get_shape_type.
        face_ids = occ.explode_shape_to_faces(shape_id)
        if len(face_ids) < 4:
            return False
        # If any face is degree 1x1 it's likely a tessellated/meshed shell, not a
        # precise solid.  (This misses planar-faced solids, so prefer the native
        # get_shape_type path above.)
        for fid in face_ids:
            bsurf_data = occ.extract_bsurf_control_points(fid)
            if not bsurf_data:
                continue
            if bsurf_data.get("u_deg", 0) == 1 and bsurf_data.get("v_deg", 0) == 1:
                return False
        return True
    except Exception:
        return False


def _transform_solid_by_object_matrix(shape, obj):
    """Apply a Blender object's world matrix to a FreeCAD Part.Shape."""
    try:
        import FreeCAD
        import Part
        m = obj.matrix_world
        # FreeCAD.Matrix constructor expects row-major 4x4 values.
        fc_matrix = FreeCAD.Matrix(
            m[0][0], m[0][1], m[0][2], m[0][3],
            m[1][0], m[1][1], m[1][2], m[1][3],
            m[2][0], m[2][1], m[2][2], m[2][3],
            0.0, 0.0, 0.0, 1.0
        )
        transformed = shape.copy()
        transformed.transformShape(fc_matrix)
        _debug(f"Transformed solid by world matrix of {obj.name}")
        return transformed
    except Exception as e:
        _debug_exc(f"Could not transform solid by object matrix: {e}")
        return shape


def _wrap_as_solid(obj):
    """Build a FreeCAD Part.Solid from the OCC shape via STEP round-trip.

    The resulting solid is transformed into the source object's world space so
    that downstream viewers and bakers show it at the correct location.
    """
    if not FREECAD_AVAILABLE:
        _debug("FreeCAD not available, cannot extract solid")
        return None

    if obj is None:
        _debug("Received None object for solid extraction")
        return None

    _debug(f"Processing solid for object: {obj.name}, hippo_kernel={obj.get('hippo_kernel')}, hippo_occ_shape_id={obj.get('hippo_occ_shape_id')}")

    if not _is_hippo3d_occ_object(obj):
        _debug(f"Object {obj.name} is not a Hippo3D OCC object")
        return None

    if not HIPPO3D_OCC_AVAILABLE:
        _debug(f"OCC core not available for solid extraction on {obj.name}")
        return None

    occ = _load_hippo_occ_core()
    shape_id = _resolve_shape_id(obj, prefer_nurbs=False)
    if shape_id < 0:
        _debug(f"Could not resolve OCC shape for solid extraction on {obj.name}")
        return None

    # Only treat genuinely solid-like shapes as solids.  Single surfaces (lofts,
    # planar srfs, etc.) should stay on the Surfaces output.
    if not _occ_shape_is_solid_like(shape_id):
        _debug(f"Shape {shape_id} for {obj.name} is not solid-like; skipping solid output")
        return None

    try:
        import tempfile
        import os
        import Part

        with tempfile.NamedTemporaryFile(suffix=".step", delete=False) as tf:
            step_path = tf.name
        try:
            ok, msg = occ.export_step(shape_id, step_path)
            _debug(f"STEP export for shape {shape_id}: ok={ok}, msg={msg}")
            if not ok:
                _debug(f"STEP export failed: {msg}")
                return None

            shape = Part.Shape()
            shape.read(step_path)
            _debug(f"Read STEP: ShapeType={shape.ShapeType}, isNull={shape.isNull()}, Volume={getattr(shape, 'Volume', 'N/A')}")
            if shape.isNull():
                _debug("STEP shape is null")
                return None

            # Make sure downstream nodes see a real solid.
            result = None
            if shape.ShapeType == "Solid":
                _debug(f"Returning Part.Solid directly")
                result = shape
            elif hasattr(shape, 'Solids') and shape.Solids:
                _debug(f"Extracting first solid from compound")
                result = shape.Solids[0]
            else:
                try:
                    solid = Part.Solid(shape)
                    if solid.isNull() or (hasattr(solid, 'Volume') and solid.Volume <= 1e-9):
                        _debug(f"Part.Solid from {shape.ShapeType} is null or zero volume; treating as non-solid")
                        return None
                    _debug(f"Created Part.Solid from {shape.ShapeType}")
                    result = solid
                except Exception as e:
                    _debug_exc(f"Part.Solid failed: {e}")
                    return None

            if result is None:
                return None

            # Transform into the source object's world space.
            result = _transform_solid_by_object_matrix(result, obj)
            return result
        finally:
            try:
                os.remove(step_path)
            except Exception:
                pass
    except Exception as e:
        _debug_exc(f"Solid extraction failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Node definition
# ---------------------------------------------------------------------------
class SvHippo3DGetObject(bpy.types.Node, SverchCustomTreeNode):
    bl_idname = 'SvHippo3DGetObject'
    bl_label = 'Hippo3D Get Object'
    bl_icon = 'MESH_DATA'

    def sv_init(self, context):
        self.inputs.new('SvObjectSocket', "Objects")
        self.outputs.new('SvVerticesSocket', "Vertices")
        self.outputs.new('SvStringsSocket', "Faces")

        # Surface and Solid sockets may not exist in all Sverchok builds; fall
        # back to a generic string socket so the node still registers.
        try:
            s = self.outputs.new('SvSurfaceSocket', "Surfaces")
            _debug(f"Surfaces socket created as {s.bl_idname}")
        except Exception as e:
            _debug(f"SvSurfaceSocket not available, falling back to SvStringsSocket: {e}")
            self.outputs.new('SvStringsSocket', "Surfaces")
        try:
            s = self.outputs.new('SvSolidSocket', "Solids")
            _debug(f"Solids socket created as {s.bl_idname}")
        except Exception as e:
            _debug(f"SvSolidSocket not available, falling back to SvStringsSocket: {e}")
            self.outputs.new('SvStringsSocket', "Solids")

    def process(self):
        _debug("=== Get Object process started ===")
        _debug(f"Sverchok classes: SvSurface={SvSurface is not None}, SvNurbsSurface={SvNurbsSurface is not None}, SvNurbsMaths={SvNurbsMaths is not None}")

        verts_linked = self.outputs['Vertices'].is_linked
        faces_linked = self.outputs['Faces'].is_linked
        surfaces_linked = self.outputs['Surfaces'].is_linked
        solids_linked = self.outputs['Solids'].is_linked

        _debug(f"outputs linked: Vertices={verts_linked}, Faces={faces_linked}, Surfaces={surfaces_linked}, Solids={solids_linked}")

        if not (verts_linked or faces_linked or surfaces_linked or solids_linked):
            _debug("No outputs linked, skipping")
            return

        objects_socket = self.inputs['Objects']
        objects_raw = None
        if objects_socket.is_linked:
            try:
                objects_raw = objects_socket.sv_get(deepcopy=False)
                _debug(f"Received {len(objects_raw)} raw object groups from linked socket")
            except SvNoDataError:
                raise
            except Exception as e:
                _debug_exc(f"Error getting objects from linked socket: {e}")
                return
        else:
            # Socket not linked: use the object selected in the socket's UI.  In
            # modern Sverchok the object reference is stored on the socket as a
            # PointerProperty named object_ref_pointer.
            selected_obj = None
            for attr in ('object_ref_pointer', 'object_ref'):
                try:
                    candidate = getattr(objects_socket, attr, None)
                    if candidate is None:
                        continue
                    if isinstance(candidate, str) and not candidate:
                        continue
                    selected_obj = candidate
                    _debug(f"Using unlinked object from socket attribute {attr}: {selected_obj}")
                    break
                except Exception:
                    pass
            if selected_obj is None:
                _debug("Objects input not linked and no object selected in socket")
                return
            objects_raw = [[selected_obj]]

        # Handle different nesting levels of input
        if not objects_raw:
            objects_nested = []
        elif isinstance(objects_raw[0], (bpy.types.Object, str)):
            objects_nested = [objects_raw]
        else:
            objects_nested = objects_raw

        out_verts = []
        out_faces = []
        out_surfaces = []
        out_solids = []

        for obj_list in objects_nested:
            verts_sub = []
            faces_sub = []

            for item in obj_list:
                if item is None:
                    continue
                if isinstance(item, bpy.types.Object):
                    obj = item
                elif isinstance(item, str):
                    obj = bpy.data.objects.get(item)
                else:
                    obj = bpy.data.objects.get(str(item))

                if obj is None or not isinstance(obj, bpy.types.Object):
                    _debug(f"Skipping invalid item: {item}")
                    continue

                mesh = obj.data
                if mesh is None or obj.type != "MESH":
                    _debug(f"Object {obj.name} is not a mesh")
                    continue

                local_verts = [tuple(v.co) for v in mesh.vertices]
                local_faces = [list(p.vertices) for p in mesh.polygons]

                if verts_linked:
                    verts_sub.append(local_verts)
                if faces_linked:
                    faces_sub.append(local_faces)
                if surfaces_linked:
                    surfaces = _wrap_as_surfaces(obj)
                    if surfaces:
                        for s in surfaces:
                            _attach_object_metadata(s, obj)
                        out_surfaces.extend(surfaces)
                    else:
                        _debug(f"No surfaces extracted from {obj.name}")
                if solids_linked:
                    solid = _wrap_as_solid(obj)
                    if solid is not None:
                        _attach_object_metadata(solid, obj)
                        out_solids.append(solid)
                    else:
                        _debug(f"No solid extracted from {obj.name}")

            out_verts.append(verts_sub)
            out_faces.append(faces_sub)

        _debug(f"=== Get Object output: verts={len(out_verts)}, faces={len(out_faces)}, surfaces={len(out_surfaces)}, solids={len(out_solids)} ===")
        if out_surfaces:
            for i, s in enumerate(out_surfaces):
                _debug(f"  Surface {i}: type={type(s).__name__}, isinstance SvSurface={isinstance(s, SvSurface) if SvSurface else 'N/A'}")
        if out_solids:
            for i, s in enumerate(out_solids):
                try:
                    import Part
                    _debug(f"  Solid {i}: type={type(s).__name__}, isinstance Part.Shape={isinstance(s, Part.Shape)}")
                except Exception:
                    pass

        if verts_linked:
            self.outputs['Vertices'].sv_set(out_verts)
        if faces_linked:
            self.outputs['Faces'].sv_set(out_faces)
        if surfaces_linked:
            # Flat list of SvNurbsSurface instances (matches Sverchok convention)
            self.outputs['Surfaces'].sv_set(out_surfaces)
        if solids_linked:
            # Flat list of Part.Shape/Part.Solid (matches Sverchok convention)
            self.outputs['Solids'].sv_set(out_solids)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------
classes = [SvHippo3DGetObject]


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
