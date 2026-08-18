#pragma once

#include <vector>
#include <pybind11/pybind11.h>

// Explode a solid/compound/shell into individual face shape IDs.
// Returns a list of shape_ids, one per face, in deterministic order.
std::vector<int> get_solid_face_shape_ids(int shape_id);

// Sew a list of face shapes into a closed shell and, if possible, a solid.
// Returns the new shape_id, or -1 on failure.
int sew_faces_to_solid(const std::vector<int>& face_shape_ids, double tolerance = 1e-6);

// Build a pybind11 dict mapping face_index -> shape_id for all faces of a shape.
pybind11::dict get_solid_face_shape_ids_dict(int shape_id);

// Try to close a shell and build a solid. Uses shape healing if needed.
// Returns the new shape_id, or -1 on failure.
int make_solid_from_shell(int shell_shape_id);

// Replace one or more faces inside a solid/compound/shell with new faces.
// The surrounding topology (edges of the other faces) is preserved.
// Returns the new shape_id, or -1 on failure.
int replace_face_in_shape(int shape_id,
                          const std::vector<std::pair<int, int>>& old_new_face_shape_ids);
