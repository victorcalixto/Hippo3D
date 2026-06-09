#include "occ_boolean.hpp"
#include "occ_registry.hpp"

#include <BRepAlgoAPI_Fuse.hxx>
#include <BRepAlgoAPI_Cut.hxx>
#include <BRepAlgoAPI_Common.hxx>
#include <TopoDS_Shape.hxx>
#include <TopExp_Explorer.hxx>

#include <stdexcept>

// Boolean Union (Fuse)
int occ_boolean_fuse(const std::vector<int>& shape_ids) {
    if (shape_ids.size() < 2) {
        throw std::invalid_argument("occ_boolean_fuse: need at least 2 shapes");
    }

    // Start with first shape
    TopoDS_Shape result = get_shape(shape_ids[0]);

    for (size_t i = 1; i < shape_ids.size(); ++i) {
        if (!has_shape(shape_ids[i])) {
            throw std::invalid_argument("occ_boolean_fuse: shape id not found");
        }
        TopoDS_Shape tool = get_shape(shape_ids[i]);

        BRepAlgoAPI_Fuse fuse(result, tool);
        if (!fuse.IsDone()) {
            throw std::runtime_error("occ_boolean_fuse: BRepAlgoAPI_Fuse failed");
        }
        result = fuse.Shape();
    }

    return register_shape(result);
}

// Boolean Difference (Cut)
int occ_boolean_cut(int base_shape_id, const std::vector<int>& tool_shape_ids) {
    if (!has_shape(base_shape_id)) {
        throw std::invalid_argument("occ_boolean_cut: base shape id not found");
    }
    if (tool_shape_ids.empty()) {
        throw std::invalid_argument("occ_boolean_cut: need at least 1 tool shape");
    }

    TopoDS_Shape result = get_shape(base_shape_id);

    for (int tool_id : tool_shape_ids) {
        if (!has_shape(tool_id)) {
            throw std::invalid_argument("occ_boolean_cut: tool shape id not found");
        }
        TopoDS_Shape tool = get_shape(tool_id);

        BRepAlgoAPI_Cut cutter(result, tool);
        if (!cutter.IsDone()) {
            throw std::runtime_error("occ_boolean_cut: BRepAlgoAPI_Cut failed");
        }
        result = cutter.Shape();
    }

    return register_shape(result);
}

// Boolean Intersection (Common)
int occ_boolean_common(int shape_a_id, int shape_b_id) {
    if (!has_shape(shape_a_id) || !has_shape(shape_b_id)) {
        throw std::invalid_argument("occ_boolean_common: shape id not found");
    }

    TopoDS_Shape shape_a = get_shape(shape_a_id);
    TopoDS_Shape shape_b = get_shape(shape_b_id);

    BRepAlgoAPI_Common common(shape_a, shape_b);
    if (!common.IsDone()) {
        throw std::runtime_error("occ_boolean_common: BRepAlgoAPI_Common failed");
    }

    TopoDS_Shape result = common.Shape();
    return register_shape(result);
}
