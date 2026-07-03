#pragma once

#include <vector>
#include <array>
#include <pybind11/pybind11.h>

// Loft / ThruSections
// wire_shape_ids: list of shape IDs for each section wire
// closed: true if the loft should close from last section back to first
// solid: true to make a solid (must have closed planar sections), false for shell
int occ_loft(
    const std::vector<int>& wire_shape_ids,
    bool closed,
    bool solid
);

// Revolve
// profile_shape_id: shape ID of the profile wire/face
// axis_origin: point on the axis of revolution
// axis_dir: direction vector of the axis
// angle_deg: rotation angle in degrees
int occ_revolve(
    int profile_shape_id,
    const std::array<double, 3>& axis_origin,
    const std::array<double, 3>& axis_dir,
    double angle_deg
);

// Sweep1 (single rail)
// rail_shape_id: shape ID of the rail wire
// profile_shape_id: shape ID of the profile wire
// solid: true to make a solid (closed profile), false for shell
int occ_sweep1(
    int rail_shape_id,
    int profile_shape_id,
    bool solid
);

// Sweep2 (two rails)
// rail_shape_ids: list of 2 rail wire shape IDs
// profile_shape_ids: list of profile wire shape IDs (at least 1)
// solid: true to make a solid (closed profile), false for shell
int occ_sweep2(
    const std::vector<int>& rail_shape_ids,
    const std::vector<int>& profile_shape_ids,
    bool solid
);

// Planar Surface from closed planar wire
// wire_shape_id: shape ID of the closed planar wire
int occ_planar_srf(
    int wire_shape_id
);

// Edge Surface (filling from boundary edges)
// wire_shape_ids: 2-4 boundary wires
// continuity: 0=position, 1=tangent, 2=curvature (default 1 for G1)
int occ_edge_srf(
    const std::vector<int>& wire_shape_ids,
    int continuity
);

// ---------------------------------------------------------------------------
// Direct surface control-point editing
// ---------------------------------------------------------------------------

// Extract NURBS surface control points (U x V grid) from the first B-spline
// face in the given shape (shape_id).
// Returns a pybind11::dict with:
//   "u_deg", "v_deg", "u_count", "v_count", "uknots", "vknots", "umults",
//   "vmults", "poles" — where poles is a flat list of (x,y,z) for each point.
pybind11::dict extract_bsurf_control_points(int shape_id);

// Rebuild a B-spline face from an edited pole list.
// old_shape_id: the face to replace
// poles: flat list of U*V points (3 floats each)
// returns new shape_id
int set_bsurf_control_points(
    int old_shape_id,
    const std::vector<std::array<double, 3>>& poles
);

// Extract an isocurve at fixed U parameter from the first face of a shape.
// Returns a shape_id for the curve wire.
int extract_isocurve_u(int shape_id, double u_param);

// Extract an isocurve at fixed V parameter from the first face of a shape.
// Returns a shape_id for the curve wire.
int extract_isocurve_v(int shape_id, double v_param);

// Explode a solid/compound/shell into individual faces.
// Returns a list of shape_ids, one per face.
std::vector<int> explode_shape_to_faces(int shape_id);

// Project a 3D point onto the first face of a shape and return UV + normal.
// Returns dict with {"u", "v", "distance", "point" (x,y,z), "normal" (x,y,z), "found": bool}.
pybind11::dict project_point_to_surface_uv(int shape_id, const std::array<double, 3>& point);

// Get param bounds of the first face in a shape.
// Returns dict with {"u0", "u1", "v0", "v1", "found": bool}.
pybind11::dict get_surface_bounds(int shape_id);
