"""Furniture poses refined in the photos themselves: the selected asset's box seen by the fitted cameras
against what was picked in each photo.

place_from_photos drops each foot's pixel onto the floor and fits the footprint to those floor points; a
pixel far from the camera or near the horizon lands 0.1-0.3 m off, and nothing but feet is used. Here the box
(footprint x height, on the floor) is projected into every photo that shows the object and compared in pixels with
  feet:     a located pixel (fit_photos `locate` with `object`, z = 0) against the nearest bottom corner of the box
            (`edge` front / back: only the corners of that side);
  outlines: a rectangle drawn around the object in a photo (u0, v0, u1, v1) against the bounds of the projected
            box. A side at the image border is not compared (the object runs out of the frame there).
Unknowns: the center (x, y) and the facing. The facing is turned from what it is now only when that fits clearly
better (TURN_GAIN), as in photo_place: picked pixels are loose and a box fits almost as well turned a little.

No rendering: only the asset's bounding box is drawn, so a chair's outline is that of its box (a few pixels
bigger than the chair). With a resident Isaac Sim the same residuals could come from a rendered mask.
"""
from __future__ import annotations

import math

from agentic_sim_world_gen.photo_fit import _axes, _lm

FOOT_SIGMA_PX = 8.0  # a picked foot is this loose
OUTLINE_SIGMA_PX = 16.0  # a side of a drawn rectangle is this loose (and the box is bigger than the thing)
BORDER_PX = 4.0  # an outline side this close to the image border is not compared
ROBUST = 3.0  # Cauchy scale [sigma]: one pixel on something else is given up, not followed
TURN_GAIN = 0.85  # turn the object only when the cost falls below this fraction of the cost at its facing now
TURN_MIN = 1.0  # ... and by at least this much (sigma^2): a fit that is already exact is not turned
MAX_TURN_DEG = 45.0  # ... and never farther than this from its facing now (a box looks alike turned by 90 / 180)
LOOSE = 2.0  # an rms above this many sigma: the asset is not the size of the real one, or a pick is on another
MAX_MOVE_M = 4.0  # a fit farther than this from where the object stands is not believed
NEAR_M = 0.05  # in front of the camera by at least this


def camera(cam: dict, size: tuple[int, int]) -> dict:
    """A fitted camera (photo_fit result `cameras[id]`) with the photo's (width, height) ready to project."""
    fw, rt, up = _axes(cam["yaw_deg"], cam["pitch_deg"], cam.get("roll_deg", 0.0))
    return {"pos": tuple(cam["position"]), "fw": fw, "rt": rt, "up": up, "size": tuple(size),
            "f": size[0] / 2 / math.tan(math.radians(cam["hfov_deg"]) / 2)}


def project(c: dict, X) -> tuple[float, float] | None:
    d = [X[i] - c["pos"][i] for i in range(3)]
    z = sum(d[i] * c["fw"][i] for i in range(3))
    if z < NEAR_M:
        return None
    x = sum(d[i] * c["rt"][i] for i in range(3))
    y = sum(d[i] * c["up"][i] for i in range(3))
    return c["size"][0] / 2 + c["f"] * x / z, c["size"][1] / 2 - c["f"] * y / z


def corners(x: float, y: float, facing_deg: float, size: tuple[float, float, float]):
    """The box's 8 corners: [(xyz, 'front' | 'back', top?)]. size = (along the front, across, height)."""
    ct, st = math.cos(math.radians(facing_deg)), math.sin(math.radians(facing_deg))
    out = []
    for z in (0.0, size[2]):
        for u in (size[0] / 2, -size[0] / 2):
            for v in (size[1] / 2, -size[1] / 2):
                out.append(((x + u * ct - v * st, y + u * st + v * ct, z), "front" if u > 0 else "back", z > 0))
    return out


def _robust(e: float) -> float:
    """Cauchy: e in sigma -> a residual whose square grows like log."""
    return math.copysign(ROBUST * math.sqrt(math.log1p((e / ROBUST) ** 2)), e)


