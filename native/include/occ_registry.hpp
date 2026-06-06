#pragma once

#include <TopoDS_Shape.hxx>

int register_shape(const TopoDS_Shape& shape);
TopoDS_Shape get_shape(int shape_id);
bool has_shape(int shape_id);
int shape_count();
void clear_registry();
