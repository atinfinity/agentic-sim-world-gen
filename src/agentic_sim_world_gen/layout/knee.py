"""Tuck a chair under the desk it stands in front of, as far as the knee space allows.

The bounding boxes cannot tell whether a desk is open underneath, and a chair pushed into a desk part explodes in
PhysX (Isaac Sim check, 2026-10-25). So the parts are measured: every gprim's box of the asset (the same scan as
agentic_sim_world_gen.assets.shelves.part_boxes) is rasterised into a height grid over the footprint, in the asset's own Z-up
frame relative to its pivot (footprint center, bottom):

- "lo": per cell, the lowest bottom of the parts over it (the desk is clear below it: the knee space);
- "hi": per cell, the highest top of the parts over it (the chair reaches up to it).

The solver slides a chair that is `in_front_of` a desk-like object (validate.tuck_pair) and has no `gap` under it
by the deepest t (up to TUCK_FRAC of the chair's depth) where, over the whole overlap, the chair's "hi" stays
CLEAR below the desk's "lo" (also SIDE_CLEAR deeper and to either side), and the chair keeps validate.LEG_MARGIN off
the desk's corners. Meshes are rasterised face by face, so a desk made of one mesh still shows its knee space (only
the top's faces are over it). Other gprims count with their bounds. The grids are filled in by
service._ensure_knees before solving and kept in the candidate (AssetRecord.knee), like the shelves: each row as
one string of millimeters ("" where no part is over the cell), so a desk's grids stay a few KB in scene.resolved.json.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Callable

VERSION = 2  # 2: rows as strings of millimeters
CELL = 0.03  # grid cell [m]
MAX_CELLS = 80  # per side: a big asset gets coarser cells
CLEAR = 0.03  # the chair's parts stay this far below the desk's underside [m]
SIDE_CLEAR = 0.02  # ... and this far from its parts that come down (legs, a modesty panel) [m]
TUCK_FRAC = 0.4  # at most this fraction of the chair's depth goes under
MIN_TUCK = 0.05  # less is not worth it: keep the usual gap
STEP = 0.01  # tried depths [m]
SAMPLE = 0.015  # spacing of the checked points over the overlap [m]
MAX_FACES = 200_000  # a mesh with more faces counts with its bounds (no knee space seen: no tuck, but no long scan)


def grid_from_boxes(boxes) -> dict[str, Any] | None:
    """Height grids ("lo", "hi") of part boxes [(path, min, max)] in the asset's Z-up frame, meters."""
    boxes = [(mn, mx) for _, mn, mx in boxes]
    if not boxes:
        return None
    x0, y0 = min(b[0][0] for b in boxes), min(b[0][1] for b in boxes)
    x1, y1 = max(b[1][0] for b in boxes), max(b[1][1] for b in boxes)
    cell = max(CELL, (x1 - x0) / MAX_CELLS, (y1 - y0) / MAX_CELLS)
    nx, ny = max(1, math.ceil((x1 - x0) / cell - 1e-9)), max(1, math.ceil((y1 - y0) / cell - 1e-9))
    lo: list[list[float | None]] = [[None] * nx for _ in range(ny)]
    hi: list[list[float | None]] = [[None] * nx for _ in range(ny)]
    for mn, mx in boxes:
        i0, i1 = _span(mn[0], mx[0], x0, cell, nx)
        j0, j1 = _span(mn[1], mx[1], y0, cell, ny)
        for j in range(j0, j1):
            for i in range(i0, i1):
                lo[j][i] = round(mn[2], 3) if lo[j][i] is None else min(lo[j][i], round(mn[2], 3))
                hi[j][i] = round(mx[2], 3) if hi[j][i] is None else max(hi[j][i], round(mx[2], 3))
    return {"v": VERSION, "x0": round(x0, 4), "y0": round(y0, 4), "cell": round(cell, 5),
            "lo": [_enc(r) for r in lo], "hi": [_enc(r) for r in hi]}


def _enc(row: list[float | None]) -> str:
    return ",".join("" if v is None else str(round(v * 1000)) for v in row)


def _dec(row: str | list) -> list[float | None]:
    if not isinstance(row, str):
        return row
    return [None if v == "" else int(v) / 1000 for v in row.split(",")]


def decoded(g: dict[str, Any]) -> dict[str, Any]:
    """The grids with their rows as lists (for many lookups)."""
    return {**g, "lo": [_dec(r) for r in g["lo"]], "hi": [_dec(r) for r in g["hi"]]}


def _span(a: float, b: float, o: float, cell: float, n: int) -> tuple[int, int]:
    """Cells [i0, i1) that the interval [a, b] covers (a cell it touches by more than a hair)."""
    i0 = max(0, math.floor((a - o) / cell + 1e-6))
    i1 = min(n, max(i0 + 1, math.ceil((b - o) / cell - 1e-6)))
    return i0, i1


def at(g: dict[str, Any], key: str, x: float, y: float) -> float | None:
    """The grid's value at a point of the asset's frame; None outside the parts."""
    c = g["cell"]
    i, j = math.floor((x - g["x0"]) / c), math.floor((y - g["y0"]) / c)
    rows = g[key]
    if j < 0 or j >= len(rows):
        return None
    row = _dec(rows[j])
    return row[i] if 0 <= i < len(row) else None


