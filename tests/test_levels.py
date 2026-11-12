"""Raised floors, stairs and railings: offline."""

import math

import pytest

from agentic_sim_world_gen import service
from agentic_sim_world_gen.scene import levels as lv
from agentic_sim_world_gen.scene.spec import Room
from tests.test_layout import rec

CATALOG = {
    "shelf": [rec("shelf", "shelf", 1.0, 0.4, 2.0)],
    "table": [rec("table", "table", 1.2, 0.8, 0.75)],
}


@pytest.fixture(autouse=True)
def fake():
    service.set_provider(type("P", (), {"name": "f", "search": lambda self, q, limit=10, size=None: CATALOG.get(q, [])})())
    yield
    service.set_provider(None)


ROOM = {"width": 6, "depth": 10, "height": 3.0,
        "levels": [{"id": "back", "rect": [0, 6, 6, 10], "height": 0.45, "rail": ["south"]}],
        "stairs": [{"id": "up", "level": "back", "edge": "south", "offset": 4.5, "width": 1.2, "rail": "left"},
                   {"id": "to_2f", "at": [1, 0.5], "up": "north", "rise": 3.0, "width": 1.0, "color": "#f2f2f2"}]}


def test_flight_onto_a_level():
    r = Room.model_validate(ROOM)
    f = lv.flight(r, r.stairs[0])
    # 0.45 m in risers of at most 0.18: 3 risers, the last one is the level's edge, so 2 treads outside it
    assert f["count"] == 3 and f["treads"] == 2 and f["onto"]
    assert f["up"] == pytest.approx((0.0, 1.0))
    assert f["foot"] == pytest.approx((4.5, 6 - 2 * 0.28))
    assert [s["top"] for s in f["steps"]] == pytest.approx([0.15, 0.3])
    on = lv.flight(Room.model_validate({**ROOM, "stairs": [{"id": "s", "at": [3, 6.5], "up": "north", "rise": 1.0}]}), lv.Stairs(id="s", at=(3, 6.5), up="north", rise=1.0))
    assert on["base"] == pytest.approx(0.45) and on["steps"][0]["top"] == pytest.approx(0.45 + 1 / 6)
    g = lv.flight(r, r.stairs[1])
    assert g["count"] == 17 and g["treads"] == 17 and not g["onto"]
    assert g["steps"][-1]["top"] == pytest.approx(3.0)
    # the level's railing has a gap where the stairs come up
    bars = [b for b in lv.rails(r) if b["owner"] == "back" and b["id"].endswith("_bar")]
    spans = sorted((min(b["a"][0], b["b"][0]), max(b["a"][0], b["b"][0])) for b in bars)
    assert [v for s in spans for v in s] == pytest.approx([0.0, 3.9, 5.1, 6.0])
    assert all(b["a"][2] == pytest.approx(0.45 + lv.RAIL_H) for b in bars)
    # the stair's handrail follows the nosings, on the left going up (west)
    sb = next(b for b in lv.rails(r) if b["id"] == "up_left_bar")
    assert sb["a"][0] == pytest.approx(4.5 - 0.6 + lv.BAR / 2)
    assert sb["b"][2] - sb["a"][2] == pytest.approx(0.3)
    assert "段差 back 0.45 m・手すり" in lv.describe(r)


def test_bad_specs():
    with pytest.raises(ValueError, match="no level"):
        Room.model_validate({**ROOM, "stairs": [{"id": "s", "level": "nope", "edge": "south"}]})
    with pytest.raises(ValueError, match="outside the room"):
        Room.model_validate({**ROOM, "levels": [{"id": "x", "rect": [0, 6, 7, 10], "height": 0.3}], "stairs": []})
    with pytest.raises(ValueError, match="level \\+ edge"):
        Room.model_validate({**ROOM, "stairs": [{"id": "s", "up": "north"}]})
    with pytest.raises(ValueError, match="go outside the room"):
        Room.model_validate({**ROOM, "stairs": [{"id": "s", "at": [1, 8], "up": "north", "rise": 3.0}]})
    with pytest.raises(ValueError, match="no levels"):
        Room.model_validate({"outdoor": True, "width": 10, "depth": 10, "levels": ROOM["levels"]})


