"""Asset front convention and orientation helpers.

Front convention (checked in Isaac Sim 6.1 with 4-side renders of ~40 assets):
an asset's front points to +X of its own Z-up frame. This holds for most SimReady, Isaac Props and
third-party store assets; known exceptions are listed in KNOWN_FRONTS, a local folder can declare
its own with agentic-sim-world-gen.json {"front": ...}, and ObjectSpec.front_offset_deg corrects a single object
(e.g. 180 if it ends up backwards). Directions: 0 = +X, 90 = +Y, 180 = -X, 270 = -Y.
"""

from __future__ import annotations

import math
import re
from pathlib import PurePosixPath

from agentic_sim_world_gen.assets import cards
from agentic_sim_world_gen.assets.record import AssetRecord
from agentic_sim_world_gen.scene.spec import ObjectSpec

DEFAULT_FRONT_DEG = 0.0

# Assets whose front is not +X, verified with scripts/isaac/front_views.py (Isaac Sim 6.1, 2026-10-25).
# Key: file name without the extension and the "_NN" version suffix.
KNOWN_FRONTS: dict[str, float] = {
    "sm_chair_office_fabric_manager_a01": 270.0,
    "sm_chair_office_amf_black_a01": 270.0,
    "sm_computer_monitor_office_a01": 270.0,
    "sm_computer_printer_office_a01": 270.0,
    "sm_storage_locker_double_bench_b01": 270.0,
    "gate_seating": 270.0,  # ForgeCAD airport bench: long along X, seats and the suitcase toward -Y
}
# Families by name pattern (re.match on the same key). SimReady warehouse racks (sm_rack_*, incl.
# compositions with goods) are long along X and open on both long sides, so their front is -Y.
KNOWN_FRONT_PATTERNS: list[tuple[str, float]] = [
    (r"sm_rack_", 270.0),
]


def _stem(uri: str) -> str:
    name = PurePosixPath(uri.split("?", 1)[0]).name
    return re.sub(r"\.usd[acz]?$", "", name, flags=re.I)


def asset_front_deg(a: AssetRecord) -> float:
    """Front direction in the asset's own Z-up footprint frame, before any per-object offset."""
    if a.front_deg is not None:
        return a.front_deg
    stem = _stem(a.uri)
    for key in (stem, re.sub(r"_\d+$", "", stem)):
        if key in KNOWN_FRONTS:
            return KNOWN_FRONTS[key]
    for pattern, deg in KNOWN_FRONT_PATTERNS:
        if re.match(pattern, stem):
            return deg
    if (card := cards.of(a)) and card.get("front_deg") is not None:  # found by a look at the asset
        return float(card["front_deg"])
    return DEFAULT_FRONT_DEG


def front_local_deg(a: AssetRecord, o: ObjectSpec) -> float:
    return asset_front_deg(a) + o.front_offset_deg


def norm_deg(y: float) -> float:
    y %= 360.0
    return y - 360.0 if y > 180.0 else y


def yaw_for_facing(a: AssetRecord, o: ObjectSpec, facing_deg: float) -> float:
    return norm_deg(facing_deg - front_local_deg(a, o))


def facing_of(a: AssetRecord, o: ObjectSpec) -> float:
    return o.yaw_deg + front_local_deg(a, o)


def unit(deg: float) -> tuple[float, float]:
    r = math.radians(deg)
    return math.cos(r), math.sin(r)


def half_extent(a: AssetRecord, yaw_deg: float, direction_deg: float) -> float:
    """Half size of the rotated footprint measured along a world direction."""
    w, d, _ = a.size_m
    ux, uy = unit(direction_deg)
    ex, ey = unit(yaw_deg)
    return abs(w / 2 * (ex * ux + ey * uy)) + abs(d / 2 * (-ey * ux + ex * uy))
