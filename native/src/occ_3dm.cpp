#include "occ_3dm.hpp"
#include "occ_registry.hpp"
#include "occ_mesh.hpp"

#include <opennurbs.h>
#include <BRepMesh_IncrementalMesh.hxx>
#include <BRep_Tool.hxx>
#include <TopExp_Explorer.hxx>
#include <TopoDS.hxx>
#include <TopoDS_Face.hxx>
#include <TopoDS_Edge.hxx>
#include <TopoDS_Compound.hxx>
#include <TopoDS_Shell.hxx>
#include <TopoDS_Wire.hxx>
#include <BRepBuilderAPI_MakeFace.hxx>
#include <BRepBuilderAPI_MakeEdge.hxx>
#include <BRepBuilderAPI_MakeWire.hxx>
#include <BRepBuilderAPI_MakeVertex.hxx>
#include <BRep_Builder.hxx>
#include <BRepBuilderAPI_NurbsConvert.hxx>
#include <BRepBuilderAPI_Sewing.hxx>
#include <BRepTools.hxx>
#include <Poly_Triangulation.hxx>
#include <Poly_Triangle.hxx>
#include <TopLoc_Location.hxx>
#include <gp_Pnt.hxx>
#include <Geom_BSplineSurface.hxx>
#include <Geom_BSplineCurve.hxx>
#include <Geom_Surface.hxx>
#include <Geom_Curve.hxx>
#include <Geom_RectangularTrimmedSurface.hxx>
#include <Geom_SphericalSurface.hxx>
#include <Geom_ToroidalSurface.hxx>
#include <GeomConvert.hxx>
#include <NCollection_Array1.hxx>
#include <NCollection_Array2.hxx>

#include <vector>
#include <array>
#include <memory>
#include <algorithm>

// ---------------------------------------------------------------------------
// Helpers: convert OCC Geom_BSplineSurface <-> ON_NurbsSurface
// ---------------------------------------------------------------------------

// Try to detect an underlying analytic surface (sphere, torus) and create
// the corresponding ON_NurbsSurface using ON's native constructors, which
// produce valid non-periodic NURBS forms.
static std::unique_ptr<ON_NurbsSurface> try_analytic_surface_to_on(
    const Handle(Geom_Surface)& surf,
    const TopLoc_Location& loc
) {
    if (surf.IsNull()) return nullptr;

    // Get the untrimmed basis surface and apply location transform if any
    Handle(Geom_Surface) basis = surf;
    gp_Trsf trsf;
    if (!loc.IsIdentity()) {
        trsf = loc.Transformation();
    }

    Handle(Geom_RectangularTrimmedSurface) trimmed =
        Handle(Geom_RectangularTrimmedSurface)::DownCast(surf);
    if (!trimmed.IsNull()) {
        Handle(Geom_Surface) untrimmed = trimmed->BasisSurface();
        if (!untrimmed.IsNull()) {
            basis = untrimmed;
        }
    }

    // Sphere
    Handle(Geom_SphericalSurface) sph = Handle(Geom_SphericalSurface)::DownCast(basis);
    if (!sph.IsNull()) {
        gp_Ax3 ax = sph->Position();
        if (!trsf.Form() == gp_Identity) {
            ax.Transform(trsf);
        }
        ON_Sphere on_sphere(
            ON_3dPoint(ax.Location().X(), ax.Location().Y(), ax.Location().Z()),
            sph->Radius()
        );
        ON_NurbsSurface* ns = ON_NurbsSurface::New();
        if (on_sphere.GetNurbForm(*ns)) {
            return std::unique_ptr<ON_NurbsSurface>(ns);
        }
        delete ns;
    }

    // Torus
    Handle(Geom_ToroidalSurface) tor = Handle(Geom_ToroidalSurface)::DownCast(basis);
    if (!tor.IsNull()) {
        gp_Ax3 ax = tor->Position();
        if (!trsf.Form() == gp_Identity) {
            ax.Transform(trsf);
        }
        ON_Plane plane(
            ON_3dPoint(ax.Location().X(), ax.Location().Y(), ax.Location().Z()),
            ON_3dVector(ax.Direction().X(), ax.Direction().Y(), ax.Direction().Z())
        );
        ON_Torus on_torus(plane, tor->MajorRadius(), tor->MinorRadius());
        ON_NurbsSurface* ns = ON_NurbsSurface::New();
        if (on_torus.GetNurbForm(*ns)) {
            return std::unique_ptr<ON_NurbsSurface>(ns);
        }
        delete ns;
    }

    return nullptr;
}

