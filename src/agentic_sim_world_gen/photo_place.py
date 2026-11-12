"""Furniture poses from the feet located in photos: the selected asset's footprint fitted to the points.

fit_photos turns each located pixel into a floor point (photo_fit.Locate, z = 0 for a foot). The points of one
object are its feet / base corners, so the object's footprint - the selected asset's size - is fitted to them as a
rectangle: each point to its nearest corner (a point marked `edge` front / back to the nearest front / back corner),
least squares over the center and the facing. Two front feet give the center as well as the facing (the mean of the
points would be the middle of the front edge), and a point on the far side keeps the long side the right way.

A rectangle looks the same turned by 180 deg (and a square by 90 deg). When the points do not tell which way is the
front, the tie is broken by:
  1. edges: the front faces the cameras of the photos its front points were picked in (the back faces away);
  2. the object's facing now (kept as it is when the photos cannot tell);
and the result says which (`facing_from`: points / cameras / kept). The facing now is also kept (only the center
fitted) when no other facing fits better by TURN_M, and always for two unmarked points (they may be any two corners).
"""
from __future__ import annotations

import math

MIN_POINTS = 2  # one point is a foot, not a pose
TIE_M = 0.02  # two poses whose rms differ by less than this are a tie
DISTINCT_DEG = 20.0  # tied poses this close are one
MISFIT_M = 0.15  # an rms above this: the asset's size is not the real thing's (or a point is on something else)
TURN_M = 0.05  # turn the object from its facing now only when that fits the points this much better: located points
# are off by 0.1-0.3 m in real videos, and a slightly lower rms at another facing turned sofas by 80 deg


def _local(p, c, th):
    """p in the object's frame: u along the front, v to the object's left."""
    dx, dy = p[0] - c[0], p[1] - c[1]
    ct, st = math.cos(th), math.sin(th)
    return dx * ct + dy * st, -dx * st + dy * ct


def _world(u, v, c, th):
    ct, st = math.cos(th), math.sin(th)
    return c[0] + u * ct - v * st, c[1] + u * st + v * ct


def _corners(hu, hv, edge):
    us = (hu,) if edge == "front" else (-hu,) if edge == "back" else (hu, -hu)
    return [(u, v) for u in us for v in (hv, -hv)]


def _fit_center(pts, hu, hv, th, c):
    """For a fixed facing: assign each point to its nearest allowed corner, move the center to fit, repeat."""
    for _ in range(8):
        acc_x = acc_y = 0.0
        for p, edge in pts:
            lu, lv = _local(p, c, th)
            u, v = min(_corners(hu, hv, edge), key=lambda k: (k[0] - lu) ** 2 + (k[1] - lv) ** 2)
            wx, wy = _world(u, v, (0.0, 0.0), th)
            acc_x += p[0] - wx
            acc_y += p[1] - wy
        n = len(pts)
        nc = (acc_x / n, acc_y / n)
        if math.dist(nc, c) < 1e-6:
            c = nc
            break
        c = nc
    err = 0.0
    for p, edge in pts:
        lu, lv = _local(p, c, th)
        err += min((k[0] - lu) ** 2 + (k[1] - lv) ** 2 for k in _corners(hu, hv, edge))
    return c, math.sqrt(err / len(pts))


def _best_at(pts, hu, hv, th):
    start = (sum(p[0] for p, _ in pts) / len(pts), sum(p[1] for p, _ in pts) / len(pts))
    # several starts: the nearest-corner assignment from the mean can lock onto the wrong corners
    best = None
    for su, sv in ((0, 0), (hu, 0), (-hu, 0), (0, hv), (0, -hv)):
        c0 = _world(su, sv, start, th)
        c, rms = _fit_center(pts, hu, hv, th, c0)
        if best is None or rms < best[1] - 1e-9:
            best = (c, rms)
    return best


def _ang_diff(a, b):
    return abs((a - b + 180) % 360 - 180)


def fit_pose(points: list[tuple[tuple[float, float], str | None]], size: tuple[float, float],
             facing_now: float | None = None, front_cams: list[tuple[float, float]] | None = None,
             back_cams: list[tuple[float, float]] | None = None) -> dict:
    """The pose of a footprint (along the front, across) [m] that puts its corners on the points.

    points: (floor xy, edge) with edge "front" / "back" / None. facing_now: the object's facing now [deg, 0 = +x].
    front_cams / back_cams: camera xy of the photos the front / back points were picked in.
    Returns {center, facing_deg, rms_m, facing_from, ties} or {error}."""
    if len(points) < MIN_POINTS:
        return {"error": "one point is a foot, not the middle: give 2-4 feet / corners"}
    hu, hv = size[0] / 2, size[1] / 2
    coarse = []
    for deg in range(0, 360, 2):
        c, rms = _best_at(points, hu, hv, math.radians(deg))
        coarse.append((rms, deg, c))
    # refine every local minimum, keep those about as good as the best
    mins = []
    n = len(coarse)
    for i, (rms, deg, c) in enumerate(coarse):
        if rms <= coarse[i - 1][0] and rms <= coarse[(i + 1) % n][0]:
            lo, hi = deg - 2.0, deg + 2.0
            for _ in range(30):  # golden-section search on the facing
                m1, m2 = lo + (hi - lo) * 0.382, lo + (hi - lo) * 0.618
                if _best_at(points, hu, hv, math.radians(m1))[1] < _best_at(points, hu, hv, math.radians(m2))[1]:
                    hi = m2
                else:
                    lo = m1
            d = (lo + hi) / 2
            c, r = _best_at(points, hu, hv, math.radians(d))
            mins.append((r, d % 360, c))
    mins.sort()
    best = mins[0][0]
    ties = []
    for r, d, c in mins:
        if r <= best + TIE_M and all(_ang_diff(d, t[1]) > DISTINCT_DEG for t in ties):
            ties.append((r, d, c))
    how = "points"
    pick = ties[0]
    if len(ties) > 1:
        def toward(t):
            s = 0.0
            for cams, sign in ((front_cams or [], 1), (back_cams or [], -1)):
                for cam in cams:
                    dx, dy = cam[0] - t[2][0], cam[1] - t[2][1]
                    norm = math.hypot(dx, dy) or 1.0
                    s += sign * (dx * math.cos(math.radians(t[1])) + dy * math.sin(math.radians(t[1]))) / norm
            return s
        scores = [toward(t) for t in ties]
        if (front_cams or back_cams) and max(scores) - sorted(scores)[-2] > 0.3:
            pick, how = ties[scores.index(max(scores))], "cameras"
        elif facing_now is not None:
            pick, how = min(ties, key=lambda t: _ang_diff(t[1], facing_now)), "kept"
        else:
            how = "unknown"
    if facing_now is not None and how != "cameras":
        unmarked = all(e is None for _, e in points)
        c_now, r_now = _best_at(points, hu, hv, math.radians(facing_now))
        if (len(points) == 2 and unmarked) or pick[0] > r_now - TURN_M:
            pick, how = (r_now, facing_now % 360, c_now), "kept"
    r, d, c = pick
    return {"center": [round(c[0], 3), round(c[1], 3)], "facing_deg": round((d + 180) % 360 - 180, 1),
            "rms_m": round(r, 3), "facing_from": how,
            **({"ties": [round((t[1] + 180) % 360 - 180, 1) for t in ties]} if len(ties) > 1 else {})}
