#include <pybind11/pybind11.h>

#include "occ_primitives.hpp"
#include "occ_registry.hpp"

namespace py = pybind11;

PYBIND11_MODULE(hippo_occ_core, m) {
    m.doc() = "Hippo3D native OpenCascade core";

    m.def("make_box_mesh", &make_box_mesh,
          py::arg("width"), py::arg("depth"), py::arg("height"), py::arg("deflection") = 0.1);

    m.def("make_sphere_mesh", &make_sphere_mesh,
          py::arg("radius"), py::arg("deflection") = 0.1);

    m.def("make_cylinder_mesh", &make_cylinder_mesh,
          py::arg("radius"), py::arg("height"), py::arg("deflection") = 0.1);

    m.def("make_cone_mesh", &make_cone_mesh,
          py::arg("radius1"), py::arg("radius2"), py::arg("height"), py::arg("deflection") = 0.1);

    m.def("make_torus_mesh", &make_torus_mesh,
          py::arg("major_radius"), py::arg("minor_radius"), py::arg("deflection") = 0.1);

    m.def("remesh_shape", &remesh_shape,
          py::arg("shape_id"), py::arg("deflection") = 0.1);

    m.def("get_shape_edges", &get_shape_edges,
          py::arg("shape_id"), py::arg("deflection") = 0.1);

    m.def("shape_count", &shape_count);
    m.def("clear_registry", &clear_registry);
}
