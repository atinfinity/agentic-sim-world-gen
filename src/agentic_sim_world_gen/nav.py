"""Navigation map of a scene for a robot: a 2D occupancy grid in the map_server format of ROS / Nav2
(map.pgm + map.yaml) and where a robot of a given size can drive.

- The map frame is the stage's XY (Z-up, meters); map.yaml's origin is the lower left corner of the grid.
- Occupied (0): walls (openings cut out; a window counts by its sill / head height) and objects whose height
  range overlaps the robot's band [min_height, height]. Free (254): the floor inside the room. Unknown (205):
  outside the room. Outdoors the site is free, buildings and water are occupied and so are the edges of
  raised areas (curbs) higher than `step`. Indoors the edges of raised floors higher than `step` are occupied, and
  so are stairs and railings.
- "bbox" maps use each object's bounding box (the space under a desk is blocked: the safe side). "isaac" maps
  take the occupancy from Isaac Sim's Occupancy Map extension (rays against the colliders, scripts/isaac/nav_map.py)
  and keep the room's inside / outside from here.
- Reach: the robot's center may stand where the nearest blocked cell is at least `radius` away; from the doors
  (or `start`, or the largest open area) it reaches a connected part of that. Zones and floor objects it cannot
  get near (within `radius` + APPROACH of the footprint) and open pockets it cannot get to are reported.

Pure Python (no numpy, Pillow for the PNG).
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agentic_sim_world_gen.layout.room import point_in_polygon

if TYPE_CHECKING:
    from agentic_sim_world_gen.scene.spec import ResolvedScene

Pt = tuple[float, float]

FREE, OCCUPIED, UNKNOWN = 254, 0, 205  # map.pgm values (trinary, negate 0)
RESOLUTION = 0.05
MAX_CELLS = 2_000_000  # a coarser cell beyond this (a 70 m x 70 m site at 5 cm)
APPROACH = 0.25  # the robot's edge this close to an object's footprint counts as reaching it
POCKET_M2 = 0.5  # smaller free pockets the robot cannot get to are not reported
WATER = {"water"}
INF = 1e18


@dataclass
class Robot:
    radius: float = 0.3  # [m] circle around the footprint
    height: float = 1.2  # [m] top of the robot: higher objects (table tops above it) do not block
    min_height: float = 0.05  # [m] lower objects (rugs, mats, door sills) do not block
    step: float = 0.02  # [m] a raised area's / floor level's edge higher than this blocks (curbs, steps)

    def band(self) -> tuple[float, float]:
        return (self.min_height, self.height)


@dataclass
class Grid:
    x0: float
    y0: float
    res: float
    nx: int
    ny: int
    cells: list[list[int]]  # [j][i], j = y index from the bottom
    owner: dict[tuple[int, int], str] = field(default_factory=dict)  # occupied cell -> object / wall id
    source: str = "bbox"

    def center(self, i: int, j: int) -> Pt:
        return (self.x0 + (i + 0.5) * self.res, self.y0 + (j + 0.5) * self.res)

    def cell(self, p: Pt) -> tuple[int, int] | None:
        i, j = int(math.floor((p[0] - self.x0) / self.res)), int(math.floor((p[1] - self.y0) / self.res))
        return (i, j) if 0 <= i < self.nx and 0 <= j < self.ny else None

    def cells_in(self, poly: list[Pt], tol: float = 0.0):
        xs, ys = [x for x, _ in poly], [y for _, y in poly]
        i0 = max(0, int((min(xs) - tol - self.x0) / self.res))
        i1 = min(self.nx - 1, int((max(xs) + tol - self.x0) / self.res))
        j0 = max(0, int((min(ys) - tol - self.y0) / self.res))
        j1 = min(self.ny - 1, int((max(ys) + tol - self.y0) / self.res))
        for j in range(j0, j1 + 1):
            for i in range(i0, i1 + 1):
                if point_in_polygon(self.center(i, j), poly, tol):
                    yield i, j

    def count(self, value: int) -> int:
        return sum(row.count(value) for row in self.cells)

    def bounds(self) -> tuple[float, float, float, float]:
        return (self.x0, self.y0, self.x0 + self.nx * self.res, self.y0 + self.ny * self.res)


def _overlaps(z: tuple[float, float], band: tuple[float, float]) -> bool:
    return z[0] < band[1] and z[1] > band[0]


def _extent(geom) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = geom.bounds if geom.room.outdoor else geom.slab
    for w in geom.walls:
        for x, y in w.rect(-w.ext_a, w.length + w.ext_b, -w.thickness / 2, w.thickness / 2):
            x0, y0, x1, y1 = min(x0, x), min(y0, y), max(x1, x), max(y1, y)
    return x0, y0, x1, y1


def build(scene: ResolvedScene, robot: Robot | None = None, resolution: float = RESOLUTION) -> tuple[Grid, list[str]]:
    """Occupancy grid of the scene from the objects' bounding boxes. Returns (grid, warnings)."""
    from agentic_sim_world_gen.layout.room import RoomGeometry
    from agentic_sim_world_gen.layout.usability import _units

    robot = robot or Robot()
    band = robot.band()
    warnings: list[str] = []
    geom = RoomGeometry(scene.spec.room)
    x0, y0, x1, y1 = _extent(geom)
    res = resolution
    if (x1 - x0) * (y1 - y0) / res**2 > MAX_CELLS:
        res = math.ceil(math.sqrt((x1 - x0) * (y1 - y0) / MAX_CELLS) * 100) / 100
        warnings.append(f"the map is large: cells of {res} m instead of {resolution} m")
    pad = res  # one cell of unknown around the walls
    x0, y0 = x0 - pad, y0 - pad
    nx, ny = int(math.ceil((x1 + pad - x0) / res)), int(math.ceil((y1 + pad - y0) / res))
    g = Grid(x0, y0, res, nx, ny, [[UNKNOWN] * nx for _ in range(ny)])
    room = scene.spec.room

    def fill(poly, value, who=None, tol=0.0):
        for i, j in g.cells_in(poly, tol):
            g.cells[j][i] = value
            if who is not None:
                g.owner[(i, j)] = who
            else:
                g.owner.pop((i, j), None)

    # free: the floor inside the room (zones, or the interior) and the doorways
    if room.outdoor:
        fill([(0.0, 0.0), (room.width, 0.0), (room.width, room.depth), (0.0, room.depth)], FREE)
    elif geom.zones:
        for poly in geom.zones.values():
            fill(poly, FREE)
    elif room.plan is not None:
        for j in range(ny):
            for i in range(nx):
                if geom._interior(g.center(i, j)):
                    g.cells[j][i] = FREE
    else:
        fill([(0.0, 0.0), (room.width, 0.0), (room.width, room.depth), (0.0, room.depth)], FREE)
    for w in geom.walls:
        for o in w.openings:
            if o.kind == "window":
                continue
            sill, _ = o.resolved(w.height)
            if sill <= band[0]:
                fill(w.rect(*w.interval(o), -w.thickness / 2 - res, w.thickness / 2 + res), FREE)
    # occupied: walls, outdoor buildings / water / curb edges, objects
    for w in geom.walls:
        for u0, u1, z0, z1, _ in w.pieces():
            if _overlaps((z0, z1), band):
                fill(w.rect(u0, u1, -w.thickness / 2, w.thickness / 2), OCCUPIED, f"wall:{w.id}", res / 2)
    if room.outdoor:
        for b in room.buildings:
            fill(b.poly, OCCUPIED, f"building:{b.id}", res / 2)
        for a in room.areas:
            if a.kind in WATER:
                fill(a.poly, OCCUPIED, f"area:{a.id}")
            elif a.top > robot.step:
                _edges(g, a.poly, f"curb:{a.id}")
    else:  # raised floors: their edges block like curbs; stairs and railings are solid
        from agentic_sim_world_gen.scene import levels as lv

        for L in room.levels:
            if L.height > robot.step:
                _edges(g, L.poly, f"level:{L.id}")
        for s in room.stairs:
            fill(lv.footprint(lv.flight(room, s), s.width), OCCUPIED, f"stairs:{s.id}", res / 2)
        for r in lv.rails(room):
            fill(lv.rail_rect(r), OCCUPIED, f"rail:{r['owner']}", res / 2)
        for c in room.columns:  # columns
            fill(c.poly, OCCUPIED, f"column:{c.id}", res / 2)
    for u in _units(scene):
        if u.o.layout and u.o.layout.ceiling:
            continue
        if _overlaps((u.z, u.top), band):
            fill(u.poly, OCCUPIED, u.id, res / 2)
    return g, warnings