static std::unique_ptr<ON_NurbsSurface> occ_surface_to_on_nurbs(const Handle(Geom_BSplineSurface)& occ_surf) {
    if (occ_surf.IsNull()) {
        return nullptr;
    }

    int u_deg = occ_surf->UDegree();
    int v_deg = occ_surf->VDegree();
    int u_poles = occ_surf->NbUPoles();
    int v_poles = occ_surf->NbVPoles();
    int u_knots = occ_surf->NbUKnots();
    int v_knots = occ_surf->NbVKnots();
    bool is_rational = occ_surf->IsURational() || occ_surf->IsVRational();
    bool u_periodic = occ_surf->IsUPeriodic();
    bool v_periodic = occ_surf->IsVPeriodic();

    // For periodic directions, ON expects the first (order-1) poles to be
    // duplicated at the end, so cv_count grows by (order-1).  The flat
    // knot vector then uses the full OCC array without dropping anything.
    int on_u_poles = u_poles + (u_periodic ? u_deg : 0);
    int on_v_poles = v_poles + (v_periodic ? v_deg : 0);

    auto on_surf = std::unique_ptr<ON_NurbsSurface>(
        ON_NurbsSurface::New(3, is_rational, u_deg + 1, v_deg + 1, on_u_poles, on_v_poles));
    if (!on_surf) {
        return nullptr;
    }

    // Copy poles (and weights if rational)
    for (int i = 0; i < on_u_poles; ++i) {
        for (int j = 0; j < on_v_poles; ++j) {
            // For periodic, wrap pole indices around.
            int src_i = (i >= u_poles) ? (i - u_poles) : i;
            int src_j = (j >= v_poles) ? (j - v_poles) : j;
            gp_Pnt p = occ_surf->Pole(src_i + 1, src_j + 1);
            if (is_rational) {
                double w = occ_surf->Weight(src_i + 1, src_j + 1);
                ON_4dPoint cp(p.X(), p.Y(), p.Z(), w);
                on_surf->SetCV(i, j, cp);
            } else {
                ON_3dPoint cp(p.X(), p.Y(), p.Z());
                on_surf->SetCV(i, j, cp);
            }
        }
    }

    // Copy knots.
    // Non-periodic: drop one copy from each end (OCC uses degree+1, ON uses degree).
    // Periodic: copy the full flat array without dropping.
    const NCollection_Array1<double>& u_knots_arr = occ_surf->UKnots();
    const NCollection_Array1<int>& u_mults_arr = occ_surf->UMultiplicities();

    int on_knot_idx = 0;
    for (int i = 1; i <= u_knots; ++i) {
        double k = u_knots_arr(i);
        int m = u_mults_arr(i);
        if (!u_periodic && (i == 1 || i == u_knots)) {
            m = std::max(0, m - 1); // drop one copy from each non-periodic end
        }
        for (int r = 0; r < m; ++r) {
            on_surf->SetKnot(0, on_knot_idx++, k);
        }
    }

    const NCollection_Array1<double>& v_knots_arr = occ_surf->VKnots();
    const NCollection_Array1<int>& v_mults_arr = occ_surf->VMultiplicities();

    on_knot_idx = 0;
    for (int i = 1; i <= v_knots; ++i) {
        double k = v_knots_arr(i);
        int m = v_mults_arr(i);
        if (!v_periodic && (i == 1 || i == v_knots)) {
            m = std::max(0, m - 1); // drop one copy from each non-periodic end
        }
        for (int r = 0; r < m; ++r) {
            on_surf->SetKnot(1, on_knot_idx++, k);
        }
    }

    return on_surf;
}

