"""How well measure_photo reads a photo's tilt from its lines, and whether its +- holds the true width.

No Isaac Sim: aisles between two shelf rows are drawn as flat-shaded boxes (floor tiles, shelf boards, walls, a
table turned off the room's axes, noise and blur) from a camera whose tilt, roll, height and field of view are
known. The scene's camera is then set with a wrong tilt and height, the way a guessed look_at is, and the pixel
pair under the two shelf fronts is measured. Drawn boxes are cleaner than photos: read the numbers as a best case.

    python scripts/eval_photo_tilt.py <out_dir> [--n 60] [--seed 0] [--keep 6]
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
from pathlib import Path

import cv2
import numpy as np

from agentic_sim_world_gen import photo_measure, photo_vanish
from agentic_sim_world_gen.photo_measure import View
from agentic_sim_world_gen.scene.spec import PhotoCamera

SIZE = (1600, 1200)
NEAR = 0.15


def _cam(v: View, X):
    d = np.asarray(X, float) - np.asarray(v.pos)
    return np.array((d @ np.asarray(v.rt), -(d @ np.asarray(v.up)), d @ np.asarray(v.fw)))


def _poly(img, v: View, pts, color):
    """Fill a 3D polygon: clip at the near plane, then project."""
    P = [_cam(v, p) for p in pts]
    out = []
    for i, a in enumerate(P):
        b = P[(i + 1) % len(P)]
        if a[2] >= NEAR:
            out.append(a)
        if (a[2] >= NEAR) != (b[2] >= NEAR):
            t = (NEAR - a[2]) / (b[2] - a[2])
            out.append(a + t * (b - a))
    if len(out) < 3:
        return
    px = np.array([[v.size[0] / 2 + v.f * p[0] / p[2], v.size[1] / 2 + v.f * p[1] / p[2]] for p in out])
    cv2.fillPoly(img, [np.round(px * 8).astype(np.int32)], color, lineType=cv2.LINE_AA, shift=3)


def _box_faces(poly, z0, z1):
    n = len(poly)
    yield [(x, y, z1) for x, y in poly], "top"
    for i in range(n):
        (ax, ay), (bx, by) = poly[i], poly[(i + 1) % n]
        yield [(ax, ay, z0), (bx, by, z0), (bx, by, z1), (ax, ay, z1)], "side"


def draw(v: View, room: tuple[float, float, float], boxes: list[dict], rng: random.Random) -> np.ndarray:
    W, D, H = room
    img = np.full((v.size[1], v.size[0], 3), 40, np.uint8)
    tile = 0.6
    for i in range(int(W / tile) + 1):
        for j in range(int(D / tile) + 1):
            x0, y0, x1, y1 = i * tile, j * tile, min(W, (i + 1) * tile), min(D, (j + 1) * tile)
            if x1 - x0 < 1e-6 or y1 - y0 < 1e-6:
                continue
            c = 168 if (i + j) % 2 else 150
            _poly(img, v, [(x0, y0, 0), (x1, y0, 0), (x1, y1, 0), (x0, y1, 0)], (c, c + 6, c + 10))
    _poly(img, v, [(0, 0, H), (W, 0, H), (W, D, H), (0, D, H)], (225, 225, 225))
    for a, b, c in (((0, 0), (0, D), 200), ((W, 0), (W, D), 190), ((0, D), (W, D), 208), ((0, 0), (W, 0), 205)):
        _poly(img, v, [(*a, 0), (*b, 0), (*b, H), (*a, H)], (c, c, c - 8))
    faces = []
    for b in boxes:
        for pts, kind in _box_faces(b["poly"], b["z"], b["top"]):
            mid = np.mean(np.asarray(pts), axis=0)
            faces.append((float(np.linalg.norm(mid - np.asarray(v.pos))), pts, kind, b))
    for _, pts, kind, b in sorted(faces, key=lambda f: -f[0]):
        base = b["color"]
        shade = 1.0 if kind == "top" else 0.72 + 0.2 * abs(pts[1][0] - pts[0][0]) / (math.dist(pts[0][:2], pts[1][:2]) or 1)
        _poly(img, v, pts, tuple(int(c * shade) for c in base))
        if kind == "side" and b.get("boards"):
            (ax, ay, _), (bx, by, _) = pts[0], pts[1]
            z = b["z"] + 0.12
            while z < b["top"] - 0.05:  # shelf boards and what stands on them
                _poly(img, v, [(ax, ay, z), (bx, by, z), (bx, by, z + 0.03), (ax, ay, z + 0.03)], (60, 60, 70))
                k = 0.06
                while k < 0.94:
                    wd = rng.uniform(0.05, 0.14)
                    hh = rng.uniform(0.12, 0.26)
                    col = tuple(rng.randrange(40, 240) for _ in range(3))
                    p0 = (ax + (bx - ax) * k, ay + (by - ay) * k)
                    p1 = (ax + (bx - ax) * min(0.97, k + wd), ay + (by - ay) * min(0.97, k + wd))
                    _poly(img, v, [(*p0, z + 0.03), (*p1, z + 0.03), (*p1, z + 0.03 + hh), (*p0, z + 0.03 + hh)], col)
                    k += wd + rng.uniform(0.0, 0.05)
                z += 0.36
    img = cv2.GaussianBlur(img, (0, 0), 1.0)
    noise = np.random.default_rng(rng.randrange(1 << 30)).normal(0, 4.0, img.shape)
    return np.clip(img.astype(float) + noise, 0, 255).astype(np.uint8)


def trial(rng: random.Random, out: Path | None, k: int) -> dict:
    from agentic_sim_world_gen.scene.spec import InputRef, ResolvedScene, Room, SceneSpec

    W, D, H = rng.uniform(4.5, 9.0), rng.uniform(8.0, 15.0), rng.uniform(2.6, 3.4)
    aisle = rng.uniform(1.1, min(4.0, W - 1.6))
    cx = rng.uniform(aisle / 2 + 0.6, W - aisle / 2 - 0.6)
    depth_s, top = 0.45, rng.uniform(1.5, 2.1)
    boxes = []
    for side, x0 in ((-1, cx - aisle / 2 - depth_s), (1, cx + aisle / 2)):
        y = 1.2
        while y + 0.9 < D - 0.6:
            boxes.append({"poly": [(x0, y), (x0 + depth_s, y), (x0 + depth_s, y + 0.9), (x0, y + 0.9)], "z": 0.0, "top": top,
                          "color": (rng.randrange(150, 220),) * 3, "boards": True})
            y += 0.9
    # something off the room's axes: a table at the far end, turned
    a = math.radians(rng.uniform(15, 75))
    tx, ty = cx + rng.uniform(-0.3, 0.3), D - 1.4
    boxes.append({"poly": [(tx + 0.5 * math.cos(a + q), ty + 0.5 * math.sin(a + q)) for q in (0.6, math.pi - 0.6, math.pi + 0.6, -0.6)],
                  "z": 0.0, "top": 0.75, "color": (90, 120, 170), "boards": False})
    true = {"pitch": rng.uniform(-14, 8), "roll": rng.uniform(-3, 3), "height": rng.uniform(1.25, 1.65),
            "hfov": rng.uniform(55, 80)}
    pos = (cx + rng.uniform(-0.3, 0.3) * aisle, rng.uniform(0.3, 1.0))
    yaw = math.radians(90 + rng.uniform(-8, 8))
    look = (pos[0] + 5 * math.cos(yaw), pos[1] + 5 * math.sin(yaw))
    cam_true = PhotoCamera(position=(*pos, true["height"]), look_at=(*look, true["height"]), hfov_deg=true["hfov"])
    vt = View(cam_true, SIZE, pitch_deg=true["pitch"], roll_deg=true["roll"])
    img = draw(vt, (W, D, H), boxes, rng)
    path = (out or Path(".")) / f"tilt_{k:03d}.png"
    cv2.imwrite(str(path), img)
    # the camera somebody guessed: tilt and height off, the field of view right
    err = {"pitch": rng.uniform(-8, 8), "height": rng.uniform(-0.1, 0.1)}
    h_set = true["height"] * (1 + err["height"])
    p_set = true["pitch"] + err["pitch"]
    cam_set = PhotoCamera(position=(*pos, h_set), look_at=(*look, h_set + 5 * math.tan(math.radians(p_set))), hfov_deg=true["hfov"])
    # the pixel pair under the shelf fronts at one depth, as read off the picture (2 px of reading error)
    ahead = rng.uniform(2.5, 6.0)
    yy = pos[1] + ahead
    pa, pb = vt.project((cx - aisle / 2, yy, 0.0)), vt.project((cx + aisle / 2, yy, 0.0))
    row = {"k": k, "true": true, "aisle": round(aisle, 3), "ahead": round(ahead, 2), "set_err": err}
    if pa is None or pb is None or not all(0 <= q[0] < SIZE[0] and 0 <= q[1] < SIZE[1] for q in (pa, pb)):
        return {**row, "skip": "the pair is outside the picture"}
    pair = [pa[0] + rng.gauss(0, 2), pa[1] + rng.gauss(0, 2), pb[0] + rng.gauss(0, 2), pb[1] + rng.gauss(0, 2)]
    scene = ResolvedScene(spec=SceneSpec(name="t", room=Room(width=W, depth=D, height=H),
                                         inputs=[InputRef(id="photo", path=str(path), size_px=SIZE, camera=cam_set)]),
                          placements=[])
    m = photo_measure.measure(scene, floor_pairs=[pair])
    pl, fp = m.get("photo_lines") or {}, m["floor_pairs"][0]
    # the same pair through the camera as set
    vs = View(cam_set, SIZE)
    a, b = vs.floor_point(*pair[:2]), vs.floor_point(*pair[2:])
    row.update(lines_ok=bool(pl.get("ok")), why=pl.get("why"), pitch=pl.get("pitch_deg"), roll=pl.get("roll_deg"),
               hfov=pl.get("hfov_deg"), hfov_open=pl.get("hfov_open"),
               photo_m=fp.get("photo_m"), range_m=fp.get("range_m"), tilt_from=fp.get("tilt_from"),
               as_set_m=round(math.dist(a, b), 2) if a and b else None)
    if out is None:
        path.unlink()
    return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)
    rows = [trial(rng, out, k) for k in range(args.n)]
    (out / "rows.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    used = [r for r in rows if "skip" not in r]
    ok = [r for r in used if r["lines_ok"]]
    med = lambda xs: round(statistics.median(xs), 2) if xs else None  # noqa: E731
    p90 = lambda xs: round(sorted(xs)[int(0.9 * (len(xs) - 1))], 2) if xs else None  # noqa: E731
    dp = [abs(r["pitch"] - r["true"]["pitch"]) for r in ok]
    dr = [abs(r["roll"] - r["true"]["roll"]) for r in ok]
    fov = [r for r in ok if r["hfov"]]
    df = [abs(r["hfov"] - r["true"]["hfov"]) for r in fov]
    e_new = [abs(r["photo_m"] - r["aisle"]) / r["aisle"] for r in ok]
    e_old = [abs(r["as_set_m"] - r["aisle"]) / r["aisle"] for r in ok if r["as_set_m"]]
    inside = [r["range_m"][0] <= r["aisle"] <= (r["range_m"][1] if r["range_m"][1] is not None else 1e9) for r in ok]
    span = [(r["range_m"][1] - r["range_m"][0]) / r["photo_m"] for r in ok if r["range_m"][1] is not None]
    s = {"trials": len(rows), "pair_in_picture": len(used), "tilt_read": len(ok),
         "pitch_err_deg": {"median": med(dp), "p90": p90(dp), "max": round(max(dp), 2) if dp else None,
                           "within_2": sum(d <= 2 for d in dp)},
         "roll_err_deg": {"median": med(dr), "p90": p90(dr)},
         "hfov": {"given": len(fov), "err_deg_median": med(df), "err_deg_max": round(max(df), 1) if df else None},
         "width_err": {"with_lines_median": med(e_new), "with_lines_p90": p90(e_new),
                       "camera_as_set_median": med(e_old), "camera_as_set_p90": p90(e_old)},
         "range_holds_truth": f"{sum(inside)}/{len(inside)}", "range_span_of_width_median": med(span),
         "not_read": [r["why"] for r in used if not r["lines_ok"]][:5]}
    (out / "summary.json").write_text(json.dumps(s, indent=1), encoding="utf-8")
    print(json.dumps(s, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
