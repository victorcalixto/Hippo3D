#include "occ_surface_ops.hpp"
#include "occ_registry.hpp"
#include "occ_mesh.hpp"

#include <BRepOffsetAPI_ThruSections.hxx>
#include <BRepPrimAPI_MakeRevol.hxx>
#include <BRepOffsetAPI_MakePipe.hxx>
#include <BRepOffsetAPI_MakePipeShell.hxx>
#include <BRepBuilderAPI_MakeFace.hxx>
#include <BRepBuilderAPI_MakeWire.hxx>
#include <BRepBuilderAPI_MakeEdge.hxx>
#include <BRepBuilderAPI_Sewing.hxx>
#include <BRepBuilderAPI_MakeSolid.hxx>
#include <BRepBuilderAPI_MakePolygon.hxx>
#include <BRepFill_Filling.hxx>
#include <BRepTools_WireExplorer.hxx>
#include <GeomFill_SectionGenerator.hxx>
#include <BRepAdaptor_CompCurve.hxx>
#include <GCPnts_UniformAbscissa.hxx>
#include <TopoDS_Wire.hxx>
#include <TopoDS_Face.hxx>
#include <TopoDS_Shape.hxx>
#include <TopoDS_Shell.hxx>
#include <TopoDS.hxx>
#include <TopoDS_Edge.hxx>
#include <TopExp_Explorer.hxx>
#include <BRep_Tool.hxx>
#include <gp_Ax1.hxx>
#include <gp_Pnt.hxx>
#include <gp_Vec.hxx>
#include <gp_Dir.hxx>
#include <Standard_Boolean.hxx>
// B-spline surface
#include <Geom_BSplineSurface.hxx>
#include <Geom_RectangularTrimmedSurface.hxx>
#include <TColgp_Array2OfPnt.hxx>
#include <TColStd_Array1OfReal.hxx>
#include <TColStd_Array1OfInteger.hxx>

// pybind11 types
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <cassert>
#include <cmath>

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
static TopoDS_Wire get_wire_from_shape(const TopoDS_Shape& shape) {
    TopoDS_Wire wire;
    if (shape.ShapeType() == TopAbs_WIRE) {
        wire = TopoDS::Wire(shape);
    } else if (shape.ShapeType() == TopAbs_EDGE) {
        BRepBuilderAPI_MakeWire wb(TopoDS::Edge(shape));
        wire = wb.Wire();
    } else if (shape.ShapeType() == TopAbs_FACE) {
        TopoDS_Face face = TopoDS::Face(shape);
        for (TopExp_Explorer exp(face, TopAbs_WIRE); exp.More(); exp.Next()) {
            wire = TopoDS::Wire(exp.Current());
            break;
        }
    }
    return wire;
}

// ---------------------------------------------------------------------------
// occ_loft -- BRepOffsetAPI_ThruSections
// ---------------------------------------------------------------------------
int occ_loft(const std::vector<int>& wire_shape_ids, bool closed, bool solid) {
    if (wire_shape_ids.size() < 2) {
        throw std::invalid_argument("occ_loft: need at least 2 section wires");
    }
    BRepOffsetAPI_ThruSections loft_builder(solid, Standard_False, 1e-6);
    for (int sid : wire_shape_ids) {
        if (!has_shape(sid))
            throw std::invalid_argument("occ_loft: shape id not found in registry");
        TopoDS_Shape shape = get_shape(sid);
        TopoDS_Wire wire = get_wire_from_shape(shape);
        if (wire.IsNull())
            throw std::invalid_argument("occ_loft: wire is null");
        loft_builder.AddWire(wire);
    }
    loft_builder.Build();
    if (!loft_builder.IsDone())
        throw std::runtime_error("occ_loft: ThruSections build failed");
    return register_shape(loft_builder.Shape());
}