static Handle(Geom_BSplineSurface) on_nurbs_to_occ_surface(const ON_NurbsSurface* on_surf) {
    if (!on_surf || !on_surf->IsValid()) {
        return nullptr;
    }

    int u_deg = on_surf->Degree(0);
    int v_deg = on_surf->Degree(1);
    int u_poles = on_surf->CVCount(0);
    int v_poles = on_surf->CVCount(1);
    bool is_rational = on_surf->IsRational();

    NCollection_Array2<gp_Pnt> poles(1, u_poles, 1, v_poles);

    // Copy poles
    for (int i = 1; i <= u_poles; ++i) {
        for (int j = 1; j <= v_poles; ++j) {
            if (is_rational) {
                ON_4dPoint cp;
                on_surf->GetCV(i - 1, j - 1, cp);
                poles(i, j) = gp_Pnt(cp.x / cp.w, cp.y / cp.w, cp.z / cp.w);
            } else {
                ON_3dPoint cp;
                on_surf->GetCV(i - 1, j - 1, cp);
                poles(i, j) = gp_Pnt(cp.x, cp.y, cp.z);
            }
        }
    }

    // Copy knots with multiplicities.
    // OpenNURBS stores a flat array where identical consecutive values
    // represent multiplicities in place.  We must collapse them into
    // unique values with summed multiplicities before creating OCC arrays.
    // OpenNURBS stores a flat array where identical consecutive values
    // represent multiplicities in place.  Each change in value is a unique knot;
    // ON::KnotMultiplicity already returns the whole-count for that value.
    std::vector<double> u_unique;
    std::vector<int>    u_unique_mults;
    for (int i = 0; i < on_surf->KnotCount(0); ++i) {
        double k = on_surf->Knot(0, i);
        if (!u_unique.empty() && std::abs(u_unique.back() - k) < 1e-12) {
            continue; // skip duplicate entries of the same value
        }
        int m = on_surf->KnotMultiplicity(0, i);
        u_unique.push_back(k);
        u_unique_mults.push_back(m);
    }
    bool u_periodic = on_surf->IsPeriodic(0);
    if (!u_periodic) {
        if (!u_unique_mults.empty()) u_unique_mults.front() += 1;
        if (u_unique_mults.size() > 1) u_unique_mults.back() += 1;
    }
    NCollection_Array1<double>    u_knots(1, static_cast<int>(u_unique.size()));
    NCollection_Array1<int> u_mults(1, static_cast<int>(u_unique.size()));
    for (size_t i = 0; i < u_unique.size(); ++i) {
        u_knots(static_cast<int>(i) + 1) = u_unique[i];
        u_mults(static_cast<int>(i) + 1) = u_unique_mults[i];
    }

    std::vector<double> v_unique;
    std::vector<int>    v_unique_mults;
    for (int i = 0; i < on_surf->KnotCount(1); ++i) {
        double k = on_surf->Knot(1, i);
        if (!v_unique.empty() && std::abs(v_unique.back() - k) < 1e-12) {
            continue;
        }
        int m = on_surf->KnotMultiplicity(1, i);
        v_unique.push_back(k);
        v_unique_mults.push_back(m);
    }
    bool v_periodic = on_surf->IsPeriodic(1);
    if (!v_periodic) {
        if (!v_unique_mults.empty()) v_unique_mults.front() += 1;
        if (v_unique_mults.size() > 1) v_unique_mults.back() += 1;
    }
    NCollection_Array1<double>    v_knots(1, static_cast<int>(v_unique.size()));
    NCollection_Array1<int> v_mults(1, static_cast<int>(v_unique.size()));
    for (size_t i = 0; i < v_unique.size(); ++i) {
        v_knots(static_cast<int>(i) + 1) = v_unique[i];
        v_mults(static_cast<int>(i) + 1) = v_unique_mults[i];
    }
    // For periodic surfaces, the ON flat array may include wrap-around knots
    // that duplicate the first/last values.  Remove them so OCC gets a proper
    // periodic definition (first/last pole duplication, no phantom knots).
    // However, since we already clamped periodic surfaces during export,
    // the imported surface should already be non-periodic.  The periodic flag
    // in the constructor is kept for round-trip information only.

    if (is_rational) {
        NCollection_Array2<double> weights(1, u_poles, 1, v_poles);
        for (int i = 1; i <= u_poles; ++i) {
            for (int j = 1; j <= v_poles; ++j) {
                weights(i, j) = on_surf->Weight(i - 1, j - 1);
            }
        }
        return new Geom_BSplineSurface(poles, weights, u_knots, v_knots, u_mults, v_mults, u_deg, v_deg, u_periodic, v_periodic);
    } else {
        return new Geom_BSplineSurface(poles, u_knots, v_knots, u_mults, v_mults, u_deg, v_deg, u_periodic, v_periodic);
    }
}

// ---------------------------------------------------------------------------
// Export: TopoDS_Shape -> ON_Brep / ON_Mesh -> 3DM
// ---------------------------------------------------------------------------

