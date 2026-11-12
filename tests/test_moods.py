"""Looks picked from sample pictures: offline."""

import pytest

from agentic_sim_world_gen import service
from agentic_sim_world_gen.preview import page
from agentic_sim_world_gen.scene import moods as md

from test_preview_edit import api, post  # noqa: F401  (fixture: the preview server for the study scene)
from test_preview_page import scene  # noqa: F401  (fixture: a 4x3 m study with a desk and a chair)


def test_groups():
    assert [m.name for m in md.for_place(None)] == ["natural", "white_modern", "industrial", "calm_dark"]
    assert md.group("convenience_store") == "shop" and md.group("restaurant") == "cafe"
    assert md.for_place("warehouse") == []
    with pytest.raises(ValueError, match="no moods"):
        md.get("natural", "factory")
    with pytest.raises(ValueError, match="unknown mood 'calm_dark' for office"):
        md.get("calm_dark", "office")
    assert md.furniture_words(md.MOODS["natural"], "bookcase") == "light wood"
    assert md.furniture_words(md.MOODS["natural"], "lamp") is None


def test_apply_and_switch(scene):  # noqa: F811
    service.set_room(scene, ceiling=True)
    listed = service.moods(scene)
    assert listed["place_group"] == "home" and listed["current"] is None and len(listed["moods"]) == 4
    st = service.apply_mood(scene, "industrial")
    room = service.load(scene).spec.room
    assert room.floor == "concrete" and room.walls == {"*": "drywall_gray", "N": "brick"}
    assert room.ceiling == "metal_deck" and room.light_kelvin == 3500 and room.baseboard is None
    assert st["mood"]["name"] == "industrial" and "mood_hint" not in st
    assert [f["try"] for f in st["mood_furniture"]] == ["black metal desk", "black metal office chair"]
    items = {a.item: a for a in service.load(scene).spec.assumptions}
    assert items["雰囲気"].value.startswith("工業系") and items["床"].value == "concrete" and items["床"].given
    assert not items["床"].auto and "floor" not in {a.item for a in service.load(scene).spec.assumptions if a.auto}

    # switching back removes what the other mood added
    service.apply_mood(scene, "calm_dark")
    room = service.load(scene).spec.room
    assert room.ceiling is True and room.wainscot is not None and room.wainscot.surface == "wood_dark"
    assert room.baseboard is not None and room.light_kelvin == 2700 and room.lighting_scale == 0.85
    assert [a.item for a in service.load(scene).spec.assumptions].count("床") == 1
    service.apply_mood(scene, "natural")
    room = service.load(scene).spec.room
    assert room.wainscot is None and room.floor == "wood_light"
    html = (scene / page.PAGE).read_text()
    assert "<h2>雰囲気" in html and "選択中</span> 木目ナチュラル" in html and "mood-swatch" in html


def test_picked_on_the_preview(scene, api):  # noqa: F811
    _, edit = api
    assert post(edit, {"op": "mood", "name": "white_modern"})[0] == 200
    st = service.status(scene)
    assert st["mood"] == {"name": "white_modern", "by": "preview", "furniture_pending": True}
    assert "apply_mood" in st["mood_hint"] and st["mood_furniture"][0] == {
        "object": "desk", "query": "desk", "words": "white", "try": "white desk"}
    assert "preview_edits" not in st or all("雰囲気" not in e for e in st["preview_edits"])
    assert post(edit, {"op": "mood", "name": "nope"})[0] != 200
    # the agent handles the furniture and confirms
    st = service.apply_mood(scene, "white_modern")
    assert not st["mood"]["furniture_pending"] and "mood_hint" not in st


def test_outdoor_has_none(tmp_path):
    d = service.create_scene({"name": "yard", "room": {"width": 10, "depth": 10, "outdoor": True}, "objects": []},
                             tmp_path / "yard")["scene_dir"]
    assert service.moods(d)["moods"] == []
    with pytest.raises(ValueError, match="indoor"):
        service.apply_mood(d, "natural")


def test_preview_pick_composes_in_the_background(scene, api, monkeypatch):  # noqa: F811
    """The page shows the new colors at once; the USD follows in a thread."""
    from agentic_sim_world_gen.preview import server

    _, edit = api
    assert post(edit, {"op": "mood", "name": "natural"}) == (200, {"ok": True, "usd": False})  # no USD yet
    (scene / "scene.usda").write_text("#usda 1.0\n")
    calls = []
    monkeypatch.setattr(service, "compose_scene", lambda d, log=True: calls.append((d, log)))
    assert post(edit, {"op": "mood", "name": "industrial"}) == (200, {"ok": True, "usd": True})
    server.wait_composed(5)
    assert calls == [(scene, False)] and service.load(scene).spec.mood.name == "industrial"


