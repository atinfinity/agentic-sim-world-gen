"""Show an image on a signage asset's screen (ObjectSpec.screen).

The asset declares its screens in asset.json `screen` (AssetRecord.screens): the material and its texture. The
texture is usually an atlas (a box unwrapped into a 3x2 grid), with the picture in the cell of the front face only.
So the image is not swapped in as is:

1. find the screen's front face: of the largest flat sides of the meshes bound to the material (a panel's front
   and back), the one whose part of the original texture holds the picture; its UV rectangle and its size in
   the world (the up direction is the stage's +Z);
2. fit the image to the face's aspect ratio (contain / cover / stretch) and draw it into that UV rectangle of
   a copy of the original texture, turned the way the UVs run (scene dir `screens/<id>_<material>.png`);
3. point the material's texture inputs (UsdPreviewSurface, MDL, OpenPBR) that used the original texture at the
   new file, with overs in the scene's root layer.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade

MAX_TEXTURE_PX = 4096  # the copy of the atlas is scaled down to this (the original can be 6144 px wide)
FLAT = 0.999  # |cos| between a face normal and the screen's normal
SIDE_AREA = 0.9  # a side this close to the largest one may be the front (a panel's back is as large)


@dataclass
class ScreenFace:
    """The front face of a screen: UV rectangle, size [m], and the map from UV to the face plane."""

    uv_min: tuple[float, float]
    uv_max: tuple[float, float]
    size_m: tuple[float, float]  # (width, height) as seen from the front
    # (right, up) [m] = m @ (u, v) + t; (right, up) relative to the face's lower-left corner
    m: tuple[tuple[float, float], tuple[float, float]]
    t: tuple[float, float]


def find_material(root: Usd.Prim, name: str) -> Usd.Prim | None:
    for p in Usd.PrimRange(root):
        if p.IsA(UsdShade.Material) and p.GetName() == name:
            return p
    return None


def _faces(mesh: UsdGeom.Mesh, xf: Gf.Matrix4d,
           only: set[int] | None = None) -> list[tuple[Gf.Vec3d, float, list[Gf.Vec3d], list[Gf.Vec2f]]]:
    """(unit normal, area, world points, uvs) of each face (of the faces `only`, a GeomSubset's, if given)."""
    pts = mesh.GetPointsAttr().Get() or []
    counts = mesh.GetFaceVertexCountsAttr().Get() or []
    idx = mesh.GetFaceVertexIndicesAttr().Get() or []
    pv = UsdGeom.PrimvarsAPI(mesh.GetPrim()).GetPrimvar("st")
    if not pv or not pts:
        return []
    uv = pv.ComputeFlattened()
    face_varying = pv.GetInterpolation() == UsdGeom.Tokens.faceVarying
    wpts = [xf.Transform(Gf.Vec3d(p)) for p in pts]
    out, k = [], 0
    for f, n in enumerate(counts):
        if only is not None and f not in only:
            k += n
            continue
        vs = [wpts[i] for i in idx[k:k + n]]
        us = [uv[j] for j in range(k, k + n)] if face_varying else [uv[i] for i in idx[k:k + n]]
        k += n
        if n < 3:
            continue
        nrm = Gf.Vec3d(0, 0, 0)
        for i in range(1, n - 1):
            nrm += Gf.Cross(vs[i] - vs[0], vs[i + 1] - vs[0])
        area = nrm.GetLength() / 2
        if area > 0:
            out.append((nrm / (2 * area), area, vs, us))
    return out


def screen_faces(stage: Usd.Stage, root: Usd.Prim, material: Usd.Prim) -> list[ScreenFace]:
    """The large flat sides of the meshes under root bound to material, largest first. A screen panel has two
    (front and back, the same size); which one shows the picture is decided from the texture (front_face)."""
    mat_path = material.GetPath()
    cache = UsdGeom.XformCache()
    faces = []
    for p in Usd.PrimRange(root):
        if not p.IsA(UsdGeom.Mesh):
            continue
        mesh = UsdGeom.Mesh(p)
        subsets = UsdShade.MaterialBindingAPI(p).GetMaterialBindSubsets()
        # a subset's own binding only: one without inherits the mesh's, and its faces are then the mesh's (review)
        mine = [s for s in subsets if (b := UsdShade.MaterialBindingAPI(s.GetPrim()).GetDirectBinding().GetMaterial())
                and b.GetPath() == mat_path]
        if mine:  # one mesh, a GeomSubset per material (a TV from SimReady Central)
            only = {int(i) for s in mine for i in (s.GetIndicesAttr().Get() or [])}
            faces += _faces(mesh, cache.GetLocalToWorldTransform(p), only)
            continue
        bound = UsdShade.MaterialBindingAPI(p).ComputeBoundMaterial()[0]
        if not bound or bound.GetPath() != mat_path:
            continue
        covered = {int(i) for s in subsets if UsdShade.MaterialBindingAPI(s.GetPrim()).GetDirectBinding().GetMaterial()
                   for i in (s.GetIndicesAttr().Get() or [])}
        faces += _faces(mesh, cache.GetLocalToWorldTransform(p),
                        set(range(len(mesh.GetFaceVertexCountsAttr().Get() or []))) - covered if covered else None)
    groups: list[tuple[Gf.Vec3d, list]] = []  # by direction
    for f in faces:
        for g in groups:
            if Gf.Dot(g[0], f[0]) > FLAT:
                g[1].append(f)
                break
        else:
            groups.append((f[0], [f]))
    areas = [sum(f[1] for f in g[1]) for g in groups]
    big = sorted((a, k) for k, a in enumerate(areas) if a >= SIDE_AREA * max(areas)) if groups else []
    return [_side(*groups[k]) for _, k in reversed(big)]


def _side(normal: Gf.Vec3d, faces: list) -> ScreenFace:
    up = Gf.Vec3d(0, 0, 1)
    up = up - normal * Gf.Dot(up, normal)
    if up.GetLength() < 1e-3:  # a screen facing up or down: "up" is +Y of the stage
        up = Gf.Vec3d(0, 1, 0) - normal * normal[1]
    up = up.GetNormalized()
    right = Gf.Cross(up, normal)  # seen from the front (looking along -normal)
    plane = [(Gf.Dot(v, right), Gf.Dot(v, up)) for f in faces for v in f[2]]
    uvs = [(float(u[0]), float(u[1])) for f in faces for u in f[3]]
    m, t = _fit_affine(uvs, plane)
    rs, ups = [q[0] for q in plane], [q[1] for q in plane]
    us, vs = [q[0] for q in uvs], [q[1] for q in uvs]
    r0, u0 = min(rs), min(ups)
    return ScreenFace(uv_min=(min(us), min(vs)), uv_max=(max(us), max(vs)), size_m=(max(rs) - r0, max(ups) - u0),
                      m=m, t=(t[0] - r0, t[1] - u0))


def front_face(sides: list[ScreenFace], atlas) -> ScreenFace:
    """The side whose part of the original texture holds a picture (the most varied pixels); the back of the
    panel is a flat color."""
    from PIL import ImageStat

    if len(sides) == 1:
        return sides[0]
    small = atlas.convert("L")
    small.thumbnail((512, 512))
    w, h = small.size

    def spread(f: ScreenFace) -> float:
        box = (int(f.uv_min[0] * w), int((1 - f.uv_max[1]) * h), int(f.uv_max[0] * w + 0.999),
               int((1 - f.uv_min[1]) * h + 0.999))
        crop = small.crop(box)
        return ImageStat.Stat(crop).stddev[0] if crop.width and crop.height else 0.0

    return max(sides, key=spread)


def _fit_affine(src: list[tuple[float, float]], dst: list[tuple[float, float]]):
    """Least squares dst = m @ src + t."""
    n = len(src)
    su = sum(p[0] for p in src) / n
    sv = sum(p[1] for p in src) / n
    a = [(p[0] - su, p[1] - sv) for p in src]
    suu = sum(x * x for x, _ in a)
    suv = sum(x * y for x, y in a)
    svv = sum(y * y for _, y in a)
    det = suu * svv - suv * suv
    if abs(det) < 1e-18:
        raise ValueError("the screen's UVs are degenerate")
    rows = []
    for k in range(2):
        mean = sum(q[k] for q in dst) / n
        bu = sum(x * (q[k] - mean) for (x, _), q in zip(a, dst))
        bv = sum(y * (q[k] - mean) for (_, y), q in zip(a, dst))
        mu = (svv * bu - suv * bv) / det
        mv = (suu * bv - suv * bu) / det
        rows.append((mu, mv, mean - mu * su - mv * sv))
    return ((rows[0][0], rows[0][1]), (rows[1][0], rows[1][1])), (rows[0][2], rows[1][2])


def _hex_rgb(c: str) -> tuple[int, int, int]:
    return int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)


