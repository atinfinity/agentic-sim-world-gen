"""ObjectSpec.tint: repaint some of an asset's materials.

The asset's own files stay as they are: overs in the scene's root layer set the base color of every shader of the
material (UsdPreviewSurface diffuseColor, OmniPBR diffuse_color_constant, OmniGlass glass_color, OpenPBR
base_color). A textured material keeps its texture and is multiplied by the color (UsdUVTexture scale, OmniPBR
diffuse_tint); with Tint.texture = false the color and normal textures are dropped for a flat color. Instanced parts are made editable (instanceable = false) so each unit can carry its own color.
"""

from __future__ import annotations

from fnmatch import fnmatchcase
from typing import Any

from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade

METAL = 0.5  # metallic at or above this is bare metal: not the "main color" a tint without materials paints


def linear(hex_: str) -> Gf.Vec3f:
    """#rrggbb (sRGB) -> linear RGB, what the shaders store."""

    def dec(c: int) -> float:
        c = c / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    return Gf.Vec3f(*(dec(int(hex_[i : i + 2], 16)) for i in (1, 3, 5)))


def _uninstance(root: Usd.Prim) -> int:
    """Make every instance under root editable (instanceable = false in the edit target). Returns how many."""
    n = 0
    while True:
        found = [p for p in Usd.PrimRange(root) if p.IsInstance()]
        if not found:
            return n
        for p in found:
            p.SetInstanceable(False)
        n += len(found)


def _shaders(material: Usd.Prim) -> list[UsdShade.Shader]:
    return [UsdShade.Shader(p) for p in Usd.PrimRange(material) if p.IsA(UsdShade.Shader)]


def _kind(sh: UsdShade.Shader) -> str:
    sid = sh.GetIdAttr().Get() or ""
    if sid == "UsdPreviewSurface":
        return "preview"
    if sid.startswith("ND_open_pbr"):
        return "openpbr"
    src = sh.GetSourceAsset("mdl")
    if src is not None and src.path:
        return "glass" if "glass" in src.path.lower() else "mdl"
    return ""


def _value(sh: UsdShade.Shader, name: str) -> Any:
    i = sh.GetInput(name)
    if not i or i.HasConnectedSource():
        return None
    return i.Get()


def describe(material: Usd.Prim) -> dict[str, Any]:
    """{color: linear rgb or None (textured), metal, glass} from the material's shaders."""
    color = metal = None
    glass = False
    for sh in _shaders(material):
        k = _kind(sh)
        if k == "preview":
            if sh.GetInput("diffuseColor") and sh.GetInput("diffuseColor").HasConnectedSource():
                color = color or "texture"
            elif _value(sh, "diffuseColor") is not None:
                color = tuple(_value(sh, "diffuseColor"))
            metal = _value(sh, "metallic") if metal is None else metal
            op = _value(sh, "opacity")
            glass = glass or (op is not None and op < 1.0)
        elif k == "mdl":
            if _value(sh, "diffuse_texture"):
                color = color or "texture"
            elif color is None and _value(sh, "diffuse_color_constant") is not None:
                color = tuple(_value(sh, "diffuse_color_constant"))
            metal = _value(sh, "metallic_constant") if metal is None else metal
        elif k == "glass":
            glass = True
        elif k == "openpbr":
            if color is None and _value(sh, "base_color") is not None:
                color = tuple(_value(sh, "base_color"))
            metal = _value(sh, "base_metalness") if metal is None else metal
            tw = _value(sh, "transmission_weight")
            glass = glass or (tw is not None and tw > 0)
    return {"color": color, "metal": (metal or 0.0) >= METAL, "glass": glass}


def materials_by_area(root: Usd.Prim) -> dict[str, tuple[Usd.Prim, float]]:
    """Material name -> (material prim, surface area it covers, from the parts' bounding boxes) under root."""
    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
    out: dict[str, tuple[Usd.Prim, float]] = {}
    for p in Usd.PrimRange(root):
        if not p.IsA(UsdGeom.Mesh):
            continue
        r = cache.ComputeWorldBound(p).ComputeAlignedRange()
        if r.IsEmpty():
            continue
        a, b, c = r.GetSize()
        area = 2 * (a * b + b * c + c * a)
        faces = len(UsdGeom.Mesh(p).GetFaceVertexCountsAttr().Get() or []) or 1
        parts = [(s.GetPrim(), len(s.GetIndicesAttr().Get() or [])) for s in UsdGeom.Subset.GetAllGeomSubsets(UsdGeom.Mesh(p))]
        rest = max(faces - sum(n for _, n in parts), 0)
        for prim, n in [(p, rest), *parts]:
            if n <= 0:
                continue
            m = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()[0]
            if not m:
                continue
            name = m.GetPrim().GetName()
            prev = out.get(name, (m.GetPrim(), 0.0))
            out[name] = (prev[0], prev[1] + area * n / faces)
    return out


