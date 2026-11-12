"""Static layout checks: oriented footprints + height ranges (3D boxes standing upright)."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from agentic_sim_world_gen.scene.site import convex_pieces
from agentic_sim_world_gen.scene.spec import STORE_PLACES

if TYPE_CHECKING:
    from agentic_sim_world_gen.assets.record import AssetRecord
    from agentic_sim_world_gen.scene.spec import ObjectSpec, ResolvedScene

Pt = tuple[float, float]

# a "tucker" may slide partly under an "over" object (chair under desk)
TUCKERS = {"chair", "stool", "armchair", "seat"}
OVERS = {"desk", "table", "workbench", "counter", "bench"}
MAX_TUCK = 0.6  # allowed penetration, as a fraction of the tucker's smaller footprint side
LEG_MARGIN = 0.05  # a tucked chair stays this far from the table's corners, where its legs stand
FLAT_SCREENS = {"tv", "television"}  # a tips_over one of these can be hung on the wall behind its stand
TIP_RATIO = 0.2  # a free body standing on something with base / height below this falls over


def footprint(center: Pt, size_wd: tuple[float, float], yaw_deg: float) -> list[Pt]:
    """Corners of a w x d rectangle centered at `center`, rotated by yaw (CCW, degrees)."""
    cx, cy = center
    w, d = size_wd
    c, s = math.cos(math.radians(yaw_deg)), math.sin(math.radians(yaw_deg))
    pts = []
    for lx, ly in ((-w / 2, -d / 2), (w / 2, -d / 2), (w / 2, d / 2), (-w / 2, d / 2)):
        pts.append((cx + lx * c - ly * s, cy + lx * s + ly * c))
    return pts


def gap(a: list[Pt], b: list[Pt]) -> float:
    """How far apart two convex polygons are along the axis that separates them most (0 if they overlap)."""
    best = 0.0
    for ax, ay in _axes(a) + _axes(b):
        n = math.hypot(ax, ay)
        if n == 0:
            continue
        pa = [(x * ax + y * ay) / n for x, y in a]
        pb = [(x * ax + y * ay) / n for x, y in b]
        best = max(best, min(pb) - max(pa), min(pa) - max(pb))
    return best


# jointed chairs (more than one rigid body: an office chair has 13, casters and seat) that touch blow up in
# PhysX and stop the GPU physics (4 chairs side by side with no gap, every headless check failed).
# Only chairs: racks, gondolas, fences and stock with several bodies stand side by side and settle fine.
JOINT_GAP = 0.03


def _axes(poly: list[Pt]) -> list[Pt]:
    out = []
    for i in range(len(poly)):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % len(poly)]
        out.append((-(y2 - y1), x2 - x1))
    return out


def overlap_depth(a: list[Pt], b: list[Pt]) -> float:
    """Separating-axis test for convex polygons. Returns penetration depth (0 if separate)."""
    best = math.inf
    for ax, ay in _axes(a) + _axes(b):
        n = math.hypot(ax, ay)
        if n == 0:
            continue
        ax, ay = ax / n, ay / n
        pa = [x * ax + y * ay for x, y in a]
        pb = [x * ax + y * ay for x, y in b]
        o = min(max(pa), max(pb)) - max(min(pa), min(pb))
        if o <= 0:
            return 0.0
        best = min(best, o)
    return best


def _inside(pt: Pt, poly: list[Pt]) -> bool:
    sign = 0
    for i in range(len(poly)):
        (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % len(poly)]
        c = (x2 - x1) * (pt[1] - y1) - (y2 - y1) * (pt[0] - x1)
        if abs(c) < 1e-9:
            continue
        if sign == 0:
            sign = 1 if c > 0 else -1
        elif (c > 0) != (sign > 0):
            return False
    return True


def _kind_words(o: ObjectSpec, a: AssetRecord | None) -> set[str]:
    text = " ".join([o.query, *(o.alt_queries or []), (a.semantic_class or "") if a else ""])
    return {w[:-1] if w.endswith("s") and len(w) > 3 else w for w in re.findall(r"[a-z]+", text.lower())}


def tuck_pair(o: ObjectSpec, a: AssetRecord | None, t: ObjectSpec, ta: AssetRecord | None) -> bool:
    """True if `o` (chair-like) may slide under `t` (desk/table-like)."""
    return bool(_kind_words(o, a) & TUCKERS) and bool(_kind_words(t, ta) & OVERS)


def through_side(chair: list[Pt], table: list[Pt], margin: float = LEG_MARGIN) -> bool:
    """True if the chair's footprint reaches under the table through one side, clear of its corners: along
    one of the table's edges the chair stays within the edge less `margin` at each end. A chair at a corner
    runs into the leg there and physics pushes it out (0.15 m)."""
    ox, oy = table[0]
    for i in (1, 3):
        ex, ey = table[i][0] - ox, table[i][1] - oy
        n = math.hypot(ex, ey)
        if n <= 2 * margin:
            continue
        ex, ey = ex / n, ey / n
        t = [(x - ox) * ex + (y - oy) * ey for x, y in chair]
        if min(t) >= margin - 1e-9 and max(t) <= n - margin + 1e-9:
            return True
    return False


def tips_over(o: ObjectSpec, a: AssetRecord, size: tuple[float, float, float]) -> bool:
    """A free body (one that moves in physics) standing on another object on a base too narrow for its height:
    a flat TV with no foot on a TV stand falls over. Hung, decor and fill items are left alone."""
    lay = o.layout
    if lay is None or not lay.on or lay.wall_height is not None or lay.ceiling or o.decor or o.fill is not None:
        return False
    if not (a.rigid_body_count or 0) > 0:
        return False
    w, d, h = size
    return h > 0 and min(w, d) / h < TIP_RATIO


# obstacle id prefix -> (issue kind, wording)
OBSTACLE_ISSUES = {
    "wall": ("hits_wall", "inside the wall"),
    "door": ("blocks_door", "in the way of the door"),
    "beam": ("hits_beam", "runs into the ceiling beam / soffit"),
    "fixture": ("hits_fixture", "runs into the hanging light"),
    "building": ("hits_building", "inside the building"),
    "curb": ("on_curb", "cuts into the raised area (set zone + layout, or z = its height)"),
    "area": ("in_keep_clear", "stands in the keep-clear area (set zone to it if intended)"),
    "level": ("on_step_edge", "straddles the edge of the raised floor (move it fully onto it or off it)"),
    "stairs": ("hits_stairs", "stands on the stairs"),
    "rail": ("hits_rail", "runs into the railing of"),
    "column": ("hits_column", "runs into the column"),
}


@dataclass
class Issue:
    kind: str  # overlap | out_of_room | too_tall | hits_wall | blocks_door | bad_opening | missing_asset
    #            | no_position | unsupported | bad_layout | hits_building | on_curb | in_keep_clear | shelf_fit
    #            | misaligned_array | on_step_edge | hits_stairs | hits_rail | stairs_into_level | hits_column
    #            | tips_over
    objects: tuple[str, ...]
    detail: str
    # ways to fix it that need no judgement, best first (service._auto_fix): {"move": id, "by": (dx, dy)}
    # | {"array": id, "aisle" | "spacing": +meters} | {"hang": id}
    fixes: tuple[dict, ...] = field(default=(), compare=False)

    def __str__(self) -> str:
        return f"[{self.kind}] {', '.join(self.objects)}: {self.detail}"



def _stairs_issues(room, geom, tol: float) -> list[Issue]:
    """Stairs in front of a door (blocks_door) or running into another level (stairs_into_level)."""
    out: list[Issue] = []
    own = {s.id: s.level for s in room.stairs}
    seen: set[tuple[str, str]] = set()
    for st in (ob for ob in geom.obstacles if ob.kind == "stairs"):
        sid = st.id.split(":", 1)[1].split("#")[0]
        for ob in geom.obstacles:
            if ob.kind not in ("door", "level"):
                continue
            name = ob.id.split(":", 1)[1].split("#")[0]
            if (ob.kind == "level" and name == own.get(sid)) or (sid, ob.id.split("#")[0]) in seen:
                continue
            if ob.z[1] > st.z[0] + tol and overlap_depth(st.poly, ob.poly) > tol:  # not stairs standing on it
                seen.add((sid, ob.id.split("#")[0]))
                out.append(Issue("blocks_door", (sid,), f"stairs {sid} are in the way of the door {name}") if ob.kind == "door"
                           else Issue("stairs_into_level", (sid,), f"stairs {sid} run into level {name}: move or turn them"))
    return out

def zone_of(scene: ResolvedScene, o: ObjectSpec) -> str | None:
    """The object's zone, or that of what it stands on (layout.on / fill.on): a crate on a table out on the
    site's terrace is outside too."""
    seen = set()
    while o is not None and o.zone is None and o.id not in seen:
        seen.add(o.id)
        sup = (o.layout.on if o.layout else None) or (o.fill.on if o.fill else None)
        if not sup:
            break
        try:
            o = scene.obj(sup)
        except (KeyError, StopIteration, ValueError):
            break
    return o.zone if o is not None else None


