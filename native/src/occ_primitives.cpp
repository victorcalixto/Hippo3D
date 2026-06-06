#include "occ_primitives.hpp"
#include "occ_mesh.hpp"
#include "occ_registry.hpp"

#include <BRepPrimAPI_MakeBox.hxx>
#include <BRepPrimAPI_MakeSphere.hxx>
#include <BRepPrimAPI_MakeCylinder.hxx>
#include <BRepPrimAPI_MakeCone.hxx>
#include <BRepPrimAPI_MakeTorus.hxx>

#include <TopoDS_Shape.hxx>

namespace py = pybind11;

static py::dict register_and_display(const TopoDS_Shape& shape, double deflection) {
    const int shape_id = register_shape(shape);
    return shape_to_display_dict(shape, shape_id, deflection);
}

py::dict make_box_mesh(double width, double depth, double height, double deflection) {
    TopoDS_Shape shape = BRepPrimAPI_MakeBox(width, depth, height).Shape();
    return register_and_display(shape, deflection);
}

py::dict make_sphere_mesh(double radius, double deflection) {
    TopoDS_Shape shape = BRepPrimAPI_MakeSphere(radius).Shape();
    return register_and_display(shape, deflection);
}

py::dict make_cylinder_mesh(double radius, double height, double deflection) {
    TopoDS_Shape shape = BRepPrimAPI_MakeCylinder(radius, height).Shape();
    return register_and_display(shape, deflection);
}

py::dict make_cone_mesh(double radius1, double radius2, double height, double deflection) {
    TopoDS_Shape shape = BRepPrimAPI_MakeCone(radius1, radius2, height).Shape();
    return register_and_display(shape, deflection);
}

py::dict make_torus_mesh(double major_radius, double minor_radius, double deflection) {
    TopoDS_Shape shape = BRepPrimAPI_MakeTorus(major_radius, minor_radius).Shape();
    return register_and_display(shape, deflection);
}

py::dict remesh_shape(int shape_id, double deflection) {
    TopoDS_Shape shape = get_shape(shape_id);
    return shape_to_display_dict(shape, shape_id, deflection);
}

py::list get_shape_edges(int shape_id, double deflection) {
    TopoDS_Shape shape = get_shape(shape_id);
    return shape_edges_to_list(shape, deflection);
}
