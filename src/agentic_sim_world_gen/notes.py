"""Notes the user writes at a spot on the preview: find what is at the spot (top view or a frame of the
check's orbit viewer) and say where it is in words the agent can act on."""

from __future__ import annotations

import math
from typing import Any

from agentic_sim_world_gen.scene.spec import ResolvedScene

Vec = tuple[float, float, float]
NEAR_M = 2.0  # objects within this of the spot are listed as `near`


def _sub(a, b) -> Vec:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b) -> Vec:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _unit(a) -> Vec:
    n = math.sqrt(_dot(a, a)) or 1.0
    return (a[0] / n, a[1] / n, a[2] / n)


def camera_ray(cam: dict[str, Any], u: float, v: float, aspect: float) -> tuple[Vec, Vec]:
    """The ray through (u, v) of an image (0..1 from the top left) shot by `cam` {eye, forward, up, hfov_deg}
    (the orbit cameras check_core records; the aperture fits the image width)."""
    f = _unit(cam["forward"])
    r = _unit(_cross(f, cam["up"]))
    up = _cross(r, f)
    t = math.tan(math.radians(cam["hfov_deg"]) / 2)
    x, y = (2 * u - 1) * t, (1 - 2 * v) * t / aspect
    return tuple(cam["eye"]), _unit(tuple(f[i] + x * r[i] + y * up[i] for i in range(3)))


def _box_frame(b) -> tuple[tuple[float, float], Vec, Vec, float, float]:
    p0, p1, _, p3 = b.poly[:4]
    cx, cy = sum(p[0] for p in b.poly) / 4, sum(p[1] for p in b.poly) / 4
    ex, ey = (p1[0] - p0[0], p1[1] - p0[1]), (p3[0] - p0[0], p3[1] - p0[1])
    lx, ly = math.hypot(*ex) or 1e-9, math.hypot(*ey) or 1e-9
    return (cx, cy), (ex[0] / lx, ex[1] / lx, 0.0), (ey[0] / ly, ey[1] / ly, 0.0), lx / 2, ly / 2


def _ray_box(o: Vec, d: Vec, b) -> float | None:
    """Distance along the ray to the box (oriented footprint, z range), or None."""
    (cx, cy), ax, ay, hx, hy = _box_frame(b)
    rel = (o[0] - cx, o[1] - cy, o[2] - (b.z[0] + b.z[1]) / 2)
    lo, hi = 0.0, math.inf
    for axis, half in ((ax, hx), (ay, hy), ((0.0, 0.0, 1.0), (b.z[1] - b.z[0]) / 2)):
        p, q = _dot(rel, axis), _dot(d, axis)
        if abs(q) < 1e-12:
            if abs(p) > half:
                return None
            continue
        t1, t2 = (-half - p) / q, (half - p) / q
        lo, hi = max(lo, min(t1, t2)), min(hi, max(t1, t2))
        if lo > hi:
            return None
    return lo


def hit(scene: ResolvedScene, origin: Vec, direction: Vec) -> dict[str, Any] | None:
    """What the ray hits first: an object's box or the floor (z = 0). None when it misses both."""
    from agentic_sim_world_gen.sdg import boxes

    best, what = math.inf, None
    for b in boxes(scene):
        t = _ray_box(origin, direction, b)
        if t is not None and t < best:
            best, what = t, b.id
    if direction[2] < -1e-9 and (t := -origin[2] / direction[2]) < best:
        best, what = t, None
    if best == math.inf:
        return None
    p = tuple(origin[i] + best * direction[i] for i in range(3))
    return {"position": (round(p[0], 3), round(p[1], 3)), "z": round(max(p[2], 0.0), 3), "object": what}


def _inside(pt, poly) -> bool:
    x, y = pt
    n, inside = len(poly), False
    for i in range(n):
        (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % n]
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def at_spot(scene: ResolvedScene, x: float, y: float) -> dict[str, Any]:
    """The top view: the highest object whose footprint holds (x, y) (a lamp on the desk over the desk)."""
    from agentic_sim_world_gen.sdg import boxes

    under = [b for b in boxes(scene) if _inside((x, y), b.poly)]
    top = max(under, key=lambda b: b.z[1], default=None)
    return {"position": (round(x, 3), round(y, 3)), "z": round(top.z[1], 3) if top else 0.0,
            "object": top.id if top else None}


def _dist(pt, poly) -> float:
    if _inside(pt, poly):
        return 0.0
    best = math.inf
    for i in range(len(poly)):
        (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % len(poly)]
        dx, dy = x2 - x1, y2 - y1
        t = max(0.0, min(1.0, ((pt[0] - x1) * dx + (pt[1] - y1) * dy) / ((dx * dx + dy * dy) or 1e-12)))
        best = min(best, math.hypot(pt[0] - x1 - t * dx, pt[1] - y1 - t * dy))
    return best


def near(scene: ResolvedScene, x: float, y: float, skip: str | None = None, limit: int = 3) -> list[dict[str, Any]]:
    """The objects closest to the spot (within NEAR_M), nearest first; array units count for their object."""
    from agentic_sim_world_gen.sdg import boxes

    by: dict[str, float] = {}
    for b in boxes(scene):
        if skip and (b.id == skip or b.parent == skip.split(".")[0]):
            continue
        d = _dist((x, y), b.poly)
        if d <= NEAR_M and d < by.get(b.parent, math.inf):
            by[b.parent] = d
    return [{"object": k, "distance_m": round(v, 2)} for k, v in sorted(by.items(), key=lambda kv: kv[1])[:limit]]


def where_ja(scene: ResolvedScene, note) -> str:
    """Where the note is, in a few Japanese words: 「机の上」「床（棚の近く 0.4 m）」."""
    names = {o.id: o.label or o.id for o in scene.spec.objects}

    def name(i: str) -> str:
        head, _, unit = i.partition(".")
        return names.get(head, head) + (f" {unit}" if unit else "")

    if note.object:
        return f"{name(note.object)}の上" if (note.z or 0) > 0.05 else name(note.object)
    if note.position is None:
        return "場所なし"
    x, y = note.position
    n = near(scene, x, y, limit=1)
    return f"床 x {x:.1f}, y {y:.1f} m" + (f"（{name(n[0]['object'])}の近く {n[0]['distance_m']:g} m）" if n else "")
