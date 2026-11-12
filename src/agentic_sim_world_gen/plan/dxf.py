"""AutoCAD DXF floor plan -> Room fields (needs `pip install ezdxf`, extra "dxf").

What is read (model space, top view):
- walls:    LINE / LWPOLYLINE / POLYLINE on wall layers (name contains WALL, 壁, A-WALL ...; or
            `wall_layers`, names or patterns like "E-B-*"). Two parallel lines 5-50 cm apart become one
            wall (centerline + thickness); a lone line becomes a wall of `default_thickness`. Lone lines
            that overlap on the same line (rooms drawn as separate rectangles) are merged.
- openings: block references (INSERT) whose block or layer name says door (DOOR, DR, ドア, 扉, 戸)
            or window (WIN, WD, 窓, サッシ). Width = the block's extent along the nearest wall.
            Wall lines broken at an opening are joined again through it.
- zones:    closed polylines on room layers (ROOM, SPACE, AREA, 部屋, 室); a TEXT / MTEXT inside
            gives the name. Without them, closed wall polylines holding exactly one text on a room layer
            (any layer if none is a room layer) become zones: single-line plans that draw each room as
            a rectangle.
Units: $INSUNITS (mm / cm / m / inch / feet); unitless drawings are guessed from their size.
`units` overrides both (some exporters write $INSUNITS=m for mm drawings: a warning says so). The result is shifted so the walls start at (0, 0).
"""

from __future__ import annotations

import fnmatch
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any

from agentic_sim_world_gen.plan.json_plan import from_dict, project_opening

INSUNITS = {1: 0.0254, 2: 0.3048, 4: 0.001, 5: 0.01, 6: 1.0}
WALL_RE = re.compile(r"wall|壁|^a-wall|躯体", re.I)
DOOR_RE = re.compile(r"door|^dr|ドア|扉|戸", re.I)
WINDOW_RE = re.compile(r"window|^win|^wd|窓|サッシ", re.I)
ROOM_RE = re.compile(r"room|space|area|部屋|室", re.I)
PAIR_MIN, PAIR_MAX = 0.05, 0.5  # wall thickness range when pairing double lines [m]


