"""Camera tilt from the photo's own lines: needs OpenCV and numpy (the `video` extra), no Isaac Sim.

measure_photo turns pixels into floor meters with the camera's tilt; 6 degrees off reads a 3.3 m aisle as 1.7 m,
and the tilt came from a look_at height somebody guessed. A room's photo carries the tilt itself:

- the vertical edges (shelf sides, door frames, wall corners) meet at one vanishing point. Its direction from
  the image center is the roll, its distance the pitch (far away = level camera);
- the level edges (floor tiles, shelf boards) point at vanishing points on the horizon. In a room whose level
  edges run in two directions at right angles, only the right focal length makes them 90 degrees apart, which
  gives the field of view; with one direction only (or a level camera and one direction) it stays open and
  `hfov_deg` is None. It is a hint: on drawn aisles it came out within 1.5 degrees, on a real living room photo
  it said 37 for what looks like a wide lens, so the tilt is computed with the caller's field of view.

`estimate(path, hfov_deg)` returns pitch_deg (up positive), roll_deg (clockwise positive), horizon_v_px (the
horizon's row at the image's center column) and how many segments agreed, or {"ok": False, "why": ...}.
"""

from __future__ import annotations

import math
import random
from pathlib import Path
from typing import Any

MAX_W = 1280  # the photo is scaled down to this width before looking for segments [px]
MIN_LEN = 0.035  # shorter segments (of the image width) are texture, not edges
VERTICAL_WITHIN = 30.0  # segments this close to the image's vertical may be vertical edges [deg]
AGREE = 1.5  # a segment agrees with a vanishing point when it points at it within this [deg]
MIN_VERTICAL = 8  # fewer agreeing vertical segments than this: no estimate
VP_MIN_DIST = 0.8  # the vertical vanishing point is at least this far from the center (of the width): pitch < ~40 deg
HFOV_GRID = (35.0, 115.0)  # fields of view tried [deg]
HFOV_BAND = 0.93  # fields of view scoring at least this share of the best are "as good"
HFOV_MAX_BAND = 14.0  # ... and when they span more than this, the field of view is open [deg]
HFOV_MIN_SCORE = 0.45  # level edges must agree at least this much on two directions at right angles


def available() -> bool:
    try:
        import cv2  # noqa: F401
        import numpy  # noqa: F401
    except Exception:
        return False
    return True


def segments(path: str | Path) -> tuple[list[tuple[float, float, float, float]], tuple[int, int]]:
    """Line segments (x1, y1, x2, y2) in the photo's own pixels, and its size."""
    import cv2

    img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise ValueError(f"cannot read {path}")
    h, w = img.shape[:2]
    k = min(1.0, MAX_W / w)
    small = cv2.resize(img, (round(w * k), round(h * k)), interpolation=cv2.INTER_AREA) if k < 1 else img
    try:
        lines = cv2.createLineSegmentDetector(0).detect(small)[0]
    except (AttributeError, cv2.error):  # builds without LSD
        edges = cv2.Canny(small, 60, 160)
        lines = cv2.HoughLinesP(edges, 1, math.pi / 360, 40, minLineLength=MIN_LEN * small.shape[1], maxLineGap=4)
    out = []
    for l in (lines if lines is not None else []):
        x1, y1, x2, y2 = (float(c) / k for c in l[0])
        if math.hypot(x2 - x1, y2 - y1) >= MIN_LEN * w:
            out.append((x1, y1, x2, y2))
    return out, (w, h)


def _off(seg, vp) -> float:
    """Angle between a segment and the line from its middle to the (homogeneous) point vp [deg]."""
    mx, my = (seg[0] + seg[2]) / 2, (seg[1] + seg[3]) / 2
    dx, dy = seg[2] - seg[0], seg[3] - seg[1]
    tx, ty = vp[0] - vp[2] * mx, vp[1] - vp[2] * my
    n = math.hypot(dx, dy) * math.hypot(tx, ty)
    if n < 1e-12:
        return 90.0
    return math.degrees(math.asin(min(1.0, abs(dx * ty - dy * tx) / n)))


def _fit_vp(segs, weights):
    """The point all segments' lines pass closest to (homogeneous, smallest eigenvector of sum w l l^T)."""
    import numpy as np

    m = np.zeros((3, 3))
    for (x1, y1, x2, y2), w in zip(segs, weights):
        l = np.cross((x1, y1, 1.0), (x2, y2, 1.0))
        l /= np.linalg.norm(l[:2]) or 1.0
        m += w * np.outer(l, l)
    return np.linalg.eigh(m)[1][:, 0]


