"""ObjectSpec.tint: repaint some of an asset's materials.

The asset's own files stay as they are: overs in the scene's root layer set the base color of every shader of the
material (UsdPreviewSurface diffuseColor, OmniPBR diffuse_color_constant, OmniGlass glass_color, OpenPBR
base_color, MaterialX standard_surface base_color). A textured material keeps its texture and is multiplied by the
color (UsdUVTexture scale, OmniPBR diffuse_tint, an ND_multiply_color3 node before standard_surface base_color);
with Tint.texture = false the color and normal textures are dropped for a flat color. Instanced parts are made editable (instanceable = false) so each unit can carry its own color.
"""

from __future__ import annotations

import re
from fnmatch import fnmatchcase
from typing import Any

from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade

METAL = 0.5  # metallic at or above this is bare metal: not the "main color" a tint without materials paints
# A metal by its name: SimReady's m_opaque__metal__chrome_a01 has its metallic in an ORM texture, no value
METAL_NAME = re.compile(r"(^|_)(metal|chrome|steel|alumin(i)?um|brass|copper|iron)(_|$)", re.IGNORECASE)
OTHERS = 3  # materials listed next to the main color a tint painted, to pick another with `materials`


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
    if sid.startswith("ND_standard_surface"):  # Poly Haven's outputs:mtlx:surface, what RTX renders (#30)
        return "stdsurf"
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
        elif k == "stdsurf":
            if sh.GetInput("base_color") and sh.GetInput("base_color").HasConnectedSource():
                color = color or "texture"
            elif color is None and _value(sh, "base_color") is not None:
                color = tuple(_value(sh, "base_color"))
            metal = _value(sh, "metalness") if metal is None else metal
            tw = _value(sh, "transmission")
            glass = glass or (tw is not None and tw > 0)
        elif k == "openpbr":
            if color is None and _value(sh, "base_color") is not None:
                color = tuple(_value(sh, "base_color"))
            metal = _value(sh, "base_metalness") if metal is None else metal
            tw = _value(sh, "transmission_weight")
            glass = glass or (tw is not None and tw > 0)
    return {"color": color, "metal": (metal or 0.0) >= METAL or bool(METAL_NAME.search(material.GetName())),
            "glass": glass}


def _face_area(points, counts, indices, faces) -> float:
    """Surface area of these faces of a mesh (fan triangles), in the mesh's own units."""
    starts, at = [], 0
    for c in counts:
        starts.append(at)
        at += c
    area = 0.0
    for f in faces:
        s, c = starts[f], counts[f]
        if s + c > len(indices):
            continue
        v0 = points[indices[s]]
        for k in range(1, c - 1):
            a, b = points[indices[s + k]] - v0, points[indices[s + k + 1]] - v0
            area += (a ^ b).GetLength() / 2
    return area


def materials_by_area(root: Usd.Prim) -> dict[str, tuple[list[Usd.Prim], float]]:
    """Material name -> (every material prim of that name, surface area it covers) under root. SimReady parts
    (a cabinet's body, drawers, doors) each carry their own copy of a material, and all of them are painted (one of 7 was). The area is the faces' own (a chair's 13 small plastic parts had more bounding-box area than
    its fabric seat and back), scaled by the part's transform."""
    xc = UsdGeom.XformCache(Usd.TimeCode.Default())
    out: dict[str, tuple[list[Usd.Prim], float]] = {}
    for p in Usd.PrimRange(root):
        if not p.IsA(UsdGeom.Mesh):
            continue
        mesh = UsdGeom.Mesh(p)
        pts = [Gf.Vec3d(v) for v in mesh.GetPointsAttr().Get() or []]
        counts = list(mesh.GetFaceVertexCountsAttr().Get() or [])
        indices = list(mesh.GetFaceVertexIndicesAttr().Get() or [])
        if not pts or not counts:
            continue
        m3 = xc.GetLocalToWorldTransform(p).ExtractRotationMatrix()
        scale = abs(m3.GetDeterminant()) ** (2 / 3)  # area scale of a (nearly) uniform scale
        parts = [(s.GetPrim(), [i for i in s.GetIndicesAttr().Get() or [] if 0 <= i < len(counts)])
                 for s in UsdGeom.Subset.GetAllGeomSubsets(mesh)]
        used = {i for _, ii in parts for i in ii}
        rest = [i for i in range(len(counts)) if i not in used]
        for prim, faces in [(p, rest), *parts]:
            if not faces:
                continue
            m = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()[0]
            if not m:
                continue
            name = m.GetPrim().GetName()
            prims, area = out.get(name, ([], 0.0))
            if m.GetPrim() not in prims:
                prims = [*prims, m.GetPrim()]
            out[name] = (prims, area + _face_area(pts, counts, indices, faces) * scale)
    return out