def check(scene: ResolvedScene, tol: float = 0.01, allow_tuck: bool = True) -> list[Issue]:
    """Arrays are checked per unit (ids "<id>.r<row>c<col>"), so aisles stay free for other objects.
    allow_tuck=False: a chair partly under a desk is an overlap too (footprints that intersect)."""
    from agentic_sim_world_gen.layout.array import instances, placed_size
    from agentic_sim_world_gen.layout.room import RoomGeometry, point_in_polygon

    room = scene.spec.room
    geom = RoomGeometry(room)
    issues: list[Issue] = [
        Issue("bad_opening", (o.id,), f"wall '{o.wall}' does not exist; walls: {[w.id for w in geom.walls]}")
        for o in geom.unknown_openings
    ]
    if geom.site is not None:  # Room.site: areas and buildings stay outside the room with its walls
        fx0, fy0, fx1, fy1 = geom.footprint
        box = [(fx0, fy0), (fx1, fy0), (fx1, fy1), (fx0, fy1)]
        for kind, items in (("area", geom.site.areas), ("building", geom.site.buildings)):
            for it in items:
                dep = max((overlap_depth(pc, box) for pc in convex_pieces(it.poly)), default=0.0)
                if dep > tol:
                    issues.append(Issue("site_in_room", (it.id,), f"site {kind} '{it.id}' reaches into the room by {dep:.2f} m"))
    # obstacles' boxes: a footprint whose box misses one cannot overlap it (thousands of stock instances)
    ob_box = [(min(x for x, _ in ob.poly), min(y for _, y in ob.poly), max(x for x, _ in ob.poly),
               max(y for _, y in ob.poly)) for ob in geom.obstacles]
    polys: dict[str, list[Pt]] = {}
    zr: dict[str, tuple[float, float]] = {}
    assets = {}
    parent: dict[str, str] = {}
    for p in scene.placements:
        o = scene.obj(p.object_id)
        a = p.asset
        if a is None:
            issues.append(Issue("missing_asset", (o.id,), f"no asset found for '{o.query}'"))
            continue
        host = geom.opening(o.opening) if o.opening is not None else None
        if o.opening is not None and host is None:
            issues.append(Issue("bad_opening", (o.id,), f"opening '{o.opening}' does not exist; openings: "
                                f"{[op.id for wl in geom.walls for op in wl.openings]}"))
            continue
        for vset, sel in o.variants.items():
            if not a.variant_sets:
                break  # not known for this asset (not a local one): authored as given
            if vset not in a.variant_sets:
                issues.append(Issue("bad_variant", (o.id,), f"no variant set '{vset}'; the asset has {a.variant_sets}"))
            elif sel not in a.variant_sets[vset]:
                issues.append(Issue("bad_variant", (o.id,), f"'{vset}' has no '{sel}'; values: {a.variant_sets[vset]}"))
        if o.screen is not None:
            issues += _screen_issues(o, a)
        elif a.screens:
            issues += _screen_content(o, a, room.place)
        if o.position is None:
            issues.append(Issue("no_position", (o.id,), "position not set"))
            continue
        zone = zone_of(scene, o)
        # a door asset sits in its opening: it pokes out of the room, into its wall and its keep-clear area
        own = {f"door:{o.opening}", f"wall:{host[0].id}"} if host else set()
        w, d, h = placed_size(o, a)
        for inst in instances(o, a):
            poly = footprint(inst.position, (w, d), inst.yaw_deg)
            polys[inst.id], zr[inst.id], assets[inst.id], parent[inst.id] = poly, (inst.z, inst.z + h), a, o.id
            if not host and not geom.inside(poly, tol, zone=zone):
                xs, ys = [x for x, _ in poly], [y for _, y in poly]
                where = ("off the site or into the room (zone " + repr(zone) + ")" if geom.outside(zone)
                         else f"vs site {room.width}x{room.depth}" if room.outdoor
                         else "outside the plan's rooms" if geom.zones else f"vs room {room.width}x{room.depth}")
                issues.append(
                    Issue(
                        "out_of_room",
                        (inst.id,),
                        f"footprint x[{min(xs):.2f},{max(xs):.2f}] y[{min(ys):.2f},{max(ys):.2f}] {where}",
                    )
                )
            hit: dict[str, float] = {}
            px0, py0 = min(x for x, _ in poly), min(y for _, y in poly)
            px1, py1 = max(x for x, _ in poly), max(y for _, y in poly)
            for ob, (ox0, oy0, ox1, oy1) in zip(geom.obstacles, ob_box):
                if ox0 >= px1 or px0 >= ox1 or oy0 >= py1 or py0 >= oy1:
                    continue
                if ob.zone is not None and ob.zone == zone:
                    continue  # e.g. a car whose zone is the road
                if ob.z[0] < inst.z + h - tol and ob.z[1] > inst.z + tol:
                    dep = overlap_depth(poly, ob.poly)
                    name = ob.id.split("#")[0]
                    if dep > tol and name not in own:
                        hit[name] = max(hit.get(name, 0.0), dep)
            for name, dep in hit.items():
                kind, what = OBSTACLE_ISSUES[name.split(":", 1)[0]]
                issues.append(Issue(kind, (inst.id,), f"{what} {name.split(':', 1)[1]} by {dep:.2f} m"))
            for v in room.levels:  # standing on a level but hanging over its edge
                if abs(inst.z - v.height) < tol and any(
                        not point_in_polygon(p, v.poly, tol) for p in poly):
                    issues.append(Issue("on_step_edge", (inst.id,), f"hangs over the edge of level {v.id}"
                                        + (" (an array stands on one floor: split it at the edge)" if inst.id != o.id else "")))
        if not room.outdoor and not geom.outside(zone) and o.z + h > room.height + tol:
            issues.append(Issue("too_tall", (o.id,), f"top {o.z + h:.2f} m > room {room.height} m"))

    issues += _stairs_issues(room, geom, tol)

    for oid in polys:
        o = scene.obj(parent[oid])
        sup = o.layout.on if o.layout else None
        if sup and sup in polys and not _inside(o.position, polys[sup]):
            issues.append(Issue("unsupported", (oid, sup), "center is not over its support"))
        elif oid == o.id and tips_over(o, assets[oid], placed_size(o, assets[oid])):
            w, d, h = placed_size(o, assets[oid])
            issues.append(Issue("tips_over", (oid,), f"{min(w, d):.2f} m deep and {h:.2f} m tall on {sup}: it has "
                                "no foot and falls over - hang it on the wall (layout.wall_height) or pick one with a stand",
                                ({"hang": oid},) if _kind_words(o, assets[oid]) & FLAT_SCREENS else ()))

    ids = list(polys)
    box = {k: (min(x for x, _ in v), min(y for _, y in v), max(x for x, _ in v), max(y for _, y in v))
           for k, v in polys.items()}
    # what each object rests on (layout.on, or the fixture a product stocks), followed down to the floor:
    # an object never "overlaps" anything under it in that chain (lamp on desk, cans in a case on a counter)
    rests = {o.id: (o.fill.on if o.fill is not None else o.layout.on if o.layout else None) for o in scene.spec.objects}

    def under(oid: str) -> set[str]:
        out, cur = set(), rests.get(oid)
        while cur and cur not in out:
            out.add(cur)
            cur = rests.get(cur)
        return out

    below = {oid: under(oid) for oid in rests}
    # sweep along x: a shop's stock makes thousands of instances, and decor placement runs this check once per
    # spot it tries (12 min for one create_scene with every pair compared)
    rank = {k: i for i, k in enumerate(ids)}
    objs = {o.id: o for o in scene.spec.objects}
    order = sorted(ids, key=lambda k: box[k][0])
    found: list[tuple[int, int, Issue]] = []
    for i, a0 in enumerate(order):
        ba0 = box[a0]
        for j in range(i + 1, len(order)):
            b0 = order[j]
            bb0 = box[b0]
            if bb0[0] >= ba0[2] - tol:
                break  # every later box starts further right
            a_id, b_id = (a0, b0) if rank[a0] < rank[b0] else (b0, a0)
            ba, bb = box[a_id], box[b_id]
            if bb[0] >= ba[2] - tol or ba[0] >= bb[2] - tol or bb[1] >= ba[3] - tol or ba[1] >= bb[3] - tol:
                continue
            pa, pb = parent[a_id], parent[b_id]
            if pa == pb:
                continue  # units of one array are laid out side by side on purpose
            if objs[pa].covers or objs[pb].covers:
                continue  # a curtain / blind hangs over what stands under the window (its fabric has no collider)
            if pb in below.get(pa, ()) or pa in below.get(pb, ()):
                continue  # resting on it, directly or through other objects
            dz = min(zr[a_id][1], zr[b_id][1]) - max(zr[a_id][0], zr[b_id][0])
            if dz <= tol:
                continue  # stacked or one is above the other
            dep = overlap_depth(polys[a_id], polys[b_id])
            if dep <= tol:
                continue
            corner = False
            for x_id, y_id in ((a_id, b_id), (b_id, a_id)):
                x, xa, y, ya = objs[parent[x_id]], assets[x_id], objs[parent[y_id]], assets[y_id]
                if allow_tuck and tuck_pair(x, xa, y, ya) and dep <= MAX_TUCK * min(xa.size_m[:2]) + tol:
                    if zr[y_id][0] > tol or through_side(polys[x_id], polys[y_id]):
                        break  # chair partly under desk: fine (the corner rule is for tables on legs on the floor)
                    corner = True
            else:
                found.append((rank[a_id], rank[b_id], Issue("overlap", (a_id, b_id), f"penetration {dep:.2f} m" + (
                    " at the table's corner, where its leg stands: move the chair along the side or out" if corner
                    else ""))))
    issues += [i for _, _, i in sorted(found, key=lambda f: f[:2])]  # the order the pairwise loop gave
    chairs = {k for k, a in assets.items() if (a.rigid_body_count or 0) > 1 and objs[parent[k]].fill is None
              and _kind_words(objs[parent[k]], a) & TUCKERS}
    return issues + _touching_chairs(chairs, polys, zr, assets, box, tol, parent, objs) + _misaligned_arrays(scene)


