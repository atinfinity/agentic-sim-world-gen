"""The USD of a plain rug (agentic_sim_world_gen.assets.rug): written into the scene and referenced like an asset.

    <scene>/assets/generic/rug_<w>x<d>x<t>[_round][_<material>].usda     defaultPrim /Rug (Z-up, meters)
        Rug        Xform, customData agentic_sim_world_gen:generic {kind, w, d, t, shape, mat}
            Slab       mesh: the outline (rounded corners, or an ellipse) at z = 0, the top inset by BEVEL at z = t,
                       UVs in meters / the texture's real size (it tiles like the floor); no collider
            Collider   1 mm static box at the floor (invisible, purpose guide)
            Looks/RugMaterial   UsdPreviewSurface with the CC0 PBR color / roughness / normal maps (copied into
                       <scene>/textures/ and referenced relative to this file), or a plain color

Physics: furniture on the rug stands at z = 0, in the pile; a full-height collider would push its legs up when
physics starts (a rigid rug lifted a low table and its things slid off) and makes a 1-2 cm step for wheeled
robots. So the slab is render-only and the collider is a 1 mm box: Isaac Sim's check sees a static object with a
collider, robots drive over it, things dropped on it land 1 mm above the floor.
"""

from __future__ import annotations

import logging
import math
import os
from pathlib import Path

from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade

from agentic_sim_world_gen.assets.rug import PLAIN_COLOR

log = logging.getLogger(__name__)

SCENE_DIR = "assets/generic"
COLLIDER_H = 0.001  # [m] the rug's collider above the floor
CORNER_R = 0.03  # [m] rounded corners of a rectangular rug
SEGMENTS = 64  # points of a round rug's outline
CORNER_SEG = 4  # points per rounded corner


def stem(p: dict) -> str:
    s = f"rug_{p['w']:g}x{p['d']:g}x{p['t']:g}{'_round' if p['shape'] == 'round' else ''}"
    if p.get("mat"):
        s += "_" + p["mat"].replace("/", "_")
    return s.replace(".", "p")


def outline(p: dict, inset: float = 0.0) -> list[tuple[float, float]]:
    """The rug's outline (counter-clockwise, centered at 0), `inset` m inside its edge."""
    a, b = p["w"] / 2 - inset, p["d"] / 2 - inset
    if p["shape"] == "round":
        return [(a * math.cos(2 * math.pi * i / SEGMENTS), b * math.sin(2 * math.pi * i / SEGMENTS))
                for i in range(SEGMENTS)]
    r = max(min(CORNER_R - inset, a / 2, b / 2), 0.0)
    pts = []
    for cx, cy, start in ((a - r, b - r, 0.0), (-a + r, b - r, 90.0), (-a + r, -b + r, 180.0), (a - r, -b + r, 270.0)):
        for k in range(CORNER_SEG + 1):
            t = math.radians(start + 90.0 * k / CORNER_SEG)
            pts.append((cx + r * math.cos(t), cy + r * math.sin(t)))
    return pts


def bevel(p: dict) -> float:
    """How far the top edge is inside the bottom one: a soft, slightly rounded-looking edge."""
    return round(min(p["t"] * 0.6, 0.01, p["d"] / 10), 4)


def slab(stage: Usd.Stage, path: str, p: dict, tile_m: float) -> UsdGeom.Mesh:
    """The slab: bottom face, top face (inset by `bevel`) and a sloped side band, UVs = xy / tile_m. Normals are
    per face (faceVarying, like agentic_sim_world_gen.usd.look.box_mesh): with shared points a renderer would smooth the flat top
    into the bevel and tilt it by tens of degrees at the edge."""
    lo, hi = outline(p), outline(p, bevel(p))
    n, t = len(lo), p["t"]
    points = [Gf.Vec3f(x, y, 0.0) for x, y in lo] + [Gf.Vec3f(x, y, t) for x, y in hi]
    counts = [n, n]
    idx = list(range(n, 2 * n)) + list(range(n - 1, -1, -1))  # top ccw, bottom cw (seen from above)
    normals = [Gf.Vec3f(0, 0, 1)] * n + [Gf.Vec3f(0, 0, -1)] * n
    for i in range(n):
        j = (i + 1) % n
        counts.append(4)
        idx += [i, j, n + j, n + i]  # outward
        nrm = Gf.Cross(points[j] - points[i], points[n + i] - points[i])
        normals += [nrm.GetNormalized() if nrm.GetLength() > 1e-12 else Gf.Vec3f(0, 0, 1)] * 4
    mesh = UsdGeom.Mesh.Define(stage, path)
    mesh.CreatePointsAttr(points)
    mesh.CreateFaceVertexCountsAttr(counts)
    mesh.CreateFaceVertexIndicesAttr(idx)
    mesh.CreateNormalsAttr(normals)
    mesh.SetNormalsInterpolation(UsdGeom.Tokens.faceVarying)
    mesh.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
    mesh.CreateExtentAttr([Gf.Vec3f(-p["w"] / 2, -p["d"] / 2, 0.0), Gf.Vec3f(p["w"] / 2, p["d"] / 2, t)])
    st = [Gf.Vec2f(q[0] / tile_m, q[1] / tile_m) for q in points]
    UsdGeom.PrimvarsAPI(mesh).CreatePrimvar("st", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.vertex).Set(st)
    return mesh


