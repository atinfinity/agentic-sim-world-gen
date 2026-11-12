"""Assumptions that cannot all hold: a room too small for what goes in it, a robot wider than the aisles,
fewer seats than people, a fixture taller than the ceiling. Found from the room and the chosen assets' sizes,
without the layout: the solver would report the same things one object at a time (no_spot, narrow_aisle) after
the scene is built, and the user would have to start over. One question is asked (which side wins); when the user
leaves it to us, the roomier side wins and is reported."""

from __future__ import annotations

import math
import re
import unicodedata
from typing import Any

from agentic_sim_world_gen.scene.spec import ResolvedScene

FLOOR_FULL = 0.5  # fixtures on more than this share of the floor: no way to walk between them
FLOOR_TARGET = 0.35  # ... the room is sized for this share when the user leaves it to us
FLAT = 0.1  # lower than this is walked on (rugs, mats): not floor taken
SEAT_WIDTH = 0.6  # one person on a sofa / bench [m]
CEILING_GAP = 0.1  # above the tallest fixture when the ceiling is raised [m]
ROBOT_SIDE = 0.2  # free width on each side of the robot [m] (Purpose.aisle_for)
ROOM_STEP = 0.1  # room sizes are suggested in this step [m]
ODD_SHARE = 0.1  # one unit of a kind with no typical size taking more of the floor is a wrong asset, not a request
ORDER = ("floor", "height", "aisle", "seats")  # which one to ask about first: the room decides the rest

SEATS = {"chair", "stool", "seat", "armchair"}
LONG_SEATS = {"sofa", "couch", "loveseat", "bench"}
PEOPLE_ITEMS = ("people", "seats", "人数", "席数", "席")
AISLE_ITEMS = ("aisle", "通路")
_KANJI = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _number(text: str) -> float | None:
    t = unicodedata.normalize("NFKC", text or "")
    m = re.search(r"\d+(?:\.\d+)?", t)
    if m:
        return float(m.group(0))
    for ch in t:
        if ch in _KANJI:
            return float(_KANJI[ch])
    return None


def _stated(scene: ResolvedScene, items: tuple[str, ...]) -> tuple[float, Any] | None:
    """(number, the assumption row) of the first room-level row about one of these items."""
    for a in scene.spec.assumptions:
        item = a.item.strip().lower()
        if a.object is None and any(item == k or k in item for k in items):
            n = _number(a.value)
            if n is not None:
                return n, a
    return None


def _floor_units(scene: ResolvedScene) -> list[tuple[Any, Any, int]]:
    """(object, asset, units) for what stands on the floor: not products on a shelf, things on a table, on the
    wall or the ceiling, door / window assets, curtains."""
    out = []
    for p in scene.placements:
        o = scene.obj(p.object_id)
        lay = o.layout
        if (p.asset is None and o.min_size is None) or o.fill is not None or o.opening is not None or o.covers:
            continue
        if lay is not None and (lay.on or lay.ceiling or lay.wall_height is not None):
            continue
        if p.asset is not None and not _asked_size(scene, p, o):
            continue
        out.append((o, p.asset, o.array.count * o.array.rows if o.array else 1))
    return out


def _asked_size(scene: ResolvedScene, p: Any, o: Any) -> bool:
    """Whether the asset's size is what the request means, not an accident of the search. With real assets the
    examples came out with a 6.0 x 3.6 m light panel for 「wall poster」 and an 8.5 m tree island for a bookcase
    island: 72-273% of the floor, and the question 「部屋を広げる / 物を減らす」 would have been the wrong one -
    those are assets to swap (the layout issues and the candidates say so). Counted: a size the spec limits
    (min_size / max_size), else a proper match (not a substitute) inside the typical range of its kind
    (agentic_sim_world_gen.assets.sizes); of a kind with no typical range, one unit up to ODD_SHARE of the floor that fits the
    room's sides."""
    from agentic_sim_world_gen.assets import sizes
    from agentic_sim_world_gen.layout.array import placed_size

    if o.min_size is not None or o.max_size is not None:
        return True
    if p.substitute:
        return False
    kind = sizes.kind_of(o.query, o.alt_queries)
    if kind is not None:
        return sizes.off_size(p.asset, kind) is None
    room = scene.spec.room
    if not room.width or not room.depth:
        return True
    w, d, _ = placed_size(o, p.asset)
    return (w * d <= ODD_SHARE * room.width * room.depth and max(w, d) <= max(room.width, room.depth)
            and min(w, d) <= min(room.width, room.depth))


