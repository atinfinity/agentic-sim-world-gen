"""Raised floors, steps / stairs and railings indoors. Pure Python (no pxr).

A level (段差・小上がり・ステージ・一段高い売り場) is a solid slab from the floor up to `height` over a rect or
polygon. Objects whose center is over it stand on it (the solver sets z, like an outdoor curb); one that
straddles its edge is an issue (on_step_edge). Stairs are a flight of solid steps going up toward `up`: onto a
level (`level` + `edge`, the steps stand outside it) or by themselves (`at` = the middle of the bottom step's
front edge; a stair to an upper floor). Railings are bars along a level's edges or a flight's sides.

    "levels": [{"id": "back", "rect": [0, 6, 4, 12], "height": 0.45, "rail": ["south"]}],
    "stairs": [{"id": "up_back", "level": "back", "edge": "south", "offset": 3.0, "width": 1.2, "rail": "left"},
               {"id": "to_2f", "at": [2, 11], "up": "north", "rise": 3.0, "width": 1.0, "color": "#f2f2f2"}]

Geometry comes out as boxes / bars in room coordinates (meters, z up) for compose, the 2D preview, the
obstacles and the nav map.
"""

from __future__ import annotations

import math
import re
from types import SimpleNamespace
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from agentic_sim_world_gen.scene.looks import Surface
from agentic_sim_world_gen.scene.site import Pt, _outline, ccw

RISER = 0.18  # [m] a step's rise when count is not given (at most this)
RAIL_H = 0.9  # [m] handrail top above the floor / step nosings
BAR = 0.04  # [m] handrail / post thickness
POST_EVERY = 1.5  # [m] posts at least this often along a level's railing
COMPASS = {"east": 0.0, "north": 90.0, "west": 180.0, "south": 270.0}
_ALIAS = {"e": "east", "n": "north", "w": "west", "s": "south"}


def _side(v):
    return _ALIAS.get(v.lower(), v.lower()) if isinstance(v, str) else v


def _color_ok(c: str | None) -> None:
    if c is not None and not re.fullmatch(r"#[0-9a-fA-F]{6}", c):
        raise ValueError("color must be #rrggbb")


class Level(BaseModel):
    """A raised part of the floor: a slab from z=0 up to `height`."""

    id: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    name: str | None = Field(default=None, description="display name, e.g. 奥の売り場")
    rect: tuple[float, float, float, float] | None = Field(default=None, description="[x0, y0, x1, y1]")
    polygon: list[Pt] | None = Field(default=None, min_length=3, description="outline instead of rect")
    height: float = Field(gt=0, le=2.0, description="its top above the floor [m] (a step 0.15-0.2, 3 steps ~0.5)")
    floor: str | Surface | None = Field(default=None, description="its floor surface; default the room's floor")
    color: str | None = Field(default=None, description="#rrggbb, shorthand for a painted floor")
    rail: list[str] = Field(default_factory=list,
                            description="edges with a railing: south / north / west / east (rect) or edge numbers "
                                        "'0', '1', ... (polygon, from its first point); stairs onto it cut a gap")

    @field_validator("rail", mode="before")
    @classmethod
    def _rail(cls, v):
        if isinstance(v, (str, int)):
            v = [v]
        return [_side(str(e)) for e in v] if v is not None else []

    @model_validator(mode="after")
    def _check(self) -> "Level":
        _outline(self.polygon, self.rect, f"level {self.id}")
        _color_ok(self.color)
        n = len(self.poly)
        for e in self.rail:
            if self.rect is not None and e not in COMPASS:
                raise ValueError(f"level {self.id}: rail edge '{e}' is not south / north / west / east")
            if self.rect is None and not (e.isdigit() and int(e) < n):
                raise ValueError(f"level {self.id}: rail edge '{e}' is not an edge number 0..{n - 1}")
        return self

    @property
    def poly(self) -> list[Pt]:
        return ccw(_outline(self.polygon, self.rect, self.id))

    def edge(self, name: str) -> tuple[Pt, Pt]:
        """The edge's two ends, counter-clockwise (the level on the left when walking from the first)."""
        if self.rect is not None:
            x0, y0, x1, y1 = (min(self.rect[0], self.rect[2]), min(self.rect[1], self.rect[3]),
                              max(self.rect[0], self.rect[2]), max(self.rect[1], self.rect[3]))
            return {"south": ((x0, y0), (x1, y0)), "east": ((x1, y0), (x1, y1)),
                    "north": ((x1, y1), (x0, y1)), "west": ((x0, y1), (x0, y0))}[name]
        poly = _outline(self.polygon, None, self.id)
        k = int(name)
        p, q = poly[k], poly[(k + 1) % len(poly)]
        return (p, q) if ccw(poly) == list(poly) else (q, p)


