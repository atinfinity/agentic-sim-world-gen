"""Room surfaces (materials + physics friction) and lighting presets for the USD stage.

Presets live in agentic_sim_world_gen.scene.looks. Every material has a UsdPreviewSurface (works everywhere);
an `mdl` surface adds an MDL output for Omniverse RTX. Friction is a UsdPhysics material bound with
the "physics" purpose. Room boxes are meshes with UVs in meters (st = position / tile_m), so
textures tile at their real size. An MDL shader gets the parameter overrides of
agentic_sim_world_gen.materials.TUNING (so patterned vMaterials read at room scale) and then the surface's mdl_inputs.
"""

from __future__ import annotations

import math
import shutil
from pathlib import Path
from typing import Any

from pxr import Gf, Sdf, Usd, UsdGeom, UsdLux, UsdPhysics, UsdShade

from agentic_sim_world_gen import pbr
from agentic_sim_world_gen.layout.room import RoomGeometry
from agentic_sim_world_gen.materials import tuning
from agentic_sim_world_gen.scene.looks import BUILTIN, LIGHTING, builtin_texture, resolve_surface, sky_url
from agentic_sim_world_gen.scene.site import ccw, is_convex, triangulate

# (axis of the normal, sign) -> the two in-plane axes used for UVs
_FACES = [((0, -1), (1, 2)), ((0, 1), (1, 2)), ((1, -1), (0, 2)), ((1, 1), (0, 2)), ((2, -1), (0, 1)), ((2, 1), (0, 1))]


def _rot(yaw_deg: float) -> tuple[float, float]:
    """cos/sin with exact values at multiples of 90 deg (keeps rectangular rooms axis-aligned)."""
    q = yaw_deg / 90.0
    if abs(q - round(q)) < 1e-9:
        return [(1.0, 0.0), (0.0, 1.0), (-1.0, 0.0), (0.0, -1.0)][round(q) % 4]
    r = math.radians(yaw_deg)
    return math.cos(r), math.sin(r)


def box_mesh(stage: Usd.Stage, path: str, center, size, tile_m: float = 1.0, yaw_deg: float = 0.0,
             approx: str | None = None) -> UsdGeom.Mesh:
    """Box as a mesh (world coordinates baked in), size along its own axes, turned by yaw about Z.
    UVs are in meters / tile_m in the box's own frame (= world frame when yaw is 0)."""
    c, h = Gf.Vec3d(*center), Gf.Vec3d(*size) / 2
    cs, sn = _rot(yaw_deg)
    lc = (c[0] * cs + c[1] * sn, -c[0] * sn + c[1] * cs, c[2])  # center in the box frame (for UVs)

    def world(p):
        return (c[0] + p[0] * cs - p[1] * sn, c[1] + p[0] * sn + p[1] * cs, c[2] + p[2])

    points, counts, indices, st, normals = [], [], [], [], []
    for (axis, sign), (u, v) in _FACES:
        quad = []
        for du, dv in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
            p = [0.0, 0.0, 0.0]
            p[axis], p[u], p[v] = sign * h[axis], du * h[u], dv * h[v]
            quad.append(p)
        n = [0.0, 0.0, 0.0]
        n[axis] = float(sign)
        # counter-clockwise seen from outside: e_u x e_v is +X, -Y, +Z for the (u, v) pairs above
        if sign * (-1 if axis == 1 else 1) < 0:
            quad.reverse()
        base = len(points)
        points += [Gf.Vec3f(*world(p)) for p in quad]
        counts.append(4)
        indices += [base, base + 1, base + 2, base + 3]
        st += [Gf.Vec2f((lc[u] + q[u]) / tile_m, (lc[v] + q[v]) / tile_m) for q in quad]
        normals += [Gf.Vec3f(n[0] * cs - n[1] * sn, n[0] * sn + n[1] * cs, n[2])] * 4
    mesh = UsdGeom.Mesh.Define(stage, path)
    mesh.CreatePointsAttr(points)
    mesh.CreateFaceVertexCountsAttr(counts)
    mesh.CreateFaceVertexIndicesAttr(indices)
    mesh.CreateNormalsAttr(normals)
    mesh.SetNormalsInterpolation(UsdGeom.Tokens.faceVarying)
    mesh.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
    lo = Gf.Vec3f(*(min(p[i] for p in points) for i in range(3)))
    hi = Gf.Vec3f(*(max(p[i] for p in points) for i in range(3)))
    mesh.CreateExtentAttr([lo, hi])
    UsdGeom.PrimvarsAPI(mesh).CreatePrimvar("st", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.faceVarying).Set(st)
    UsdPhysics.CollisionAPI.Apply(mesh.GetPrim())
    UsdPhysics.MeshCollisionAPI.Apply(mesh.GetPrim()).CreateApproximationAttr(approx or UsdPhysics.Tokens.convexHull)
    return mesh


