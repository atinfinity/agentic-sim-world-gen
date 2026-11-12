"""Shelf stocking (ObjectSpec.fill): rows and columns of a product on a fixture's shelf boards.

The boards come from the fixture's geometry (agentic_sim_world_gen.assets.shelves, in the fixture's Z-up pivot
frame). Per board, products stand in columns across the board's width (as seen from the fixture's
front) and rows from the front edge backward, facing the same way as the fixture (fill.lay_flat: lying on
their backs, front up). A level is skipped when the product is taller than the clearance to the board above.

fill.hang: the products hang on the fixture's hooks instead (blister packs on a pegboard), one stack per
hook from the tip toward the base, facing out along the hook. Hooks are taken from the top row down; a hook
whose stack would run into a stack already hung (the row above, the next hook) or a shelf board is left empty.
"""

from __future__ import annotations

import math

from agentic_sim_world_gen.assets.record import AssetRecord
from agentic_sim_world_gen.layout.array import instances, unit_size
from agentic_sim_world_gen.layout.frame import front_local_deg, norm_deg, yaw_for_facing
from agentic_sim_world_gen.scene.spec import ObjectSpec

MARGIN = 0.01  # from the board's front / back edge [m]
LIFT = 0.002  # products start this far above the board (PhysX settles them)
HEAD = 0.01  # min space between the product top and the board above [m]
HANG_TOP = 0.012  # a hung card's top is this far above the hook's center (the hole sits just under the top) [m]
TIP = 0.015  # first hung product's front this far in from the hook's tip (the tip cap / price tag) [m]
BASE = 0.01  # last hung product's back this far from the hook's base [m]


def levels(shelves: list[dict]) -> list[list[dict]]:
    """Boards grouped by height (a cooler has one board per door at each level), lowest first."""
    out: list[list[dict]] = []
    for s in sorted(shelves, key=lambda s: (s["z"], s["x0"], s["y0"])):
        if out and abs(out[-1][0]["z"] - s["z"]) < 0.01:
            out[-1].append(s)
        else:
            out.append([s])
    return out


def _board_frame(s: dict, front_deg: float):
    """(front edge point, back direction, lateral direction (left -> right seen from the front),
    width, depth) of a board in the fixture frame. The front snaps to the nearest axis."""
    k = round(norm_deg(front_deg) / 90.0) % 4  # 0 +X, 1 +Y, 2 -X, 3 -Y
    cx, cy = (s["x0"] + s["x1"]) / 2, (s["y0"] + s["y1"]) / 2
    w, d = s["x1"] - s["x0"], s["y1"] - s["y0"]
    if k == 0:
        return (s["x1"], cy), (-1.0, 0.0), (0.0, 1.0), d, w
    if k == 1:
        return (cx, s["y1"]), (0.0, -1.0), (-1.0, 0.0), w, d
    if k == 2:
        return (s["x0"], cy), (1.0, 0.0), (0.0, -1.0), d, w
    return (cx, s["y0"]), (0.0, 1.0), (1.0, 0.0), w, d


def hook_rows(hooks: list[dict]) -> list[list[dict]]:
    """Hooks grouped by height, lowest row first."""
    out: list[list[dict]] = []
    for h in sorted(hooks, key=lambda h: h["z"]):
        if out and abs(out[-1][0]["z"] - h["z"]) < 0.01:
            out[-1].append(h)
        else:
            out.append([h])
    return out


