#include "occ_mesh.hpp"

#include <pybind11/stl.h>

#include <BRep_Tool.hxx>
#include <BRepMesh_IncrementalMesh.hxx>
#include <BRepAdaptor_Curve.hxx>

#include <TopAbs_Orientation.hxx>
#include <TopExp_Explorer.hxx>
#include <TopoDS.hxx>
#include <TopoDS_Face.hxx>
#include <TopoDS_Edge.hxx>
#include <TopLoc_Location.hxx>

#include <Poly_Triangulation.hxx>
#include <Poly_Triangle.hxx>

#include <gp_Pnt.hxx>
#include <gp_Trsf.hxx>

#include <array>
#include <vector>

namespace py = pybind11;

struct MeshData {
    std::vector<std::array<double, 3>> vertices;
    std::vector<std::array<int, 3>> faces;
};

static MeshData shape_to_mesh(const TopoDS_Shape& shape, double deflection) {
    BRepMesh_IncrementalMesh mesher(shape, deflection);

    MeshData result;
    int vertex_offset = 0;

    for (TopExp_Explorer exp(shape, TopAbs_FACE); exp.More(); exp.Next()) {
        TopoDS_Face face = TopoDS::Face(exp.Current());
        TopLoc_Location location;

        Handle(Poly_Triangulation) triangulation =
            BRep_Tool::Triangulation(face, location);

        if (triangulation.IsNull()) {
            continue;
        }

        const gp_Trsf transform = location.Transformation();
        const int node_count = triangulation->NbNodes();

        for (int i = 1; i <= node_count; ++i) {
            gp_Pnt p = triangulation->Node(i).Transformed(transform);
            result.vertices.push_back({p.X(), p.Y(), p.Z()});
        }

        const int triangle_count = triangulation->NbTriangles();

        for (int i = 1; i <= triangle_count; ++i) {
            Poly_Triangle triangle = triangulation->Triangle(i);

            int n1, n2, n3;
            triangle.Get(n1, n2, n3);

            int a = vertex_offset + (n1 - 1);
            int b = vertex_offset + (n2 - 1);
            int c = vertex_offset + (n3 - 1);

            if (face.Orientation() == TopAbs_REVERSED) {
                result.faces.push_back({a, c, b});
            } else {
                result.faces.push_back({a, b, c});
            }
        }

        vertex_offset = static_cast<int>(result.vertices.size());
    }

    return result;
}

py::dict shape_to_mesh_dict(const TopoDS_Shape& shape, double deflection) {
    MeshData mesh = shape_to_mesh(shape, deflection);

    py::dict data;
    data["vertices"] = mesh.vertices;
    data["faces"] = mesh.faces;
    return data;
}

py::list shape_edges_to_list(const TopoDS_Shape& shape, double deflection) {
    py::list edges;

    const int min_samples = 8;
    const int max_samples = 96;

    for (TopExp_Explorer exp(shape, TopAbs_EDGE); exp.More(); exp.Next()) {
        TopoDS_Edge edge = TopoDS::Edge(exp.Current());

        double first = 0.0;
        double last = 0.0;
        BRepAdaptor_Curve curve(edge);
        first = curve.FirstParameter();
        last = curve.LastParameter();

        if (last <= first) {
            continue;
        }

        int samples = static_cast<int>((last - first) / deflection);
        if (samples < min_samples) {
            samples = min_samples;
        }
        if (samples > max_samples) {
            samples = max_samples;
        }

        py::list polyline;

        for (int i = 0; i <= samples; ++i) {
            const double t = first + (last - first) * (static_cast<double>(i) / samples);
            gp_Pnt p = curve.Value(t);
            polyline.append(py::make_tuple(p.X(), p.Y(), p.Z()));
        }

        edges.append(polyline);
    }

    return edges;
}

py::dict shape_to_display_dict(const TopoDS_Shape& shape, int shape_id, double deflection) {
    py::dict data = shape_to_mesh_dict(shape, deflection);
    data["shape_id"] = shape_id;
    data["edges"] = shape_edges_to_list(shape, deflection);
    return data;
}

#include "occ_registry.hpp"
#include <BRepBuilderAPI_MakePolygon.hxx>
#include <BRepBuilderAPI_MakeFace.hxx>
#include <BRepBuilderAPI_Sewing.hxx>
#include <TopoDS_Shell.hxx>
#include <TopoDS_Compound.hxx>
#include <BRep_Builder.hxx>
#include <gp_Pnt.hxx>
#include <ShapeFix_Shell.hxx>
#include <ShapeFix_Face.hxx>
#include <GeomAPI_PointsToBSplineSurface.hxx>
#include <Geom_BSplineSurface.hxx>
#include <Geom_Plane.hxx>
#include <BRepBuilderAPI_MakeSolid.hxx>
#include <BRepBuilderAPI_MakeShell.hxx>
#include <BRepBuilderAPI_FindPlane.hxx>
#include <BRepBuilderAPI_MakeVertex.hxx>
#include <BRepBuilderAPI_MakeEdge.hxx>
#include <BRepBuilderAPI_MakeWire.hxx>
#include <TopoDS_Vertex.hxx>
#include <TColgp_Array2OfPnt.hxx>
#include <TColStd_Array1OfReal.hxx>
#include <TColStd_Array1OfInteger.hxx>

