"""Usability checks: layouts that fit but are awkward to use. Warnings only, separate from `check()`.

- narrow_aisle / unreachable: walking room from the doors to what people use (shelves, cases, registers) and to
  every zone, on a floor grid (objects and walls blocked; a person needs `aisle_width(place)`, or squeezes
  through `SQUEEZE` at least).
- no_front_space: no room to stand in front of a shelf, case or register.
- chair_facing / chair_far: a chair near a desk or table that does not face it, or sits too far from it.
- tv_facing / tv_distance: a TV and the sofa not facing each other, or too near / far for the screen.
- register_hidden: in a shop, the register cannot be seen from the entrance (tall fixtures in the line of sight).
- blocks_window: tall furniture standing in front of a window.
- empty_floor / wide_aisle (shops): a patch of sales floor `EMPTY_SPAN` m (`EMPTY_OVER`) or more across with no fixture, or
  shelves facing each other across more than `WIDE_AISLE` times the place's aisle width (`WIDE_OVER` for stores
  with cart / forklift main aisles): the shop looks unfinished.
- empty_shelves: in a shop, a fixture whose shelf levels mostly hold no products (fill).

Pure Python (no numpy): the grid cell grows with the room so it stays near `MAX_CELLS` cells.
"""

from __future__ import annotations

import math
import re
from collections import deque
from typing import TYPE_CHECKING, Any

from agentic_sim_world_gen.layout.validate import SHOP_PLACES, Issue, _inside, footprint, overlap_depth, zone_of
from agentic_sim_world_gen.layout.widths import INF, narrowest, ridge, widest  # noqa: F401
from agentic_sim_world_gen.layout.widths import edt as _edt
from agentic_sim_world_gen.layout.widths import edt1d as _edt1d  # noqa: F401
from agentic_sim_world_gen.scene.spec import STORE_PLACES

if TYPE_CHECKING:
    from agentic_sim_world_gen.assets.record import AssetRecord
    from agentic_sim_world_gen.scene.spec import ObjectSpec, ResolvedScene

Pt = tuple[float, float]

# walking width [m] a place needs between objects (default DEFAULT_AISLE)
# (small shops such as a konbini have ~1 m aisles; a supermarket's carts need 1.2 m and more)
AISLE_WIDTH = {"home": 0.75, **{p: 1.0 for p in ("shop", *STORE_PLACES)},
               **{p: 1.2 for p in ("supermarket", "home_center", "garden_center", "sporting_goods_store",
                                         "furniture_store", "warehouse", "factory",
                                         "plant_factory")}}
DEFAULT_AISLE = 0.9
SQUEEZE = 0.6  # narrower than this, nobody gets through (sideways)
FRONT_DEPTH = 0.6  # standing room in front of a shelf / case / register
FRONT_FREE = 0.5  # ... at least this fraction of it free
BODY = (0.1, 1.2)  # height range a walking person sweeps: lower objects (mats) and higher (signs) are fine
MAX_CELLS = 40000
CELL_MIN = 0.05

# kinds by head noun (last word of the query or the asset class)
SEATS = {"chair", "stool", "seat"}
TABLES = {"desk", "table", "workbench"}
SIDE_TABLES = {"coffee", "side", "end", "bedside", "night", "nightstand", "tv", "console", "bar"}  # not a work/dining table
TVS = {"tv", "television"}
SOFAS = {"sofa", "couch", "loveseat"}
FRONTS = {"shelf", "shelve", "shelving", "bookcase", "bookshelf", "rack", "refrigerator", "fridge", "freezer",
          "cooler", "showcase", "multideck", "gondola", "pegboard", "register", "checkout", "counter", "vending", "machine", "locker",
          "wardrobe", "atm", "display"}
REGISTERS = {"register", "checkout", "cashier", "pos"}
MOVERS = {"forklift", "truck", "cart", "trolley", "jack", "car", "robot", "amr", "agv"}  # drive away: not in the way

CHAIR_NEAR = 1.0  # a chair this close to a table (gap) belongs to it
CHAIR_GAP = 0.5  # ... and should be at most this far from its edge
CHAIR_ANGLE = 60.0
TV_ANGLE = 30.0
SOFA_ANGLE = 45.0
TV_RANGE = (1.2, 6.0)  # viewing distance in screen heights (16:9: height = 0.5625 x width)
TV_MIN, TV_MAX = 1.0, 3.0  # ... but never asked nearer than TV_MIN, and up to TV_MAX is always fine
EYE = 1.5  # eye height at the entrance
EMPTY_SPAN = 3.0  # an empty shop floor patch this wide [m] (a circle with no fixture or wall) looks unfinished
EMPTY_SPOTS = 3  # report at most this many
EMPTY_OVER = {"home_center": 3.5, "garden_center": 3.5}  # ... wider for stores whose main aisles are 2.5–3 m
WIDE_AISLE = 2.0  # aisles between shelves wider than this x aisle_width(place) waste the floor ...
WIDE_OVER = {"supermarket": 2.5, "electronics_store": 2.5, "home_center": EMPTY_SPAN, "garden_center": EMPTY_SPAN}  # ... except these [m] (main aisles)
WINDOW_DEPTH = 0.35  # an object this close to a window's wall face stands in front of it
WINDOW_COVER = 0.3  # ... covering this fraction of its width
WINDOW_RISE = 0.3  # ... and rising this far above the sill


def aisle_width(place: str | None) -> float:
    return AISLE_WIDTH.get(place or "", DEFAULT_AISLE)


PHOTO_TOL = 0.2  # an aisle within this share of the width measured in the photo is the photo's


