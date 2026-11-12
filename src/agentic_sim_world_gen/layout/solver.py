"""Relational layout: turn ObjectSpec.layout into position / yaw_deg / z.

See agentic_sim_world_gen.layout.frame for the (unverified) asset front convention.
Objects are solved in dependency order (a chair after its desk). Objects without `layout` keep
their explicit position and are treated as fixed obstacles. An object with `array` is placed as
one block (agentic_sim_world_gen.layout.array.block_record).
"""

from __future__ import annotations

import math

from agentic_sim_world_gen.assets.record import AssetRecord
from agentic_sim_world_gen.layout.array import block_record, instances
from agentic_sim_world_gen.layout.room import Face, RoomGeometry
from agentic_sim_world_gen.layout.frame import facing_of, front_local_deg, half_extent, unit, yaw_for_facing  # noqa: F401
from agentic_sim_world_gen.layout.validate import Issue, footprint, overlap_depth
from agentic_sim_world_gen.scene.spec import FACING_DIRS, ObjectSpec, ResolvedScene
from agentic_sim_world_gen.scene.fixtures import ceiling_at
from agentic_sim_world_gen.scene.walls import trim_depth

WALL_GAP = 0.02  # between a wall and an object's back
HANG_GAP = 0.005  # a thing hung on the wall (layout.wall_height): almost touching
NEAR_GAP = 0.10  # default gap for in_front_of
SIDE_GAP = 0.05  # default gap for next_to
STEP = 0.05  # sampling step along a wall



FIT_RANGE = (0.75, 1.33)  # stretch beyond this and the window looks wrong (sash bars, lock): pick another asset
COVER_RISE = 0.10  # a curtain rail above the window head
COVER_WIDER = 0.20  # a curtain / blind wider than its window (0.1 m each side)
COVER_GAP = 0.01  # between the wall face and the curtain's back


def _fit(o: ObjectSpec, a: AssetRecord, width: float, height: float | None) -> tuple[float, float, float]:
    """Scale along the asset's own x, y, z so its width seen from the front is `width` (and its height
    `height`, None = keep). The lateral axis is local x unless the asset's front is along x."""
    from agentic_sim_world_gen.layout.array import _front_on_x

    sx, sy, sz = 1.0, 1.0, 1.0
    lat = a.size_m[1] if _front_on_x(a, o) else a.size_m[0]
    k = width / lat if lat > 1e-6 else 1.0
    if _front_on_x(a, o):
        sy = k
    else:
        sx = k
    if height is not None and a.size_m[2] > 1e-6:
        sz = height / a.size_m[2]
    return (round(sx, 4), round(sy, 4), round(sz, 4))


def _fit_issue(o: ObjectSpec, opening: str) -> list[Issue]:
    lo, hi = FIT_RANGE
    bad = [f"{ax} x{k:.2f}" for ax, k in zip("xyz", o.fit_scale or ()) if not lo <= k <= hi]
    if not bad:
        return []
    return [Issue("opening_fit", (o.id, opening), f"stretched {', '.join(bad)} to fit; pick an asset closer "
                  f"to the opening's size (or change the opening)")]


def _cover(o: ObjectSpec, a: AssetRecord, geom: RoomGeometry, room_h: float) -> list[Issue]:
    """A curtain / blind in front of window o.covers: on the room side, centered, top above the window head."""
    found = geom.opening(o.covers)
    if found is None or found[1].kind != "window":
        return [Issue("bad_opening", (o.id,), f"covers '{o.covers}' is not a window; windows: "
                      f"{[op.id for w in geom.walls for op in w.openings if op.kind == 'window']}")]
    w, op = found
    (cx, cy), facing, _ = geom.opening_pose(op.id, o.zone)
    sill, top = op.resolved(w.height)
    o.fit_scale = _fit(o, a, op.width + COVER_WIDER, None)
    from agentic_sim_world_gen.layout.array import placed_size, unit_size

    d = unit_size(o, a)[1]
    h = placed_size(o, a)[2]
    off = w.thickness / 2 + COVER_GAP + d / 2
    fx, fy = unit(facing)
    o.position = (round(cx + fx * off, 4), round(cy + fy * off, 4))
    o.yaw_deg = round(yaw_for_facing(a, o, facing), 3)
    # The rail sits just above the window head; a longer (floor-length) curtain raises it up to the ceiling.
    z_top = min(max(top + COVER_RISE, h), room_h)
    o.z = round(max(z_top - h, 0.0), 4)
    if z_top - h < -0.01:
        return [Issue("opening_fit", (o.id, op.id), f"{h:.2f} m tall: longer than the {z_top:.2f} m from the "
                      "floor to the ceiling; pick a shorter one")]
    return []


