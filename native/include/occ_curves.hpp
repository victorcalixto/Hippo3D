#pragma once

#include <pybind11/pybind11.h>
#include <vector>
#include <array>

namespace py = pybind11;

// Create a wire from a sequence of point-connected line segments
// If closed=true, connects last point back to first
int make_polyline_wire(
    const std::vector<std::array<double, 3>>& points,
    bool closed
);

// Create a B-Spline curve from control points (x,y,z,w), knots, multiplicities, degree
// Returns shape_id. cvs: each element is {x, y, z, w} where w is weight (1.0 for non-rational)
int make_nurbs_curve(
    const std::vector<std::array<double, 4>>& cvs,
    const std::vector<double>& knots,
    const std::vector<int>& mults,
    int degree,
    bool periodic
);

// Create a circular arc wire from center, radius, normal, start/end angles (radians)
int make_circle_wire(
    const std::array<double, 3>& center,
    double radius,
    const std::array<double, 3>& normal,
    double start_angle,
    double end_angle
);

// Create a full circle wire (convenience wrapper)
int make_full_circle(
    const std::array<double, 3>& center,
    double radius,
    const std::array<double, 3>& normal
);

// Sample a curve/wire into polylines for display.
// Returns a Python dict with {"edges": [[(x,y,z), ...], ...]}
py::dict remesh_curve(int shape_id, double deflection);