static std::tuple<bool, std::string> export_3dm_mesh(int shape_id, const std::string& filepath) {
    if (!has_shape(shape_id)) {
        return {false, "Shape ID not found in registry"};
    }

    TopoDS_Shape shape = get_shape(shape_id);
    BRepMesh_IncrementalMesh mesher(shape, 0.1);

    std::vector<ON_3dPoint> vertices;
    std::vector<std::array<int, 3>> triangles;

    for (TopExp_Explorer exp(shape, TopAbs_FACE); exp.More(); exp.Next()) {
        TopoDS_Face face = TopoDS::Face(exp.Current());
        TopLoc_Location location;

        Handle(Poly_Triangulation) triangulation = BRep_Tool::Triangulation(face, location);
        if (triangulation.IsNull()) {
            continue;
        }

        const gp_Trsf transform = location.Transformation();
        const int node_count = triangulation->NbNodes();
        const int triangle_count = triangulation->NbTriangles();

        int vertex_offset = static_cast<int>(vertices.size());

        for (int i = 1; i <= node_count; ++i) {
            gp_Pnt p = triangulation->Node(i).Transformed(transform);
            vertices.emplace_back(p.X(), p.Y(), p.Z());
        }

        for (int i = 1; i <= triangle_count; ++i) {
            Poly_Triangle triangle = triangulation->Triangle(i);
            int n1, n2, n3;
            triangle.Get(n1, n2, n3);
            if (face.Orientation() == TopAbs_REVERSED) {
                triangles.push_back({vertex_offset + n1 - 1, vertex_offset + n3 - 1, vertex_offset + n2 - 1});
            } else {
                triangles.push_back({vertex_offset + n1 - 1, vertex_offset + n2 - 1, vertex_offset + n3 - 1});
            }
        }
    }

    if (vertices.empty()) {
        return {false, "No mesh data found for export"};
    }

    ON_Mesh* on_mesh = new ON_Mesh(
        static_cast<int>(triangles.size()),
        static_cast<int>(vertices.size()),
        false,
        false
    );

    for (size_t i = 0; i < vertices.size(); ++i) {
        on_mesh->SetVertex(static_cast<int>(i), vertices[i]);
    }

    for (size_t i = 0; i < triangles.size(); ++i) {
        on_mesh->SetTriangle(static_cast<int>(i), triangles[i][0], triangles[i][1], triangles[i][2]);
    }

    ONX_Model model;
    ON_3dmObjectAttributes* attributes = new ON_3dmObjectAttributes();
    attributes->m_name = "OCC_Mesh_Export";
    ON_CreateUuid(attributes->m_uuid);
    model.AddModelGeometryComponent(on_mesh, attributes);

    bool ok = model.Write(filepath.c_str(), 0);
    if (!ok) {
        return {false, "3DM mesh write failed"};
    }
    return {true, "Exported 3DM mesh: " + filepath};
}

