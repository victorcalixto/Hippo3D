#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include "occ_primitives.hpp"
#include "occ_registry.hpp"
#include "occ_step.hpp"
#include "occ_3dm.hpp"
#include "occ_curves.hpp"
#include "occ_surface_ops.hpp"
#include "occ_boolean.hpp"
#include "occ_trim.hpp"

namespace py = pybind11;

PYBIND11_MODULE(hippo_occ_core, m) {
    m.doc() = "Hippo3D native OpenCascade core";

    m.def("make_box_mesh", &make_box_mesh,
          py::arg("width"), py::arg("depth"), py::arg("height"), py::arg("deflection") = 0.1);

    m.def("make_sphere_mesh", &make_sphere_mesh,
          py::arg("radius"), py::arg("deflection") = 0.1);

    m.def("make_cylinder_mesh", &make_cylinder_mesh,
          py::arg("radius"), py::arg("height"), py::arg("deflection") = 0.1);

    m.def("make_cone_mesh", &make_cone_mesh,
          py::arg("radius1"), py::arg("radius2"), py::arg("height"), py::arg("deflection") = 0.1);

    m.def("make_torus_mesh", &make_torus_mesh,
          py::arg("major_radius"), py::arg("minor_radius"), py::arg("deflection") = 0.1);

    m.def("remesh_shape", &remesh_shape,
          py::arg("shape_id"), py::arg("deflection") = 0.1);

    m.def("get_shape_edges", &get_shape_edges,
          py::arg("shape_id"), py::arg("deflection") = 0.1);

    // STEP Data Exchange
    m.def("export_step", &export_step,
          py::arg("shape_id"), py::arg("filepath"),
          "Export a cached shape to STEP file. Returns (success, message).");

    m.def("export_step_multi", &export_step_multi,
          py::arg("shape_ids"), py::arg("filepath"),
          "Export multiple cached shapes to a single STEP file. Returns (success, message).");

    m.def("import_step", &import_step,
          py::arg("filepath"),
          "Import shapes from STEP file. Returns list of shape_ids.");

    // 3DM / OpenNURBS Data Exchange
    m.def("export_3dm", &export_3dm,
          py::arg("shape_id"), py::arg("filepath"),
          "Export a cached shape to .3dm file (BRep/Surface/Mesh). Returns (success, message).");

    m.def("export_3dm_brep", &export_3dm_brep,
          py::arg("shape_id"), py::arg("filepath"),
          "Export a cached shape to .3dm file as real BRep/Surface. Returns (success, message).");

    m.def("export_3dm_multi", &export_3dm_multi,
          py::arg("shape_ids"), py::arg("filepath"),
          "Export multiple cached shapes to a single .3dm file (BRep/Surface/Mesh per shape). Returns (success, message).");

    m.def("import_3dm", &import_3dm,
          py::arg("filepath"),
          "Import from .3dm file (Brep / Surface / Mesh). Returns list of (shape_id, type) tuples.");

    m.def("transform_shape", &transform_shape,
          py::arg("shape_id"), py::arg("matrix"),
          "Apply a 4x4 affine matrix (row-major list of 16) to a cached shape. Returns new shape_id.");

    m.def("delete_shape", &delete_shape,
          py::arg("shape_id"),
          "Remove a cached shape from registry.");

    m.def("shape_count", &shape_count);
    m.def("clear_registry", &clear_registry);
    m.def("has_shape", &has_shape,
          py::arg("shape_id"),
          "Check if a shape ID is still present in the registry.");

    // Curves / Wires
    m.def("make_polyline_wire", &make_polyline_wire,
          py::arg("points"), py::arg("closed") = false,
          "Create a wire from a sequence of point-connected line segments. Returns shape_id.");

    m.def("make_nurbs_curve", &make_nurbs_curve,
          py::arg("cvs"), py::arg("knots"), py::arg("mults"), py::arg("degree"), py::arg("periodic") = false,
          "Create a B-Spline curve/wire from control points (x,y,z,w), knots, multiplicities, degree. Returns shape_id.");

    m.def("make_circle_wire", &make_circle_wire,
          py::arg("center"), py::arg("radius"), py::arg("normal"), py::arg("start_angle") = 0.0, py::arg("end_angle") = 6.28318530718,
          "Create a circular arc wire from center, radius, normal, start/end angles (radians). Returns shape_id.");

    m.def("make_full_circle", &make_full_circle,
          py::arg("center"), py::arg("radius"), py::arg("normal"),
          "Create a full circle wire. Returns shape_id.");

    m.def("remesh_curve", &remesh_curve,
          py::arg("shape_id"), py::arg("deflection") = 0.1,
          "Sample a curve/wire into polylines for display. Returns dict with {edges: [[(x,y,z), ...], ...]}.");

    // Surface Operations
    m.def("occ_loft", &occ_loft,
          py::arg("wire_shape_ids"), py::arg("closed") = false, py::arg("solid") = false,
          "Loft through a series of wires. Returns shape_id.");

    m.def("occ_revolve", &occ_revolve,
          py::arg("profile_shape_id"), py::arg("axis_origin"), py::arg("axis_dir"), py::arg("angle_deg") = 360.0,
          "Revolve a profile around an axis. Returns shape_id.");

    m.def("occ_sweep1", &occ_sweep1,
          py::arg("rail_shape_id"), py::arg("profile_shape_id"), py::arg("solid") = false,
          "Sweep a profile along a single rail. Returns shape_id.");

    m.def("occ_sweep2", &occ_sweep2,
          py::arg("rail_shape_ids"), py::arg("profile_shape_ids"), py::arg("solid") = false,
          "Sweep a profile along two rails. Returns shape_id.");

    m.def("occ_planar_srf", &occ_planar_srf,
          py::arg("wire_shape_id"),
          "Create a planar surface from a closed planar wire. Returns shape_id.");

    m.def("occ_edge_srf", &occ_edge_srf,
          py::arg("wire_shape_ids"), py::arg("continuity") = 1,
          "Create a filling surface from 2-4 boundary wires. Returns shape_id.");

    // Boolean Operations
    m.def("occ_boolean_fuse", &occ_boolean_fuse,
          py::arg("shape_ids"),
          "Boolean union (fuse) of multiple shapes. Returns shape_id.");

    m.def("occ_boolean_cut", &occ_boolean_cut,
          py::arg("base_shape_id"), py::arg("tool_shape_ids"),
          "Boolean difference (cut) of base minus tools. Returns shape_id.");

    m.def("occ_boolean_common", &occ_boolean_common,
          py::arg("shape_a_id"), py::arg("shape_b_id"),
          "Boolean intersection (common) of two shapes. Returns shape_id.");

    // Trim / Split Operations
    m.def("occ_trim", &occ_trim,
          py::arg("surface_shape_id"), py::arg("cutter_shape_id"), py::arg("side_point"),
          "Trim surface by cutter, keep side containing side_point. Returns shape_id.");

    m.def("occ_split", &occ_split,
          py::arg("surface_shape_id"), py::arg("cutter_shape_id"),
          "Split surface by cutter, return all piece shape_ids.");

    m.def("occ_untrim", &occ_untrim,
          py::arg("trimmed_face_shape_id"),
          "Untrim a face to get the underlying surface. Returns shape_id.");

    // Surface Control-Point editing
    m.def("extract_bsurf_control_points", &extract_bsurf_control_points,
          py::arg("shape_id"),
          "Extract B-surface control points from shape. Returns dict with poles, knots, etc.");

    m.def("set_bsurf_control_points", &set_bsurf_control_points,
          py::arg("old_shape_id"), py::arg("poles"),
          "Rebuild a B-surface from an edited flat pole list. Returns new shape_id.");
}