def _edges(g: Grid, poly: list[Pt], who: str) -> None:
    """Mark the cells along a polygon's outline (a curb: the raised area itself is drivable)."""
    n = len(poly)
    for k in range(n):
        (ax, ay), (bx, by) = poly[k], poly[(k + 1) % n]
        steps = max(1, int(math.dist((ax, ay), (bx, by)) / (g.res / 2)))
        for s in range(steps + 1):
            t = s / steps
            c = g.cell((ax + t * (bx - ax), ay + t * (by - ay)))
            if c is not None and g.cells[c[1]][c[0]] != UNKNOWN:
                g.cells[c[1]][c[0]] = OCCUPIED
                g.owner[c] = who


def with_occupancy(g: Grid, raw: dict[str, Any]) -> Grid:
    """The bbox grid's inside / outside with the occupancy of an Isaac Sim map (scripts/isaac/nav_map.py:
    {"min": [x, y], "res", "dims": [w, h], "occupied": [cell indices, row-major from min]}).
    Free cells the Isaac map calls occupied become occupied (owner from the bbox map if it had one there,
    else "isaac"); occupied bbox cells the Isaac map calls free become free."""
    mx, my = raw["min"][0], raw["min"][1]
    r = raw["res"]
    w, h = raw["dims"][0], raw["dims"][1]
    occ = set(raw["occupied"])
    out = Grid(g.x0, g.y0, g.res, g.nx, g.ny, [row[:] for row in g.cells], source="isaac")
    for j in range(g.ny):
        for i in range(g.nx):
            if g.cells[j][i] == UNKNOWN:
                continue
            x, y = g.center(i, j)
            a, b = int(math.floor((x - mx) / r)), int(math.floor((y - my) / r))
            if not (0 <= a < w and 0 <= b < h):
                continue
            hit = (b * w + a) in occ
            out.cells[j][i] = OCCUPIED if hit else FREE
            if hit:
                out.owner[(i, j)] = g.owner.get((i, j), "isaac")
    return out


