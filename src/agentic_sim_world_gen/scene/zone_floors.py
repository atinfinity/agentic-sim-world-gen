"""Floors per plan zone (和室 = tatami, 洗面所 = tiles, ...). Data only (no pxr), shared by spec, preview, USD.

A zone's floor comes from, in order:
    Room.zone_floors[zone id]   scene override (set_room(zone_floors=...)), survives re-importing the plan file
    Zone.floor                  written in the plan JSON
    the zone's name / id        INFER: 和室 / 畳 / tatami -> tatami; 洗面 / 脱衣 / 浴室 / 風呂 / トイレ / 玄関 / 土間 /
                                bath / toilet / wc / lavatory / entrance ... -> tile_white
    Room.floor                  everything else
The value "room" means Room.floor (turns the guess off). Each value is a surface like Room.floor (preset name or
Surface). A zone whose floor resolves to the same look as Room.floor gets no piece of its own.
"""

from __future__ import annotations

import re
from typing import Any

from agentic_sim_world_gen.scene.looks import check_surface, resolve_surface

ROOM = "room"  # zone floor value: same as Room.floor (no guess from the name)

# (preset, words): Japanese words match anywhere in the name, ASCII words at the start of a word
INFER: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("tatami", ("和室", "畳", "tatami", "washitsu", "japanese")),
    ("tile_white", ("洗面", "脱衣", "浴室", "風呂", "バス", "トイレ", "便所", "玄関", "土間", "ランドリー",
                    "bath", "shower", "toilet", "wc", "lavatory", "washroom", "restroom", "powder", "laundry",
                    "entrance", "entry", "genkan", "foyer")),
)


def infer(*names: str | None) -> str | None:
    """The floor preset a zone's name suggests, or None."""
    text = " ".join(n for n in names if n).lower()
    tokens = re.findall(r"[a-z0-9]+", text)
    for preset, words in INFER:
        for w in words:
            if (w.isascii() and any(t.startswith(w) for t in tokens)) or (not w.isascii() and w in text):
                return preset
    return None


def zone_floor_value(room: Any, zone: Any) -> tuple[Any, str]:
    """(surface value or None = Room.floor, how: "scene" / "plan" / "name" / "room")."""
    v = (getattr(room, "zone_floors", None) or {}).get(zone.id)
    if v is not None:
        return (None, "room") if v == ROOM else (v, "scene")
    v = getattr(zone, "floor", None)
    if v is not None:
        return (None, "room") if v == ROOM else (v, "plan")
    if (p := infer(zone.name, zone.id)) is not None:
        return p, "name"
    return None, "room"


def zone_floors(room: Any) -> list[dict[str, Any]]:
    """Zones whose floor differs from Room.floor: [{id, name, polygon, value, surface (resolved), how}]."""
    if room.outdoor or room.plan is None:
        return []
    base = resolve_surface(room.floor, "wood_light")
    out = []
    for z in room.plan.zones:
        v, how = zone_floor_value(room, z)
        if v is None:
            continue
        s = resolve_surface(v, "wood_light")
        if s == base:
            continue
        out.append({"id": z.id, "name": z.name or z.id, "polygon": [tuple(p) for p in z.polygon],
                    "value": v, "surface": s, "how": how})
    return out


def check_value(v: Any) -> Any:
    """Validate one zone floor value (raises ValueError on an unknown preset)."""
    if v is not None and v != ROOM:
        check_surface(v, "wood_light")
    return v
