"""Room geometry shared by the solver, the checks, the USD writer and the preview.

A rectangular room and a floor plan (Room.plan) are turned into the same pieces:
- walls:     centerline segments with thickness/height and the openings cut into them
- faces:     interior wall faces objects can stand against (with a compass direction and a zone)
- obstacles: wall bodies (plan only; a rectangle's walls are outside its interior) and the space to
             keep clear in front of doors
- zones:     named interior polygons (plan zones; a rectangle has none)

An indoor room with the outside around it (Room.site) keeps the room as above and adds the site in
`site_faces` (the site edges, the area edges, the buildings and the room's own outer walls, "outside:<side>")
and in the obstacles; the site's areas are not zones (`zones` stays the room's own). Objects outside have
zone = an area id or "outside" (`outside()`).

An outdoor site (Room.outdoor, see agentic_sim_world_gen.scene.site) has no walls; its faces are the site edges,
the edges of its areas (zone = area id) and the outsides of its buildings; its obstacles are the
buildings, raised areas (curbs) and keep-clear areas (roads).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from agentic_sim_world_gen.scene.looks import fixture_grid
from agentic_sim_world_gen.scene.plan import Opening
from agentic_sim_world_gen.scene.site import convex_pieces

Pt = tuple[float, float]

MIN_FACE = 0.3  # ignore wall faces shorter than this (stubs, wall ends)
PROBE = 0.05  # distance from a face used to find which zone it borders
COMPASS = {90.0: "south", -90.0: "north", 0.0: "west", 180.0: "east"}  # inward facing -> wall direction
RECT_WALL_IDS = ("south", "north", "west", "east")


@dataclass
class Wall:
    id: str
    a: Pt  # centerline start
    b: Pt  # centerline end
    thickness: float
    height: float
    origin: float = 0.0  # opening offsets are measured from this distance along a->b
    openings: list[Opening] = field(default_factory=list)
    ext_a: float = 0.0  # solid extends this far past a (L corner: reach the other wall's outer face)
    ext_b: float = 0.0  # ... and past b

    @property
    def length(self) -> float:
        return math.dist(self.a, self.b)

    @property
    def dir(self) -> Pt:
        L = self.length or 1.0
        return ((self.b[0] - self.a[0]) / L, (self.b[1] - self.a[1]) / L)

    @property
    def normal(self) -> Pt:  # left of a->b
        dx, dy = self.dir
        return (-dy, dx)

    @property
    def yaw_deg(self) -> float:
        return math.degrees(math.atan2(self.dir[1], self.dir[0]))

    def at(self, u: float, n: float = 0.0) -> Pt:
        (dx, dy), (nx, ny) = self.dir, self.normal
        return (self.a[0] + dx * u + nx * n, self.a[1] + dy * u + ny * n)

    def interval(self, o: Opening) -> tuple[float, float]:
        s = self.origin + o.offset
        return max(0.0, s - o.width / 2), min(self.length, s + o.width / 2)

    def pieces(self) -> list[tuple[float, float, float, float, str]]:
        """Solid parts as (u0, u1, z0, z1, kind) with kind "wall", "below" (under a window) or "above"."""
        cuts = sorted((self.interval(o), o.resolved(self.height)) for o in self.openings)
        out, u = [], -self.ext_a
        for (u0, u1), (sill, top) in cuts:
            if u0 > u + 1e-6:
                out.append((u, u0, 0.0, self.height, "wall"))
            if sill > 1e-6:
                out.append((u0, u1, 0.0, sill, "below"))
            if top < self.height - 1e-6:
                out.append((u0, u1, top, self.height, "above"))
            u = max(u, u1)
        end = self.length + self.ext_b
        if u < end - 1e-6:
            out.append((u, end, 0.0, self.height, "wall"))
        return out

    def rect(self, u0: float, u1: float, n0: float, n1: float) -> list[Pt]:
        return [self.at(u0, n0), self.at(u1, n0), self.at(u1, n1), self.at(u0, n1)]


@dataclass
class Face:
    wall: str
    direction: str  # compass name of the wall as seen from inside (north = the wall on the north side)
    p0: Pt
    p1: Pt
    facing: float  # inward normal angle [deg] = facing of an object backed against it
    zone: str | None = None
    kind: str = "wall"  # wall | site (outdoor site edge) | area (area edge) | building (outside of a building)

    @property
    def side(self) -> str:
        """Compass side of the wall/building this face is on (a building's south face: "south")."""
        return _compass(self.facing + 180.0)

    @property
    def length(self) -> float:
        return math.dist(self.p0, self.p1)


def _fixture_spacing(kind: str) -> float:
    from agentic_sim_world_gen.scene.fixtures import KINDS

    return KINDS[kind]["spacing"]


@dataclass
class Obstacle:
    id: str  # wall:<id> / door:<id> / beam:<id> / fixture:<id> / building:<id> / curb:<id> / area:<id> / level:<id>
    #          / stairs:<id> / rail:<id>
    kind: str  # wall | door | beam | fixture | building | curb | keep_clear | level | stairs | rail | column
    poly: list[Pt]
    z: tuple[float, float]
    zone: str | None = None  # objects in this zone ignore the obstacle (a car on its road)


def point_in_polygon(pt: Pt, poly: list[Pt], tol: float = 0.0) -> bool:
    x, y = pt
    inside = False
    n = len(poly)
    for i in range(n):
        (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % n]
        if tol > 0 and _seg_dist(pt, (x1, y1), (x2, y2)) <= tol:
            return True
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def _seg_dist(p: Pt, a: Pt, b: Pt) -> float:
    ax, ay = b[0] - a[0], b[1] - a[1]
    L2 = ax * ax + ay * ay
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * ax + (p[1] - a[1]) * ay) / L2))
    return math.dist(p, (a[0] + ax * t, a[1] + ay * t))