// ---------------------------------------------------------------------------
// occ_revolve -- BRepPrimAPI_MakeRevol
// ---------------------------------------------------------------------------
int occ_revolve(int profile_shape_id, const std::array<double,3>& axis_origin,
                const std::array<double,3>& axis_dir, double angle_deg) {
    if (!has_shape(profile_shape_id))
        throw std::invalid_argument("occ_revolve: profile shape id not found");
    TopoDS_Shape profile = get_shape(profile_shape_id);
    gp_Pnt origin(axis_origin[0], axis_origin[1], axis_origin[2]);
    gp_Dir dir(axis_dir[0], axis_dir[1], axis_dir[2]);
    gp_Ax1 axis(origin, dir);
    double angle_rad = angle_deg * M_PI / 180.0;
    BRepPrimAPI_MakeRevol revol(profile, axis, angle_rad);
    if (!revol.IsDone())
        throw std::runtime_error("occ_revolve: BRepPrimAPI_MakeRevol failed");
    return register_shape(revol.Shape());
}

// ---------------------------------------------------------------------------
// occ_sweep1 -- BRepOffsetAPI_MakePipe
// ---------------------------------------------------------------------------
int occ_sweep1(int rail_shape_id, int profile_shape_id, bool solid) {
    (void)solid;  // BRepOffsetAPI_MakePipe does not accept a solid flag directly
    if (!has_shape(rail_shape_id))
        throw std::invalid_argument("occ_sweep1: rail shape id not found");
    if (!has_shape(profile_shape_id))
        throw std::invalid_argument("occ_sweep1: profile shape id not found");
    TopoDS_Shape rail = get_shape(rail_shape_id);
    TopoDS_Shape profile = get_shape(profile_shape_id);
    TopoDS_Wire rail_wire;
    if (rail.ShapeType() == TopAbs_WIRE)
        rail_wire = TopoDS::Wire(rail);
    else if (rail.ShapeType() == TopAbs_EDGE) {
        BRepBuilderAPI_MakeWire wb(TopoDS::Edge(rail));
        rail_wire = wb.Wire();
    } else
        throw std::invalid_argument("occ_sweep1: rail must be a wire or edge");
    BRepOffsetAPI_MakePipe pipe(rail_wire, profile);
    if (!pipe.IsDone())
        throw std::runtime_error("occ_sweep1: BRepOffsetAPI_MakePipe failed");
    return register_shape(pipe.Shape());
}

// ---------------------------------------------------------------------------
// occ_sweep2 -- BRepOffsetAPI_MakePipeShell
// ---------------------------------------------------------------------------
int occ_sweep2(const std::vector<int>& rail_shape_ids,
               const std::vector<int>& profile_shape_ids, bool solid) {
    (void)solid;
    if (rail_shape_ids.empty())
        throw std::invalid_argument("occ_sweep2: need at least one rail");
    if (profile_shape_ids.empty())
        throw std::invalid_argument("occ_sweep2: need at least one profile");
    TopoDS_Shape spine = get_shape(rail_shape_ids[0]);
    TopoDS_Wire spine_wire;
    if (spine.ShapeType() == TopAbs_WIRE)
        spine_wire = TopoDS::Wire(spine);
    else if (spine.ShapeType() == TopAbs_EDGE) {
        BRepBuilderAPI_MakeWire wb(TopoDS::Edge(spine));
        spine_wire = wb.Wire();
    } else
        throw std::invalid_argument("occ_sweep2: spine must be a wire or edge");
    BRepOffsetAPI_MakePipeShell pipeShell(spine_wire);
    for (int sid : profile_shape_ids) {
        if (!has_shape(sid))
            throw std::invalid_argument("occ_sweep2: profile shape id not found");
        pipeShell.Add(get_shape(sid));
    }
    pipeShell.Build();
    if (!pipeShell.IsDone())
        throw std::runtime_error("occ_sweep2: BRepOffsetAPI_MakePipeShell failed");
    return register_shape(pipeShell.Shape());
}

// ---------------------------------------------------------------------------
// occ_planar_srf -- BRepBuilderAPI_MakeFace from a single closed wire
// ---------------------------------------------------------------------------
int occ_planar_srf(int wire_shape_id) {
    if (!has_shape(wire_shape_id))
        throw std::invalid_argument("occ_planar_srf: wire shape id not found");
    TopoDS_Shape shape = get_shape(wire_shape_id);
    TopoDS_Wire wire = get_wire_from_shape(shape);
    if (wire.IsNull())
        throw std::invalid_argument("occ_planar_srf: wire is null");
    BRepBuilderAPI_MakeFace face_builder(wire, Standard_True);
    if (!face_builder.IsDone())
        throw std::runtime_error("occ_planar_srf: face construction failed");
    return register_shape(face_builder.Face());
}