def _apart(a: str, b: str, need: float, box: dict, parent: dict, objs: dict) -> tuple[dict, ...]:
    """How to get two touching chairs `need` m further apart: the spacing (the aisle for two rows) of the array
    both are units of, or one of them along the line between the two."""
    pa, pb = parent[a], parent[b]
    if pa == pb:
        if objs[pa].array is None:
            return ()
        rows = [re.search(r"\.r(\d+)c\d+$", k) for k in (a, b)]  # units of different rows part by the aisle
        across = all(rows) and rows[0].group(1) != rows[1].group(1)
        return tuple({"array": pa, item: need} for item in (("aisle", "spacing") if across else ("spacing", "aisle")))
    ca = ((box[a][0] + box[a][2]) / 2, (box[a][1] + box[a][3]) / 2)
    cb = ((box[b][0] + box[b][2]) / 2, (box[b][1] + box[b][3]) / 2)
    n = math.dist(ca, cb)
    if n < 1e-6:
        return ()
    ux, uy = (cb[0] - ca[0]) / n * need, (cb[1] - ca[1]) / n * need
    return ({"move": pb, "by": (ux, uy)}, {"move": pa, "by": (-ux, -uy)})


def _touching_chairs(chairs: set[str], polys: dict, zr: dict, assets: dict, box: dict, tol: float,
                     parent: dict, objs: dict) -> list[Issue]:
    """Pairs of jointed chairs less than JOINT_GAP apart, units of one array too (an overlap is reported
    as one already)."""
    jointed = sorted(chairs, key=lambda k: box[k][0])
    out = []
    for i, a in enumerate(jointed):
        for b in jointed[i + 1:]:
            if box[b][0] >= box[a][2] + JOINT_GAP:
                break
            if box[b][1] >= box[a][3] + JOINT_GAP or box[a][1] >= box[b][3] + JOINT_GAP:
                continue
            if min(zr[a][1], zr[b][1]) - max(zr[a][0], zr[b][0]) <= tol or overlap_depth(polys[a], polys[b]) > tol:
                continue
            if (g := gap(polys[a], polys[b])) < JOINT_GAP:
                out.append(Issue("too_close", (a, b), f"{g:.2f} m apart: both are jointed chairs "
                                 f"({assets[a].rigid_body_count} bodies) and PhysX blows them up when they touch, "
                                 f"which stops the GPU physics: leave {JOINT_GAP * 100:.0f} cm (array spacing)",
                                 _apart(a, b, math.ceil((JOINT_GAP - g) * 100 + 1) / 100, box, parent, objs)))
    return out