def main_color(found: dict[str, tuple[Usd.Prim, float]], skip: set[str]) -> list[str]:
    """The materials of the asset's main color: group the painted (not glass, not metal, not screen) materials by
    color and take the group with the largest area. Falls back to metal when nothing is painted."""
    info = {n: describe(m) for n, (m, _) in found.items() if n not in skip}
    for allow_metal in (False, True):
        groups: dict[Any, list[str]] = {}
        area: dict[Any, float] = {}
        for n, d in info.items():
            if d["glass"] or (d["metal"] and not allow_metal) or d["color"] is None:
                continue
            key = d["color"] if d["color"] == "texture" else tuple(round(c, 2) for c in d["color"])
            key = (key, n) if key == "texture" else key  # textures differ; one material each
            groups.setdefault(key, []).append(n)
            area[key] = area.get(key, 0.0) + found[n][1]
        if groups:
            return sorted(groups[max(area, key=area.get)])
    return []


MDL_TEXTURES = ("diffuse_texture", "normalmap_texture")  # dropped by texture=False (OmniPBR falls back to constants)


def paint(material: Usd.Prim, color: Gf.Vec3f, texture: bool = True) -> list[str]:
    """Set the material's base color on every shader that has one. Returns the inputs changed.
    texture=False drops the color and normal textures first, so the color is flat instead of multiplying them."""
    stage = material.GetStage()
    changed = []

    def put(sh: UsdShade.Shader, name: str, value, kind=Sdf.ValueTypeNames.Color3f) -> None:
        i = sh.GetInput(name) or sh.CreateInput(name, kind)
        i.Set(value)
        changed.append(f"{sh.GetPath()}.{name}")

    for sh in _shaders(material):
        k = _kind(sh)
        if not texture:
            if k in ("preview", "openpbr"):
                for name in ("diffuseColor", "normal") if k == "preview" else ("base_color",):
                    i = sh.GetInput(name)
                    if i and i.HasConnectedSource():
                        i.DisconnectSource()
                        changed.append(f"{sh.GetPath()}.{name}")
            elif k == "mdl":
                for name in MDL_TEXTURES:
                    if _value(sh, name):
                        put(sh, name, Sdf.AssetPath(""), Sdf.ValueTypeNames.Asset)
                put(sh, "diffuse_tint", Gf.Vec3f(1, 1, 1))
        if k == "preview":
            i = sh.GetInput("diffuseColor")
            if i and i.HasConnectedSource():
                for src in i.GetConnectedSources()[0]:
                    tex = UsdShade.Shader(stage.GetPrimAtPath(src.source.GetPath()))
                    if tex and tex.GetIdAttr().Get() == "UsdUVTexture":
                        s = tex.GetInput("scale") or tex.CreateInput("scale", Sdf.ValueTypeNames.Float4)
                        s.Set(Gf.Vec4f(color[0], color[1], color[2], 1.0))
                        changed.append(f"{tex.GetPath()}.scale")
            else:
                put(sh, "diffuseColor", color)
        elif k == "mdl":
            put(sh, "diffuse_tint" if _value(sh, "diffuse_texture") else "diffuse_color_constant", color)
        elif k == "glass":
            put(sh, "glass_color", color)
        elif k == "openpbr":
            if sh.GetInput("base_color") and sh.GetInput("base_color").HasConnectedSource():
                continue  # an image node: left as it is (RTX uses the MDL shader)
            put(sh, "base_color", color)
    return changed


def apply_tint(stage: Usd.Stage, prim_paths: list[str], asset, o) -> list[dict[str, Any]]:
    """Paint o.tint on every unit (the `Asset` child of each path). Returns what was done per tint (customData)."""
    from agentic_sim_world_gen.usd.compose import ASSET_CHILD

    roots = [stage.GetPrimAtPath(f"{p}/{ASSET_CHILD}") for p in prim_paths]
    roots = [r for r in roots if r]
    if not roots:
        return []
    for r in roots:
        _uninstance(r)
    found = materials_by_area(roots[0])
    screens = {s["material"] for s in getattr(asset, "screens", None) or []}
    done = []
    for t in o.tint:
        if t.materials is None:
            names = main_color(found, screens)
        else:
            names = sorted(n for n in found if any(fnmatchcase(n, pat) for pat in t.materials))
        if not names:
            done.append({"color": t.color, "materials": t.materials, "error": "no material matched"})
            continue
        rgb = linear(t.color)
        inputs = 0
        for n in names:
            m = found[n][0]
            path = m.GetPath()
            if not path.HasPrefix(roots[0].GetPath()):  # bound outside the asset: shared, paint once
                inputs += len(paint(m, rgb, t.texture))
                continue
            rel = path.MakeRelativePath(roots[0].GetPath())
            for r in roots:
                q = stage.GetPrimAtPath(r.GetPath().AppendPath(rel))
                if q:
                    inputs += len(paint(q, rgb, t.texture))
        done.append({"color": t.color, "materials": names, "inputs": inputs, **({} if t.texture else {"texture": False})})
    return done