def prism_mesh(stage: Usd.Stage, path: str, poly, z0: float, z1: float, tile_m: float = 1.0,
               approx: str | None = None) -> UsdGeom.Mesh:
    """Polygon footprint extruded from z0 to z1 (world coordinates), caps ear-clipped into triangles.
    UVs: caps x, y / tile_m; sides distance along the outline, z / tile_m. A static collider
    (convex hull if the outline is convex, else the triangle mesh; `approx` overrides)."""
    poly = ccw([tuple(p) for p in poly])
    n = len(poly)
    tris = triangulate(poly)
    points, counts, indices, st, normals = [], [], [], [], []

    def add(face, uv, nrm):
        base = len(points)
        points.extend(Gf.Vec3f(*p) for p in face)
        counts.append(len(face))
        indices.extend(range(base, base + len(face)))
        st.extend(Gf.Vec2f(u / tile_m, v / tile_m) for u, v in uv)
        normals.extend([Gf.Vec3f(*nrm)] * len(face))

    for i, j, k in tris:  # top (counter-clockwise seen from above) and bottom (reversed)
        top = [(*poly[m], z1) for m in (i, j, k)]
        add(top, [p[:2] for p in top], (0, 0, 1))
        bot = [(*poly[m], z0) for m in (k, j, i)]
        add(bot, [p[:2] for p in bot], (0, 0, -1))
    u = 0.0
    for i in range(n):
        (x0, y0), (x1, y1) = poly[i], poly[(i + 1) % n]
        L = math.dist((x0, y0), (x1, y1))
        if L < 1e-9:
            continue
        nrm = ((y1 - y0) / L, -(x1 - x0) / L, 0.0)  # outward for a counter-clockwise outline
        add([(x0, y0, z0), (x1, y1, z0), (x1, y1, z1), (x0, y0, z1)], [(u, z0), (u + L, z0), (u + L, z1), (u, z1)], nrm)
        u += L
    mesh = UsdGeom.Mesh.Define(stage, path)
    mesh.CreatePointsAttr(points)
    mesh.CreateFaceVertexCountsAttr(counts)
    mesh.CreateFaceVertexIndicesAttr(indices)
    mesh.CreateNormalsAttr(normals)
    mesh.SetNormalsInterpolation(UsdGeom.Tokens.faceVarying)
    mesh.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
    mesh.CreateExtentAttr([Gf.Vec3f(min(xs), min(ys), z0), Gf.Vec3f(max(xs), max(ys), z1)])
    UsdGeom.PrimvarsAPI(mesh).CreatePrimvar("st", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.faceVarying).Set(st)
    UsdPhysics.CollisionAPI.Apply(mesh.GetPrim())
    approx = approx or (UsdPhysics.Tokens.convexHull if is_convex(poly) else UsdPhysics.Tokens.none)
    UsdPhysics.MeshCollisionAPI.Apply(mesh.GetPrim()).CreateApproximationAttr(approx)
    return mesh


def _resolve_tex(path: str, base_dir: Path | None) -> str:
    """Absolute path or URL. A built-in texture, or a CC0 PBR map from the cache ("pbr:", agentic_sim_world_gen.pbr; fetched
    again if the cache was cleared), is copied to <base_dir>/textures/ (kept with the scene)."""
    if path.startswith((BUILTIN, pbr.REF)):
        if path.startswith(BUILTIN):
            src = builtin_texture(path)
            name = src.name
        else:
            src = pbr.resolve_ref(path)
            lib, mat, file = pbr.parse_ref(path)  # type: ignore[misc]
            name = f"{lib}_{mat}_{file}"
        if base_dir is None:
            return str(src)
        dst = base_dir / "textures" / name
        if not dst.exists() or dst.stat().st_size != src.stat().st_size:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
        return str(dst.resolve())
    if "://" in path or base_dir is None:
        return path
    p = Path(path).expanduser()
    return str(p if p.is_absolute() else (base_dir / p).resolve())