def test_objects_stand_on_the_level(tmp_path):
    st = service.create_scene({"name": "lv", "room": ROOM, "objects": [
        {"id": "shelf", "query": "shelf", "layout": {"against_wall": "east"}, "position": [5.7, 8.0]},
        {"id": "back_table", "query": "table", "position": [3.0, 8.0]},
        {"id": "front_table", "query": "table", "position": [3.0, 3.0]},
        {"id": "edge_table", "query": "table", "position": [2.0, 6.0]},
        {"id": "stair_table", "query": "table", "position": [4.5, 5.7]},
        {"id": "row", "query": "shelf", "position": [0.3, 6.5], "yaw_deg": 90, "array": {"count": 3}},
    ]}, tmp_path / "lv")
    z = {o["id"]: o.get("z", 0.0) for o in st["objects"]}
    assert z["back_table"] == pytest.approx(0.45) and z["front_table"] == pytest.approx(0.0)
    kinds = {(i["kind"], i["objects"][0]) for i in st["issues"]}
    assert ("on_step_edge", "edge_table") in kinds
    assert ("hits_stairs", "stair_table") in kinds
    # an array stands on the floor under its center: the unit below the level's edge is flagged, with a hint
    row = [i for i in st["issues"] if i["kind"] == "on_step_edge" and i["objects"][0].startswith("row.")]
    assert row and "split it" in row[0]["detail"]
    assert not any(o in ("back_table", "front_table") for _, o in kinds)
    # stairs into another level / in front of a door
    st = service.set_room(st["scene_dir"], stairs=[*ROOM["stairs"], {"id": "s3", "at": [2.5, 4.6], "up": "north", "rise": 1.0}],
                          openings=[{"id": "d", "kind": "door", "wall": "south", "offset": 1.0, "width": 0.9}])
    kinds = {(i["kind"], i["objects"][0]) for i in st["issues"]}
    assert ("stairs_into_level", "s3") in kinds and ("blocks_door", "to_2f") in kinds
    st2 = service.set_room(st["scene_dir"], stairs=[*ROOM["stairs"], {"id": "s4", "at": [3.0, 6.5], "up": "north", "rise": 1.0}])
    assert not any(i["objects"][0] == "s4" for i in st2["issues"])  # stairs standing on the level
    assert not any(o == "up" for _, o in kinds)
    # moved by hand onto the floor below: down to z = 0
    st = service.move_object(st["scene_dir"], "back_table", position=[3.0, 2.0])
    assert next(o for o in st["objects"] if o["id"] == "back_table").get("z", 0.0) == pytest.approx(0.0)


def test_usd(tmp_path):
    pxr = pytest.importorskip("pxr")
    d = service.create_scene({"name": "u", "room": ROOM, "objects": []}, tmp_path / "u")["scene_dir"]
    service.compose_scene(d)
    stage = pxr.Usd.Stage.Open(str(tmp_path / "u" / "scene.usda"))
    slab = stage.GetPrimAtPath("/World/Room/Levels/back")
    assert slab and slab.HasAPI(pxr.UsdPhysics.CollisionAPI)
    ext = pxr.UsdGeom.Mesh(slab).GetExtentAttr().Get()
    assert ext[1][2] == pytest.approx(0.45)
    steps = stage.GetPrimAtPath("/World/Room/Stairs/up").GetChildren()
    assert len(steps) == 2
    assert len(stage.GetPrimAtPath("/World/Room/Stairs/to_2f").GetChildren()) == 17
    bar = stage.GetPrimAtPath("/World/Room/Railings/up_left_bar")
    m = pxr.UsdGeom.Xformable(bar).ComputeLocalToWorldTransform(pxr.Usd.TimeCode.Default())
    # the bar's +X runs up the stairs: north and up
    v = m.TransformDir(pxr.Gf.Vec3d(1, 0, 0))
    assert v[1] > 0 and v[2] > 0 and abs(v[0]) < 1e-6
    assert math.degrees(math.atan2(v[2], v[1])) == pytest.approx(math.degrees(math.atan2(0.3, 0.56)))



def test_nav_blocks_the_step(tmp_path):
    from agentic_sim_world_gen import nav

    d = service.create_scene({"name": "n", "room": ROOM, "objects": []}, tmp_path / "n")["scene_dir"]
    g, _ = nav.build(service.load(d), nav.Robot(radius=0.2), 0.1)

    def at(x, y):
        i, j = g.cell((x, y))
        return g.cells[j][i], g.owner.get((i, j))

    assert at(2.0, 6.0)[0] == nav.OCCUPIED and at(2.0, 6.0)[1] in ("level:back", "rail:back")  # the edge
    assert at(4.5, 5.7)[1] == "stairs:up"
    assert at(1.0, 3.0)[1] == "stairs:to_2f"
    assert at(3.0, 3.0)[0] == nav.FREE and at(3.0, 8.0)[0] == nav.FREE  # on the level: drivable