def photo_aisle(scene: ResolvedScene) -> tuple[float, float, float] | None:
    """(m, lo, hi) of the aisle measured in a photo: the scene's aisle is right between lo and hi - the
    measurement's own range, and never tighter than +-PHOTO_TOL."""
    t = scene.spec.photo_aisle
    if t is None:
        return None
    lo, hi = t.range_m or (t.m, t.m)
    return t.m, round(min(lo, t.m * (1 - PHOTO_TOL)), 2), round(max(hi, t.m * (1 + PHOTO_TOL)), 2)


def need_width(scene: ResolvedScene) -> float:
    """The walking width to keep: the place's, or the robot's when the scene is for a robot and that is wider.
    A photo that measured a tighter aisle than the place's usual lowers the place's to it (the real one is that
    tight), never below SQUEEZE."""
    w = aisle_width(scene.spec.room.place)
    if (t := photo_aisle(scene)) is not None and t[0] < w:
        w = max(SQUEEZE, t[0])
    p = scene.spec.purpose
    return max(w, p.aisle) if p is not None and p.aisle else w


def _sing(w: str) -> str:
    return w[:-1] if w.endswith("s") and not w.endswith("ss") and len(w) > 3 else w


def _words(text: str | None) -> list[str]:
    """Words with counts dropped and the object noun last ("sofa 3 seat" -> "seat sofa")."""
    from agentic_sim_world_gen.assets.rerank import noun_last

    return [_sing(w) for w in noun_last(re.findall(r"[a-z0-9]+", (text or "").lower())) if w.isalpha()]


def heads(o: ObjectSpec, a: AssetRecord | None) -> set[str]:
    """Head nouns: the last word of the query and of the asset class ("desk lamp" is a lamp, not a desk)."""
    out = set()
    for text in (o.query, a.semantic_class if a else None):
        w = _words(text)
        if w:
            out.add(w[-1])
    return out


def _mods(o: ObjectSpec) -> set[str]:
    return set(_words(o.query)[:-1])


class _Unit:
    """One placed footprint (an object, or one unit of an array)."""

    def __init__(self, inst, o: ObjectSpec, a: AssetRecord):
        from agentic_sim_world_gen.layout.array import placed_size
        from agentic_sim_world_gen.layout.frame import facing_of, half_extent

        self.id, self.o, self.a = inst.id, o, a
        self.pos, self.yaw, self.z = tuple(inst.position), inst.yaw_deg, inst.z
        w, d, h = placed_size(o, a)
        self.top = self.z + h
        self.poly = footprint(self.pos, (w, d), self.yaw)
        self.facing = facing_of(a, o) + (self.yaw - o.yaw_deg)
        self.half_depth = half_extent(a, self.yaw, self.facing)
        self.half_width = half_extent(a, self.yaw, self.facing + 90.0)
        self.heads = heads(o, a)

    def is_(self, kinds: set[str]) -> bool:
        return bool(self.heads & kinds)

    @property
    def floor(self) -> bool:
        return self.z < 0.3 and not (self.o.layout and (self.o.layout.ceiling or self.o.layout.on))


def _units(scene: ResolvedScene) -> list[_Unit]:
    from agentic_sim_world_gen.layout.array import instances

    out = []
    for p in scene.placements:
        o = scene.obj(p.object_id)
        if p.asset is None or o.position is None or o.fill is not None or o.opening is not None or o.covers:
            continue  # products sit inside their fixture; door / window assets fill their opening; curtains hang
        out += [_Unit(i, o, p.asset) for i in instances(o, p.asset)]
    return out