int make_shape_from_mesh(const std::vector<std::array<double, 3>>& vertices,
                         const std::vector<std::vector<int>>& faces) {
    BRepBuilderAPI_Sewing sewer(1e-6);
    int added = 0;

    for (const auto& face_indices : faces) {
        size_t n = face_indices.size();
        if (n < 3) continue;

        // Triangulate ngons using triangle fan
        size_t tri_count = n - 2;
        for (size_t t = 0; t < tri_count; ++t) {
            int i0 = face_indices[0];
            int i1 = face_indices[t + 1];
            int i2 = face_indices[t + 2];

            if (i0 < 0 || i0 >= static_cast<int>(vertices.size()) ||
                i1 < 0 || i1 >= static_cast<int>(vertices.size()) ||
                i2 < 0 || i2 >= static_cast<int>(vertices.size())) {
                continue;
            }

            gp_Pnt p0(vertices[i0][0], vertices[i0][1], vertices[i0][2]);
            gp_Pnt p1(vertices[i1][0], vertices[i1][1], vertices[i1][2]);
            gp_Pnt p2(vertices[i2][0], vertices[i2][1], vertices[i2][2]);

            BRepBuilderAPI_MakePolygon poly(p0, p1, p2, true);
            if (!poly.IsDone()) continue;

            BRepBuilderAPI_MakeFace face_builder(poly.Wire());
            if (!face_builder.IsDone()) continue;

            TopoDS_Face face = face_builder.Face();
            if (!face.IsNull()) {
                sewer.Add(face);
                ++added;
            }
        }
    }

    if (added == 0) {
        return -1;
    }

    sewer.Perform();
    TopoDS_Shape shape = sewer.SewedShape();
    if (shape.IsNull()) {
        return -1;
    }

    return register_shape(shape);
}

// ---------------------------------------------------------------------------
// Detect a regular quad grid in face topology and fit a B-spline surface.
// Returns the new shape_id on success, -1 if the mesh is not a clean grid.
// ---------------------------------------------------------------------------
static bool is_quad_face(const std::vector<int>& face) {
    return face.size() == 4;
}

int make_bspline_surface_from_grid(int rows, int cols,
                                    const std::vector<std::array<double, 3>>& grid_points) {
    if (rows < 2 || cols < 2 || static_cast<int>(grid_points.size()) != rows * cols) {
        return -1;
    }

    TColgp_Array2OfPnt points(1, rows, 1, cols);
    for (int i = 0; i < rows; ++i) {
        for (int j = 0; j < cols; ++j) {
            const auto& p = grid_points[i * cols + j];
            points.SetValue(i + 1, j + 1, gp_Pnt(p[0], p[1], p[2]));
        }
    }

    try {
        // Check for a planar grid first and, if so, build an exact plane-based
        // B-spline surface.
        BRepBuilderAPI_FindPlane plane_finder;
        {
            TopoDS_Vertex v1 = BRepBuilderAPI_MakeVertex(points.Value(1, 1)).Vertex();
            TopoDS_Vertex v2 = BRepBuilderAPI_MakeVertex(points.Value(1, cols)).Vertex();
            TopoDS_Vertex v3 = BRepBuilderAPI_MakeVertex(points.Value(rows, 1)).Vertex();
            BRepBuilderAPI_MakeWire wire_maker;
            wire_maker.Add(BRepBuilderAPI_MakeEdge(v1, v2).Edge());
            wire_maker.Add(BRepBuilderAPI_MakeEdge(v2, v3).Edge());
            wire_maker.Add(BRepBuilderAPI_MakeEdge(v3, v1).Edge());
            plane_finder.Init(wire_maker.Wire());
        }
        if (plane_finder.Found()) {
            Handle(Geom_Plane) plane = plane_finder.Plane();
            if (!plane.IsNull()) {
                // For a planar grid keep the full control-point grid so the
                // resulting B-spline matches the mesh resolution and is not
                // collapsed to a 2x2 corner approximation.
                TColgp_Array2OfPnt poles(1, rows, 1, cols);
                for (int i = 0; i < rows; ++i)
                    for (int j = 0; j < cols; ++j)
                        poles.SetValue(i + 1, j + 1, points.Value(i + 1, j + 1));

                // Build clamped knot vectors with uniform internal knots.
                TColStd_Array1OfReal uknots(1, rows);
                TColStd_Array1OfInteger umults(1, rows);
                for (int i = 0; i < rows; ++i) {
                    uknots.SetValue(i + 1, static_cast<double>(i) / (rows - 1));
                    umults.SetValue(i + 1, (i == 0 || i == rows - 1) ? 2 : 1);
                }
                TColStd_Array1OfReal vknots(1, cols);
                TColStd_Array1OfInteger vmults(1, cols);
                for (int j = 0; j < cols; ++j) {
                    vknots.SetValue(j + 1, static_cast<double>(j) / (cols - 1));
                    vmults.SetValue(j + 1, (j == 0 || j == cols - 1) ? 2 : 1);
                }

                Handle(Geom_BSplineSurface) bsurf = new Geom_BSplineSurface(
                    poles, uknots, vknots, umults, vmults, 1, 1,
                    false, false);

                BRepBuilderAPI_MakeFace face_builder(bsurf, 1e-7);
                if (face_builder.IsDone()) {
                    return register_shape(face_builder.Face());
                }
            }
        }

        // General non-planar case: approximate B-spline surface through grid points.
        GeomAPI_PointsToBSplineSurface fitter(points, 3, 3, GeomAbs_C2, 1e-7);
        if (fitter.IsDone()) {
            Handle(Geom_BSplineSurface) bsurf = fitter.Surface();
            if (!bsurf.IsNull()) {
                BRepBuilderAPI_MakeFace face_builder(bsurf, 1e-7);
                if (face_builder.IsDone()) {
                    return register_shape(face_builder.Face());
                }
            }
        }

        return -1;
    } catch (...) {
        return -1;
    }
}

