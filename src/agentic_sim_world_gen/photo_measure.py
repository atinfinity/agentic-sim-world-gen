"""Room and aisle widths as a photo's camera sees them: pure Python, no Isaac Sim.

compare_photo only shows the photo next to its render; nobody compared sizes, and renders came out with aisles
twice as wide as the photo. This measures, from the scene and the photo's camera:

- `across`: the free width across the view (left blocker ↔ right blocker, perpendicular to the view on the floor)
  at the camera and at a few distances ahead up to the far wall, with what blocks each side (a wall, a shelf
  unit, stairs ...); where a table or island splits the line, every free gap (`gaps`);
- `objects`: what the camera sees, left to right, with its box in the photo's pixels and its distance (array
  units are counted per array, e.g. "east_books: 5 of 7 units in view");
- `floor_pairs`: for pixel pairs the caller read off the photo (e.g. the bottom of the left and right shelf fronts
  at the same depth), the floor points under them and the metric distance, next to the scene's free width on
  the same line. A photo aisle of 2.6 m against a scene aisle of 5.1 m means the scene is too wide.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

Pt = tuple[float, float]
AHEAD = (1.0, 2.0, 3.5, 5.0, 7.0, 10.0)  # distances ahead of the camera to measure the width at [m]
BLOCK_BELOW = 1.0  # things whose bottom is below this block a walking line on the floor [m]
MIN_GAP = 0.3  # narrower free stretches (behind a shelf, between two units) are not aisles [m]
TILT_ERR = 2.0  # how far the tilt read off the photo's lines may be off [deg]
TILT_ERR_UNREAD = 5.0  # ... and a tilt nobody read off the photo (the camera as set_input guessed it) [deg]
HEIGHT_ERR = 0.1  # how far the camera's height may be off (share of the height)
WIDE_RANGE = 0.5  # a range wider than this share of the width is too loose to act on: count shelf units instead
TILT_OFF = 2.0  # the camera's tilt differs from the photo's lines by more than this: say so [deg]
OUTLIER = 0.25  # an estimate this far (share) from the median of the estimates is named
DEPTH_SCALE = (0.85, 1.24)  # a monocular depth map's scale per photo is off by -15 .. +24 % (agentic_sim_world_gen.depth)
# aisle widths a kind of store usually has [m] (.claude/skills/world-gen/shops.md); a single figure there is +-20 %
TYPICAL_AISLE = {"supermarket": (1.8, 2.4), "drugstore": (1.5, 1.8), "home_center": (2.5, 3.0),
                 "hundred_yen_shop": (1.0, 1.4), "electronics_store": (1.6, 2.4), "garden_center": (1.0, 1.4),
                 "sporting_goods_store": (1.0, 1.4), "furniture_store": (1.0, 1.4)}
TYPICAL_SLACK = 0.2  # outside the typical range by more than this share: say so
# Things whose size is known well enough to set a photo's scale: kind -> (size, smallest, largest [m],
# what is measured). Rough catalog sizes (Japan); `size_m` in the entry replaces them.
KNOWN_SIZES: dict[str, tuple[float, float, float, str]] = {
    "shelf_unit": (0.9, 0.85, 0.95, "one shelf / gondola unit's width (1.2 m units exist: give size_m)"),
    "door": (0.85, 0.8, 0.9, "a single door leaf's width"),
    "basket": (0.48, 0.42, 0.52, "a shopping basket's longer side"),
    "cart": (0.5, 0.45, 0.6, "a shopping cart's width"),
    "pallet": (1.1, 1.0, 1.2, "a pallet's side (JIS 1.1 x 1.1, EU 1.2 x 0.8: give size_m)"),
}
TILE_SIZES = (0.3, 0.45, 0.6)  # floor tiles [m]: the one nearest the geometry's width is taken unless size_m says
TILE_UNSURE = 0.15  # ... and called unsure when the geometry is this far (share) from what that tile gives
KIND_ALIASES = {"shelf": "shelf_unit", "gondola": "shelf_unit", "棚": "shelf_unit", "ゴンドラ": "shelf_unit", "ドア": "door",
                "tile": "floor_tile", "tiles": "floor_tile", "タイル": "floor_tile", "床タイル": "floor_tile",
                "カゴ": "basket", "かご": "basket", "買い物カゴ": "basket", "カート": "cart", "パレット": "pallet"}
SCALE_OFF = 0.1  # known sizes put the geometry's scale off by more than this share: say so
REFS_APART = 0.25  # two known sizes give scales this far apart (share): say so


def _norm(v):
    n = math.sqrt(sum(c * c for c in v)) or 1.0
    return tuple(c / n for c in v)


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


class View:
    """A pinhole camera from PhotoCamera (position, look_at, hfov_deg) and the image size."""

    def __init__(self, cam, size: tuple[int, int], pitch_deg: float | None = None, roll_deg: float = 0.0,
                 hfov_deg: float | None = None, height: float | None = None):
        """pitch_deg / roll_deg / hfov_deg / height replace what the camera says (the tilt read off the photo's
        lines); the position on the floor and the direction on the floor stay the camera's."""
        self.pos = tuple(cam.position) if height is None else (cam.position[0], cam.position[1], height)
        fw = _norm(tuple(b - a for a, b in zip(cam.position, cam.look_at)))
        flat = math.hypot(fw[0], fw[1])
        self.fw2 = _norm((fw[0], fw[1])) if flat > 1e-9 else (1.0, 0.0)  # on the floor: the view direction
        self.rt2 = (self.fw2[1], -self.fw2[0])  # ... and its right
        self.pitch = math.atan2(fw[2], flat) if pitch_deg is None else math.radians(pitch_deg)
        self.roll = math.radians(roll_deg)
        cp, sp, cr, sr = math.cos(self.pitch), math.sin(self.pitch), math.cos(self.roll), math.sin(self.roll)
        self.fw = (cp * self.fw2[0], cp * self.fw2[1], sp)
        rt0 = (self.rt2[0], self.rt2[1], 0.0)
        up0 = _cross(rt0, self.fw)
        self.rt = tuple(cr * a + sr * b for a, b in zip(rt0, up0))  # roll: clockwise as the camera sees it
        self.up = tuple(cr * b - sr * a for a, b in zip(rt0, up0))
        self.size = size
        self.hfov = cam.hfov_deg if hfov_deg is None else hfov_deg
        self.f = size[0] / 2 / math.tan(math.radians(self.hfov) / 2)

    def project(self, X) -> tuple[float, float, float] | None:
        d = tuple(x - p for x, p in zip(X, self.pos))
        depth = _dot(d, self.fw)
        if depth < 0.05:
            return None
        return (self.size[0] / 2 + self.f * _dot(d, self.rt) / depth,
                self.size[1] / 2 - self.f * _dot(d, self.up) / depth, depth)

    def floor_point(self, u: float, v: float, z: float = 0.0) -> Pt | None:
        """Where the ray through pixel (u, v) meets the plane at height z (None above the horizon)."""
        x, y = (u - self.size[0] / 2) / self.f, (v - self.size[1] / 2) / self.f
        ray = tuple(self.fw[i] + x * self.rt[i] - y * self.up[i] for i in range(3))
        if abs(ray[2]) < 1e-9 or (t := (z - self.pos[2]) / ray[2]) <= 0:
            return None
        return (self.pos[0] + t * ray[0], self.pos[1] + t * ray[1])


