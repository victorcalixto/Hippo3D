#include "occ_registry.hpp"

#include <map>
#include <stdexcept>

#include <BRepBuilderAPI_Transform.hxx>
#include <gp_Trsf.hxx>

static std::map<int, TopoDS_Shape> g_shapes;
static int g_next_shape_id = 1;

int register_shape(const TopoDS_Shape& shape) {
    const int shape_id = g_next_shape_id++;
    g_shapes[shape_id] = shape;
    return shape_id;
}

TopoDS_Shape get_shape(int shape_id) {
    auto it = g_shapes.find(shape_id);
    if (it == g_shapes.end()) {
        throw std::runtime_error("OCC shape id not found");
    }
    return it->second;
}

bool has_shape(int shape_id) {
    return g_shapes.find(shape_id) != g_shapes.end();
}

int shape_count() {
    return static_cast<int>(g_shapes.size());
}

void clear_registry() {
    g_shapes.clear();
    g_next_shape_id = 1;
}

void delete_shape(int shape_id) {
    g_shapes.erase(shape_id);
}

int transform_shape(int shape_id, const std::array<double, 16>& matrix) {
    TopoDS_Shape shape = get_shape(shape_id);
    gp_Trsf trsf;
    trsf.SetValues(
        matrix[0], matrix[1], matrix[2], matrix[3],
        matrix[4], matrix[5], matrix[6], matrix[7],
        matrix[8], matrix[9], matrix[10], matrix[11]
    );
    BRepBuilderAPI_Transform transform(shape, trsf, true);
    if (!transform.IsDone()) {
        return shape_id;
    }
    return register_shape(transform.Shape());
}
