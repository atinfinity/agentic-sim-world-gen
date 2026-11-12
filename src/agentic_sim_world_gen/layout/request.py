"""Match the user's request against the generated scene.

The agent splits the request into items (`Requirement`) and, where it can, attaches relations that are
measured here on the placed footprints (`RequestCheck`): which wall an object backs onto, what it stands
next to, how many there are, where it faces, which zone it is in, what it sits on. Items without checks
(style, color, the asset itself) carry the agent's own verdict.

An item is unmet if any check fails, met if every check passes, otherwise the agent's verdict (or unknown).
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from agentic_sim_world_gen.layout.validate import _inside, footprint, overlap_depth, zone_of

if TYPE_CHECKING:
    from agentic_sim_world_gen.scene.spec import RequestCheck, Requirement, ResolvedScene

Pt = tuple[float, float]

WALL_TOL = 0.15  # against_wall: footprint at most this far from the wall face [m]
NEXT_TO = 0.5  # next_to: default max gap [m]
NEAR = 1.5  # near: default max gap [m]
FACING = 45.0  # facing: default max angle [deg]
ON_TOL = 0.05  # on: bottom within this of the target's top [m]
COMPASS_DEG = {"east": 0.0, "north": 90.0, "west": 180.0, "south": 270.0}
OPENING_JA = {"door": "ドア", "window": "窓", "opening": "開口"}
COMPASS_JA = {"east": "東", "north": "北", "west": "西", "south": "南"}


def _seg_dist(p: Pt, a: Pt, b: Pt) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    L = dx * dx + dy * dy
    t = 0.0 if L == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L))
    return math.dist(p, (a[0] + t * dx, a[1] + t * dy))


def _edges(poly: list[Pt]):
    return [(poly[i], poly[(i + 1) % len(poly)]) for i in range(len(poly))]


def seg_poly_dist(a: Pt, b: Pt, poly: list[Pt]) -> float:
    if _inside(a, poly) or _inside(b, poly):
        return 0.0
    d = min(_seg_dist(p, a, b) for p in poly)
    return min([d] + [_seg_dist(q, p0, p1) for p0, p1 in _edges(poly) for q in (a, b)])


def poly_gap(p: list[Pt], q: list[Pt]) -> float:
    """Gap between two convex polygons (0 if they touch or overlap)."""
    if overlap_depth(p, q) > 0:
        return 0.0
    return min(seg_poly_dist(a, b, q) for a, b in _edges(p))


def _angle(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


class _Obj:
    """An object's placed units: footprints, facing, bottom / top heights."""

    def __init__(self, o, a):
        from agentic_sim_world_gen.layout.array import instances, placed_size
        from agentic_sim_world_gen.layout.frame import facing_of

        self.o, self.a = o, a
        self.insts = instances(o, a) if a is not None else []
        self.polys, self.facings, self.z, self.top, self.boards = [], [], [], [], []
        if a is None:
            return
        w, d, h = placed_size(o, a)
        board_z = sorted({round(b["z"], 3) for b in (a.shelves or [])})
        for i in self.insts:
            self.polys.append(footprint(tuple(i.position), (w, d), i.yaw_deg))
            self.facings.append(facing_of(a, o) + (i.yaw_deg - o.yaw_deg))
            self.z.append(i.z)
            self.top.append(i.z + h)
            self.boards.append([i.z + bz for bz in board_z])  # shelf boards (fill), lowest first

    @property
    def placed(self) -> bool:
        return bool(self.polys)

    def center(self) -> Pt:
        xs = [p[0] for poly in self.polys for p in poly]
        ys = [p[1] for poly in self.polys for p in poly]
        return ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)

    def gap(self, other: "_Obj") -> float:
        return min(poly_gap(p, q) for p in self.polys for q in other.polys)