def _facing_ref(lay, ids: set[str]) -> str | None:
    """layout.facing as an object id (None when it names a direction: east/west/north/south)."""
    f = lay.facing
    return None if f is None or (f not in ids and f in FACING_DIRS) else f


def _order(scene: ResolvedScene) -> tuple[list[str], list[Issue]]:
    deps: dict[str, set[str]] = {}
    ids = {o.id for o in scene.spec.objects}
    issues = []
    for o in scene.spec.objects:
        lay = o.layout
        refs = {r for r in ((lay.in_front_of, lay.next_to, lay.on, _facing_ref(lay, ids)) if lay else ()) if r}
        if o.fill:
            refs.add(o.fill.on)
        bad = refs - ids
        for r in bad:
            issues.append(Issue("bad_layout", (o.id,), f"refers to unknown object '{r}'"))
        deps[o.id] = refs & ids - {o.id}
    order, done, visiting = [], set(), set()

    def visit(i: str) -> None:
        if i in done:
            return
        if i in visiting:
            issues.append(Issue("bad_layout", (i,), "circular layout references"))
            return
        visiting.add(i)
        for j in sorted(deps[i]):
            visit(j)
        visiting.discard(i)
        done.add(i)
        order.append(i)

    for o in scene.spec.objects:
        visit(o.id)
    return order, issues


def _polys(o: ObjectSpec, a: AssetRecord, pos, yaw) -> list:
    """Footprints of an object (one per array unit) if it stood at pos/yaw."""
    q = o.model_copy(update={"position": tuple(pos), "yaw_deg": yaw})
    return [footprint(u.position, a.size_m[:2], u.yaw_deg) for u in instances(q, a)]


def _penetration(scene: ResolvedScene, asset: dict[str, AssetRecord], o: ObjectSpec, pos, yaw, placed: list[str],
                 geom: RoomGeometry | None = None) -> float:
    """Overlap of `o` at pos/yaw with already placed floor objects, walls and door clearances, unit by
    unit (aisles stay usable)."""
    a = asset[o.id]
    mine = _polys(o, a, pos, yaw)
    total = 0.0
    for pid in placed:
        q, qa = scene.obj(pid), asset.get(pid)
        if qa is None or q.position is None or q.fill is not None:
            continue
        if q.layout is not None and q.layout.on:
            continue  # sits on another placed object, which counts already
        if q.z >= o.z + a.size_m[2] - 0.01 or o.z >= q.z + qa.size_m[2] - 0.01:
            continue  # one is above the other (ceiling signage over a shelf)
        for pa in mine:
            for pb in _polys(q, qa, q.position, q.yaw_deg):
                total += overlap_depth(pa, pb)
    if geom is not None:
        top = o.z + a.size_m[2]
        for ob in geom.obstacles:
            if ob.zone is not None and ob.zone == o.zone:
                continue
            if ob.z[0] < top and ob.z[1] > o.z:
                for pa in mine:
                    total += overlap_depth(pa, ob.poly)
        for pa in mine:  # leaving the room (plan zones / outline) counts as a big penetration
            if not geom.inside(pa, zone=o.zone):
                total += 1.0
    return total


def _against_face(scene, geom, ext, asset, o, a, face: Face, along, placed) -> tuple[tuple[float, float], float, float]:
    """Best (position, yaw, penetration) with the back to one wall face. along 0..1 runs p0 -> p1."""
    e = ext[o.id]
    yaw = yaw_for_facing(a, o, face.facing)
    gap = (WALL_GAP if o.layout.wall_height is None else HANG_GAP) if o.layout.gap is None else o.layout.gap
    length = face.length
    (x0, y0), (x1, y1) = face.p0, face.p1
    dx, dy = (x1 - x0) / length, (y1 - y0) / length
    nx, ny = unit(face.facing)
    # wainscot / baseboards stand out of the walls: the gap counts from their face, and the ends keep
    # clear of the trim on the walls at the corners (their colliders would push the object out)
    trim = trim_depth(geom.room, face.wall, above=o.z) if face.kind == "wall" else 0.0
    ends = max((trim_depth(geom.room, w.id) for w in geom.walls), default=0.0) if face.kind == "wall" else 0.0
    off = half_extent(e, yaw, face.facing) + gap + trim
    t_half = half_extent(e, yaw, math.degrees(math.atan2(dy, dx))) + ends
    lo, hi = t_half, length - t_half
    if hi < lo:
        lo = hi = length / 2

    def at(t: float) -> tuple[float, float]:
        return (x0 + dx * t + nx * off, y0 + dy * t + ny * off)

    if along is not None:
        t = lo + (hi - lo) * along
        return at(t), yaw, _penetration(scene, asset, o, at(t), yaw, placed, geom)
    # from the face's middle outward in STEP increments, plus both ends
    mid = min(max(length / 2, lo), hi)
    ts, k = [mid], 1
    while mid - k * STEP >= lo - 1e-9 or mid + k * STEP <= hi + 1e-9:
        ts += [t for t in (mid - k * STEP, mid + k * STEP) if lo - 1e-9 <= t <= hi + 1e-9]
        k += 1
    ts += [lo, hi]
    best = None
    for t in ts:
        pen = _penetration(scene, asset, o, at(t), yaw, placed, geom)
        if best is None or pen < best[2] - 1e-9:
            best = (at(t), yaw, pen)
        if pen == 0:
            break
    return best


