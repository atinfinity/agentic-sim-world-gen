"""Floors per plan zone: guess from the zone name, plan / scene overrides, USD pieces, previews, licenses."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("pxr")
from pxr import Usd, UsdGeom, UsdPhysics, UsdShade  # noqa: E402

from agentic_sim_world_gen import service  # noqa: E402
from agentic_sim_world_gen.preview.changes import diff, snapshot  # noqa: E402
from agentic_sim_world_gen.preview.layout2d import render_svg  # noqa: E402
from agentic_sim_world_gen.scene.looks import SURFACES, hex_color  # noqa: E402
from agentic_sim_world_gen.scene.spec import ResolvedScene, Room, SceneSpec  # noqa: E402
from agentic_sim_world_gen.scene.zone_floors import infer, zone_floors  # noqa: E402
from agentic_sim_world_gen.usd.compose import compose  # noqa: E402

PLAN = Path(__file__).parents[1] / "examples" / "plans" / "1ldk.json"


def _room(**kw) -> Room:
    return Room.model_validate({"plan_file": str(PLAN), **kw})


def _scene(**kw) -> ResolvedScene:
    return ResolvedScene(spec=SceneSpec(name="z", room=_room(**kw)), placements=[])


def test_guess_from_zone_name():
    assert infer("和室") == "tatami" and infer("Japanese room") == "tatami" and infer(None, "tatami_6") == "tatami"
    for n in ("洗面所", "浴室", "トイレ", "玄関", "Bathroom", "WC", "Entrance hall"):
        assert infer(n) == "tile_white", n
    for n in ("LDK", "洋室", "寝室", "Living", "Bedroom", "Closet", "Bus"):
        assert infer(n) is None, n


def test_resolution_order():
    by = {z["id"]: z for z in zone_floors(_room())}
    assert by["wa"]["surface"]["name"] == "tatami" and by["wa"]["how"] == "plan"
    assert by["wet"]["surface"]["name"] == "tile_white" and by["wet"]["how"] == "name"
    assert "ldk" not in by  # same as Room.floor: no piece
    by = {z["id"]: z for z in zone_floors(_room(zone_floors={"wa": "room", "ldk": "carpet_gray", "wet": {"preset": "concrete"}}))}
    assert set(by) == {"ldk", "wet"} and by["ldk"]["how"] == "scene" and by["wet"]["surface"]["name"] == "concrete"
    assert zone_floors(_room(floor="tile_white"))[0]["id"] == "wa"  # the wet zone now matches the room floor
    with pytest.raises(ValueError, match="unknown zone"):
        _room(zone_floors={"attic": "tatami"})
    with pytest.raises(ValueError):
        _room(zone_floors={"wa": {"preset": "marble"}})
    assert zone_floors(Room(width=3, depth=3)) == []


def _bound(stage, path, purpose=""):
    api = UsdShade.MaterialBindingAPI(stage.GetPrimAtPath(path))
    mat, _ = api.ComputeBoundMaterial(purpose) if purpose else api.ComputeBoundMaterial()
    return mat.GetPath()


def test_usd_pieces(tmp_path):
    stage = compose(_scene(), tmp_path / "a.usda")
    floor = stage.GetPrimAtPath("/World/Room/Floor")
    rng = UsdGeom.Mesh(floor).GetExtentAttr().Get()
    assert rng[1][2] == pytest.approx(-0.001)  # sunk 1 mm under the zone pieces
    for zid, preset in (("wa", "tatami"), ("wet", "tile_white")):
        p = f"/World/Room/ZoneFloors/{zid}"
        prim = stage.GetPrimAtPath(p)
        assert prim.IsValid() and prim.GetCustomDataByKey("agentic_sim_world_gen:zone") == zid
        assert UsdGeom.Mesh(prim).GetExtentAttr().Get()[1][2] == pytest.approx(0.0)
        assert prim.HasAPI(UsdPhysics.CollisionAPI)
        assert _bound(stage, p) == f"/World/Room/Looks/Floor_{zid}" == _bound(stage, p, "physics")
        assert stage.GetPrimAtPath(f"/World/Room/Looks/Floor_{zid}").GetCustomDataByKey("agentic_sim_world_gen:surface") == preset
        assert list(prim.GetAttribute("semantics:labels:class").Get()) == ["floor"]
    tex = UsdShade.Shader(stage.GetPrimAtPath("/World/Room/Looks/Floor_wa/DiffuseTexture"))
    assert tex.GetInput("file").Get().path.endswith("tatami.jpg")
    assert not stage.GetPrimAtPath("/World/Room/ZoneFloors/ldk").IsValid()

    stage = compose(ResolvedScene(spec=SceneSpec(name="r", room=Room(width=3, depth=3)), placements=[]), tmp_path / "b.usda")
    assert UsdGeom.Mesh(stage.GetPrimAtPath("/World/Room/Floor")).GetExtentAttr().Get()[1][2] == pytest.approx(0.0)
    assert not stage.GetPrimAtPath("/World/Room/ZoneFloors").IsValid()


def test_svg_and_changes():
    svg = render_svg(_scene())
    tatami = hex_color(tuple(0.5 * c + 0.5 for c in SURFACES["tatami"]["color"]))
    assert f'fill="{tatami}"' in svg and "和室 tatami" in svg and "洗面 tile_white" in svg
    a = snapshot(_scene(), [])
    b = snapshot(_scene(zone_floors={"wa": "wood_dark", "wet": "room"}), [])
    assert a["room"]["zone_floors"] == {"和室": "畳", "洗面": "白いタイル"}
    lines = diff(a, b)
    assert "床（和室）：畳 → 暗い木目" in lines and "床（洗面）：白いタイル → 部屋の床" in lines
    old = json.loads(json.dumps(a))
    old["room"].pop("zone_floors")
    assert not any(x.startswith("床（") for x in diff(old, b))  # snapshots from an older version


def test_set_room_and_select(tmp_path):
    d = tmp_path / "s"
    service.create_scene({"name": "z", "room": {"plan_file": str(PLAN)}, "objects": []}, d)
    st = service.set_room(d, zone_floors={"ldk": "carpet_gray"})
    zones = {z["id"]: z for z in st["room"]["layout"]["zones"]}
    assert zones["ldk"]["floor"] == "carpet_gray" and zones["wa"]["floor"] == "tatami" and zones["wet"]["floor_from"] == "name"
    st = service.set_room(d, zone_floors={"wa": "light oak planks"}, zone_floor_alt_queries={"wa": ["pale wood"]})
    assert st["room"]["zone_floors"]["wa"]["mdl"].endswith(".mdl") and "carpet" in json.dumps(st["room"]["zone_floors"]["ldk"])
    assert "floor:wa" in st["surface_candidates"]
    service.select_surface(d, "floor:wa", 1)
    r = service.license_report(d)
    assert any(m["surface"] == "floor wa" for m in r["materials"])
    st = service.set_room(d, zone_floors={"wa": None, "ldk": None})
    assert st["room"]["zone_floors"] == {}
    zones = {z["id"]: z for z in st["room"]["layout"]["zones"]}
    assert zones["wa"]["floor"] == "tatami" and "floor" not in zones["ldk"]