# --- reach ----------------------------------------------------------------------------------------------


def _edt(seed: list[list[bool]]) -> list[list[float]]:
    """Distance (in cells) from every cell to the nearest seed cell (INF if there is none)."""
    from agentic_sim_world_gen.layout.widths import edt as _edt

    if not any(any(r) for r in seed):
        return [[INF] * len(seed[0]) for _ in seed]
    return _edt(seed)


def _components(ok: list[list[bool]]) -> tuple[list[list[int]], list[int]]:
    ny, nx = len(ok), len(ok[0])
    comp = [[-1] * nx for _ in range(ny)]
    sizes: list[int] = []
    for j in range(ny):
        for i in range(nx):
            if not ok[j][i] or comp[j][i] >= 0:
                continue
            n, q, size = len(sizes), deque([(i, j)]), 0
            comp[j][i] = n
            while q:
                a, b = q.popleft()
                size += 1
                for da, db in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    x, y = a + da, b + db
                    if 0 <= x < nx and 0 <= y < ny and ok[y][x] and comp[y][x] < 0:
                        comp[y][x] = n
                        q.append((x, y))
            sizes.append(size)
    return comp, sizes


def entrances(scene: ResolvedScene) -> list[tuple[Pt, str]]:
    from agentic_sim_world_gen.layout.room import RoomGeometry
    from agentic_sim_world_gen.layout.usability import _entrances_all

    return _entrances_all(RoomGeometry(scene.spec.room))


