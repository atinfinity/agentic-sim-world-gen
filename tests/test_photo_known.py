"""Things of known size in the photo set its scale: offline, pinhole photos made from a true camera."""

import pytest

from agentic_sim_world_gen import service
from agentic_sim_world_gen.photo_measure import KNOWN_SIZES, View
from agentic_sim_world_gen.scene.spec import PhotoCamera
from tests.test_photo_tilt import SIZE, _scene

SET = {"position": [3.0, 0.5, 1.5], "look_at": [3.0, 8.0, 1.5], "hfov_deg": 70}  # the camera as somebody set it
TRUE_W = 2.4


def _true(h=1.5, pitch=0.0):
    return View(PhotoCamera(position=[3.0, 0.5, h], look_at=[3.0, 8.0, h], hfov_deg=70), SIZE, pitch_deg=pitch)


def _px(v, a, b):
    p, q = v.project(a), v.project(b)
    return [p[0], p[1], q[0], q[1]]


def _aisle(v):  # a 2.4 m aisle 4 m ahead
    return _px(v, (1.8, 4.5, 0.0), (4.2, 4.5, 0.0))


def _unit(v, y=4.9):  # one 0.9 m unit facing the camera
    return _px(v, (2.0, y, 0.0), (2.9, y, 0.0))


def _known(fp):
    return [e for e in fp["estimates"] if e["by"].startswith("known size")]


def test_a_unit_in_the_photo_fixes_the_camera_height(tmp_path):
    """The camera was 1.3 m high, set as 1.5: the geometry reads the aisle 15 % wide, the unit puts it right."""
    v = _true(h=1.3)
    m = service.measure_photo(_scene(tmp_path, SET), floor_pairs=[_aisle(v)], known=[{"kind": "shelf_unit", "line": _unit(v)}])
    fp = m["floor_pairs"][0]
    assert fp["photo_m"] == pytest.approx(TRUE_W * 1.5 / 1.3, abs=0.02)
    (k,) = _known(fp)
    assert k["m"] == pytest.approx(TRUE_W, abs=0.02) and k["range_m"][0] < TRUE_W < k["range_m"][1] and not k.get("loose")
    r = m["known"][0]
    assert r["size_m"] == 0.9 and r["scale"] == pytest.approx(1.3 / 1.5, abs=0.01)
    assert m["scale"]["camera_height_m"] == pytest.approx(1.3, abs=0.02)
    assert any("1.3 m high, not 1.5 m" in n for n in m["notes"])
    assert not fp.get("single")


def test_the_tilt_off_too(tmp_path):
    """Tilt 6 deg off and the height 0.2 m: the geometry is 72 % wide, the unit near the pair's depth within 6 %."""
    v = _true(h=1.3, pitch=-6.0)
    m = service.measure_photo(_scene(tmp_path, SET), floor_pairs=[_aisle(v)], known=[{"kind": "shelf_unit", "line": _unit(v)}])
    fp = m["floor_pairs"][0]
    assert fp["photo_m"] > 1.6 * TRUE_W
    assert _known(fp)[0]["m"] == pytest.approx(TRUE_W, rel=0.06)
    assert fp["disagree"]


def test_a_far_thing_running_away_is_listed_not_counted(tmp_path):
    v = _true(pitch=-3.0)
    side = _px(v, (4.2, 7.0, 0.0), (4.2, 7.9, 0.0))  # a unit along the aisle, 7 m ahead
    m = service.measure_photo(_scene(tmp_path, SET), floor_pairs=[_aisle(v)], known=[{"kind": "棚", "line": side}])
    fp = m["floor_pairs"][0]
    assert m["known"][0]["runs_away"] and _known(fp)[0]["loose"]
    assert fp["single"] and fp["best_m"] == fp["photo_m"]
    assert any("runs away from the camera" in n for n in m["notes"]) and any("too loose to count" in n for n in m["notes"])


def test_counted_across(tmp_path):
    """So many units / tiles across the aisle: the server multiplies. A tile's size is taken from the geometry
    unless given, and then it is not an estimate of its own."""
    v = _true(h=1.3)
    d = _scene(tmp_path, SET)
    fp = service.measure_photo(d, floor_pairs=[_aisle(v)], known=[{"pair": 0, "kind": "floor_tile", "count": 8, "size_m": 0.3}])["floor_pairs"][0]
    (k,) = _known(fp)
    assert k["m"] == 2.4 and "tile_from" not in k and not fp.get("single")
    m = service.measure_photo(d, floor_pairs=[_aisle(v)], known=[{"pair": 0, "kind": "tiles", "count": 8}])
    fp = m["floor_pairs"][0]
    (k,) = _known(fp)
    assert k["tile_from"].startswith("geometry") and k["choices_m"] == {"0.3": 2.4, "0.45": 3.6, "0.6": 4.8}
    assert fp["single"] and any("give size_m" in n for n in m["notes"])
    fp = service.measure_photo(d, floor_pairs=[_aisle(v)], known=[{"pair": 0, "kind": "shelf_unit", "count": 2.5}])["floor_pairs"][0]
    assert _known(fp)[0]["m"] == 2.25 and _known(fp)[0]["range_m"] == [round(2.5 * KNOWN_SIZES["shelf_unit"][1], 2), round(2.5 * 0.95, 2)]
    # the width itself, as before
    fp = service.measure_photo(d, floor_pairs=[_aisle(v)], known=[{"pair": 0, "m": 2.4, "from": "tiles"}])["floor_pairs"][0]
    assert _known(fp)[0] == {"by": "known size (tiles)", "m": 2.4}


def test_box_two_things_apart_and_errors(tmp_path):
    v = _true()
    u = _unit(v)
    box = [u[0], u[1] - 300, u[2], u[1]]  # the unit's pixel box: its bottom edge is on the floor
    door = _px(v, (2.0, 4.9, 0.0), (3.2, 4.9, 0.0))  # 1.2 m called a door: one of the two is misread
    m = service.measure_photo(_scene(tmp_path, SET), floor_pairs=[_aisle(v)],
                              known=[{"kind": "shelf_unit", "box": box}, {"kind": "door", "line": door},
                                     {"kind": "sofa", "line": u}, {"kind": "cart", "line": [800, 100, 900, 100]}])
    r = m["known"]
    assert r[0]["scale"] == pytest.approx(1.0, abs=0.01) and r[1]["scale"] == pytest.approx(0.85 / 1.2, abs=0.01)
    assert "unknown kind" in r[2]["error"] and "above the horizon" in r[3]["error"]
    assert m["scale"]["apart"] and any("one of them is misread" in n for n in m["notes"])
    assert len(_known(m["floor_pairs"][0])) == 2
