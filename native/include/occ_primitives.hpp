#pragma once

#include <pybind11/pybind11.h>

pybind11::dict make_box_mesh(double width, double depth, double height, double deflection = 0.1);
pybind11::dict make_sphere_mesh(double radius, double deflection = 0.1);
pybind11::dict make_cylinder_mesh(double radius, double height, double deflection = 0.1);
pybind11::dict make_cone_mesh(double radius1, double radius2, double height, double deflection = 0.1);
pybind11::dict make_torus_mesh(double major_radius, double minor_radius, double deflection = 0.1);

pybind11::dict remesh_shape(int shape_id, double deflection = 0.1);
pybind11::list get_shape_edges(int shape_id, double deflection = 0.1);
