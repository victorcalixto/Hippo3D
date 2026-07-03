#pragma once

#include <vector>
#include <array>

// Trim: select surface, cut with cutter, keep side indicated by side_point
// surface_shape_id: the surface/face/solid to trim
// cutter_shape_id: the cutting surface/face/solid
// side_point: 3D point on the side to keep
int occ_trim(
    int surface_shape_id,
    int cutter_shape_id,
    const std::array<double, 3>& side_point
);

// Split: select surface, cut with cutter, return all pieces
// surface_shape_id: the surface/face/solid to split
// cutter_shape_id: the cutting surface/face/solid
std::vector<int> occ_split(
    int surface_shape_id,
    int cutter_shape_id
);

// Untrim: restore the original untrimmed surface from a trimmed face
// trimmed_face_shape_id: the trimmed face shape
// (stores original surface alongside trimmed face in registry)
int occ_untrim(
    int trimmed_face_shape_id
);