// ---------------------------------------------------------------------------
// occ_edge_srf -- BRepFill_Filling from multiple boundary edges/wires
// ---------------------------------------------------------------------------
int occ_edge_srf(const std::vector<int>& wire_shape_ids, int continuity) {
    if (wire_shape_ids.empty())
        throw std::invalid_argument("occ_edge_srf: need at least one boundary wire/edge");
    BRepFill_Filling filler;
    GeomAbs_Shape cont = GeomAbs_C0;
    if (continuity == 1) cont = GeomAbs_C1;
    else if (continuity >= 2) cont = GeomAbs_C2;
    for (int sid : wire_shape_ids) {
        if (!has_shape(sid))
            throw std::invalid_argument("occ_edge_srf: shape id not found");
        TopoDS_Shape shape = get_shape(sid);
        if (shape.ShapeType() == TopAbs_EDGE)
            filler.Add(TopoDS::Edge(shape), cont);
        else if (shape.ShapeType() == TopAbs_WIRE) {
            for (TopExp_Explorer exp(shape, TopAbs_EDGE); exp.More(); exp.Next())
                filler.Add(TopoDS::Edge(exp.Current()), cont);
        } else
            throw std::invalid_argument("occ_edge_srf: shape must be an edge or wire");
    }
    filler.Build();
    if (!filler.IsDone())
        throw std::runtime_error("occ_edge_srf: BRepFill_Filling failed");
    return register_shape(filler.Face());
}

// ---------------------------------------------------------------------------
// Level 2: Direct B-spline surface control-point extraction / setting
// ---------------------------------------------------------------------------

static Handle(Geom_BSplineSurface) get_first_bspline_surface(int shape_id) {
    if (!has_shape(shape_id))
        throw std::invalid_argument("Shape not found");
    TopoDS_Shape shape = get_shape(shape_id);
    for (TopExp_Explorer exp(shape, TopAbs_FACE); exp.More(); exp.Next()) {
        TopoDS_Face face = TopoDS::Face(exp.Current());
        TopLoc_Location loc;
        Handle(Geom_Surface) surf = BRep_Tool::Surface(face, loc);
        if (surf.IsNull()) continue;
        if (surf->IsKind(STANDARD_TYPE(Geom_BSplineSurface))) {
            return Handle(Geom_BSplineSurface)::DownCast(surf);
        }
        if (surf->IsKind(STANDARD_TYPE(Geom_RectangularTrimmedSurface))) {
            Handle(Geom_RectangularTrimmedSurface) tr =
                Handle(Geom_RectangularTrimmedSurface)::DownCast(surf);
            if (!tr.IsNull() && tr->BasisSurface()->IsKind(STANDARD_TYPE(Geom_BSplineSurface)))
                return Handle(Geom_BSplineSurface)::DownCast(tr->BasisSurface());
        }
    }
    return Handle(Geom_BSplineSurface)();
}

pybind11::dict extract_bsurf_control_points(int shape_id) {
    Handle(Geom_BSplineSurface) bsurf = get_first_bspline_surface(shape_id);
    if (bsurf.IsNull())
        throw std::runtime_error("No B-spline surface found");

    int u_count = bsurf->NbUPoles();
    int v_count = bsurf->NbVPoles();
    int u_deg   = bsurf->UDegree();
    int v_deg   = bsurf->VDegree();

    pybind11::list poles;
    const TColgp_Array2OfPnt& arr = bsurf->Poles();
    for (int i = arr.LowerRow(); i <= arr.UpperRow(); ++i) {
        for (int j = arr.LowerCol(); j <= arr.UpperCol(); ++j) {
            gp_Pnt p = arr(i, j);
            poles.append(pybind11::make_tuple(p.X(), p.Y(), p.Z()));
        }
    }

    pybind11::list uknots, vknots;
    const TColStd_Array1OfReal& uka = bsurf->UKnots();
    for (int i = uka.Lower(); i <= uka.Upper(); ++i) uknots.append(uka(i));
    const TColStd_Array1OfReal& vka = bsurf->VKnots();
    for (int i = vka.Lower(); i <= vka.Upper(); ++i) vknots.append(vka(i));

    pybind11::list umults, vmults;
    const TColStd_Array1OfInteger& uma = bsurf->UMultiplicities();
    for (int i = uma.Lower(); i <= uma.Upper(); ++i) umults.append(uma(i));
    const TColStd_Array1OfInteger& vma = bsurf->VMultiplicities();
    for (int i = vma.Lower(); i <= vma.Upper(); ++i) vmults.append(vma(i));

    pybind11::dict result;
    result["u_deg"]      = u_deg;
    result["v_deg"]      = v_deg;
    result["u_count"]    = u_count;
    result["v_count"]    = v_count;
    result["poles"]      = poles;
    result["uknots"]     = uknots;
    result["vknots"]     = vknots;
    result["umults"]     = umults;
    result["vmults"]     = vmults;
    result["u_periodic"] = bsurf->IsUPeriodic();
    result["v_periodic"] = bsurf->IsVPeriodic();
    result["rational"]   = bsurf->IsURational() || bsurf->IsVRational();
    return result;
}

