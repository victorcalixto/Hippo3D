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

#include <Standard_Integer.hxx>
#include <Standard_Real.hxx>

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
        const Standard_Integer node_count = triangulation->NbNodes();

        for (Standard_Integer i = 1; i <= node_count; ++i) {
            gp_Pnt p = triangulation->Node(i).Transformed(transform);
            result.vertices.push_back({p.X(), p.Y(), p.Z()});
        }

        const Standard_Integer triangle_count = triangulation->NbTriangles();

        for (Standard_Integer i = 1; i <= triangle_count; ++i) {
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

        Standard_Real first = 0.0;
        Standard_Real last = 0.0;
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
