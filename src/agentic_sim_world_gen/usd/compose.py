"""Build a USD stage from a ResolvedScene.

Works with either `usd-core` (plain venv) or the pxr bundled with Isaac Sim.
Assets are referenced by URL (pinned HF commit). Pass `uri_map` to swap URLs
for local copies (see agentic_sim_world_gen.assets.localize).
Room surfaces and lights: agentic_sim_world_gen.usd.look (relative texture paths resolve against the scene directory).
"""

from __future__ import annotations

import contextlib
import math
import os
import re
import sys
import tempfile
from pathlib import Path

from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics, UsdSemantics, Vt

from agentic_sim_world_gen import atomic
from agentic_sim_world_gen.assets.record import wants_box_collider
from agentic_sim_world_gen.layout.array import instances, unit_size
from agentic_sim_world_gen.layout.frame import front_local_deg, norm_deg
from agentic_sim_world_gen.layout.room import RoomGeometry
from agentic_sim_world_gen.origin import origin
from agentic_sim_world_gen.scene import levels as levels_mod
from agentic_sim_world_gen.scene.fixtures import beams, ceiling_glow, ceiling_tint, feature_surface, linear_rgb
from agentic_sim_world_gen.scene.looks import ceiling_surface, resolve_surface
from agentic_sim_world_gen.scene.walls import applies, default_value, overrides, wall_surface
from agentic_sim_world_gen.sdg import semantic_class
from agentic_sim_world_gen.scene.spec import ResolvedScene
from agentic_sim_world_gen.scene.zone_floors import zone_floors
from agentic_sim_world_gen.usd.door import build_doors
from agentic_sim_world_gen.usd.look import bind, box_mesh, build_lights, make_material, prism_mesh


# usd-core has no http(s) resolver, so every remote reference warns "Could not open asset @https:...".
# Isaac Sim resolves them; the warnings only confuse users, so compose() drops those lines.
_REMOTE_REF_WARNING = re.compile(r"Could not open asset @(https?|omniverse|s3):")


@contextlib.contextmanager
def _quiet_remote_refs():
    try:
        fd = sys.stderr.fileno()
    except (AttributeError, OSError, ValueError):  # no real stderr (e.g. captured): nothing to filter
        yield
        return
    sys.stderr.flush()
    saved = os.dup(fd)
    with tempfile.TemporaryFile(mode="w+b") as tmp:
        os.dup2(tmp.fileno(), fd)
        try:
            yield
        finally:
            sys.stderr.flush()
            os.dup2(saved, fd)
            os.close(saved)
            tmp.seek(0)
            text = tmp.read().decode(errors="replace")
            kept = [ln for ln in text.splitlines(keepends=True) if not _REMOTE_REF_WARNING.search(ln)]
            if kept:
                sys.stderr.write("".join(kept))
                sys.stderr.flush()


def label(prim: Usd.Prim, cls: str, taxonomy: str = "class") -> None:
    """Semantic label for Replicator annotators (UsdSemantics.LabelsAPI, read as semanticTypes ["class"]).
    SimReady assets carry their own "wikidata_class" labels; ours is the same for every source."""
    UsdSemantics.LabelsAPI.Apply(prim, taxonomy).CreateLabelsAttr().Set(Vt.TokenArray([cls]))


RECT_NAMES = {"south": "Wall_S", "north": "Wall_N", "west": "Wall_W", "east": "Wall_E"}