class Stairs(BaseModel):
    """A flight of solid steps going up toward `up`."""

    id: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    level: str | None = Field(default=None, description="goes up onto this level (rise = its height)")
    edge: str | None = Field(default=None, description="with level: the edge it climbs (south / north / west / east "
                                                       "of a rect level, or an edge number); the steps stand outside")
    offset: float | None = Field(default=None, description="with level + edge: middle of the flight along the edge "
                                                           "from its start (west / south end) [m]; default the middle")
    at: Pt | None = Field(default=None, description="by itself: the middle of the bottom step's front edge [x, y]")
    up: str | float | None = Field(default=None, description="the way you face going up: south / north / west / east "
                                                             "or degrees (0 = east, 90 = north); with edge it is into the level")
    width: float = Field(default=1.0, gt=0.3, le=6.0)
    rise: float | None = Field(default=None, gt=0, le=6.0, description="total height [m]; default the level's height "
                                                                      "(by itself: the room's height)")
    count: int | None = Field(default=None, ge=1, le=40, description="number of risers; default rise / 0.18 rounded up")
    going: float = Field(default=0.28, gt=0.1, le=1.0, description="tread depth [m]")
    rail: Literal["none", "left", "right", "both"] = Field(default="none", description="handrail side(s), seen going up")
    surface: str | Surface | None = Field(default=None, description="default the room's floor (or the level's)")
    color: str | None = Field(default=None, description="#rrggbb, shorthand for a painted surface (white stairs: #f2f2f2)")

    @field_validator("edge", "up", mode="before")
    @classmethod
    def _sides(cls, v):
        return _side(str(v)) if isinstance(v, (str, int)) and not isinstance(v, bool) and not isinstance(v, float) else v

    @model_validator(mode="after")
    def _check(self) -> "Stairs":
        _color_ok(self.color)
        if self.level is None and (self.at is None or self.up is None):
            raise ValueError(f"stairs {self.id}: give level + edge, or at + up")
        if self.level is not None and self.at is None and self.edge is None:
            raise ValueError(f"stairs {self.id}: give the level's edge it climbs (or at + up)")
        if isinstance(self.up, str) and self.up not in COMPASS:
            try:
                self.up = float(self.up)
            except ValueError:
                raise ValueError(f"stairs {self.id}: up must be south / north / west / east or degrees") from None
        return self


def up_deg(up: str | float) -> float:
    return COMPASS[up] if isinstance(up, str) else float(up)


def check(levels: list[Level], stairs: list[Stairs], width: float, depth: float, height: float = 3.0) -> None:
    """Ids unique, levels inside the room, stairs onto known levels / edges and inside the room."""
    ids = [lv.id for lv in levels] + [s.id for s in stairs]
    if dup := sorted({i for i in ids if ids.count(i) > 1}):
        raise ValueError(f"duplicate level / stairs ids: {dup}")
    for lv in levels:
        for x, y in lv.poly:
            if not (-1e-6 <= x <= width + 1e-6 and -1e-6 <= y <= depth + 1e-6):
                raise ValueError(f"level {lv.id} goes outside the room ({x:g}, {y:g}) vs {width:g} x {depth:g}")
    by = {lv.id: lv for lv in levels}
    for s in stairs:
        if s.level is None:
            continue
        if s.level not in by:
            raise ValueError(f"stairs {s.id}: no level '{s.level}'; levels: {sorted(by)}")
        lv = by[s.level]
        if s.edge is not None:
            try:
                lv.edge(s.edge)
            except (KeyError, ValueError, IndexError):
                raise ValueError(f"stairs {s.id}: level {lv.id} has no edge '{s.edge}'") from None
    room = SimpleNamespace(levels=levels, height=height)
    for s in stairs:
        f = flight(room, s)
        out = [(x, y) for x, y in footprint(f, s.width) if not (-1e-6 <= x <= width + 1e-6 and -1e-6 <= y <= depth + 1e-6)]
        if out:
            raise ValueError(f"stairs {s.id} ({f['treads']} steps, {f['run']:.2f} m long) go outside the room at "
                             f"({out[0][0]:.2f}, {out[0][1]:.2f}) vs {width:g} x {depth:g}: move its foot (at / offset) or turn it (up)")