def _misaligned_arrays(scene: ResolvedScene) -> list[Issue]:
    """Two arrays on the same spot (desk islands and their chairs) whose fronts are not parallel: usually the
    same yaw_deg given to assets with different fronts."""
    from agentic_sim_world_gen.layout.frame import facing_of

    arrs = []
    for p in scene.placements:
        o = scene.obj(p.object_id)
        if o.array is not None and o.position is not None and p.asset is not None:
            arrs.append((o, facing_of(p.asset, o)))
    out = []
    for i, (o, f) in enumerate(arrs):
        for t, g in arrs[i + 1:]:
            if math.dist(o.position, t.position) > 0.05:
                continue
            off = abs((f - g + 90.0) % 180.0 - 90.0)  # 0 = parallel (same or opposite way)
            if off > 5.0:
                out.append(Issue("misaligned_array", (o.id, t.id), f"fronts {off:.0f}° apart (facing {f % 360:.0f}° and "
                                 f"{g % 360:.0f}°); give both the same facing_deg (move_object) or layout.facing"))
    return out


# where a screen's own picture fits when its asset.json does not say (built-in signage pictures are shop ads)
SHOP_PLACES = ("shop", *STORE_PLACES, "cafe", "restaurant")


def place_fits(place: str | None, fits) -> bool:
    """A picture made for `fits` suits `place`; one made for any shop ("shop") suits every kind of shop, and
    "any" (an exit sign) suits every place, even an unset one."""
    return "any" in fits or (place is not None and (place in fits or (place in STORE_PLACES and "shop" in fits)))