def _clip(p: Pt, q: Pt, poly: list[Pt]) -> tuple[float, float] | None:
    """Parameter interval [t0, t1] of segment p->q inside a convex polygon (Cyrus-Beck)."""
    area = sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1] for i in range(len(poly)))
    sgn = 1.0 if area > 0 else -1.0
    t0, t1 = 0.0, 1.0
    dx, dy = q[0] - p[0], q[1] - p[1]
    for i in range(len(poly)):
        (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % len(poly)]
        nx, ny = sgn * (y2 - y1), -sgn * (x2 - x1)  # outward normal
        num = nx * (p[0] - x1) + ny * (p[1] - y1)
        den = nx * dx + ny * dy
        if abs(den) < 1e-12:
            if num > 0:
                return None
            continue
        t = -num / den
        if den < 0:
            t0 = max(t0, t)
        else:
            t1 = min(t1, t)
        if t0 >= t1:
            return None
    return (t0, t1)


def _subtract(span: tuple[float, float], cuts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out, (lo, hi) = [], span
    for a, b in sorted(cuts):
        if a > lo:
            out.append((lo, min(a, hi)))
        lo = max(lo, b)
    if lo < hi:
        out.append((lo, hi))
    return out


def centroid(poly: list[Pt]) -> Pt:
    a = cx = cy = 0.0
    for i in range(len(poly)):
        (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % len(poly)]
        c = x1 * y2 - x2 * y1
        a += c
        cx += (x1 + x2) * c
        cy += (y1 + y2) * c
    if abs(a) < 1e-12:
        return (sum(p[0] for p in poly) / len(poly), sum(p[1] for p in poly) / len(poly))
    return (cx / (3 * a), cy / (3 * a))


def _compass(facing: float) -> str:
    best = min(COMPASS, key=lambda k: abs(((facing - k + 180) % 360) - 180))
    return COMPASS[best]


def _edges(poly: list[Pt], inward: bool) -> list[tuple[Pt, Pt, float]]:
    """Edges of a counter-clockwise polygon (>= MIN_FACE) with the facing of an object backed against
    them: inward (inside an area) or outward (outside a building)."""
    out = []
    for i in range(len(poly)):
        p, q = poly[i], poly[(i + 1) % len(poly)]
        if math.dist(p, q) < MIN_FACE:
            continue
        left = math.degrees(math.atan2(q[0] - p[0], p[1] - q[1]))  # left normal (-dy, dx) = the inside
        f = left if inward else left + 180.0
        dx, dy = q[0] - p[0], q[1] - p[1]
        if (dx < 0) if abs(dx) >= abs(dy) else (dy < 0):  # run west->east / south->north, like `along`
            p, q = q, p
        out.append((p, q, round(((f + 180.0) % 360.0) - 180.0, 6)))
    return out


def _close_corners(walls: list[Wall], tol: float = 1e-3) -> None:
    """Centerline walls that share an end point (L corner) leave the outer corner square open; extend
    each such end by half the other wall's thickness (more for acute angles) so the corner is closed.
    T joins (an end on another wall's middle) already reach the other wall's centerline."""
    for w in walls:
        for end in ("a", "b"):
            p = getattr(w, end)
            ext = 0.0
            for o in walls:
                if o is w or not (math.dist(p, o.a) < tol or math.dist(p, o.b) < tol):
                    continue
                sin = abs(w.dir[0] * o.dir[1] - w.dir[1] * o.dir[0])
                if sin > 0.2:  # skip (nearly) collinear joins
                    ext = max(ext, min(o.thickness / 2 / sin, o.thickness))
            setattr(w, f"ext_{end}", ext)


class RoomGeometry:
    def __init__(self, room):
        self.room = room
        t, h = room.wall_thickness, room.height
        self.zones: dict[str, list[Pt]] = {}
        self.areas: dict = {}
        self.buildings: dict = {}
        self.zone_names: dict[str, str] = {}
        self.site = None  # Room.site
        self.site_bounds: tuple[float, float, float, float] | None = None
        self.site_faces: list[Face] = []
        if room.outdoor:
            self._site(room)
            return
        if room.plan is None:
            w, d = room.width, room.depth
            self.walls = [
                Wall("south", (-t, -t / 2), (w + t, -t / 2), t, h, origin=t),
                Wall("north", (-t, d + t / 2), (w + t, d + t / 2), t, h, origin=t),
                Wall("west", (-t / 2, 0.0), (-t / 2, d), t, h),
                Wall("east", (w + t / 2, 0.0), (w + t / 2, d), t, h),
            ]
            self.bounds = (0.0, 0.0, w, d)  # interior
            self.slab = (-t, -t, w + t, d + t)
            self.faces = [
                Face("south", "south", (0.0, 0.0), (w, 0.0), 90.0),
                Face("north", "north", (0.0, d), (w, d), -90.0),
                Face("west", "west", (0.0, 0.0), (0.0, d), 0.0),
                Face("east", "east", (w, 0.0), (w, d), 180.0),
            ]
        else:
            self.walls = [
                Wall(pw.id, tuple(pw.start), tuple(pw.end), pw.thickness or t, pw.height or h) for pw in room.plan.walls
            ]
            _close_corners(self.walls)
            for z in room.plan.zones:
                self.zones[z.id] = [tuple(p) for p in z.polygon]
                self.zone_names[z.id] = z.name or z.id
            self.bounds = (0.0, 0.0, room.width, room.depth)
            self.slab = self.bounds
        by_id = {w.id: w for w in self.walls}
        self.unknown_openings = []
        for o in room.openings:
            if o.wall in by_id:
                by_id[o.wall].openings.append(o)
            else:
                self.unknown_openings.append(o)
        self.obstacles = self._obstacles()
        if room.plan is not None:
            self.faces = self._plan_faces()
        if room.site is not None:
            self._surroundings(room.site)

    # --- construction ---------------------------------------------------------------

    def _site(self, room) -> None:
        w, d, m = room.width, room.depth, room.ground_margin
        self.walls, self.unknown_openings = [], []
        self.bounds = (0.0, 0.0, w, d)
        self.slab = (-m, -m, w + m, d + m)
        self.faces = [
            Face("south", "south", (0.0, 0.0), (w, 0.0), 90.0, kind="site"),
            Face("north", "north", (0.0, d), (w, d), -90.0, kind="site"),
            Face("west", "west", (0.0, 0.0), (0.0, d), 0.0, kind="site"),
            Face("east", "east", (w, 0.0), (w, d), 180.0, kind="site"),
        ]
        self.obstacles = []
        self.areas = {a.id: a for a in room.areas}
        self.buildings = {b.id: b for b in room.buildings}
        for a in room.areas:
            poly = a.poly
            self.zones[a.id] = poly
            self.zone_names[a.id] = a.name or a.id
            self.faces += [Face(a.id, _compass(f), p, q, f, a.id, "area") for p, q, f in _edges(poly, inward=True)]
            pieces = convex_pieces(poly)
            if a.clear:
                self.obstacles += [Obstacle(f"area:{a.id}", "keep_clear", pc, (0.0, 1e3), a.id) for pc in pieces]
            elif a.top > 1e-6:
                self.obstacles += [Obstacle(f"curb:{a.id}", "curb", pc, (0.0, a.top)) for pc in pieces]
        for b in room.buildings:
            poly = b.poly
            self.faces += [Face(b.id, _compass(f), p, q, f, None, "building") for p, q, f in _edges(poly, inward=False)]
            self.obstacles += [Obstacle(f"building:{b.id}", "building", pc, (0.0, b.height)) for pc in convex_pieces(poly)]

    def _surroundings(self, site) -> None:
        """Room.site: faces and obstacles of the outside around the room."""
        self.site = site
        self.footprint = self.room.footprint()
        self.site_bounds = x0, y0, x1, y1 = site.bounds(self.footprint)
        self.areas = {a.id: a for a in site.areas}
        self.buildings = {b.id: b for b in site.buildings}
        fs = [
            Face("site", "south", (x0, y0), (x1, y0), 90.0, "outside", "site"),
            Face("site", "north", (x0, y1), (x1, y1), -90.0, "outside", "site"),
            Face("site", "west", (x0, y0), (x0, y1), 0.0, "outside", "site"),
            Face("site", "east", (x1, y0), (x1, y1), 180.0, "outside", "site"),
        ]
        fx0, fy0, fx1, fy1 = self.footprint
        outline = [(fx0, fy0), (fx1, fy0), (fx1, fy1), (fx0, fy1)]
        fs += [Face("outside", _compass(f), p, q, f, None, "exterior") for p, q, f in _edges(outline, inward=False)]
        for a in site.areas:
            poly = a.poly
            fs += [Face(a.id, _compass(f), p, q, f, a.id, "area") for p, q, f in _edges(poly, inward=True)]
            pieces = convex_pieces(poly)
            if a.clear:
                self.obstacles += [Obstacle(f"area:{a.id}", "keep_clear", pc, (0.0, 1e3), a.id) for pc in pieces]
            elif a.top > 1e-6:
                self.obstacles += [Obstacle(f"curb:{a.id}", "curb", pc, (0.0, a.top)) for pc in pieces]
        for b in site.buildings:
            poly = b.poly
            fs += [Face(b.id, _compass(f), p, q, f, None, "building") for p, q, f in _edges(poly, inward=False)]
            self.obstacles += [Obstacle(f"building:{b.id}", "building", pc, (0.0, b.height)) for pc in convex_pieces(poly)]
        self.site_faces = fs

    def outside(self, zone: str | None) -> bool:
        """An object with this zone stands outside the room, on the site (Room.site)."""
        return self.site is not None and zone is not None and (zone == "outside" or zone in self.areas)

    def _obstacles(self) -> list[Obstacle]:
        out = []
        for w in self.walls:
            if self.room.plan is not None:
                for i, (u0, u1, z0, z1, _) in enumerate(w.pieces()):
                    out.append(Obstacle(f"wall:{w.id}" + (f"#{i}" if i else ""), "wall",
                                        w.rect(u0, u1, -w.thickness / 2, w.thickness / 2), (z0, z1)))
            for o in w.openings:
                c = o.keep_clear
                if c <= 0:
                    continue
                u0, u1 = w.interval(o)
                _, top = o.resolved(w.height)
                half = w.thickness / 2
                out.append(Obstacle(f"door:{o.id}", "door", w.rect(u0, u1, half, half + c), (0.0, top)))
                out.append(Obstacle(f"door:{o.id}", "door", w.rect(u0, u1, -half - c, -half), (0.0, top)))
        from agentic_sim_world_gen.scene.fixtures import beams

        for b in beams(self.room):  # beams / soffits under the ceiling
            x0, y0, x1, y1 = b["rect"]
            out.append(Obstacle(f"beam:{b['id']}", "beam", [(x0, y0), (x1, y0), (x1, y1), (x0, y1)], b["z"]))
        from agentic_sim_world_gen.layout.validate import footprint
        from agentic_sim_world_gen.scene import levels as lv

        for v in self.room.levels:  # raised floors: an object straddling the edge cuts into the slab
            out += [Obstacle(f"level:{v.id}", "level", pc, (0.0, v.height)) for pc in convex_pieces(v.poly)]
        for s in self.room.stairs:
            f = lv.flight(self.room, s)
            for st in f["steps"]:
                cx, cy, _ = st["center"]
                out.append(Obstacle(f"stairs:{s.id}", "stairs", footprint((cx, cy), st["size"][:2], st["yaw"]),
                                    (f["base"], st["top"])))
        for c in self.room.columns:  # columns: solid from the floor up
            out.append(Obstacle(f"column:{c.id}", "column", c.poly, (0.0, c.top(self.room.height))))
        for r in lv.rails(self.room):
            z = (min(r["a"][2], r["b"][2]), max(r["a"][2], r["b"][2]))
            out.append(Obstacle(f"rail:{r['owner']}", "rail", lv.rail_rect(r), (min(z[0], z[1] - lv.RAIL_H), z[1])))
        from agentic_sim_world_gen.scene.fixtures import plan as fixture_plan

        if self.room.fixtures is not None and not self.room.outdoor:
            pts = self.fixture_points(self.room.fixtures.spacing or _fixture_spacing(self.room.fixtures.kind))
            for i, f in enumerate(fixture_plan(self.room, pts)):
                if f["flush"]:
                    continue  # in the ceiling: only things taller than the room reach it (too_tall)
                r = max([p.get("radius", 0.0) for p in f["parts"]] + [max(p["size"][:2]) / 2 for p in f["parts"] if "size" in p])
                x, y = f["x"], f["y"]
                out.append(Obstacle(f"fixture:Fixture_{i:02d}", "fixture",
                                    [(x - r, y - r), (x + r, y - r), (x + r, y + r), (x - r, y + r)],
                                    (f["lowest"], f["ceiling"])))
        return out

    def _plan_faces(self) -> list[Face]:
        """Both sides of every wall, cut where other walls touch them (corners, T-junctions), kept if
        the space in front is inside a zone (or the plan's interior when there are no zones)."""
        faces = []
        bodies = {w.id: w.rect(0.0, w.length, -w.thickness / 2, w.thickness / 2) for w in self.walls}
        for w in self.walls:
            for side in (1.0, -1.0):
                n = side * w.thickness / 2
                p, q = w.at(0.0, n + side * 0.01), w.at(w.length, n + side * 0.01)
                blocked = [iv for vid, poly in bodies.items() if vid != w.id and (iv := _clip(p, q, poly))]
                nx, ny = w.normal
                facing = round(math.degrees(math.atan2(side * ny, side * nx)), 6)
                for t0, t1 in _subtract((0.0, 1.0), blocked):
                    u0, u1 = t0 * w.length, t1 * w.length
                    if u1 - u0 < MIN_FACE:
                        continue
                    probe = w.at((u0 + u1) / 2, n + side * PROBE)
                    zone = self.zone_at(probe)
                    if (self.zones and zone is None) or (not self.zones and not self._interior(probe)):
                        continue
                    faces.append(Face(w.id, _compass(facing), w.at(u0, n), w.at(u1, n), facing, zone))
        return faces

    def _interior(self, p: Pt) -> bool:
        x0, y0, x1, y1 = self.bounds
        if not (x0 < p[0] < x1 and y0 < p[1] < y1):
            return False
        return not any(o.kind == "wall" and point_in_polygon(p, o.poly) for o in self.obstacles)

    # --- queries ------------------------------------------------------------------------

    def zone_at(self, p: Pt) -> str | None:
        for zid, poly in self.zones.items():
            if point_in_polygon(p, poly):
                return zid
        return None

    def ground_z(self, p: Pt) -> float:
        """Top of the ground at p: the highest raised area (outdoor curbs) or level (indoors) containing it, else 0."""
        if not self.room.outdoor:
            return max([v.height for v in self.room.levels if point_in_polygon(p, v.poly)], default=0.0)
        return max([a.top for a in self.room.areas if a.top > 0 and point_in_polygon(p, self.zones[a.id])], default=0.0)

    def zone_top(self, zone: str | None) -> float:
        a = self.areas.get(zone) if zone and (self.room.outdoor or self.site is not None) else None
        return a.top if a else 0.0

    def opening(self, opening_id: str) -> tuple[Wall, Opening] | None:
        """The wall and opening with this id (None if no wall holds it)."""
        for w in self.walls:
            for o in w.openings:
                if o.id == opening_id:
                    return w, o
        return None

    def opening_pose(self, opening_id: str, zone: str | None = None) -> tuple[Pt, float, float] | None:
        """Where an object filling the opening goes: (center on the wall centerline, facing into the room
        [deg], sill height). "Into the room" is the side inside `zone` (or the room); the wall's left
        side if both or neither are."""
        found = self.opening(opening_id)
        if found is None:
            return None
        w, o = found
        u0, u1 = w.interval(o)
        u = (u0 + u1) / 2
        probe = w.thickness / 2 + 0.3
        poly = self.zones.get(zone) if zone else None
        ins = [point_in_polygon(w.at(u, s * probe), poly) if poly else self.inside([w.at(u, s * probe)], 0.0)
               for s in (1.0, -1.0)]
        side = -1.0 if ins == [False, True] else 1.0
        nx, ny = w.normal
        facing = math.degrees(math.atan2(side * ny, side * nx))
        return w.at(u, 0.0), facing, o.resolved(w.height)[0]

    def inside(self, poly: list[Pt], tol: float = 0.01, zone: str | None = None) -> bool:
        """Inside the room (its zones / outline); with an outside zone (Room.site), on the site and clear of
        the room with its walls."""
        if self.outside(zone):
            from agentic_sim_world_gen.layout.validate import overlap_depth

            x0, y0, x1, y1 = self.site_bounds
            fx0, fy0, fx1, fy1 = self.footprint
            return all(x0 - tol <= x <= x1 + tol and y0 - tol <= y <= y1 + tol for x, y in poly) and overlap_depth(
                poly, [(fx0, fy0), (fx1, fy0), (fx1, fy1), (fx0, fy1)]) <= tol
        if self.zones and not self.room.outdoor:
            return all(any(point_in_polygon(p, z, tol) for z in self.zones.values()) for p in poly)
        x0, y0, x1, y1 = self.bounds
        return all(x0 - tol <= x <= x1 + tol and y0 - tol <= y <= y1 + tol for x, y in poly)

    def center(self, zone: str | None = None) -> Pt:
        if zone and zone in self.zones:
            return centroid(self.zones[zone])
        if self.outside(zone):
            if zone in self.areas:
                return centroid(self.areas[zone].poly)
            # "outside": the middle of the widest strip of the site beside the room
            x0, y0, x1, y1 = self.site_bounds
            fx0, fy0, fx1, fy1 = self.footprint
            strips = [(fy0 - y0, ((x0 + x1) / 2, (y0 + fy0) / 2)), (y1 - fy1, ((x0 + x1) / 2, (fy1 + y1) / 2)),
                      (fx0 - x0, ((x0 + fx0) / 2, (y0 + y1) / 2)), (x1 - fx1, ((fx1 + x1) / 2, (y0 + y1) / 2))]
            return max(strips, key=lambda s: s[0])[1]
        x0, y0, x1, y1 = self.bounds
        return ((x0 + x1) / 2, (y0 + y1) / 2)

    def faces_for(self, wall: str, zone: str | None = None) -> list[Face]:
        """Faces matching a layout's against_wall: compass name, "any" or a wall id; longest first.
        Outdoor: compass / "any" pick the site edges, or the zone's (area's) edges with a zone;
        "<building>" any outside face of that building, "<building>:<side>" one side (e.g. shop:south)."""
        order = {"north": 0, "west": 1, "east": 2, "south": 3}
        if self.site is not None and (self.outside(zone) or wall.split(":")[0] in ("outside", *self.buildings)):
            # Room.site: "outside[:<side>]" the room's outer walls, "<building>[:<side>]", else the site edges
            # ("outside") or the area's edges
            head, _, side = wall.partition(":")
            if head == "outside" or head in self.buildings:
                kind = "exterior" if head == "outside" else "building"
                return sorted((f for f in self.site_faces if f.kind == kind and f.wall == head and (not side or f.side == side)),
                              key=lambda f: (order[f.side], -f.length))
            fs = [f for f in self.site_faces if f.kind in ("site", "area") and f.zone == zone]
        elif self.room.outdoor:
            if ":" in wall:
                bid, side = wall.split(":", 1)
                return sorted((f for f in self.faces if f.kind == "building" and f.wall == bid and f.side == side),
                              key=lambda f: -f.length)
            if wall in self.buildings:
                return sorted((f for f in self.faces if f.kind == "building" and f.wall == wall), key=lambda f: -f.length)
            fs = [f for f in self.faces if f.kind in ("site", "area") and f.zone == zone]
        else:
            fs = [f for f in self.faces if zone is None or not self.zones or f.zone == zone]
        if wall == "any":
            return sorted(fs, key=lambda f: (order[f.direction], -f.length))
        if wall in order:
            return sorted((f for f in fs if f.direction == wall), key=lambda f: -f.length)
        return sorted((f for f in fs if f.wall == wall), key=lambda f: -f.length)

    def inner_side(self, w: Wall, u: float) -> float:
        """+1 / -1: the side of wall `w` (at distance u along it) that faces the room (doors swing there)."""
        if self.room.plan is None:
            cx, cy = self.center()
            mx, my = w.at(u)
            return 1.0 if w.normal[0] * (cx - mx) + w.normal[1] * (cy - my) > 0 else -1.0
        for side in (1.0, -1.0):
            pt = w.at(u, side * (w.thickness / 2 + PROBE))
            if self.zone_at(pt) or (not self.zones and self._interior(pt)):
                return side
        return 1.0

    def face_runs(self, face: Face, height: float) -> list[tuple[Pt, Pt, float]]:
        """Pieces of a wall face up to `height` for wainscot / baseboards: (p0, p1, top). Doors cut them;
        under a window a piece stops at the sill (none if the sill is below 1 cm)."""
        w = next((x for x in self.walls if x.id == face.wall), None)
        if w is None:
            return []
        height = min(height, w.height)

        def u_of(p: Pt) -> float:
            return (p[0] - w.a[0]) * w.dir[0] + (p[1] - w.a[1]) * w.dir[1]

        ua, ub = sorted((u_of(face.p0), u_of(face.p1)))
        n = (face.p0[0] - w.a[0]) * w.normal[0] + (face.p0[1] - w.a[1]) * w.normal[1]  # the face's side
        cuts = sorted((w.interval(o), min(height, o.resolved(w.height)[0])) for o in w.openings)
        out, u = [], ua
        for (c0, c1), top in cuts:
            c0, c1 = max(c0, ua), min(c1, ub)
            if c1 <= c0:
                continue
            if c0 > u + 1e-6:
                out.append((u, c0, height))
            if top > 0.01:
                out.append((c0, c1, top))
            u = max(u, c1)
        if ub > u + 1e-6:
            out.append((u, ub, height))
        runs: list[tuple[float, float, float]] = []
        for u0, u1, top in out:  # one piece past a high window sill
            if runs and abs(runs[-1][2] - top) < 1e-6 and abs(runs[-1][1] - u0) < 1e-6:
                runs[-1] = (runs[-1][0], u1, top)
            else:
                runs.append((u0, u1, top))
        return [(w.at(u0, n), w.at(u1, n), top) for u0, u1, top in runs if u1 - u0 > 0.01]

    def fixture_points(self, spacing: float) -> list[Pt]:
        if not self.zones or self.room.outdoor:
            x0, y0, x1, y1 = self.bounds
            pts = [(x0 + x, y0 + y) for x, y in fixture_grid(x1 - x0, y1 - y0, spacing)]
            # outdoor: no lamp posts inside buildings
            return [p for p in pts if not any(point_in_polygon(p, b.poly) for b in self.room.buildings)]
        out = []
        for poly in self.zones.values():
            xs, ys = [p[0] for p in poly], [p[1] for p in poly]
            pts = [(min(xs) + x, min(ys) + y) for x, y in fixture_grid(max(xs) - min(xs), max(ys) - min(ys), spacing)]
            pts = [p for p in pts if point_in_polygon(p, poly)]
            out += pts or [centroid(poly)]
        return out

    def summary(self) -> dict:
        """Walls, wall faces and zones in a compact form for the agent (ids to use in layouts)."""
        def area(poly):
            return abs(sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1]
                           for i in range(len(poly)))) / 2

        if self.room.outdoor:
            return {
                "size": [round(self.room.width, 3), round(self.room.depth, 3)],
                "outdoor": True,
                "zones": [{"id": a.id, "kind": a.kind, "name": a.name or a.id, "area_m2": round(area(a.poly), 2),
                           "center": [round(c, 2) for c in centroid(a.poly)], "height": a.top, "keep_clear": a.clear}
                          for a in self.room.areas],
                "buildings": [{"id": b.id, "height": b.height, "area_m2": round(area(b.poly), 2),
                               "sides": sorted({f.side for f in self.faces if f.kind == "building" and f.wall == b.id})}
                              for b in self.room.buildings],
            }
        from agentic_sim_world_gen.scene.zone_floors import zone_floors

        zf = {z["id"]: z for z in zone_floors(self.room)}
        out = {
            "size": [round(self.room.width, 3), round(self.room.depth, 3)],
            "walls": [
                {"id": w.id, "length": round(w.length, 2), "thickness": w.thickness,
                 "openings": [f"{o.kind} {o.id} @{o.offset:.2f} w{o.width:.2f}" for o in w.openings]}
                for w in self.walls if self.room.plan is not None or w.openings
            ],
            "faces": [
                {"wall": f.wall, "direction": f.direction, "zone": f.zone, "length": round(f.length, 2)}
                for f in self.faces
            ] if self.room.plan is not None else [],
            "zones": [
                {"id": z, "name": self.zone_names[z], "area_m2": round(area(p), 2),
                 "center": [round(c, 2) for c in centroid(p)],
                 **({"floor": zf[z]["surface"]["name"], "floor_from": zf[z]["how"]} if z in zf else {})}
                for z, p in self.zones.items()
            ],
        }
        if self.unknown_openings:
            out["unknown_openings"] = [o.id for o in self.unknown_openings]
        if self.site is not None:  # the outside around the room
            out["site"] = {
                "bounds": [round(v, 2) for v in self.site_bounds],
                "room_footprint": [round(v, 2) for v in self.footprint],
                "outer_walls": sorted({f.side for f in self.site_faces if f.kind == "exterior"}),
                "areas": [{"id": a.id, "kind": a.kind, "name": a.name or a.id, "area_m2": round(area(a.poly), 2),
                           "center": [round(c, 2) for c in centroid(a.poly)], "keep_clear": a.clear}
                          for a in self.areas.values()],
                "buildings": [{"id": b.id, "height": b.height,
                               "sides": sorted({f.side for f in self.site_faces if f.kind == "building" and f.wall == b.id})}
                              for b in self.buildings.values()],
            }
        return out