def _level(room, s: Stairs) -> Level | None:
    return next((lv for lv in room.levels if lv.id == s.level), None) if s.level else None


def _inside(pt: Pt, poly: list[Pt]) -> bool:
    x, y = pt
    inside = False
    for i in range(len(poly)):
        (x1, y1), (x2, y2) = poly[i], poly[i - 1]
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def flight(room, s: Stairs) -> dict:
    """Where a flight stands: foot (middle of the bottom step's front), up (unit vector), rise, count, run, and
    the steps as boxes {id, center, size (along up, across, height), yaw, top}. base: the floor height at the foot
    (a freestanding flight on a level starts on its top)."""
    lv = _level(room, s)
    rise = s.rise if s.rise is not None else lv.height if lv else room.height
    count = s.count or max(1, math.ceil(rise / RISER - 1e-9))
    onto = lv is not None and s.at is None  # the last riser is the level's edge: count - 1 treads outside it
    base = 0.0
    treads = count - 1 if onto else count
    if onto:
        p, q = lv.edge(s.edge)
        L = math.dist(p, q)
        ux, uy = (q[0] - p[0]) / L, (q[1] - p[1]) / L
        t = L / 2 if s.offset is None else s.offset
        if lv.rect is not None and s.edge in ("north", "west"):  # offsets run west -> east / south -> north
            t = L - t
        ex, ey = p[0] + ux * t, p[1] + uy * t
        nx, ny = -uy, ux  # into the level (counter-clockwise edge: the level is on the left)
        foot = (ex - nx * treads * s.going, ey - ny * treads * s.going)
        deg = math.degrees(math.atan2(ny, nx))
    else:
        deg = up_deg(s.up) if s.up is not None else 90.0
        nx, ny = math.cos(math.radians(deg)), math.sin(math.radians(deg))
        foot = s.at
        # standing on a level (stairs up from a raised back room): they start on its top
        base = max((v.height for v in room.levels if _inside(foot, v.poly)), default=0.0)
    steps = []
    for k in range(treads):
        top = rise * (k + 1) / count
        d = (k + 0.5) * s.going
        steps.append({"id": f"Step_{k:02d}", "center": (foot[0] + nx * d, foot[1] + ny * d, base + top / 2),
                      "size": (s.going, s.width, top), "yaw": deg, "top": base + top})
    return {"foot": foot, "up": (nx, ny), "deg": deg, "base": base, "rise": rise, "count": count, "treads": treads,
            "run": treads * s.going, "onto": onto, "steps": steps}


def footprint(f: dict, width: float) -> list[Pt]:
    """The flight's outline on the floor."""
    (fx, fy), (nx, ny) = f["foot"], f["up"]
    ax, ay = -ny * width / 2, nx * width / 2
    rx, ry = nx * f["run"], ny * f["run"]
    return [(fx - ax, fy - ay), (fx - ax + rx, fy - ay + ry), (fx + ax + rx, fy + ay + ry), (fx + ax, fy + ay)]


