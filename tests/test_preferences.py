"""The user's corrections remembered as preferences: 2 scenes with the same correction -> the 3rd starts with it."""

import json
import os

import pytest

from agentic_sim_world_gen import preferences, service
from tests.test_layout import rec

CATALOG = {
    "desk": [rec("desk", "desk", 1.4, 0.7, 0.74)],
    "office chair": [rec("chair_a", "office chair", 0.55, 0.55, 0.85), rec("chair_b", "office chair", 0.6, 0.6, 0.9)],
    "rack": [rec("rack", "rack", 2.0, 0.8, 2.0)],
}


@pytest.fixture(autouse=True)
def fake():
    service.set_provider(type("P", (), {"name": "f", "search": lambda self, q, limit=10, size=None: CATALOG.get(q, [])})())
    yield
    service.set_provider(None)


def make(tmp_path, name, place="home", **room):
    return service.create_scene({"name": name, "room": {"width": 4, "depth": 3, "place": place, **room}, "objects": [
        {"id": "desk", "label": "机", "query": "desk", "layout": {"against_wall": "north"}},
        {"id": "chair", "label": "椅子", "query": "office chair", "layout": {"in_front_of": "desk"}},
    ]}, tmp_path / name)


def by_item(st):
    return {(a.get("object"), a["item"]): a for a in st["assumptions"]}


def test_two_corrections_become_the_default(tmp_path):
    for n in ("a", "b"):
        st = make(tmp_path, n)
        assert "preferences_applied" not in st
        d = st["scene_dir"]
        service.set_room(d, lighting="home", correction=True)
        service.set_layout(d, "chair", {"in_front_of": "desk", "gap": -0.2}, correction=True)
        if n == "a":
            assert service.get_preferences()["defaults"] == []  # one scene is not a preference yet
    prefs = {(p["item"], p["target"]): p for p in service.get_preferences()["defaults"]}
    assert prefs[("lighting", "place:home")]["value"] == "home" and prefs[("lighting", "place:home")]["count"] == 2
    assert prefs[("gap", "chair")]["direction"] == "down"

    st = make(tmp_path, "c")
    s = service.load(st["scene_dir"])
    assert s.spec.room.lighting == "home"
    assert s.obj("chair").layout.gap == -0.2
    rows = by_item(st)
    assert rows[(None, "lighting")]["basis"] == preferences.BASIS and not rows[(None, "lighting")]["given"]
    assert rows[("chair", "gap")]["basis"] == preferences.BASIS
    assert {p["item"] for p in st["preferences_applied"]} == {"lighting", "gap"}
    html = (tmp_path / "c" / "preview.html").read_text(encoding="utf-8")
    assert "前回の好み" in html

    # what the spec says wins; another place does not get the home preference
    st = make(tmp_path, "d", lighting="office")
    assert service.load(st["scene_dir"]).spec.room.lighting == "office"
    st = make(tmp_path, "e", place="office")
    assert service.load(st["scene_dir"]).spec.room.lighting != "home"
    st = service.create_scene({"name": "f", "room": {"width": 4, "depth": 3, "place": "home"}}, tmp_path / "f",
                              preferences=False)
    assert service.load(st["scene_dir"]).spec.room.lighting != "home"


def test_only_corrections_count(tmp_path):
    for n in ("a", "b"):
        d = make(tmp_path, n)["scene_dir"]
        service.set_room(d, lighting="night")  # the agent's own adjustment
    assert service.get_preferences()["defaults"] == [] and service.get_preferences()["pending"] == []


def test_disagreeing_scenes_and_undo(tmp_path):
    a = make(tmp_path, "a")["scene_dir"]
    b = make(tmp_path, "b")["scene_dir"]
    service.set_room(a, floor="wood_dark", correction=True)
    service.set_room(b, floor="tatami", correction=True)
    p = service.get_preferences()["pending"]
    assert [(x["value"], x["count"]) for x in p] == [("tatami", 1)]
    # the same scene corrected twice counts once (its latest value)
    service.set_room(a, floor="tatami", correction=True)
    service.set_room(a, floor="tatami", correction=True)
    (p,) = service.get_preferences()["defaults"]
    assert p["value"] == "tatami" and p["count"] == 2
    # back to what the scene had: it no longer counts
    service.set_room(b, floor="wood_light", correction=True)
    assert service.get_preferences()["defaults"] == []


def test_numbers_by_direction_and_preview_asset(tmp_path):
    for n, aisle in (("a", 1.6), ("b", 1.8)):
        d = service.create_scene({"name": n, "room": {"width": 12, "depth": 10, "place": "warehouse"}, "objects": [
            {"id": "racks", "query": "rack", "array": {"count": 2, "rows": 2}, "layout": {"against_wall": "west"}},
            {"id": "chair", "query": "office chair", "position": [6, 6]},
        ]}, tmp_path / n)["scene_dir"]
        service.set_array(d, "racks", {"count": 2, "rows": 2, "aisle": aisle}, correction=True)
        tok = service.ACTOR.set("preview")  # the user picks the other chair on the preview page
        try:
            service.select_asset(d, "chair", 1)
        finally:
            service.ACTOR.reset(tok)
    prefs = {(p["item"], p["target"]): p for p in service.get_preferences()["defaults"]}
    assert prefs[("aisle", "rack")]["value"] == 1.8 and prefs[("aisle", "rack")]["direction"] == "up"
    assert prefs[("asset", "office chair")]["label"] == "chair_b"
    st = service.create_scene({"name": "c", "room": {"width": 12, "depth": 10, "place": "warehouse"}, "objects": [
        {"id": "racks", "query": "rack", "array": {"count": 2, "rows": 2}, "layout": {"against_wall": "west"}},
        {"id": "chair", "query": "office chair", "position": [6, 6]},
    ]}, tmp_path / "c")
    s = service.load(st["scene_dir"])
    assert s.obj("racks").array.aisle == 1.8
    assert s.placements[1].selected == 1
    assert by_item(st)[("chair", "asset")]["value"] == "chair_b"


def test_forget_and_file(tmp_path):
    for n in ("a", "b"):
        d = make(tmp_path, n)["scene_dir"]
        service.set_room(d, lighting="home", lighting_scale=1.5, correction=True)
    out = service.get_preferences()
    assert out["file"] == os.environ["AGENTIC_SIM_WORLD_GEN_PREFERENCES"]
    data = json.loads(open(out["file"], encoding="utf-8").read())
    assert {"item", "target", "value", "count", "last_used"} <= set(data["items"][0])
    out = service.forget_preference("lighting")
    assert out["forgotten"] == [{"item": "lighting", "target": "place:home"}]
    assert [p["item"] for p in out["defaults"]] == ["lighting_scale"]
    assert service.forget_preference("all")["defaults"] == []