def test_folded_when_pictures_set_the_look(scene):  # noqa: F811
    """A photo or a reference image already decided the look: the cards are folded until one is picked."""
    from agentic_sim_world_gen.scene.spec import InputRef

    s = service.load(scene)
    s.spec.inputs.append(InputRef(id="ref", kind="reference", url="https://example.com/r.jpg", license="CC0-1.0"))
    service._save(s, scene)
    html = (scene / page.PAGE).read_text()
    assert "<details class='moods-fold'>" in html and "参考画像から見た目を決めた" in html
    service.apply_mood(scene, "natural")
    html = (scene / page.PAGE).read_text()
    assert "moods-fold" not in html and "<h2>雰囲気" in html


def test_shop_fixtures_are_not_swapped(tmp_path):
    """In a shop the mood leaves the gondolas and the counter alone (set_tint repaints them)."""
    service.set_provider(type("P", (), {"name": "f", "search": lambda self, q, limit=10, size=None: []})())
    try:
        d = service.create_scene({"name": "s", "room": {"width": 6, "depth": 5, "place": "variety_store"}, "objects": [
            {"id": "shelf", "query": "gondola shelf"}, {"id": "counter", "query": "checkout counter"},
            {"id": "table", "query": "display table"}]}, tmp_path / "s")["scene_dir"]
        st = service.apply_mood(d, "natural")
        assert [f["object"] for f in st["mood_furniture"]] == ["table"]
    finally:
        service.set_provider(None)


def test_asks_before_overwriting_what_was_asked(scene):  # noqa: F811
    """A mood over the user's own floor / walls is confirmed first; their words on a row are kept."""
    service.set_room(scene, floor="tatami", walls="paint_white", light_kelvin=3500)
    service.set_assumptions(scene, [{"item": "床", "value": "tatami", "given": True},
                                    {"item": "壁", "value": "pink paint", "given": True}])
    service.assumption_feedback(scene, "壁", "pink wall")
    got = service.apply_mood(scene, "white_modern", confirm=True)
    assert got["applied"] is False and [c["item"] for c in got["confirm"]] == ["床", "壁"]
    assert service.load(scene).spec.room.floor == "tatami" and service.load(scene).spec.mood is None

    st = service.apply_mood(scene, "white_modern")  # the agent applies it and is told what it replaced
    assert [c["item"] for c in st["overwrote"]] == ["床", "壁"]
    rows = {a.item: a for a in service.load(scene).spec.assumptions}
    assert rows["壁"].basis.startswith("雰囲気") and rows["壁"].feedback.text == "pink wall"
    # switching to another mood does not ask again, and undo goes back past both
    assert "applied" not in service.apply_mood(scene, "natural", confirm=True)
    service.apply_mood(scene, "undo")
    s = service.load(scene)
    assert s.spec.mood is None and s.spec.room.floor == "tatami" and s.spec.room.light_kelvin == 3500
    rows = {a.item: a for a in s.spec.assumptions}
    assert "雰囲気" not in rows and rows["床"].value == "tatami" and rows["壁"].value == "pink paint"
    assert rows["壁"].feedback.text == "pink wall"
    with pytest.raises(ValueError, match="no mood"):
        service.apply_mood(scene, "undo")


def test_reference_taste_is_a_conflict(scene):  # noqa: F811
    from agentic_sim_world_gen.scene.spec import InputRef, Taste

    s = service.load(scene)
    s.spec.inputs.append(InputRef(id="ref", kind="reference", url="https://example.com/r.jpg", license="CC0-1.0",
                                  taste=Taste(walls="pink paint", light_kelvin=3000)))
    service._save(s, scene)
    got = service.apply_mood(scene, "natural", confirm=True)
    assert got["confirm"] == [{"item": "参考画像 ref", "value": "pink paint、3000 K", "from": "参考画像"}]


def test_changing_the_look_by_hand_drops_the_mood(scene):  # noqa: F811
    """The agent put the look back by hand: the scene no longer says it has the mood."""
    service.apply_mood(scene, "industrial")
    service.set_room(scene, lighting_scale=md.MOODS["industrial"].scale, density="normal")  # nothing of the look changed
    assert service.load(scene).spec.mood.name == "industrial"
    service.set_room(scene, walls="paint_white")
    s = service.load(scene)
    assert s.spec.mood is None and "雰囲気" not in {a.item for a in s.spec.assumptions}
    assert s.spec.room.walls == "paint_white" and "床" in {a.item for a in s.spec.assumptions}


def test_preview_asks_and_undoes(scene, api):  # noqa: F811
    _, edit = api
    service.set_assumptions(scene, [{"item": "床", "value": "wood_light", "given": True}])
    code, got = post(edit, {"op": "mood", "name": "white_modern"})
    assert code == 200 and got["ok"] is False and got["confirm"][0]["item"] == "床"
    assert service.load(scene).spec.mood is None
    assert post(edit, {"op": "mood", "name": "white_modern", "force": True})[1]["ok"] is True
    html = (scene / page.PAGE).read_text()
    assert "mood-undo" in html and "元に戻す" in html
    assert post(edit, {"op": "mood_undo"})[1]["ok"] is True
    assert service.load(scene).spec.mood is None and service.load(scene).spec.room.floor != "tile_white"
