"""The aisle width measured in a photo is what the layout aims at: offline."""

import pytest

from agentic_sim_world_gen import service
from agentic_sim_world_gen.layout import usability
from agentic_sim_world_gen.photo_measure import View
from agentic_sim_world_gen.scene.spec import PhotoCamera
from tests.test_layout import rec

CATALOG = {"shelf": [rec("shelf", "shelf", 0.9, 0.45, 1.8)]}
CAM = {"position": [4.0, 0.6, 1.5], "look_at": [4.0, 8.0, 1.0], "hfov_deg": 70}
VIEW = View(PhotoCamera.model_validate(CAM), (1600, 1200))


@pytest.fixture(autouse=True)
def fake():
    service.set_provider(type("P", (), {"name": "f", "search": lambda self, q, limit=10, size=None: CATALOG.get(q, [])})())
    yield
    service.set_provider(None)


def _pair(x0, x1, y=4.6):
    a, b = VIEW.project((x0, y, 0.0)), VIEW.project((x1, y, 0.0))
    return [a[0], a[1], b[0], b[1]]


def _rows(tmp_path, aisle=1.2, assumptions=()):
    """Two rows of shelves facing each other across `aisle`, centered on x = 4."""
    return service.create_scene({
        "name": "rows", "room": {"width": 8, "depth": 10, "height": 3.0, "place": "convenience_store"},
        "inputs": [{"id": "photo", "kind": "photo", "camera": CAM, "size_px": [1600, 1200]}],
        "assumptions": list(assumptions),
        "objects": [{"id": "rows", "query": "shelf", "position": [4.0, 5.0], "yaw_deg": 90,
                     "array": {"count": 5, "rows": 2, "aisle": aisle, "alternate": True}}]}, tmp_path / "rows")["scene_dir"]


def _walls(tmp_path, east=7.5):
    """Two separate rows (no array aisle to set): the scene's aisle is far wider than the photo's."""
    return service.create_scene({
        "name": "walls", "room": {"width": 8, "depth": 10, "height": 3.0, "place": "convenience_store"},
        "inputs": [{"id": "photo", "kind": "photo", "camera": CAM, "size_px": [1600, 1200]}],
        "objects": [
            {"id": "west", "query": "shelf", "position": [0.5, 5.0], "yaw_deg": 90, "array": {"count": 6}},
            {"id": "east", "query": "shelf", "position": [east, 5.0], "yaw_deg": 90, "array": {"count": 6}},
        ]}, tmp_path / "walls")["scene_dir"]


def test_without_aisle_pair_nothing_changes(tmp_path):
    d = _rows(tmp_path)
    m = service.measure_photo(d, floor_pairs=[_pair(3.0, 5.0)])
    assert "aisle" not in m and service.load(d).spec.photo_aisle is None
    assert "photo_aisle" not in service.status(d)


def test_array_aisle_follows_the_photo(tmp_path):
    d = _rows(tmp_path)  # 1.2 m, the default
    m = service.measure_photo(d, floor_pairs=[_pair(3.0, 5.0)], aisle_pair=0)  # the photo: 2.0 m
    a = m["aisle"]
    assert a["m"] == pytest.approx(2.0, abs=0.02)
    assert a["scene_m_was"] == pytest.approx(1.2, abs=0.01) and a["ratio_was"] == pytest.approx(0.6, abs=0.01)
    assert a["arrays"] == [{"object": "rows", "aisle_was": 1.2, "aisle": a["m"]}]
    assert a["scene_m"] == pytest.approx(2.0, abs=0.02) and a["ratio"] == pytest.approx(1.0, abs=0.01) and a["ok"]
    s = service.load(d)
    assert s.spec.objects[0].array.aisle == a["m"] and s.spec.photo_aisle.input == "photo"
    row = next(x for x in s.spec.assumptions if x.item == "aisle")
    assert not row.given and row.basis.startswith(service.PHOTO_AISLE_BASIS) and "m" in row.value
    st = service.status(d)["photo_aisle"]
    assert st["ok"] and st["ratio"] == pytest.approx(1.0, abs=0.01)
    assert not [i for i in usability.usability(s) if i.kind in ("photo_aisle", "wide_aisle")]