def _hang_plan(o: ObjectSpec, a: AssetRecord, ta: AssetRecord, taken: list[tuple[float, ...]]) -> tuple[list, list, set]:
    """Stacks of `o` on the hooks of `ta` (fixture frame): ([(row, col, [(x, y)], facing_deg, bottom z)], their
    boxes, rows skipped), around the boxes `taken` by other products on the same fixture."""
    f = o.fill
    rows = hook_rows(ta.hooks or [])
    want = range(len(rows)) if f.levels is None else [i for i in f.levels if 0 <= i < len(rows)]
    lat, dep = unit_size(o, a)
    h = a.size_m[2]
    sx, sy = ta.size_m[0], ta.size_m[1]
    boards = [(b["x0"], b["y0"], b["z"] - 0.03, b["x1"], b["y1"], b["z"]) for b in ta.shelves or []]
    plan = []
    used: list[tuple[float, ...]] = []  # boxes of the stacks hung so far
    skipped = set()
    for ri in sorted(want, reverse=True):  # top row first: lower rows give way to the stacks above
        for col, k in enumerate(sorted(rows[ri], key=lambda k: _across(k, sx, sy))):
            if k["busy"]:
                continue
            (bx, by), (tx, ty) = k["base"], k["tip"]
            length = math.hypot(tx - bx, ty - by)
            fx, fy = (tx - bx) / length, (ty - by) / length  # out along the hook = the products' front
            if not f.span[0] - 1e-6 <= _across(k, sx, sy) <= f.span[1] + 1e-6:
                continue
            n = int((length - TIP - BASE + f.gap) // (dep + f.gap))
            if f.rows is not None:
                n = min(n, f.rows)
            z0 = k["z"] + HANG_TOP - h
            if n < 1 or z0 < 0.02:
                skipped.add(ri)
                continue
            cs = [(tx - fx * (TIP + dep / 2 + i * (dep + f.gap)), ty - fy * (TIP + dep / 2 + i * (dep + f.gap)))
                  for i in range(n)]
            rx, ry = abs(fy), abs(fx)  # sideways extent of a product, per axis (hooks run along an axis)
            hx, hy = abs(fx) * dep / 2 + rx * lat / 2, abs(fy) * dep / 2 + ry * lat / 2
            box = (min(c[0] for c in cs) - hx, min(c[1] for c in cs) - hy, z0,
                   max(c[0] for c in cs) + hx, max(c[1] for c in cs) + hy, z0 + h)
            if any(_hits(box, u) for u in used + boards + taken):
                skipped.add(ri)
                continue
            used.append(box)
            plan.append((ri, col, cs, math.degrees(math.atan2(fy, fx)), z0))
    return plan, used, skipped


def _hang(o: ObjectSpec, a: AssetRecord, t: ObjectSpec, ta: AssetRecord,
          taken: list[tuple[float, ...]] | None = None) -> tuple[list, str | None]:
    f = o.fill
    hooks = ta.hooks or []
    if not hooks:
        return [], f"no hooks found on '{t.id}' ({ta.name})"
    rows = hook_rows(hooks)
    want = range(len(rows)) if f.levels is None else f.levels
    if bad := [i for i in want if not 0 <= i < len(rows)]:
        return [], f"hook rows {bad} do not exist; '{t.id}' has {len(rows)} (0 = lowest)"
    lat, dep = unit_size(o, a)
    h = a.size_m[2]
    plan, _, skipped = _hang_plan(o, a, ta, taken or [])
    out = []
    units = [u for u in instances(t, ta)
             if f.units is None or u.id == t.id or u.id.split(".", 1)[1] in f.units]
    if f.units and not units:
        return [], f"units {f.units} not in '{t.id}'"
    for u in units:
        uname = u.id.split(".", 1)[1] if u.id != t.id else "u"
        c, s_ = math.cos(math.radians(u.yaw_deg)), math.sin(math.radians(u.yaw_deg))
        for ri, col, cs, facing, z0 in sorted(plan):
            yaw = round(yaw_for_facing(a, o, u.yaw_deg + facing), 3)
            for i, (px, py) in enumerate(cs):
                x = u.position[0] + c * px - s_ * py
                y = u.position[1] + s_ * px + c * py
                out.append((f"{uname}_h{ri}_{col}_{i}", round(x, 4), round(y, 4), yaw, round(u.z + z0, 4)))
    problem = None
    if not out:
        free = sum(1 for k in hooks if not k["busy"])
        problem = (f"nothing hangs on '{t.id}': product {lat:.2f} x {dep:.2f} x {h:.2f} m (w x d x h); "
                   f"{free} of {len(hooks)} hooks free, rows at z " + ", ".join(f"{r[0]['z']:.2f}" for r in rows))
    elif skipped and f.levels is not None and not f.fit_only:
        problem = f"hook rows {sorted(skipped)} skipped: the product does not fit there"
    return out, problem


def _across(k: dict, sx: float, sy: float) -> float:
    """Where a hook is across the fixture, 0 = left .. 1 = right as seen from the front."""
    (bx, by), (tx, ty) = k["base"], k["tip"]
    length = math.hypot(tx - bx, ty - by) or 1.0
    fx, fy = (tx - bx) / length, (ty - by) / length
    rx, ry = -fy, fx  # the viewer's right (the viewer faces -front)
    w = abs(rx) * sx + abs(ry) * sy
    return 0.5 + (tx * rx + ty * ry) / w if w else 0.5


def _hits(p: tuple[float, ...], q: tuple[float, ...]) -> bool:
    e = 1e-4
    return all(p[i] < q[i + 3] - e and q[i] < p[i + 3] - e for i in range(3))


def hang_taken(before: list[tuple[ObjectSpec, AssetRecord]], ta: AssetRecord) -> list[tuple[float, ...]]:
    """Boxes (fixture frame) of the stacks that the products `before` (hung on the same fixture, in order) take."""
    taken: list[tuple[float, ...]] = []
    for o, a in before:
        taken += _hang_plan(o, a, ta, taken)[1]
    return taken


def slots(o: ObjectSpec, a: AssetRecord, t: ObjectSpec, ta: AssetRecord,
          taken: list[tuple[float, ...]] | None = None) -> tuple[list, str | None]:
    """[(name, x, y, yaw_deg, z)] for the product `o` on fixture `t`, and a problem (or None). taken: hooks only,
    the boxes of products hung on `t` before `o` (hang_taken)."""
    f = o.fill
    if f.hang:
        return _hang(o, a, t, ta, taken)
    shelves = ta.shelves or []
    if not shelves:
        return [], f"no shelf boards found on '{t.id}' ({ta.name})"
    lv = levels(shelves)
    want = range(len(lv)) if f.levels is None else f.levels
    if bad := [i for i in want if not 0 <= i < len(lv)]:
        return [], f"levels {bad} do not exist; '{t.id}' has {len(lv)} (0 = lowest)"
    lat, dep = unit_size(o, a)
    h = a.size_m[2]
    if f.lay_flat:  # front up: the depth is the product's height, the height its depth
        dep, h = h, dep
    fl = front_local_deg(ta, t)
    out, skipped = [], []
    units = [u for u in instances(t, ta)
             if f.units is None or u.id == t.id or u.id.split(".", 1)[1] in f.units]
    if f.units and not units:
        return [], f"units {f.units} not in '{t.id}'"
    for u in units:
        uname = u.id.split(".", 1)[1] if u.id != t.id else "u"
        c, s_ = math.cos(math.radians(u.yaw_deg)), math.sin(math.radians(u.yaw_deg))
        yaw = round(yaw_for_facing(a, o, u.yaw_deg + fl), 3)
        for li in want:
            for bi, b in enumerate(lv[li]):
                clear = b["clear"]
                if clear is not None and h > clear - HEAD:
                    skipped.append(li)
                    continue
                (ex, ey), (bx, by), (lx, ly), width, depth = _board_frame(b, fl)
                w0, w1 = width * f.span[0], width * f.span[1]
                n = int((w1 - w0 + f.gap) // (lat + f.gap))
                m = int((depth - 2 * MARGIN + f.gap) // (dep + f.gap))
                if f.rows is not None:
                    m = min(m, f.rows)
                if n < 1 or m < 1:
                    skipped.append(li)
                    continue
                used = n * lat + (n - 1) * f.gap
                start = -width / 2 + (w0 + w1) / 2 - used / 2 + lat / 2  # columns centered in the span
                for col in range(n):
                    du = start + col * (lat + f.gap)
                    for row in range(m):
                        dv = MARGIN + dep / 2 + row * (dep + f.gap)
                        px, py = ex + lx * du + bx * dv, ey + ly * du + by * dv
                        x = u.position[0] + c * px - s_ * py
                        y = u.position[1] + s_ * px + c * py
                        name = f"{uname}_l{li}" + (f"b{bi}" if len(lv[li]) > 1 else "") + f"_{col}_{row}"
                        out.append((name, round(x, 4), round(y, 4), yaw, round(u.z + b["z"] + LIFT, 4)))
    problem = None
    if not out:
        problem = (f"nothing fits on '{t.id}': product {lat:.2f} x {dep:.2f} x {h:.2f} m (w x d x h); "
                   f"boards: " + ", ".join(f"{i}: z {g[0]['z']:.2f} clear {g[0]['clear'] or 'open'}" for i, g in enumerate(lv)))
    elif skipped and f.levels is not None and not f.fit_only:
        problem = f"levels {sorted(set(skipped))} skipped: the product does not fit there"
    return out, problem