def fit_image(img, size: tuple[int, int], fit: str, background: str):
    """The image on a canvas of size (w, h): contain (letterbox), cover (crop) or stretch."""
    from PIL import Image

    img = img.convert("RGB")
    w, h = size
    if fit == "stretch":
        return img.resize((w, h), Image.LANCZOS)
    s = (min if fit == "contain" else max)(w / img.width, h / img.height)
    iw, ih = max(1, round(img.width * s)), max(1, round(img.height * s))
    img = img.resize((iw, ih), Image.LANCZOS)
    canvas = Image.new("RGB", (w, h), _hex_rgb(background))
    canvas.paste(img, ((w - iw) // 2, (h - ih) // 2))
    return canvas


def render_texture(atlas_path: Path, image_path: Path, sides: list[ScreenFace], fit: str, background: str,
                   out: Path) -> ScreenFace:
    """Copy of the atlas with the image drawn into the front face's UV rectangle (as seen from the front).
    Returns the face used."""
    from PIL import Image

    atlas = Image.open(atlas_path).convert("RGB")
    face = front_face(sides, atlas)
    k = min(1.0, MAX_TEXTURE_PX / max(atlas.size))
    if k < 1:
        atlas = atlas.resize((round(atlas.width * k), round(atlas.height * k)), Image.LANCZOS)
    tw, th = atlas.size
    x0, x1 = int(face.uv_min[0] * tw), min(tw, int(face.uv_max[0] * tw + 0.999))
    y0, y1 = int((1 - face.uv_max[1]) * th), min(th, int((1 - face.uv_min[1]) * th + 0.999))
    cw, ch = x1 - x0, y1 - y0
    # the picture as seen from the front, about as many pixels as the cell
    fw, fh = face.size_m
    vh = max(cw, ch)
    vw = max(1, round(vh * fw / fh))
    view = fit_image(Image.open(image_path), (vw, vh), fit, background)
    # cell pixel (x, y) -> uv -> (right, up) -> view pixel (X, Y)
    (a, b), (c, d) = face.m
    tr, tu = face.t
    sx, sy = vw / fw, vh / fh
    # u = (x + x0 + 0.5) / tw, v = 1 - (y + y0 + 0.5) / th
    u_c, v_c = (x0 + 0.5) / tw, 1 - (y0 + 0.5) / th
    coeffs = (
        sx * a / tw, -sx * b / th, sx * (a * u_c + b * v_c + tr),
        -sy * c / tw, sy * d / th, sy * (fh - (c * u_c + d * v_c + tu)),
    )
    cell = view.transform((cw, ch), Image.AFFINE, coeffs, resample=Image.BICUBIC, fillcolor=_hex_rgb(background))
    atlas.paste(cell, (x0, y0))
    out.parent.mkdir(parents=True, exist_ok=True)
    atlas.save(out, optimize=True)
    return face


def retarget_textures(material: Usd.Prim, original: str, new: str, other: str | None = None) -> list[str]:
    """Point every texture input of the material's shaders that uses `original` (file name) at `new`.
    other: only the emissive ones (emissive_inputs) get `new`, the rest (base color ...) get `other`.
    Returns the shader inputs changed (prim path.input)."""
    name = Path(original).name
    lit = emissive_inputs(material) if other is not None else set()
    changed = []
    for p in Usd.PrimRange(material):
        if not p.IsA(UsdShade.Shader):
            continue
        for i in UsdShade.Shader(p).GetInputs():
            if i.GetTypeName() != Sdf.ValueTypeNames.Asset:
                continue
            v = i.Get()
            if v is not None and Path(v.path).name == name:
                key = f"{p.GetPath()}.{i.GetBaseName()}"
                i.Set(Sdf.AssetPath(new if other is None or key in lit else other))
                changed.append(key)
    return changed


# a material that lights up a texture is a screen: MDL / OmniPBR, OpenPBR, MaterialX standard_surface
EMISSIVE_TEXTURES = ("emissive_color_texture", "emissive_texture", "emission_color_texture")
EMISSIVE_COLORS = ("emissiveColor", "emission_color", "emissive_color", "emission")  # connected to an image node
EMISSION_ON = "enable_emission"
EMISSION_AMOUNT = ("emissive_intensity", "emission_luminance", "emission_weight", "emission_intensity")


def _local_file(attr_value) -> str | None:
    """The local file of an asset-valued input (resolved against its layer), or None."""
    if attr_value is None:
        return None
    path = getattr(attr_value, "resolvedPath", "") or ""
    return path if path and Path(path).is_file() else None


def _is_zero(v) -> bool:
    """An emission amount of 0 (a token, a string or a vector is not a number: not 0)."""
    return isinstance(v, (int, float)) and not isinstance(v, bool) and float(v) == 0.0


def _emissive_texture(material: Usd.Prim) -> str | None:
    """The texture a material emits, if it emits one: an emissive texture input of a shader whose emission is on
    (enable_emission true where the shader has it, intensity not 0), or an emission color fed by an image node."""
    for p in Usd.PrimRange(material):
        if not p.IsA(UsdShade.Shader):
            continue
        sh = UsdShade.Shader(p)
        ins = {i.GetBaseName(): i for i in sh.GetInputs()}
        # an MDL (OmniPBR) emits only with enable_emission true: its default is false
        mdl = sh.GetImplementationSource() == UsdShade.Tokens.sourceAsset
        if (EMISSION_ON in ins or mdl) and (EMISSION_ON not in ins or ins[EMISSION_ON].Get() is not True):
            continue
        if any(_is_zero(ins[k].Get()) for k in EMISSION_AMOUNT if k in ins):
            continue
        for k in EMISSIVE_TEXTURES:
            if k in ins and (f := _local_file(ins[k].Get())):
                return f
        for k in EMISSIVE_COLORS:
            i = ins.get(k)
            if i is None or not i.HasConnectedSource():
                continue
            src = i.GetConnectedSource()[0]
            node = UsdShade.Shader(src.GetPrim())
            for name in ("file", "texture", "filename"):
                fi = node.GetInput(name)
                if fi and (f := _local_file(fi.Get())):
                    return f
    return None


def emissive_inputs(material: Usd.Prim) -> set[str]:
    """The texture inputs (prim path.input) of the material that feed its emission: an emissive texture input, or the
    file of an image node connected to an emission color. An image node that also feeds the base color counts
    (it is one input: switched off, the screen's base color goes black with it)."""
    out = set()
    for p in Usd.PrimRange(material):
        if not p.IsA(UsdShade.Shader):
            continue
        ins = {i.GetBaseName(): i for i in UsdShade.Shader(p).GetInputs()}
        out |= {f"{p.GetPath()}.{k}" for k in EMISSIVE_TEXTURES if k in ins}
        for k in EMISSIVE_COLORS:
            i = ins.get(k)
            if i is not None and i.HasConnectedSource():
                src = i.GetConnectedSource()[0].GetPrim()
                out |= {f"{src.GetPath()}.{n}" for n in ("file", "texture", "filename")
                        if UsdShade.Shader(src).GetInput(n)}
    return out


def _emits(sh: UsdShade.Shader, ins: dict) -> bool:
    """The shader's emission is on: enable_emission true where it has it (an MDL's default is false), no amount 0,
    and something to emit (a texture, a connected or non-black emission color)."""
    mdl = sh.GetImplementationSource() == UsdShade.Tokens.sourceAsset
    if (EMISSION_ON in ins or mdl) and (EMISSION_ON not in ins or ins[EMISSION_ON].Get() is not True):
        return False
    if any(_is_zero(ins[k].Get()) for k in EMISSION_AMOUNT if k in ins):
        return False
    if any(k in ins and getattr(ins[k].Get(), "path", "") for k in EMISSIVE_TEXTURES):
        return True
    for k in EMISSIVE_COLORS:
        i = ins.get(k)
        if i is not None and (i.HasConnectedSource() or _bright(i.Get())):
            return True
    return False


def _bright(v) -> bool:
    """A color (or a number) above 0."""
    try:
        return any(float(x) > 0 for x in v) if hasattr(v, "__len__") else float(v) > 0
    except (TypeError, ValueError):
        return False


def lights_off(root: Usd.Prim) -> list[str]:
    """Switch off every material under root that emits light (seen in Isaac Sim: the LG monitor's purple glow is a
    constant emission, `deco_lighting_on`, behind the panel; its lit texture `on_1` is 8 small faces): enable_emission
    false, the amounts 0, an unconnected emission color black. A monitor switched off has no light left.
    Returns the names of the materials switched off."""
    out = []
    for p in Usd.PrimRange(root):
        if not p.IsA(UsdShade.Material) or p.IsInstanceProxy():
            continue
        hit = False
        for q in Usd.PrimRange(p):
            if not q.IsA(UsdShade.Shader):
                continue
            sh = UsdShade.Shader(q)
            ins = {i.GetBaseName(): i for i in sh.GetInputs()}
            if not _emits(sh, ins):
                continue
            hit = True
            if EMISSION_ON in ins or sh.GetImplementationSource() == UsdShade.Tokens.sourceAsset:
                sh.CreateInput(EMISSION_ON, Sdf.ValueTypeNames.Bool).Set(False)
            for k in EMISSION_AMOUNT:
                if k in ins:
                    ins[k].Set(0.0)
            for k in EMISSIVE_COLORS:  # a connected one (an image node) is cut off: nothing to multiply by
                i = ins.get(k)
                if i is None:
                    continue
                if i.HasConnectedSource():  # an empty list over the asset's (DisconnectSource edits only this layer)
                    i.GetAttr().SetConnections([])
                if i.GetTypeName() == Sdf.ValueTypeNames.Color3f or i.GetTypeName() == Sdf.ValueTypeNames.Float3:
                    i.Set(Gf.Vec3f(0, 0, 0))
                elif _bright(i.Get()):
                    i.Set(0.0)
        if hit:
            out.append(p.GetName())
    return out


def black_faces(atlas_path: Path, sides: list[ScreenFace], out: Path) -> None:
    """A copy of the atlas with the screen's faces (every side's UV box) black: the base color of a screen switched
    off; what else the atlas holds (a bezel, a logo) keeps its color."""
    from PIL import Image

    atlas = Image.open(atlas_path).convert("RGB")
    w, h = atlas.size
    for f in sides:
        atlas.paste((0, 0, 0), (int(f.uv_min[0] * w), int((1 - f.uv_max[1]) * h),
                                int(f.uv_max[0] * w + 0.999), int((1 - f.uv_min[1]) * h + 0.999)))
    out.parent.mkdir(parents=True, exist_ok=True)
    atlas.save(out, optimize=True)


def detect_screens(root: Usd.Prim) -> list[dict[str, Any]]:
    """Screens of an asset that declares none in asset.json (a TV from SimReady Central): its materials that
    emit a texture, as AssetRecord.screens entries (`detected`: found, not declared). The texture must be a local
    file (a Hugging Face asset is fetched first, like a tint)."""
    out = []
    for p in Usd.PrimRange(root, Usd.TraverseInstanceProxies()):
        if p.IsA(UsdShade.Material) and (tex := _emissive_texture(p)):
            out.append({"kind": "screen", "material": p.GetName(), "image": tex, "detected": True})
    return out


def printed_screens(root: Usd.Prim) -> list[dict[str, Any]]:
    """Screens that do not light up but show a picture (seen in Isaac Sim: NVIDIA's office monitor
    `sm_computer_monitor_office_a01` has a login screen, purple to blue, as the base color of `..._decal_screen_a`
    (`t_officemonitor_a01_screen_emissive_albedo.png`, emissive_intensity 0: it renders purple).
    A material with "screen" in its name or in a texture's file name, and a local texture: a screen to switch off
    (black), not one to show an image on (set_screen's image needs a lit one, detect_screens)."""
    out = []
    for p in Usd.PrimRange(root, Usd.TraverseInstanceProxies()):
        if not p.IsA(UsdShade.Material) or "physics" in p.GetName().lower():
            continue
        named = "screen" in p.GetName().lower()
        for q in Usd.PrimRange(p, Usd.TraverseInstanceProxies()):
            if not q.IsA(UsdShade.Shader):
                continue
            files = [f for i in UsdShade.Shader(q).GetInputs()
                     if i.GetTypeName() == Sdf.ValueTypeNames.Asset and (f := _local_file(i.Get()))]
            tex = next((f for f in files if named or "screen" in Path(f).name.lower()), None)
            if tex:
                out.append({"kind": "screen", "material": p.GetName(), "image": tex, "detected": True,
                            "printed": True})
                break
    return out


def uninstance(root: Usd.Prim) -> int:
    """Make the instanced prims under root editable (instanceable false): a material inside an instance is a
    proxy that takes no override (NVIDIA's SimReady assets are instanced). Returns how many were switched."""
    n, todo = 0, [root]
    while todo:
        p = todo.pop()
        if p.IsInstance():
            p.SetInstanceable(False)
            n += 1
        todo.extend(p.GetChildren())
    return n


@functools.lru_cache(maxsize=64)
def _found_in(path: str, mtime: float) -> tuple[str, ...]:
    stage = Usd.Stage.Open(path)
    if not stage:
        return (), ()
    root = stage.GetDefaultPrim() or stage.GetPseudoRoot()  # what a reference brings in, like compose (review)
    return tuple(s["material"] for s in detect_screens(root)), tuple(s["material"] for s in printed_screens(root))


def found_screens(asset, printed: bool = False) -> list[str] | None:
    """The screen materials compose would find on an asset that declares none: [] = none, so set_screen
    has nothing to draw on; None = not known before compose (a Hugging Face asset, fetched only then). Cached by
    the file's mtime: a texture added later is seen after the USD file changes (or a restart).
    printed: also the screens that show a picture without light (printed_screens: what "off" blackens)."""
    from agentic_sim_world_gen.assets.localize import parse_hf_url
    from agentic_sim_world_gen.assets.providers.isaac_index import rebase_uri

    uri = rebase_uri(asset) or asset.uri or ""
    if parse_hf_url(uri):
        return None
    if "://" in uri or not Path(uri).is_file():
        return []
    try:
        lit, shown = _found_in(str(Path(uri).resolve()), Path(uri).stat().st_mtime)
        return list(lit) + (list(shown) if printed else [])
    except Exception:  # an unreadable file shows no screen
        return []


def black_image(scene_dir: Path) -> Path:
    """An all-black image (scene dir `screens/off.png`) for a screen switched off: painted over the screen's
    face, it neither shows a picture nor lights up (the emissive texture is black too)."""
    from PIL import Image

    out = scene_dir / "screens" / "off.png"
    if not out.is_file():
        out.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (16, 16), (0, 0, 0)).save(out)
    return out


def apply_screen(stage: Usd.Stage, prim_paths: list[str], asset, o, scene_dir: Path,
                 whole: bool = False) -> list[dict[str, Any]]:
    """Render the screen textures for object o once and point every unit's (prim_paths) screen materials at them.
    The reference is the `Asset` child of each path. Returns what was done per screen (for customData).
    whole: a found screen switched off: the image (black) replaces the emissive texture inputs as it is,
    nothing drawn into a copy (the lit material of an LG monitor is 8 faces in 4 slightly turned groups, each on half
    of the texture, so painting the largest face black left the rest of the screen purple); the material's other
    inputs on that texture (base color) get a copy with every screen face black (black_faces)."""
    from agentic_sim_world_gen.usd.compose import ASSET_CHILD

    spec = o.screen
    screens = {s["material"]: s for s in asset.screens}
    done = []
    for mat_name, img in spec.images(list(screens)).items():
        s = screens.get(mat_name)
        if s is None:
            continue  # reported by validate (bad_screen)
        img_path = Path(img).expanduser()
        if not img_path.is_absolute():
            img_path = scene_dir / img_path
        if not img_path.exists():
            continue  # reported by validate
        first = stage.GetPrimAtPath(f"{prim_paths[0]}/{ASSET_CHILD}")
        mat = find_material(first, mat_name) if first else None
        if mat is None or mat.IsInstanceProxy():
            done.append({"material": mat_name, "error": "material not found" if mat is None else "instanceable asset"})
            continue
        sides = screen_faces(stage, first, mat)
        if not sides:
            done.append({"material": mat_name, "error": "no mesh with this material"})
            continue
        face, base = None, None
        if whole:
            tex = img_path.resolve()
            base = (scene_dir / f"screens/{o.id}_{mat_name}_off.png").resolve()
            black_faces(Path(s["image"]), sides, base)
        else:
            # absolute like the room's textures: RTX's MDL reader resolves a relative path against the asset's layer
            tex = (scene_dir / f"screens/{o.id}_{mat_name}.png").resolve()
            face = render_texture(Path(s["image"]), img_path, sides, spec.fit, spec.background, tex)
        inputs = []
        for path in prim_paths:
            ref = stage.GetPrimAtPath(f"{path}/{ASSET_CHILD}")
            m = find_material(ref, mat_name) if ref else None
            if m is not None and not m.IsInstanceProxy():
                inputs += retarget_textures(m, s["image"], str(tex), str(base) if base else None)
        done.append({"material": mat_name, "image": str(img_path), "texture": str(tex), "inputs": len(inputs),
                     **({"base_texture": str(base)} if base else {}),
                     **({"size_m": [round(x, 3) for x in face.size_m]} if face else {})})
    return done