def reach(g: Grid, scene: ResolvedScene, robot: Robot | None = None, start: Pt | None = None) -> dict[str, Any]:
    """Where the robot's center can go from `start` (or the doors, or the largest open area) and what it
    cannot get near. Returns the report; the masks are under the private keys _center / _covered."""
    from agentic_sim_world_gen.layout.room import RoomGeometry
    from agentic_sim_world_gen.layout.usability import _units

    robot = robot or Robot()
    c = g.res
    blocked = [[v != FREE for v in row] for row in g.cells]
    clear = [[d * c for d in row] for row in _edt(blocked)]  # center to the nearest blocked cell center
    # a blocked cell's edge is half a cell nearer than its center
    ok = [[g.cells[j][i] == FREE and clear[j][i] - c / 2 >= robot.radius - 1e-9 for i in range(g.nx)]
          for j in range(g.ny)]
    comp, sizes = _components(ok)
    starts = [(tuple(start), "start")] if start is not None else entrances(scene)
    keep: set[int] = set()
    used: list[dict[str, Any]] = []
    look = max(1, int(math.ceil(1.0 / c)))  # a door point may sit in a narrow doorway: look 1 m around
    for p, sid in starts:
        cc = g.cell(p)
        if cc is None:
            continue
        best = None
        for j in range(max(0, cc[1] - look), min(g.ny, cc[1] + look + 1)):
            for i in range(max(0, cc[0] - look), min(g.nx, cc[0] + look + 1)):
                if comp[j][i] >= 0 and (d := math.dist(g.center(i, j), p)) <= 1.0 and (best is None or d < best[0]):
                    best = (d, comp[j][i], g.center(i, j))
        if best:
            keep.add(best[1])
            used.append({"id": sid, "at": [round(best[2][0], 3), round(best[2][1], 3)]})
        else:
            used.append({"id": sid, "at": None, "note": "no room for the robot within 1 m of it"})
    origin = "start" if start is not None else ("doors" if starts else "largest open area")
    if not starts and sizes:
        k = max(range(len(sizes)), key=sizes.__getitem__)
        keep.add(k)
        i, j = max(((i, j) for j in range(g.ny) for i in range(g.nx) if comp[j][i] == k),
                   key=lambda c2: clear[c2[1]][c2[0]])  # the most open spot of it
        used.append({"id": "open_area", "at": [round(x, 3) for x in g.center(i, j)]})
    center = [[comp[j][i] in keep for i in range(g.nx)] for j in range(g.ny)]
    to_center = _edt(center)
    covered = [[g.cells[j][i] == FREE and to_center[j][i] * c <= robot.radius + c / 2 for i in range(g.nx)]
               for j in range(g.ny)]
    free = g.count(FREE)
    ncov = sum(r.count(True) for r in covered)
    a = c * c
    rep: dict[str, Any] = {
        "robot": {"radius": robot.radius, "height": robot.height, "min_height": robot.min_height,
                  **({"step": robot.step} if scene.spec.room.outdoor else {})},
        "from": origin,
        "starts": used,
        "free_m2": round(free * a, 2),
        "reachable_m2": round(ncov * a, 2),
        "reachable": round(ncov / free, 3) if free else 0.0,
    }
    # zones the robot cannot get into, and how wide the way into each zone is at its narrowest (the same
    # widest-way map as the usability check's narrow_aisle, on this grid)
    geom = RoomGeometry(scene.spec.room)
    zones = []
    ways = _ways(g, clear, [x["at"] for x in used if x.get("at")], geom, robot) if geom.zones else {}
    for zid, poly in geom.zones.items():
        cells = list(g.cells_in(poly))
        if cells and not any(center[j][i] for i, j in cells):
            zones.append({"id": zid, "name": geom.zone_names.get(zid, zid),
                          **({"widest_m": ways[zid]["width_m"], "narrowest_at": ways[zid]["at"]} if zid in ways else {})})
    if zones:
        rep["unreachable_zones"] = zones
    if ways:
        from agentic_sim_world_gen.layout.usability import need_width

        # the width the scene's purpose asks for (a robot: its width + 0.4 m), never under the robot's own
        need = max(need_width(scene), 2 * robot.radius)
        rep["need_width"] = need
        rep["routes"] = [{"to": zid, **w, **({"tight": True} if w["width_m"] < need else {})} for zid, w in ways.items()]
    # floor objects the robot cannot get near
    near = (robot.radius + APPROACH) / c + 1.0
    objs = []
    for u in _units(scene):
        if not u.floor or (u.o.layout and u.o.layout.ceiling) or u.o.decor:
            continue
        cells = list(g.cells_in(u.poly, c / 2))
        if cells and not any(to_center[j][i] <= near for i, j in cells):
            gap = min(to_center[j][i] for i, j in cells)
            objs.append({"id": u.id, "query": u.o.query,
                         **({"nearest_m": round(gap * c - robot.radius, 2)} if gap < INF / 2 else {})})
    if objs:
        rep["unreachable_objects"] = objs
    # open areas the robot fits in but cannot get to (behind furniture)
    pockets = []
    for k, size in enumerate(sizes):
        if k in keep or size * a < POCKET_M2:
            continue
        pts = [g.center(i, j) for j in range(g.ny) for i in range(g.nx) if comp[j][i] == k]
        cx, cy = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
        pockets.append({"at": [round(cx, 2), round(cy, 2)], "m2": round(size * a, 2)})
    if pockets:
        rep["pockets"] = sorted(pockets, key=lambda p: -p["m2"])
    rep["_center"], rep["_covered"] = center, covered
    return rep