def build_room(stage: Usd.Stage, scene: ResolvedScene, root: str = "/World/Room", base_dir: Path | None = None) -> None:
    """Floor slab, walls (cut around doors/windows) and an optional ceiling, as meshes with the room's
    surface materials. A wall with openings becomes an Xform holding its solid parts (Part_00, ...).
    Openings are empty, except auto_sliding doors (agentic_sim_world_gen.usd.door)."""
    r = scene.spec.room
    geom = RoomGeometry(r)
    t = r.wall_thickness
    UsdGeom.Xform.Define(stage, root)
    floor_s = resolve_surface(r.floor, "wood_light")
    wall_s = resolve_surface(default_value(r.walls), "paint_white")
    floor_m = make_material(stage, f"{root}/Looks/Floor", floor_s, base_dir)
    wall_m = make_material(stage, f"{root}/Looks/Wall", wall_s, base_dir)
    x0, y0, x1, y1 = geom.slab
    slab_c, slab_s = ((x0 + x1) / 2, (y0 + y1) / 2), (x1 - x0, y1 - y0, t)
    zones = zone_floors(r)
    # zone floors: their own slabs with the top at z=0; the room's slab sinks 1 mm under them (no z-fighting).
    # Floor areas lie over both, 1 mm apart, the last one at z=0.
    n_areas = len(r.floor_areas) if not r.outdoor else 0
    zone_top = -FLAT_STEP * n_areas
    sink = FLAT_STEP * (n_areas + (1 if zones else 0))
    floor = box_mesh(stage, f"{root}/Floor", (*slab_c, -t / 2 - sink), slab_s, floor_s["tile_m"]).GetPrim()
    bind(floor, floor_m)
    label(floor, "floor")
    if zones:
        UsdGeom.Xform.Define(stage, f"{root}/ZoneFloors")
    for z in zones:
        m = make_material(stage, f"{root}/Looks/Floor_{z['id']}", z["surface"], base_dir)
        piece = prism_mesh(stage, f"{root}/ZoneFloors/{z['id']}", z["polygon"], -t, zone_top, z["surface"]["tile_m"]).GetPrim()
        bind(piece, m)
        label(piece, "floor")
        piece.SetCustomDataByKey("agentic_sim_world_gen:zone", z["id"])
    if n_areas:
        UsdGeom.Xform.Define(stage, f"{root}/FloorAreas")
    for i, a in enumerate(r.floor_areas if n_areas else []):
        s = a.surface()
        m = make_material(stage, f"{root}/Looks/Floor_{a.id}", s, base_dir)
        piece = prism_mesh(stage, f"{root}/FloorAreas/{a.id}", a.poly, -t, -FLAT_STEP * (n_areas - 1 - i), s["tile_m"]).GetPrim()
        bind(piece, m)
        label(piece, "floor")
        piece.SetCustomDataByKey("agentic_sim_world_gen:floor_area", a.id)
    glow = dict(glow=ceiling_glow(r), tint=ceiling_tint(r))  # the ceiling's glow under downward fixtures
    if ceil_s := ceiling_surface(r.ceiling, r.lighting, r.place):
        top = max(w.height for w in geom.walls)
        ceil_m = make_material(stage, f"{root}/Looks/Ceiling", ceil_s, base_dir, **glow)
        ceil = box_mesh(stage, f"{root}/Ceiling", (*slab_c, top + t / 2), slab_s, ceil_s["tile_m"]).GetPrim()
        bind(ceil, ceil_m)
        label(ceil, "ceiling")
    if boxes := beams(r):  # beams / soffits under the ceiling: static colliders, labeled ceiling
        UsdGeom.Xform.Define(stage, f"{root}/CeilingFeatures")
        mats: dict = {}
        for b in boxes:
            s = resolve_surface(feature_surface(b["feature"], ceil_s), "paint_white")
            key = repr(sorted((k, str(v)) for k, v in s.items()))
            if key not in mats:
                mats[key] = make_material(stage, f"{root}/Looks/Ceiling_{b['id']}", s, base_dir, **glow)
            x0, y0, x1, y1 = b["rect"]
            z0, z1 = b["z"]
            m = box_mesh(stage, f"{root}/CeilingFeatures/{b['id']}", ((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2),
                         (x1 - x0, y1 - y0, z1 - z0), s["tile_m"])
            bind(m.GetPrim(), mats[key])
            label(m.GetPrim(), "ceiling")
            m.GetPrim().SetCustomDataByKey("agentic_sim_world_gen:ceiling_feature", b["feature"].kind)
    if r.levels or r.stairs:
        _build_levels(stage, r, root, floor_s, base_dir)
    if r.columns and not r.outdoor:
        _build_columns(stage, r, root, wall_s, base_dir)
    rect = r.plan is None
    own = overrides(r.walls, rect)
    trims = _trim_looks(stage, r, root, base_dir)
    for w in geom.walls:
        name = RECT_NAMES.get(w.id, f"Wall_{w.id}") if rect else f"Wall_{w.id}"
        if w.id in own:  # an accent wall: its own material
            ws = wall_surface(r.walls, w.id, rect)
            wm = make_material(stage, f"{root}/Looks/{name}", ws, base_dir)
        else:
            ws, wm = wall_s, wall_m
        parts = w.pieces()
        faces = [f for f in geom.faces if f.wall == w.id]
        trimmed = bool(faces) and any(applies(t, w.id, rect) for t, _, _ in trims.values())
        if not w.openings and len(parts) == 1 and not trimmed:
            paths = [f"{root}/{name}"]
        else:
            UsdGeom.Xform.Define(stage, f"{root}/{name}")
            paths = [f"{root}/{name}/Part_{i:02d}" for i in range(len(parts))]
        for path, (u0, u1, z0, z1, _) in zip(paths, parts):
            cx, cy = w.at((u0 + u1) / 2)
            mesh = box_mesh(stage, path, (cx, cy, (z0 + z1) / 2), (u1 - u0, w.thickness, z1 - z0), ws["tile_m"], w.yaw_deg)
            bind(mesh.GetPrim(), wm)
        if trimmed:
            _build_trim(stage, geom, r, f"{root}/{name}", w.id, faces, trims)
        label(stage.GetPrimAtPath(f"{root}/{name}"), "wall")
        if w.openings:
            prim = stage.GetPrimAtPath(f"{root}/{name}")
            prim.SetCustomDataByKey("agentic_sim_world_gen:openings", Vt.StringArray([o.id for o in w.openings]))
    build_doors(stage, geom, root, label)


def _own_surface(surface, color, default):
    if surface is not None:
        return resolve_surface(surface, "paint_white")
    if color is not None:
        return resolve_surface({"preset": "paint_white", "color": linear_rgb(color)}, "paint_white")
    return default


def _build_columns(stage: Usd.Stage, r, root: str, wall_s: dict, base_dir: Path | None) -> None:
    """Columns: static colliders from the floor up (box: convex hull; round: a 24-sided prism), labeled
    column, customData agentic_sim_world_gen:column = [width, depth, height]."""
    UsdGeom.Xform.Define(stage, f"{root}/Columns")
    mats: dict = {}
    for c in r.columns:
        s = _own_surface(c.surface, c.color, wall_s)
        key = repr(sorted((k, str(v)) for k, v in s.items()))
        if key not in mats:
            mats[key] = make_material(stage, f"{root}/Looks/Column_{c.id}", s, base_dir)
        top = c.top(r.height)
        m = prism_mesh(stage, f"{root}/Columns/{c.id}", c.poly, 0.0, top, s["tile_m"]).GetPrim()
        bind(m, mats[key])
        label(m, "column")
        m.SetCustomDataByKey("agentic_sim_world_gen:column", Vt.DoubleArray([*c.dims, top]))


def _build_levels(stage: Usd.Stage, r, root: str, floor_s: dict, base_dir: Path | None) -> None:
    """Raised floors, stairs and railings: static colliders (triangle meshes for concave levels), labeled
    floor / stairs / railing, customData agentic_sim_world_gen:level / agentic_sim_world_gen:stairs."""
    mats: dict = {}

    def mat(s: dict, name: str):
        key = repr(sorted((k, str(v)) for k, v in s.items()))
        if key not in mats:
            mats[key] = make_material(stage, f"{root}/Looks/{name}", s, base_dir)
        return mats[key]

    levels = {}
    if r.levels:
        UsdGeom.Xform.Define(stage, f"{root}/Levels")
    for lv in r.levels:
        s = _own_surface(lv.floor, lv.color, floor_s)
        levels[lv.id] = s
        m = prism_mesh(stage, f"{root}/Levels/{lv.id}", lv.poly, 0.0, lv.height, s["tile_m"]).GetPrim()
        bind(m, mat(s, f"Level_{lv.id}"))
        label(m, "floor")
        m.SetCustomDataByKey("agentic_sim_world_gen:level", lv.height)
    if r.stairs:
        UsdGeom.Xform.Define(stage, f"{root}/Stairs")
    for st in r.stairs:
        s = _own_surface(st.surface, st.color, levels.get(st.level or "", floor_s))
        m = mat(s, f"Stairs_{st.id}")
        f = levels_mod.flight(r, st)
        xf = UsdGeom.Xform.Define(stage, f"{root}/Stairs/{st.id}").GetPrim()
        xf.SetCustomDataByKey("agentic_sim_world_gen:stairs", Vt.DoubleArray([f["rise"], float(f["count"]), st.going]))
        label(xf, "stairs")
        for b in f["steps"]:
            mesh = box_mesh(stage, f"{root}/Stairs/{st.id}/{b['id']}", b["center"], b["size"], s["tile_m"], b["yaw"])
            bind(mesh.GetPrim(), m)
    rails = levels_mod.rails(r)
    if rails:
        UsdGeom.Xform.Define(stage, f"{root}/Railings")
        rm = mat(resolve_surface({"preset": "paint_white", "color": linear_rgb("#3a3a3a"), "metallic": 0.6,
                                  "roughness": 0.4}, "paint_white"), "Railing")
    for b in rails:
        (ax, ay, az), (bx, by, bz) = b["a"], b["b"]
        L = math.dist((ax, ay, az), (bx, by, bz))
        xf = UsdGeom.Xform.Define(stage, f"{root}/Railings/{b['id']}")
        xf.AddTranslateOp().Set(Gf.Vec3d((ax + bx) / 2, (ay + by) / 2, (az + bz) / 2))
        xf.AddRotateZOp().Set(math.degrees(math.atan2(by - ay, bx - ax)) if math.hypot(bx - ax, by - ay) > 1e-9 else 0.0)
        xf.AddRotateYOp().Set(-math.degrees(math.atan2(bz - az, math.hypot(bx - ax, by - ay))))
        mesh = box_mesh(stage, f"{root}/Railings/{b['id']}/Bar", (0.0, 0.0, 0.0),
                        (L, levels_mod.BAR, levels_mod.BAR), 1.0)
        bind(mesh.GetPrim(), rm)
        label(xf.GetPrim(), "railing")


def _trim_looks(stage: Usd.Stage, r, root: str, base_dir: Path | None) -> dict:
    """kind -> (Wainscot / Baseboard spec, its material) for the trim the room has."""
    out = {}
    for kind, t in (("Wainscot", r.wainscot), ("Baseboard", r.baseboard)):
        if t is not None:
            s = t.resolved()
            out[kind] = (t, make_material(stage, f"{root}/Looks/{kind}", s, base_dir), s)
    return out


def _build_trim(stage: Usd.Stage, geom: RoomGeometry, r, wall_path: str, wall_id: str, faces, trims) -> None:
    """Wainscot / baseboard boxes on the wall's inner faces (Wainscot_NN, Baseboard_NN under the wall).
    Static colliders like the wall (no rigid body); the baseboard stands on the wainscot's face."""
    rect = r.plan is None
    i = {"Wainscot": 0, "Baseboard": 0}
    for kind, (t, m, s) in trims.items():
        if not applies(t, wall_id, rect):
            continue
        back = r.wainscot.thickness if kind == "Baseboard" and applies(r.wainscot, wall_id, rect) else 0.0
        for f in faces:
            nx, ny = math.cos(math.radians(f.facing)), math.sin(math.radians(f.facing))
            yaw = math.degrees(math.atan2(f.p1[1] - f.p0[1], f.p1[0] - f.p0[0]))
            n = back + t.thickness / 2
            for p0, p1, top in geom.face_runs(f, t.height):
                c = ((p0[0] + p1[0]) / 2 + nx * n, (p0[1] + p1[1]) / 2 + ny * n, top / 2)
                path = f"{wall_path}/{kind}_{i[kind]:02d}"
                i[kind] += 1
                mesh = box_mesh(stage, path, c, (math.dist(p0, p1), t.thickness, top), s["tile_m"], yaw)
                bind(mesh.GetPrim(), m)
                mesh.GetPrim().SetCustomDataByKey("agentic_sim_world_gen:trim", kind.lower())


FLAT_STEP = 0.001  # flat areas stack by 1 mm (later areas on top), the last one at z=0, the ground below them
GROUND_T = 0.2


def build_site(stage: Usd.Stage, scene: ResolvedScene, root: str = "/World/Room", base_dir: Path | None = None) -> None:
    """Outdoor: ground slab (site + margin), areas as thin (or curb-high) slabs on it, buildings as
    closed blocks. Static colliders with the surfaces' friction; semantic labels ground / <area kind> / building."""
    r = scene.spec.room
    geom = RoomGeometry(r)
    rp = UsdGeom.Xform.Define(stage, root).GetPrim()
    rp.SetCustomDataByKey("agentic_sim_world_gen:outdoor", True)
    rp.SetCustomDataByKey("agentic_sim_world_gen:site", Vt.DoubleArray([float(v) for v in geom.bounds]))  # x0, y0, x1, y1
    _site_parts(stage, root, r.areas, r.buildings, resolve_surface(r.floor, "grass"), geom.slab, base_dir)


def build_surroundings(stage: Usd.Stage, scene: ResolvedScene, root: str = "/World/Room",
                       base_dir: Path | None = None) -> None:
    """Room.site: the outside around the indoor room, built like an outdoor site under the room's prim.
    Areas stay out of the room; the ground runs on under the room's floor slab (top z = 0), below it."""
    r = scene.spec.room
    geom = RoomGeometry(r)
    site = r.site
    rp = stage.GetPrimAtPath(root)
    x0, y0, x1, y1 = geom.site_bounds
    rp.SetCustomDataByKey("agentic_sim_world_gen:site", Vt.DoubleArray([float(v) for v in (x0, y0, x1, y1)]))
    m = site.ground_margin
    _site_parts(stage, root, site.areas, site.buildings, resolve_surface(site.ground, "asphalt"),
                (x0 - m, y0 - m, x1 + m, y1 + m), base_dir, ground_below=2)


def _site_parts(stage: Usd.Stage, root: str, areas, buildings, ground_s, slab, base_dir: Path | None,
                ground_below: int = 0) -> None:
    """Ground, areas and buildings. ground_below: steps more the ground sinks (under a room's floor slab)."""
    # Objects stand at z=0 (or a curb top): nothing may stick above that, or PhysX pushes them out
    # of the overlap on the first step and they slide. Colliders are triangle meshes: PhysX convex
    # hulls of a 100 m ground slab or a long building made crates next to them slide.
    tri = UsdPhysics.Tokens.none
    ground_m = make_material(stage, f"{root}/Looks/{'Ground' if ground_below else 'Floor'}", ground_s, base_dir)
    n = len(areas)
    x0, y0, x1, y1 = slab
    ground = box_mesh(stage, f"{root}/Ground", ((x0 + x1) / 2, (y0 + y1) / 2, -FLAT_STEP * (n + ground_below) - GROUND_T / 2),
                      (x1 - x0, y1 - y0, GROUND_T), ground_s["tile_m"], approx=tri).GetPrim()
    bind(ground, ground_m)
    label(ground, "ground")
    if areas:
        UsdGeom.Xform.Define(stage, f"{root}/Areas")
    for i, a in enumerate(areas):
        s = resolve_surface(a.surface_or_default, "concrete")
        m = make_material(stage, f"{root}/Looks/Area_{a.id}", s, base_dir)
        top = a.top if a.top > 1e-6 else -FLAT_STEP * (n - 1 - i)
        mesh = prism_mesh(stage, f"{root}/Areas/{a.id}", a.poly, -0.05, top, s["tile_m"], approx=tri).GetPrim()
        bind(mesh, m)
        label(mesh, a.kind)
        mesh.SetCustomDataByKey("agentic_sim_world_gen:area", a.kind)
    if buildings:
        UsdGeom.Xform.Define(stage, f"{root}/Buildings")
    for b in buildings:
        s = resolve_surface(b.surface, "concrete")
        m = make_material(stage, f"{root}/Looks/Building_{b.id}", s, base_dir)
        mesh = prism_mesh(stage, f"{root}/Buildings/{b.id}", b.poly, 0.0, b.height, s["tile_m"], approx=tri).GetPrim()
        bind(mesh, m)
        label(mesh, "building")


ASSET_CHILD = "Asset"


COLLIDER_CHILD = "Collider"

# Physics scenes some assets bring along (ForgeCAD's SimReady assets each define Physics/PhysicsScene, GPU
# dynamics). Every reference adds one more PhysX scene and about 25 of them stop Isaac Sim 6.1 in the first
# physics frame, so they are switched off: the stage's own /World/PhysicsScene is the one scene.
# An over on a path the asset does not have stays undefined and does nothing.
ASSET_SCENES = ("Physics/PhysicsScene",)


# GPU PhysX buffers of /World/PhysicsScene (PhysxSceneAPI, written by name: usd-core has no PhysxSchema). With
# Kit's defaults the restaurant (~600 dishes and food, many convex hulls each) overflowed the collision stack
# (needed ~90 MB of 64) and the aggregate pairs (1054 of 1024): PhysX dropped the contacts and the fridge fell
# through the floor. These cost a few hundred MB of GPU memory in every scene.
GPU_BUFFERS = {
    "gpuCollisionStackSize": 512 * 2**20,
    "gpuTotalAggregatePairsCapacity": 2**14,
    "gpuFoundLostAggregatePairsCapacity": 2**14,
    "gpuFoundLostPairsCapacity": 2**20,
    "gpuMaxRigidContactCount": 2**21,
    "gpuMaxRigidPatchCount": 2**19,
}


def _gpu_buffers(prim: Usd.Prim) -> None:
    prim.AddAppliedSchema("PhysxSceneAPI")
    for name, v in GPU_BUFFERS.items():
        prim.CreateAttribute(f"physxScene:{name}", Sdf.ValueTypeNames.UInt).Set(v)


def _mute_asset_scenes(stage: Usd.Stage, ref_path: str) -> None:
    for rel in ASSET_SCENES:
        stage.OverridePrim(f"{ref_path}/{rel}").SetActive(False)


def add_box_collider(stage: Usd.Stage, path: str, a) -> None:
    """<path>/Collider: a cube the size of the asset's bbox with PhysicsCollisionAPI and no rigid body, so the
    object stays put and things rest on it / bump into it. It sits under the placement ops (in asset units,
    like the reference), purpose=guide keeps it out of renders and bounding boxes."""
    cube = UsdGeom.Cube.Define(stage, f"{path}/{COLLIDER_CHILD}")
    cube.CreateSizeAttr(2.0)
    cube.CreatePurposeAttr(UsdGeom.Tokens.guide)
    mn, mx = a.bbox_min, a.bbox_max
    xf = UsdGeom.Xformable(cube)
    xf.AddTranslateOp().Set(Gf.Vec3d(*((lo + hi) / 2 for lo, hi in zip(mn, mx))))
    xf.AddScaleOp().Set(Gf.Vec3f(*(max((hi - lo) / 2, 1e-4) for lo, hi in zip(mn, mx))))
    UsdPhysics.CollisionAPI.Apply(cube.GetPrim())
    cube.GetPrim().SetCustomDataByKey("agentic_sim_world_gen:collider", "box")


def _lay_flat_ops(xf: UsdGeom.Xformable, a, o) -> None:
    """Tip the product onto its back (fill.lay_flat): its front F turns up, its top toward -F, and the
    result sits on z = 0 centered on the slot. In the pivot frame a point (f along F, lateral, z) goes to
    (h/2 - z along F, lateral, f + dF/2 up)."""
    f = front_local_deg(a, o)
    dF, h = unit_size(o, a)[1], a.size_m[2]
    fr = math.radians(f)
    xf.AddTranslateOp(opSuffix="layFlat").Set(Gf.Vec3d(h / 2 * math.cos(fr), h / 2 * math.sin(fr), dF / 2))
    xf.AddRotateZOp(opSuffix="layFlatFront").Set(float(f))
    xf.AddRotateYOp(opSuffix="layFlat").Set(-90.0)
    xf.AddRotateZOp(opSuffix="layFlatBack").Set(float(-f))


def _add_asset(stage: Usd.Stage, path: str, a, uri: str, o, inst) -> None:
    # <path> (Xform: our placement ops + provenance) / Asset (the reference + variant selections).
    # The reference sits on a child so the asset's own root xformOps (e.g. scale 0.01 on a cm
    # asset) survive: authoring xformOpOrder on the referencing prim would replace them.
    prim = UsdGeom.Xform.Define(stage, path).GetPrim()
    ref = stage.DefinePrim(f"{path}/{ASSET_CHILD}")  # typeless: the type comes from the asset
    ref.GetReferences().AddReference(uri)
    for vset, sel in {**a.variant_selections, **o.variants}.items():  # the object's own choice wins
        # authors the selection even if the variant set cannot be resolved locally
        ref.GetVariantSets().SetSelection(vset, sel)
    _mute_asset_scenes(stage, ref.GetPath().pathString)

    # p' = T * Rz(yaw) * [S(fit)] * S(mpu) * [Rx(90) if Y-up] * (p - pivot)
    xf = UsdGeom.Xformable(prim)
    xf.ClearXformOpOrder()
    xf.AddTranslateOp().Set(Gf.Vec3d(inst.position[0], inst.position[1], float(inst.z)))
    xf.AddRotateZOp().Set(float(inst.yaw_deg))
    if o.fit_scale is not None:  # a window stretched to its opening, a curtain to its window
        xf.AddScaleOp(opSuffix="fit").Set(Gf.Vec3f(*o.fit_scale))
    if o.fill is not None and o.fill.lay_flat:
        _lay_flat_ops(xf, a, o)
    s = float(a.meters_per_unit)
    xf.AddScaleOp().Set(Gf.Vec3f(s, s, s))
    if a.up_axis.upper() == "Y":
        xf.AddRotateXOp(opSuffix="upAxis").Set(90.0)
    px, py, pz = a.pivot
    xf.AddTranslateOp(opSuffix="pivot").Set(Gf.Vec3d(-px, -py, -pz))

    if wants_box_collider(o.collider, a):
        add_box_collider(stage, path, a)
    _place_reset_parts(stage, path, a, uri)

    # provenance for later tools and license reports
    prim.SetCustomDataByKey("agentic_sim_world_gen:source", a.source)
    prim.SetCustomDataByKey("agentic_sim_world_gen:uri", a.uri)
    org = origin(a.uri, a.source, a.id)
    prim.SetCustomDataByKey("agentic_sim_world_gen:origin", {k: str(v) for k, v in org.items()
                                                if v is not None and k in ("from", "url", "version", "asset_version",
                                                                           "path", "sha256", "dirty")})
    if a.license:
        prim.SetCustomDataByKey("agentic_sim_world_gen:license", a.license)
    if a.author:
        prim.SetCustomDataByKey("agentic_sim_world_gen:author", a.author)
    if o.label:
        prim.SetCustomDataByKey("agentic_sim_world_gen:label", o.label)
    label(prim, semantic_class(o, a))
    prim.CreateAttribute("agentic_sim_world_gen:query", Sdf.ValueTypeNames.String).Set(o.query)
    if o.ifc_guid:
        prim.SetCustomDataByKey("agentic_sim_world_gen:ifc_guid", o.ifc_guid)
    if o.opening or o.covers:  # check hides it with its wall (a window floats when the wall is hidden)
        prim.SetCustomDataByKey("agentic_sim_world_gen:opening", o.opening or o.covers)
    # world direction the asset's (assumed) front points to; scripts/isaac/front_views.py renders from it
    prim.SetCustomDataByKey("agentic_sim_world_gen:facing_deg", round(norm_deg(inst.yaw_deg + front_local_deg(a, o)), 3))


def _place_reset_parts(stage: Usd.Stage, path: str, a, uri: str) -> None:
    """Parts with !resetXformStack! (ForgeCAD's knobs, doors, ATM keys) moved to where the asset is."""
    from agentic_sim_world_gen.usd import reset_parts

    if not reset_parts.wanted(a, uri):
        return
    n = reset_parts.place(stage, path, f"{path}/{ASSET_CHILD}", reset_parts.scan(uri, reset_parts.cache_dir()))
    if n:
        stage.GetPrimAtPath(path).SetCustomDataByKey("agentic_sim_world_gen:reset_parts", n)


def _add_tint(stage: Usd.Stage, paths: list[str], a, o) -> None:
    """ObjectSpec.tint: new colors for the asset's materials (agentic_sim_world_gen.usd.tint); the result as customData."""
    if not o.tint:
        return
    from agentic_sim_world_gen.usd.tint import apply_tint

    done = apply_tint(stage, paths, a, o)
    if done:
        stage.GetPrimAtPath(f"/World/Objects/{o.id}").SetCustomDataByKey(
            "agentic_sim_world_gen:tint", {d["color"]: {k: (", ".join(v) if isinstance(v, list) else v) for k, v in d.items()
                                           if v is not None and k != "color"} for d in done})


FLOOR_COVER = 0.02  # a rug / mat this thin lies flat on the floor: static and sunk into it
COVER_TOP = 0.001  # what stays above the floor


def _floor_cover(o, a, inst) -> bool:
    """A rug or mat on the floor. Things stand on it (validate counts 1 cm as no overlap), but a 1 cm rigid rug
    under a table's legs pushes the table up when physics starts and what is on it slides off."""
    return (o.fill is None and o.array is None and a.size_m[2] <= FLOOR_COVER and abs(float(inst.z)) < 1e-6
            and (o.layout is None or (o.layout.on is None and not o.layout.ceiling and o.layout.wall_height is None)))


def _hung(o) -> bool:
    """Hung from the ceiling or on a wall (layout.ceiling / wall_height): mounted, so nothing holds it up in physics."""
    return o.layout is not None and (o.layout.ceiling or o.layout.wall_height is not None)


def _make_static(stage: Usd.Stage, path: str, uri: str | None = None, key: str = "agentic_sim_world_gen:display") -> None:
    """Display stock (Fill.physics) or a hung object: the asset's rigid bodies are disabled, so its colliders are
    static. The root is often the body, but SimReady furniture has its bodies on parts (a TV's panel, a cabinet's
    doors under Geometry/), which usd-core cannot see through an http reference: those are read from the asset's
    layers (fetched and cached like the reset parts) and disabled with overs, opening instances that hold one."""
    from agentic_sim_world_gen.usd import reset_parts

    ref = stage.GetPrimAtPath(f"{path}/{ASSET_CHILD}")
    ref.CreateAttribute("physics:rigidBodyEnabled", Sdf.ValueTypeNames.Bool).Set(False)
    bodies = reset_parts.scan_bodies(uri, reset_parts.cache_dir()) if uri else []
    if bodies:
        opened = reset_parts.disable_bodies(stage, ref.GetPath().pathString, bodies)
        stage.GetPrimAtPath(path).SetCustomDataByKey("agentic_sim_world_gen:static_bodies", len(bodies))
        if opened:
            stage.GetPrimAtPath(path).SetCustomDataByKey("agentic_sim_world_gen:uninstanced", opened)
    for p in Usd.PrimRange(ref):
        if p != ref and p.HasAPI(UsdPhysics.RigidBodyAPI):
            UsdPhysics.RigidBodyAPI(p).CreateRigidBodyEnabledAttr().Set(False)
    stage.GetPrimAtPath(path).SetCustomDataByKey(key, True)


def _stock_sdf(uri: str | None) -> list[dict]:
    """SDF colliders of a stocked product (Fill), to be made bounding boxes. Products on a shelf hold nothing, so a
    box loses nothing, and SDF boxes slid 2-27 cm on a bakery table and 3 cm on a steel table in Isaac Sim 6.1
    where the same boxes with box colliders moved 2 mm. Convex hulls (64 vertices, or 255) also fixed
    the boxes but left rounded cupcakes rolling 3-9 cm off a tray. Also fewer SDF colliders for the GPU solver
."""
    if not uri or uri.startswith("polyhaven:"):
        return []
    from agentic_sim_world_gen.usd import reset_parts

    return reset_parts.scan_sdf(uri, reset_parts.cache_dir())


def _add_screen(stage: Usd.Stage, paths: list[str], a, o, scene_dir: Path) -> None:
    """ObjectSpec.screen: the image on the asset's screens (agentic_sim_world_gen.usd.screen); the result as customData."""
    if o.screen is None or not a.screens:
        return
    from agentic_sim_world_gen.usd.screen import apply_screen

    done = apply_screen(stage, paths, a, o, scene_dir)
    if not done:
        return
    from agentic_sim_world_gen.inputs import screen_source

    screens = {}
    for d in done:
        src = screen_source(o.screen, d["image"]) if d.get("image") else {}
        screens[d["material"]] = {k: str(v) for k, v in {**d, **src}.items() if k != "material" and v is not None}
    stage.GetPrimAtPath(f"/World/Objects/{o.id}").SetCustomDataByKey(
        "agentic_sim_world_gen:screen", {"fit": o.screen.fit, "screens": screens})


def build_inputs(stage: Usd.Stage, scene: ResolvedScene, root: str = "/World/Cameras") -> None:
    """Provenance of the input photos/drawings as customData on /World, and a camera per photo viewpoint
    (/World/Cameras/<id>, same field of view and aspect ratio; scripts/isaac/render_view.py renders it)."""
    from agentic_sim_world_gen.inputs import SENSOR_W_MM, focal_mm, provenance

    ins = scene.spec.inputs
    if not ins:
        return
    stage.GetPrimAtPath("/World").SetCustomDataByKey("agentic_sim_world_gen:inputs", {i.id: provenance(i) for i in ins})
    for i in ins:
        if i.camera is None:
            continue
        UsdGeom.Scope.Define(stage, root)
        cam = UsdGeom.Camera.Define(stage, f"{root}/{i.id}")
        eye, at = Gf.Vec3d(*i.camera.position), Gf.Vec3d(*i.camera.look_at)
        m = Gf.Matrix4d().SetLookAt(eye, at, Gf.Vec3d(0, 0, 1)).GetInverse()
        xf = UsdGeom.Xformable(cam)
        xf.ClearXformOpOrder()
        xf.AddTransformOp().Set(m)
        w, h = i.size_px or (4, 3)
        cam.CreateHorizontalApertureAttr(SENSOR_W_MM)
        cam.CreateVerticalApertureAttr(SENSOR_W_MM * h / w)
        cam.CreateFocalLengthAttr(focal_mm(i.camera))
        cam.CreateClippingRangeAttr(Gf.Vec2f(0.05, 1000.0))
        cam.GetPrim().SetCustomDataByKey("agentic_sim_world_gen:input", i.id)


def render_settings(scene: ResolvedScene) -> dict[str, int]:
    """Renderer settings for the root layer (Kit applies customLayerData.renderSettings when the stage opens).

    Real-Time 2.0 stops after 3 specular/transmission bounces by default, so liquid inside a bottle (bottle wall,
    then liquid surface) shows no color."""
    n = scene.spec.render.transmission_bounces
    return {"rtx:rtpt:maxSpecularAndTransmissionBounces": n} if n else {}


def build_sdg_looks(stage: Usd.Stage, scene: ResolvedScene, base_dir: Path | None = None) -> None:
    """Unbound materials for SDG material randomization (SdgSpec.randomize.materials) under
    /World/Room/Looks/SDG; scripts/isaac/sdg.py rebinds the floor / walls / ceiling to them per frame."""
    from agentic_sim_world_gen.sdg import SDG_LOOKS, material_candidates

    mats, _ = material_candidates(scene.spec.room, scene.spec.sdg)
    if not mats:
        return
    UsdGeom.Scope.Define(stage, SDG_LOOKS)
    for target, cands in mats.items():
        for c in cands:
            if c["path"]:
                glow = ceiling_glow(scene.spec.room) if target == "ceiling" else 0.0
                m = make_material(stage, c["path"], c["surface"], base_dir, glow=glow,
                                  tint=ceiling_tint(scene.spec.room))
                m.GetPrim().SetCustomDataByKey("agentic_sim_world_gen:sdg_target", target)
                if c.get("license"):
                    m.GetPrim().SetCustomDataByKey("agentic_sim_world_gen:license", c["license"])


def compose(scene: ResolvedScene, out_path: str | Path, uri_map: dict[str, str] | None = None) -> Usd.Stage:
    """Write the scene's USD. If it fails on the way (an asset that cannot be fetched), the file is put back as it
    was: Usd.Stage.CreateNew has already replaced it with an empty stage, which the next finish would take for
    the scene's USD."""
    out_path = Path(out_path)
    before = out_path.read_bytes() if out_path.is_file() else None
    try:
        with _quiet_remote_refs():
            return _compose(scene, out_path, uri_map)
    except BaseException:
        if before is None:
            out_path.unlink(missing_ok=True)
        else:
            atomic.write(out_path, before)
        raise


def _compose(scene: ResolvedScene, out_path: Path, uri_map: dict[str, str] | None) -> Usd.Stage:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    stage = Usd.Stage.CreateNew(str(out_path))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    world = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(world.GetPrim())
    if settings := render_settings(scene):
        layer = stage.GetRootLayer()
        layer.customLayerData = {**layer.customLayerData, "renderSettings": settings}

    phys = UsdPhysics.Scene.Define(stage, "/World/PhysicsScene")
    phys.CreateGravityDirectionAttr(Gf.Vec3f(0, 0, -1))
    phys.CreateGravityMagnitudeAttr(9.81)
    _gpu_buffers(phys.GetPrim())

    build_lights(stage, scene.spec.room, base_dir=out_path.parent)
    (build_site if scene.spec.room.outdoor else build_room)(stage, scene, base_dir=out_path.parent)
    if scene.spec.room.site is not None:
        build_surroundings(stage, scene, base_dir=out_path.parent)
    build_sdg_looks(stage, scene, base_dir=out_path.parent)
    build_inputs(stage, scene)
    UsdGeom.Xform.Define(stage, "/World/Objects")

    for p in scene.placements:
        o = scene.obj(p.object_id)
        a = p.asset
        if a is None or o.position is None:
            continue
        uri = (uri_map or {}).get(a.uri, a.uri)
        if uri.startswith("polyhaven:"):  # fetched on first use, copied into the scene
            from agentic_sim_world_gen import polyhaven_models

            uri = polyhaven_models.scene_ref(uri, out_path.parent)
        insts = instances(o, a)
        if o.array is None and o.fill is None:
            _add_asset(stage, f"/World/Objects/{o.id}", a, uri, o, insts[0])
            if _hung(o):
                _make_static(stage, f"/World/Objects/{o.id}", uri, "agentic_sim_world_gen:hung")
            elif _floor_cover(o, a, insts[0]):
                prim = stage.GetPrimAtPath(f"/World/Objects/{o.id}")
                op = UsdGeom.Xformable(prim).GetOrderedXformOps()[0]  # translate
                x, y, z = op.Get()
                op.Set(Gf.Vec3d(x, y, z - (a.size_m[2] - COVER_TOP)))
                _make_static(stage, f"/World/Objects/{o.id}", uri, "agentic_sim_world_gen:floor_cover")
            _add_screen(stage, [f"/World/Objects/{o.id}"], a, o, out_path.parent)
            _add_tint(stage, [f"/World/Objects/{o.id}"], a, o)
            continue
        # array: /World/Objects/<id> (group) / <id>_r<row>c<col> (one reference each);
        # fill: /World/Objects/<id> / <id>_<unit>_l<level>_<col>_<row>
        group = UsdGeom.Xform.Define(stage, f"/World/Objects/{o.id}").GetPrim()
        if o.label:
            group.SetCustomDataByKey("agentic_sim_world_gen:label", o.label)
        if o.array is not None:
            group.SetCustomDataByKey("agentic_sim_world_gen:array", o.array.model_dump())
        else:
            group.SetCustomDataByKey("agentic_sim_world_gen:fill", {"on": o.fill.on, "count": len(insts)})
        paths = [f"/World/Objects/{o.id}/{inst.id.replace('.', '_')}" for inst in insts]
        sdf = _stock_sdf(uri) if o.fill is not None else []
        if sdf:
            group.SetCustomDataByKey("agentic_sim_world_gen:box_colliders", len(sdf))
        for path, inst in zip(paths, insts):
            _add_asset(stage, path, a, uri, o, inst)
            if sdf:
                from agentic_sim_world_gen.usd import reset_parts

                reset_parts.box_colliders(stage, f"{path}/{ASSET_CHILD}", sdf)
            if o.fill is not None and not o.fill.simulated(inst.id.split(".", 1)[1]):
                _make_static(stage, path, uri)
            elif o.fill is None and _hung(o):
                _make_static(stage, path, uri, "agentic_sim_world_gen:hung")
        _add_screen(stage, paths, a, o, out_path.parent)
        _add_tint(stage, paths, a, o)

    stage.GetRootLayer().Save()
    return stage