int set_bsurf_control_points(int old_shape_id,
                             const std::vector<std::array<double,3>>& poles) {
    Handle(Geom_BSplineSurface) bsurf = get_first_bspline_surface(old_shape_id);
    if (bsurf.IsNull())
        throw std::runtime_error("No B-spline surface found");

    int u_count = bsurf->NbUPoles();
    int v_count = bsurf->NbVPoles();
    size_t expected = static_cast<size_t>(u_count * v_count);
    if (poles.size() != expected)
        throw std::runtime_error(
            "Pole count mismatch: got " + std::to_string(poles.size()) +
            ", expected " + std::to_string(expected));

    // Re-use original knots / mults / degree / periodicity
    TColStd_Array1OfReal uknots(bsurf->UKnots());
    TColStd_Array1OfReal vknots(bsurf->VKnots());
    TColStd_Array1OfInteger umults(bsurf->UMultiplicities());
    TColStd_Array1OfInteger vmults(bsurf->VMultiplicities());

    Handle(Geom_BSplineSurface) new_surf;
    if (bsurf->IsURational() || bsurf->IsVRational()) {
        // Rational: keep original weights, replace only poles
        TColgp_Array2OfPnt new_poles(1, u_count, 1, v_count);
        TColStd_Array2OfReal new_weights(1, u_count, 1, v_count);
        const TColgp_Array2OfPnt& old_poles = bsurf->Poles();
        const TColStd_Array2OfReal* old_weights_ptr = bsurf->Weights();
        size_t idx = 0;
        for (int i = 1; i <= u_count; ++i) {
            for (int j = 1; j <= v_count; ++j) {
                new_poles(i, j) = gp_Pnt(poles[idx][0], poles[idx][1], poles[idx][2]);
                new_weights(i, j) = old_weights_ptr ? old_weights_ptr->Value(i, j) : 1.0;
                ++idx;
            }
        }
        new_surf = new Geom_BSplineSurface(new_poles, new_weights,
            uknots, vknots, umults, vmults,
            bsurf->UDegree(), bsurf->VDegree(),
            bsurf->IsUPeriodic(), bsurf->IsVPeriodic());
    } else {
        // Non-rational
        TColgp_Array2OfPnt new_poles(1, u_count, 1, v_count);
        size_t idx = 0;
        for (int i = 1; i <= u_count; ++i) {
            for (int j = 1; j <= v_count; ++j) {
                new_poles(i, j) = gp_Pnt(poles[idx][0], poles[idx][1], poles[idx][2]);
                ++idx;
            }
        }
        new_surf = new Geom_BSplineSurface(new_poles, uknots, vknots, umults, vmults,
            bsurf->UDegree(), bsurf->VDegree(),
            bsurf->IsUPeriodic(), bsurf->IsVPeriodic());
    }

    // Build a face from the complete surface (no trimming needed; user edits the surface)
    BRepBuilderAPI_MakeFace face_builder(new_surf, 1e-7);
    if (!face_builder.IsDone())
        throw std::runtime_error("set_bsurf_control_points: face builder failed");
    return register_shape(face_builder.Face());
}