def test_an_aisle_at_the_photos_width_is_kept(tmp_path):
    d = _rows(tmp_path, aisle=1.97)
    a = service.measure_photo(d, floor_pairs=[_pair(3.0, 5.0)], aisle_pair=0)["aisle"]
    assert "arrays" not in a and a["ok"] and service.load(d).spec.objects[0].array.aisle == 1.97


def test_the_users_aisle_is_kept(tmp_path):
    d = _rows(tmp_path, assumptions=[{"item": "aisle", "value": "1.2 m", "given": True}])
    a = service.measure_photo(d, floor_pairs=[_pair(3.0, 5.0)], aisle_pair=0)["aisle"]
    assert "the user said" in a["kept"] and "scene_m" not in a
    s = service.load(d)
    assert s.spec.photo_aisle is None and s.spec.objects[0].array.aisle == 1.2


def test_issue_until_the_rows_are_moved(tmp_path):
    d = _walls(tmp_path)  # shelf fronts at 0.725 and 7.275: 6.55 m
    a = service.measure_photo(d, floor_pairs=[_pair(3.4, 4.6)], aisle_pair=0)["aisle"]  # the photo: 1.2 m
    assert a["scene_m"] == pytest.approx(6.55, abs=0.01) and a["ratio"] > 5 and not a["ok"]
    assert "closer" in a["next"] and "arrays" not in a
    issue = next(i for i in usability.usability(service.load(d)) if i.kind == "photo_aisle")
    assert set(issue.objects) == {"west", "east"} and "closer" in issue.detail
    assert not service.status(d)["photo_aisle"]["ok"]
    # move the rows to the photo's width: fronts at 3.4 and 4.6
    service.move_object(d, "west", position=[3.175, 5.0])
    service.move_object(d, "east", position=[4.825, 5.0])
    st = service.status(d)["photo_aisle"]
    assert st["ok"] and st["scene_m"] == pytest.approx(1.2, abs=0.01) and st["ratio"] == pytest.approx(1.0, abs=0.01)
    assert not [i for i in usability.usability(service.load(d)) if i.kind == "photo_aisle"]


def test_wide_aisle_compares_with_the_photo(tmp_path):
    # rows 2.6 m apart: wider than twice a convenience store's aisle, but the photo measured 2.6 m
    d = _rows(tmp_path, aisle=2.6)
    need = usability.aisle_width("convenience_store")
    assert 2.6 > usability.WIDE_AISLE * need
    before = [i for i in usability.usability(service.load(d)) if i.kind == "wide_aisle"]
    service.measure_photo(d, floor_pairs=[_pair(2.7, 5.3)], aisle_pair=0)
    after = [i for i in usability.usability(service.load(d)) if i.kind == "wide_aisle"]
    assert before and not after


def test_a_tight_real_aisle_lowers_the_walking_width(tmp_path):
    d = _rows(tmp_path, aisle=0.8)
    s = service.load(d)
    place = usability.aisle_width("convenience_store")
    assert usability.need_width(s) == place > 0.8
    service.measure_photo(d, floor_pairs=[_pair(3.6, 4.4)], aisle_pair=0)  # the photo: 0.8 m
    s = service.load(d)
    assert usability.need_width(s) == pytest.approx(0.8, abs=0.01)
    # saving does not widen it back to the place's 1.0 m (auto fix)
    assert s.spec.objects[0].array.aisle == pytest.approx(0.8, abs=0.01)
    assert not [i for i in usability.usability(s) if i.kind in ("narrow_aisle", "photo_aisle")]
    assert service.status(d)["photo_aisle"]["ratio"] == pytest.approx(1.0, abs=0.02)


def test_bad_index(tmp_path):
    d = _rows(tmp_path)
    m = service.measure_photo(d, floor_pairs=[[10, 10, 20, 10]], aisle_pair=0)  # above the horizon
    assert "error" in m["aisle"] and service.load(d).spec.photo_aisle is None
    assert "error" in service.measure_photo(d, floor_pairs=[_pair(3.0, 5.0)], aisle_pair=3)["aisle"]
