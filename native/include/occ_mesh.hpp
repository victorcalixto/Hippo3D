#pragma once

#include <pybind11/pybind11.h>
#include <TopoDS_Shape.hxx>

pybind11::dict shape_to_display_dict(const TopoDS_Shape& shape, int shape_id, double deflection = 0.1);
pybind11::dict shape_to_mesh_dict(const TopoDS_Shape& shape, double deflection = 0.1);
pybind11::list shape_edges_to_list(const TopoDS_Shape& shape, double deflection = 0.1);
