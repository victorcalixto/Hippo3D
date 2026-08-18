#include "occ_solid_ops.hpp"
#include "occ_registry.hpp"

#include <BRepBuilderAPI_Sewing.hxx>
#include <BRepBuilderAPI_MakeSolid.hxx>
#include <ShapeFix_Shell.hxx>
#include <ShapeFix_Shape.hxx>
#include <ShapeFix_Face.hxx>
#include <BRepTools_ReShape.hxx>
#include <BRepTools.hxx>
#include <BRepTools_Substitution.hxx>
#include <TopoDS.hxx>
#include <TopoDS_Face.hxx>
#include <TopoDS_Shell.hxx>
#include <TopoDS_Solid.hxx>
#include <TopExp_Explorer.hxx>
#include <TopAbs.hxx>
#include <BRepCheck_Shell.hxx>

#include <vector>
#include <utility>

namespace py = pybind11;

std::vector<int> get_solid_face_shape_ids(int shape_id) {
    if (!has_shape(shape_id))
        throw std::invalid_argument("get_solid_face_shape_ids: shape id not found");
    TopoDS_Shape shape = get_shape(shape_id);
    std::vector<int> result;
    for (TopExp_Explorer exp(shape, TopAbs_FACE); exp.More(); exp.Next()) {
        TopoDS_Face face = TopoDS::Face(exp.Current());
        result.push_back(register_shape(face));
    }
    return result;
}

py::dict get_solid_face_shape_ids_dict(int shape_id) {
    std::vector<int> face_ids = get_solid_face_shape_ids(shape_id);
    py::dict result;
    for (size_t i = 0; i < face_ids.size(); ++i) {
        result[py::cast(static_cast<int>(i))] = face_ids[i];
    }
    return result;
}

int sew_faces_to_solid(const std::vector<int>& face_shape_ids, double tolerance) {
    BRepBuilderAPI_Sewing sewing(tolerance);
    int added = 0;
    for (int fid : face_shape_ids) {
        if (!has_shape(fid))
            continue;
        TopoDS_Shape shape = get_shape(fid);
        if (shape.IsNull() || shape.ShapeType() != TopAbs_FACE)
            continue;
        sewing.Add(TopoDS::Face(shape));
        ++added;
    }
    if (added == 0)
        return -1;

    sewing.Perform();
    TopoDS_Shape sewn = sewing.SewedShape();
    if (sewn.IsNull())
        return -1;

    // If we got a shell, try to build a solid from it if it is closed.
    if (sewn.ShapeType() == TopAbs_SHELL) {
        TopoDS_Shell shell = TopoDS::Shell(sewn);
        BRepCheck_Shell shell_check(shell);
        BRepCheck_Status status = shell_check.Closed();
        if (status == BRepCheck_NoError) {
            BRepBuilderAPI_MakeSolid solid_maker(shell);
            if (solid_maker.IsDone()) {
                TopoDS_Solid solid = solid_maker.Solid();
                if (!solid.IsNull())
                    return register_shape(solid);
            }
        }
    }

    // Fallback: register whatever we got (shell, compound, ...)
    return register_shape(sewn);
}

int make_solid_from_shell(int shell_shape_id) {
    if (!has_shape(shell_shape_id))
        return -1;
    TopoDS_Shape shape = get_shape(shell_shape_id);
    if (shape.IsNull() || shape.ShapeType() != TopAbs_SHELL)
        return -1;

    TopoDS_Shell shell = TopoDS::Shell(shape);

    // First attempt: use ShapeFix_Shell to fix the shell and then build a solid.
    try {
        ShapeFix_Shell shell_fixer;
        shell_fixer.Init(shell);
        shell_fixer.Perform();
        TopoDS_Shape fixed = shell_fixer.Shape();
        if (!fixed.IsNull()) {
            for (TopExp_Explorer exp(fixed, TopAbs_SHELL); exp.More(); exp.Next()) {
                TopoDS_Shell candidate = TopoDS::Shell(exp.Current());
                BRepCheck_Shell check(candidate);
                if (check.Closed() == BRepCheck_NoError) {
                    BRepBuilderAPI_MakeSolid maker(candidate);
                    if (maker.IsDone()) {
                        TopoDS_Solid solid = maker.Solid();
                        if (!solid.IsNull())
                            return register_shape(solid);
                    }
                }
            }
        }
    } catch (...) {
        // fall through
    }

    // Second attempt: general shape healing.
    try {
        ShapeFix_Shape shape_fixer(shape);
        shape_fixer.Perform();
        TopoDS_Shape healed = shape_fixer.Shape();
        if (!healed.IsNull()) {
            for (TopExp_Explorer exp(healed, TopAbs_SHELL); exp.More(); exp.Next()) {
                TopoDS_Shell candidate = TopoDS::Shell(exp.Current());
                BRepCheck_Shell check(candidate);
                if (check.Closed() == BRepCheck_NoError) {
                    BRepBuilderAPI_MakeSolid maker(candidate);
                    if (maker.IsDone()) {
                        TopoDS_Solid solid = maker.Solid();
                        if (!solid.IsNull())
                            return register_shape(solid);
                    }
                }
            }
            // If healing produced a solid directly, register it.
            if (healed.ShapeType() == TopAbs_SOLID)
                return register_shape(healed);
        }
    } catch (...) {
        // fall through
    }

    return -1;
}

int replace_face_in_shape(int shape_id,
                          const std::vector<std::pair<int, int>>& old_new_face_shape_ids) {
    if (!has_shape(shape_id))
        return -1;

    TopoDS_Shape shape = get_shape(shape_id);
    if (shape.IsNull())
        return -1;

    std::vector<std::pair<TopoDS_Face, TopoDS_Face>> replacements;
    replacements.reserve(old_new_face_shape_ids.size());
    for (const auto& p : old_new_face_shape_ids) {
        if (!has_shape(p.first) || !has_shape(p.second))
            return -1;
        TopoDS_Shape old_shape = get_shape(p.first);
        TopoDS_Shape new_shape = get_shape(p.second);
        if (old_shape.IsNull() || new_shape.IsNull())
            return -1;
        if (old_shape.ShapeType() != TopAbs_FACE || new_shape.ShapeType() != TopAbs_FACE)
            return -1;
        replacements.emplace_back(TopoDS::Face(old_shape), TopoDS::Face(new_shape));
    }

    try {
        // Apply all requested face substitutions in one ReShape pass. This
        // keeps the surrounding shell structure consistent when multiple faces
        // are edited at once.
        Handle(BRepTools_ReShape) reshaper = new BRepTools_ReShape();
        for (const auto& r : replacements)
            reshaper->Replace(r.first, r.second);
        TopoDS_Shape result = reshaper->Apply(shape);
        if (result.IsNull())
            return -1;

        // Healing the result improves the chance that the shell stays closed
        // after new faces with independent edge tolerances are inserted.
        ShapeFix_Shape fixer(result);
        fixer.SetPrecision(1e-6);
        fixer.Perform();
        TopoDS_Shape fixed = fixer.Shape();
        if (!fixed.IsNull())
            result = fixed;

        return register_shape(result);
    } catch (...) {
        return -1;
    }
}
