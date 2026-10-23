"""Rocket League field geometry, in Unreal units (uu).

The origin is the center of the field. Blue (team 0) defends the goal at
negative y, and orange (team 1) defends the goal at positive y.
"""

# Playable area, excluding the goal boxes.
FIELD_HALF_WIDTH = 4096.0  # x extent, side wall to side wall
FIELD_HALF_LENGTH = 5120.0  # y extent, back wall to back wall
CEILING_HEIGHT = 2044.0

GOAL_HALF_WIDTH = 892.755
GOAL_HEIGHT = 642.775
GOAL_DEPTH = 880.0

# Boost is stored in replays as 0-255. In game it is shown as 0-100.
BOOST_RAW_MAX = 255.0


def raw_boost_to_percent(raw: float) -> float:
    """Convert a raw replay boost value (0-255) to the in-game 0-100 scale."""
    return raw * 100.0 / BOOST_RAW_MAX
