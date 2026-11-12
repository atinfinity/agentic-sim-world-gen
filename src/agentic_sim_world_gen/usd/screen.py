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


def _faces(mesh: UsdGeom.Mesh, xf: Gf.Matrix4d) -> list[tuple[Gf.Vec3d, float, list[Gf.Vec3d], list[Gf.Vec2f]]]:
    """(unit normal, area, world points, uvs) of each face."""
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
    for n in counts:
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
        bound = UsdShade.MaterialBindingAPI(p).ComputeBoundMaterial()[0]
        if not bound or bound.GetPath() != mat_path:
            continue
        faces += _faces(UsdGeom.Mesh(p), cache.GetLocalToWorldTransform(p))
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


def retarget_textures(material: Usd.Prim, original: str, new: str) -> list[str]:
    """Point every texture input of the material's shaders that uses `original` (file name) at `new`.
    Returns the shader inputs changed (prim path.input)."""
    name = Path(original).name
    changed = []
    for p in Usd.PrimRange(material):
        if not p.IsA(UsdShade.Shader):
            continue
        for i in UsdShade.Shader(p).GetInputs():
            if i.GetTypeName() != Sdf.ValueTypeNames.Asset:
                continue
            v = i.Get()
            if v is not None and Path(v.path).name == name:
                i.Set(Sdf.AssetPath(new))
                changed.append(f"{p.GetPath()}.{i.GetBaseName()}")
    return changed


def apply_screen(stage: Usd.Stage, prim_paths: list[str], asset, o, scene_dir: Path) -> list[dict[str, Any]]:
    """Render the screen textures for object o once and point every unit's (prim_paths) screen materials at them.
    The reference is the `Asset` child of each path. Returns what was done per screen (for customData)."""
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
        # absolute like the room's textures: RTX's MDL reader resolves a relative path against the asset's layer
        tex = (scene_dir / f"screens/{o.id}_{mat_name}.png").resolve()
        face = render_texture(Path(s["image"]), img_path, sides, spec.fit, spec.background, tex)
        inputs = []
        for path in prim_paths:
            ref = stage.GetPrimAtPath(f"{path}/{ASSET_CHILD}")
            m = find_material(ref, mat_name) if ref else None
            if m is not None and not m.IsInstanceProxy():
                inputs += retarget_textures(m, s["image"], str(tex))
        done.append({"material": mat_name, "image": str(img_path), "texture": str(tex), "inputs": len(inputs),
                     "size_m": [round(x, 3) for x in face.size_m]})
    return done
