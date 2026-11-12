"""Several estimates of a photo's width side by side, the odd one named: offline."""

import json
import math

import pytest

from agentic_sim_world_gen import service
from agentic_sim_world_gen.photo_measure import View
from agentic_sim_world_gen.scene.spec import PhotoCamera
from tests.test_photo_tilt import SIZE, _edges, _scene, fake  # noqa: F401

np = pytest.importorskip("numpy")
LEVEL = {"position": [3.0, 0.5, 1.5], "look_at": [3.0, 8.0, 1.5], "hfov_deg": 70}


def _pair(view, y=4.5):
    a, b = view.project((1.0, y, 0.0)), view.project((5.0, y, 0.0))
    return [a[0], a[1], b[0], b[1]]


def test_one_estimate_cannot_be_checked(tmp_path):
    d = _scene(tmp_path, LEVEL)
    p = service.measure_photo(d, floor_pairs=[_pair(View(PhotoCamera(**LEVEL), SIZE))])
    fp = p["floor_pairs"][0]
    assert fp["single"] and fp["best_m"] == pytest.approx(4.0, abs=0.02)
    assert [e["by"] for e in fp["estimates"]] == ["geometry (camera as set)"]
    assert any("one estimate only" in n for n in p["notes"])


def test_two_apart_then_a_third_names_the_wrong_one(tmp_path):
    """The past failure: the camera's tilt 6 deg off. Its width is far from a count off the photo; with the
    photo's own tilt as the third estimate, the camera as set is the one named."""
    cv2 = pytest.importorskip("cv2")
    true = View(PhotoCamera(**LEVEL), SIZE, pitch_deg=-6.0)
    pair = _pair(true)
    d = _scene(tmp_path, LEVEL)  # no file: the camera as set (level) is all the geometry there is
    m = service.measure_photo(d, floor_pairs=[pair], known=[{"pair": 0, "m": 4.0, "from": "tiles"}])
    fp = m["floor_pairs"][0]
    assert fp["disagree"] and "outliers" not in fp
    assert any("neither can be called wrong" in n for n in m["notes"])

    img = np.full((SIZE[1], SIZE[0], 3), 235, np.uint8)
    for x1, y1, x2, y2 in _edges(true):
        cv2.line(img, (round(x1), round(y1)), (round(x2), round(y2)), (30, 30, 30), 3, cv2.LINE_AA)
    cv2.imwrite(str(tmp_path / "aisle.png"), img)
    d = _scene(tmp_path / "b", LEVEL, str(tmp_path / "aisle.png"))
    m = service.measure_photo(d, floor_pairs=[pair], known=[{"pair": 0, "m": 4.0, "from": "tiles"}])
    fp = m["floor_pairs"][0]
    assert fp["outliers"] == ["geometry (camera as set)"]
    assert fp["best_m"] == pytest.approx(4.0, abs=0.15)
    assert any("geometry (camera as set) is far from the others" in n and "set_input" in n for n in m["notes"])


def test_depth_map_is_an_estimate_without_the_tilt(tmp_path):
    true = View(PhotoCamera(**LEVEL), SIZE, pitch_deg=-6.0)
    pair = _pair(true)
    # a depth map of the floor as the true camera sees it (depth along the view axis), 1/4 size
    h, w = SIZE[1] // 4, SIZE[0] // 4
    dm = np.full((h, w), 20.0, np.float32)
    for j in range(h):
        for i in range(0, w):
            q = true.floor_point(i * 4 + 2, j * 4 + 2)
            if q is not None:
                dm[j, i] = sum((c - o) * f for c, o, f in zip((*q, 0.0), true.pos, true.fw))
    (tmp_path / "shot.png").write_bytes(b"")
    np.savez(tmp_path / "shot.depth.npz", depth=dm, meta=json.dumps({"size": list(SIZE), "hfov_deg": 70}))
    d = _scene(tmp_path, LEVEL, str(tmp_path / "shot.png"))
    m = service.measure_photo(d, floor_pairs=[pair], known=[{"pair": 0, "m": 4.1}])
    fp = m["floor_pairs"][0]
    by = {e["by"]: e for e in fp["estimates"]}
    assert by["depth map"]["m"] == pytest.approx(4.0, abs=0.1)
    assert by["depth map"]["range_m"][0] < 4.0 < by["depth map"]["range_m"][1]
    assert fp["outliers"] == ["geometry (camera as set)"] and fp["best_m"] == pytest.approx(4.05, abs=0.1)


def test_typical_aisle_of_the_store(tmp_path):
    d = _scene(tmp_path, LEVEL)
    service.set_room(d, place="supermarket")
    v = View(PhotoCamera(**LEVEL), SIZE)
    m = service.measure_photo(d, floor_pairs=[_pair(v)])
    fp = m["floor_pairs"][0]
    assert fp["typical_m"] == [1.8, 2.4] and fp["typical_off"]  # 4 m between gondolas is not a supermarket aisle
    assert any("outside what a supermarket usually has" in n for n in m["notes"])
    a, b = v.project((2.0, 4.5, 0.0)), v.project((4.0, 4.5, 0.0))
    fp = service.measure_photo(d, floor_pairs=[[a[0], a[1], b[0], b[1]]])["floor_pairs"][0]
    assert fp["best_m"] == pytest.approx(2.0, abs=0.02) and not fp["typical_off"]
    assert math.isclose(fp["scene_vs_best"], fp["scene_m"] / fp["best_m"], abs_tol=0.01)