def estimate_from(segs: list[tuple[float, float, float, float]], size: tuple[int, int],
                  hfov_deg: float | None = None) -> dict[str, Any]:
    """Tilt (and the field of view, when the lines fix it) from segments in pixels."""
    import numpy as np

    w, h = size
    # centered coordinates in units of the image width: x right, y down
    cs = [((x1 - w / 2) / w, (y1 - h / 2) / w, (x2 - w / 2) / w, (y2 - h / 2) / w) for x1, y1, x2, y2 in segs]
    ln = [math.hypot(s[2] - s[0], s[3] - s[1]) for s in cs]
    up = [i for i, s in enumerate(cs)
          if abs(math.degrees(math.atan2(s[2] - s[0], s[3] - s[1])) % 180 - 90) > 90 - VERTICAL_WITHIN]
    if len(up) < MIN_VERTICAL:
        return {"ok": False, "why": f"{len(up)} near-vertical segments (need {MIN_VERTICAL}): no shelf sides / door "
                                    "frames / wall corners to read the tilt from"}
    rng = random.Random(0)
    best, best_in = 0.0, []
    for _ in range(400):
        a, b = rng.sample(up, 2)
        vp = np.cross(np.cross((*cs[a][:2], 1.0), (*cs[a][2:], 1.0)), np.cross((*cs[b][:2], 1.0), (*cs[b][2:], 1.0)))
        n = math.hypot(vp[0], vp[1])
        if n < 1e-12 or n < VP_MIN_DIST * abs(vp[2]) or abs(vp[0]) > abs(vp[1]):
            continue  # inside the picture (an aisle's floor lines under the camera), or sideways
        ins = [i for i in up if _off(cs[i], vp) < AGREE]
        score = sum(ln[i] for i in ins)
        if score > best:
            best, best_in = score, ins
    if len(best_in) < MIN_VERTICAL:
        return {"ok": False, "why": f"only {len(best_in)} vertical segments agree on one vanishing point"}
    for _ in range(2):  # refit on everything that agrees
        vp = _fit_vp([cs[i] for i in best_in], [ln[i] for i in best_in])
        best_in = [i for i in up if _off(cs[i], vp) < AGREE] or best_in
    vp = _fit_vp([cs[i] for i in best_in], [ln[i] for i in best_in])
    if math.hypot(vp[0], vp[1]) < VP_MIN_DIST * abs(vp[2]) or abs(vp[0]) > abs(vp[1]):
        return {"ok": False, "why": "the vertical segments meet inside the picture: not a room's vertical edges"}
    if vp[1] > 0:  # up in the picture is -y
        vp = -vp
    inl = set(best_in)
    level = [i for i in range(len(cs)) if i not in inl and _off(cs[i], vp) > 3 * AGREE]

    def up_dir(f: float):
        u = np.array((vp[0] / f, vp[1] / f, vp[2]))
        return u / np.linalg.norm(u)

    def level_score(f: float) -> float:
        """How well the level segments fall into two directions at right angles under focal length f."""
        u = up_dir(f)
        e1 = np.cross(u, (0.0, 0.0, 1.0))
        e1 /= np.linalg.norm(e1)
        e2 = np.cross(u, e1)
        re = im = tot = 0.0
        for i in level:
            s = cs[i]
            d = np.cross(np.cross((s[0] / f, s[1] / f, 1.0), (s[2] / f, s[3] / f, 1.0)), u)
            n = np.linalg.norm(d)
            if n < 1e-9:
                continue
            a = 4 * math.atan2(float(d @ e2), float(d @ e1))
            re, im, tot = re + ln[i] * math.cos(a), im + ln[i] * math.sin(a), tot + ln[i]
        return math.hypot(re, im) / tot if tot else 0.0

    out: dict[str, Any] = {"ok": True, "vertical_segments": len(best_in), "level_segments": len(level), "hfov_deg": None}
    if len(level) >= MIN_VERTICAL:
        grid = [HFOV_GRID[0] + i for i in range(int(HFOV_GRID[1] - HFOV_GRID[0]) + 1)]
        sc = [level_score(0.5 / math.tan(math.radians(g) / 2)) for g in grid]
        top = max(sc)
        band = [g for g, s in zip(grid, sc) if s >= HFOV_BAND * top]
        k = sc.index(top)
        out["hfov_score"] = round(top, 2)
        if top >= HFOV_MIN_SCORE and band[-1] - band[0] <= HFOV_MAX_BAND and 0 < k < len(grid) - 1:
            out["hfov_deg"] = grid[k]
        else:
            out["hfov_open"] = [band[0], band[-1]]
    used = hfov_deg or out["hfov_deg"] or 65.0  # the caller's field of view first: the fitted one is a hint
    f = 0.5 / math.tan(math.radians(used) / 2)
    u = up_dir(f)
    pitch = math.asin(max(-1.0, min(1.0, float(u[2]))))
    roll = math.atan2(float(u[0]), -float(u[1]))
    # the horizon: points p = (x, y, 1) (in focal lengths) with p . u = 0; at the center column x = 0
    out.update({"pitch_deg": round(math.degrees(pitch), 2), "roll_deg": round(math.degrees(roll), 2),
                "hfov_used": used,
                "horizon_v_px": round(h / 2 + (-u[2] / u[1]) * f * w, 1) if abs(u[1]) > 1e-9 else None})
    return out


def estimate(path: str | Path, hfov_deg: float | None = None) -> dict[str, Any]:
    if not available():
        return {"ok": False, "why": "needs OpenCV and numpy (pip install -e '.[video]')"}
    try:
        segs, size = segments(path)
    except Exception as e:  # unreadable file
        return {"ok": False, "why": str(e)}
    return {**estimate_from(segs, size, hfov_deg), "segments": len(segs)}