def _hits(o: Pt, d: Pt, poly: list[Pt]) -> list[float]:
    """Parameters s of the line o + s d where it crosses the polygon's edges."""
    out = []
    for i in range(len(poly)):
        a, b = poly[i], poly[(i + 1) % len(poly)]
        e = (b[0] - a[0], b[1] - a[1])
        den = d[0] * e[1] - d[1] * e[0]
        if abs(den) < 1e-12:
            continue
        w = (a[0] - o[0], a[1] - o[1])
        s = (w[0] * e[1] - w[1] * e[0]) / den
        t = (w[0] * d[1] - w[1] * d[0]) / den
        if -1e-9 <= t <= 1 + 1e-9:
            out.append(s)
    return out


class Blockers:
    """What stops a line on the floor: the room's walls and the footprints standing on the floor."""

    def __init__(self, scene):
        from agentic_sim_world_gen.layout.room import RoomGeometry, point_in_polygon
        from agentic_sim_world_gen.layout.usability import _units

        self.inside = point_in_polygon
        room = scene.spec.room
        geom = RoomGeometry(room)
        self.items: list[tuple[str, list[Pt]]] = []
        if room.plan is None:
            x0, y0, x1, y1 = geom.bounds
            self.items += [("wall:south", [(x0 - 1, y0 - 1), (x1 + 1, y0 - 1), (x1 + 1, y0), (x0 - 1, y0)]),
                           ("wall:north", [(x0 - 1, y1), (x1 + 1, y1), (x1 + 1, y1 + 1), (x0 - 1, y1 + 1)]),
                           ("wall:west", [(x0 - 1, y0 - 1), (x0, y0 - 1), (x0, y1 + 1), (x0 - 1, y1 + 1)]),
                           ("wall:east", [(x1, y0 - 1), (x1 + 1, y0 - 1), (x1 + 1, y1 + 1), (x1, y1 + 1)])]
        for ob in geom.obstacles:
            if ob.kind not in ("door", "keep_clear", "curb") and ob.z[0] < BLOCK_BELOW:
                self.items.append((ob.id.split("#")[0], ob.poly))
        self.units = [u for u in _units(scene) if u.z < BLOCK_BELOW and u.top > 0.3]
        self.items += [(u.id, u.poly) for u in self.units]

    def at(self, p: Pt) -> str | None:
        return next((n for n, poly in self.items if self.inside(p, poly)), None)

    def span(self, p: Pt, d: Pt, limit: float = 50.0, walls: bool = False
             ) -> tuple[tuple[float, str | None], tuple[float, str | None]]:
        """Free distance from p along -d (left) and +d (right) to the first blocker (or wall), and its name."""
        left, right = (limit, None), (limit, None)
        for name, poly in self.items:
            if walls and not name.startswith("wall"):
                continue
            for s in _hits(p, d, poly):
                if 0 < s < right[0]:
                    right = (s, name)
                elif 0 < -s < left[0]:
                    left = (-s, name)
        return left, right

    def gaps(self, p: Pt, d: Pt) -> list[dict[str, Any]]:
        """Free stretches of the line p + s d between the walls: each with its ends (s, left of p negative),
        width and what bounds it. Tables and islands in the middle split the line into several gaps."""
        (l, ln), (r, rn) = self.span(p, d, walls=True)
        lo, hi = -l, r
        occ: list[tuple[float, float, str]] = []
        for name, poly in self.items:
            if name in (ln, rn):
                continue
            ss = _hits(p, d, poly)
            if len(ss) >= 2:
                a, b = max(min(ss), lo), min(max(ss), hi)
                if a < b:
                    occ.append((a, b, name))
        occ.sort()
        out, at, by = [], lo, ln
        for a, b, name in occ + [(hi, hi, rn)]:
            if a > at + MIN_GAP:
                out.append({"from": round(at, 2), "to": round(a, 2), "width": round(a - at, 2),
                            "left_by": by, "right_by": name})
            if b >= at:
                at, by = b, name
        return out


LINE_REACH = 0.1  # a floor pair's line counts a free gap it reaches this far into [m]


def _line_width(bl: Blockers, a: Pt, b: Pt) -> tuple[float, list[str | None]]:
    """The scene's free width on the line through floor points a and b: from the blocker left of a to the one
    right of b (a table between them does not count; a point just inside a shelf front still does)."""
    mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
    d = _norm((b[0] - a[0], b[1] - a[1]))
    dist = math.dist(a, b)
    gaps = bl.gaps(mid, d) or [{"from": -dist / 2, "to": dist / 2, "left_by": None, "right_by": None}]
    # a point inside a fixture (the scene's aisle is narrower than the photo's) belongs to the gap between the
    # points, not to the one behind the fixture: gaps the segment does not reach into are left out
    gaps = [g for g in gaps if min(g["to"], dist / 2) - max(g["from"], -dist / 2) > LINE_REACH] or gaps
    ga = min(gaps, key=lambda g: max(g["from"] - -dist / 2, -dist / 2 - g["to"], 0.0))
    gb = min(gaps, key=lambda g: max(g["from"] - dist / 2, dist / 2 - g["to"], 0.0))
    if gb["to"] < ga["from"]:
        ga, gb = gb, ga
    return gb["to"] - ga["from"], [ga["left_by"], gb["right_by"]]


