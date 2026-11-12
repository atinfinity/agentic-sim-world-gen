"""Furniture located in the photos vs where the scene put it, no network."""
import json
import math

import pytest

from tests.test_layout import rec
from tests.test_photo_fit import _proj, _request
from agentic_sim_world_gen import service
from agentic_sim_world_gen.photo_fit import located_objects

CATALOG = {"desk": [rec("desk", "desk", 1.2, 0.6, 0.74)], "wardrobe": [rec("wardrobe", "wardrobe", 1.0, 0.6, 1.8)]}
DESK = (3.3, 0.5)  # where the photos show the desk: its feet are at ±0.55, ±0.25 around it


@pytest.fixture(autouse=True)
def fake():
    service.set_provider(type("P", (), {"name": "f", "search": lambda self, q, limit=10, size=None: CATALOG.get(q, [])})())
    yield
    service.set_provider(None)


def _feet(cid, c=DESK):
    out = []
    for name, (dx, dy) in {"sw": (-0.55, -0.25), "se": (0.55, -0.25), "ne": (0.55, 0.25), "nw": (-0.55, 0.25)}.items():
        if q := _proj(cid, (c[0] + dx, c[1] + dy, 0.0)):
            out.append({"name": name, "photo": cid, "px": q, "object": "desk"})
    return out


def test_fit_sums_up_each_object():
    req = _request()
    req["locate"] = _feet("w") + _feet("ne")
    r = service.fit_photos(req)
    assert r["ok"]
    o = r["objects"]["desk"]
    assert math.dist(o["center"], DESK) < 0.03 and set(o["photos"]) == {"ne", "w"} and len(o["points"]) == 4
    assert o["axis_deg"] < 2 or o["axis_deg"] > 178  # the desk runs along x
    assert abs(o["extent_m"][0] - 1.1) < 0.03 and abs(o["extent_m"][1] - 0.5) < 0.03
    assert not any("one point" in w for w in r["warnings"])
    assert not any("apart between photos" in w for w in r["warnings"])


def test_fit_warns_when_photos_disagree():
    req = _request()
    wrong = _feet("ne", (DESK[0] - 1.0, DESK[1] + 0.5))  # a click on another object in this photo
    req["locate"] = _feet("w") + wrong
    r = service.fit_photos(req)
    assert any("of desk is" in w and "apart between photos" in w for w in r["warnings"]), r["warnings"]


def test_located_objects_skips_errors_and_unnamed():
    objs, far = located_objects([{"name": "a", "photo": "p", "xy": [1, 1], "object": "x"},
                                 {"name": "a", "photo": "q", "xy": [1.2, 1], "object": "x"},
                                 {"name": "b", "error": "no photo", "object": "x"},
                                 {"name": "c", "photo": "p", "xy": [5, 5]}])
    assert objs["x"]["center"] == [1.1, 1.0] and objs["x"]["points"] == {"a": [1.1, 1.0]} and far == []
    assert objs["x"]["photos"] == ["p", "q"] and objs["x"]["note"].startswith("one point")


def _scene(tmp_path, desk_pos):
    req = _request()
    req["locate"] = _feet("w") + _feet("ne")
    d = tmp_path / "s"
    assert service.fit_photos(req, d)["ok"]
    objects = [{"id": "desk", "query": "desk", "position": list(desk_pos), "yaw_deg": 0},
               {"id": "wardrobe", "query": "wardrobe", "position": list(DESK), "yaw_deg": 0}]
    service.create_scene({"name": "t", "room": {"width": 4.5, "depth": 3.6, "height": 2.4}, "objects": objects}, d)
    return d


def test_status_flags_a_misplaced_object(tmp_path):
    d = _scene(tmp_path, (1.0, 2.8))  # a relation put the desk elsewhere, the wardrobe where the desk is
    st = service.status(d)
    pp = st["photo_placement"]
    m = pp["misplaced"][0]
    assert m["id"] == "desk" and m["off_m"] > 1 and m["there"] == ["wardrobe"]
    assert "place_from_photos" in pp["hint"]
    w = service._placement_warnings(service.load(d), d)
    assert w and "desk is placed at" in w[0] and "wardrobe stands there" in w[0]


def test_status_quiet_when_placed_at_the_feet(tmp_path):
    d = _scene(tmp_path, (DESK[0] + 0.1, DESK[1]))
    service.remove_object(d, "wardrobe")
    assert "photo_placement" not in service.status(d)


def test_located_unknown(tmp_path):
    d = _scene(tmp_path, DESK)
    f = d / "photo_fit.json"
    j = json.loads(f.read_text())
    for lo in j["result"]["located"]:
        lo["object"] = "table"
    f.write_text(json.dumps(j))
    assert service.status(d)["photo_placement"]["located_unknown"] == ["table"]


def test_fit_warns_without_located_furniture():
    req = _request()
    req["locate"] = []
    r = service.fit_photos(req)
    assert any(w.startswith("no furniture located") for w in r["warnings"])
    req["locate"] = _feet("w")
    assert not any(w.startswith("no furniture located") for w in service.fit_photos(req)["warnings"])


def test_fit_warns_on_one_point():
    req = _request()
    req["locate"] = _feet("w")[:1]
    r = service.fit_photos(req)
    assert any("desk rest on one point" in w for w in r["warnings"]), r["warnings"]


def test_status_lists_unlocated_floor_furniture(tmp_path):
    d = _scene(tmp_path, DESK)  # the wardrobe stands on the floor but nothing locates it
    pp = service.status(d)["photo_placement"]
    assert pp["unlocated"] == ["wardrobe"] and "misplaced" not in pp and "own id" in pp["hint"]
    service.set_layout(d, "wardrobe", {"on": "desk"})  # on something: not floor furniture
    assert "photo_placement" not in service.status(d)


def test_compose_waits_for_the_photos(tmp_path):
    d = _scene(tmp_path, (1.0, 2.8))  # the desk is misplaced and the wardrobe not located
    with pytest.raises(ValueError, match="misplaced: desk; unlocated: wardrobe"):
        service.compose_scene(d)
    service.move_object(d, "desk", position=list(DESK), facing_deg=0)
    with pytest.raises(ValueError, match="unlocated: wardrobe"):
        service.compose_scene(d)
    r = service.compose_scene(d, not_in_photos={"wardrobe": "behind the camera in every frame"})
    assert r["usd"].endswith("scene.usda")
    assert service.status(d)["photo_placement"] == {"not_in_photos": {"wardrobe": "behind the camera in every frame"}}
    service.compose_scene(d, log=False)  # the preview's rewrite never stops
    with pytest.raises(ValueError, match="unlocated: wardrobe"):
        service.compose_scene(d, not_in_photos={"wardrobe": ""})  # removed again
    assert service.status(d)["photo_placement"]["unlocated"] == ["wardrobe"]


def test_one_point_is_listed(tmp_path):
    req = _request()
    req["locate"] = _feet("w")[:1]
    d = tmp_path / "s"
    assert service.fit_photos(req, d)["ok"]
    service.create_scene({"name": "t", "room": {"width": 4.5, "depth": 3.6, "height": 2.4},
                          "objects": [{"id": "desk", "query": "desk", "position": list(DESK), "yaw_deg": 0}]}, d)
    pp = service.status(d)["photo_placement"]
    assert pp["one_point"] == ["desk"] and "diagonal" in pp["hint"]
    with pytest.raises(ValueError, match="one_point: desk"):
        service.compose_scene(d)