def residuals(pose, size, feet, outlines, cams, robust=True) -> list[float]:
    """Residuals in sigma. feet: [(photo, (u, v), edge)], outlines: [(photo, (u0, v0, u1, v1))]."""
    cs = corners(pose[0], pose[1], pose[2], size)
    w = _robust if robust else (lambda e: e)
    r = []
    for photo, px, edge in feet:
        best = None
        for X, side, top in cs:
            if top or (edge and side != edge):
                continue
            p = project(cams[photo], X)
            if p is None:
                continue
            d = (p[0] - px[0], p[1] - px[1])
            if best is None or d[0] ** 2 + d[1] ** 2 < best[0] ** 2 + best[1] ** 2:
                best = d
        if best is None:
            best = (10 * FOOT_SIGMA_PX, 10 * FOOT_SIGMA_PX)  # behind the camera: far off
        r += [w(best[0] / FOOT_SIGMA_PX), w(best[1] / FOOT_SIGMA_PX)]
    for photo, box in outlines:
        c = cams[photo]
        ps = [p for X, _, _ in cs if (p := project(c, X)) is not None]
        W, H = c["size"]
        if len(ps) < 8:  # partly behind the camera: its bounds are not a rectangle of 8 corners
            r += [w(10.0)] * 4
            continue
        got = (min(p[0] for p in ps), min(p[1] for p in ps), max(p[0] for p in ps), max(p[1] for p in ps))
        for k, lim in enumerate((0.0, 0.0, float(W), float(H))):
            at_border = abs(box[k] - lim) <= BORDER_PX
            r.append(0.0 if at_border else w((got[k] - box[k]) / OUTLINE_SIGMA_PX))
    return r


def _rms(r: list[float]) -> float:
    n = sum(1 for e in r if e != 0.0) or 1
    return math.sqrt(sum(e * e for e in r) / n)


def refine(size: tuple[float, float, float], pose: tuple[float, float, float], feet: list, outlines: list,
           cams: dict[str, dict], starts: list[tuple[float, float]] | None = None) -> dict:
    """The pose (x, y, facing_deg) of a box of `size` (along the front, across, height) that fits the picks.

    pose: where it stands now. starts: more centers to start from (e.g. the footprint fitted on the floor).
    Returns {center, facing_deg, rms_px_was, rms_px, rms_sigma, turned, feet, outlines, photos} or {error}."""
    feet = [f for f in feet if f[0] in cams]
    outlines = [o for o in outlines if o[0] in cams]
    if not feet and not outlines:
        return {"error": "no pick of this object in a fitted photo"}
    if len(feet) < 2 and not outlines:
        return {"error": "one foot only: add another foot / corner or an outline"}
    th0 = pose[2]

    def cost(r):
        return sum(e * e for e in r)

    begin = [(pose[0], pose[1])] + list(starts or [])
    if feet and outlines:  # where the outlines alone put it: feet lock onto the nearest corners, the wrong ones
        x, _, _ = _lm(lambda v: residuals((v[0], v[1], th0), size, [], outlines, cams), list(begin[0]), [1.0, 1.0], 60)
        begin.append((x[0], x[1]))
    best = None
    for sx, sy in begin:
        x, c, _ = _lm(lambda v: residuals((v[0], v[1], th0), size, feet, outlines, cams), [sx, sy], [1.0, 1.0], 60)
        if math.dist(x, begin[0]) > MAX_MOVE_M:  # ran away (behind every camera the residuals are flat)
            continue
        if best is None or c < best[1]:
            best = ((x[0], x[1], th0), c)
    if best is None:
        return {"error": f"the picks put it more than {MAX_MOVE_M:g} m from where it stands: a pick is on another "
                         "object or the cameras are off"}
    kept = best
    turned = False
    # the facing: free from the best center, held within MAX_TURN_DEG of the facing now
    f3 = lambda v: residuals((v[0], v[1], v[2]), size, feet, outlines, cams)  # noqa: E731
    for d0 in (0.0, -20.0, 20.0):
        x, c, _ = _lm(f3, [kept[0][0], kept[0][1], th0 + d0], [1.0, 1.0, 10.0], 60)
        if (abs((x[2] - th0 + 180) % 360 - 180) <= MAX_TURN_DEG and c < TURN_GAIN * kept[1] and c < best[1]
                and kept[1] - c > TURN_MIN
                and math.dist(x[:2], begin[0]) <= MAX_MOVE_M):
            best, turned = ((x[0], x[1], x[2]), c), True
    p = best[0]

    def px(q):  # rms in pixels, not robust, over what is compared
        r = residuals(q, size, feet, outlines, cams, robust=False)
        nf = 2 * len(feet)
        e = [a * FOOT_SIGMA_PX for a in r[:nf]] + [a * OUTLINE_SIGMA_PX for a in r[nf:] if a != 0.0]
        return math.sqrt(sum(a * a for a in e) / max(1, len(e)))

    return {"center": [round(p[0], 3), round(p[1], 3)], "facing_deg": round((p[2] + 180) % 360 - 180, 1),
            "rms_px_was": round(px(pose), 1), "rms_px": round(px(p), 1),
            "rms_sigma": round(_rms(residuals(p, size, feet, outlines, cams, robust=False)), 2), "turned": turned,
            "feet": len(feet), "outlines": len(outlines),
            "photos": sorted({f[0] for f in feet} | {o[0] for o in outlines})}
