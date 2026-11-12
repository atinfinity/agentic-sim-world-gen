"""Room surfaces (materials, friction) and lighting presets."""

from __future__ import annotations

import pytest

pytest.importorskip("pxr")
from pxr import Usd, UsdLux, UsdPhysics, UsdShade  # noqa: E402

from agentic_sim_world_gen.assets.record import AssetRecord  # noqa: E402
from agentic_sim_world_gen.preview.layout2d import render_svg  # noqa: E402
from agentic_sim_world_gen.scene.looks import SURFACES, TEXTURES, builtin_texture, fixture_grid, resolve_surface  # noqa: E402
from agentic_sim_world_gen.scene.spec import Placement, ResolvedScene, Room, SceneSpec, record_to_dict  # noqa: E402
from agentic_sim_world_gen.usd.compose import compose  # noqa: E402


def _scene(**room) -> ResolvedScene:
    spec = SceneSpec.model_validate({"name": "t", "room": {"width": 6, "depth": 4, "height": 3, **room},
                                     "objects": [{"id": "box", "query": "box", "position": [1, 1]}]})
    a = AssetRecord(id="x", source="test", uri="box.usda", name="box.usda", bbox_min=(-0.5, -0.5, 0), bbox_max=(0.5, 0.5, 1))
    return ResolvedScene(spec=spec, placements=[Placement(object_id="box", candidates=[record_to_dict(a)], selected=0)])


def test_surface_resolution():
    s = resolve_surface({"preset": "concrete", "color": [0.2, 0.2, 0.2]}, "wood_light")
    assert s["color"] == (0.2, 0.2, 0.2) and s["roughness"] == 0.9 and s["name"] == "concrete"
    assert resolve_surface(None, "tatami")["name"] == "tatami"
    with pytest.raises(ValueError):
        Room(width=1, depth=1, floor={"preset": "marble"})
    with pytest.raises(ValueError):
        Room(width=1, depth=1, lighting="disco")


def test_fixture_grid():
    assert fixture_grid(6, 4, 2.4) == [(1.0, 1.0), (3.0, 1.0), (5.0, 1.0), (1.0, 3.0), (3.0, 3.0), (5.0, 3.0)]
    assert fixture_grid(1, 1, 6) == [(0.5, 0.5)]


def test_compose_materials_and_lights(tmp_path):
    scene = _scene(floor={"preset": "epoxy_gray", "texture": "tex/floor.png", "tile_m": 2.0, "static_friction": 0.9},
                   walls="metal_panel", lighting="office", lighting_scale=0.5, ceiling=True)
    stage = compose(scene, tmp_path / "scene.usda")
    stage = Usd.Stage.Open(str(tmp_path / "scene.usda"))

    floor = stage.GetPrimAtPath("/World/Room/Floor")
    mat, _ = UsdShade.MaterialBindingAPI(floor).ComputeBoundMaterial()
    assert mat.GetPath() == "/World/Room/Looks/Floor"
    pmat, _ = UsdShade.MaterialBindingAPI(floor).ComputeBoundMaterial("physics")
    assert UsdPhysics.MaterialAPI(pmat.GetPrim()).GetStaticFrictionAttr().Get() == pytest.approx(0.9)
    tex = UsdShade.Shader(stage.GetPrimAtPath("/World/Room/Looks/Floor/DiffuseTexture"))
    assert tex.GetInput("file").Get().path == str((tmp_path / "tex/floor.png").resolve())
    st = floor.GetAttribute("primvars:st").Get()
    assert max(v[0] for v in st) == pytest.approx((6 + 0.24) / 2 / 2 + 3 / 2)  # meters / tile_m

    assert stage.GetPrimAtPath("/World/Room/Ceiling").IsValid()
    fixtures = [p for p in stage.GetPrimAtPath("/World/Lights").GetChildren() if p.GetName().startswith("Fixture")]
    assert len(fixtures) == 6 and fixtures[0].IsA(UsdLux.RectLight)
    assert UsdLux.RectLight(fixtures[0]).GetIntensityAttr().Get() == pytest.approx(4000.0)
    assert UsdLux.DomeLight(stage.GetPrimAtPath("/World/Lights/Dome")).GetIntensityAttr().Get() == pytest.approx(100.0)


