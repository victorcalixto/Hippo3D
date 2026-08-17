#include "occ_registry.hpp"

#include <array>
#include <map>
#include <stdexcept>
#include <string>

#include <BRepBuilderAPI_Transform.hxx>
#include <gp_Trsf.hxx>
#include <gp_Vec.hxx>
#include <TopAbs.hxx>

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
    // Row-major 4x4 matrix: the last column is translation.
    // MSVC needs explicit std::array::operator[] on a const reference, so use .data().
    const double* m = matrix.data();
    trsf.SetValues(
        m[0],  m[1],  m[2],  m[3],
        m[4],  m[5],  m[6],  m[7],
        m[8],  m[9],  m[10], m[11]
    );
    // gp_Trsf.SetValues ignores the last column (translation) in this overload.
    // We must set translation explicitly from the 4th column.
    gp_Vec translation(m[3], m[7], m[11]);
    if (translation.Magnitude() > 1e-12) {
        trsf.SetTranslationPart(translation);
    }
    BRepBuilderAPI_Transform transform(shape, trsf, true);
    if (!transform.IsDone()) {
        return shape_id;
    }
    return register_shape(transform.Shape());
}

std::string get_shape_type(int shape_id) {
    const TopoDS_Shape shape = get_shape(shape_id);
    if (shape.IsNull()) {
        return "null";
    }
    switch (shape.ShapeType()) {
        case TopAbs_COMPOUND:   return "compound";
        case TopAbs_COMPSOLID:  return "compsolid";
        case TopAbs_SOLID:      return "solid";
        case TopAbs_SHELL:      return "shell";
        case TopAbs_FACE:       return "face";
        case TopAbs_WIRE:       return "wire";
        case TopAbs_EDGE:       return "edge";
        case TopAbs_VERTEX:     return "vertex";
        default:                return "shape";
    }
}
