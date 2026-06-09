#pragma once

#include <vector>

// Boolean Union (Fuse)
// shape_ids: list of OCC shape IDs to fuse together
int occ_boolean_fuse(
    const std::vector<int>& shape_ids
);

// Boolean Difference (Cut)
// base_shape_id: the shape to cut from
// tool_shape_ids: the shapes to subtract
int occ_boolean_cut(
    int base_shape_id,
    const std::vector<int>& tool_shape_ids
);

// Boolean Intersection (Common)
int occ_boolean_common(
    int shape_a_id,
    int shape_b_id
);