WAYS_MAX_CELLS = 400_000  # the widest-way search is skipped on larger maps (pure Python)


def _ways(g: Grid, clear: list[list[float]], starts: list[Any], geom, robot: Robot) -> dict[str, dict[str, Any]]:
    """{zone id: {width_m, at}}: the narrowest spot of the widest way from the starts into each zone (zones no
    way leads into at all are left out). width_m is the free width there."""
    from agentic_sim_world_gen.layout.widths import narrowest, widest

    if g.nx * g.ny > WAYS_MAX_CELLS or not starts:
        return {}
    c = g.res
    # center to the nearest blocked cell's edge, as in reach()
    cl = [[clear[j][i] - c / 2 if g.cells[j][i] == FREE else 0.0 for i in range(g.nx)] for j in range(g.ny)]
    seeds = [s for s in (g.cell(tuple(p)) for p in starts) if s is not None]
    best, prev = widest(cl, seeds)
    out = {}
    for zid, poly in geom.zones.items():
        cells = [x for x in g.cells_in(poly) if best[x[1]][x[0]] >= 0]
        if not cells:
            continue
        at, low = narrowest(cl, prev, max(cells, key=lambda x: best[x[1]][x[0]]))
        x, y = g.center(*at)
        out[zid] = {"width_m": round(2 * low, 2), "at": [round(x, 2), round(y, 2)]}
    return out


def summary(rep: dict[str, Any]) -> str:
    s = f"robot r {rep['robot']['radius']} m reaches {rep['reachable']:.0%} of {rep['free_m2']} m² free floor"
    if z := rep.get("unreachable_zones"):
        s += f"; zones out of reach: {', '.join(x['name'] for x in z)}"
    if o := rep.get("unreachable_objects"):
        s += f"; cannot get near {len(o)} object(s): {', '.join(x['id'] for x in o[:5])}" + ("…" if len(o) > 5 else "")
    if t := [x for x in rep.get("routes", []) if x.get("tight") and x["width_m"] >= 2 * rep["robot"]["radius"]]:
        s += "; tight on the way to " + ", ".join(f"{x['to']} ({x['width_m']:g} m at {x['at'][0]:g}, {x['at'][1]:g})" for x in t[:5])
    if p := rep.get("pockets"):
        s += f"; {len(p)} open pocket(s) it cannot get to"
    return s


# --- files ---------------------------------------------------------------------------------------------