class _Ctx:
    def __init__(self, scene: ResolvedScene):
        from agentic_sim_world_gen.layout.room import RoomGeometry

        self.scene = scene
        self.geom = RoomGeometry(scene.spec.room)
        self.objs = {}
        for p in scene.placements:
            self.objs[p.object_id] = _Obj(scene.obj(p.object_id), p.asset)

    def label(self, oid: str | None) -> str:
        if oid and oid in self.objs:
            return self.objs[oid].o.label or oid
        for w in self.geom.walls:
            for op in w.openings:
                if op.id == oid:
                    ja = OPENING_JA.get(op.kind, op.kind)
                    return ja if oid in (op.kind, "door", "window") else f"{ja}（{oid}）"
        return oid or "?"

    def opening_span(self, oid: str) -> tuple[Pt, Pt] | None:
        """The opening's two ends on its wall's centre line."""
        for w in self.geom.walls:
            for op in w.openings:
                if op.id == oid:
                    u0, u1 = w.interval(op)
                    return w.at(u0), w.at(u1)
        return None

    def opening(self, oid: str) -> Pt | None:
        for w in self.geom.walls:
            for op in w.openings:
                if op.id == oid:
                    u0, u1 = w.interval(op)
                    return w.at((u0 + u1) / 2)
        return None


def _zone_at(geom, p: Pt, areas: dict) -> str | None:
    """The zone at p; around a room with a site, the site's area there, or "outside" off the room."""
    if areas or geom.site is not None:
        x0, y0, x1, y1 = geom.footprint
        if not (x0 <= p[0] <= x1 and y0 <= p[1] <= y1):
            return next((aid for aid, a in areas.items() if _inside(p, a.poly)), "outside")
    return geom.zone_at(p)


def _result(ok: bool | None, detail: str) -> dict:
    return {"ok": ok, "detail": detail}


