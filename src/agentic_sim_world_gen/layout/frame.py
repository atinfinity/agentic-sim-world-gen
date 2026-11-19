"""Asset front convention and orientation helpers.

Front convention (checked in Isaac Sim 6.1 with 4-side renders of ~40 assets):
an asset's front points to +X of its own Z-up frame. This holds for most SimReady, Isaac Props and
third-party store assets; known exceptions are listed in KNOWN_FRONTS, ForgeCAD's assets face -Y, a local
folder can declare its own with agentic-sim-world-gen.json {"front": ...}, and ObjectSpec.front_offset_deg corrects
a single object (e.g. 180 if it ends up backwards). Directions: 0 = +X, 90 = +Y, 180 = -X, 270 = -Y.
A thing hung on a wall without any of these faces out along its thin side (FLAT_FRONT_DEG).
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
# ForgeCAD's SimReady assets (huggingface.co/datasets/ForgeCAD/simready-assets) face -Y: a dining chair's and an
# office chair's backrest, a toilet's tank are at +Y and a kitchen base cabinet's countertop overhangs at -Y
# (a classroom's dining chairs stood turned 90 degrees from their tables).
FORGECAD_FRONT_DEG = 270.0
# A thing hung on a wall (layout.wall_height, or a card's mount "wall") whose front is not recorded and whose
# footprint is thin along Y (short side under FLAT_RATIO of the long side) faces -Y: the default +X stood a framed
# picture (61 x 6 cm) at right angles to the wall. Which of the two faces is the picture is not known;
# front_offset_deg 180 turns it.
FLAT_RATIO = 0.3
FLAT_FRONT_DEG = 270.0


def _forgecad(a: AssetRecord) -> bool:
    return (a.author or "").strip().lower() == "forgecad" or "/forgecad/" in a.uri.lower()


def _stem(uri: str) -> str:
    name = PurePosixPath(uri.split("?", 1)[0]).name
    return re.sub(r"\.usd[acz]?$", "", name, flags=re.IGNORECASE)


def asset_front_deg(a: AssetRecord) -> float:
    """Front direction in the asset's own Z-up footprint frame, before any per-object offset."""
    f = _recorded_front(a)
    return DEFAULT_FRONT_DEG if f is None else f


def _recorded_front(a: AssetRecord) -> float | None:
    """The front from the record, KNOWN_FRONTS, a card or ForgeCAD; None: nothing says."""
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
    if _forgecad(a):
        return FORGECAD_FRONT_DEG
    return None


def _hung(a: AssetRecord, o: ObjectSpec) -> bool:
    """On a wall: layout.wall_height above the floor, a card's mount "wall", or moved by hand (no layout) and left
    above the floor, as service._floor_furniture tells (move_object drops the layout: the front must not change)."""
    if o.layout is not None:
        if (o.layout.wall_height or 0) > 0.05:
            return True
    elif (o.z or 0) > 0.05:
        return True
    return (cards.of(a) or {}).get("mount") == "wall"


def flat_front(a: AssetRecord, o: ObjectSpec) -> bool:
    """A thing hung on a wall, thin along its own Y, without a recorded front."""
    w, d, _ = a.size_m
    return d < FLAT_RATIO * w and _recorded_front(a) is None and _hung(a, o)


def front_local_deg(a: AssetRecord, o: ObjectSpec) -> float:
    return (FLAT_FRONT_DEG if flat_front(a, o) else asset_front_deg(a)) + o.front_offset_deg


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