// Helper: convert a TopoDS_Shape into ON geometry.
// Returns a list of ON_Brep* (owned) and ON_NurbsSurface* (owned) that the
// caller must add to an ONX_Model and then delete.
// If no geometry is produced, returns empty vectors and the caller should
// fall back to mesh export.
static void build_on_geometry_from_shape(
    const TopoDS_Shape& shape,
    std::vector<ON_Brep*>& out_breps,
    std::vector<ON_NurbsSurface*>& out_surfs
) {
    ON_Brep* on_brep = ON_Brep::New();
    if (!on_brep) return;

    std::vector<std::unique_ptr<ON_NurbsSurface>> standalone_surfs;
    std::vector<int> face_indices;

    for (TopExp_Explorer exp(shape, TopAbs_FACE); exp.More(); exp.Next()) {
        TopoDS_Face face = TopoDS::Face(exp.Current());

        TopLoc_Location loc;
        Handle(Geom_Surface) surf = BRep_Tool::Surface(face, loc);
        if (surf.IsNull()) continue;

        // --- Step 1: try analytic surface (sphere, torus) ---
        // These produce valid non-periodic ON_NurbsSurface forms directly.
        auto analytic = try_analytic_surface_to_on(surf, loc);
        if (analytic) {
            ON_BrepFace* on_face = on_brep->NewFace(*analytic);
            if (on_face) {
                ON_BrepLoop* loop = on_brep->NewOuterLoop(on_face->m_face_index);
                if (loop) {
                    face_indices.push_back(on_face->m_face_index);
                    continue; // analytic BRep face succeeded
                }
                on_brep->DeleteFace(on_brep->m_F[on_face->m_face_index], true);
            }
            // If BRep building fails, keep as standalone surface
            standalone_surfs.push_back(std::move(analytic));
            continue;
        }

        // --- Step 2: generic NURBS conversion for everything else ---
        Handle(Geom_BSplineSurface) bsurf = Handle(Geom_BSplineSurface)::DownCast(surf);
        if (bsurf.IsNull()) {
            Handle(Geom_RectangularTrimmedSurface) trimmed = Handle(Geom_RectangularTrimmedSurface)::DownCast(surf);
            if (!trimmed.IsNull()) {
                Handle(Geom_Surface) basis = trimmed->BasisSurface();
                if (!basis.IsNull()) {
                    bsurf = Handle(Geom_BSplineSurface)::DownCast(basis);
                }
            }
        }
        if (bsurf.IsNull()) {
            // Planar surfaces throw from GeomConvert::SurfaceToBSplineSurface.
            // Use BRepBuilderAPI_NurbsConvert as a fallback.
            try {
                BRepBuilderAPI_NurbsConvert nurbs_converter(face, false);
                if (nurbs_converter.IsDone()) {
                    TopoDS_Face nurbs_face = TopoDS::Face(nurbs_converter.Shape());
                    TopLoc_Location nurbs_loc;
                    Handle(Geom_Surface) nurbs_surf = BRep_Tool::Surface(nurbs_face, nurbs_loc);
                    if (!nurbs_surf.IsNull()) {
                        bsurf = Handle(Geom_BSplineSurface)::DownCast(nurbs_surf);
                        if (bsurf.IsNull() && !nurbs_loc.IsIdentity()) {
                            // If the converter applied a transform to the surface
                            // directly, we need to capture it.
                        }
                    }
                }
            } catch (...) {
            }
        }
        if (bsurf.IsNull()) continue;

        if (!loc.IsIdentity()) {
            gp_Trsf tr = loc.Transformation();
            bsurf = Handle(Geom_BSplineSurface)::DownCast(bsurf->Transformed(tr));
            if (bsurf.IsNull()) continue;
        }

        // Clamp periodic directions so ON_Brep::NewFace can build a valid face.
        {
            Handle(Geom_BSplineSurface) copy = Handle(Geom_BSplineSurface)::DownCast(bsurf->Copy());
            if (!copy.IsNull()) {
                if (copy->IsUPeriodic()) copy->SetUNotPeriodic();
                if (copy->IsVPeriodic()) copy->SetVNotPeriodic();
                bsurf = copy;
            } else {
                continue;
            }
        }

        auto on_surf = occ_surface_to_on_nurbs(bsurf);
        if (!on_surf) continue;

        ON_BrepFace* on_face = on_brep->NewFace(*on_surf);
        if (on_face) {
            face_indices.push_back(on_face->m_face_index);
        } else {
            standalone_surfs.push_back(std::move(on_surf));
        }
    }

    // Build outer loops; discard BRep faces that fail.
    std::vector<int> success_faces;
    for (int fi : face_indices) {
        if (on_brep->NewOuterLoop(fi)) {
            success_faces.push_back(fi);
        } else {
            const ON_BrepFace& f = on_brep->m_F[fi];
            if (f.m_si >= 0 && f.m_si < on_brep->m_S.Count()) {
                ON_Surface* srf = on_brep->m_S[f.m_si];
                if (srf) {
                    ON_NurbsSurface* ns = ON_NurbsSurface::Cast(srf);
                    if (ns) {
                        standalone_surfs.push_back(std::unique_ptr<ON_NurbsSurface>(ns->Duplicate()));
                    }
                }
            }
            on_brep->DeleteFace(on_brep->m_F[fi], true);
        }
    }

    if (!success_faces.empty()) {
        on_brep->SetVertices();
        on_brep->SetTrimIsoFlags();
        on_brep->SetTrimTypeFlags();
        on_brep->SetTolerancesBoxesAndFlags(0.0, true, true, true, true);
        on_brep->Compact();
        out_breps.push_back(on_brep);
    } else {
        delete on_brep;
    }

    for (auto& s : standalone_surfs) {
        out_surfs.push_back(s.release());
    }
}