// ---------------------------------------------------------------------------
// Build an exact B-spline/NURBS surface from full control data.
// Returns the new shape_id or -1 on failure.
// ---------------------------------------------------------------------------
int make_nurbs_surface(int degree_u, int degree_v,
                       const std::vector<double>& knots_u,
                       const std::vector<double>& knots_v,
                       const std::vector<int>& mults_u,
                       const std::vector<int>& mults_v,
                       const std::vector<std::array<double, 3>>& poles,
                       const std::vector<double>& weights,
                       bool periodic_u, bool periodic_v) {
    if (degree_u < 1 || degree_v < 1)
        return -1;

    const int n_u = static_cast<int>(mults_u.size());
    const int n_v = static_cast<int>(mults_v.size());
    if (n_u < 2 || n_v < 2)
        return -1;
    if (static_cast<int>(knots_u.size()) != n_u || static_cast<int>(knots_v.size()) != n_v)
        return -1;

    int expected_poles_u = 0, expected_poles_v = 0;
    for (int m : mults_u) expected_poles_u += m;
    for (int m : mults_v) expected_poles_v += m;
    expected_poles_u -= degree_u + 1;
    expected_poles_v -= degree_v + 1;
    if (expected_poles_u < 2 || expected_poles_v < 2)
        return -1;

    const int total_poles = expected_poles_u * expected_poles_v;
    if (static_cast<int>(poles.size()) != total_poles)
        return -1;

    const bool rational = !weights.empty();
    if (rational && static_cast<int>(weights.size()) != total_poles)
        return -1;

    try {
        NCollection_Array1<double> uknots(1, n_u);
        NCollection_Array1<double> vknots(1, n_v);
        NCollection_Array1<int> umults(1, n_u);
        NCollection_Array1<int> vmults(1, n_v);
        for (int i = 0; i < n_u; ++i) {
            uknots.SetValue(i + 1, knots_u[i]);
            umults.SetValue(i + 1, mults_u[i]);
        }
        for (int j = 0; j < n_v; ++j) {
            vknots.SetValue(j + 1, knots_v[j]);
            vmults.SetValue(j + 1, mults_v[j]);
        }

        NCollection_Array2<gp_Pnt> pole_array(1, expected_poles_u, 1, expected_poles_v);
        for (int i = 0; i < expected_poles_u; ++i) {
            for (int j = 0; j < expected_poles_v; ++j) {
                const auto& p = poles[i * expected_poles_v + j];
                pole_array.SetValue(i + 1, j + 1, gp_Pnt(p[0], p[1], p[2]));
            }
        }

        Handle(Geom_BSplineSurface) bsurf;
        if (rational) {
            NCollection_Array2<double> weight_array(1, expected_poles_u, 1, expected_poles_v);
            for (int i = 0; i < expected_poles_u; ++i) {
                for (int j = 0; j < expected_poles_v; ++j) {
                    weight_array.SetValue(i + 1, j + 1, weights[i * expected_poles_v + j]);
                }
            }
            bsurf = new Geom_BSplineSurface(pole_array, weight_array,
                uknots, vknots, umults, vmults,
                degree_u, degree_v, periodic_u, periodic_v);
        } else {
            bsurf = new Geom_BSplineSurface(pole_array,
                uknots, vknots, umults, vmults,
                degree_u, degree_v, periodic_u, periodic_v);
        }

        BRepBuilderAPI_MakeFace face_builder(bsurf, 1e-7);
        if (!face_builder.IsDone())
            return -1;
        return register_shape(face_builder.Face());
    } catch (...) {
        return -1;
    }
}