def main_color(found: dict[str, tuple[list[Usd.Prim], float]], skip: set[str]) -> list[str]:
    """The materials of the asset's main color: group the painted (not glass, not metal, not screen) materials by
    color and take the group with the largest area. Falls back to metal when nothing is painted."""
    info = {n: describe(ms[0]) for n, (ms, _) in found.items() if n not in skip}
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
            if k in ("preview", "openpbr", "stdsurf"):
                names = {"preview": ("diffuseColor", "normal"), "openpbr": ("base_color",),
                         "stdsurf": ("base_color", "normal")}[k]
                for name in names:
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
        elif k == "stdsurf":
            i = sh.GetInput("base_color")
            if i and i.HasConnectedSource():  # a texture: multiply it by the color in a node of our own
                changed.extend(_multiply(sh, i, color))
            else:
                put(sh, "base_color", color)
    return changed


TINT_NODE = "agentic_sim_world_gen_tint"  # the ND_multiply_color3 put before a textured standard_surface base_color


def _multiply(sh: UsdShade.Shader, base: UsdShade.Input, color: Gf.Vec3f) -> list[str]:
    """base_color = (what it was connected to) * color, through an ND_multiply_color3 next to the shader. Painting
    again only changes the node's color."""
    stage = sh.GetPrim().GetStage()
    path = sh.GetPath().GetParentPath().AppendChild(f"{sh.GetPath().name}_{TINT_NODE}")
    node = UsdShade.Shader(stage.GetPrimAtPath(path)) if stage.GetPrimAtPath(path) else None
    if node is None:
        node = UsdShade.Shader.Define(stage, path)
        node.CreateIdAttr("ND_multiply_color3")
        src = base.GetConnectedSources()[0][0]
        node.CreateInput("in1", Sdf.ValueTypeNames.Color3f).ConnectToSource(src)
        out = node.CreateOutput("out", Sdf.ValueTypeNames.Color3f)
        base.ConnectToSource(out)
    node.CreateInput("in2", Sdf.ValueTypeNames.Color3f).Set(color)
    return [f"{path}.in2", f"{sh.GetPath()}.base_color"]


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
            why = "no material matched" if found else "the asset's materials could not be read (not opened here)"
            done.append({"color": t.color, "materials": t.materials, "error": why})
            continue
        rgb = linear(t.color)
        inputs = 0
        for m in (m for n in names for m in found[n][0]):
            path = m.GetPath()
            if not path.HasPrefix(roots[0].GetPath()):  # bound outside the asset: shared, paint once
                inputs += len(paint(m, rgb, t.texture))
                continue
            rel = path.MakeRelativePath(roots[0].GetPath())
            for r in roots:
                q = stage.GetPrimAtPath(r.GetPath().AppendPath(rel))
                if q:
                    inputs += len(paint(q, rgb, t.texture))
        row = {"color": t.color, "materials": names, "inputs": inputs, **({} if t.texture else {"texture": False})}
        if t.materials is None:  # the main color was a guess: the next largest, to pick with `materials`
            total = sum(a for _, a in found.values()) or 1.0
            rest = sorted(((a, n) for n, (_, a) in found.items() if n not in names), reverse=True)[:OTHERS]
            row["others"] = [f"{n} {a / total:.0%}" for a, n in rest]
            row["share"] = f"{sum(found[n][1] for n in names) / total:.0%}"
        done.append(row)
    return done
