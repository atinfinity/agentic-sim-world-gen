"""set_screen on a monitor whose screen has no texture: LG's `monitor_27gx790a` has its front screen as the
material `lcd` (no texture, every UV the same point) and a lit texture `on_1` on a small decoration at the back.
The image goes on a quad laid just in front of the `lcd` face. Offline, with a monitor built like it."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("pxr")
pytest.importorskip("PIL")
from PIL import Image
from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics, UsdShade
from test_screen import poster

from agentic_sim_world_gen import service
from agentic_sim_world_gen.assets.providers.local_folder import build_index
from agentic_sim_world_gen.usd.screen import detect_screens, panel_screens

W, H, Z0 = 0.6, 0.34, 0.2  # the panel's front: facing -Y at y = 0


def _mesh(stage, path, quads, mat, uvs=None, st=True):
    mesh = UsdGeom.Mesh.Define(stage, path)
    mesh.CreatePointsAttr([p for q in quads for p in q])
    mesh.CreateFaceVertexCountsAttr([4] * len(quads))
    mesh.CreateFaceVertexIndicesAttr(list(range(4 * len(quads))))
    if st:
        pv = UsdGeom.PrimvarsAPI(mesh.GetPrim()).CreatePrimvar("st", Sdf.ValueTypeNames.TexCoord2fArray,
                                                               UsdGeom.Tokens.faceVarying)
        pv.Set(uvs or [(0.5, 0.5)] * (4 * len(quads)))  # degenerate: one point
    UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(mat)


def make_monitor(folder: Path, lit_logo: bool = True, body: str | None = None, st: bool = True) -> Path:
    """body: where the rigid body is (SimReady puts it on a part): "part" (/Mon/Part, holding the panel),
    "instance" (/Mon/Part, an instance of /Proto), "in_instance" (/Proto/Body inside the instance /Mon/Part)."""
    folder.mkdir(parents=True)
    (folder / "textures").mkdir()
    stage = Usd.Stage.CreateNew(str(folder / "monitor.usd"))
    UsdGeom.SetStageUpAxis(stage, "Z")
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    root = UsdGeom.Xform.Define(stage, "/Mon")
    stage.SetDefaultPrim(root.GetPrim())
    names = ("lcd_bezel", "body") if body in ("instance", "in_instance") else ("lcd", "lcd_bezel", "body")
    mats = {n: UsdShade.Material.Define(stage, f"/Mon/Looks/{n}") for n in names}
    for n, m in mats.items():
        ps = UsdShade.Shader.Define(stage, f"/Mon/Looks/{n}/Surface")
        ps.CreateIdAttr("UsdPreviewSurface")
        m.CreateSurfaceOutput().ConnectToSource(ps.CreateOutput("surface", Sdf.ValueTypeNames.Token))
    x0, x1, z1 = -W / 2, W / 2, Z0 + H
    front = [(x0, 0, Z0), (x1, 0, Z0), (x1, 0, z1), (x0, 0, z1)]  # counter-clockwise from -Y
    back = [(x0, 0.01, Z0), (x0, 0.01, z1), (x1, 0.01, z1), (x1, 0.01, Z0)]
    if body in ("instance", "in_instance"):
        stage.CreateClassPrim("/Proto")
        lcd = UsdShade.Material.Define(stage, "/Proto/Looks/lcd")  # bindings stay inside the prototype
        holder = "/Proto/Body" if body == "in_instance" else "/Proto"
        UsdGeom.Xform.Define(stage, holder)
        _mesh(stage, f"{holder}/Panel", [front, back], lcd, st=st)
        part = UsdGeom.Xform.Define(stage, "/Mon/Part").GetPrim()
        part.GetReferences().AddInternalReference("/Proto")
        part.SetInstanceable(True)
        rb = part if body == "instance" else stage.GetPrimAtPath(holder)
        UsdPhysics.RigidBodyAPI.Apply(rb)
    elif body == "part":
        UsdPhysics.RigidBodyAPI.Apply(UsdGeom.Xform.Define(stage, "/Mon/Part").GetPrim())
        _mesh(stage, "/Mon/Part/Panel", [front, back], mats["lcd"], st=st)
    else:
        _mesh(stage, "/Mon/Panel", [front, back], mats["lcd"], st=st)
    _mesh(stage, "/Mon/Bezel", [[(x0 - 0.01, -0.001, Z0 - 0.01), (x0, -0.001, Z0 - 0.01), (x0, -0.001, z1 + 0.01),
                                 (x0 - 0.01, -0.001, z1 + 0.01)]], mats["lcd_bezel"])
    body = UsdGeom.Cube.Define(stage, "/Mon/Body")  # behind the panel
    UsdGeom.XformCommonAPI(body).SetTranslate((0, 0.06, 0.3))
    UsdGeom.XformCommonAPI(body).SetScale((0.1, 0.05, 0.3))
    UsdShade.MaterialBindingAPI.Apply(body.GetPrim()).Bind(mats["body"])
    if lit_logo:  # LG's `on_1`: a lit texture on a small face at the back
        Image.new("RGB", (8, 8), (120, 20, 150)).save(folder / "textures" / "Image_2.png")
        logo = UsdShade.Material.Define(stage, "/Mon/Looks/on_1")
        sh = UsdShade.Shader.Define(stage, "/Mon/Looks/on_1/Shader")
        sh.SetSourceAsset("OmniPBR.mdl", "mdl")
        sh.CreateInput("enable_emission", Sdf.ValueTypeNames.Bool).Set(True)
        sh.CreateInput("emissive_color_texture", Sdf.ValueTypeNames.Asset).Set("./textures/Image_2.png")
        logo.CreateSurfaceOutput("mdl").ConnectToSource(sh.CreateOutput("out", Sdf.ValueTypeNames.Token))
        _mesh(stage, "/Mon/Logo", [[(-0.03, 0.12, 0.3), (-0.03, 0.12, 0.36), (0.03, 0.12, 0.36), (0.03, 0.12, 0.3)]],
              logo, uvs=[(0, 0), (0, 1), (1, 1), (1, 0)])
    stage.GetRootLayer().Save()
    (folder / "asset.json").write_text(json.dumps({"usd": "monitor.usd", "name": "monitor", "category": "electronics",
                                                   "tags": ["computer monitor"], "license": "CC0-1.0"}))
    return folder / "monitor.usd"


@pytest.fixture(autouse=True)
def _reset():
    yield
    service.set_provider(None)


def _scene(tmp_path, lit_logo=True, objects=None, **kw):
    make_monitor(tmp_path / "lib" / "monitor", lit_logo, **kw)
    recs = build_index(tmp_path / "lib", tmp_path / "cache")
    service.set_provider(type("P", (), {"name": "f", "search": lambda self, q, limit=10, size=None: recs})())
    d = tmp_path / "s"
    service.create_scene({"name": "s", "room": {"width": 4, "depth": 3, "height": 2.6},
                          "objects": objects or [{"id": "mon", "query": "computer monitor", "position": [2, 1.5]}]}, d)
    return d


def test_panel_screens_name_the_display_not_its_bezel(tmp_path):
    stage = Usd.Stage.Open(str(make_monitor(tmp_path / "m")))
    assert [s["material"] for s in panel_screens(stage.GetPseudoRoot())] == ["lcd"]
    assert [s["material"] for s in detect_screens(stage.GetPseudoRoot())] == ["on_1"]


@pytest.mark.parametrize("lit_logo", [True, False])
def test_the_image_goes_on_a_quad_over_the_front(tmp_path, lit_logo):
    d = _scene(tmp_path, lit_logo)
    img = poster(tmp_path / "poster.png")
    assert service.set_screen(d, "mon", {"image": str(img)})["issues"] == []
    service.compose_scene(d)
    stage = Usd.Stage.Open(str(d / "scene.usda"))
    data = stage.GetPrimAtPath("/World/Objects/mon").GetCustomDataByKey("agentic_sim_world_gen:screen")
    assert list(data["screens"]) == ["lcd"]  # the large panel, not the lit logo at the back
    sc = data["screens"]["lcd"]
    assert sc["size_m"] == str([W, H]) and Path(sc["texture"]).is_file()
    assert Image.open(sc["texture"]).size == (2048, round(2048 * H / W))
    quad = stage.GetPrimAtPath(sc["overlay"])
    assert quad.IsA(UsdGeom.Mesh) and quad.GetParent().GetPath() == Sdf.Path("/World/Objects/mon")
    # world corners: the front's, 1 mm out of it (toward -Y of the asset), the first one lower left
    xf = UsdGeom.XformCache().GetLocalToWorldTransform(quad)
    pts = [xf.Transform(Gf.Vec3d(p)) for p in UsdGeom.Mesh(quad).GetPointsAttr().Get()]
    panel = stage.GetPrimAtPath("/World/Objects/mon/Asset/Panel")
    pxf = UsdGeom.XformCache().GetLocalToWorldTransform(panel)
    front = [pxf.Transform(Gf.Vec3d(p)) for p in UsdGeom.Mesh(panel).GetPointsAttr().Get()[:4]]
    out = pxf.TransformDir(Gf.Vec3d(0, -1, 0)).GetNormalized()
    for p, q in zip(pts, front):
        assert (p - (q + out * 0.001)).GetLength() < 1e-5
    # its material shows the image lit (OmniPBR) and in UsdPreviewSurface
    mat = UsdShade.MaterialBindingAPI(quad).GetDirectBinding().GetMaterial()
    mdl = UsdShade.Shader(stage.GetPrimAtPath(f"{mat.GetPath()}/MDL"))
    assert mdl.GetInput("emissive_color_texture").Get().path == sc["texture"]
    assert mdl.GetInput("enable_emission").Get() is True
    # the asset's own materials are left as they were
    files = [str(i.Get().path) for p in Usd.PrimRange(stage.GetPrimAtPath("/World/Objects/mon/Asset"))
             if p.IsA(UsdShade.Shader) for i in UsdShade.Shader(p).GetInputs()
             if i.GetTypeName() == Sdf.ValueTypeNames.Asset and i.Get()]
    assert sc["texture"] not in files


def test_every_unit_of_an_array_gets_a_quad(tmp_path):
    d = _scene(tmp_path, objects=[{"id": "mon", "query": "computer monitor", "position": [2, 1.5],
                                   "array": {"count": 3, "spacing": 0.2}}])
    service.set_screen(d, "mon", {"image": str(poster(tmp_path / "poster.png"))})
    service.compose_scene(d)
    stage = Usd.Stage.Open(str(d / "scene.usda"))
    data = stage.GetPrimAtPath("/World/Objects/mon").GetCustomDataByKey("agentic_sim_world_gen:screen")
    assert data["screens"]["lcd"]["units"] == "3"
    quads = [p for p in Usd.PrimRange(stage.GetPrimAtPath("/World/Objects/mon")) if p.GetName() == "Screen_lcd"]
    assert len(quads) == 3
    mats = {UsdShade.MaterialBindingAPI(q).GetDirectBinding().GetMaterial().GetPath() for q in quads}
    assert len(mats) == 1


def test_off_does_not_add_a_quad(tmp_path):
    d = _scene(tmp_path)
    service.set_screen(d, "mon", "off")
    service.compose_scene(d)
    stage = Usd.Stage.Open(str(d / "scene.usda"))
    assert not [p for p in Usd.PrimRange(stage.GetPrimAtPath("/World/Objects/mon")) if p.GetName().startswith("Screen_")]


def _corners(stage, quad):
    xf = UsdGeom.XformCache().GetLocalToWorldTransform(quad)
    return [xf.Transform(Gf.Vec3d(p)) for p in UsdGeom.Mesh(quad).GetPointsAttr().Get()]


@pytest.mark.parametrize("body, parent", [("part", "Asset/Part"), ("instance", "Asset/Part"),
                                          ("in_instance", "Asset/Part/Body")])
def test_the_quad_moves_with_the_rigid_body(tmp_path, body, parent):
    """The quad goes under the rigid body holding the screen, so that it follows it in a simulation
    (an instance is switched off for that)."""
    d = _scene(tmp_path, body=body, objects=[{"id": "mon", "query": "computer monitor", "position": [2, 1.5],
                                              "yaw_deg": 30}])
    service.set_screen(d, "mon", {"image": str(poster(tmp_path / "poster.png"))})
    service.compose_scene(d)
    stage = Usd.Stage.Open(str(d / "scene.usda"))
    sc = stage.GetPrimAtPath("/World/Objects/mon").GetCustomDataByKey("agentic_sim_world_gen:screen")["screens"]["lcd"]
    quad = stage.GetPrimAtPath(sc["overlay"])
    assert quad.IsValid() and not quad.IsInstanceProxy()
    assert quad.GetParent().GetPath() == Sdf.Path(f"/World/Objects/mon/{parent}")
    # turned with the object: 1 mm out of the front, the size of the panel
    panel = [p for p in Usd.PrimRange(stage.GetPrimAtPath("/World/Objects/mon/Asset"), Usd.TraverseInstanceProxies())
             if p.GetName() == "Panel"][0]
    pxf = UsdGeom.XformCache().GetLocalToWorldTransform(panel)
    out = pxf.TransformDir(Gf.Vec3d(0, -1, 0)).GetNormalized()
    front = [pxf.Transform(Gf.Vec3d(p)) for p in UsdGeom.Mesh(panel).GetPointsAttr().Get()[:4]]
    pts = _corners(stage, quad)
    for p, q in zip(pts, front):
        assert (p - (q + out * 0.001)).GetLength() < 1e-5
    n = Gf.Cross(pts[1] - pts[0], pts[3] - pts[0]).GetNormalized()
    assert Gf.Dot(n, out) > 0.9999
    assert abs((pts[1] - pts[0]).GetLength() - W) < 1e-5 and abs((pts[3] - pts[0]).GetLength() - H) < 1e-5
    assert abs(pts[0][2] - Z0) < 1e-5


def test_a_panel_without_uvs_takes_the_quad_too(tmp_path):
    d = _scene(tmp_path, lit_logo=False, st=False)
    assert service.set_screen(d, "mon", {"image": str(poster(tmp_path / "poster.png"))})["issues"] == []
    service.compose_scene(d)
    stage = Usd.Stage.Open(str(d / "scene.usda"))
    sc = stage.GetPrimAtPath("/World/Objects/mon").GetCustomDataByKey("agentic_sim_world_gen:screen")["screens"]["lcd"]
    assert "error" not in sc and stage.GetPrimAtPath(sc["overlay"]).IsValid()


def test_off_and_on_need_a_screen_that_lights_up(tmp_path):
    """A monitor with only an untextured panel: an image goes on it, but "off" / "on" have nothing to switch."""
    d = _scene(tmp_path, lit_logo=False)
    r = service.set_screen(d, "mon", "off")
    assert r["ok"] is False and "no screen that lights up" in r["error"]