def _angle(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


def _dir(p: Pt, q: Pt) -> float:
    return math.degrees(math.atan2(q[1] - p[1], q[0] - p[0]))


def _closest(p: Pt, poly: list[Pt]) -> Pt:
    """Closest point of a convex polygon (its boundary or p itself if inside)."""
    if _inside(p, poly):
        return p
    best, bd = poly[0], math.inf
    for i in range(len(poly)):
        (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % len(poly)]
        dx, dy = x2 - x1, y2 - y1
        L = dx * dx + dy * dy
        t = 0.0 if L == 0 else max(0.0, min(1.0, ((p[0] - x1) * dx + (p[1] - y1) * dy) / L))
        c = (x1 + t * dx, y1 + t * dy)
        d = math.dist(p, c)
        if d < bd:
            best, bd = c, d
    return best


# --- chairs, TV ------------------------------------------------------------------------------------------


def _chairs(units: list[_Unit]) -> list[Issue]:
    from agentic_sim_world_gen.layout.frame import half_extent

    tables = [t for t in units if t.is_(TABLES) and not (_mods(t.o) & SIDE_TABLES) and t.floor]
    out = []
    for c in units:
        if not c.is_(SEATS) or not c.floor or not tables:
            continue
        lay = c.o.layout
        named = {lay.in_front_of, lay.facing if isinstance(lay.facing, str) else None} if lay else set()
        own = [t for t in tables if t.o.id in named]
        best = None
        for t in own or tables:
            cp = _closest(c.pos, t.poly)
            d = math.dist(c.pos, cp)
            gap = 0.0 if d < 1e-6 else d - half_extent(c.a, c.yaw, _dir(c.pos, cp))
            if best is None or gap < best[0]:
                best = (gap, t, cp, d)
        gap, t, cp, d = best
        if gap > CHAIR_NEAR and not own:
            continue  # a chair on its own
        if d > 1e-6 and (off := _angle(c.facing, _dir(c.pos, cp))) > CHAIR_ANGLE:
            out.append(Issue("chair_facing", (c.id, t.id), f"faces {off:.0f}° away from {t.id}; turn it to face it "
                             "(layout.facing / in_front_of, or move_object facing_deg)"))
        elif gap > CHAIR_GAP:
            out.append(Issue("chair_far", (c.id, t.id), f"{gap:.2f} m from {t.id} (> {CHAIR_GAP} m); "
                             "move it closer (layout.in_front_of)"))
    return out


def _tvs(units: list[_Unit]) -> list[Issue]:
    sofas = [s for s in units if s.is_(SOFAS) and s.floor]
    out = []
    for tv in units:
        if not tv.is_(TVS) or not sofas:
            continue
        # the sofa the TV is meant for: the best facing pair (armchairs at the side do not count)
        pairs = [(_angle(tv.facing, _dir(tv.pos, s.pos)), _angle(s.facing, _dir(s.pos, tv.pos)), s) for s in sofas]
        a1, a2, s = min(pairs, key=lambda p: (p[0] > TV_ANGLE or p[1] > SOFA_ANGLE, p[0] + p[1]))
        dist = math.dist(tv.pos, s.pos)
        if a1 > TV_ANGLE or a2 > SOFA_ANGLE:
            who = f"the TV points {a1:.0f}° off" if a1 > TV_ANGLE else f"the sofa points {a2:.0f}° off"
            out.append(Issue("tv_facing", (tv.id, s.id), f"{who}; face them to each other (layout.facing)"))
            continue
        h = 0.5625 * 2 * tv.half_width
        lo, hi = max(TV_MIN, TV_RANGE[0] * h), max(TV_MAX, TV_RANGE[1] * h)
        if not lo <= dist <= hi:
            out.append(Issue("tv_distance", (tv.id, s.id), f"{dist:.1f} m apart; a {2 * tv.half_width:.2f} m wide "
                             f"screen is best seen from {lo:.1f}–{hi:.1f} m"))
    return out


# --- windows, line of sight -------------------------------------------------------------------------------


def _windows(geom, units: list[_Unit]) -> list[Issue]:
    out = []
    for w in geom.walls:
        dx, dy = w.dir
        for op in w.openings:
            if op.kind != "window":
                continue
            u0, u1 = w.interval(op)
            sill, top = op.resolved(w.height)
            half = w.thickness / 2
            sides = [w.rect(u0, u1, half, half + WINDOW_DEPTH), w.rect(u0, u1, -half - WINDOW_DEPTH, -half)]
            for u in units:
                if u.top < sill + WINDOW_RISE or u.z > top or u.o.layout and u.o.layout.ceiling:
                    continue
                if not any(overlap_depth(u.poly, s) > 0.02 for s in sides):
                    continue
                us = [(x - w.a[0]) * dx + (y - w.a[1]) * dy for x, y in u.poly]
                cover = (min(max(us), u1) - max(min(us), u0)) / (u1 - u0)
                if cover >= WINDOW_COVER:
                    out.append(Issue("blocks_window", (u.id, op.id), f"{u.top:.2f} m tall in front of window "
                                     f"{op.id} (sill {sill:.2f} m), covering {cover:.0%} of it"))
    return out


def _entrances(geom) -> list[tuple[Pt, str]]:
    """Points 0.6 m inside each door / opening (auto-sliding doors only, if the room has one)."""
    ops = [(w, o) for w in geom.walls for o in w.openings if o.kind != "window"]
    auto = [(w, o) for w, o in ops if o.style == "auto_sliding"]
    out = []
    for w, o in auto or ops:
        u0, u1 = w.interval(o)
        for s in (1.0, -1.0):
            p = w.at((u0 + u1) / 2, s * (w.thickness / 2 + 0.6))
            if geom.inside([p], 0.0):
                out.append((p, o.id))
    return out


def _registers(scene: ResolvedScene, geom, units: list[_Unit]) -> list[Issue]:
    if scene.spec.room.place not in SHOP_PLACES:
        return []
    regs = [u for u in units if u.is_(REGISTERS)]
    ents = _entrances(geom)
    if not regs or not ents:
        return []
    rests = {o.id: (o.layout.on if o.layout else None) for o in scene.spec.objects}
    out = []
    for r in regs:
        family = {r.o.id, rests.get(r.o.id)} | {k for k, v in rests.items() if v == r.o.id}
        target_z = r.z + 0.8 * (r.top - r.z)
        best = None
        for e, oid in ents:
            n = max(2, int(math.dist(e, r.pos) / 0.05))
            hit = []
            for i in range(1, n):
                t = i / n
                p = (e[0] + t * (r.pos[0] - e[0]), e[1] + t * (r.pos[1] - e[1]))
                z = EYE + t * (target_z - EYE)
                for u in units:
                    if u.o.id in family or u.id in hit or not (u.z < z < u.top):
                        continue
                    if _inside(p, u.poly):
                        hit.append(u.id)
            if not hit:
                best = None
                break
            if best is None or len(hit) < len(best[1]):
                best = (oid, hit)
        if best:
            out.append(Issue("register_hidden", (r.id, *best[1][:3]), f"cannot be seen from entrance {best[0]}: "
                             f"{', '.join(best[1][:3])} in the way (at eye height {EYE} m)"))
    return out


# --- floor grid --------------------------------------------------------------------------------------------

class _Grid:
    def __init__(self, geom, units: list[_Unit]):
        x0, y0, x1, y1 = geom.bounds
        self.c = c = max(CELL_MIN, math.sqrt((x1 - x0) * (y1 - y0) / MAX_CELLS))
        self.x0, self.y0 = x0, y0
        self.nx, self.ny = max(1, int(math.ceil((x1 - x0) / c))), max(1, int(math.ceil((y1 - y0) / c)))
        from agentic_sim_world_gen.layout.room import point_in_polygon

        free = [[True] * self.nx for _ in range(self.ny)]
        if geom.zones:
            doors = [w.rect(*w.interval(o), -w.thickness, w.thickness) for w in geom.walls for o in w.openings
                     if o.kind != "window"]
            for j in range(self.ny):
                for i in range(self.nx):
                    p = self.center(i, j)
                    free[j][i] = any(point_in_polygon(p, z) for z in geom.zones.values()) or any(
                        point_in_polygon(p, d) for d in doors)
        for ob in geom.obstacles:
            if ob.kind in ("wall", "column") and ob.z[0] < BODY[1]:
                self._fill(free, ob.poly, False)
        self.free = free
        for u in units:
            if u.z < BODY[1] and u.top > BODY[0]:
                self._fill(free, u.poly, False)

    def center(self, i: int, j: int) -> Pt:
        return (self.x0 + (i + 0.5) * self.c, self.y0 + (j + 0.5) * self.c)

    def cell(self, p: Pt) -> tuple[int, int] | None:
        i, j = int((p[0] - self.x0) / self.c), int((p[1] - self.y0) / self.c)
        return (i, j) if 0 <= i < self.nx and 0 <= j < self.ny else None

    def cells_in(self, poly: list[Pt]):
        from agentic_sim_world_gen.layout.room import point_in_polygon

        xs, ys = [x for x, _ in poly], [y for _, y in poly]
        i0, i1 = max(0, int((min(xs) - self.x0) / self.c)), min(self.nx - 1, int((max(xs) - self.x0) / self.c))
        j0, j1 = max(0, int((min(ys) - self.y0) / self.c)), min(self.ny - 1, int((max(ys) - self.y0) / self.c))
        for j in range(j0, j1 + 1):
            for i in range(i0, i1 + 1):
                if point_in_polygon(self.center(i, j), poly):
                    yield i, j

    def _fill(self, grid, poly, value) -> None:
        for i, j in self.cells_in(poly):
            grid[j][i] = value

    def clearance(self) -> list[list[float]]:
        """Distance [m] from each free cell's center to the nearest blocked cell's edge."""
        pad = [[True] * (self.nx + 2)] + [[True, *(not v for v in row), True] for row in self.free] + [[True] * (self.nx + 2)]
        d = _edt(pad)  # outside the room counts as blocked
        return [[(x - 0.5) * self.c for x in row[1:-1]] for row in d[1:-1]]

    def walk(self, clear: list[list[float]], width: float, starts: list[Pt]) -> list[list[bool]]:
        """Floor swept by a person `width` wide walking from `starts` (or the largest open area)."""
        r = width / 2
        ok = [[clear[j][i] >= r - self.c for i in range(self.nx)] for j in range(self.ny)]
        comp = [[-1] * self.nx for _ in range(self.ny)]
        sizes = []
        for j in range(self.ny):
            for i in range(self.nx):
                if ok[j][i] and comp[j][i] < 0:
                    sizes.append(self._flood(ok, comp, i, j, len(sizes)))
        keep = set()
        reach = max(1, int(math.ceil(1.0 / self.c)))  # a door point may sit in a narrow doorway: look 1 m around
        for p in starts:
            c = self.cell(p)
            if c is None:
                continue
            for j in range(max(0, c[1] - reach), min(self.ny, c[1] + reach + 1)):
                for i in range(max(0, c[0] - reach), min(self.nx, c[0] + reach + 1)):
                    if comp[j][i] >= 0 and math.dist(self.center(i, j), p) <= 1.0:
                        keep.add(comp[j][i])
        if not starts and sizes:
            keep.add(max(range(len(sizes)), key=sizes.__getitem__))
        return self.sweep([[comp[j][i] in keep for i in range(self.nx)] for j in range(self.ny)], width)

    def sweep(self, centers: list[list[bool]], width: float) -> list[list[bool]]:
        """The free floor a body `width` wide covers with its centre on `centers`."""
        if not any(any(r) for r in centers):
            return centers
        d = _edt(centers)
        r = width / 2
        return [[self.free[j][i] and d[j][i] * self.c <= r + self.c for i in range(self.nx)] for j in range(self.ny)]

    def _flood(self, ok, comp, i, j, n) -> int:
        q, comp[j][i], size = deque([(i, j)]), n, 0
        while q:
            a, b = q.popleft()
            size += 1
            for da, db in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                x, y = a + da, b + db
                if 0 <= x < self.nx and 0 <= y < self.ny and ok[y][x] and comp[y][x] < 0:
                    comp[y][x] = n
                    q.append((x, y))
        return size


def _front_box(u: _Unit) -> list[Pt]:
    from agentic_sim_world_gen.layout.frame import unit

    fx, fy = unit(u.facing)
    lx, ly = unit(u.facing + 90.0)
    cx, cy = u.pos[0] + fx * (u.half_depth + FRONT_DEPTH / 2), u.pos[1] + fy * (u.half_depth + FRONT_DEPTH / 2)
    hw, hd = 0.8 * u.half_width, FRONT_DEPTH / 2 - 0.02
    return [(cx + sx * lx * hw + sd * fx * hd, cy + sx * ly * hw + sd * fy * hd)
            for sx, sd in ((-1, -1), (1, -1), (1, 1), (-1, 1))]


def wide_over(place: str | None) -> float | None:
    """Aisles between shop shelves wider than this [m] waste the floor (None where it is not checked)."""
    if place not in ("shop", *STORE_PLACES):
        return None
    return WIDE_OVER.get(place, WIDE_AISLE * aisle_width(place))


class AisleMap:
    """The walking floor of a scene as one map: the grid (what stands in a walking person's way; vehicles
    and seats are moved aside), each free cell's clearance, and the widest way from the entrances to every cell.
    narrow_aisle / unreachable are read off it, layout.svg paints it, and `spots` says where the ways to the
    shelves and rooms are narrowest."""

    def __init__(self, scene: ResolvedScene, geom, units: list[_Unit]):
        self.scene, self.geom, self.units = scene, geom, units
        self.need = need_width(scene)
        self.grid = g = _Grid(geom, [u for u in units if not u.is_(MOVERS) and not u.is_(SEATS)])
        self.starts = [p for p, _ in _entrances_all(geom)]
        self.clear = g.clearance()
        seeds = []
        look = max(1, int(math.ceil(1.0 / g.c)))  # a door point may sit in a narrow doorway: look 1 m around
        for p in self.starts:
            c = g.cell(p)
            if c is None:
                continue
            for j in range(max(0, c[1] - look), min(g.ny, c[1] + look + 1)):
                for i in range(max(0, c[0] - look), min(g.nx, c[0] + look + 1)):
                    if g.free[j][i] and math.dist(g.center(i, j), p) <= 1.0:
                        seeds.append((i, j))
        self.by_doors = bool(seeds)
        if not seeds:  # no door: from the most open spot
            v, i, j = max(((self.clear[j][i], i, j) for j in range(g.ny) for i in range(g.nx) if g.free[j][i]),
                          default=(0.0, 0, 0))
            seeds = [(i, j)] if v > 0 else []
        self.best, self.prev = widest(self.clear, seeds)

    def swept(self, width: float) -> list[list[bool]]:
        """Floor swept by a person `width` wide walking from the doors (or the largest open area)."""
        g = self.grid
        if not self.by_doors:  # which open area is the largest depends on the width
            return g.walk(self.clear, width, [])
        t = width / 2 - g.c
        return g.sweep([[g.free[j][i] and self.best[j][i] >= t for i in range(g.nx)] for j in range(g.ny)], width)

    def way_to(self, cells: list[tuple[int, int]], share: float = 1.0) -> dict[str, Any] | None:
        """The narrowest spot on the widest way to `cells`: {width [m], at [x, y]}. share < 1 takes the cell that
        far down the cells ordered by how wide the way to them is (0.5 = the median: the way to most of a shelf's
        front, not only to its end at the mouth of the aisle)."""
        ok = sorted((c for c in cells if self.best[c[1]][c[0]] >= 0), key=lambda c: -self.best[c[1]][c[0]])
        if not ok:
            return None
        at, low = narrowest(self.clear, self.prev, ok[min(len(ok) - 1, int((1.0 - share) * len(ok)))])
        x, y = self.grid.center(*at)
        return {"width": round(2 * low, 2), "at": (round(x, 2), round(y, 2))}

    def targets(self) -> list[tuple[str, list[tuple[int, int]], float]]:
        """(id, cells, share) of what people walk to: the fronts of shelves / cases / registers, and the rooms."""
        g = self.grid
        out = [(u.id, [c for c in g.cells_in(_front_box(u)) if g.free[c[1]][c[0]]], 0.5)
               for u in self.units if u.is_(FRONTS) and u.floor]
        out += [(zid, [c for c in g.cells_in(poly) if g.free[c[1]][c[0]]], 1.0) for zid, poly in self.geom.zones.items()]
        return [t for t in out if t[1]]

    def spots(self) -> list[dict[str, Any]]:
        """The narrowest spots of the ways from the entrances, narrowest first: {width, at, to: [ids], narrow}.
        Ways whose spots are close and as wide (the same aisle) are one entry, at the narrowest of them."""
        ways = [(w, tid) for tid, cells, share in self.targets() if (w := self.way_to(cells, share)) is not None]
        ways.sort(key=lambda x: x[0]["width"])
        out: list[dict[str, Any]] = []
        groups: list[list[Pt]] = []  # every spot of a group: a long aisle is a chain of them
        for w, tid in ways:
            for row, pts in zip(out, groups):
                if w["width"] - row["width"] <= 2 * self.grid.c and any(math.dist(w["at"], p) <= SPOT_MERGE for p in pts):
                    row["to"].append(tid)
                    pts.append(w["at"])
                    break
            else:
                out.append({**w, "to": [tid]})
                groups.append([w["at"]])
        for r in out:
            r["narrow"] = r["width"] < self.need - 2 * self.grid.c
        return out

    def ridge(self) -> list[tuple[tuple[int, int], float]]:
        """((i, j), width [m]) along the centre lines of the aisles, for painting."""
        return [((i, j), 2 * self.clear[j][i]) for i, j in ridge(self.clear, self.grid.c / 2) if self.grid.free[j][i]]


SPOT_MERGE = 1.5  # narrowest spots this close [m] and as wide are the same spot (one aisle is one spot)


def aisle_map(scene: ResolvedScene) -> AisleMap | None:
    """The scene's AisleMap (None outdoors or with nothing placed)."""
    from agentic_sim_world_gen.layout.room import RoomGeometry

    room = scene.spec.room
    if room.outdoor:
        return None
    geom = RoomGeometry(room)
    units = [u for u in _units(scene) if not geom.outside(zone_of(scene, u.o))]
    return AisleMap(scene, geom, units) if units else None


def _spot_words(amap: AisleMap, cells: list[tuple[int, int]], need: float) -> str:
    """Where the way to `cells` is narrowest, in words (nothing when that spot is not narrower than `need`)."""
    w = amap.way_to(cells, 0.5)
    if w is None or w["width"] >= need:
        return ""
    return f": about {w['width']:.2f} m at ({w['at'][0]:.1f}, {w['at'][1]:.1f})"


def _floor(scene: ResolvedScene, geom, units: list[_Unit], amap: AisleMap | None = None) -> list[Issue]:
    place = scene.spec.room.place
    amap = amap or AisleMap(scene, geom, units)
    need = amap.need
    targets = [u for u in units if u.is_(FRONTS) and u.floor]
    grid = stand = amap.grid
    starts = amap.starts
    squeeze = amap.swept(SQUEEZE)
    roomy = amap.swept(need)
    out = []
    for u in targets:
        box = list(grid.cells_in(_front_box(u)))
        if not box:
            continue
        free = sum(stand.free[j][i] for i, j in box) / len(box)
        if free < FRONT_FREE:
            out.append(Issue("no_front_space", (u.id,), f"only {free:.0%} of the {FRONT_DEPTH} m in front of it is "
                             "free; leave room to stand there"))
            continue
        if not any(squeeze[j][i] for i, j in box):
            out.append(Issue("unreachable", (u.id,), f"no way from the {'doors' if starts else 'open floor'} to the "
                             f"front of it (even {SQUEEZE} m wide)"))
        elif sum(roomy[j][i] for i, j in box) < FRONT_FREE * sum(stand.free[j][i] for i, j in box):
            # wide enough only at the mouth of the aisle: most of the front needs a narrower way
            out.append(Issue("narrow_aisle", (u.id,), f"the way to it is narrower than {need} m"
                             + (f" (the robot's way for purpose {scene.spec.purpose.name}; people need {aisle_width(place)} m)"
                                   if need > aisle_width(place)
                                else f" (people's aisle for place {place})" if place else "")
                             + _spot_words(amap, [c for c in box if stand.free[c[1]][c[0]]], need)
                             + "; " + (fix := _aisle_fix(u, units, grid, need))[0], fix[1]))
    for zid, poly in geom.zones.items():
        cells = list(grid.cells_in(poly))
        if cells and starts and not any(squeeze[j][i] for i, j in cells):
            out.append(Issue("unreachable", (zid,), f"zone {geom.zone_names.get(zid, zid)} cannot be reached from "
                             f"the doors (even {SQUEEZE} m wide)"))
    return out


def _sparse(scene: ResolvedScene, geom, units: list[_Unit]) -> list[Issue]:
    """Shop floors with large empty patches or over-wide aisles: fixtures too few for the room."""
    from agentic_sim_world_gen.layout.frame import unit
    from agentic_sim_world_gen.layout.room import point_in_polygon

    place = scene.spec.room.place
    if place not in ("shop", *STORE_PLACES):
        return []
    need = aisle_width(place)
    target = photo_aisle(scene)
    # with an aisle measured in a photo, "too wide" is wider than the photo's (not the kind of store's usual)
    over = target[2] if target is not None else wide_over(place)
    grid = _Grid(geom, [u for u in units if u.floor])
    clear = [row[:] for row in grid.clearance()]
    doors = [p for p, _ in _entrances_all(geom)]
    out = []
    for _ in range(EMPTY_SPOTS):
        r, i, j = max((clear[j][i], i, j) for j in range(grid.ny) for i in range(grid.nx))
        if 2 * r < EMPTY_OVER.get(place, EMPTY_SPAN):
            break
        c = grid.center(i, j)
        near = " (by a door: fine if it is kept free on purpose)" if any(
            math.dist(c, d) < r + 1.0 for d in doors) else ""
        out.append(Issue("empty_floor", (), f"an empty patch {2 * r:.1f} m across around ({c[0]:.1f}, {c[1]:.1f}) "
                         f"with no fixture{near}; add rows or lengthen them, or make the room smaller"))
        for jj in range(grid.ny):  # the next spot must not overlap this one
            for ii in range(grid.nx):
                if math.dist(grid.center(ii, jj), c) < 2 * r:
                    clear[jj][ii] = 0.0
    # aisles between shelves facing each other; wider ones than EMPTY_SPAN are empty patches already, and the
    # floor in front of a register ("checkout counter" too) is for the queue
    shelves = [u for u in units if u.is_(FRONTS) and not (u.is_(REGISTERS) or _mods(u.o) & REGISTERS) and u.floor]
    wide: dict[frozenset[str], list[float]] = {}
    for u in shelves:
        fx, fy = unit(u.facing)
        step, d = grid.c / 2, 0.0
        x, y = u.pos[0] + fx * u.half_depth, u.pos[1] + fy * u.half_depth
        while (c := grid.cell((x, y))) is not None and (grid.free[c[1]][c[0]] or d < grid.c):
            x, y, d = x + fx * step, y + fy * step, d + step
        if c is None or not over < d <= max(EMPTY_SPAN, over):
            continue
        other = next((v for v in shelves if v is not u and point_in_polygon((x, y), v.poly)), None)
        if other is not None and _angle(other.facing, u.facing + 180.0) <= 30.0:
            wide.setdefault(frozenset((u.o.id, other.o.id)), []).append(d)
    for ids, ds in wide.items():
        d = sorted(ds)[len(ds) // 2]
        out.append(Issue("wide_aisle", tuple(sorted(ids)), f"the aisle between them is {d:.1f} m wide ("
                         + (f"the photo measured {target[0]:g} m" if target is not None else f"place {place} needs {need} m")
                         + "); move the rows closer and add a row, or lengthen them"))
    return out


COMPASS = ((0.0, "east (+x)"), (90.0, "north (+y)"), (180.0, "west (-x)"), (270.0, "south (-y)"))


def _compass(deg: float) -> str:
    return min(COMPASS, key=lambda c: _angle(c[0], deg))[1]


def _across(u: _Unit, units: list[_Unit], grid: _Grid, reach: float) -> tuple[float, _Unit | None]:
    """Free floor straight in front of the unit (the narrowest of three lines across its width) and what stops it
    (another unit, or None for a wall)."""
    from agentic_sim_world_gen.layout.frame import unit
    from agentic_sim_world_gen.layout.request import seg_poly_dist
    from agentic_sim_world_gen.layout.room import point_in_polygon

    fx, fy = unit(u.facing)
    lx, ly = unit(u.facing + 90.0)
    step = grid.c / 2
    best: tuple[float, _Unit | None] = (reach, None)
    for s in (-0.6, 0.0, 0.6):
        x0 = u.pos[0] + fx * u.half_depth + lx * s * u.half_width
        y0 = u.pos[1] + fy * u.half_depth + ly * s * u.half_width
        t = step
        while t < best[0]:
            p = (x0 + fx * t, y0 + fy * t)
            c = grid.cell(p)
            if c is None or not grid.free[c[1]][c[0]]:
                hit = next((v for v in units if v is not u and point_in_polygon(p, v.poly)), None)
                if hit is None:  # the blocked cell's centre can fall just outside the unit (named a wall
                    # or the wrong fixture): the nearest unit within a cell and a half
                    near = [(seg_poly_dist(p, p, v.poly), i) for i, v in enumerate(units) if v is not u]
                    d, i = min(near, default=(math.inf, -1))
                    hit = units[i] if d <= 1.5 * grid.c else None
                best = (t, hit)
                break
            t += step
    return best


def _aisle_fix(u: _Unit, units: list[_Unit], grid: _Grid, need: float) -> tuple[str, tuple[dict, ...]]:
    """How to widen the way to u: who to move, which way and how far, in words and as Issue.fixes."""
    from agentic_sim_world_gen.layout.frame import unit
    gap, other = _across(u, units, grid, need + 1.0)
    if gap >= need:
        return (f"{gap:.2f} m is free straight in front of it, so the narrow part is on the way there (the end of "
                "the aisle, or a gap between fixtures): widen that"), ()
    short = math.ceil((need - gap) * 20) / 20  # round up to 5 cm
    back = _compass(u.facing + 180.0)
    me = u.o.id
    fx, fy = unit(u.facing)
    away = {"move": me, "by": (-fx * short, -fy * short)}
    if other is None:
        return (f"only {gap:.2f} m to the wall in front: move {me} {short:.2f} m {back} (set_layout gap / "
                "move_object), or turn it"), (away,)
    if other.o.id == me and u.o.array is not None:
        return (f"only {gap:.2f} m to {other.id} of the same rows: widen the array aisle by {short:.2f} m "
                f"(set_array aisle={u.o.array.aisle + short:.2f})"), ({"array": me, "aisle": short},)
    return (f"only {gap:.2f} m to {other.id} in front: move {other.o.id} {short:.2f} m {_compass(u.facing)} "
            f"or {me} {short:.2f} m {back}"), ({"move": other.o.id, "by": (fx * short, fy * short)}, away)


def _entrances_all(geom) -> list[tuple[Pt, str]]:
    """Points just inside every door / opening (both sides that are inside)."""
    out = []
    for w in geom.walls:
        for o in w.openings:
            if o.kind == "window":
                continue
            u0, u1 = w.interval(o)
            for s in (1.0, -1.0):
                p = w.at((u0 + u1) / 2, s * (w.thickness / 2 + 0.3))
                if geom.inside([p], 0.0):
                    out.append((p, o.id))
    return out


EMPTY_SHELVES = 0.5  # share of a fixture's shelf levels (all its units) holding nothing


def shelf_fixtures(scene: ResolvedScene) -> set[str]:
    """The shop's display fixtures (shelves, cases, racks; not registers or stock): their shelf boards are
    found even when nothing is stocked on them, so empty ones can be reported and stocked."""
    if scene.spec.room.place not in SHOP_PLACES:
        return set()
    from agentic_sim_world_gen.scene.decor import KIND_FIXTURES, shop_kind

    kinds = FRONTS | KIND_FIXTURES.get(shop_kind(scene) or "", frozenset())  # a liquor store's crates
    asset = {p.object_id: p.asset for p in scene.placements if p.asset is not None}
    return {o.id for o in scene.spec.objects
            if o.id in asset and o.fill is None and not o.decor and o.opening is None
            and heads(o, asset[o.id]) & kinds and not heads(o, asset[o.id]) & REGISTERS
            and not _mods(o) & REGISTERS}


def _shelves(scene: ResolvedScene) -> list[Issue]:
    """Shop fixtures whose shelf levels (and hook rows) are mostly empty: a shop with bare gondolas does not
    look like one. A hook row whose hooks all come with products (a pre-stocked pegboard) counts as held."""
    from agentic_sim_world_gen.layout.array import instances
    from agentic_sim_world_gen.layout.fill import hook_rows, levels

    fixtures = shelf_fixtures(scene)
    asset = {p.object_id: p.asset for p in scene.placements if p.asset is not None}
    held: dict[str, set[tuple[str, int]]] = {}
    for o in scene.spec.objects:
        if o.fill is not None:
            for n, *_ in o.fill.slots:
                if m := re.match(r"(.+?)_([lh])(\d+)", n):
                    held.setdefault(o.fill.on, set()).add((m.group(1), m.group(2), int(m.group(3))))
    out = []
    for o in scene.spec.objects:
        a = asset.get(o.id)
        if o.id not in fixtures or not (a.shelves or a.hooks) or o.position is None:
            continue
        rows = hook_rows(a.hooks or [])
        n_units, n_lv = len(instances(o, a)), len(levels(a.shelves or [])) + len(rows)
        full = sum(1 for r in rows if all(k["busy"] for k in r))  # pre-stocked rows
        total = n_units * n_lv
        empty = total - len(held.get(o.id, ())) - n_units * full
        if total and empty / total >= EMPTY_SHELVES:
            out.append(Issue("empty_shelves", (o.id,),
                             f"{empty} of {total} shelf levels hold no products ({round(100 * empty / total)} %)"))
    return out


def _photo_aisle(scene: ResolvedScene) -> list[Issue]:
    """The scene's width on the line an aisle was measured on in a photo, when it is not the photo's."""
    t = photo_aisle(scene)
    if t is None or len(scene.spec.photo_aisle.points) != 2:
        return []
    from agentic_sim_world_gen.photo_measure import line_width

    w, by = line_width(scene, *scene.spec.photo_aisle.points)
    if t[1] <= w <= t[2]:
        return []
    ids = tuple(sorted({str(n).split(".")[0] for n in by if n and not str(n).startswith("wall")} &
                       {o.id for o in scene.spec.objects}))
    return [Issue("photo_aisle", ids, f"the aisle is {w:g} m wide where the photo measured {t[0]:g} m ({t[1]:g}-{t[2]:g} m): "
                  + ("move the rows closer (set_array aisle / move_object) or add a row" if w > t[2] else
                     "move the rows apart (set_array aisle / move_object) or take one out")
                  + f" until it is about {t[0]:g} m")]


def usability(scene: ResolvedScene) -> list[Issue]:
    """Usability warnings for an indoor scene (none outdoors)."""
    from agentic_sim_world_gen.layout.room import RoomGeometry

    room = scene.spec.room
    if room.outdoor:
        return []
    geom = RoomGeometry(room)
    units = [u for u in _units(scene) if not geom.outside(zone_of(scene, u.o))]  # the site outside a room is not checked
    if not units:
        return []
    return (_floor(scene, geom, units, AisleMap(scene, geom, units)) + _sparse(scene, geom, units) + _chairs(units) + _tvs(units)
            + _registers(scene, geom, units) + _windows(geom, units) + _shelves(scene) + _photo_views(scene, units)
            + _photo_aisle(scene))


PHOTO_VIEW = 0.03  # share of a photo an object not in it may cover before it is reported


def view_share(cam, size_px: tuple[int, int] | None, lo: tuple[float, float, float],
               hi: tuple[float, float, float]) -> float:
    """Share of the photo (0..1) that the box lo..hi covers seen from camera `cam` (PhotoCamera): the bounding
    rectangle of its points in front of the camera, clipped to the frame."""
    c = cam.position
    f = [cam.look_at[i] - c[i] for i in range(3)]
    n = math.sqrt(sum(v * v for v in f)) or 1.0
    f = [v / n for v in f]
    r = [f[1], -f[0], 0.0]  # forward x up
    n = math.hypot(r[0], r[1])
    if n < 1e-6:
        return 0.0  # looking straight up or down
    r = [r[0] / n, r[1] / n, 0.0]
    u = [r[1] * f[2] - r[2] * f[1], r[2] * f[0] - r[0] * f[2], r[0] * f[1] - r[1] * f[0]]
    w, h = size_px or (4, 3)
    tx = math.tan(math.radians(cam.hfov_deg) / 2)
    ty = tx * h / w
    xs, ys = [], []
    k = 6
    for i in range(k + 1):
        for j in range(k + 1):
            for m in range(k + 1):
                p = (lo[0] + (hi[0] - lo[0]) * i / k, lo[1] + (hi[1] - lo[1]) * j / k, lo[2] + (hi[2] - lo[2]) * m / k)
                d = [p[q] - c[q] for q in range(3)]
                zc = sum(d[q] * f[q] for q in range(3))
                if zc < 0.05:
                    continue
                xs.append(sum(d[q] * r[q] for q in range(3)) / (zc * tx))
                ys.append(sum(d[q] * u[q] for q in range(3)) / (zc * ty))
    if not xs:
        return 0.0
    x0, x1 = max(min(xs), -1.0), min(max(xs), 1.0)
    y0, y1 = max(min(ys), -1.0), min(max(ys), 1.0)
    return max(0.0, x1 - x0) * max(0.0, y1 - y0) / 4


def _photo_views(scene: ResolvedScene, units: list[_Unit]) -> list[Issue]:
    """Things not in a photo standing in its view: the compare render shows them in front of what the photo
    shows. Only for photos that some object names in seen_in; things added for the look (decor) are left out."""
    named = {i for o in scene.spec.objects for i in o.seen_in or []}
    out = []
    for inp in scene.spec.inputs:
        if inp.camera is None or inp.kind != "photo" or inp.id not in named:
            continue
        worst: dict[str, tuple[float, _Unit]] = {}
        for u in units:
            if u.o.decor or inp.id in (u.o.seen_in or []):
                continue
            xs, ys = [p[0] for p in u.poly], [p[1] for p in u.poly]
            share = view_share(inp.camera, inp.size_px, (min(xs), min(ys), u.z), (max(xs), max(ys), u.top))
            if share >= PHOTO_VIEW and share > worst.get(u.o.id, (0.0, None))[0]:
                worst[u.o.id] = (share, u)
        for oid, (share, u) in worst.items():
            out.append(Issue("in_photo_view", (oid, inp.id), f"not in photo {inp.id} but covers {share:.0%} of its "
                             "view: move it out of the camera's view (behind or beside the camera) or add the photo "
                             "to its seen_in"))
    return out
