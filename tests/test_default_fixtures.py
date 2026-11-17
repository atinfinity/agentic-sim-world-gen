"""Ceiling fixtures by place: an office / classroom / shop under a ceiling gets fixtures you can see. Offline."""

import pytest

from agentic_sim_world_gen import service
from agentic_sim_world_gen.scene.fixtures import PLACE_KINDS, ceiling_glow, density, place_default, plan
from agentic_sim_world_gen.scene.looks import LIGHTING
from agentic_sim_world_gen.scene.spec import PLACES, Room
from tests.test_layout import rec

CATALOG = {
    "desk": [rec("desk", "desk", 1.2, 0.6, 0.72)],
    "ceiling base light": [rec("light", "ceiling light", 1.2, 0.2, 0.08)],
    "emergency light": [rec("elight", "emergency light", 0.3, 0.1, 0.1)],
}


@pytest.fixture(autouse=True)
def fake():
    service.set_provider(type("P", (), {"name": "f", "search": lambda self, q, limit=10, size=None: CATALOG.get(q, [])})())
    yield
    service.set_provider(None)


def classroom(**kw):
    return {"name": "c", "room": {"place": "school", "width": 8, "depth": 7, "height": 3.0, "ceiling": True,
                                  "lighting": "office", **kw},
            "objects": [{"id": "desk", "query": "desk", "position": [4, 3.5]}]}


def fixture_rows(s):
    return [a for a in s.spec.assumptions if a.item == service.FIXTURE_ITEM]


def test_place_kinds_are_places():
    assert set(PLACE_KINDS) <= set(PLACES)
    assert {"home", "warehouse", "factory", "outdoor"}.isdisjoint(PLACE_KINDS)


def test_place_default():
    r = Room.model_validate({"place": "school", "width": 8, "depth": 7, "ceiling": True, "lighting": "office"})
    f = place_default(r)
    assert f.kind == "line" and f.spacing == LIGHTING["office"]["spacing"]  # on the preset's grid
    assert place_default(r.model_copy(update={"ceiling": False})) is None  # no ceiling to hang them from
    assert place_default(r.model_copy(update={"place": "home"})) is None
    assert place_default(r.model_copy(update={"place": "office"})).kind == "panel"
    cafe = place_default(r.model_copy(update={"place": "cafe"}))
    assert cafe.kind == "downlight" and cafe.spacing == 2.4
    assert place_default(r.model_copy(update={"place": "cafe", "lighting": "home"})).spacing == 2.5
    assert place_default(r.model_copy(update={"lighting": "warehouse"})) is None  # high-bays stay
    # studio has no ceiling lights: the office's grid, at the default light per floor area (the office's)
    assert place_default(r.model_copy(update={"lighting": "studio"})).spacing == 2.4


def test_same_light_per_floor_area_as_the_preset():
    r = Room.model_validate({"place": "school", "width": 8, "depth": 7, "ceiling": True, "lighting": "office"})
    with_f = r.model_copy(update={"fixtures": place_default(r)})
    fx = plan(with_f)
    p = LIGHTING["office"]
    preset = p["intensity"] * p["size"][0] * p["size"][1] * len(fx)  # same grid, same count as the bare lights
    L = fx[0]["light"]
    assert L["intensity"] * L["width"] * L["height"] * len(fx) == pytest.approx(preset, rel=1e-3)
    assert density(with_f) == pytest.approx(500.0)
    assert ceiling_glow(with_f) == pytest.approx(0.3)  # the ceiling as bright as the walls


def test_classroom_gets_line_fixtures(tmp_path):
    d = service.create_scene(classroom(), tmp_path / "c")["scene_dir"]
    s = service.load(d)
    f = s.spec.room.fixtures
    assert f is not None and f.kind == "line" and f.spacing == 2.4
    assert len(plan(s.spec.room)) == 12  # ceil(8 / 2.4) x ceil(7 / 2.4)
    row, = fixture_rows(s)
    assert not row.given and row.basis.startswith(service.FIXTURE_BASIS)
    svg = (tmp_path / "c" / "layout.svg").read_text()
    marks = [ln for ln in svg.splitlines() if 'fill="#ffd54f"' in ln]
    assert len(marks) == 12 and all(m.startswith("<rect") for m in marks)  # the fixtures' shape, not dots


def test_no_default_without_ceiling_or_for_home(tmp_path):
    s = service.load(service.create_scene(classroom(ceiling=False), tmp_path / "a")["scene_dir"])
    assert s.spec.room.fixtures is None and not fixture_rows(s)
    s = service.load(service.create_scene(classroom(place="home"), tmp_path / "b")["scene_dir"])
    assert s.spec.room.fixtures is None


def test_spec_fixtures_are_kept(tmp_path):
    s = service.load(service.create_scene(classroom(fixtures="pendant"), tmp_path / "c")["scene_dir"])
    assert s.spec.room.fixtures.kind == "pendant" and not fixture_rows(s)


def test_follows_place_and_ceiling(tmp_path):
    d = service.create_scene(classroom(), tmp_path / "c")["scene_dir"]
    service.set_room(d, place="office")
    assert service.load(d).spec.room.fixtures.kind == "panel"
    service.set_room(d, ceiling=False)
    s = service.load(d)
    assert s.spec.room.fixtures is None and not fixture_rows(s)
    service.set_room(d, ceiling=True, place="home")
    assert service.load(d).spec.room.fixtures is None