def test_default_look_and_preview(tmp_path):
    scene = _scene()
    stage = compose(scene, tmp_path / "scene.usda")
    assert stage.GetPrimAtPath("/World/Lights/Sun").IsValid()
    assert not stage.GetPrimAtPath("/World/Room/Ceiling").IsValid()
    svg = render_svg(_scene(lighting="warehouse", floor="tatami"))
    assert "床 tatami" in svg and "照明 warehouse" in svg and "#ffd54f" in svg


def test_set_room(tmp_path):
    from agentic_sim_world_gen import service

    service._save(_scene(), tmp_path)
    st = service.set_room(tmp_path, floor="tatami", lighting="home", width=8)
    assert st["room"]["floor"] == "tatami" and st["room"]["lighting"] == "home" and st["room"]["width"] == 8
    assert st["room"]["walls"] == "paint_white"  # untouched
    with pytest.raises(ValueError):
        service.set_room(tmp_path, lighting="disco")


def test_builtin_textures_resolution():
    """Indoor presets bring their own texture and normal map; a color override or own texture drops them."""
    s = resolve_surface("wood_light", "paint_white")
    assert s["texture"] == "builtin:wood_light" and s["normal"] == "builtin:wood_light_normal" and s["tile_m"] == 1.2
    assert resolve_surface({"preset": "tatami", "roughness": 0.5}, "x")["texture"] == "builtin:tatami"
    plain = resolve_surface({"preset": "wood_light", "color": [0.5, 0.3, 0.1]}, "x")
    assert "texture" not in plain and "normal" not in plain and plain["tile_m"] == 1.2
    own = resolve_surface({"preset": "wood_light", "texture": "oak.jpg", "tile_m": 0.5}, "x")
    assert own["texture"] == "oak.jpg" and "normal" not in own and own["tile_m"] == 0.5
    assert "texture" not in resolve_surface("asphalt", "x") and resolve_surface("asphalt", "x")["tile_m"] == 1.0
    for name in TEXTURES:
        assert name in SURFACES and SURFACES[name]["tile_m"] > 0
        assert builtin_texture(f"builtin:{name}").exists() and builtin_texture(f"builtin:{name}_normal").exists()


def test_builtin_textures_in_usd(tmp_path):
    scene = _scene(floor="tatami", walls="brick")
    compose(scene, tmp_path / "scene.usda")
    stage = Usd.Stage.Open(str(tmp_path / "scene.usda"))
    looks = "/World/Room/Looks/Floor"
    tex = UsdShade.Shader(stage.GetPrimAtPath(f"{looks}/DiffuseTexture"))
    assert tex.GetInput("file").Get().path == str((tmp_path / "textures/tatami.jpg").resolve())
    assert (tmp_path / "textures/tatami.jpg").exists() and (tmp_path / "textures/brick_normal.png").exists()
    nt = UsdShade.Shader(stage.GetPrimAtPath(f"{looks}/NormalTexture"))
    assert nt.GetInput("sourceColorSpace").Get() == "raw"
    assert tuple(nt.GetInput("bias").Get()) == (-1, -1, -1, 0)
    ps = UsdShade.Shader(stage.GetPrimAtPath(f"{looks}/PreviewSurface"))
    src = ps.GetInput("normal").GetConnectedSources()[0][0]
    assert src.source.GetPath() == nt.GetPath()


def test_compose_mdl_inputs(tmp_path):
    from pxr import Gf

    mdl = "https://example.com/Materials/vMaterials_2/Wood/Wood_Tiles_Ash.mdl"
    scene = _scene(floor={"preset": "wood_light", "mdl": mdl, "mdl_name": "Wood_Tiles_Ash_Herringbone",
                          "mdl_inputs": {"tiles_base_roughness": 0.5, "flip": True, "seed": 3}},
                   walls={"preset": "paint_white", "mdl": "https://example.com/Plaster.mdl", "mdl_name": "Plaster"})
    stage = compose(scene, tmp_path / "s.usda")
    sh = UsdShade.Shader(stage.GetPrimAtPath("/World/Room/Looks/Floor/MDL"))
    assert sh.GetInput("texture_scale").Get() == Gf.Vec2f(1.6, 1.6)
    assert sh.GetInput("wood_brightness_variation").Get() == pytest.approx(0.4)
    assert sh.GetInput("tiles_base_roughness").Get() == pytest.approx(0.5)  # the surface's mdl_inputs win
    assert sh.GetInput("flip").Get() is True and sh.GetInput("seed").Get() == 3
    walls = UsdShade.Shader(stage.GetPrimAtPath("/World/Room/Looks/Wall/MDL"))
    assert walls and not walls.GetInputs()