def _screen_content(o, a, place: str | None) -> list[Issue]:
    """A lit screen or a printed picture showing the asset's own picture: it must suit Room.place
    (asset.json screen.places / picture.places)."""
    x = a.screens[0]
    vset, on = x.get("variant_set"), x.get("on")
    if vset and on is not None and o.variants.get(vset, on) != on:
        return []  # turned off / blank
    fits = x.get("places") or list(SHOP_PLACES)
    if place_fits(place, fits):
        return []
    off = x.get("off_variant") or "Off"
    plain = "plain" if x.get("kind") == "picture" else "dark screen"
    fix = (f'set variants {{"{vset}": "{off}"}} ({plain}) or ' if vset else "") + "set_screen with an image that fits"
    if place is None:
        return [Issue("screen_content", (o.id,), f"{a.name} shows its own picture, made for {', '.join(fits)}; "
                      f"set room.place (set_room place=...) to check it, or {fix}")]
    return [Issue("screen_content", (o.id,), f"{a.name} shows its own picture, made for {', '.join(fits)}, "
                  f"not a {place}: {fix}")]


def _screen_issues(o, a) -> list[Issue]:
    """ObjectSpec.screen: the asset must declare screens, the images must exist, and the screen must be on."""
    if not a.screens:
        return [Issue("no_screen", (o.id,), f"{a.name} declares no screen or picture whose image can be "
                      "replaced (asset.json `screen` / `picture`); pick a signage asset that does, or drop screen")]
    out = []
    names = [x["material"] for x in a.screens]
    for mat, img in o.screen.images(names).items():
        if mat not in names:
            out.append(Issue("bad_screen", (o.id,), f"no screen material '{mat}'; screens: {names}"))
        elif Path(img).expanduser().is_absolute() and not Path(img).expanduser().is_file():  # relative: scene dir
            out.append(Issue("bad_screen", (o.id,), f"screen image not found: {img}"))
    for x in a.screens:
        vset, on = x.get("variant_set"), x.get("on")
        sel = o.variants.get(vset) if vset else None
        if sel is not None and on is not None and sel != on:
            what = "makes the picture blank" if x.get("kind") == "picture" else "turns the screen off"
            out.append(Issue("screen_off", (o.id,), f"variant {vset}={sel} {what}, so the image "
                             f"does not show; set {vset}={on} or drop screen"))
            break
    return out


