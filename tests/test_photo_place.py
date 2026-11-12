"""Furniture poses from the located feet, no network."""
import json
import math

import pytest

from tests.test_photo_fit import _request
from tests.test_photo_placement import CATALOG, DESK, _feet
from agentic_sim_world_gen import service
from agentic_sim_world_gen.photo_place import fit_pose


@pytest.fixture(autouse=True)
def fake():
    service.set_provider(type("P", (), {"name": "f", "search": lambda self, q, limit=10, size=None: CATALOG.get(q, [])})())
    yield
    service.set_provider(None)


def corners(c, facing, along, across):
    """{label: (xy, edge)} of a footprint: fl = the front corner on the object's left."""
    t = math.radians(facing)
    out = {}
    for name, u, v in (("fl", 1, 1), ("fr", 1, -1), ("bl", -1, 1), ("br", -1, -1)):
        x, y = u * along / 2, v * across / 2
        out[name] = ((c[0] + x * math.cos(t) - y * math.sin(t), c[1] + x * math.sin(t) + y * math.cos(t)),
                     "front" if u > 0 else "back")
    return out


def ang(a, b):
    return abs((a - b + 180) % 360 - 180)


def test_four_corners_set_center_and_axis():
    k = corners((2.0, 1.0), 30, 0.6, 1.2)
    r = fit_pose([(p, None) for p, _ in k.values()], (0.6, 1.2), facing_now=20)
    assert math.dist(r["center"], (2.0, 1.0)) < 0.01 and r["rms_m"] < 0.01
    # unlabeled corners cannot tell front from back: the facing nearest the one now is kept
    assert ang(r["facing_deg"], 30) < 1 and r["facing_from"] == "kept" and len(r["ties"]) == 2
    r = fit_pose([(p, None) for p, _ in k.values()], (0.6, 1.2), facing_now=200)
    assert ang(r["facing_deg"], 210) < 1


def test_front_feet_give_the_center_not_the_front_edge():
    k = corners((2.0, 1.0), 90, 0.6, 1.2)  # faces north
    pts = [k["fl"], k["fr"]]
    # the camera that saw the front stands north of it
    r = fit_pose(pts, (0.6, 1.2), facing_now=0, front_cams=[(2.0, 3.0)])
    assert math.dist(r["center"], (2.0, 1.0)) < 0.01 and ang(r["facing_deg"], 90) < 1
    assert r["facing_from"] == "cameras"
    # the mean of the points would be the middle of the front edge, 0.3 m off
    assert math.dist(((pts[0][0][0] + pts[1][0][0]) / 2, (pts[0][0][1] + pts[1][0][1]) / 2), (2.0, 1.0)) > 0.29


def test_front_and_back_feet_tell_the_front():
    k = corners((1.0, 2.0), -45, 0.5, 1.6)
    r = fit_pose([k["fl"], k["br"]] + [k["fr"]], (0.5, 1.6), facing_now=135)
    assert ang(r["facing_deg"], -45) < 1 and r["facing_from"] == "points"


def test_inset_feet_and_one_point():
    k = corners((1.0, 1.0), 0, 0.56, 1.16)  # legs 2 cm in from the top's corners
    r = fit_pose([(p, e) for p, e in k.values()], (0.6, 1.2), facing_now=0)
    assert math.dist(r["center"], (1.0, 1.0)) < 0.01 and 0.02 < r["rms_m"] < 0.04
    assert "error" in fit_pose([k["fl"]], (0.6, 1.2))


def _scene(tmp_path, edge=True):
    req = _request()
    feet = _feet("w") + _feet("ne")
    for lo in feet:  # the fake desk's front is -y: the south feet are its front
        if edge:
            lo["edge"] = "front" if lo["name"] in ("sw", "se") else "back"
    req["locate"] = feet
    d = tmp_path / "s"
    assert service.fit_photos(req, d)["ok"]
    objects = [{"id": "desk", "query": "desk", "layout": {"against_wall": "west"}},
               {"id": "wardrobe", "query": "wardrobe", "layout": {"against_wall": "north"}}]
    service.create_scene({"name": "t", "room": {"width": 4.5, "depth": 3.6, "height": 2.4}, "objects": objects}, d)
    return d


def test_place_from_photos_moves_and_records(tmp_path):
    d = _scene(tmp_path)
    assert service.status(d)["photo_placement"]["misplaced"][0]["id"] == "desk"
    r = service.place_from_photos(d, apply=False)
    assert r["ok"] and not r["applied"] and service.load(d).obj("desk").layout is not None
    r = service.place_from_photos(d)
    assert r["applied"], r
    p = r["placed"][0]
    assert p["id"] == "desk" and math.dist(p["position"], DESK) < 0.03 and ang(p["facing_deg"], -90) < 2
    assert p["facing_from"] == "points" and p["points"] == 4 and p["moved_m"] > 1
    o = service.load(d).obj("desk")
    assert o.layout is None and math.dist(o.position, DESK) < 0.03 and ang(o.facing_deg, -90) < 2
    a = [a for a in service.load(d).spec.assumptions if a.object == "desk"]
    assert a[0].item == "位置" and a[0].basis == "写真から当てはめ" and "南向き" in a[0].value
    pp = service.status(d).get("photo_placement", {})
    assert "misplaced" not in pp
    assert pp.get("unlocated") == ["wardrobe"]  # still not located: the check keeps asking


def test_place_from_photos_without_edges_keeps_the_facing(tmp_path):
    d = _scene(tmp_path, edge=False)
    service.move_object(d, "desk", position=(1.0, 2.0), facing_deg=100)
    p = service.place_from_photos(d)["placed"][0]
    assert ang(p["facing_deg"], 90) < 2 and p["facing_from"] == "kept"


def test_place_from_photos_skips(tmp_path):
    d = _scene(tmp_path)
    r = service.place_from_photos(d, objects=["wardrobe", "nope"], apply=True)
    assert not r["applied"] and {s["id"] for s in r["skipped"]} == {"wardrobe", "nope"}
    (d / "photo_fit.json").unlink()
    assert not service.place_from_photos(d)["ok"]


def test_misfit_warns(tmp_path):
    d = _scene(tmp_path)
    f = d / "photo_fit.json"
    j = json.loads(f.read_text())
    for lo in j["result"]["located"]:  # the real desk is twice as long as the asset
        if "xy" in lo:
            lo["xy"] = [DESK[0] + 2 * (lo["xy"][0] - DESK[0]), lo["xy"][1]]
    f.write_text(json.dumps(j))
    r = service.place_from_photos(d, apply=False)
    assert any("not the size of the real one" in w for w in r["warnings"]), r["warnings"]