def _segments(e) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    t = e.dxftype()
    if t == "LINE":
        return [((e.dxf.start.x, e.dxf.start.y), (e.dxf.end.x, e.dxf.end.y))]
    if t in ("LWPOLYLINE", "POLYLINE"):
        pts = [(p[0], p[1]) for p in (e.get_points("xy") if t == "LWPOLYLINE" else [v.dxf.location for v in e.vertices])]
        if e.is_closed and len(pts) > 2:
            pts.append(pts[0])
        return [(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
    return []


def _polygon(e) -> list[tuple[float, float]] | None:
    t = e.dxftype()
    if t == "LWPOLYLINE" and e.is_closed:
        return [(p[0], p[1]) for p in e.get_points("xy")]
    if t == "POLYLINE" and e.is_closed:
        return [(v.dxf.location.x, v.dxf.location.y) for v in e.vertices]
    return None


def _line_params(a, b):
    L = math.dist(a, b)
    d = ((b[0] - a[0]) / L, (b[1] - a[1]) / L)
    if d[0] < -1e-9 or (abs(d[0]) <= 1e-9 and d[1] < 0):  # canonical direction
        d = (-d[0], -d[1])
    return L, d


def _pair_walls(segs, default_t: float) -> list[dict[str, Any]]:
    """Double lines -> centerlines (segs in meters). A side may be broken into several collinear pieces
    (T-junctions); the centerline runs between the averaged ends of both sides, which puts L-corners on
    the centerline corner."""
    segs = [s for s in segs if math.dist(*s) > 1e-3]
    used = [False] * len(segs)
    info = [_line_params(*s) for s in segs]
    walls = []

    def frame(i):
        a = segs[i][0]
        d = info[i][1]
        return a, d, (-d[1], d[0])

    def span(j, a, d):
        return sorted(((p[0] - a[0]) * d[0] + (p[1] - a[1]) * d[1]) for p in segs[j])

    def offset(j, a, n):
        return [(p[0] - a[0]) * n[0] + (p[1] - a[1]) * n[1] for p in segs[j]]

    def parallel(i, j):
        di, dj = info[i][1], info[j][1]
        return abs(di[0] * dj[1] - di[1] * dj[0]) < 0.01

    for i in sorted(range(len(segs)), key=lambda i: -info[i][0]):  # longest lines first
        if used[i]:
            continue
        a, d, n = frame(i)
        si = span(i, a, d)
        best = None
        for j in range(len(segs)):
            if j == i or used[j] or not parallel(i, j):
                continue
            o = offset(j, a, n)
            if abs(o[0] - o[1]) > 0.005 or not PAIR_MIN <= abs(o[0]) <= PAIR_MAX:
                continue
            sj = span(j, a, d)
            if min(si[1], sj[1]) - max(si[0], sj[0]) <= 0.01:
                continue
            if best is None or abs(o[0]) < abs(best[1]) - 1e-6:
                best = (j, o[0])
        if best is None:
            continue
        _, off = best
        side_a, side_b = [], []  # (index, span) of the pieces on each side
        for j in range(len(segs)):
            if used[j] or not parallel(i, j):
                continue
            o = offset(j, a, n)
            if abs(o[0] - o[1]) > 0.005:
                continue
            if abs(o[0]) < 0.005:
                side_a.append((j, span(j, a, d)))
            elif abs(o[0] - off) < 0.005:
                side_b.append((j, span(j, a, d)))

        def overlapping(pieces, others):
            lo, hi = min(s[0] for _, s in others), max(s[1] for _, s in others)
            return [(j, s) for j, s in pieces if s[1] > lo - 0.01 and s[0] < hi + 0.01]

        side_b = overlapping(side_b, [(i, si)])
        side_a = overlapping(side_a, side_b)
        side_b = overlapping(side_b, side_a)
        for j, _ in side_a + side_b:
            used[j] = True
        u0 = (min(s[0] for _, s in side_a) + min(s[0] for _, s in side_b)) / 2
        u1 = (max(s[1] for _, s in side_a) + max(s[1] for _, s in side_b)) / 2
        mx, my = a[0] + n[0] * off / 2, a[1] + n[1] * off / 2
        walls.append({"start": [mx + d[0] * u0, my + d[1] * u0], "end": [mx + d[0] * u1, my + d[1] * u1],
                      "thickness": round(abs(off), 4)})
    for i, (a, b) in enumerate(segs):
        if not used[i]:
            walls.append({"start": list(a), "end": list(b), "thickness": default_t})
    return walls


def _join_through(walls: list[dict[str, Any]], centers: list[tuple[float, float]], max_gap: float = 3.0):
    """Join collinear walls of equal thickness whose gap holds an opening center (walls drawn broken at doors)."""
    changed = True
    while changed:
        changed = False
        for i, w in enumerate(walls):
            for j, v in enumerate(walls):
                if i >= j or abs(w["thickness"] - v["thickness"]) > 0.01:
                    continue
                Lw, dw = _line_params(w["start"], w["end"])
                _, dv = _line_params(v["start"], v["end"])
                if abs(dw[0] * dv[1] - dw[1] * dv[0]) > 0.01:
                    continue
                a = w["start"]
                nx, ny = -dw[1], dw[0]
                if any(abs((p[0] - a[0]) * nx + (p[1] - a[1]) * ny) > 0.02 for p in (v["start"], v["end"])):
                    continue  # parallel but not on the same line
                proj = lambda p: (p[0] - a[0]) * dw[0] + (p[1] - a[1]) * dw[1]  # noqa: E731
                iw, iv = sorted(map(proj, (w["start"], w["end"]))), sorted(map(proj, (v["start"], v["end"])))
                lo_seg, hi_seg = (iw, iv) if iw[0] <= iv[0] else (iv, iw)
                g0, g1 = lo_seg[1], hi_seg[0]
                if not 0 < g1 - g0 <= max_gap:
                    continue
                if not any(g0 - 0.01 <= proj(c) <= g1 + 0.01 and abs((c[0] - a[0]) * nx + (c[1] - a[1]) * ny) < 0.5
                           for c in centers):
                    continue
                u0, u1 = min(iw[0], iv[0]), max(iw[1], iv[1])
                walls[i] = {**w, "start": [a[0] + dw[0] * u0, a[1] + dw[1] * u0], "end": [a[0] + dw[0] * u1, a[1] + dw[1] * u1]}
                del walls[j]
                changed = True
                break
            if changed:
                break
    return walls


def _merge_overlaps(walls: list[dict[str, Any]], tol: float = 0.01) -> list[dict[str, Any]]:
    """Merge collinear walls of equal thickness that overlap or touch (shared edges drawn twice)."""
    changed = True
    while changed:
        changed = False
        for i, w in enumerate(walls):
            Lw, dw = _line_params(w["start"], w["end"])
            a, (nx, ny) = w["start"], (-dw[1], dw[0])
            proj = lambda p: (p[0] - a[0]) * dw[0] + (p[1] - a[1]) * dw[1]  # noqa: E731
            iw = sorted(map(proj, (w["start"], w["end"])))
            for j in range(i + 1, len(walls)):
                v = walls[j]
                if abs(w["thickness"] - v["thickness"]) > 0.01:
                    continue
                _, dv = _line_params(v["start"], v["end"])
                if abs(dw[0] * dv[1] - dw[1] * dv[0]) > 0.01:
                    continue
                if any(abs((p[0] - a[0]) * nx + (p[1] - a[1]) * ny) > tol for p in (v["start"], v["end"])):
                    continue
                iv = sorted(map(proj, (v["start"], v["end"])))
                if min(iw[1], iv[1]) - max(iw[0], iv[0]) < -tol:  # apart
                    continue
                u0, u1 = min(iw[0], iv[0]), max(iw[1], iv[1])
                walls[i] = {**w, "start": [a[0] + dw[0] * u0, a[1] + dw[1] * u0], "end": [a[0] + dw[0] * u1, a[1] + dw[1] * u1]}
                del walls[j]
                changed = True
                break
            if changed:
                break
    return walls


def _scale_guess(doc, units: str | None) -> float:
    if units:
        return {"m": 1.0, "cm": 0.01, "mm": 0.001}[units]
    k = INSUNITS.get(doc.header.get("$INSUNITS", 0))
    if k:
        return k
    from ezdxf import bbox

    ext = bbox.extents(doc.modelspace())
    size = max(ext.size.x, ext.size.y) if ext.has_data else 0
    return 0.001 if size > 200 else 0.01 if size > 50 else 1.0  # a building is 3-200 m


def load_dxf(path: str | Path, *, units: str | None = None, wall_layers: list[str] | None = None,
             default_thickness: float = 0.12) -> dict[str, Any]:
    import ezdxf
    from ezdxf import bbox

    doc = ezdxf.readfile(str(Path(path).expanduser()))
    msp = doc.modelspace()
    k = _scale_guess(doc, units)

    def is_wall_layer(name: str) -> bool:
        if wall_layers:
            return any(fnmatch.fnmatchcase(name.upper(), pat.upper()) for pat in wall_layers)
        return bool(WALL_RE.search(name))

    segs, zones_raw, texts, inserts, wall_polys = [], [], [], [], []
    line_layers: Counter[str] = Counter()
    for e in msp:
        layer = e.dxf.get("layer", "0")
        t = e.dxftype()
        if t in ("LINE", "LWPOLYLINE", "POLYLINE"):
            line_layers[layer] += 1
        if t == "INSERT":
            name = e.dxf.name
            kind = "window" if WINDOW_RE.search(name) or WINDOW_RE.search(layer) else (
                "door" if DOOR_RE.search(name) or DOOR_RE.search(layer) else None)
            if kind:
                inserts.append((kind, e))
        elif t in ("TEXT", "MTEXT"):
            p = e.dxf.insert
            txt = (e.plain_text() if t == "MTEXT" else e.dxf.text).strip()
            texts.append(((p.x * k, p.y * k), txt.splitlines()[0].strip() if txt else "", layer))  # name line
        elif ROOM_RE.search(layer) and (poly := _polygon(e)):
            zones_raw.append([(x * k, y * k) for x, y in poly])
        elif is_wall_layer(layer):
            segs += [((a[0] * k, a[1] * k), (b[0] * k, b[1] * k)) for a, b in _segments(e)]
            if poly := _polygon(e):
                wall_polys.append([(x * k, y * k) for x, y in poly])
    top = ", ".join(f"{n} ({c})" for n, c in line_layers.most_common(12))
    if not segs:
        raise ValueError(f"no wall lines found; layers with the most lines: {top}. Pass wall_layers=[...]")
    warnings = []
    ext = bbox.extents(msp)
    if ext.has_data:
        xs = [p[0] for s_ in segs for p in s_]
        ys = [p[1] for s_ in segs for p in s_]
        if max(max(xs) - min(xs), max(ys) - min(ys)) < 0.5 * max(ext.size.x, ext.size.y) * k:
            warnings.append(f"the wall lines cover only a small part of the drawing: check the wall layers "
                            f"(wall_layers=[...]); layers with the most lines: {top}")

    walls = _merge_overlaps(_pair_walls(segs, default_thickness))
    boxes = []
    for kind, e in inserts:
        ext = bbox.extents([e])
        if not ext.has_data:
            continue
        c = ((ext.extmin.x + ext.extmax.x) / 2 * k, (ext.extmin.y + ext.extmax.y) / 2 * k)
        boxes.append((kind, e, c, (ext.extmin.x * k, ext.extmin.y * k, ext.extmax.x * k, ext.extmax.y * k)))
    walls = _join_through(walls, [b[2] for b in boxes])
    for i, w in enumerate(walls):
        w["id"] = f"w{i}"

    openings, skipped = [], []
    for kind, e, c, (x0, y0, x1, y1) in boxes:
        hit = project_opening(walls, c, max_dist=1.0)
        if hit is None:
            skipped.append(e.dxf.name)
            continue
        wid, off, _ = hit
        w = next(w for w in walls if w["id"] == wid)
        _, d = _line_params(w["start"], w["end"])
        width = abs(d[0]) * (x1 - x0) + abs(d[1]) * (y1 - y0)  # block extent along the wall (leaf + arc for doors)
        Lw = math.dist(w["start"], w["end"])
        openings.append({"kind": kind, "wall": wid, "offset": min(max(off, 0.0), Lw), "width": round(max(width, 0.3), 3)})

    from agentic_sim_world_gen.layout.room import point_in_polygon

    zones = []
    for i, poly in enumerate(zones_raw):
        name = next((txt.strip() for p, txt, _ in texts if point_in_polygon(p, poly) and txt.strip()), None)
        zones.append({"id": f"z{i}", "name": name, "polygon": [list(p) for p in poly]})
    if not zones:  # single-line plans: each room is a closed outline on a wall layer with its name inside
        labels = [(p, txt.strip()) for p, txt, layer in texts if ROOM_RE.search(layer) and txt.strip()]
        if not any(ROOM_RE.search(layer) for _, _, layer in texts):  # no room layer: any text names a room
            labels = [(p, txt.strip()) for p, txt, _ in texts if txt.strip()]
        for poly in wall_polys:
            inside = [txt for p, txt in labels if point_in_polygon(p, poly)]
            if len(inside) == 1:
                zones.append({"id": f"z{len(zones)}", "name": inside[0], "polygon": [list(p) for p in poly]})

    out = from_dict({"units": "m", "walls": walls, "openings": openings, "zones": zones,
                     "wall_thickness": default_thickness})
    size = max(max(p[i] for w in walls for p in (w["start"], w["end"])) - min(p[i] for w in walls for p in (w["start"], w["end"]))
               for i in (0, 1))
    if size > 300:
        warnings.append(f"the plan is {size:.0f} m across: the units look wrong ($INSUNITS="
                        f"{doc.header.get('$INSUNITS', 0)}); pass units='mm' / 'cm' / 'm'")
    if skipped:
        warnings.append(f"{len(skipped)} door/window blocks not near any wall: {sorted(set(skipped))}")
    if warnings:
        out["warnings"] = warnings
    return out