def _surface(p: dict) -> dict:
    """agentic_sim_world_gen.usd.look surface of the rug: the PBR material's maps (fetched once into the cache), or a plain
    color when there is none or it cannot be fetched (offline)."""
    from agentic_sim_world_gen import pbr
    from agentic_sim_world_gen.materials import CATEGORY, linear

    c = CATEGORY["Carpet"]
    plain = {"name": "rug", "color": list(linear(PLAIN_COLOR)), "roughness": c["roughness"], "metallic": 0.0,
             "static_friction": c["static_friction"], "dynamic_friction": c["dynamic_friction"], "tile_m": 1.0}
    r = pbr.by_id(p["mat"]) if p.get("mat") else None
    if r is None:
        if p.get("mat"):
            log.warning("rug material %s is not in the CC0 PBR index: plain color", p["mat"])
        return plain
    try:
        s = pbr.surface(r)
    except (pbr.FetchError, OSError) as e:
        log.warning("rug material %s could not be fetched (%s): plain color", r["id"], e)
        return {**plain, "fetch_error": str(e)}
    return {**plain, **s, "name": r["id"], "metallic": s.get("metallic", 0.0)}


def _relative_textures(mat: UsdShade.Material, here: Path) -> None:
    """Texture paths relative to the rug's file: the scene folder can be moved or copied."""
    for prim in Usd.PrimRange(mat.GetPrim()):
        inp = UsdShade.Shader(prim).GetInput("file") if prim.IsA(UsdShade.Shader) else None
        v = inp.Get() if inp else None
        if v is not None and v.path and Path(v.path).is_absolute():
            inp.Set(Sdf.AssetPath(os.path.relpath(v.path, here).replace(os.sep, "/")))


def build(stage: Usd.Stage, root: str, p: dict, scene_dir: Path | None = None, here: Path | None = None) -> dict:
    """Author the rug under `root` (asset frame: centered, bottom at z = 0). Returns {"slab", "collider", "surface"}."""
    from agentic_sim_world_gen.usd.door import _cube
    from agentic_sim_world_gen.usd.look import bind, make_material

    s = _surface(p)
    mesh = slab(stage, f"{root}/Slab", p, float(s.get("tile_m") or 1.0))
    mat = make_material(stage, f"{root}/Looks/RugMaterial", s, scene_dir)
    if here is not None:
        _relative_textures(mat, here)
    bind(mesh.GetPrim(), mat)
    col = _cube(stage, f"{root}/Collider", (0.0, 0.0, COLLIDER_H / 2), (p["w"], p["d"], COLLIDER_H), collider=True)
    col.CreatePurposeAttr(UsdGeom.Tokens.guide)
    col.CreateVisibilityAttr(UsdGeom.Tokens.invisible)
    UsdShade.MaterialBindingAPI.Apply(col.GetPrim()).Bind(mat, UsdShade.Tokens.weakerThanDescendants, "physics")
    return {"slab": mesh.GetPath().pathString, "collider": col.GetPath().pathString, "surface": s}


def write(path: Path, p: dict, scene_dir: Path) -> dict:
    """The rug as its own USD file (defaultPrim /Rug), built in memory and exported (see agentic_sim_world_gen.usd.board.write)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    root = UsdGeom.Xform.Define(stage, "/Rug").GetPrim()
    stage.SetDefaultPrim(root)
    built = build(stage, "/Rug", p, scene_dir, path.parent)
    s = built["surface"]
    root.SetCustomDataByKey("agentic_sim_world_gen:generic", {**{k: v for k, v in p.items() if v is not None},
                                                 "textured": bool(s.get("texture"))})
    stage.GetRootLayer().Export(str(path))
    return built


def scene_ref(uri: str, scene_dir: Path) -> str:
    """Write the rug of a "generic:rug?..." uri into <scene>/assets/generic/ and return the relative reference."""
    from agentic_sim_world_gen.assets.generic import parse

    p = parse(uri)
    if p is None or p.get("kind") != "rug":
        raise ValueError(f"bad generic rug uri {uri!r} (expected generic:rug?w=2&d=1.4&t=0.012)")
    rel = f"{SCENE_DIR}/{stem(p)}.usda"
    write(Path(scene_dir) / rel, p, Path(scene_dir))
    return f"./{rel}"