def test_set_room_choice_stays(tmp_path):
    d = service.create_scene(classroom(), tmp_path / "c")["scene_dir"]
    assert service.load(d).spec.room.fixtures_from == "place"
    service.set_room(d, fixtures=False)
    s = service.load(d)
    assert s.spec.room.fixtures is None and s.spec.room.fixtures_from == "none" and not fixture_rows(s)
    service.set_room(d, place="office")  # off holds when the place changes
    assert service.load(d).spec.room.fixtures is None
    service.set_room(d, fixtures="downlight", correction=True)
    s = service.load(d)
    assert s.spec.room.fixtures.kind == "downlight" and s.spec.room.fixtures_from is None
    service.set_room(d, place="school")  # a chosen kind holds too
    assert service.load(d).spec.room.fixtures.kind == "downlight"


def test_spec_fixtures_false_stays_off(tmp_path):
    """fixtures: false in a spec (or an old scene) is an explicit off, not "nothing decided"."""
    assert Room(width=4, depth=4, fixtures=False).fixtures_from == "none"
    assert Room(width=4, depth=4, fixtures="line", fixtures_from="none").fixtures_from is None  # a kind given wins over off
    d = service.create_scene(classroom(fixtures=False), tmp_path / "c")["scene_dir"]
    s = service.load(d)
    assert s.spec.room.fixtures is None and s.spec.room.fixtures_from == "none"
    service.set_room(d, place="office")
    assert service.load(d).spec.room.fixtures is None


def test_the_row_is_not_the_marker(tmp_path):
    """Removing the guessed row, or a shop's own "fixtures" (store fixtures) row, does not freeze the default."""
    d = service.create_scene(classroom(), tmp_path / "c")["scene_dir"]
    service.set_assumptions(d, [{"item": "fixtures", "value": "ゴンドラ什器 4 列", "basis": "店の標準"}])
    service.set_assumptions(d, [], remove=[service.FIXTURE_ITEM])
    service.set_room(d, place="office")
    s = service.load(d)
    assert s.spec.room.fixtures.kind == "panel" and fixture_rows(s)[0].value == "パネル照明"
    assert any(a.item == "fixtures" and a.value == "ゴンドラ什器 4 列" for a in s.spec.assumptions)
    service.set_assumptions(d, [{"item": "照明", "value": "明るめ"}], replace=True)
    service.set_room(d, place="school")
    assert service.load(d).spec.room.fixtures.kind == "line"


def test_the_row_leaves_the_lighting_row(tmp_path):
    """照明器具 contains 照明 but is not the lighting preset: the automatic lighting row stays."""
    s = service.load(service.create_scene(classroom(), tmp_path / "c")["scene_dir"])
    assert any(a.item == "lighting" and a.auto for a in s.spec.assumptions)


def test_started_from_a_scene_follows_the_new_place(tmp_path):
    service.create_scene(classroom(), tmp_path / "c")
    st = service.start_from_example("scene:c", name="o", room={"place": "office"}, out=tmp_path,
                                    skip=["draft", "preview"])
    s = service.load(st["scene_dir"])
    assert s.spec.room.fixtures.kind == "panel" and s.spec.room.fixtures_from == "place" and fixture_rows(s)
    st = service.start_from_example("scene:c", name="p", room={"fixtures": "pendant"}, out=tmp_path,
                                    skip=["draft", "preview"])
    s = service.load(st["scene_dir"])
    assert s.spec.room.fixtures.kind == "pendant" and s.spec.room.fixtures_from is None


@pytest.mark.parametrize("query, light", [
    ("design pendant", True), ("chandelier", True), ("recessed light", True), ("track lights", True),
    ("LED panel light", True), ("linear light", True), ("light strip", True), ("lights", True),
    ("ダウンライト", True), ("シャンデリア", True), ("sm_lighting_taskLight", True),
    ("emergency light", False), ("exit sign light", False), ("誘導灯", False), ("smoke detector", False),
    ("ceiling fan", False), ("lightweight shelf", False)])
def test_ceiling_light_words(query, light):
    from agentic_sim_world_gen.scene.spec import ObjectSpec
    o = ObjectSpec.model_validate({"id": "x", "query": query, "layout": {"ceiling": True}})
    assert service._is_ceiling_light(o, None) is light


def test_light_objects_replace_the_default(tmp_path):
    """A photo's ceiling lights placed as objects (layout.ceiling): no fixtures on top of them."""
    d = service.create_scene(classroom(), tmp_path / "c")["scene_dir"]
    service.add_objects(d, [{"id": "elight", "query": "emergency light", "layout": {"ceiling": True}}])
    assert service.load(d).spec.room.fixtures is not None  # an emergency light is not a fixture row
    st = service.add_objects(d, [{"id": "ceiling_light", "query": "ceiling base light", "position": [2, 2],
                                  "layout": {"ceiling": True}}])
    s = service.load(d)
    assert s.spec.room.fixtures is None and not fixture_rows(s)
    service.remove_object(d, "ceiling_light")
    assert service.load(d).spec.room.fixtures.kind == "line"


def test_restore_keeps_the_saved_room(tmp_path):
    d = service.create_scene(classroom(), tmp_path / "c")["scene_dir"]
    s = service.load(d)
    s.spec.room.fixtures = None
    service._save(s, tmp_path / "c", None)  # no event: saved as given
    assert service.load(d).spec.room.fixtures is None
