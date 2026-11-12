"""Synthetic data plan: per-frame camera poses and randomization, decided here in plain Python.

scripts/isaac/sdg.py (Isaac Sim python) only applies the plan and runs the Replicator writers, so
the sampling is reproducible from the seed and testable offline.

Cameras stand in free floor space (clearance from walls, doors and objects), are aimed at an object
(center of its box, jittered), and need a clear line of sight to it (walls and taller objects in
between reject the pose). If that fails too often the line-of-sight test is dropped and a warning added.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from agentic_sim_world_gen.layout.array import instances, placed_size
from agentic_sim_world_gen.layout.room import RoomGeometry, point_in_polygon
from agentic_sim_world_gen.layout.validate import footprint, overlap_depth
from agentic_sim_world_gen.scene.looks import SURFACES, ceiling_surface, resolve_surface
from agentic_sim_world_gen.scene.spec import ResolvedScene, SdgMaterialChoice, SdgSpec

Pt = tuple[float, float]

FLOOR_PRESETS = ("wood_light", "wood_dark", "tatami", "carpet_gray", "tile_white", "concrete", "epoxy_gray")
WALL_PRESETS = ("paint_white", "paint_beige", "drywall_gray", "brick", "metal_panel")
GROUND_PRESETS = ("grass", "soil", "gravel", "concrete", "asphalt")
TRIES = 400  # pose attempts per frame


def semantic_class(o, a) -> str:
    """Class label written on the object (UsdSemantics labels, taxonomy "class")."""
    c = o.semantic_class or (a.semantic_class if a else None) or o.query
    return " ".join(c.lower().replace("_", " ").split())


def prim_path(inst) -> str:
    """USD path of one placed object (array units sit under their group, see usd/compose.py)."""
    if inst.id == inst.parent:
        return f"/World/Objects/{inst.id}"
    return f"/World/Objects/{inst.parent}/{inst.id.replace('.', '_')}"


@dataclass
class Box:
    id: str
    parent: str
    cls: str
    path: str
    poly: list[Pt]
    z: tuple[float, float]

    @property
    def center(self) -> tuple[float, float, float]:
        xs, ys = [p[0] for p in self.poly], [p[1] for p in self.poly]
        return (sum(xs) / 4, sum(ys) / 4, (self.z[0] + self.z[1]) / 2)


# Class names an agent gives the things it does not want detected: they belong in the background
OTHER_NAMES = {"other", "others", "misc", "unknown", "background", "none"}


def boxes(scene: ResolvedScene, classes: dict[str, str] | None = None, others: str = "label") -> list[Box]:
    """One box per placed unit. cls "" = no label: classes[id] == "", or others="background" for the objects
    not in a non-empty `classes`."""
    out = []
    classes = classes or {}
    for p in scene.placements:
        o, a = scene.obj(p.object_id), p.asset
        if a is None or o.position is None:
            continue
        w, d, h = placed_size(o, a)
        if o.id in classes:
            cls = " ".join(classes[o.id].lower().replace("_", " ").split())
        else:
            cls = "" if classes and others == "background" else semantic_class(o, a)
        for inst in instances(o, a):
            out.append(Box(inst.id, o.id, cls, prim_path(inst),
                           footprint(inst.position, (w, d), inst.yaw_deg), (inst.z, inst.z + h)))
    return out


def _square(p: Pt, r: float) -> list[Pt]:
    x, y = p
    return [(x - r, y - r), (x + r, y - r), (x + r, y + r), (x - r, y + r)]


def stuff_classes(room) -> list[str]:
    """Labels compose puts on the room itself (not objects): floor/wall/ceiling, or ground/areas/building."""
    if room.outdoor:
        return list(dict.fromkeys(["ground", *(a.kind for a in room.areas), *(["building"] if room.buildings else [])]))
    # auto_sliding doors are labelled "door" too (agentic_sim_world_gen.usd.door); left out of COCO's categories they were
    # CocoWriter's fallback "other"
    door = any(o.style == "auto_sliding" for w in RoomGeometry(room).walls for o in w.openings)
    return ["floor", "wall", *(["ceiling"] if room.ceiling else []), *(["door"] if door else []),
            *(["column"] if room.columns else [])]


class _Free:
    """Where a camera may stand and what blocks its view."""

    def __init__(self, scene: ResolvedScene, sdg: SdgSpec, bx: list[Box]):
        self.geom = RoomGeometry(scene.spec.room)
        self.zone = self.geom.zones.get(sdg.camera.zone) if sdg.camera.zone else None
        self.c = sdg.camera.clearance
        self.boxes = bx
        self.walls = [ob for ob in self.geom.obstacles if ob.kind in ("wall", "building", "column")]
        self.doors = [ob for ob in self.geom.obstacles if ob.kind == "door"]

    def stand_ok(self, p: Pt, z: float) -> bool:
        sq = _square(p, max(self.c, 0.05))
        if self.zone is not None:
            if not all(point_in_polygon(q, self.zone) for q in sq):
                return False
        elif not self.geom.inside(sq, tol=0.0):
            return False
        if any(overlap_depth(sq, ob.poly) > 0 for ob in self.walls):
            return False
        if any(point_in_polygon(p, ob.poly) for ob in self.doors):
            return False
        for b in self.boxes:  # not inside (or right next to) anything at camera height or below it
            if b.z[0] <= z + self.c and overlap_depth(sq, b.poly) > 0:
                return False
        return True

    def sees(self, eye: tuple[float, float, float], at: tuple[float, float, float], target: str) -> bool:
        """Sampled line of sight: no wall, and no other object whose box contains a point on the ray."""
        n = max(8, int(math.dist(eye, at) / 0.1))
        for k in range(1, n):
            t = k / n
            q = tuple(e + (a - e) * t for e, a in zip(eye, at))
            if any(ob.z[0] <= q[2] <= ob.z[1] and point_in_polygon(q[:2], ob.poly) for ob in self.walls):
                return False
            for b in self.boxes:
                if b.parent != target and b.z[0] <= q[2] <= b.z[1] and point_in_polygon(q[:2], b.poly):
                    return False
        return True


# --- material candidates (built by usd/compose.py under Looks/SDG, rebound per frame by scripts/isaac/sdg.py) ---
LOOK_NAMES = {"floor": "Floor", "walls": "Wall", "ceiling": "Ceiling"}
SDG_LOOKS = "/World/Room/Looks/SDG"


def _label(s: dict) -> str:
    return s.get("mdl_name") or (s["name"] if s["name"] != "custom" else None) or "custom"


def _key(s: dict) -> tuple:
    return ("mdl", s["mdl"], s.get("mdl_name")) if s.get("mdl") else (s["name"], tuple(s["color"]), s.get("texture"))


def _current(room, target: str) -> dict | None:
    if target == "floor":
        return resolve_surface(room.floor, "grass" if room.outdoor else "wood_light")
    if target == "walls":
        return resolve_surface(room.walls, "paint_white")
    return ceiling_surface(room.ceiling, room.lighting, room.place)


def _searched(c: SdgMaterialChoice, target: str, seed: int) -> list[dict]:
    from agentic_sim_world_gen import materials

    recs = materials.records()
    if c.category:
        cats = {x.lower() for x in ([c.category] if isinstance(c.category, str) else c.category)}
        recs = tuple(r for r in recs if r["category"].lower() in cats or (r.get("sub") or "").lower() in cats)
    if c.tags:
        recs = tuple(r for r in recs if materials.has_words(r, c.tags))
    if c.query:
        found = materials.search(c.query, c.alt_queries, target, limit=max(c.count * 3, 12), recs=recs)
        strong = [r for r in found if not r["match"]["weak"]]
        return (strong or found)[: c.count]
    if not c.category:  # tags only: keep to categories usual for the target
        recs = tuple(r for r in recs if materials.used_for(r, target))
    pool = sorted(recs, key=lambda r: r["id"])
    return random.Random(f"{seed}:{target}").sample(pool, min(c.count, len(pool)))


def material_candidates(room, sdg: SdgSpec | None) -> tuple[dict[str, list[dict]], list[str]]:
    """{target: [candidate]} for the room's floor / walls / ceiling, and warnings. A candidate is
    {name, path (material prim; None = the surface the room already has), source, surface (resolved, for
    compose), [mdl, id, license]}. Deterministic for a room and sdg (searched sets are sampled from the seed)."""
    from agentic_sim_world_gen import materials

    out: dict[str, list[dict]] = {}
    warnings: list[str] = []
    if sdg is None:
        return out, warnings
    for target, c in sdg.randomize.materials.items():
        if room.outdoor and target != "floor":
            warnings.append(f"outdoor site: no {target} to randomize")
            continue
        cur = _current(room, target)
        if cur is None:
            warnings.append(f"no {target} in the room: its materials are not randomized")
            continue
        cands: list[dict] = []
        if c.keep_current:
            cands.append({"name": _label(cur), "path": None, "source": "current", "surface": cur})
        picked: list[dict] = []
        for v in c.surfaces:
            if isinstance(v, str) and v.startswith("mdl:"):
                r = materials.by_id(v[4:])
                if r is None:
                    warnings.append(f"{target}: unknown MDL material id '{v[4:]}'")
                    continue
                picked.append({"source": "mdl", "record": r})
            else:
                try:
                    sv = resolve_surface(v, "concrete")
                except ValueError as e:
                    warnings.append(f"{target}: {e}")
                    continue
                picked.append({"source": "preset" if isinstance(v, str) else "surface", "surface": sv})
        if c.searched:
            found = _searched(c, target, sdg.seed)
            if not found:
                warnings.append(f"{target}: no bundled MDL material matches {c.model_dump(exclude_defaults=True)}")
            picked += [{"source": "mdl", "record": r} for r in found]
        seen = {_key(cur)} if c.keep_current else set()
        for p in picked:
            if r := p.pop("record", None):
                p["surface"] = resolve_surface(materials.surface(r), "concrete")
                p.update(id=r["id"], license=r.get("license"))
            sv = p["surface"]
            if _key(sv) in seen:
                continue
            seen.add(_key(sv))
            if sv.get("mdl"):
                p["mdl"] = sv["mdl"]
            cands.append({"name": _label(sv), **p})
        for i, cand in enumerate(x for x in cands if x["source"] != "current"):
            cand["path"] = f"{SDG_LOOKS}/{LOOK_NAMES[target]}_{i:02d}"
        if len(cands) < 2:
            warnings.append(f"{target}: fewer than 2 material candidates, nothing to randomize")
            continue
        out[target] = cands
    return out, warnings


def _r(rng: random.Random, span: tuple[float, float] | None) -> float | None:
    return None if span is None else rng.uniform(min(span), max(span))


def plan(scene: ResolvedScene, sdg: SdgSpec | None = None) -> dict:
    """Frames for scripts/isaac/sdg.py. Deterministic for a given scene and sdg.seed."""
    sdg = sdg or scene.spec.sdg or SdgSpec()
    rng = random.Random(sdg.seed)
    bx = boxes(scene, sdg.classes, sdg.others)
    free = _Free(scene, sdg, bx)
    cam = sdg.camera
    warnings: list[str] = []
    ids = {b.parent for b in bx}
    if unknown := [t for t in cam.targets if t not in ids]:
        warnings.append(f"unknown camera targets ignored: {unknown}")
    if unknown := [t for t in sdg.classes if t not in ids]:
        warnings.append(f"classes for unknown objects ignored: {unknown}")
    warnings += _scope_warnings(bx, sdg, scene.spec.purpose.targets if scene.spec.purpose else [])
    # aim at what is labelled unless the targets were named (background objects are not what the data is for)
    targets = [b for b in bx if b.parent in cam.targets] if cam.targets else [b for b in bx if b.cls] or bx
    if free.zone is not None:
        targets = [b for b in targets if point_in_polygon(b.center[:2], free.zone)] or targets
    room_c = free.geom.center(cam.zone)
    fallback = Box("room", "room", "room", "", _square(room_c, 0.01), (0.9, 1.1))
    if not targets:
        warnings.append("no objects: cameras aim at the room center")
        targets = [fallback]

    mats, mat_warnings = material_candidates(scene.spec.room, sdg)
    warnings += mat_warnings
    mrng = random.Random(f"{sdg.seed}:materials")  # own stream: camera poses stay the same with or without it
    bags: dict[str, list[int]] = {t: [] for t in mats}

    frames, blind, failed = [], 0, 0
    for _ in range(sdg.frames):
        pose = None
        for attempt in range(TRIES):
            t = rng.choice(targets)
            cx, cy, cz = t.center
            jit = cam.aim_jitter
            at = (cx + rng.uniform(-jit, jit), cy + rng.uniform(-jit, jit),
                  min(max(cz + rng.uniform(-jit, jit), t.z[0]), t.z[1]))
            dist, ang = _r(rng, cam.distance), rng.uniform(0, 2 * math.pi)
            p = (cx + dist * math.cos(ang), cy + dist * math.sin(ang))
            z = _r(rng, cam.height)
            if not free.stand_ok(p, z):
                continue
            check_los = attempt < TRIES * 3 // 4
            if check_los and not free.sees((*p, z), at, t.parent):
                continue
            blind += not check_los
            pose = {"position": [round(p[0], 4), round(p[1], 4), round(z, 4)],
                    "look_at": [round(v, 4) for v in at], "target": t.id}
            break
        if pose is None:  # nowhere to stand around the targets: room center, looking at a target
            failed += 1
            t = rng.choice(targets)
            pose = {"position": [round(room_c[0], 4), round(room_c[1], 4), round(_r(rng, cam.height), 4)],
                    "look_at": [round(v, 4) for v in t.center], "target": t.id}
        pose["hfov_deg"] = round(_r(rng, cam.hfov_deg), 3)
        f = {"camera": pose}
        rz = sdg.randomize
        if rz.light_scale is not None:
            f["light_scale"] = round(_r(rng, rz.light_scale), 4)
        if rz.color_temp_k is not None:
            f["color_temp_k"] = round(_r(rng, rz.color_temp_k), 1)
        if rz.room_colors:  # a surface whose material is swapped keeps the candidate's own color
            outdoor = scene.spec.room.outdoor
            if "floor" not in mats:
                f["floor_color"] = _jitter(rng, SURFACES[rng.choice(GROUND_PRESETS if outdoor else FLOOR_PRESETS)]["color"])
            if not outdoor and "walls" not in mats:  # areas and buildings keep their colors
                f["wall_color"] = _jitter(rng, SURFACES[rng.choice(WALL_PRESETS)]["color"])
        if mats:
            f["materials"] = {}
            for t, cands in mats.items():
                if not bags[t]:  # shuffle bag: every candidate shows up evenly
                    bags[t] = list(range(len(cands)))
                    mrng.shuffle(bags[t])
                i = bags[t].pop()
                f["materials"][t] = {"index": i, "name": cands[i]["name"]}
        frames.append(f)
    if blind:
        warnings.append(f"{blind} frame(s) without a clear line of sight (crowded room?)")
    if failed:
        warnings.append(f"{failed} frame(s) found no free spot near a target; camera put at the room center")

    return {
        "version": 1,
        "scene": scene.spec.name,
        "seed": sdg.seed,
        "resolution": list(sdg.resolution),
        "rt_subframes": sdg.rt_subframes,
        "annotators": list(dict.fromkeys(["rgb", *sdg.annotators])),
        "writers": list(dict.fromkeys(["basic", *sdg.writers])),
        "classes": {b.path: b.cls for b in bx},
        "stuff": stuff_classes(scene.spec.room) if sdg.structure else [],
        "blank_structure": not sdg.structure,
        "materials": {t: [{k: v for k, v in c.items() if k != "surface"} for c in cands] for t, cands in mats.items()},
        "frames": frames,
        "warnings": warnings,
    }


def _scope_warnings(bx: list[Box], sdg: SdgSpec, targets: list[str]) -> list[str]:
    """Boxes outside the classes the data is for outnumber every class that is: an "other" class the agent
    made up, or, when the purpose names its targets, an object left out of `classes` whose own class is none of
    them (`classes` alone may only rename a few objects)."""
    wanted = {c for c in sdg.classes.values() if c and c not in OTHER_NAMES} | {t.lower() for t in targets}
    if not sdg.classes and not targets:
        return []
    count: dict[str, int] = {}
    outside: dict[str, int] = {}
    for b in bx:
        if not b.cls:
            continue
        if b.cls in OTHER_NAMES or (targets and b.parent not in sdg.classes and b.cls not in wanted):
            outside[b.cls] = outside.get(b.cls, 0) + 1
        else:
            count[b.cls] = count.get(b.cls, 0) + 1
    n = sum(outside.values())
    if not n or n <= max(count.values(), default=0):
        return []
    top = ", ".join(f"{c} {k}" for c, k in sorted(outside.items(), key=lambda kv: -kv[1])[:4])
    return [f"{n} box(es) are outside the dataset's classes ({top}), more than any class in it: give them "
            f'class "" in sdg.classes or set sdg.others="background" so they stay unlabelled (no box, not in COCO)']


def _jitter(rng: random.Random, c, k: float = 0.15) -> list[float]:
    s = rng.uniform(1 - k, 1 + k)
    return [round(min(max(v * s * rng.uniform(0.95, 1.05), 0.0), 1.0), 4) for v in c]