def _size(o: Any, a: Any) -> tuple[float, float, float]:
    """The object's size as placed; with no asset (none is as large as asked), the smallest size asked for."""
    from agentic_sim_world_gen.layout.array import placed_size

    return placed_size(o, a) if a is not None else tuple(o.min_size)


def scene_asset(scene: ResolvedScene, o: Any) -> Any:
    return next((p.asset for p in scene.placements if p.object_id == o.id), None)


def _up(v: float, step: float = ROOM_STEP) -> float:
    return round(math.ceil(v / step - 1e-9) * step, 2)


def _name(o: Any) -> str:
    return o.label or o.id


def _floor(scene: ResolvedScene, units: list) -> dict[str, Any] | None:
    room = scene.spec.room
    if not room.width or not room.depth or scene.spec.room.plan is not None:
        return None  # a floor plan's rooms are not the bounding box; the solver reports those
    area = room.width * room.depth
    long_side, short_side = max(room.width, room.depth), min(room.width, room.depth)
    taken, biggest = 0.0, None
    for o, a, n in units:
        w, d, h = _size(o, a)
        if h < FLAT:
            continue
        taken += w * d * n
        if max(w, d) > long_side or min(w, d) > short_side:
            biggest = biggest or (o, max(w, d), min(w, d))
    if biggest is None and taken <= FLOOR_FULL * area:
        return None
    k = max(1.0, math.sqrt(taken / FLOOR_TARGET / area))
    width, depth = room.width * k, room.depth * k
    if biggest is not None:  # and it has to go in with a way past it
        o, lo, sh = biggest
        need_long, need_short = lo + 1.0, sh + 1.0
        if room.width >= room.depth:
            width, depth = max(width, need_long), max(depth, need_short)
        else:
            width, depth = max(width, need_short), max(depth, need_long)
    width, depth = _up(width), _up(depth)
    size = f"{room.width:g} × {room.depth:g} m"
    if biggest is not None:
        o, lo, sh = biggest
        text = f"{_name(o)}（{lo:.2f} × {sh:.2f} m{'' if scene_asset(scene, o) else ' 以上という指定'}）が部屋（{size}）に入りません"
    else:
        text = (f"床に置く物の合計が {taken:.1f} m² で、部屋（{size} = {area:.1f} m²）の {100 * taken / area:.0f}% を占めます"
                f"（歩ける場所が残るのは {100 * FLOOR_FULL:.0f}% まで）")
    return {"kind": "floor", "text": text, "between": ["room size", "objects"],
            "options": [f"部屋を広げる（{width:g} × {depth:g} m）", "物を減らす・小さい物に替える"],
            "left_to_us": f"部屋を {width:g} × {depth:g} m に広げる",
            "fix": {"room": {"width": width, "depth": depth}},
            "numbers": {"floor_m2": round(area, 2), "taken_m2": round(taken, 2), "share": round(taken / area, 2)}}


def _height(scene: ResolvedScene, units: list) -> dict[str, Any] | None:
    room = scene.spec.room
    if room.place == "outdoor":
        return None
    tall = [(_size(o, a)[2], o) for o, a, _ in units]  # its own height: z is the level it stands on
    tall = sorted(((h, o) for h, o in tall if h > room.height), key=lambda t: -t[0])
    if not tall:
        return None
    top, o = tall[0]
    new = _up(top + CEILING_GAP)
    names = "、".join(f"{_name(x)}（{h:.2f} m{'' if scene_asset(scene, x) else ' 以上という指定'}）" for h, x in tall[:3]) + (f" ほか {len(tall) - 3} 個" if len(tall) > 3 else "")
    return {"kind": "height", "text": f"{names}が天井（{room.height:g} m）より高い", "between": ["ceiling height", "objects"],
            "options": [f"天井を {new:g} m に上げる", "低い物に替える"], "left_to_us": f"天井を {new:g} m に上げる",
            "fix": {"room": {"height": new}}, "numbers": {"ceiling_m": room.height, "tallest_m": round(top, 2)},
            "objects": [x.id for _, x in tall]}