def line_width(scene, a: Pt, b: Pt) -> tuple[float, list[str | None]]:
    """(the scene's free width [m] on the line through floor points a and b, what bounds it): the width to set
    against one measured in a photo between the same two points."""
    w, by = _line_width(Blockers(scene), tuple(a), tuple(b))
    return round(w, 2), by


def measure(scene, input_id: str | None = None, floor_pairs: list[list[float]] | None = None,
            known: list[dict[str, Any]] | None = None, scene_dir: str | Path | None = None) -> dict[str, Any]:
    photos = [i for i in scene.spec.inputs if i.camera is not None and (input_id is None or i.id == input_id)]
    if not photos:
        return {"ok": False, "error": f"no input {input_id or ''} with a camera; set one with set_input"}
    if scene.spec.room.outdoor:
        return {"ok": False, "error": "measure_photo is for indoor rooms"}
    from agentic_sim_world_gen.layout.usability import _units

    inp = photos[0]
    size = tuple(inp.size_px) if inp.size_px else (1600, 1200)
    view = View(inp.camera, size)  # the camera as set: what compare_photo renders with
    lines = _lines(inp, view)  # the tilt the photo's own lines give, None without the file / OpenCV
    read = lines is not None and lines.get("ok")
    # pixels read off the photo go through the photo's tilt when its lines gave one
    tilt = {"pitch_deg": lines["pitch_deg"], "roll_deg": lines["roll_deg"]} if read else {}
    pview = View(inp.camera, size, **tilt)
    bl = Blockers(scene)
    place = scene.spec.room.place
    cam2 = (view.pos[0], view.pos[1])

    def across(p: Pt) -> dict[str, Any]:
        (l, ln), (r, rn) = bl.span(p, view.rt2)
        row = {"width": round(l + r, 2), "left": round(l, 2), "right": round(r, 2), "left_by": ln, "right_by": rn}
        if (on := bl.at(p)) is not None:  # the view's center line crosses a table / island here
            row = {"width": None, "on": on}
        gaps = bl.gaps(p, view.rt2)
        if len(gaps) > 1 or on is not None:
            row["gaps"] = gaps
        return row

    out: dict[str, Any] = {"ok": True, "input": inp.id, "size_px": list(size), "place": place,
                           "camera": {"position": [round(c, 2) for c in view.pos],
                                      "facing_deg": round(math.degrees(math.atan2(view.fw2[1], view.fw2[0])), 1),
                                      "pitch_deg": round(math.degrees(view.pitch), 1),
                                      "hfov_deg": inp.camera.hfov_deg}}
    if lines is not None:
        out["photo_lines"] = lines
    stand = across(cam2)
    # where level lines running away from the camera meet in the image (the horizon): compare with the photo's
    # vanishing point of the aisle's floor / shelf lines before trusting floor_pairs (a wrong tilt skews them)
    hz = view.project(tuple(view.pos[i] + 1000.0 * (view.fw2 + (0.0,))[i] for i in range(3)))
    out["camera"]["horizon_v_px"] = round(hz[1]) if hz else None
    out["camera"]["stands"] = stand
    # straight ahead: how far the floor goes, then the width across at distances up to the far wall
    _, (ahead, ahead_by) = bl.span(cam2, view.fw2)
    out["ahead"] = {"free_m": round(ahead, 2), "blocked_by": ahead_by}
    far = min((s for n, poly in bl.items if n.startswith("wall") for s in _hits(cam2, view.fw2, poly) if s > 0),
              default=max(AHEAD) + 1)
    rows = []
    for t in AHEAD:
        if t >= far:
            break
        p = (cam2[0] + t * view.fw2[0], cam2[1] + t * view.fw2[1])
        rows.append({"ahead_m": t, **across(p)})
    out["across"] = rows
    # the same widths straight from the photo's depth map, when it has one
    if (df := depth_floor(inp, view)) is not None:
        by_t = {r["ahead_m"]: r for r in rows}
        for r in df.get("across", []):
            if (s := by_t.get(r["ahead_m"])) is not None:
                r["scene_width"] = s.get("width")
        out["depth_floor"] = df

    # what the camera sees, left to right
    seen: list[dict[str, Any]] = []
    counts: dict[str, list[int]] = {}
    for u in _units(scene):
        corners = [(x, y, z) for x, y in u.poly for z in (u.z, u.top)]
        pr = [view.project(c) for c in corners]
        parent = u.o.id
        counts.setdefault(parent, [0, 0])[1] += 1
        if any(q is None for q in pr):
            continue
        us, vs = [q[0] for q in pr], [q[1] for q in pr]
        box = [max(0.0, min(us)), max(0.0, min(vs)), min(size[0], max(us)), min(size[1], max(vs))]
        if box[0] >= box[2] or box[1] >= box[3]:
            continue
        counts[parent][0] += 1
        seen.append({"id": u.id, "box_px": [round(b) for b in box], "distance_m": round(min(q[2] for q in pr), 2),
                     "cut": min(us) < 0 or max(us) > size[0] or min(vs) < 0 or max(vs) > size[1]})
    seen.sort(key=lambda s: (s["box_px"][0] + s["box_px"][2]) / 2)
    out["objects"] = seen
    out["arrays_in_view"] = {k: f"{n} of {m} units" for k, (n, m) in counts.items() if m > 1}

    # things of known size seen in the photo: each gives the scale of the floor geometry
    refs = [_reference(k, pview) for k in known or [] if _pixels(k)]
    if refs:
        out["known"] = refs
        if sc := _scale(refs, pview):
            out["scale"] = sc
    if floor_pairs:
        res = []
        for pair in floor_pairs:
            if len(pair) not in (4, 5):
                res.append({"pair": pair, "error": "give [u1, v1, u2, v2] (and optionally z, the floor height)"})
                continue
            z = pair[4] if len(pair) == 5 else 0.0
            a, b = pview.floor_point(pair[0], pair[1], z), pview.floor_point(pair[2], pair[3], z)
            if a is None or b is None:
                res.append({"pair": pair, "error": "a pixel is above the horizon (not on the floor)"})
                continue
            dist = math.dist(a, b)
            scene, scene_by = _line_width(bl, a, b)
            ahead = [_dot((q[0] - cam2[0], q[1] - cam2[1]), view.fw2) for q in (a, b)]
            res.append({"pair": pair, "photo_m": round(dist, 2),
                        "points": [[round(c, 2) for c in a], [round(c, 2) for c in b]],
                        "ahead_m": round(sum(ahead) / 2, 2),
                        "scene_m": round(scene, 2), "scene_by": scene_by,
                        "ratio": round(scene / dist, 2) if dist > 1e-6 else None,
                        "range_m": _range(inp.camera, size, pview, pair, z, TILT_ERR if read else TILT_ERR_UNREAD),
                        "tilt_from": "photo lines" if read else "camera as set"})
            i = len(res) - 1
            mine = [_known_estimate(k, dist) for k in known or [] if k.get("pair") == i and not _pixels(k)]
            mine += [_by_reference(r, inp.camera, size, pview, pair, z, TILT_ERR if read else TILT_ERR_UNREAD)
                     for r in refs if r.get("scale") and r.get("pair") in (None, i)]
            res[-1].update(_estimates(res[-1], inp, view, read, z, place, [e for e in mine if e]))
            if abs(ahead[0] - ahead[1]) > 0.25 * dist:
                res[-1]["warning"] = (f"the points are {abs(ahead[0] - ahead[1]):.1f} m apart in depth: pick two at "
                                      "the same depth (a line across the aisle) for a width")
        out["floor_pairs"] = res
    out["notes"] = _notes(out)
    return out


