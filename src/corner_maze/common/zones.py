"""Zone boundary definitions for the Corner Maze.

Each zone is defined by an x range, y range, and an optional extra
constraint (for triangular corner zones). The lookup function takes
(x, y) and returns the zone ID, or 0 if the point doesn't fall in
any defined zone.
"""

from __future__ import annotations

import numpy as np

# (zone_id, x_min, x_max, y_min, y_max, extra_constraint_fn or None)
ZONE_DEFS: list[tuple[int, int, int, int, int, object]] = [
    (1,   0,  47,   0,  47, lambda x, y: x <= (47 - y)),
    (2,  43,  97,   0,  35, None),
    (3,  98, 138,   0,  35, None),
    (4, 139, 194,   0,  35, None),
    (5, 193, 239,   0,  47, lambda x, y: (x - 193) >= y),
    (6,   0,  34,  45, 100, None),
    (7,  96, 138,  36,  96, None),
    (8, 205, 239,  41,  97, None),
    (9,   0,  34, 101, 139, None),
    (10,  35,  95, 103, 137, None),
    (11,  96, 143,  97, 142, None),
    (12, 144, 204,  98, 139, None),
    (13, 205, 239,  98, 139, None),
    (14,   0,  34, 140, 196, None),
    (15, 102, 137, 143, 204, None),
    (16, 205, 239, 140, 195, None),
    (17,   0,  46, 194, 239, lambda x, y: x <= (y - 194)),
    (18,  43,  97, 205, 239, None),
    (19,  98, 139, 205, 239, None),
    (20, 140, 195, 205, 239, None),
    (21, 192, 239, 192, 239, lambda x, y: (x - 192) >= (239 - y)),
]


def xy_to_zone(x: int, y: int) -> int:
    """Return the zone ID for a given (x, y) coordinate, or 0 if none."""
    for zone_id, x_min, x_max, y_min, y_max, extra in ZONE_DEFS:
        if x_min <= x <= x_max and y_min <= y <= y_max:
            if extra is None or extra(x, y):
                return zone_id
    return 0


def xy_to_zone_array(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Vectorized zone lookup for arrays of x, y coordinates.

    Returns an int8 array of zone IDs (0 where no zone matches).
    """
    zones = np.zeros(len(x), dtype=np.int8)

    for zone_id, x_min, x_max, y_min, y_max, extra in ZONE_DEFS:
        in_rect = (x >= x_min) & (x <= x_max) & (y >= y_min) & (y <= y_max)

        if extra is not None:
            # Apply extra constraint only to points in the rectangle
            rect_idx = np.where(in_rect)[0]
            if len(rect_idx) > 0:
                extra_ok = np.array(
                    [extra(x[i], y[i]) for i in rect_idx], dtype=bool
                )
                in_rect_final = np.zeros(len(x), dtype=bool)
                in_rect_final[rect_idx[extra_ok]] = True
                in_rect = in_rect_final

        # Only assign if not already assigned (first match wins)
        zones[in_rect & (zones == 0)] = zone_id

    return zones
