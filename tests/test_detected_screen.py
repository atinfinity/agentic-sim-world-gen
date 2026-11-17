"""set_screen on an asset that declares no screen: a material that lights up a texture is the screen
(a TV from SimReady Central has an MDL with emissive_color_texture). Offline, with usdgen-like signage."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("pxr")
pytest.importorskip("PIL")
from PIL import Image  # noqa: E402
from pxr import Sdf, Usd, UsdShade  # noqa: E402

from agentic_sim_world_gen import service  # noqa: E402
from agentic_sim_world_gen.assets.providers.local_folder import build_index  # noqa: E402
from agentic_sim_world_gen.usd.screen import detect_screens  # noqa: E402

from test_screen import make_signage, poster  # noqa: E402


def make_tv(folder: Path, how: str) -> None:
    """The signage without asset.json `screen`; its material emits the screen texture as `how` says:
    mdl (OmniPBR-like inputs), subset (mdl, bound to a GeomSubset of a mesh bound to another material), preview (UsdPreviewSurface emissiveColor from the image node), off (emission off),
    none (no emission)."""
    usd = make_signage(folder)
    meta = json.loads((folder / "asset.json").read_text())
    meta.pop("screen")
    meta.update(name="tv", tags=["television"])
    (folder / "asset.json").write_text(json.dumps(meta))
    stage = Usd.Stage.Open(str(usd))
    base = "/Sign/_materials/ScreenMat"
    if how == "subset":  # one mesh, the screen a GeomSubset with its own material (the LG TV)
        mesh = stage.GetPrimAtPath("/Sign/Screen")
        other = UsdShade.Material.Define(stage, "/Sign/_materials/Body")
        UsdShade.MaterialBindingAPI(mesh).Bind(other)
        sub = UsdShade.MaterialBindingAPI(mesh).CreateMaterialBindSubset("screen", [0, 1])
        UsdShade.MaterialBindingAPI.Apply(sub.GetPrim()).Bind(UsdShade.Material(stage.GetPrimAtPath(base)))
    if how in ("mdl", "off", "subset"):
        sh = UsdShade.Shader.Define(stage, f"{base}/Shader")
        sh.SetSourceAsset("OmniPBR.mdl", "mdl")
        sh.CreateInput("enable_emission", Sdf.ValueTypeNames.Bool).Set(how != "off")
        sh.CreateInput("emissive_intensity", Sdf.ValueTypeNames.Float).Set(1000.0)
        sh.CreateInput("emissive_color_texture", Sdf.ValueTypeNames.Asset).Set("./textures/Screen.png")
    elif how == "preview":
        ps = UsdShade.Shader(stage.GetPrimAtPath(f"{base}/Principled_BSDF"))
        tex = UsdShade.Shader(stage.GetPrimAtPath(f"{base}/Image_Texture"))
        ps.CreateInput("emissiveColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(
            tex.CreateOutput("rgb", Sdf.ValueTypeNames.Float3))
    stage.GetRootLayer().Save()


def _lib(tmp_path, how):
    make_tv(tmp_path / "lib" / "tv", how)
    recs = build_index(tmp_path / "lib", tmp_path / "cache")
    service.set_provider(type("P", (), {"name": "f", "search": lambda self, q, limit=10, size=None: recs})())
    d = tmp_path / "s"
    service.create_scene({"name": "s", "room": {"width": 4, "depth": 3, "height": 2.6},
                          "objects": [{"id": "tv", "query": "television", "position": [2, 1.5]}]}, d)
    return recs, d


@pytest.fixture(autouse=True)
def _reset():
    yield
    service.set_provider(None)


@pytest.mark.parametrize("how", ["mdl", "preview"])
def test_detect(tmp_path, how):
    make_tv(tmp_path / "tv", how)
    stage = Usd.Stage.Open(str(tmp_path / "tv" / "sign.usd"))
    (s,) = detect_screens(stage.GetPseudoRoot())
    assert s["material"] == "ScreenMat" and s["detected"] and Path(s["image"]).name == "Screen.png"


@pytest.mark.parametrize("how", ["off", "none"])
def test_no_lit_texture_is_no_screen(tmp_path, how):
    make_tv(tmp_path / "tv", how)
    stage = Usd.Stage.Open(str(tmp_path / "tv" / "sign.usd"))
    assert detect_screens(stage.GetPseudoRoot()) == []
    (r,), d = _lib(tmp_path, how)
    assert r.screens == []
    with pytest.raises(ValueError, match="no screen"):
        service.set_screen(d, "tv", {"image": str(poster(tmp_path / "p.png"))})


@pytest.mark.parametrize("how", ["mdl", "preview", "subset"])
def test_set_screen_on_a_tv(tmp_path, how):
    (r,), d = _lib(tmp_path, how)
    assert r.screens == []  # nothing declared
    img = poster(tmp_path / "poster.png")
    (tmp_path / "poster.source.json").write_text(json.dumps({"title": "Safety", "license": "CC0-1.0"}))
    st = service.set_screen(d, "tv", {"image": str(img)})
    assert st["issues"] == []
    res = service.compose_scene(d)
    assert not [w for w in res.get("warnings", []) if "screen" in w]
    stage = Usd.Stage.Open(str(d / "scene.usda"))
    data = stage.GetPrimAtPath("/World/Objects/tv").GetCustomDataByKey("agentic_sim_world_gen:screen")
    assert data["detected"] and data["screens"]["ScreenMat"]["inputs"] != "0"
    tex = Path(data["screens"]["ScreenMat"]["texture"])
    assert tex.is_file()
    # the emissive input now shows the rendered texture
    files = [str(i.Get().path) for p in Usd.PrimRange(stage.GetPrimAtPath("/World/Objects/tv"))
             if p.IsA(UsdShade.Shader) for i in UsdShade.Shader(p).GetInputs()
             if i.GetTypeName() == Sdf.ValueTypeNames.Asset and i.Get()]
    assert str(tex) in files and not any(f.endswith("/Screen.png") for f in files)
    rep = service.license_report(d)
    assert [s["title"] for s in rep["screens"]] == ["Safety"] and not rep["warnings"]


def test_mdl_without_enable_emission_is_dark(tmp_path):
    """OmniPBR's enable_emission defaults to false: a texture authored without it does not light up (review)."""
    make_tv(tmp_path / "tv", "mdl")
    stage = Usd.Stage.Open(str(tmp_path / "tv" / "sign.usd"))
    sh = stage.GetPrimAtPath("/Sign/_materials/ScreenMat/Shader")
    sh.RemoveProperty("inputs:enable_emission")
    assert detect_screens(stage.GetPseudoRoot()) == []