def _lines(inp, view: View) -> dict[str, Any] | None:
    """The photo's tilt from its vanishing points (photo_vanish), next to the camera's: how far the camera is off
    and the look_at height that would match."""
    if not inp.path or not Path(inp.path).exists():
        return None
    from agentic_sim_world_gen import photo_vanish

    if not photo_vanish.available():
        return None
    r = photo_vanish.estimate(inp.path, inp.camera.hfov_deg)
    if not r.get("ok"):
        return r
    r["camera_off_deg"] = round(math.degrees(view.pitch) - r["pitch_deg"], 1)
    flat = math.dist(inp.camera.position[:2], inp.camera.look_at[:2])
    r["look_at_z"] = round(inp.camera.position[2] + flat * math.tan(math.radians(r["pitch_deg"])), 2)
    return r


def _range(cam, size, pview: View, pair, z: float, tilt_err: float) -> list[float | None]:
    """The distance between the pair's floor points with the tilt off by +-tilt_err and the camera's height by
    +-HEIGHT_ERR: [smallest, largest]; largest None when a pixel reaches the horizon within that error."""
    ds: list[float | None] = []
    for dp in (-tilt_err, 0.0, tilt_err):
        for kh in (1 - HEIGHT_ERR, 1.0, 1 + HEIGHT_ERR):
            v = View(cam, size, pitch_deg=math.degrees(pview.pitch) + dp, roll_deg=math.degrees(pview.roll),
                     hfov_deg=pview.hfov, height=z + (pview.pos[2] - z) * kh)
            a, b = v.floor_point(pair[0], pair[1], z), v.floor_point(pair[2], pair[3], z)
            ds.append(None if a is None or b is None else math.dist(a, b))
    known = [d for d in ds if d is not None]
    return [round(min(known), 2), None if len(known) < len(ds) else round(max(known), 2)]