def _fill(scene: ResolvedScene, asset: dict[str, AssetRecord], o: ObjectSpec, a: AssetRecord | None) -> list[Issue]:
    """Products on the shelves of fill.on (agentic_sim_world_gen.layout.fill); position/z/yaw = the first product's."""
    from agentic_sim_world_gen.layout.fill import hang_taken, slots

    t, ta = scene.obj(o.fill.on) if o.fill.on in asset else None, asset.get(o.fill.on)
    o.fill.slots = []
    if a is None or t is None or ta is None or t.position is None:
        what = "no asset selected" if a is None else f"fixture '{o.fill.on}' is not placed"
        return [Issue("bad_layout", (o.id,), what)]
    taken = None
    if o.fill.hang:  # stacks hung on the same fixture earlier in the spec keep their hooks
        before = []
        for q in scene.spec.objects:
            if q is o:
                break
            if q.fill is not None and q.fill.hang and q.fill.on == o.fill.on and asset.get(q.id) is not None:
                before.append((q, asset[q.id]))
        taken = hang_taken(before, ta)
    o.fill.slots, problem = slots(o, a, t, ta, taken)
    if o.fill.slots:
        _, x, y, yaw, z = o.fill.slots[0]
        o.position, o.yaw_deg, o.z = (x, y), yaw, z
    else:
        o.position, o.z = t.position, t.z
    return [Issue("shelf_fit", (o.id, t.id), problem)] if problem else []