def _inside(pt: Pt, poly: list[Pt]) -> bool:
    """Point in a convex polygon (either winding)."""
    sides = [(b[0] - a[0]) * (pt[1] - a[1]) - (b[1] - a[1]) * (pt[0] - a[0]) for a, b in zip(poly, poly[1:] + poly[:1])]
    return all(s >= 0 for s in sides) or all(s <= 0 for s in sides)


def cameras_inside(scene: ResolvedScene) -> list[tuple[str, str]]:
    """(input id, object id) for each photo camera that stands inside a placed object (its footprint, below its
    top): the render from it shows the object's inside, e.g. all black (a fitted camera in the office
    chair the agent put where the real room had none)."""
    from agentic_sim_world_gen.layout.array import instances, placed_size

    cams = [(i.id, i.camera.position) for i in scene.spec.inputs if i.camera is not None]
    if not cams:
        return []
    out = []
    for p in scene.placements:
        o, a = scene.obj(p.object_id), p.asset
        if a is None or o.position is None:
            continue
        w, d, h = placed_size(o, a)
        for inst in instances(o, a):
            poly = footprint(inst.position, (w, d), inst.yaw_deg)
            for cid, (x, y, z) in cams:
                if inst.z - 0.01 < z < inst.z + h and _inside((x, y), poly):
                    out.append((cid, inst.id))
    return out