def test_an_odd_emission_amount_does_not_crash(tmp_path):
    make_tv(tmp_path / "tv", "mdl")
    stage = Usd.Stage.Open(str(tmp_path / "tv" / "sign.usd"))
    sh = UsdShade.Shader(stage.GetPrimAtPath("/Sign/_materials/ScreenMat/Shader"))
    sh.CreateInput("emission_weight", Sdf.ValueTypeNames.Token).Set("auto")
    assert [s["material"] for s in detect_screens(stage.GetPseudoRoot())] == ["ScreenMat"]


def _add_lit_logo(usd: Path) -> None:
    """A small lit logo with its own emissive texture under the screen."""
    from pxr import UsdGeom

    stage = Usd.Stage.Open(str(usd))
    Image.new("RGB", (8, 8), (255, 255, 255)).save(usd.parent / "textures" / "Logo.png")
    mat = UsdShade.Material.Define(stage, "/Sign/_materials/Logo")
    sh = UsdShade.Shader.Define(stage, "/Sign/_materials/Logo/Shader")
    sh.SetSourceAsset("OmniPBR.mdl", "mdl")
    sh.CreateInput("enable_emission", Sdf.ValueTypeNames.Bool).Set(True)
    sh.CreateInput("emissive_color_texture", Sdf.ValueTypeNames.Asset).Set("./textures/Logo.png")
    m = UsdGeom.Mesh.Define(stage, "/Sign/Logo")
    m.CreatePointsAttr([(-0.05, -0.001, 0.9), (0.05, -0.001, 0.9), (0.05, -0.001, 0.95), (-0.05, -0.001, 0.95)])
    m.CreateFaceVertexCountsAttr([4])
    m.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
    UsdGeom.PrimvarsAPI(m.GetPrim()).CreatePrimvar("st", Sdf.ValueTypeNames.TexCoord2fArray,
                                                   UsdGeom.Tokens.faceVarying).Set([(0, 0), (1, 0), (1, 1), (0, 1)])
    UsdShade.MaterialBindingAPI.Apply(m.GetPrim()).Bind(mat)
    stage.GetRootLayer().Save()


def test_the_picture_goes_on_the_screen_not_a_lit_logo(tmp_path):
    make_tv(tmp_path / "lib" / "tv", "mdl")
    _add_lit_logo(tmp_path / "lib" / "tv" / "sign.usd")
    recs = build_index(tmp_path / "lib", tmp_path / "cache")
    service.set_provider(type("P", (), {"name": "f", "search": lambda self, q, limit=10, size=None: recs})())
    d = tmp_path / "s"
    service.create_scene({"name": "s", "room": {"width": 4, "depth": 3, "height": 2.6},
                          "objects": [{"id": "tv", "query": "television", "position": [2, 1.5]}]}, d)
    service.set_screen(d, "tv", {"image": str(poster(tmp_path / "p.png"))})
    service.compose_scene(d)
    stage = Usd.Stage.Open(str(d / "scene.usda"))
    data = stage.GetPrimAtPath("/World/Objects/tv").GetCustomDataByKey("agentic_sim_world_gen:screen")
    assert list(data["screens"]) == ["ScreenMat"]
    del stage  # compose writes the layer anew
    # a material the asset does not have is a compose warning
    service.set_screen(d, "tv", {"image": {"ScreenMat": str(tmp_path / "p.png"), "Nope": str(tmp_path / "p.png")}})
    ws = service.compose_scene(d)["warnings"]
    assert any("Nope" in w for w in ws)