def solve(scene: ResolvedScene) -> list[Issue]:
    """Fill position/yaw_deg/z of objects that have `layout`. Mutates the scene; returns problems."""
    order, issues = _order(scene)
    geom = RoomGeometry(scene.spec.room)
    placed: list[str] = [o.id for o in scene.spec.objects
                         if o.layout is None and o.fill is None and o.opening is None and o.covers is None
                         and o.position is not None]
    asset = {p.object_id: p.asset for p in scene.placements}
    ext = {oid: block_record(scene.obj(oid), a) for oid, a in asset.items() if a is not None}

    for oid in order:
        o = scene.obj(oid)
        lay, a = o.layout, asset.get(oid)
        if o.fill is not None:
            issues += _fill(scene, asset, o, a)
            continue
        if o.opening is not None:
            pose = geom.opening_pose(o.opening, o.zone)
            o.fit_scale = None
            if pose is not None and a is not None:  # unknown opening: reported by check (bad_opening)
                (x, y), facing, o.z = pose
                o.position = (round(x, 4), round(y, 4))
                o.yaw_deg = round(yaw_for_facing(a, o, facing), 3)
                w, op = geom.opening(o.opening)
                if op.kind == "window":
                    sill, top = op.resolved(w.height)
                    o.fit_scale = _fit(o, a, op.width, top - sill)
                    issues += _fit_issue(o, op.id)
                placed.append(oid)
            continue
        if o.covers is not None:
            o.fit_scale = None
            if a is not None:
                issues += _cover(o, a, geom, scene.spec.room.height)
            continue
        if lay is None:
            if o.facing_deg is not None and a is not None:  # fixed spot, front toward facing_deg (IFC furniture)
                o.yaw_deg = round(yaw_for_facing(a, o, o.facing_deg), 3)
            floors = {0.0, *(v.height for v in scene.spec.room.levels)}
            if floors != {0.0} and o.position is not None and o.opening is None and round(o.z, 4) in floors:
                o.z = round(geom.ground_z(o.position), 4)  # moved by hand onto / off a level
            continue
        if a is None:
            issues.append(Issue("bad_layout", (oid,), "no asset selected, cannot place"))
            continue
        face_ref = _facing_ref(lay, set(asset))
        if any(r and r not in asset for r in (lay.in_front_of, lay.next_to, lay.on, face_ref)):
            continue  # reported by _order
        o.z = geom.zone_top(o.zone)  # 0, or a raised area's top (outdoor sidewalk)
        if lay.ceiling:
            if scene.spec.room.outdoor:
                issues.append(Issue("bad_layout", (oid,), "an outdoor site has no ceiling to hang from"))
                continue
            o.z = round(scene.spec.room.height - a.size_m[2], 4)
        if lay.wall_height is not None:
            o.z = round(o.z + lay.wall_height, 4)
        if lay.on:
            t, ta = scene.obj(lay.on), asset.get(lay.on)
            if ta is None or t.position is None:
                issues.append(Issue("bad_layout", (oid,), f"support '{lay.on}' is not placed"))
                continue
            tf = facing_of(ta, t)
            right, front = lay.offset or (0.0, 0.0)
            (rx, ry), (fx, fy) = unit(tf + 90.0), unit(tf)  # right of a person facing its front; its front
            o.position = (round(t.position[0] + right * rx + front * fx, 4),
                          round(t.position[1] + right * ry + front * fy, 4))
            o.z = round(t.z + (ta.size_m[2] if lay.on_height is None else lay.on_height), 4)
            o.yaw_deg = yaw_for_facing(a, o, tf)
        elif lay.in_front_of or lay.next_to:
            tid = lay.in_front_of or lay.next_to
            t, ta = scene.obj(tid), asset.get(tid)
            if ta is None or t.position is None:
                issues.append(Issue("bad_layout", (oid,), f"target '{tid}' is not placed"))
                continue
            tf = facing_of(ta, t)
            if lay.in_front_of:
                o.yaw_deg = yaw_for_facing(a, o, tf + 180.0)  # face the target
                # No automatic tuck under desks: the bbox can't tell whether the knee space is open,
                # and an overlapping chair explodes in PhysX (Isaac Sim check, 2026-10-25).
                gap = NEAR_GAP if lay.gap is None else lay.gap
                dist = half_extent(ext[tid], t.yaw_deg, tf) + half_extent(ext[oid], o.yaw_deg, tf) + gap
                ux, uy = unit(tf)
            else:
                o.yaw_deg = yaw_for_facing(a, o, tf)
                lat = tf + (90.0 if lay.side == "right" else -90.0)  # viewer faces the target's front
                gap = SIDE_GAP if lay.gap is None else lay.gap
                dist = half_extent(ext[tid], t.yaw_deg, lat) + half_extent(ext[oid], o.yaw_deg, lat) + gap
                ux, uy = unit(lat)
            o.position = (round(t.position[0] + ux * dist, 4), round(t.position[1] + uy * dist, 4))
        elif lay.against_wall:
            faces = geom.faces_for(lay.against_wall, o.zone)
            if not faces:
                where = f" in zone '{o.zone}'" if o.zone else ""
                issues.append(Issue("bad_layout", (oid,), f"no wall '{lay.against_wall}'{where}"))
                continue
            best = None
            for face in faces:
                cand = _against_face(scene, geom, ext, asset, o, a, face, lay.along, placed)
                if best is None or cand[2] < best[2] - 1e-9:
                    best = cand
                if cand[2] == 0:
                    break
            (x, y), o.yaw_deg, _ = best
            o.position = (round(x, 4), round(y, 4))
        elif lay.center:
            cx, cy = geom.center(o.zone)
            o.position = (round(cx, 4), round(cy, 4))
        elif o.position is None:
            issues.append(Issue("bad_layout", (oid,), "layout has no placement rule and no position"))
            continue

        if lay.facing and not (lay.against_wall or lay.in_front_of or lay.next_to):
            if face_ref is None:
                o.yaw_deg = yaw_for_facing(a, o, FACING_DIRS[lay.facing])
            else:
                t = scene.obj(face_ref)
                if t.position is not None and o.position is not None:
                    dx, dy = t.position[0] - o.position[0], t.position[1] - o.position[1]
                    o.yaw_deg = yaw_for_facing(a, o, math.degrees(math.atan2(dy, dx)))
        if lay.ceiling and o.position is not None:  # under a beam: from the beam's bottom
            o.z = round(ceiling_at(scene.spec.room, *o.position) - a.size_m[2], 4)
        if (not lay.on and not lay.ceiling and o.position is not None
                and (o.zone is None or not (scene.spec.room.outdoor or geom.outside(o.zone)))):
            # indoors a level under it lifts it; outdoors and outside the room the area's top did
            o.z = round(geom.ground_z(o.position) + (lay.wall_height or 0.0), 4)
        o.yaw_deg = round(o.yaw_deg, 3)
        placed.append(oid)
    return issues
