#include "occ_curves.hpp"
#include "occ_registry.hpp"
#include "occ_mesh.hpp"

#include <Geom_BSplineCurve.hxx>
#include <Geom_Circle.hxx>
#include <Geom_TrimmedCurve.hxx>
#include <gp_Ax2.hxx>
#include <gp_Circ.hxx>
#include <gp_Pnt.hxx>
#include <gp_Dir.hxx>
#include <BRepBuilderAPI_MakeEdge.hxx>
#include <BRepBuilderAPI_MakeWire.hxx>
#include <BRepBuilderAPI_MakePolygon.hxx>
#include <TopoDS_Wire.hxx>
#include <TopoDS_Edge.hxx>
#include <TopoDS_Shape.hxx>
#include <TopoDS.hxx>
#include <TopExp_Explorer.hxx>
#include <BRepAdaptor_Curve.hxx>
#include <GCPnts_UniformAbscissa.hxx>
#include <NCollection_Array1.hxx>

#include <stdexcept>
#include <cmath>

namespace py = pybind11;

// ---------------------------------------------------------------------------
// make_polyline_wire
// ---------------------------------------------------------------------------
int make_polyline_wire(
    const std::vector<std::array<double, 3>>& points,
    bool closed
) {
    if (points.size() < 2) {
        throw std::invalid_argument("make_polyline_wire: need at least 2 points");
    }

    // Use BRepBuilderAPI_MakePolygon to build a proper polygonal wire.
    // It handles vertex sharing automatically and skips zero-length edges.
    BRepBuilderAPI_MakePolygon poly_maker;
    for (const auto& p : points) {
        gp_Pnt gp(p[0], p[1], p[2]);
        poly_maker.Add(gp);
    }

    if (closed) {
        poly_maker.Close();
    }

    if (!poly_maker.IsDone()) {
        throw std::runtime_error("make_polyline_wire: MakePolygon failed");
    }

    TopoDS_Wire wire = poly_maker.Wire();
    int shape_id = register_shape(wire);
    return shape_id;
}

// ---------------------------------------------------------------------------
// make_nurbs_curve
// ---------------------------------------------------------------------------
int make_nurbs_curve(
    const std::vector<std::array<double, 4>>& cvs,
    const std::vector<double>& knots,
    const std::vector<int>& mults,
    int degree,
    bool periodic
) {
    if (cvs.empty()) {
        throw std::invalid_argument("make_nurbs_curve: no control points");
    }
    if (degree < 1) {
        throw std::invalid_argument("make_nurbs_curve: degree must be >= 1");
    }
    if (knots.empty()) {
        throw std::invalid_argument("make_nurbs_curve: no knots");
    }

    int nb_poles = static_cast<int>(cvs.size());
    int nb_knots = static_cast<int>(knots.size());

    NCollection_Array1<gp_Pnt> poles(1, nb_poles);
    for (int i = 0; i < nb_poles; ++i) {
        poles(i + 1) = gp_Pnt(cvs[i][0], cvs[i][1], cvs[i][2]);
    }

    NCollection_Array1<double> occ_knots(1, nb_knots);
    for (int i = 0; i < nb_knots; ++i) {
        occ_knots(i + 1) = knots[i];
    }

    NCollection_Array1<int> occ_mults(1, nb_knots);
    for (int i = 0; i < nb_knots; ++i) {
        int m = (i < static_cast<int>(mults.size())) ? mults[i] : 1;
        occ_mults(i + 1) = m;
    }

    bool is_rational = false;
    for (const auto& cv : cvs) {
        if (std::abs(cv[3] - 1.0) > 1e-12) {
            is_rational = true;
            break;
        }
    }

    Handle(Geom_BSplineCurve) curve;
    if (is_rational) {
        NCollection_Array1<double> weights(1, nb_poles);
        for (int i = 0; i < nb_poles; ++i) {
            weights(i + 1) = cvs[i][3];
        }
        curve = new Geom_BSplineCurve(poles, weights, occ_knots, occ_mults, degree, periodic);
    } else {
        curve = new Geom_BSplineCurve(poles, occ_knots, occ_mults, degree, periodic);
    }

    TopoDS_Edge edge = BRepBuilderAPI_MakeEdge(curve).Edge();
    TopoDS_Wire wire = BRepBuilderAPI_MakeWire(edge).Wire();

    int shape_id = register_shape(wire);
    return shape_id;
}

// ---------------------------------------------------------------------------
// make_circle_wire / make_full_circle
// ---------------------------------------------------------------------------
int make_circle_wire(
    const std::array<double, 3>& center,
    double radius,
    const std::array<double, 3>& normal,
    double start_angle,
    double end_angle
) {
    if (radius <= 0) {
        throw std::invalid_argument("make_circle_wire: radius must be > 0");
    }

    gp_Pnt cen(center[0], center[1], center[2]);
    gp_Dir norm(normal[0], normal[1], normal[2]);
    gp_Ax2 ax2(cen, norm);
    gp_Circ circ(ax2, radius);

    Handle(Geom_Circle) circle = new Geom_Circle(circ);
    Handle(Geom_TrimmedCurve) trimmed = new Geom_TrimmedCurve(
        circle, start_angle, end_angle, true
    );

    TopoDS_Edge edge = BRepBuilderAPI_MakeEdge(trimmed).Edge();
    TopoDS_Wire wire = BRepBuilderAPI_MakeWire(edge).Wire();

    int shape_id = register_shape(wire);
    return shape_id;
}

int make_full_circle(
    const std::array<double, 3>& center,
    double radius,
    const std::array<double, 3>& normal
) {
    return make_circle_wire(center, radius, normal, 0.0, 2.0 * M_PI);
}

// ---------------------------------------------------------------------------
// remesh_curve — sample into polylines for display
// ---------------------------------------------------------------------------
py::dict remesh_curve(int shape_id, double deflection) {
    if (!has_shape(shape_id)) {
        throw std::runtime_error("OCC shape id not found");
    }

    TopoDS_Shape shape = get_shape(shape_id);
    py::list edges_list;

    for (TopExp_Explorer exp(shape, TopAbs_EDGE); exp.More(); exp.Next()) {
        TopoDS_Edge edge = TopoDS::Edge(exp.Current());
        BRepAdaptor_Curve adapt(edge);

        double u0 = adapt.FirstParameter();
        double u1 = adapt.LastParameter();

        GCPnts_UniformAbscissa sampler(adapt, deflection, u0, u1);
        int nb_points = sampler.NbPoints();
        if (nb_points < 2) nb_points = 2;

        py::list polyline;
        for (int i = 1; i <= nb_points; ++i) {
            double u = sampler.Parameter(i);
            gp_Pnt p = adapt.Value(u);
            polyline.append(py::make_tuple(p.X(), p.Y(), p.Z()));
        }
        edges_list.append(polyline);
    }

    py::dict result;
    result["edges"] = edges_list;
    result["shape_id"] = shape_id;
    return result;
}
