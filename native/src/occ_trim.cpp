#include "occ_trim.hpp"
#include "occ_registry.hpp"

#include <BRepAlgoAPI_Splitter.hxx>
#include <BRepBuilderAPI_MakeSolid.hxx>
#include <BRepBuilderAPI_Sewing.hxx>
#include <BRepBuilderAPI_MakeFace.hxx>
#include <BRepClass3d_SolidClassifier.hxx>
#include <BRepGProp.hxx>
#include <Geom_Surface.hxx>
#include <ShapeAnalysis.hxx>
#include <TopoDS_Face.hxx>
#include <TopoDS_Solid.hxx>
#include <TopoDS_Compound.hxx>
#include <TopoDS.hxx>
#include <TopoDS_Wire.hxx>
#include <TopExp_Explorer.hxx>
#include <TopTools_ListOfShape.hxx>
#include <gp_Pnt.hxx>
#include <gp_Vec.hxx>
#include <GProp_GProps.hxx>
#include <BRep_Tool.hxx>

#include <stdexcept>
#include <vector>

// Forward: find outer wire from face
static TopoDS_Wire get_outer_wire(const TopoDS_Face& face) {
    TopoDS_Wire outer;
    double best_len = -1.0;
    for (TopExp_Explorer exp(face, TopAbs_WIRE); exp.More(); exp.Next()) {
        TopoDS_Wire w = TopoDS::Wire(exp.Current());
        GProp_GProps props;
        BRepGProp::LinearProperties(w, props);
        double len = props.Mass();
        if (len > best_len) {
            best_len = len;
            outer = w;
        }
    }
    return outer;
}

// occ_split: split surface by cutter, return all pieces
std::vector<int> occ_split(int surface_shape_id, int cutter_shape_id) {
    if (!has_shape(surface_shape_id) || !has_shape(cutter_shape_id)) {
        throw std::invalid_argument("occ_split: shape id not found");
    }

    TopoDS_Shape surface = get_shape(surface_shape_id);
    TopoDS_Shape cutter = get_shape(cutter_shape_id);

    TopTools_ListOfShape arguments;
    arguments.Append(surface);
    TopTools_ListOfShape tools;
    tools.Append(cutter);

    BRepAlgoAPI_Splitter splitter;
    splitter.SetArguments(arguments);
    splitter.SetTools(tools);
    splitter.Build();

    if (!splitter.IsDone()) {
        throw std::runtime_error("occ_split: BRepAlgoAPI_Splitter failed");
    }

    TopoDS_Shape result = splitter.Shape();

    // Collect all sub-faces/solids
    std::vector<int> pieces;
    for (TopExp_Explorer exp(result, TopAbs_SOLID); exp.More(); exp.Next()) {
        pieces.push_back(register_shape(exp.Current()));
    }
    for (TopExp_Explorer exp(result, TopAbs_FACE); exp.More(); exp.Next()) {
        // Only register faces if no solids were found (pure shell case)
        if (pieces.empty()) {
            pieces.push_back(register_shape(exp.Current()));
        }
    }

    return pieces;
}

// occ_trim: split surface by cutter, keep side indicated by side_point
int occ_trim(int surface_shape_id, int cutter_shape_id,
             const std::array<double, 3>& side_point) {
    if (!has_shape(surface_shape_id) || !has_shape(cutter_shape_id)) {
        throw std::invalid_argument("occ_trim: shape id not found");
    }

    // Use split to get all pieces
    std::vector<int> pieces = occ_split(surface_shape_id, cutter_shape_id);
    if (pieces.empty()) {
        throw std::runtime_error("occ_trim: no pieces after split");
    }

    gp_Pnt sp(side_point[0], side_point[1], side_point[2]);

    // Pick piece containing side_point (inside for solid, closest distance for face)
    int best_id = -1;
    double best_dist = 1e100;

    for (int pid : pieces) {
        if (!has_shape(pid)) continue;
        TopoDS_Shape s = get_shape(pid);

        // If solid: use point-in-solid test
        bool inside = false;
        if (s.ShapeType() == TopAbs_SOLID || s.ShapeType() == TopAbs_COMPSOLID) {
            for (TopExp_Explorer exp(s, TopAbs_SOLID); exp.More(); exp.Next()) {
                BRepClass3d_SolidClassifier classifier(TopoDS::Solid(exp.Current()), sp, 1e-7);
                TopAbs_State state = classifier.State();
                if (state == TopAbs_IN || state == TopAbs_ON) {
                    inside = true;
                    break;
                }
            }
        }

        if (inside) {
            best_id = pid;
            break;
        }

        // Fallback: compute distance to closest face vertex/edge
        for (TopExp_Explorer exp(s, TopAbs_VERTEX); exp.More(); exp.Next()) {
            gp_Pnt vp = BRep_Tool::Pnt(TopoDS::Vertex(exp.Current()));
            double d = vp.Distance(sp);
            if (d < best_dist) {
                best_dist = d;
                best_id = pid;
            }
        }
    }

    if (best_id < 0) {
        throw std::runtime_error("occ_trim: could not determine which side to keep");
    }

    // Delete unused pieces from registry (optional, but keeps things clean)
    for (int pid : pieces) {
        if (pid != best_id) {
            delete_shape(pid);
        }
    }

    return best_id;
}

// occ_untrim: store original surface alongside trimmed face in registry
int occ_untrim(int trimmed_face_shape_id) {
    if (!has_shape(trimmed_face_shape_id)) {
        throw std::invalid_argument("occ_untrim: shape id not found");
    }

    TopoDS_Shape shape = get_shape(trimmed_face_shape_id);
    if (shape.ShapeType() != TopAbs_FACE) {
        throw std::invalid_argument("occ_untrim: input must be a face");
    }

    TopoDS_Face face = TopoDS::Face(shape);
    TopLoc_Location loc;
    Handle(Geom_Surface) surf = BRep_Tool::Surface(face, loc);

    if (surf.IsNull()) {
        throw std::runtime_error("occ_untrim: no surface found on face");
    }

    // Create untrimmed face from the underlying surface
    BRepBuilderAPI_MakeFace face_builder(surf, 1e-7);
    if (!face_builder.IsDone()) {
        throw std::runtime_error("occ_untrim: could not build untrimmed face from surface");
    }

    TopoDS_Face untrimmed = face_builder.Face();
    return register_shape(untrimmed);
}