std::tuple<bool, std::string> export_3dm_brep(int shape_id, const std::string& filepath) {
    if (!has_shape(shape_id)) {
        return {false, "Shape ID not found in registry"};
    }

    try {
        TopoDS_Shape shape = get_shape(shape_id);

        // Pass the original shape to the builder so it can detect analytic
        // surfaces (sphere, torus) and produce valid ON_NurbsSurface forms.
        // BRepBuilderAPI_NurbsConvert is applied only inside the builder for
        // non-analytic faces.
        std::vector<ON_Brep*> breps;
        std::vector<ON_NurbsSurface*> surfs;
        build_on_geometry_from_shape(shape, breps, surfs);

        if (breps.empty() && surfs.empty()) {
            return export_3dm_mesh(shape_id, filepath);
        }

        ONX_Model model;
        for (ON_Brep* b : breps) {
            ON_3dmObjectAttributes* attr = new ON_3dmObjectAttributes();
            attr->m_name = "OCC_Brep_Export";
            ON_CreateUuid(attr->m_uuid);
            model.AddModelGeometryComponent(b, attr);
        }
        for (ON_NurbsSurface* s : surfs) {
            ON_3dmObjectAttributes* attr = new ON_3dmObjectAttributes();
            attr->m_name = "OCC_Surface_Export";
            ON_CreateUuid(attr->m_uuid);
            model.AddModelGeometryComponent(s, attr);
        }

        bool ok = model.Write(filepath.c_str(), 0);
        if (!ok) {
            return {false, "3DM BRep/Surface write failed"};
        }
        return {true, "Exported 3DM BRep/Surface: " + filepath};
    } catch (const std::exception& e) {
        return {false, std::string("3DM BRep export error: ") + e.what()};
    }
}

// ---------------------------------------------------------------------------
// Import: 3DM -> TopoDS_Shape (Brep / Surface / Mesh)
// ---------------------------------------------------------------------------

static TopoDS_Shape on_brep_to_occ_shape(const ON_Brep* on_brep) {
    if (!on_brep || on_brep->m_F.Count() == 0) {
        return TopoDS_Shape();
    }

    BRepBuilderAPI_Sewing sewing(1e-6);
    BRep_Builder builder;
    TopoDS_Compound compound;
    builder.MakeCompound(compound);
    bool has_any = false;

    for (int fi = 0; fi < on_brep->m_F.Count(); ++fi) {
        const ON_BrepFace& on_face = on_brep->m_F[fi];
        const ON_Surface* on_surf = on_face.SurfaceOf();
        if (!on_surf) {
            continue;
        }

        const ON_NurbsSurface* on_nurbs = ON_NurbsSurface::Cast(on_surf);
        if (!on_nurbs) {
            // Try to get NURBS form
            ON_NurbsSurface nurbs_form;
            if (on_surf->GetNurbForm(nurbs_form) && nurbs_form.IsValid()) {
                on_nurbs = &nurbs_form;
            }
        }

        if (!on_nurbs) {
            continue;
        }

        Handle(Geom_BSplineSurface) occ_surf = on_nurbs_to_occ_surface(on_nurbs);
        if (occ_surf.IsNull()) {
            continue;
        }

        // Build face.  If the BRep has trim curves we should build a wire
        // from them; for a first pass we create an untrimmed face from the
        // surface domain and let the sewing tool close it if possible.
        BRepBuilderAPI_MakeFace face_builder(occ_surf, 1e-7);
        if (face_builder.IsDone()) {
            TopoDS_Face face = face_builder.Face();
            if (!face.IsNull()) {
                sewing.Add(face);
                has_any = true;
            }
        }
    }

    if (!has_any) {
        return TopoDS_Shape();
    }

    sewing.Perform();
    TopoDS_Shape result = sewing.SewedShape();
    if (!result.IsNull()) {
        return result;
    }

    // Fallback: return compound of individual faces
    for (TopExp_Explorer exp(sewing.SewedShape(), TopAbs_FACE); exp.More(); exp.Next()) {
        builder.Add(compound, exp.Current());
    }
    return compound;
}

static TopoDS_Shape on_surface_to_occ_shape(const ON_Surface* on_surf) {
    if (!on_surf) {
        return TopoDS_Shape();
    }

    const ON_NurbsSurface* on_nurbs = ON_NurbsSurface::Cast(on_surf);
    if (!on_nurbs) {
        ON_NurbsSurface nurbs_form;
        if (on_surf->GetNurbForm(nurbs_form) && nurbs_form.IsValid()) {
            on_nurbs = &nurbs_form;
        }
    }

    if (!on_nurbs) {
        return TopoDS_Shape();
    }

    Handle(Geom_BSplineSurface) occ_surf = on_nurbs_to_occ_surface(on_nurbs);
    if (occ_surf.IsNull()) {
        return TopoDS_Shape();
    }

    BRepBuilderAPI_MakeFace face_builder(occ_surf, 1e-7);
    if (face_builder.IsDone()) {
        return face_builder.Shape();
    }
    return TopoDS_Shape();
}

