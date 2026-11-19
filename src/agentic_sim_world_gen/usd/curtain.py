"""The USD of a pleated window curtain (agentic_sim_world_gen.assets.generic_curtain): written into the scene and referenced
like an asset.

    <scene>/assets/generic/curtain_<style>_<w>x<h>[_<color>].usda     defaultPrim /Curtain (Z-up, meters, front +X)
        Curtain   Xform, customData agentic_sim_world_gen:generic {kind, width, height, style, color}
            Rail, Rail_Sheer          thin rods along the top (static colliders: the only ones)
            Panel_L, Panel_R          opaque panels (open: gathered to the sides; closed: meeting in the middle)
            Sheer                     one translucent panel across the window (sheer / open_sheer)
            Looks/

A panel is a strip of quads whose x follows a sine across its width (the pleats), from just under its rail down
to the bottom. Fabric has no collider (a robot pushes it aside; a rigid one would stop it at the window); the rail
is the thin static collider, so check_in_isaac does not report the curtain as no_collider. The sheer panel casts
no shadow (primvars:doNotCastShadows): like the window's glass, RTX shades with its opacity but a
translucent surface still casts a full shadow, and the daylight through the window would be gone.
"""

from __future__ import annotations

from pathlib import Path

from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics, UsdShade, Vt

from agentic_sim_world_gen.assets.generic_curtain import X_SHEER, panels, parse, wave
from agentic_sim_world_gen.usd.board import SCENE_DIR, _linear
from agentic_sim_world_gen.usd.door import _bind, _material

RAIL_R = 0.012  # rail radius [m]
HANG = 0.035  # the top of the fabric is this far under the top of the bounding box (rings / hooks)
SHEER_OPACITY = 0.4


def stem(p: dict) -> str:
    color = p["sheer_color"] if p["style"] == "sheer" else p["color"]
    return f"curtain_{p['style']}_{p['w']:g}x{p['h']:g}_{color.lstrip('#').lower()}".replace(".", "p")


def _panel(stage: Usd.Stage, path: str, y0: float, y1: float, x0: float, kind: str, z0: float,
           z1: float) -> UsdGeom.Mesh:
    pts, normals = [], []
    for x, y, slope in wave(y0, y1, x0, kind):
        nrm = Gf.Vec3f(1.0, -slope, 0.0).GetNormalized()
        pts += [Gf.Vec3f(x, y, z0), Gf.Vec3f(x, y, z1)]
        normals += [nrm, nrm]
    cols = len(pts) // 2
    mesh = UsdGeom.Mesh.Define(stage, path)
    mesh.CreatePointsAttr(Vt.Vec3fArray(pts))
    mesh.CreateFaceVertexCountsAttr(Vt.IntArray([4] * (cols - 1)))
    # counter-clockwise seen from the front (+X): along +Y, then up, so the face normal is +X like `normals`
    idx = []
    for i in range(cols - 1):
        idx += [2 * i, 2 * i + 2, 2 * i + 3, 2 * i + 1]
    mesh.CreateFaceVertexIndicesAttr(Vt.IntArray(idx))
    mesh.CreateNormalsAttr(Vt.Vec3fArray(normals))
    mesh.SetNormalsInterpolation(UsdGeom.Tokens.vertex)
    mesh.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
    mesh.CreateDoubleSidedAttr(True)
    mesh.CreateExtentAttr(UsdGeom.PointBased.ComputeExtent(Vt.Vec3fArray(pts)))
    return mesh


def _rail(stage: Usd.Stage, path: str, x: float, w: float, z: float, mat: UsdShade.Material) -> str:
    c = UsdGeom.Cylinder.Define(stage, path)
    c.CreateAxisAttr(UsdGeom.Tokens.y)
    c.CreateHeightAttr(w)
    c.CreateRadiusAttr(RAIL_R)
    c.CreateExtentAttr([Gf.Vec3f(-RAIL_R, -w / 2, -RAIL_R), Gf.Vec3f(RAIL_R, w / 2, RAIL_R)])
    UsdGeom.Xformable(c).AddTranslateOp().Set(Gf.Vec3d(x, 0.0, z))
    UsdPhysics.CollisionAPI.Apply(c.GetPrim())
    _bind(c.GetPrim(), mat)
    return c.GetPath().pathString


def build(stage: Usd.Stage, root: str, p: dict) -> list[str]:
    """Author the curtain's parts under `root` (asset frame: front +X, width along Y, bottom at z = 0).
    Returns the part paths."""
    w, h = p["w"], p["h"]
    looks = f"{root}/Looks"
    rail_mat = _material(stage, f"{looks}/CurtainRail", (0.62, 0.63, 0.65), 1.0, 0.4, 0.8)
    drape = _material(stage, f"{looks}/CurtainFabric", _linear(p["color"]), 1.0, 0.9, 0.0)
    sheer = None
    out = []
    z_rail = h - RAIL_R
    rails = sorted({x for *_, x, _ in panels(p)})
    for x in rails:
        out.append(_rail(stage, f"{root}/Rail" + ("_Sheer" if x == X_SHEER and len(rails) > 1 else ""), x, w,
                         z_rail, rail_mat))
    for name, y0, y1, x0, kind in panels(p):
        # the sheer behind open panels is a little shorter: its hem stays off the floor behind theirs
        z0 = 0.02 if kind == "sheer" and p["style"] == "open_sheer" else 0.0
        m = _panel(stage, f"{root}/{name}", y0, y1, x0, kind, z0, h - HANG)
        if kind == "sheer":
            if sheer is None:
                sheer = _material(stage, f"{looks}/CurtainSheer", _linear(p["sheer_color"]), SHEER_OPACITY, 0.8, 0.0)
            _bind(m.GetPrim(), sheer)
            # RTX: a translucent panel still casts a full shadow, and the daylight through the window is gone
            UsdGeom.PrimvarsAPI(m.GetPrim()).CreatePrimvar("doNotCastShadows", Sdf.ValueTypeNames.Bool).Set(True)
        else:
            _bind(m.GetPrim(), drape)
        out.append(m.GetPath().pathString)
    return out


def write(path: Path, p: dict) -> None:
    """The curtain as its own USD file (defaultPrim /Curtain), built in memory and exported (see board.write)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    root = UsdGeom.Xform.Define(stage, "/Curtain").GetPrim()
    stage.SetDefaultPrim(root)
    root.SetCustomDataByKey("agentic_sim_world_gen:generic", {"kind": "curtain", "width": p["w"], "height": p["h"],
                                                 "style": p["style"], "color": p["color"]})
    build(stage, "/Curtain", p)
    stage.GetRootLayer().Export(str(path))


def scene_ref(uri: str, scene_dir: Path) -> str:
    """Write the curtain of a "generic:curtain" uri into <scene>/assets/generic/ and return the relative reference."""
    p = parse(uri)
    if p is None:
        raise ValueError(f"bad generic curtain uri {uri!r} (expected generic:curtain?w=2.2&h=2.3&style=open)")
    rel = f"{SCENE_DIR}/{stem(p)}.usda"
    write(Path(scene_dir) / rel, p)
    return f"./{rel}"
