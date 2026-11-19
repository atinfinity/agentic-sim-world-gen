"""The USD of a plain board (agentic_sim_world_gen.assets.generic): written into the scene and referenced like an asset.

    <scene>/assets/generic/<kind>_<w>x<h>[_notray].usda     defaultPrim /Board (Z-up, meters, front +X)
        Board     Xform, customData agentic_sim_world_gen:generic {kind, width, height, tray}
            Surface                    the writing surface (chalkboard: matte dark green, whiteboard: glossy white)
            Frame_B, Frame_T, Frame_L, Frame_R   thin aluminium frame
            Tray, Tray_Lip             chalk / marker tray along the bottom (left out with tray=0)
            Looks/

Every part is a cube with a static collider (no rigid body: the board is mounted on its wall).
"""

from __future__ import annotations

from pathlib import Path

from pxr import Usd, UsdGeom

from agentic_sim_world_gen.assets.generic import DEPTH, KINDS, TRAY_D, parse
from agentic_sim_world_gen.usd.door import _bind, _cube, _material

SCENE_DIR = "assets/generic"
BAR = 0.03  # frame bar width [m]
PANEL_T = 0.018  # board panel thickness [m]
TRAY_T = 0.012  # tray board thickness
LIP_H = 0.03  # tray lip height
ALUMINIUM = ((0.72, 0.73, 0.75), 0.35, 0.8)


def _linear(hexcolor: str) -> tuple[float, float, float]:
    """#rrggbb (sRGB) -> linear RGB for UsdPreviewSurface diffuseColor."""
    def ch(v: int) -> float:
        c = v / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    h = hexcolor.lstrip("#")
    return tuple(round(ch(int(h[i:i + 2], 16)), 4) for i in (0, 2, 4))  # type: ignore[return-value]


def stem(p: dict) -> str:
    return f"{p['kind']}_{p['w']:g}x{p['h']:g}{'' if p['tray'] else '_notray'}".replace(".", "p")


def build(stage: Usd.Stage, root: str, p: dict) -> list[str]:
    """Author the board's parts under `root` (asset frame: front +X, width along Y, bottom at z = 0).
    Returns the part paths."""
    kind, w, h, tray = p["kind"], p["w"], p["h"], p["tray"]
    k = KINDS[kind]
    d = TRAY_D if tray else DEPTH
    x0 = -d / 2  # the back, against the wall
    looks = f"{root}/Looks"
    surf = _material(stage, f"{looks}/{kind.capitalize()}Surface", _linear(k["color"]), 1.0, k["roughness"], 0.0)
    alu = _material(stage, f"{looks}/BoardFrame", ALUMINIUM[0], 1.0, ALUMINIUM[1], ALUMINIUM[2])
    parts = [("Surface", (x0 + PANEL_T / 2, 0.0, h / 2), (PANEL_T, w - 2 * BAR, h - 2 * BAR), surf),
             ("Frame_B", (x0 + DEPTH / 2, 0.0, BAR / 2), (DEPTH, w, BAR), alu),
             ("Frame_T", (x0 + DEPTH / 2, 0.0, h - BAR / 2), (DEPTH, w, BAR), alu),
             ("Frame_L", (x0 + DEPTH / 2, (w - BAR) / 2, h / 2), (DEPTH, BAR, h - 2 * BAR), alu),
             ("Frame_R", (x0 + DEPTH / 2, -(w - BAR) / 2, h / 2), (DEPTH, BAR, h - 2 * BAR), alu)]
    if tray:  # a shelf along the bottom frame with a lip at its front edge
        parts += [("Tray", (x0 + TRAY_D / 2, 0.0, TRAY_T / 2), (TRAY_D, w - 2 * BAR, TRAY_T), alu),
                  ("Tray_Lip", (x0 + TRAY_D - TRAY_T / 2, 0.0, LIP_H / 2), (TRAY_T, w - 2 * BAR, LIP_H), alu)]
    paths = []
    for name, center, size, m in parts:
        c = _cube(stage, f"{root}/{name}", center, size, collider=True)
        _bind(c.GetPrim(), m)
        paths.append(c.GetPath().pathString)
    return paths


def write(path: Path, p: dict) -> None:
    """The board as its own USD file (defaultPrim /Board). Built in memory and exported: the file's layer may still
    be open from an earlier compose in this process, where CreateNew fails (the content only depends on the name)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    root = UsdGeom.Xform.Define(stage, "/Board").GetPrim()
    stage.SetDefaultPrim(root)
    root.SetCustomDataByKey("agentic_sim_world_gen:generic", {"kind": p["kind"], "width": p["w"], "height": p["h"],
                                                 "tray": p["tray"]})
    build(stage, "/Board", p)
    stage.GetRootLayer().Export(str(path))


def scene_ref(uri: str, scene_dir: Path) -> str:
    """Write the board of a "generic:" uri into <scene>/assets/generic/ and return the relative reference."""
    p = parse(uri)
    if p is None:
        raise ValueError(f"bad generic uri {uri!r} (expected generic:chalkboard?w=3.6&h=1.2)")
    rel = f"{SCENE_DIR}/{stem(p)}.usda"
    write(Path(scene_dir) / rel, p)
    return f"./{rel}"
