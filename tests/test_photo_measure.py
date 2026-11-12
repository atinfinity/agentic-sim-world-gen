"""Room and aisle widths from a photo's camera: offline."""

import pytest

from agentic_sim_world_gen import service
from agentic_sim_world_gen.photo_measure import View
from agentic_sim_world_gen.scene.spec import PhotoCamera
from tests.test_layout import rec

CATALOG = {"shelf": [rec("shelf", "shelf", 0.9, 0.45, 1.8)], "table": [rec("table", "table", 1.2, 0.8, 0.75)]}


@pytest.fixture(autouse=True)
def fake():
    service.set_provider(type("P", (), {"name": "f", "search": lambda self, q, limit=10, size=None: CATALOG.get(q, [])})())
    yield
    service.set_provider(None)


CAM = {"position": [3.0, 0.6, 1.5], "look_at": [3.0, 8.0, 1.0], "hfov_deg": 70}


def _scene(tmp_path):
    return service.create_scene({
        "name": "aisle", "room": {"width": 6, "depth": 10, "height": 3.0},
        "inputs": [{"id": "photo", "kind": "photo", "camera": CAM, "size_px": [1600, 1200]}],
        "objects": [
            {"id": "west", "query": "shelf", "position": [0.5, 5.0], "yaw_deg": 90, "array": {"count": 6}},
            {"id": "east", "query": "shelf", "position": [5.5, 5.0], "yaw_deg": 90, "array": {"count": 6}},
        ]}, tmp_path / "aisle")["scene_dir"]


def test_widths_and_units(tmp_path):
    d = _scene(tmp_path)
    m = service.measure_photo(d)
    assert m["ok"] and m["input"] == "photo"
    # 6 shelf units of 0.9 m from y = 2.3 to 7.7: the camera at y = 0.6 stands between the walls
    s = m["camera"]["stands"]
    assert s["left"] == pytest.approx(3.0, abs=0.01) and s["left_by"] == "wall:west"
    assert s["right"] == pytest.approx(3.0, abs=0.01) and s["right_by"] == "wall:east"
    assert m["ahead"]["blocked_by"] == "wall:north"
    # 3.5 m ahead (y = 4.1): between the shelf fronts (0.5 ± 0.225, 5.5 ± 0.225)
    row = next(r for r in m["across"] if r["ahead_m"] == 3.5)
    assert row["width"] == pytest.approx(5.0 - 0.45, abs=0.01)
    assert row["left_by"].startswith("west.") and row["right_by"].startswith("east.")
    assert set(m["arrays_in_view"]) == {"west", "east"}
    assert all(o["box_px"][0] < o["box_px"][2] for o in m["objects"])
    assert any("in view" in n for n in m["notes"])
    # looking 3.9 deg down: level lines meet above the image center
    assert m["camera"]["horizon_v_px"] == pytest.approx(600 - 1142.6 * 0.5 / 7.4, abs=2)


def test_floor_pairs(tmp_path):
    d = _scene(tmp_path)
    v = View(PhotoCamera.model_validate(CAM), (1600, 1200))
    # the bottoms of the two shelf fronts 4 m ahead, as read off the photo, but 1.2 m apart (a narrower aisle)
    a, b = v.project((2.4, 4.6, 0.0)), v.project((3.6, 4.6, 0.0))
    m = service.measure_photo(d, floor_pairs=[[a[0], a[1], b[0], b[1]], [10, 10, 20, 10]])
    p = m["floor_pairs"][0]
    assert p["photo_m"] == pytest.approx(1.2, abs=0.01)
    assert p["ahead_m"] == pytest.approx(4.0, abs=0.01)
    assert p["scene_m"] == pytest.approx(4.55, abs=0.01) and p["ratio"] > 3
    assert "error" in m["floor_pairs"][1]  # above the horizon
    assert any("wider in the scene" in n for n in m["notes"])


def test_table_splits_the_aisle(tmp_path):
    d = _scene(tmp_path)
    service.add_object(d, {"id": "island", "query": "table", "position": [3.0, 4.1]})
    m = service.measure_photo(d)
    # the table in the middle does not stop the rows ahead
    assert [r["ahead_m"] for r in m["across"]] == [1.0, 2.0, 3.5, 5.0, 7.0]
    row = next(r for r in m["across"] if r["ahead_m"] == 3.5)
    assert row["width"] is None and row["on"] == "island"
    left, right = row["gaps"]
    assert left["width"] == pytest.approx(2.4 - 0.725, abs=0.01) and left["right_by"] == "island"
    assert right["width"] == pytest.approx(5.275 - 3.6, abs=0.01) and right["right_by"].startswith("east.")
    # a pixel pair from shelf front to shelf front across the table: the table between does not count
    v = View(PhotoCamera.model_validate(CAM), (1600, 1200))
    a, b = v.project((0.725, 4.1, 0.0)), v.project((5.275, 4.1, 0.0))
    c = v.project((5.275, 6.0, 0.0))
    p, q = service.measure_photo(d, floor_pairs=[[a[0], a[1], b[0], b[1]], [a[0], a[1], c[0], c[1]]])["floor_pairs"]
    assert p["scene_m"] == pytest.approx(4.55, abs=0.02) and p["ratio"] == pytest.approx(1.0, abs=0.01)
    assert p["scene_by"][0].startswith("west.") and p["scene_by"][1].startswith("east.") and "warning" not in p
    assert "same depth" in q["warning"]


def test_no_camera(tmp_path):
    d = service.create_scene({"name": "x", "room": {"width": 3, "depth": 3}, "objects": []}, tmp_path / "x")["scene_dir"]
    assert not service.measure_photo(d)["ok"]
