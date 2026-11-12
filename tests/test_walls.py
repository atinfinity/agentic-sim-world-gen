"""Per-wall finishes (accent walls), wainscot and baseboards."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("pxr")
from pxr import Usd, UsdGeom, UsdPhysics, UsdShade  # noqa: E402

from agentic_sim_world_gen import service  # noqa: E402
from agentic_sim_world_gen.assets.record import AssetRecord  # noqa: E402
from agentic_sim_world_gen.layout.room import RoomGeometry  # noqa: E402
from agentic_sim_world_gen.preview.changes import diff, snapshot  # noqa: E402
from agentic_sim_world_gen.preview.layout2d import render_svg  # noqa: E402
from agentic_sim_world_gen.scene.spec import ResolvedScene, Room, SceneSpec  # noqa: E402
from agentic_sim_world_gen.scene.walls import trim_depth, wall_surface  # noqa: E402
from agentic_sim_world_gen.usd.compose import compose  # noqa: E402

PLAN = Path(__file__).parents[1] / "examples" / "plans" / "1ldk.json"
DOOR = {"kind": "door", "wall": "south", "offset": 1.0, "width": 0.8}
WINDOW = {"kind": "window", "wall": "east", "offset": 1.5, "width": 1.0, "sill": 0.8, "height": 1.0}


def _room(**kw) -> Room:
    return Room(width=4, depth=3, **kw)


def _bound(stage, path):
    return UsdShade.MaterialBindingAPI(stage.GetPrimAtPath(path)).ComputeBoundMaterial()[0].GetPath().pathString


def test_per_wall_map_and_validation():
    r = _room(walls={"*": "paint_beige", "N": "brick", "east": {"color": [0.2, 0.3, 0.5]}})
    assert wall_surface(r.walls, "north", True)["name"] == "brick"
    assert wall_surface(r.walls, "east", True)["color"] == (0.2, 0.3, 0.5)
    assert wall_surface(r.walls, "south", True)["name"] == "paint_beige"
    assert wall_surface({"N": "brick"}, "west", True)["name"] == "paint_white"  # no default key
    assert Room.model_validate(r.model_dump()).model_dump()["walls"] == r.model_dump()["walls"]  # round trip
    # one surface as a dict is still one surface for every wall
    assert _room(walls={"preset": "brick", "roughness": 0.5}).walls.preset == "brick"
    with pytest.raises(ValueError, match="unknown wall 'X'"):
        _room(walls={"X": "brick"})
    with pytest.raises(ValueError):
        _room(walls={"N": {"preset": "marble"}})
    plan = json.loads(PLAN.read_text())
    ids = [w["id"] for w in plan["walls"]]
    pr = Room(plan_file=str(PLAN))
    assert wall_surface(Room.model_validate({**pr.model_dump(), "walls": {ids[0]: "brick"}}).walls, ids[0], False)["name"] == "brick"
    with pytest.raises(ValueError, match="unknown wall 'N'"):
        Room.model_validate({**pr.model_dump(), "walls": {"N": "brick"}})
    with pytest.raises(ValueError, match="wainscot.walls"):
        _room(wainscot={"walls": ["up"]})
    assert _room(baseboard=True).baseboard.height == 0.06 and _room(baseboard=False).baseboard is None
    with pytest.raises(ValueError, match="outdoor"):
        Room(width=10, depth=10, outdoor=True, baseboard=True)


def test_face_runs_cut_at_doors_and_sills():
    g = RoomGeometry(_room(openings=[DOOR, WINDOW]))
    south = next(f for f in g.faces if f.wall == "south")
    runs = g.face_runs(south, 0.9)
    assert [(round(p[0], 3), round(q[0], 3), top) for p, q, top in runs] == [(0, 0.6, 0.9), (1.4, 4, 0.9)]
    east = next(f for f in g.faces if f.wall == "east")
    assert [(round(p[1], 3), round(q[1], 3), top) for p, q, top in g.face_runs(east, 0.9)] == [
        (0, 1, 0.9), (1, 2, 0.8), (2, 3, 0.9)]  # stops at the window sill
    assert len(g.face_runs(east, 0.06)) == 1  # below the sill: one piece


def test_compose_accent_wall_and_trim(tmp_path):
    room = _room(walls={"*": "paint_white", "N": "brick"}, openings=[DOOR],
                 wainscot={"height": 0.9, "walls": ["S"]}, baseboard={"surface": "trim_wood", "color": [0.1, 0.1, 0.1]})
    stage = compose(ResolvedScene(spec=SceneSpec(name="t", room=room), placements=[]), tmp_path / "a.usda")
    assert _bound(stage, "/World/Room/Wall_N/Part_00") == "/World/Room/Looks/Wall_N"
    assert _bound(stage, "/World/Room/Wall_W/Part_00") == "/World/Room/Looks/Wall"
    tex = UsdShade.Shader.Get(stage, "/World/Room/Looks/Wall_N/DiffuseTexture")
    assert tex.GetInput("file").Get().path.endswith("brick.jpg")
    # wainscot only on the south wall, around the door; baseboards everywhere, in front of the wainscot
    kids = [p.GetName() for p in stage.GetPrimAtPath("/World/Room/Wall_S").GetChildren()]
    assert [k for k in kids if k.startswith(("Wainscot", "Baseboard"))] == [
        "Wainscot_00", "Wainscot_01", "Baseboard_00", "Baseboard_01"]
    assert not stage.GetPrimAtPath("/World/Room/Wall_N/Wainscot_00")
    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ["default"])
    bb = cache.ComputeWorldBound(stage.GetPrimAtPath("/World/Room/Wall_S/Baseboard_00")).ComputeAlignedRange()
    assert (bb.GetMin()[1], bb.GetMax()[1], bb.GetMax()[2]) == pytest.approx((0.01, 0.02, 0.06))
    bn = cache.ComputeWorldBound(stage.GetPrimAtPath("/World/Room/Wall_N/Baseboard_00")).ComputeAlignedRange()
    assert (bn.GetMin()[1], bn.GetMax()[1]) == pytest.approx((2.99, 3.0))
    p = stage.GetPrimAtPath("/World/Room/Wall_S/Baseboard_00")
    assert p.HasAPI(UsdPhysics.CollisionAPI) and not p.HasAPI(UsdPhysics.RigidBodyAPI)
    assert p.GetCustomDataByKey("agentic_sim_world_gen:trim") == "baseboard"
    ps = UsdShade.Shader.Get(stage, "/World/Room/Looks/Baseboard/PreviewSurface")
    assert tuple(ps.GetInput("diffuseColor").Get()) == pytest.approx((0.1, 0.1, 0.1))
    # no trim: walls stay plain meshes as before
    plain = compose(ResolvedScene(spec=SceneSpec(name="t", room=_room()), placements=[]), tmp_path / "b.usda")
    assert plain.GetPrimAtPath("/World/Room/Wall_S").GetTypeName() == "Mesh"


def test_compose_plan_trim(tmp_path):
    pr = Room(plan_file=str(PLAN), baseboard=True)
    stage = compose(ResolvedScene(spec=SceneSpec(name="t", room=pr), placements=[]), tmp_path / "p.usda")
    n = sum(1 for p in stage.Traverse() if p.GetName().startswith("Baseboard_"))
    assert n >= len(RoomGeometry(pr).faces)


class _Fake:
    name = "f"

    def search(self, q, limit=10, size=None):
        return [AssetRecord(id="shelf", source="fake", uri="https://example.com/shelf.usd", name="shelf",
                            bbox_min=(-0.4, -0.175, 0), bbox_max=(0.4, 0.175, 1.8), semantic_class="bookcase",
                            license="CC-BY 4.0", front_deg=270.0)]


@pytest.fixture
def fake():
    service.set_provider(_Fake())
    yield
    service.set_provider(None)


def test_against_wall_stands_in_front_of_trim(tmp_path, fake):
    objs = [{"id": "a", "query": "bookshelf", "layout": {"against_wall": "west", "along": 0.0}}]
    st = service.create_scene({"name": "t", "room": {"width": 3.64, "depth": 2.73, "wainscot": True, "baseboard": True},
                               "objects": objs}, tmp_path / "t")
    room = service.load(tmp_path / "t").spec.room
    assert trim_depth(room, "west") == pytest.approx(0.02)
    o = {x["id"]: x for x in st["objects"]}["a"]
    # back 2 cm (WALL_GAP) in front of the 2 cm of trim; the end clear of the south wall's trim too
    assert o["position"] == pytest.approx((0.175 + 0.02 + 0.02, 0.4 + 0.02))
    assert st["issues"] == []


def test_set_room_preview_changes_license(tmp_path):
    d = tmp_path / "s"
    service.create_scene({"name": "m", "room": {"width": 4, "depth": 3}, "objects": []}, d)
    s0 = service.load(d)
    before = snapshot(s0, [])
    st = service.set_room(d, walls={"*": "paint_white", "N": "red brick"}, baseboard=True, wainscot={"height": 1.0})
    assert st["room"]["walls"]["*"] == "paint_white" and st["room"]["walls"]["N"]["mdl"]
    assert "walls:N" in st["surface_candidates"]
    s1 = service.load(d)
    lines = diff(before, snapshot(s1, []))
    assert any(ln.startswith("壁：白い塗装 → 白い塗装（北 MDL") for ln in lines)
    assert "巾木：なし → 白い巾木 0.06 m" in lines and "腰壁：なし → 明るい木目 1 m" in lines
    svg = render_svg(s1)
    assert "（北 custom）" in svg and "腰壁 1 m" in svg and "巾木 0.06 m" in svg
    r = service.license_report(d)
    assert "wall north" in {m["surface"] for m in r["materials"]}
    # another candidate for that one wall
    st = service.select_surface(d, "walls:N", 1)
    assert st["room"]["walls"]["N"]["mdl"] and st["room"]["walls"]["*"] == "paint_white"
    st = service.set_room(d, baseboard=False)
    assert st["room"]["baseboard"] is None
