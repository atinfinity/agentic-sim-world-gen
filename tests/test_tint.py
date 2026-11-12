"""Repainting an asset's materials (ObjectSpec.tint): offline, with a tiny rack made with usd-core."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("pxr")
from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade  # noqa: E402

from agentic_sim_world_gen import service  # noqa: E402
from agentic_sim_world_gen.assets.providers.local_folder import build_index  # noqa: E402
from agentic_sim_world_gen.scene.spec import ObjectSpec  # noqa: E402
from agentic_sim_world_gen.usd.tint import linear  # noqa: E402

WHITE = Gf.Vec3f(0.9, 0.9, 0.9)


def _material(stage, path, color, metallic=0.0, opacity=1.0, texture=None):
    """UsdPreviewSurface + OmniPBR (or OmniGlass) + OpenPBR, like many local assets."""
    m = UsdShade.Material.Define(stage, path)
    ps = UsdShade.Shader.Define(stage, f"{path}/Preview")
    ps.CreateIdAttr("UsdPreviewSurface")
    if texture:
        tex = UsdShade.Shader.Define(stage, f"{path}/Tex")
        tex.CreateIdAttr("UsdUVTexture")
        tex.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(texture)
        ps.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(
            tex.ConnectableAPI(), "rgb")
    else:
        ps.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(color)
    ps.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(metallic)
    ps.CreateInput("opacity", Sdf.ValueTypeNames.Float).Set(opacity)
    mdl = UsdShade.Shader.Define(stage, f"{path}/MDL")
    mdl.SetSourceAsset("OmniGlass.mdl" if opacity < 1 else "OmniPBR.mdl", "mdl")
    if opacity < 1:
        mdl.CreateInput("glass_color", Sdf.ValueTypeNames.Color3f).Set(color)
    elif texture:
        mdl.CreateInput("diffuse_texture", Sdf.ValueTypeNames.Asset).Set(texture)
    else:
        mdl.CreateInput("diffuse_color_constant", Sdf.ValueTypeNames.Color3f).Set(color)
        mdl.CreateInput("metallic_constant", Sdf.ValueTypeNames.Float).Set(metallic)
    op = UsdShade.Shader.Define(stage, f"{path}/OpenPBR")
    op.CreateIdAttr("ND_open_pbr_surface_surfaceshader")
    if color is not None:
        op.CreateInput("base_color", Sdf.ValueTypeNames.Color3f).Set(color)
    return m


def _box(stage, path, size, at, material):
    mesh = UsdGeom.Mesh.Define(stage, path)
    sx, sy, sz = size
    x, y, z = at
    pts = [(x + i * sx, y + j * sy, z + k * sz) for k in (0, 1) for j in (0, 1) for i in (0, 1)]
    mesh.CreatePointsAttr(pts)
    mesh.CreateFaceVertexCountsAttr([4] * 6)
    mesh.CreateFaceVertexIndicesAttr([0, 1, 3, 2, 4, 6, 7, 5, 0, 4, 5, 1, 2, 3, 7, 6, 0, 2, 6, 4, 1, 5, 7, 3])
    mesh.CreateExtentAttr([(x, y, z), (x + sx, y + sy, z + sz)])
    UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(material)


def make_rack(folder: Path) -> Path:
    """A white rack: two trays (largest, white), a frame (white too, instanced), a chrome pole, a big glass
    front, a dark base and a printed label (textured)."""
    folder.mkdir(parents=True, exist_ok=True)
    stage = Usd.Stage.CreateNew(str(folder / "rack.usda"))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    root = UsdGeom.Xform.Define(stage, "/rack").GetPrim()
    stage.SetDefaultPrim(root)
    mats = {
        "Tray0Mat": _material(stage, "/rack/_materials/Tray0Mat", WHITE),
        "Tray1Mat": _material(stage, "/rack/_materials/Tray1Mat", WHITE),
        "FrameMat": _material(stage, "/rack/_materials/FrameMat", WHITE),
        "PoleMat": _material(stage, "/rack/_materials/PoleMat", Gf.Vec3f(0.6, 0.6, 0.6), metallic=1.0),
        "GlassMat": _material(stage, "/rack/_materials/GlassMat", WHITE, opacity=0.2),
        "BaseMat": _material(stage, "/rack/_materials/BaseMat", Gf.Vec3f(0.05, 0.05, 0.05)),
        "LabelMat": _material(stage, "/rack/_materials/LabelMat", None, texture="label.png"),
    }
    _box(stage, "/rack/Tray0", (0.6, 0.4, 0.02), (0, 0, 0.5), mats["Tray0Mat"])
    _box(stage, "/rack/Tray1", (0.6, 0.4, 0.02), (0, 0, 1.0), mats["Tray1Mat"])
    _box(stage, "/rack/Pole", (0.03, 0.03, 1.4), (0.3, 0.2, 0), mats["PoleMat"])
    _box(stage, "/rack/Glass", (0.6, 0.01, 1.4), (0, -0.01, 0), mats["GlassMat"])
    _box(stage, "/rack/Base", (0.4, 0.3, 0.05), (0.1, 0.05, 0), mats["BaseMat"])
    _box(stage, "/rack/Label", (0.2, 0.01, 0.05), (0.2, -0.02, 1.3), mats["LabelMat"])
    frame = UsdGeom.Xform.Define(stage, "/rack/Frame").GetPrim()
    _box(stage, "/rack/Frame/Bar", (0.6, 0.02, 0.02), (0, 0.38, 1.38), mats["FrameMat"])
    frame.SetInstanceable(True)
    stage.GetRootLayer().Save()
    return folder / "rack.usda"


@pytest.fixture
def scene(tmp_path):
    make_rack(tmp_path / "lib" / "rack")
    recs = build_index(tmp_path / "lib", tmp_path / "cache")
    service.set_provider(type("P", (), {"name": "f", "search": lambda self, q, limit=10, size=None: recs})())
    d = tmp_path / "s"
    service.create_scene({"name": "s", "room": {"width": 4, "depth": 3, "height": 2.6}, "objects": [
        {"id": "rack", "query": "accessory rack", "position": [1, 1.5]},
        {"id": "racks", "query": "accessory rack", "position": [3, 1.5], "array": {"count": 2, "spacing": 0.1}},
    ]}, d)
    yield d
    service.set_provider(None)


def _shader(stage, prim, mat, shader):
    return UsdShade.Shader(stage.GetPrimAtPath(f"{prim}/Asset/_materials/{mat}/{shader}"))


def test_tint_shorthand_and_check():
    o = ObjectSpec.model_validate({"id": "a", "query": "rack", "tint": "#CC2222"})
    assert [(t.color, t.materials) for t in o.tint] == [("#cc2222", None)]
    o = ObjectSpec.model_validate({"id": "a", "query": "rack", "tint": {"color": "#000000", "materials": ["Pole*"]}})
    assert o.tint[0].materials == ["Pole*"]
    with pytest.raises(ValueError):
        ObjectSpec.model_validate({"id": "a", "query": "rack", "tint": "red"})


def test_main_color_is_repainted_on_every_shader_and_unit(scene):
    st = service.set_tint(scene, "rack", "#cc2222")
    assert st["issues"] == []
    service.set_tint(scene, "racks", ["#cc2222", {"color": "#ffd700", "materials": ["Label*"]}])
    service.compose_scene(scene)
    stage = Usd.Stage.Open(str(scene / "scene.usda"))
    red = linear("#cc2222")
    for prim in ("/World/Objects/rack", "/World/Objects/racks/racks_r0c0", "/World/Objects/racks/racks_r0c1"):
        # the white trays and the (instanced) white frame: the main color
        for mat in ("Tray0Mat", "Tray1Mat", "FrameMat"):
            assert _shader(stage, prim, mat, "Preview").GetInput("diffuseColor").Get() == red
            assert _shader(stage, prim, mat, "MDL").GetInput("diffuse_color_constant").Get() == red
            assert _shader(stage, prim, mat, "OpenPBR").GetInput("base_color").Get() == red
        # glass (largest area), chrome and the dark base keep their colors
        assert _shader(stage, prim, "GlassMat", "MDL").GetInput("glass_color").Get() == WHITE
        assert _shader(stage, prim, "PoleMat", "Preview").GetInput("diffuseColor").Get() == Gf.Vec3f(0.6, 0.6, 0.6)
        assert _shader(stage, prim, "BaseMat", "OpenPBR").GetInput("base_color").Get() == Gf.Vec3f(0.05, 0.05, 0.05)
    # the textured label of the array: the texture multiplied by the color
    gold = linear("#ffd700")
    lab = "/World/Objects/racks/racks_r0c1"
    assert _shader(stage, lab, "LabelMat", "Tex").GetInput("scale").Get() == Gf.Vec4f(*gold, 1.0)
    assert _shader(stage, lab, "LabelMat", "MDL").GetInput("diffuse_tint").Get() == gold
    assert not _shader(stage, "/World/Objects/rack", "LabelMat", "Tex").GetInput("scale")
    cd = stage.GetPrimAtPath("/World/Objects/rack").GetCustomDataByKey("agentic_sim_world_gen:tint")
    assert cd["#cc2222"]["materials"] == "FrameMat, Tray0Mat, Tray1Mat" and cd["#cc2222"]["inputs"] == 9
    # the asset file itself is untouched
    src = Usd.Stage.Open(str(scene.parent / "lib" / "rack" / "rack.usda"))
    assert UsdShade.Shader(src.GetPrimAtPath("/rack/_materials/Tray0Mat/Preview")).GetInput("diffuseColor").Get() == WHITE


def test_texture_false_paints_flat(scene):
    """A printed sign made plain white: multiplying by white would leave the print, so the texture is dropped."""
    service.set_tint(scene, "rack", {"color": "#ffffff", "materials": ["Label*"], "texture": False})
    assert service.load(scene).obj("rack").tint[0].texture is False
    service.compose_scene(scene)
    stage = Usd.Stage.Open(str(scene / "scene.usda"))
    prim = "/World/Objects/rack"
    white = linear("#ffffff")
    ps = _shader(stage, prim, "LabelMat", "Preview").GetInput("diffuseColor")
    assert not ps.HasConnectedSource() and ps.Get() == white
    mdl = _shader(stage, prim, "LabelMat", "MDL")
    assert mdl.GetInput("diffuse_texture").Get().path == ""
    assert mdl.GetInput("diffuse_color_constant").Get() == white
    assert mdl.GetInput("diffuse_tint").Get() == Gf.Vec3f(1, 1, 1)
    cd = stage.GetPrimAtPath(prim).GetCustomDataByKey("agentic_sim_world_gen:tint")
    assert cd["#ffffff"]["texture"] is False and cd["#ffffff"]["materials"] == "LabelMat"


def test_no_match_is_reported_and_none_goes_back(scene):
    service.set_tint(scene, "rack", {"color": "#000000", "materials": ["Nothing*"]})
    service.compose_scene(scene)
    stage = Usd.Stage.Open(str(scene / "scene.usda"))
    cd = stage.GetPrimAtPath("/World/Objects/rack").GetCustomDataByKey("agentic_sim_world_gen:tint")
    assert cd["#000000"]["error"] == "no material matched"
    st = service.set_tint(scene, "rack", None)
    assert service.load(scene).obj("rack").tint == []
    hist = service.status(scene)
    assert hist["issues"] == [] and st["issues"] == []