LOCATED_SLACK_M = 0.3  # a located foot this far outside the placed footprint still counts as matching


def _poly_dist(pt: Pt, poly: list[Pt]) -> float:
    """0 inside the convex polygon, else the distance to its nearest edge."""
    if _inside(pt, poly):
        return 0.0
    best = math.inf
    for a, b in zip(poly, poly[1:] + poly[:1]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = dx * dx + dy * dy
        u = 0.0 if L == 0 else max(0.0, min(1.0, ((pt[0] - a[0]) * dx + (pt[1] - a[1]) * dy) / L))
        best = min(best, math.dist(pt, (a[0] + u * dx, a[1] + u * dy)))
    return best


def located_misses(scene: ResolvedScene, objects: dict[str, dict]) -> tuple[list[dict], list[str]]:
    """Objects placed away from where the photos show them: `objects` is photo_fit's {id: {center, points}}.
    An object misses when one of its located points lies more than LOCATED_SLACK_M outside every footprint of it
    (array units included). Each miss: id, located center, placed position, off_m (the worst point), and `there`
    (the objects whose footprint holds the located center: often the one put in its place). Also the ids that are
    located but not in the scene."""
    from agentic_sim_world_gen.layout.array import instances, placed_size

    polys: dict[str, list[list[Pt]]] = {}
    for p in scene.placements:
        o, a = scene.obj(p.object_id), p.asset
        if a is None or o.position is None:
            continue
        w, d, _ = placed_size(o, a)
        polys[o.id] = [footprint(i.position, (w, d), i.yaw_deg) for i in instances(o, a)]
    out, unknown = [], []
    for oid, v in objects.items():
        if oid not in {o.id for o in scene.spec.objects}:
            unknown.append(oid)
            continue
        if oid not in polys:
            continue
        off = max(min(_poly_dist(tuple(xy), poly) for poly in polys[oid]) for xy in v["points"].values())
        if off <= LOCATED_SLACK_M:
            continue
        c = tuple(v["center"])
        there = [k for k, ps in polys.items() if k != oid and any(_inside(c, poly) for poly in ps)]
        o = scene.obj(oid)
        out.append({"id": oid, "located": list(c), "placed": [round(x, 2) for x in o.position],
                    "off_m": round(off, 2), **({"there": there} if there else {})})
    return out, unknown