def heights_of_stage(stage) -> dict[str, Any] | None:
    """Height grids of an opened asset stage: each mesh face's box (a desk made of one mesh still shows its knee
    space: only the top's faces are over it), other gprims' bounds."""
    from pxr import Usd, UsdGeom

    from agentic_sim_world_gen.assets.shelves import part_boxes

    parts, _, frame = part_boxes(stage)
    if frame is None:
        return None
    zup, mpu, piv = frame
    boxes = []
    for path, mn, mx in parts:
        prim = stage.GetPrimAtPath(path)
        mesh = UsdGeom.Mesh(prim) if prim and prim.IsA(UsdGeom.Mesh) else None
        pts = mesh.GetPointsAttr().Get() if mesh else None
        counts = mesh.GetFaceVertexCountsAttr().Get() if mesh else None
        idx = mesh.GetFaceVertexIndicesAttr().Get() if mesh else None
        if not pts or not counts or not idx or len(counts) > MAX_FACES:
            boxes.append((path, mn, mx))
            continue
        xf = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
        world = [tuple((q - c) * mpu for q, c in zip(zup(tuple(xf.Transform(p))), piv)) for p in pts]
        k = 0
        for n in counts:
            f = [world[i] for i in idx[k:k + n]]
            k += n
            boxes.append((path, tuple(min(v[a] for v in f) for a in range(3)),
                          tuple(max(v[a] for v in f) for a in range(3))))
    return grid_from_boxes(boxes)


def scan(uri: str, cache_dir: str | Path) -> dict[str, Any] | None:
    """Height grids of the asset at `uri`, cached by uri; None when it cannot be read (no tuck)."""
    from agentic_sim_world_gen.usd.reset_parts import _scan_cached

    out = _scan_cached(f"knee{VERSION}", heights_of_stage, uri, cache_dir)
    return out or None


def _local(p: tuple[float, float], pos, yaw_deg: float) -> tuple[float, float]:
    """A world point in an object's own frame (relative to its pivot, before its yaw)."""
    c, s = math.cos(math.radians(yaw_deg)), math.sin(math.radians(yaw_deg))
    dx, dy = p[0] - pos[0], p[1] - pos[1]
    return c * dx + s * dy, -s * dx + c * dy


def tuck_depth(desk: dict[str, Any], desk_pos, desk_yaw: float, desk_half: float, desk_side: float,
               chair: dict[str, Any], chair_yaw: float, chair_half: float, chair_side: float,
               toward_deg: float, z_step: float | Callable[[tuple[float, float]], float] = 0.0,
               cap: float | None = None) -> float:
    """How deep [m] the chair can go under the desk (0 = not at all).

    desk / chair: their height grids. The chair stands on the desk's side `toward_deg` (world direction from the desk),
    centered on it, facing it. desk_half / chair_half: their half extents along toward_deg; desk_side / chair_side:
    their half extents across it. z_step: the chair's floor minus the desk's (0 on one floor), or a function of the
    chair's center giving it. cap: at most this deep (validate's MAX_TUCK)."""
    from agentic_sim_world_gen.layout.validate import LEG_MARGIN

    if chair_side + LEG_MARGIN > desk_side + 1e-9:
        return 0.0  # it would run into the legs at the desk's corners
    (ux, uy), (vx, vy) = _unit(toward_deg), _unit(toward_deg + 90.0)
    desk, chair = decoded(desk), decoded(chair)
    best = 0.0
    n = math.floor(min(TUCK_FRAC * 2 * chair_half, desk_half, math.inf if cap is None else cap) / STEP + 1e-9)
    for k in range(n, round(MIN_TUCK / STEP) - 1, -1):
        t = k * STEP
        cu = desk_half + chair_half - t  # the chair's center along toward_deg, from the desk's
        cpos = (desk_pos[0] + ux * cu, desk_pos[1] + uy * cu)
        dz = z_step(cpos) if callable(z_step) else z_step
        if _clear(desk, desk_pos, desk_yaw, chair, cpos, chair_yaw, desk_half - t, desk_half, chair_side,
                  (ux, uy), (vx, vy), dz):
            best = t
            break
    return round(best, 3)


def _clear(desk, desk_pos, desk_yaw, chair, cpos, chair_yaw, u0, u1, side, u, v, z_step) -> bool:
    nu = max(1, math.ceil((u1 - u0) / SAMPLE))
    nv = max(1, math.ceil(2 * side / SAMPLE))
    for a in range(nu + 1):
        uu = u0 + (u1 - u0) * a / nu
        for b in range(nv + 1):
            vv = -side + 2 * side * b / nv
            w = (desk_pos[0] + u[0] * uu + v[0] * vv, desk_pos[1] + u[1] * uu + v[1] * vv)
            top = at(chair, "hi", *_local(w, cpos, chair_yaw))
            if top is None:
                continue
            for du, dv in ((0.0, 0.0), (-SIDE_CLEAR, 0.0), (0.0, SIDE_CLEAR), (0.0, -SIDE_CLEAR)):
                q = (w[0] + u[0] * du + v[0] * dv, w[1] + u[1] * du + v[1] * dv)  # deeper, either side
                under = at(desk, "lo", *_local(q, desk_pos, desk_yaw))
                if under is not None and top + z_step + CLEAR > under:
                    return False
    return True


def _unit(deg: float) -> tuple[float, float]:
    r = math.radians(deg)
    return math.cos(r), math.sin(r)