def _mdl_value(v) -> tuple[Sdf.ValueTypeName, Any]:
    """USD type and value of an MDL parameter override: bool, int, float, float2 (2 numbers), color (3)."""
    if isinstance(v, bool):
        return Sdf.ValueTypeNames.Bool, v
    if isinstance(v, int):
        return Sdf.ValueTypeNames.Int, v
    if isinstance(v, float):
        return Sdf.ValueTypeNames.Float, v
    v = [float(x) for x in v]
    if len(v) == 2:
        return Sdf.ValueTypeNames.Float2, Gf.Vec2f(*v)
    if len(v) == 3:
        return Sdf.ValueTypeNames.Color3f, Gf.Vec3f(*v)
    raise ValueError(f"MDL input value {v!r}: give a number, 2 numbers (float2) or 3 (color)")


def make_material(stage: Usd.Stage, path: str, s: dict[str, Any], base_dir: Path | None = None,
                  glow: float = 0.0, tint=(1.0, 1.0, 1.0)) -> UsdShade.Material:
    """UsdPreviewSurface (+ texture, + MDL) and a physics material with the surface's friction. `glow` > 0 makes it
    emit its own color times glow times tint (the light's color; a ceiling under downward fixtures; the MDL output does not glow)."""
    mat = UsdShade.Material.Define(stage, path)
    ps = UsdShade.Shader.Define(stage, f"{path}/PreviewSurface")
    ps.CreateIdAttr("UsdPreviewSurface")
    ps.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(float(s["roughness"]))
    ps.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(float(s["metallic"]))
    diffuse = ps.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f)
    diffuse.Set(Gf.Vec3f(*s["color"]))
    reader = None

    def texture(name: str, file: str, color_space: str) -> UsdShade.Shader:
        nonlocal reader
        if reader is None:
            reader = UsdShade.Shader.Define(stage, f"{path}/StReader")
            reader.CreateIdAttr("UsdPrimvarReader_float2")
            reader.CreateInput("varname", Sdf.ValueTypeNames.Token).Set("st")
        tex = UsdShade.Shader.Define(stage, f"{path}/{name}")
        tex.CreateIdAttr("UsdUVTexture")
        tex.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(_resolve_tex(file, base_dir))
        tex.CreateInput("sourceColorSpace", Sdf.ValueTypeNames.Token).Set(color_space)
        tex.CreateInput("wrapS", Sdf.ValueTypeNames.Token).Set("repeat")
        tex.CreateInput("wrapT", Sdf.ValueTypeNames.Token).Set("repeat")
        tex.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(reader.ConnectableAPI(), "result")
        return tex

    if s.get("texture"):
        diffuse.ConnectToSource(texture("DiffuseTexture", s["texture"], "sRGB").ConnectableAPI(), "rgb")
    if s.get("normal"):
        nt = texture("NormalTexture", s["normal"], "raw")
        nt.CreateInput("scale", Sdf.ValueTypeNames.Float4).Set(Gf.Vec4f(2, 2, 2, 1))
        nt.CreateInput("bias", Sdf.ValueTypeNames.Float4).Set(Gf.Vec4f(-1, -1, -1, 0))
        nt.CreateOutput("rgb", Sdf.ValueTypeNames.Float3)
        ps.CreateInput("normal", Sdf.ValueTypeNames.Normal3f).ConnectToSource(nt.ConnectableAPI(), "rgb")
    if s.get("roughness_texture"):
        rt = texture("RoughnessTexture", s["roughness_texture"], "raw")
        rt.CreateOutput("r", Sdf.ValueTypeNames.Float)
        ps.GetInput("roughness").ConnectToSource(rt.ConnectableAPI(), "r")
    if glow > 0:
        emissive = ps.CreateInput("emissiveColor", Sdf.ValueTypeNames.Color3f)
        if s.get("texture"):
            et = texture("EmissiveTexture", s["texture"], "sRGB")
            et.CreateInput("scale", Sdf.ValueTypeNames.Float4).Set(Gf.Vec4f(*(glow * t for t in tint), 1))
            et.CreateOutput("rgb", Sdf.ValueTypeNames.Float3)
            emissive.ConnectToSource(et.ConnectableAPI(), "rgb")
        else:
            emissive.Set(Gf.Vec3f(*(c * glow * t for c, t in zip(s["color"], tint))))
        mat.GetPrim().SetCustomDataByKey("agentic_sim_world_gen:glow", glow)
    mat.CreateSurfaceOutput().ConnectToSource(ps.ConnectableAPI(), "surface")
    if s.get("mdl"):
        mdl = UsdShade.Shader.Define(stage, f"{path}/MDL")
        mdl.CreateImplementationSourceAttr(UsdShade.Tokens.sourceAsset)
        mdl.SetSourceAsset(Sdf.AssetPath(_resolve_tex(s["mdl"], base_dir)), "mdl")
        name = s.get("mdl_name") or Path(s["mdl"].split("?")[0]).stem
        mdl.SetSourceAssetSubIdentifier(name, "mdl")
        for k, v in {**tuning(s["mdl"], name), **(s.get("mdl_inputs") or {})}.items():
            t, val = _mdl_value(v)
            mdl.CreateInput(k, t).Set(val)
        mat.CreateSurfaceOutput("mdl").ConnectToSource(mdl.ConnectableAPI(), "out")
    phys = UsdPhysics.MaterialAPI.Apply(mat.GetPrim())
    phys.CreateStaticFrictionAttr(float(s["static_friction"]))
    phys.CreateDynamicFrictionAttr(float(s["dynamic_friction"]))
    phys.CreateRestitutionAttr(0.0)
    mat.GetPrim().SetCustomDataByKey("agentic_sim_world_gen:surface", s["name"])
    return mat