def rails(room) -> list[dict]:
    """Handrail bars and posts: {id, owner (level / stairs id), a (x, y, z), b (x, y, z)} (bar from a to b)."""
    out: list[dict] = []
    gaps: dict[tuple[str, str], list[tuple[float, float]]] = {}
    for s in room.stairs:
        lv = _level(room, s)
        if lv is not None and s.at is None:
            p, q = lv.edge(s.edge)
            f = flight(room, s)
            L = math.dist(p, q)
            ux, uy = (q[0] - p[0]) / L, (q[1] - p[1]) / L
            t = (f["foot"][0] - p[0]) * ux + (f["foot"][1] - p[1]) * uy
            gaps.setdefault((lv.id, s.edge), []).append((t - s.width / 2, t + s.width / 2))
    for lv in room.levels:
        for e in lv.rail:
            p, q = lv.edge(e)
            L = math.dist(p, q)
            ux, uy = (q[0] - p[0]) / L, (q[1] - p[1]) / L
            ins = BAR / 2  # just inside the edge
            nx, ny = -uy * ins, ux * ins
            runs = _cut((0.0, L), gaps.get((lv.id, e), []))
            for i, (t0, t1) in enumerate(runs):
                a = (p[0] + ux * t0 + nx, p[1] + uy * t0 + ny)
                b = (p[0] + ux * t1 + nx, p[1] + uy * t1 + ny)
                z = lv.height + RAIL_H
                rid = f"{lv.id}_{e}_{i}"
                out.append({"id": f"{rid}_bar", "owner": lv.id, "a": (*a, z), "b": (*b, z)})
                n = max(1, math.ceil((t1 - t0) / POST_EVERY))
                for k in range(n + 1):
                    c = (a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n)
                    out.append({"id": f"{rid}_post{k}", "owner": lv.id, "a": (*c, lv.height), "b": (*c, z)})
    for s in room.stairs:
        if s.rail == "none":
            continue
        f = flight(room, s)
        (fx, fy), (nx, ny) = f["foot"], f["up"]
        sides = {"left": [1.0], "right": [-1.0], "both": [1.0, -1.0]}[s.rail]
        step_h = f["rise"] / f["count"]
        z0 = f["base"]
        for sg in sides:
            off = sg * (s.width / 2 - BAR / 2)
            bx, by = fx - ny * off, fy + nx * off
            # along the nosings: first tread's top at its front to the last tread's top at its front (+1 going)
            a = (bx, by, z0 + step_h + RAIL_H)
            end = f["run"] if f["onto"] else f["run"] - s.going
            top = (f["treads"] + (1 if f["onto"] else 0)) * step_h
            b = (bx + nx * end, by + ny * end, z0 + top + RAIL_H)
            if end <= 1e-6:  # a single step: a short level bar
                b = (bx + nx * s.going, by + ny * s.going, a[2])
            name = f"{s.id}_{'left' if sg > 0 else 'right'}"
            out.append({"id": f"{name}_bar", "owner": s.id, "a": a, "b": b})
            out.append({"id": f"{name}_post0", "owner": s.id, "a": (a[0], a[1], z0 + step_h), "b": a})
            out.append({"id": f"{name}_post1", "owner": s.id, "a": (b[0], b[1], b[2] - RAIL_H), "b": b})
    return out


def _cut(run: tuple[float, float], gaps: list[tuple[float, float]], min_len: float = 0.1) -> list[tuple[float, float]]:
    out = [run]
    for g0, g1 in gaps:
        nxt = []
        for a, b in out:
            if g1 <= a or g0 >= b:
                nxt.append((a, b))
                continue
            if g0 - a >= min_len:
                nxt.append((a, g0))
            if b - g1 >= min_len:
                nxt.append((g1, b))
        out = nxt
    return out


def rail_rect(r: dict, pad: float = BAR / 2) -> list[Pt]:
    """A bar's footprint (a thin rectangle; a post is a small square)."""
    (ax, ay, _), (bx, by, _) = r["a"], r["b"]
    L = math.dist((ax, ay), (bx, by))
    if L < 1e-6:
        return [(ax - pad, ay - pad), (ax + pad, ay - pad), (ax + pad, ay + pad), (ax - pad, ay + pad)]
    ux, uy = (bx - ax) / L, (by - ay) / L
    nx, ny = -uy * pad, ux * pad
    return [(ax - ux * pad - nx, ay - uy * pad - ny), (bx + ux * pad - nx, by + uy * pad - ny),
            (bx + ux * pad + nx, by + uy * pad + ny), (ax - ux * pad + nx, ay - uy * pad + ny)]


def describe(room) -> str | None:
    """Short Japanese summary for the change history: 段差 奥 0.45 m・階段 2（手すり 1）."""
    parts = []
    for lv in room.levels:
        parts.append(f"段差 {lv.name or lv.id} {lv.height:g} m" + ("・手すり" if lv.rail else ""))
    for s in room.stairs:
        f = flight(room, s)
        where = f"→ {s.level}" if s.level else f"{f['rise']:g} m"
        parts.append(f"階段 {s.id} {f['count']} 段 {where}" + ("・手すり" if s.rail != "none" else ""))
    return "、".join(parts) if parts else None