def _check(ctx: _Ctx, c: RequestCheck) -> dict:
    if c.kind == "count":
        ids = c.objects or ([c.object] if c.object else [])
        missing = [i for i in ids if i not in ctx.objs]
        if not ids or missing:
            return _result(None, f"物体が見つからない：{', '.join(missing) or '（指定なし）'}")
        n = sum(len(ctx.objs[i].insts) for i in ids)
        if c.value is None:
            return _result(None, f"合計 {n}（指示の数が無い）")
        return _result(n == int(round(c.value)), f"合計 {n}（指示 {int(round(c.value))}）")

    obj = ctx.objs.get(c.object or "")
    if obj is None:
        return _result(None, f"物体が見つからない：{c.object}")
    if not obj.placed:
        return _result(False, f"{ctx.label(c.object)} が配置されていない（アセットか位置が無い）")
    t = c.target or ""

    if c.kind == "against_wall":
        tol = c.value if c.value is not None else WALL_TOL
        lay = obj.o.layout
        gap = lay.gap if c.value is None and lay and lay.against_wall == t and (lay.gap or 0) > 0 else 0.0
        faces = ctx.geom.faces_for(t, zone_of(ctx.scene, obj.o))
        if not faces:
            return _result(None, f"壁が見つからない：{t}")
        d = min(seg_poly_dist(f.p0, f.p1, poly) for f in faces for poly in obj.polys)
        name = COMPASS_JA.get(t, t)
        # a gap the layout leaves on purpose (the aisle behind a counter) still counts as against the wall
        why = f"、layout の gap {gap:.2f} m を含む" if gap else ""
        return _result(d <= tol + gap, f"{name}の壁から {d:.2f} m（{tol + gap:.2f} m 以内{why}）")

    if c.kind in ("next_to", "near"):
        lim = c.value if c.value is not None else (NEXT_TO if c.kind == "next_to" else NEAR)
        if t in ctx.objs and ctx.objs[t].placed:
            d = obj.gap(ctx.objs[t])
        elif (seg := ctx.opening_span(t)) is not None:  # to the opening's width, not its centre
            d = min(seg_poly_dist(*seg, poly) for poly in obj.polys)
        else:
            return _result(None, f"相手が見つからない：{t}")
        return _result(d <= lim, f"{ctx.label(t)} まで {d:.2f} m（{lim:.2f} m 以内）")

    if c.kind == "facing":
        lim = c.value if c.value is not None else FACING
        if t in COMPASS_DEG:
            want = [COMPASS_DEG[t]] * len(obj.polys)
            name = f"{COMPASS_JA[t]}向き"
        else:
            if t in ctx.objs and ctx.objs[t].placed:
                tp = ctx.objs[t].center()
            elif (tp := ctx.opening(t)) is None:
                return _result(None, f"相手が見つからない：{t}")
            want = []
            for poly in obj.polys:
                cx, cy = sum(p[0] for p in poly) / len(poly), sum(p[1] for p in poly) / len(poly)
                want.append(math.degrees(math.atan2(tp[1] - cy, tp[0] - cx)))
            name = f"{ctx.label(t)} の方"
        worst = max(_angle(f, w) for f, w in zip(obj.facings, want))
        return _result(worst <= lim, f"{name}との差 {worst:.0f}°（{lim:.0f}° 以内）")

    if c.kind == "in_zone":
        g = ctx.geom
        areas = g.areas if g.site is not None else {}  # the site around a room: its areas and "outside"
        if t not in g.zones and t not in areas and not (t == "outside" and g.site is not None):
            return _result(None, f"ゾーンが見つからない：{t}")
        out = []
        for poly in obj.polys:
            c_ = (sum(p[0] for p in poly) / len(poly), sum(p[1] for p in poly) / len(poly))
            out.append(_zone_at(g, c_, areas))
        bad = [z for z in out if z != t and not (t == "outside" and z in areas)]
        name = g.zone_names.get(t, "敷地（部屋の外）" if t == "outside" else t)
        return _result(not bad, f"{name} の中" if not bad else f"{name} の外（{bad[0] or 'ゾーン外'}）")

    if c.kind == "on":
        base, _, unit = t.partition(".")  # "shelf" or one unit of an array, "shelf.r0c1"
        tgt = ctx.objs.get(base)
        if tgt is None or not tgt.placed:
            return _result(None, f"相手が見つからない：{t}")
        units = [k for k, i in enumerate(tgt.insts) if not unit or i.id == t]
        if not units:
            return _result(None, f"相手が見つからない：{t}")
        if obj.o.layout and obj.o.layout.on == base and not unit:
            return _result(True, f"{ctx.label(base)} の上（layout.on）")
        f = obj.o.fill
        if f is not None and f.hang and f.on == base and obj.polys and (
                not unit or any(n.startswith(unit + "_") for n, *_ in f.slots)):
            return _result(True, f"{ctx.label(base)}{' ' + unit if unit else ''} のフック（{len(obj.polys)} 個）")
        name = ctx.label(base) + (f" {unit}" if unit else "")
        on_board, n_ok = set(), 0
        for z, poly in zip(obj.z, obj.polys):
            c_ = (sum(p[0] for p in poly) / len(poly), sum(p[1] for p in poly) / len(poly))
            for k in units:
                if not _inside(c_, tgt.polys[k]):
                    continue
                if abs(z - tgt.top[k]) <= ON_TOL:
                    n_ok += 1
                    break
                lv = next((j for j, bz in enumerate(tgt.boards[k]) if abs(z - bz) <= ON_TOL), None)
                if lv is not None:
                    on_board.add(lv)
                    n_ok += 1
                    break
        n = len(obj.polys)
        if n_ok < n:
            return _result(False, f"{name} の上に無い" + (f"（{n - n_ok}/{n} 個）" if n_ok else ""))
        where = f"の棚板（{', '.join(str(v) for v in sorted(on_board))} 段目、0 = 最下段）" if on_board else "の上"
        return _result(True, f"{name} {where}" + (f"、{n} 個" if n > 1 else ""))

    return _result(None, f"未対応の種類：{c.kind}")


def evaluate(scene: ResolvedScene, reqs: list[Requirement] | None = None) -> list[dict]:
    """Each requirement with its checks measured: {text, status met|unmet|unknown, by measured|judged|none,
    note, checks [{kind, object, target, value, ok, detail}]}."""
    reqs = scene.spec.requirements if reqs is None else reqs
    if not reqs:
        return []
    ctx = _Ctx(scene)
    out = []
    for r in reqs:
        checks = []
        for c in r.checks:
            checks.append({**c.model_dump(exclude_none=True, exclude_defaults=True), "kind": c.kind, **_check(ctx, c)})
        oks = [c["ok"] for c in checks]
        if any(ok is False for ok in oks):
            status, by = "unmet", "measured"
        elif oks and all(ok is True for ok in oks):
            status, by = "met", "measured"
        else:
            status, by = (r.verdict, "judged") if r.verdict else ("unknown", "none")
        out.append({"text": r.text, "status": status, "by": by, "note": r.note, "checks": checks})
    return out


def summary(rows: list[dict]) -> dict:
    n = {k: sum(r["status"] == k for r in rows) for k in ("met", "unmet", "unknown")}
    return {**n, "total": len(rows)}