def bind(prim: Usd.Prim, mat: UsdShade.Material) -> None:
    api = UsdShade.MaterialBindingAPI.Apply(prim)
    api.Bind(mat)
    api.Bind(mat, UsdShade.Tokens.weakerThanDescendants, "physics")


PLAIN_DOME = 250.0  # untextured dome intensity for an outdoor preset whose sky is turned off


def _kelvin(light, k: float | None) -> None:
    if k:
        light.CreateEnableColorTemperatureAttr(True)
        light.CreateColorTemperatureAttr(float(k))


def room_sky(room) -> str | None:
    """Sky image URL of the room: Room.sky, else the lighting preset's (None = plain dome color). With
    Room.site the site's sky (Site.sky, else its outdoor lighting's)."""
    site = getattr(room, "site", None)
    if site is not None:
        return sky_url(site.sky if site.sky is not None else LIGHTING[site.lighting].get("sky"))
    return sky_url(room.sky if room.sky is not None else LIGHTING[room.lighting].get("sky"))


SITE_LIGHT = 0.5  # a room's site: its sky and sun at half the outdoor preset, else the sidewalk and road
# burned out next to a room lit by its fixtures (Isaac Sim 6.1: paving 231 → 207, asphalt 143 → 95 of 255)


def build_lights(stage: Usd.Stage, room, root: str = "/World/Lights", base_dir: Path | None = None) -> list[str]:
    """Dome (with the sky image), sun and fixtures of the room's lighting preset. Returns the light prim paths.
    With Room.site the dome, sky and sun are the site's outdoor lighting at SITE_LIGHT (not scaled by the
    room's lighting_scale, nor tinted by its light_kelvin) and the room keeps only its fixtures."""
    p = LIGHTING[room.lighting]
    k = room.lighting_scale
    prim = UsdGeom.Xform.Define(stage, root).GetPrim()
    prim.SetCustomDataByKey("agentic_sim_world_gen:lighting", room.lighting)
    out = []
    sky = room_sky(room)
    site = getattr(room, "site", None)
    if site is not None:
        prim.SetCustomDataByKey("agentic_sim_world_gen:site_lighting", site.lighting)
        sp = LIGHTING[site.lighting]
        dome = UsdLux.DomeLight.Define(stage, f"{root}/Dome")
        if sky:
            dome.CreateTextureFileAttr(Sdf.AssetPath(_resolve_tex(sky, base_dir)))
            dome.CreateTextureFormatAttr(UsdLux.Tokens.latlong)
            dome.GetPrim().SetCustomDataByKey("agentic_sim_world_gen:sky", site.sky or sp.get("sky", ""))
        dome.CreateIntensityAttr((sp["dome"] if sky else PLAIN_DOME) * SITE_LIGHT)
        out.append(str(dome.GetPath()))
        if sp.get("sun"):
            sun = UsdLux.DistantLight.Define(stage, f"{root}/Sun")
            sun.CreateIntensityAttr(sp["sun"] * SITE_LIGHT)
            sun.CreateAngleAttr(0.53)
            _kelvin(sun, sp.get("kelvin_sun"))
            UsdGeom.XformCommonAPI(sun).SetRotate(Gf.Vec3f(*sp.get("sun_angle", (45.0, 0.0, 30.0))))
            out.append(str(sun.GetPath()))
        p = {k2: v for k2, v in p.items() if k2 not in ("dome", "sun", "sky")}  # the room's fixtures only
    if p.get("dome"):
        dome = UsdLux.DomeLight.Define(stage, f"{root}/Dome")
        if sky:
            dome.CreateTextureFileAttr(Sdf.AssetPath(_resolve_tex(sky, base_dir)))
            dome.CreateTextureFormatAttr(UsdLux.Tokens.latlong)
            dome.GetPrim().SetCustomDataByKey("agentic_sim_world_gen:sky", room.sky or p.get("sky", ""))
        # an outdoor preset's dome value scales its sky image; with sky "none" a plain dome of PLAIN_DOME
        dome.CreateIntensityAttr((p["dome"] if sky or not p.get("sky") else PLAIN_DOME) * k)
        out.append(str(dome.GetPath()))
    if p.get("sun"):
        sun = UsdLux.DistantLight.Define(stage, f"{root}/Sun")
        sun.CreateIntensityAttr(p["sun"] * k)
        sun.CreateAngleAttr(0.53)
        _kelvin(sun, room.light_kelvin or p.get("kelvin_sun"))
        UsdGeom.XformCommonAPI(sun).SetRotate(Gf.Vec3f(*p.get("sun_angle", (45.0, 0.0, 30.0))))
        out.append(str(sun.GetPath()))
    if getattr(room, "fixtures", None) is not None and not room.outdoor:
        out += build_fixtures(stage, room, root)
    elif p.get("fixture"):
        z = p["pole"] if p.get("pole") else room.height - p["drop"]
        for i, (x, y) in enumerate(RoomGeometry(room).fixture_points(p["spacing"])):
            path = f"{root}/Fixture_{i:02d}"
            if p["fixture"] == "rect":
                light = UsdLux.RectLight.Define(stage, path)
                light.CreateWidthAttr(p["size"][0])
                light.CreateHeightAttr(p["size"][1])
            elif p["fixture"] == "disk":
                light = UsdLux.DiskLight.Define(stage, path)
                light.CreateRadiusAttr(p["size"])
            else:
                light = UsdLux.SphereLight.Define(stage, path)
                light.CreateRadiusAttr(p["size"])
            light.CreateIntensityAttr(p["intensity"] * k)
            _kelvin(light, room.light_kelvin or p.get("kelvin"))
            UsdGeom.XformCommonAPI(light).SetTranslate(Gf.Vec3d(x, y, z))  # rect/disk emit toward -Z
            out.append(path)
    return out