def write_pgm(g: Grid, path: Path) -> None:
    """Binary PGM, first row = top of the map (largest y), as map_server reads it."""
    data = bytearray()
    for j in range(g.ny - 1, -1, -1):
        data += bytes(g.cells[j])
    path.write_bytes(f"P5\n# agentic-sim-world-gen nav map ({g.source})\n{g.nx} {g.ny}\n255\n".encode() + bytes(data))


def write_yaml(g: Grid, path: Path, image: str) -> None:
    path.write_text(
        f"image: {image}\nmode: trinary\nresolution: {g.res}\norigin: [{g.x0:.4f}, {g.y0:.4f}, 0.0]\n"
        "negate: 0\noccupied_thresh: 0.65\nfree_thresh: 0.25\n"
    )


COLORS = {
    "unknown": (205, 205, 205),
    "occupied": (40, 40, 40),
    "reach": (190, 228, 196),  # floor the robot sweeps
    "free": (255, 255, 255),  # floor it cannot reach
    "center": (120, 190, 135),  # where its center can go
    "pocket": (240, 190, 170),  # open floor it fits in but cannot get to
    "start": (30, 100, 220),
}


def image(g: Grid, rep: dict[str, Any] | None, px: int = 900):
    """RGB picture of the map (y up) with the robot's reach, scaled up to about `px` on the long side."""
    from PIL import Image, ImageDraw

    s = max(1, px // max(g.nx, g.ny))
    im = Image.new("RGB", (g.nx, g.ny))
    pix = im.load()
    center = rep.get("_center") if rep else None
    covered = rep.get("_covered") if rep else None
    for j in range(g.ny):
        for i in range(g.nx):
            v = g.cells[j][i]
            if v == UNKNOWN:
                col = COLORS["unknown"]
            elif v == OCCUPIED:
                col = COLORS["occupied"]
            elif center and center[j][i]:
                col = COLORS["center"]
            elif covered and covered[j][i]:
                col = COLORS["reach"]
            else:
                col = COLORS["free"]
            pix[i, g.ny - 1 - j] = col
    im = im.resize((g.nx * s, g.ny * s), Image.NEAREST)
    dr = ImageDraw.Draw(im)
    if rep:
        for p in rep.get("pockets", []):
            cc = g.cell(tuple(p["at"]))
            if cc:
                x, y = (cc[0] + 0.5) * s, (g.ny - 0.5 - cc[1]) * s
                dr.ellipse((x - 6, y - 6, x + 6, y + 6), outline=(200, 60, 40), width=3)
        for st in rep.get("starts", []):
            if st.get("at"):
                cc = g.cell(tuple(st["at"]))
                if cc:
                    x, y = (cc[0] + 0.5) * s, (g.ny - 0.5 - cc[1]) * s
                    r = max(4, robot_px(rep, g, s))
                    dr.ellipse((x - r, y - r, x + r, y + r), outline=COLORS["start"], width=3)
    return im


def robot_px(rep: dict[str, Any], g: Grid, s: int) -> int:
    return int(rep["robot"]["radius"] / g.res * s)


def write(g: Grid, rep: dict[str, Any], out_dir: Path, stem: str = "map") -> dict[str, str]:
    """map.pgm, map.yaml, map.png and nav.json (the report) in out_dir; `stem` names them."""
    import json

    out_dir.mkdir(parents=True, exist_ok=True)
    pgm, yml, png = out_dir / f"{stem}.pgm", out_dir / f"{stem}.yaml", out_dir / f"{stem}.png"
    write_pgm(g, pgm)
    write_yaml(g, yml, pgm.name)
    image(g, rep).save(png)
    js = out_dir / ("nav.json" if stem == "map" else f"nav_{stem.removeprefix('map_')}.json")
    body = {k: v for k, v in rep.items() if not k.startswith("_")}
    body.update(source=g.source, resolution=g.res, origin=[round(g.x0, 4), round(g.y0, 4)], size=[g.nx, g.ny],
                files={"pgm": pgm.name, "yaml": yml.name, "png": png.name})
    js.write_text(json.dumps(body, ensure_ascii=False, indent=1))
    return {"pgm": str(pgm), "yaml": str(yml), "png": str(png), "json": str(js)}