def _bound(stage, path):
    mat, _ = UsdShade.MaterialBindingAPI(stage.GetPrimAtPath(path)).ComputeBoundMaterial()
    return mat.GetPath()


def test_ceiling_has_its_own_surface(tmp_path):
    from agentic_sim_world_gen.scene.looks import ceiling_surface

    assert ceiling_surface(False, "office") is None
    assert ceiling_surface(True, "office")["name"] == "ceiling_tile"
    assert ceiling_surface(True, "home")["name"] == "ceiling_board"
    assert ceiling_surface(True, "warehouse")["name"] == "metal_deck"
    assert ceiling_surface("paint_beige", "office")["name"] == "paint_beige"
    # the place wins over the lighting
    assert ceiling_surface(True, "home", "convenience_store")["name"] == "ceiling_tile"
    assert ceiling_surface(True, "office", "home_center")["name"] == "metal_deck"
    assert ceiling_surface(True, "office", "cafe")["name"] == "ceiling_board"
    assert ceiling_surface(True, "office", "bakery")["name"] == "ceiling_board"
    assert ceiling_surface(True, "home", "other")["name"] == "ceiling_board"
    assert ceiling_surface("tile_white", "home", "warehouse")["name"] == "tile_white"
    with pytest.raises(ValueError):
        Room(width=1, depth=1, ceiling={"preset": "disco_ball"})

    stage = compose(_scene(walls="brick", lighting="home", ceiling=True), tmp_path / "a.usda")
    assert _bound(stage, "/World/Room/Ceiling") == "/World/Room/Looks/Ceiling"
    assert _bound(stage, "/World/Room/Wall_N") == "/World/Room/Looks/Wall"
    tex = UsdShade.Shader(stage.GetPrimAtPath("/World/Room/Looks/Ceiling/DiffuseTexture"))
    assert tex.GetInput("file").Get().path.endswith("ceiling_board.jpg")

    stage = compose(_scene(ceiling={"color": [0.9, 0.9, 0.9]}), tmp_path / "b.usda")
    assert _bound(stage, "/World/Room/Ceiling") == "/World/Room/Looks/Ceiling"
    assert "天井 custom" in render_svg(_scene(ceiling={"color": [0.9, 0.9, 0.9]}))
    assert "天井 ceiling_tile" in render_svg(_scene(lighting="office", ceiling=True))


def test_set_room_ceiling(tmp_path):
    from agentic_sim_world_gen import service

    service._save(_scene(), tmp_path)
    st = service.set_room(tmp_path, ceiling="ceiling_tile")
    assert st["room"]["ceiling"] == "ceiling_tile"
    st = service.set_room(tmp_path, ceiling=False)
    assert st["room"]["ceiling"] is False


def test_surface_color_takes_an_srgb_hex_and_stores_it_linear():
    """A color picked from an image is sRGB; '#rrggbb' converts it so the preview shows the same color."""
    from agentic_sim_world_gen.scene.looks import Surface, hex_color, linear_from_hex

    s = Surface(color="#8cbfad")
    assert s.color == pytest.approx((0.2623, 0.5209, 0.4179), abs=1e-3)
    assert hex_color(s.color) == "#8cbfad"
    assert linear_from_hex("fff") == (1.0, 1.0, 1.0)
    assert Surface(color=[0.2, 0.3, 0.4]).color == (0.2, 0.3, 0.4)  # numbers stay linear
    room = Room(width=4, depth=3, walls={"*": "paint_white", "N": {"preset": "paint_white", "color": "#8cbfad"}})
    assert hex_color(resolve_surface(room.walls["N"], "paint_white")["color"]) == "#8cbfad"
    with pytest.raises(ValueError):
        Surface(color="mint")


def test_trim_color_takes_an_srgb_hex():
    from agentic_sim_world_gen.scene.walls import Baseboard, Wainscot

    assert Wainscot(color="#8cbfad").color == pytest.approx((0.2623, 0.5209, 0.4179), abs=1e-3)
    assert Baseboard(color="#ffffff").color == (1.0, 1.0, 1.0)