def _frustum_mesh(stage: Usd.Stage, path: str, center, r_bottom: float, r_top: float, h: float,
                  n: int = 32) -> UsdGeom.Mesh:
    """A lamp shade: the side of a truncated cone and its top, open at the bottom (seen from both sides)."""
    cx, cy, cz = center
    z0, z1 = cz - h / 2, cz + h / 2
    pts = []
    for r, z in ((r_bottom, z0), (r_top, z1)):
        pts += [(cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n), z) for i in range(n)]
    pts.append((cx, cy, z1))
    counts, idx = [], []
    for i in range(n):
        j = (i + 1) % n
        counts.append(4)
        idx += [i, j, n + j, n + i]
        counts.append(3)
        idx += [n + i, n + j, 2 * n]
    mesh = UsdGeom.Mesh.Define(stage, path)
    mesh.CreatePointsAttr([Gf.Vec3f(*q) for q in pts])
    mesh.CreateFaceVertexCountsAttr(counts)
    mesh.CreateFaceVertexIndicesAttr(idx)
    mesh.CreateDoubleSidedAttr(True)
    mesh.CreateExtentAttr([Gf.Vec3f(cx - r_bottom, cy - r_bottom, z0), Gf.Vec3f(cx + r_bottom, cy + r_bottom, z1)])
    return mesh