def _aisle(scene: ResolvedScene, units: list) -> dict[str, Any] | None:
    p = scene.spec.purpose
    if p is None or not p.aisle:
        return None
    need = p.aisle
    narrow = [(o.array.aisle, _name(o) + "の列の間", o.id) for o, _, _ in units
              if o.array is not None and o.array.rows > 1 and o.array.aisle < need - 1e-6]
    said = _stated(scene, AISLE_ITEMS)
    if said is not None and said[0] < need - 1e-6 and not (said[1].basis or "").startswith("用途から"):
        narrow.append((said[0], f"仮定「{said[1].item}」", None))
    if not narrow:
        return None
    narrow.sort(key=lambda t: t[0])
    w = narrow[0][0]
    radius = round(max(0.0, (w - 2 * ROBOT_SIDE) / 2), 2)
    where = "、".join(f"{n}（{x:g} m）" for x, n, _ in narrow[:3])
    options = [f"通路を {need:g} m に広げる"]
    if radius >= 0.1:
        options.append(f"ロボットを小さくする（半径 {radius:g} m 以下）")
    else:
        options.append("ロボットを走らせない（用途を変える）")
    return {"kind": "aisle",
            "text": f"ロボット（半径 {p.robot_radius:g} m）には幅 {need:g} m の通路が要りますが、{where}です",
            "between": ["purpose", "aisle"], "options": options, "left_to_us": f"通路を {need:g} m に広げる",
            "fix": {"aisle": need}, "numbers": {"need_m": need, "narrowest_m": w},
            "objects": [i for _, _, i in narrow if i]}


def seats(scene: ResolvedScene, units: list | None = None) -> int:
    """People the chairs, stools, sofas and benches seat (a sofa: one per 0.6 m of its width)."""
    from agentic_sim_world_gen.layout.array import unit_size
    from agentic_sim_world_gen.layout.usability import heads

    n = 0
    for o, a, k in units if units is not None else _floor_units(scene):
        if a is None:
            continue
        h = heads(o, a)
        if h & LONG_SEATS:
            n += k * max(1, int(unit_size(o, a)[0] / SEAT_WIDTH + 1e-6))
        elif h & SEATS:
            n += k
    return n


def _seats(scene: ResolvedScene, units: list) -> dict[str, Any] | None:
    said = _stated(scene, PEOPLE_ITEMS)
    if said is None:
        return None
    people, row = int(said[0]), said[1]
    have = seats(scene, units)
    if have == 0 or have >= people:  # no seat at all: not a room people sit in (a shop, a bedroom)
        return None
    what = {"people": "人数", "seats": "席数"}.get(row.item.strip().lower(), row.item)
    return {"kind": "seats", "text": f"{what}は {row.value} ですが、座れるのは {have} 人分です",
            "between": [row.item, "seats"], "options": [f"席を {people} 人分に増やす", f"{have} 人分のままにする"],
            "left_to_us": f"席を {people} 人分に増やす", "fix": {"seats": people},
            "numbers": {"people": people, "seats": have}}


def find(scene: ResolvedScene) -> list[dict[str, Any]]:
    """The conflicts, the one to ask about first (ORDER)."""
    units = _floor_units(scene)
    found = [c for c in (_floor(scene, units), _height(scene, units), _aisle(scene, units), _seats(scene, units)) if c]
    return sorted(found, key=lambda c: ORDER.index(c["kind"]))


def report(scene: ResolvedScene) -> dict[str, Any] | None:
    """What to tell the agent: every conflict, and the one question to ask (None when nothing conflicts)."""
    found = find(scene)
    if not found:
        return None
    first = found[0]
    return {"found": found,
            "ask": {"question": f"{first['text']}。どちらを優先しますか？", "options": [*first["options"], "おまかせ"]},
            "next": "ask this ONE question before building further (the first conflict; fixing it often settles the "
                    "others). おまかせ / no answer wanted: apply each `left_to_us` (the roomier side) and tell the user "
                    "what you changed. Then record the decision (set_assumptions given=true) and finish(draft=true)."}