def _estimates(row: dict[str, Any], inp, view: View, read: bool, z: float, place: str | None,
               known: list[dict[str, Any]]) -> dict[str, Any]:
    """Every estimate of one width side by side: the floor geometry (with the photo's tilt, and with the
    camera as set when they differ), a monocular depth map when the photo has one, known sizes (`known`: ready
    estimates from _known_estimate / _by_reference), and what that kind of store usually has. One estimate alone cannot be told wrong; the
    median of several is `best_m`, and estimates far from it are named in `outliers`."""
    pair = row["pair"]
    est: list[dict[str, Any]] = [{"by": "geometry (photo's tilt)" if read else "geometry (camera as set)",
                                  "m": row["photo_m"], "range_m": row["range_m"]}]
    if read:  # the same pixels through the camera somebody set: differs when its tilt is off
        a, b = view.floor_point(pair[0], pair[1], z), view.floor_point(pair[2], pair[3], z)
        est.append({"by": "geometry (camera as set)", "m": round(math.dist(a, b), 2) if a and b else None})
    d = _depth_width(inp, view, pair)
    if d is not None:
        est.append({"by": "depth map", "m": round(d, 2), "range_m": [round(d * DEPTH_SCALE[0], 2), round(d * DEPTH_SCALE[1], 2)]})
    est += known
    # not counted: a known size too loose to tell anything (`loose`), a tile whose size the geometry picked
    counted = [e for e in est if e.get("m") and not e.get("loose") and not e.get("tile_from")]
    vals = sorted(e["m"] for e in counted)
    out: dict[str, Any] = {"estimates": est}
    if not vals:
        return out
    best = vals[len(vals) // 2] if len(vals) % 2 else (vals[len(vals) // 2 - 1] + vals[len(vals) // 2]) / 2
    out["best_m"] = round(best, 2)
    far = [e for e in counted if abs(e["m"] - best) > OUTLIER * best]
    if len(vals) >= 3:
        out["outliers"] = [e["by"] for e in far]
    elif len(vals) == 2 and vals[1] - vals[0] > OUTLIER * vals[0]:
        out["disagree"] = [e["by"] for e in est]  # two estimates apart: neither can be called the wrong one
    if len(vals) == 1:
        out["single"] = True
    # wall to wall in the scene is the room's width, not an aisle
    walls = [n for n in row.get("scene_by") or [] if str(n).startswith("wall")]
    if (t := TYPICAL_AISLE.get(place or "")) and len(walls) < 2:
        out["typical_m"] = list(t)
        out["typical_off"] = bool(best < t[0] * (1 - TYPICAL_SLACK) or best > t[1] * (1 + TYPICAL_SLACK))
    out["scene_vs_best"] = round(row["scene_m"] / best, 2) if best > 1e-6 else None
    return out


def _pixels(k: dict[str, Any]) -> list[float] | None:
    """The two floor pixels of a known-size thing: `line` [u1, v1, u2, v2] (its two ends on the floor), or the
    bottom edge of `box` [u1, v1, u2, v2] (a thing seen square-on)."""
    if isinstance(k.get("line"), (list, tuple)) and len(k["line"]) == 4:
        return [float(c) for c in k["line"]]
    if isinstance(k.get("box"), (list, tuple)) and len(k["box"]) == 4:
        u1, v1, u2, v2 = (float(c) for c in k["box"])
        return [min(u1, u2), max(v1, v2), max(u1, u2), max(v1, v2)]
    return None


def _kind(k: dict[str, Any]) -> str:
    name = str(k.get("kind") or "").strip()
    return KIND_ALIASES.get(name.lower(), name.lower())


def _size(k: dict[str, Any], near: float | None) -> dict[str, Any]:
    """The real length an entry stands for: count x the kind's size (or size_m), with its smallest and largest.
    A floor tile without size_m takes the size in TILE_SIZES that brings count x size nearest `near` (the
    geometry's length of the same line) and says so."""
    kind = _kind(k)
    n = k.get("count", 1)
    if not isinstance(n, (int, float)) or n <= 0:
        return {"error": "count must be a positive number"}
    own = k.get("size_m")
    if isinstance(own, (int, float)) and own > 0:
        return {"kind": kind or "given", "count": n, "size_m": float(own), "m": n * own, "range_m": [n * own, n * own]}
    if kind == "floor_tile":
        if not near:
            return {"error": "floor_tile needs size_m (0.3 / 0.45 / 0.6) when there is no geometry to pick it"}
        s = min(TILE_SIZES, key=lambda c: abs(math.log(n * c / near)))
        out = {"kind": kind, "count": n, "size_m": s, "m": n * s, "range_m": [n * s, n * s], "size_from": "geometry",
               "choices_m": {f"{c:g}": round(n * c, 2) for c in TILE_SIZES}}
        if abs(n * s / near - 1) > TILE_UNSURE:
            out["unsure"] = True
        return out
    if kind not in KNOWN_SIZES:
        return {"error": f"unknown kind {k.get('kind')!r}: one of {', '.join([*KNOWN_SIZES, 'floor_tile'])}, or give size_m"}
    s, lo, hi, _ = KNOWN_SIZES[kind]
    return {"kind": kind, "count": n, "size_m": s, "m": n * s, "range_m": [n * lo, n * hi]}


def _label(sz: dict[str, Any]) -> str:
    return f"{sz['count']:g} x {sz['kind']} {sz['size_m']:g} m"


def _known_estimate(k: dict[str, Any], photo_m: float) -> dict[str, Any] | None:
    """A pair's width from what was counted across it: {pair, m, from} (the width itself) or
    {pair, kind, count} (so many shelf units / floor tiles across: the server multiplies)."""
    if isinstance(k.get("m"), (int, float)) and k["m"] > 0:
        return {"by": f"known size ({k.get('from') or 'counted off the photo'})", "m": round(float(k["m"]), 2)}
    if not k.get("kind") and not k.get("size_m"):
        return None
    sz = _size(k, photo_m)
    if "error" in sz:
        return {"by": f"known size ({k.get('kind')})", "m": None, "error": sz["error"]}
    out = {"by": f"known size ({_label(sz)} across)", "m": round(sz["m"], 2), "range_m": [round(c, 2) for c in sz["range_m"]]}
    if sz.get("size_from"):
        out["choices_m"] = sz["choices_m"]
        out["tile_from"] = "geometry" + (" (unsure)" if sz.get("unsure") else "")
    return out


def _reference(k: dict[str, Any], pview: View) -> dict[str, Any]:
    """A known-size thing seen in the photo: its two floor pixels through the floor geometry give `geometry_m`;
    scale = real size / geometry_m is how far the geometry's lengths are off (the camera's height, mostly)."""
    px = _pixels(k)
    z = float(k.get("z") or 0.0)
    out: dict[str, Any] = {"kind": _kind(k) or "given", "px": [round(c, 1) for c in px], **({"z": z} if z else {}),
                           **({"pair": k["pair"]} if k.get("pair") is not None else {})}
    a, b = pview.floor_point(px[0], px[1], z), pview.floor_point(px[2], px[3], z)
    if a is None or b is None:
        return {**out, "error": "a pixel is above the horizon (not on the floor): give the thing's two ends on the floor"}
    geo = math.dist(a, b)
    if geo < 1e-6:
        return {**out, "error": "the two pixels are the same point"}
    sz = _size(k, geo)
    if "error" in sz:
        return {**out, "error": sz["error"]}
    ahead = [_dot((q[0] - pview.pos[0], q[1] - pview.pos[1]), pview.fw2) for q in (a, b)]
    out.update({"kind": sz["kind"], "count": sz["count"], "size_m": sz["size_m"], "m": round(sz["m"], 2),
                "range_m": [round(c, 2) for c in sz["range_m"]], "geometry_m": round(geo, 2),
                "ahead_m": round(sum(ahead) / 2, 2), "scale": round(sz["m"] / geo, 3)})
    if abs(ahead[0] - ahead[1]) > 0.5 * geo:
        out["runs_away"] = True  # its length is read along the depth: the tilt moves it most
    if sz.get("size_from"):
        out["size_from"] = "geometry" + (" (unsure)" if sz.get("unsure") else "")
        out["choices_m"] = sz["choices_m"]
    return out


def _scale(refs: list[dict[str, Any]], pview: View) -> dict[str, Any] | None:
    """The scale the known-size things give together (the median) and the camera height that goes with it."""
    ks = sorted(r["scale"] for r in refs if r.get("scale") and r.get("size_from") is None) \
        or sorted(r["scale"] for r in refs if r.get("scale"))
    if not ks:
        return None
    k = ks[len(ks) // 2] if len(ks) % 2 else (ks[len(ks) // 2 - 1] + ks[len(ks) // 2]) / 2
    out = {"factor": round(k, 3), "from": len(ks), "camera_height_m": round(pview.pos[2] * k, 2),
           "camera_height_set_m": round(pview.pos[2], 2)}
    if ks[-1] > ks[0] * (1 + REFS_APART):
        out["apart"] = [round(ks[0], 3), round(ks[-1], 3)]
    return out


def _by_reference(ref: dict[str, Any], cam, size, pview: View, pair, z: float, tilt_err: float) -> dict[str, Any] | None:
    """A pair's width from a known-size thing in the same photo: real size x (pair's floor length / the
    thing's floor length). The camera's height cancels; what is left is the tilt (worse the farther apart in
    depth the two are) and the thing's own size range."""
    px, rz = ref["px"], ref.get("z", 0.0)
    ws: list[float | None] = []
    for dp in (0.0, -tilt_err, tilt_err):
        v = View(cam, size, pitch_deg=math.degrees(pview.pitch) + dp, roll_deg=math.degrees(pview.roll), hfov_deg=pview.hfov)
        pts = [v.floor_point(pair[0], pair[1], z), v.floor_point(pair[2], pair[3], z),
               v.floor_point(px[0], px[1], rz), v.floor_point(px[2], px[3], rz)]
        if any(q is None for q in pts) or math.dist(pts[2], pts[3]) < 1e-6:
            ws.append(None)
        else:
            ws.append(math.dist(pts[0], pts[1]) / math.dist(pts[2], pts[3]))
    if ws[0] is None:
        return None
    got = [w for w in ws if w is not None]
    lo, hi = ref["range_m"]
    m = ref["m"] * ws[0]
    lo, hi = lo * min(got), None if len(got) < len(ws) else hi * max(got)
    out = {"by": f"known size ({_label(ref)} in the photo at {ref['ahead_m']:g} m ahead)", "m": round(m, 2),
           "range_m": [round(lo, 2), None if hi is None else round(hi, 2)]}
    if hi is None or hi - lo > WIDE_RANGE * m:
        out["loose"] = True  # listed, not counted in best_m
    if ref.get("size_from"):
        out["tile_from"] = ref["size_from"]
    return out


def _depth_width(inp, view: View, pair) -> float | None:
    """The distance between the pair's two pixels from the photo's depth map (<stem>.depth.npz, agentic_sim_world_gen.depth):
    no camera tilt or height in it, only the field of view and the map's scale."""
    if not inp.path:
        return None
    try:
        from agentic_sim_world_gen import depth

        got = depth.load(inp.path)
    except Exception:  # no numpy, unreadable map
        return None
    if got is None:
        return None
    pts = []
    for u, v in ((pair[0], pair[1]), (pair[2], pair[3])):
        d = depth.sample(got[0], view.size, (u, v))
        if d is None:
            return None
        pts.append(((u - view.size[0] / 2) / view.f * d, (v - view.size[1] / 2) / view.f * d, d))
    return math.dist(*pts)


DEPTH_MAX = 15.0  # depth-map points farther than this are not used [m]
DEPTH_COLS = 240  # the map is sampled down to about this many columns
FLOOR_TOL = (0.06, 0.03)  # a point is on the floor within max(tol[0], tol[1] * its depth) [m]: MoGe is 2-6 % off inside a frame
FLOOR_LEAN = 30.0  # the floor's normal is within this of the camera's up as set [deg]
FLOOR_MIN = 0.03  # share of the map's points that must lie on the plane to call it the floor
BAND = (0.15, 0.06)  # points within max(band[0], band[1] * t) of t m ahead count for the width there [m]
EDGE_POINTS = 3  # fewer blocker points than this in a bin do not close the aisle (stray points)
EDGE_BIN = 0.05  # blockers are counted in bins of this width from the center line outwards [m]
EDGE_SHARE = 0.3  # a side's blocker starts at the first bin with this share of that side's usual bin count
ON_LINE = 0.15  # blockers this close to the camera's center line: the line itself is blocked [m]
DEPTH_OFF = 0.25  # the depth map's width differs from the scene's by more than this share: say so


def depth_floor(inp, view: View) -> dict[str, Any] | None:
    """Free widths on the floor straight from the photo's depth map: no pixels to read off the photo.
    A plane is fitted to the floor (RANSAC near the camera's up, the lowest wide one), points below BLOCK_BELOW
    above it are the blockers, and at each AHEAD distance the free stretch around the camera's center line is
    measured in the plane. The scale is the depth map's alone (DEPTH_SCALE per photo): `width_at_set_height`
    rescales by the camera's height as set / the height the map gives. None without a map / numpy."""
    if not inp.path:
        return None
    try:
        import numpy as np

        from agentic_sim_world_gen import depth

        got = depth.load(inp.path)
    except Exception:  # no numpy, unreadable map
        return None
    if got is None:
        return None
    d = got[0]
    h, w = d.shape
    step = max(1, w // DEPTH_COLS)
    vs, us = np.mgrid[step // 2:h:step, step // 2:w:step]
    z = d[vs, us].astype("float64")
    ok = np.isfinite(z) & (z > 0.05) & (z < DEPTH_MAX)
    if ok.sum() < 200:
        return {"ok": False, "why": "the depth map has too few points"}
    # camera frame: x right, y down, z forward (the pixels of the photo's size, the field of view as set)
    u_px, v_px = (us[ok] + 0.5) / w * view.size[0], (vs[ok] + 0.5) / h * view.size[1]
    z = z[ok]
    pts = np.stack([(u_px - view.size[0] / 2) / view.f * z, (v_px - view.size[1] / 2) / view.f * z, z], axis=1)
    up0 = np.array([view.rt[2], -view.up[2], view.fw[2]])  # the world's up in the camera frame, as the camera is set
    rng = np.random.default_rng(0)
    tol = np.maximum(FLOOR_TOL[0], FLOOR_TOL[1] * z)
    below = pts[pts @ up0 < -0.3]  # the floor is below the camera
    if len(below) < 50:
        return {"ok": False, "why": "no points below the camera in the depth map"}
    best = None
    cos_lean = math.cos(math.radians(FLOOR_LEAN))
    for _ in range(300):
        a, b, c = below[rng.choice(len(below), 3, replace=False)]
        n = np.cross(b - a, c - a)
        ln = np.linalg.norm(n)
        if ln < 1e-9:
            continue
        n = n / ln
        if n @ up0 < 0:
            n = -n
        if n @ up0 < cos_lean:
            continue
        off = -(n @ a)  # height above the plane = p . n + off; the camera is at `off`
        hgt = pts @ n + off
        on = np.abs(hgt) <= tol
        under = (hgt < -2 * tol).mean()
        if under > 0.02:  # a table top or a shelf board: the floor has nothing below it
            continue
        if best is None or on.sum() > best[0]:
            best = (int(on.sum()), n, off)
    if best is None or best[0] < FLOOR_MIN * len(pts):
        return {"ok": False, "why": "no floor plane found in the depth map (the floor is hidden or the map is off)"}
    _, n, off = best
    # refine: weighted least squares (Tukey) on the residual per meter of depth. A plain fit inside `tol` takes the
    # foot of the far wall and of the shelves for floor (nothing is below the floor to balance them) and tilts.
    for _ in range(6):
        res = (pts @ n + off) / z
        near = np.abs(res) <= tol / z
        s = max(1.4826 * float(np.median(np.abs(res[near]))), 1e-3)
        wgt = np.where(near, np.clip(1 - (res / (3 * s)) ** 2, 0, None) ** 2, 0.0)
        if wgt.sum() < 1e-9:
            break
        c = (pts * wgt[:, None]).sum(axis=0) / wgt.sum()
        n2 = np.linalg.svd((pts - c) * np.sqrt(wgt)[:, None], full_matrices=False)[2][2]
        n = n2 if n2 @ up0 > 0 else -n2
        off = -(n @ c)
    hgt = pts @ n + off
    on = np.abs(hgt) <= tol
    fz = np.array([0.0, 0.0, 1.0])
    f2 = fz - (fz @ n) * n
    f2 /= np.linalg.norm(f2)
    r2 = np.cross(f2, n)  # right of the view, on the floor
    across, ahead = pts @ r2, pts @ f2
    block = (hgt > tol) & (hgt < BLOCK_BELOW)
    k = view.pos[2] / off if off > 1e-6 else None  # the map's scale against the camera's height as set
    rows = []
    for t in AHEAD:
        band = np.abs(ahead - t) <= max(BAND[0], BAND[1] * t)
        if (band & on).sum() < 5:
            continue  # the floor is not seen there
        xs = across[band & block]
        seen = across[band]
        row: dict[str, Any] = {"ahead_m": t}
        if (np.abs(xs) < ON_LINE).sum() >= EDGE_POINTS:
            row.update({"width": None, "on": "something on the center line"})
            rows.append(row)
            continue

        def edge(v):
            """Distance to the first dense run of blocker points on one side (None: none in view)."""
            if len(v) < EDGE_POINTS:
                return None
            bins = np.bincount((v / EDGE_BIN).astype(int))
            need = max(EDGE_POINTS, EDGE_SHARE * float(np.median(bins[bins > 0])))
            first = np.nonzero(bins >= need)[0]
            if not len(first):
                return None
            inside = v[(v >= first[0] * EDGE_BIN) & (v < (first[0] + 1) * EDGE_BIN)]
            return float(np.median(inside))

        lm, rm = edge(-xs[xs < 0]), edge(xs[xs > 0])
        row["left"], row["right"] = (round(lm, 2) if lm is not None else None), (round(rm, 2) if rm is not None else None)
        if lm is None or rm is None:  # the image's edge comes before a blocker: a lower bound only
            wmin = (lm if lm is not None else float(-seen.min())) + (rm if rm is not None else float(seen.max()))
            row.update({"width": None, "at_least": round(wmin, 2), "open": [s for s, m in (("left", lm), ("right", rm)) if m is None]})
        else:
            wd = lm + rm
            row.update({"width": round(wd, 2), "range_m": [round(wd * DEPTH_SCALE[0], 2), round(wd * DEPTH_SCALE[1], 2)]})
            if k is not None:
                row["width_at_set_height"] = round(wd * k, 2)
        rows.append(row)
    return {"ok": True, "camera_height_m": round(float(off), 2), "camera_height_set": round(view.pos[2], 2),
            "scale_to_set_height": round(k, 3) if k is not None else None,
            "pitch_deg": round(math.degrees(math.asin(max(-1.0, min(1.0, float(fz @ n))))), 1),
            "floor_share": round(float(on.mean()), 3), "across": rows}


def _notes(m: dict[str, Any]) -> list[str]:
    """Things to check against the photo, in a few words."""
    n = []
    pl = m.get("photo_lines")
    if pl and pl.get("ok"):
        if abs(pl["camera_off_deg"]) > TILT_OFF:
            n.append(f"the photo's vertical edges put its camera at pitch {pl['pitch_deg']} deg (horizon at row "
                     f"{pl['horizon_v_px']}), the scene's camera is at {m['camera']['pitch_deg']} deg: set look_at's "
                     f"height to {pl['look_at_z']} (set_input) before compare_photo; floor_pairs already use the photo's tilt")
        if abs(pl["roll_deg"]) > TILT_OFF:
            n.append(f"the photo is rolled {pl['roll_deg']} deg; the scene's camera cannot roll, so its render stays level")
        if pl.get("hfov_deg") and abs(pl["hfov_deg"] - m["camera"]["hfov_deg"]) > 8:
            n.append(f"the photo's level edges fit a field of view of {pl['hfov_deg']} deg, the camera has "
                     f"{m['camera']['hfov_deg']} (a hint, not used: check hfov_deg against the photo's lens)")
    elif pl:
        n.append(f"the photo's tilt could not be read off its lines ({pl.get('why')}): widths from floor_pairs carry "
                 f"a tilt error of +-{TILT_ERR_UNREAD:g} deg")
    if (hv := m["camera"].get("horizon_v_px")) is not None and not (pl and pl.get("ok")):
        n.append(f"the scene's camera puts the horizon at row {hv} of {m['size_px'][1]}: floor and shelf lines running away "
                 "from the camera meet there; if they meet at another row in the photo, fix look_at's height first")
    for r in m.get("known", []):
        if r.get("error"):
            n.append(f"known {r['kind']}: {r['error']}")
        elif r.get("size_from"):
            n.append(f"known floor_tile: {r['size_m']} m tiles were picked by the geometry ({r['geometry_m']} m; "
                     f"{r['choices_m']}), so it is not counted as an estimate of its own: give size_m")
        elif r.get("runs_away"):
            n.append(f"known {r['kind']} at {r['ahead_m']} m ahead runs away from the camera: its length follows the tilt; "
                     "prefer a thing seen across the view (a unit or door facing the camera, tiles across the aisle) "
                     "near the pair's depth")
    if sc := m.get("scale"):
        if abs(sc["factor"] - 1) > SCALE_OFF:
            n.append(f"known sizes say the floor geometry is off by x{sc['factor']}: if the tilt is right, the camera is "
                     f"{sc['camera_height_m']} m high, not {sc['camera_height_set_m']} m (set position's z with set_input); "
                     "the 'known size' estimates already carry it")
        if sc.get("apart"):
            n.append(f"the known sizes give scales x{sc['apart'][0]} to x{sc['apart'][1]}: one of them is misread "
                     "(pixels not on the floor, another size of unit) or the tilt is off")
    s = m["camera"]["stands"]
    if s.get("width") is None:
        n.append(f"the camera stands on {s.get('on')}: move the camera (set_input) or that object")
    else:
        n.append(f"camera stands {s['left']} m from {s['left_by']} (left) and {s['right']} m from {s['right_by']} (right); "
                 "check that the photo was taken from the same place in the aisle")
    w = [g["width"] for r in m["across"] for g in (r.get("gaps") or [r]) if g.get("width")]
    if w:
        n.append(f"free widths across the view {min(w)}-{max(w)} m; count shelf units / doors / ceiling lights in the "
                 "photo to estimate its aisle (shelf unit ~0.9 m, door ~0.8-0.9 m) and compare")
    for k, v in m["arrays_in_view"].items():
        n.append(f"{k}: {v} in view; compare with the units you count in the photo")
    df = m.get("depth_floor")
    if df and not df.get("ok"):
        n.append(f"depth map: {df.get('why')}; use floor_pairs")
    elif df:
        n.append(f"depth map: the floor puts the camera {df['camera_height_m']} m up at pitch {df['pitch_deg']} deg (set: "
                 f"{df['camera_height_set']} m, {m['camera']['pitch_deg']} deg); its scale alone is off by "
                 f"{round((DEPTH_SCALE[0] - 1) * 100)} .. +{round((DEPTH_SCALE[1] - 1) * 100)} % per photo")
        for r in df["across"]:
            w, s = r.get("width_at_set_height") or r.get("width"), r.get("scene_width")
            if w and s and abs(s / w - 1) > DEPTH_OFF:
                n.append(f"depth map: {r['ahead_m']:g} m ahead the photo's floor is free for {w} m "
                         f"(depth alone {r['width']} m, {r['range_m'][0]}-{r['range_m'][1]}), the scene for {s} m: "
                         f"the scene is too {'wide' if s > w else 'narrow'} there; confirm with floor_pairs or a known size")
    for p in m.get("floor_pairs", []):
        if not p.get("ratio"):
            continue
        at = f"at {p['ahead_m']} m ahead"
        ests = ", ".join(f"{e['by']} {e['m']} m" for e in p.get("estimates", []))
        for e in p.get("estimates", []):
            if e.get("error"):
                n.append(f"width {at}: {e['by']}: {e['error']}")
            elif e.get("tile_from") and "across" in e["by"]:
                n.append(f"width {at}: {e['by']}: the tile size was picked by the geometry ({e['choices_m']}), so it is "
                         "not counted in best_m: give size_m (0.3 / 0.45 / 0.6)")
            elif e.get("loose"):
                n.append(f"width {at}: {e['by']} is too loose to count ({e['range_m'][0]}-"
                         f"{e['range_m'][1] if e['range_m'][1] is not None else 'no upper bound'} m)")
        for by in p.get("outliers", []):
            fix = {"geometry (camera as set)": ": the camera's tilt or height is off, fix it with set_input",
                   "geometry (photo's tilt)": ": check the camera's height and that both pixels are on the floor",
                   "depth map": ": the depth map's scale is off for this photo"}.get(by, ": count again")
            n.append(f"width {at}: {by} is far from the others ({ests}; median {p['best_m']} m){fix}")
        if p.get("disagree"):
            n.append(f"width {at}: the two estimates disagree ({ests}) and neither can be called wrong: add a third "
                     "(known=[{pair, kind: shelf_unit / floor_tile / door, count}] across the aisle, or a thing in the photo "
                     "{kind, line: its two ends on the floor})")
        if p.get("single"):
            n.append(f"width {at}: one estimate only ({ests}); a wrong one cannot be noticed, add a known size "
                     "(known=[{pair, kind, count}] counted across it, or {kind, line} for a unit / door / tile seen in the "
                     "photo) before resizing the scene")
        if p.get("typical_off"):
            n.append(f"width {at}: {p['best_m']} m is outside what a {m.get('place')} usually has "
                     f"({p['typical_m'][0]}-{p['typical_m'][1]} m): check the estimates before using it")
        lo, hi = p["range_m"]
        span = f"{p['photo_m']} m ({lo}-{hi if hi is not None else 'no upper bound'})"
        off = (p["scene_m"] < lo or (hi is not None and p["scene_m"] > hi)) and abs(p["ratio"] - 1) > 0.2
        if off:  # outside even the error: the scene differs
            n.append(f"photo {span} vs scene {p['scene_m']} m at {p['ahead_m']} m ahead "
                     f"({'wider' if p['ratio'] > 1 else 'narrower'} in the scene): move the blockers or resize the room")
        elif hi is None or hi - lo > WIDE_RANGE * p["photo_m"]:
            err = TILT_ERR if p["tilt_from"] == "photo lines" else TILT_ERR_UNREAD
            n.append(f"photo {span} at {p['ahead_m']} m ahead: too loose to tell it from the scene's {p['scene_m']} m "
                     f"(tilt +-{err:g} deg, camera height +-{HEIGHT_ERR:.0%}); count the shelf units / floor tiles "
                     "across the aisle instead, or take the pair nearer the camera")
    return n