def _fixture_light(stage: Usd.Stage, path: str, x: float, y: float, L: dict, up: bool) -> str:
    """A fixture's disk / rect light at (x, y, L["z"]): emits toward -Z, or +Z (the ceiling) when up."""
    if L["shape"] == "rect":
        light = UsdLux.RectLight.Define(stage, path)
        light.CreateWidthAttr(L["width"])
        light.CreateHeightAttr(L["height"])
    else:
        light = UsdLux.DiskLight.Define(stage, path)
        light.CreateRadiusAttr(L["radius"])
    light.CreateIntensityAttr(L["intensity"])
    _kelvin(light, L["kelvin"])
    api = UsdGeom.XformCommonAPI(light)
    api.SetTranslate(Gf.Vec3d(x, y, L["z"]))
    if L["yaw"] or up:
        api.SetRotate(Gf.Vec3f(180.0 if up else 0.0, 0.0, L["yaw"]))
    return path


def build_fixtures(stage: Usd.Stage, room, root: str = "/World/Lights",
                   body_root: str = "/World/Room/Fixtures") -> list[str]:
    """Room.fixtures: a light per fixture under root (Fixture_NN, like the presets' lights) and its body
    under body_root (Fixture_NN: flange / cord + shade / housing; no collider, it is above everyone's reach).
    A flush fixture's body has customData agentic_sim_world_gen:flush (the dollhouse view hides it with the ceiling)."""
    from agentic_sim_world_gen.scene.fixtures import linear_rgb, plan

    fx = plan(room)
    if not fx:
        return []
    UsdGeom.Xform.Define(stage, body_root).GetPrim().SetCustomDataByKey("agentic_sim_world_gen:fixtures", room.fixtures.kind)
    looks = {}
    out = []
    for i, f in enumerate(fx):
        L = f["light"]
        path = f"{root}/Fixture_{i:02d}"
        _fixture_light(stage, path, f["x"], f["y"], L, up=False)
        if L.get("up"):
            out.append(_fixture_light(stage, f"{path}_Up", f["x"], f["y"], L["up"], up=True))
        out.append(path)

        color = f["color"]
        if color not in looks:
            surf = {"preset": "paint_white", "color": linear_rgb(color), "roughness": 0.4}
            looks[color] = make_material(stage, f"{body_root}/Looks/Body_{len(looks)}", resolve_surface(surf, "paint_white"))
        body = UsdGeom.Xform.Define(stage, f"{body_root}/Fixture_{i:02d}").GetPrim()
        body.SetCustomDataByKey("agentic_sim_world_gen:fixture", f["kind"])
        if f["flush"]:
            body.SetCustomDataByKey("agentic_sim_world_gen:flush", True)
        for j, part in enumerate(f["parts"]):
            pp = f"{body_root}/Fixture_{i:02d}/Part_{j}"
            if part["shape"] == "box":
                prim = box_mesh(stage, pp, part["center"], part["size"], 1.0, part.get("yaw", 0.0)).GetPrim()
            elif part["shape"] == "shade":
                prim = _frustum_mesh(stage, pp, part["center"], part["radius"], part["top_radius"], part["height"]).GetPrim()
            else:
                cyl = UsdGeom.Cylinder.Define(stage, pp)
                cyl.CreateRadiusAttr(part["radius"])
                cyl.CreateHeightAttr(part["height"])
                cyl.CreateAxisAttr(UsdGeom.Tokens.z)
                r, h = part["radius"], part["height"]
                cyl.CreateExtentAttr([Gf.Vec3f(-r, -r, -h / 2), Gf.Vec3f(r, r, h / 2)])
                UsdGeom.XformCommonAPI(cyl).SetTranslate(Gf.Vec3d(*part["center"]))
                prim = cyl.GetPrim()
            UsdShade.MaterialBindingAPI.Apply(prim).Bind(looks[color])
    return out