static TopoDS_Shape on_mesh_to_occ_shape(const ON_Mesh* on_mesh) {
    if (!on_mesh || on_mesh->VertexCount() == 0 || on_mesh->FaceCount() == 0) {
        return TopoDS_Shape();
    }

    std::vector<gp_Pnt> vertices;
    vertices.reserve(on_mesh->VertexCount());
    for (int i = 0; i < on_mesh->VertexCount(); ++i) {
        ON_3fPoint p = on_mesh->m_V[i];
        vertices.emplace_back(p.x, p.y, p.z);
    }

    std::vector<std::array<int, 3>> faces;
    faces.reserve(on_mesh->FaceCount());
    const ON_MeshFace* mesh_faces = on_mesh->m_F.Array();
    for (int i = 0; i < on_mesh->FaceCount(); ++i) {
        faces.push_back({mesh_faces[i].vi[0], mesh_faces[i].vi[1], mesh_faces[i].vi[2]});
        if (mesh_faces[i].vi[2] != mesh_faces[i].vi[3]) {
            faces.push_back({mesh_faces[i].vi[0], mesh_faces[i].vi[2], mesh_faces[i].vi[3]});
        }
    }

    if (vertices.empty() || faces.empty()) {
        return TopoDS_Shape();
    }

    BRep_Builder builder;
    TopoDS_Compound compound;
    builder.MakeCompound(compound);

    for (const auto& tri : faces) {
        gp_Pnt a = vertices[tri[0]];
        gp_Pnt b = vertices[tri[1]];
        gp_Pnt c = vertices[tri[2]];

        TopoDS_Edge e1 = BRepBuilderAPI_MakeEdge(a, b);
        TopoDS_Edge e2 = BRepBuilderAPI_MakeEdge(b, c);
        TopoDS_Edge e3 = BRepBuilderAPI_MakeEdge(c, a);

        BRepBuilderAPI_MakeWire wire_builder;
        wire_builder.Add(e1);
        wire_builder.Add(e2);
        wire_builder.Add(e3);

        if (wire_builder.IsDone()) {
            BRepBuilderAPI_MakeFace face_builder(wire_builder.Wire());
            if (face_builder.IsDone()) {
                builder.Add(compound, face_builder.Face());
            }
        }
    }

    return compound;
}

std::vector<std::pair<int, std::string>> import_3dm(const std::string& filepath) {
    std::vector<std::pair<int, std::string>> results;

    try {
        ONX_Model model;
        bool ok = model.Read(filepath.c_str());
        if (!ok) {
            return results;
        }

        ONX_ModelComponentIterator it(model, ON_ModelComponent::Type::ModelGeometry);
        for (const ON_ModelComponent* component = it.FirstComponent(); component != nullptr; component = it.NextComponent()) {
            const ON_ModelGeometryComponent* geom_component = ON_ModelGeometryComponent::Cast(component);
            if (!geom_component) {
                continue;
            }

            const ON_Geometry* geometry = geom_component->Geometry(nullptr);
            if (!geometry) {
                continue;
            }

            TopoDS_Shape shape;
            std::string geom_type = "Unknown";

            // Try ON_Brep first
            const ON_Brep* on_brep = ON_Brep::Cast(geometry);
            if (on_brep) {
                shape = on_brep_to_occ_shape(on_brep);
                geom_type = "BRep";
            }

            // Try ON_Mesh
            if (shape.IsNull()) {
                const ON_Mesh* on_mesh = ON_Mesh::Cast(geometry);
                if (on_mesh) {
                    shape = on_mesh_to_occ_shape(on_mesh);
                    geom_type = "Mesh";
                }
            }

            // Try ON_NurbsSurface (standalone surface from sphere/torus export)
            if (shape.IsNull()) {
                const ON_NurbsSurface* on_nurbs_surf = ON_NurbsSurface::Cast(geometry);
                if (on_nurbs_surf) {
                    shape = on_surface_to_occ_shape(on_nurbs_surf);
                    geom_type = "Surface";
                }
            }

            // Try generic ON_Surface (fallback)
            if (shape.IsNull()) {
                const ON_Surface* on_surf = ON_Surface::Cast(geometry);
                if (on_surf) {
                    shape = on_surface_to_occ_shape(on_surf);
                    geom_type = "Surface";
                }
            }

            if (!shape.IsNull()) {
                int shape_id = register_shape(shape);
                results.emplace_back(shape_id, geom_type);
            }
        }
    } catch (const std::exception& e) {
        // silently return empty on exception
    }

    return results;
}

