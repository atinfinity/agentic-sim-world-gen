"""Columns and floor areas: offline."""

import pytest

from agentic_sim_world_gen import service
from agentic_sim_world_gen.scene.columns import Column, describe
from agentic_sim_world_gen.scene.spec import Room
from tests.test_layout import rec

CATALOG = {"table": [rec("table", "table", 1.2, 0.8, 0.75)]}


@pytest.fixture(autouse=True)
def fake():
    service.set_provider(type("P", (), {"name": "f", "search": lambda self, q, limit=10, size=None: CATALOG.get(q, [])})())
    yield
    service.set_provider(None)


ROOM = {"width": 10, "depth": 8, "height": 3.0,
        "columns": [{"id": "c1", "at": [3, 4], "size": 0.6},
                    {"id": "c2", "at": [7, 4], "diameter": 0.5, "color": "#e8e8e8"}],
        "floor_areas": [{"id": "ent", "name": "入口", "rect": [4, 0, 6, 1.5], "floor": "tile_white"},
                        {"id": "rug", "polygon": [[1, 6], [3, 6], [3, 7.5], [1, 7.5]], "color": "#704030"}]}


def test_shapes_and_describe():
    r = Room.model_validate(ROOM)
    assert len(r.columns[1].poly) == 24 and r.columns[0].dims == (0.6, 0.6)
    c = Column(id="w", at=(1, 1), size=(1.0, 0.4), yaw_deg=90)
    xs = [p[0] for p in c.poly]
    assert max(xs) - min(xs) == pytest.approx(0.4)
    assert r.columns[0].top(3.0) == 3.0
    d = describe(r)
    assert d["columns"] == "2 本（0.6 m 角・直径 0.5 m）"
    assert d["floor_areas"].startswith("入口：")


def test_bad_specs():
    with pytest.raises(ValueError, match="outside the room"):
        Room.model_validate({**ROOM, "columns": [{"id": "c", "at": [11, 4]}]})
    with pytest.raises(ValueError, match="duplicate"):
        Room.model_validate({**ROOM, "columns": [{"id": "ent", "at": [2, 2]}]})
    with pytest.raises(ValueError, match="not both"):
        Room.model_validate({**ROOM, "columns": [{"id": "c", "at": [2, 2], "size": 0.5, "diameter": 0.5}]})
    with pytest.raises(ValueError, match="goes outside"):
        Room.model_validate({**ROOM, "floor_areas": [{"id": "a", "rect": [9, 0, 11, 1], "floor": "tile_white"}]})
    with pytest.raises(ValueError, match="floor .* or color"):
        Room.model_validate({**ROOM, "floor_areas": [{"id": "a", "rect": [0, 0, 1, 1]}]})
    with pytest.raises(ValueError, match="unknown surface preset"):
        Room.model_validate({**ROOM, "floor_areas": [{"id": "a", "rect": [0, 0, 1, 1], "floor": "nope"}]})
    with pytest.raises(ValueError, match="no columns"):
        Room.model_validate({"outdoor": True, "width": 10, "depth": 10, "columns": ROOM["columns"]})


def test_objects_hit_columns_and_nav(tmp_path):
    from agentic_sim_world_gen import nav

    st = service.create_scene({"name": "col", "room": ROOM, "objects": [
        {"id": "hit", "query": "table", "position": [3.2, 4.0]},
        {"id": "free", "query": "table", "position": [5.0, 6.0]},
    ]}, tmp_path / "col")
    kinds = {(i["kind"], i["objects"][0]) for i in st["issues"]}
    assert ("hits_column", "hit") in kinds
    assert not any(o == "free" for _, o in kinds)
    g, _ = nav.build(service.load(st["scene_dir"]), nav.Robot(radius=0.2), 0.1)
    i, j = g.cell((7.0, 4.0))
    assert g.cells[j][i] == nav.OCCUPIED and g.owner.get((i, j)) == "column:c2"
    i, j = g.cell((5.0, 1.0))
    assert g.cells[j][i] == nav.FREE  # a floor area is still floor
    # set_room replaces the lists, [] removes them; the change history says so
    st = service.set_room(st["scene_dir"], columns=[], floor_areas=[])
    assert not any(i["kind"] == "hits_column" for i in st["issues"])
    assert not service.load(st["scene_dir"]).spec.room.columns


def test_changes_line():
    from agentic_sim_world_gen.preview.changes import diff, snapshot
    from agentic_sim_world_gen.scene.spec import ResolvedScene, SceneSpec

    a = ResolvedScene(spec=SceneSpec(name="x", room=Room.model_validate({"width": 10, "depth": 8})), placements=[])
    b = ResolvedScene(spec=SceneSpec(name="x", room=Room.model_validate(ROOM)), placements=[])
    lines = diff(snapshot(a, []), snapshot(b, []))
    assert any("柱" in s and "2 本" in s for s in lines)
    assert any("床の一部" in s for s in lines)


def test_usd_and_svg(tmp_path):
    pxr = pytest.importorskip("pxr")
    d = service.create_scene({"name": "u", "room": ROOM, "objects": []}, tmp_path / "u")["scene_dir"]
    service.compose_scene(d)
    stage = pxr.Usd.Stage.Open(str(tmp_path / "u" / "scene.usda"))
    col = stage.GetPrimAtPath("/World/Room/Columns/c2")
    assert col and col.HasAPI(pxr.UsdPhysics.CollisionAPI)
    ext = pxr.UsdGeom.Mesh(col).GetExtentAttr().Get()
    assert ext[1][2] == pytest.approx(3.0) and ext[1][0] - ext[0][0] == pytest.approx(0.5, abs=0.01)
    tops = {n: pxr.UsdGeom.Mesh(stage.GetPrimAtPath(f"/World/Room/FloorAreas/{n}")).GetExtentAttr().Get()[1][2]
            for n in ("ent", "rug")}
    assert tops["rug"] == pytest.approx(0.0) and tops["ent"] == pytest.approx(-0.001)  # later ones lie over earlier ones
    floor = pxr.UsdGeom.Xformable(stage.GetPrimAtPath("/World/Room/Floor"))
    assert floor  # the room floor sinks under the areas
    svg = (tmp_path / "u" / "layout.svg").read_text(encoding="utf-8")
    assert "柱 c1" in svg and "入口" in svg