std::tuple<bool, std::string> export_3dm_multi(const std::vector<int>& shape_ids, const std::string& filepath) {
    if (shape_ids.empty()) {
        return {false, "No shapes selected for export"};
    }

    try {
        ONX_Model model;
        size_t exported_count = 0;

        for (int shape_id : shape_ids) {
            if (!has_shape(shape_id)) {
                continue;
            }

            TopoDS_Shape shape = get_shape(shape_id);
            bool exported = false;

            // Try BRep export via analytic detection + ON_Brep builder
            try {
                std::vector<ON_Brep*> breps;
                std::vector<ON_NurbsSurface*> surfs;
                build_on_geometry_from_shape(shape, breps, surfs);
                for (ON_Brep* b : breps) {
                    ON_3dmObjectAttributes* attributes = new ON_3dmObjectAttributes();
                    attributes->m_name = "OCC_Brep_Export";
                    ON_CreateUuid(attributes->m_uuid);
                    model.AddModelGeometryComponent(b, attributes);
                    exported = true;
                    exported_count++;
                }
                for (ON_NurbsSurface* s : surfs) {
                    ON_3dmObjectAttributes* attributes = new ON_3dmObjectAttributes();
                    attributes->m_name = "OCC_Surface_Export";
                    ON_CreateUuid(attributes->m_uuid);
                    model.AddModelGeometryComponent(s, attributes);
                    exported = true;
                    exported_count++;
                }
            } catch (...) {
            }

            // Fallback to mesh export for this shape
            if (!exported) {
                BRepMesh_IncrementalMesh mesher(shape, 0.1);
                std::vector<ON_3dPoint> vertices;
                std::vector<std::array<int, 3>> triangles;

                for (TopExp_Explorer exp(shape, TopAbs_FACE); exp.More(); exp.Next()) {
                    TopoDS_Face face = TopoDS::Face(exp.Current());
                    TopLoc_Location location;
                    Handle(Poly_Triangulation) triangulation = BRep_Tool::Triangulation(face, location);
                    if (triangulation.IsNull()) continue;

                    const gp_Trsf transform = location.Transformation();
                    const int node_count = triangulation->NbNodes();
                    const int triangle_count = triangulation->NbTriangles();
                    int vertex_offset = static_cast<int>(vertices.size());

                    for (int i = 1; i <= node_count; ++i) {
                        gp_Pnt p = triangulation->Node(i).Transformed(transform);
                        vertices.emplace_back(p.X(), p.Y(), p.Z());
                    }
                    for (int i = 1; i <= triangle_count; ++i) {
                        Poly_Triangle triangle = triangulation->Triangle(i);
                        int n1, n2, n3;
                        triangle.Get(n1, n2, n3);
                        if (face.Orientation() == TopAbs_REVERSED) {
                            triangles.push_back({vertex_offset + n1 - 1, vertex_offset + n3 - 1, vertex_offset + n2 - 1});
                        } else {
                            triangles.push_back({vertex_offset + n1 - 1, vertex_offset + n2 - 1, vertex_offset + n3 - 1});
                        }
                    }
                }

                if (!vertices.empty()) {
                    ON_Mesh* on_mesh = new ON_Mesh(
                        static_cast<int>(triangles.size()),
                        static_cast<int>(vertices.size()),
                        false,
                        false
                    );
                    for (size_t i = 0; i < vertices.size(); ++i) {
                        on_mesh->SetVertex(static_cast<int>(i), vertices[i]);
                    }
                    for (size_t i = 0; i < triangles.size(); ++i) {
                        on_mesh->SetTriangle(static_cast<int>(i), triangles[i][0], triangles[i][1], triangles[i][2]);
                    }
                    ON_3dmObjectAttributes* attributes = new ON_3dmObjectAttributes();
                    attributes->m_name = "OCC_Mesh_Export";
                    ON_CreateUuid(attributes->m_uuid);
                    model.AddModelGeometryComponent(on_mesh, attributes);
                    exported = true;
                    exported_count++;
                }
            }
        }

        if (exported_count == 0) {
            return {false, "No shapes could be exported"};
        }

        bool ok = model.Write(filepath.c_str(), 0);
        if (!ok) {
            return {false, "3DM multi-export write failed"};
        }
        return {true, "Exported " + std::to_string(exported_count) + " shape(s) to " + filepath};
    } catch (const std::exception& e) {
        return {false, std::string("3DM multi-export error: ") + e.what()};
    }
}

// Keep the old mesh-only export symbol as an alias to the new BRep export.
std::tuple<bool, std::string> export_3dm(int shape_id, const std::string& filepath) {
    return export_3dm_brep(shape_id, filepath);
}
