"""Undo / back to a version of the history, and the direct edits on the preview page."""

import json

import pytest

from test_preview_page import scene  # noqa: F401  (fixture)
from agentic_sim_world_gen import service
from agentic_sim_world_gen.preview import page, server


def hist(d):
    return json.loads((d / page.STATE).read_text())["history"]


def test_every_logged_save_keeps_a_version(scene):
    assert service.versions(scene) == [1]  # create_scene
    service.move_object(scene, "chair", position=(1.0, 1.0))
    service.remove_object(scene, "chair")
    assert service.versions(scene) == [1, 2, 3]
    assert [e["version"] for e in hist(scene)] == [1, 2, 3]
    rows = service.version_rows(scene)
    assert rows[-1]["current"] and "削除" in rows[-1]["text"]
    service.status(scene)  # reading does not add one
    assert service.versions(scene) == [1, 2, 3]


def test_undo_then_redo(scene):
    before = service.load(scene).model_dump()
    service.remove_object(scene, "chair")
    st = service.restore(scene)
    assert st["restored"] == 1
    assert service.load(scene).model_dump() == before  # exactly as it was
    assert "戻す" in hist(scene)[-1]["text"]
    service.restore(scene)  # the restore is a save too: undoing it is the redo
    assert [o.id for o in service.load(scene).spec.objects] == ["desk"]


def test_back_to_a_version_keeps_the_notes(scene):
    service.move_object(scene, "chair", position=(1.0, 1.0))  # 2
    service.set_room(scene, width=5.0)  # 3
    service.add_note(scene, "ここに棚がほしい", position=(2.0, 1.0))
    service.restore(scene, 2)
    s = service.load(scene)
    assert s.spec.room.width == 4 and s.obj("chair").position == (1.0, 1.0)
    assert [n.text for n in s.spec.notes] == ["ここに棚がほしい"]
    with pytest.raises(ValueError, match="kept"):
        service.restore(scene, 99)


def test_old_versions_go(scene, monkeypatch):
    monkeypatch.setattr(service, "SNAPSHOTS", 3)
    for i in range(4):
        service.move_object(scene, "chair", position=(1.0 + 0.1 * i, 1.0))
    assert service.versions(scene) == [3, 4, 5]
    html = (scene / page.PAGE).read_text()
    assert html.count("class='restore'") == 2  # the kept ones but the newest
    assert "id='undo'" in html


def test_scene_from_before_keeps_where_it_was(scene):
    import shutil

    shutil.rmtree(scene / service.SNAP_DIR)
    service.remove_object(scene, "chair")
    assert service.versions(scene) == [1, 2]
    service.restore(scene)
    assert {o.id for o in service.load(scene).spec.objects} == {"desk", "chair"}


def test_duplicate(scene):
    st = service.duplicate_object(scene, "chair")
    assert st["added"] == ["chair_2"]
    s = service.load(scene)
    a, b = s.obj("chair"), s.obj("chair_2")
    assert b.layout is None and b.placed_by_user and b.position != a.position
    assert not [i for i in st["issues"] if "chair_2" in i["objects"]]
    assert service._placement(s, "chair_2").asset.name == "chair"
    assert service.duplicate_object(scene, "chair")["added"] == ["chair_3"]


def test_page_edits(scene):
    n = len(hist(scene))
    assert server.edit(scene, {"op": "duplicate", "id": "desk"})["ok"]
    assert server.edit(scene, {"op": "remove", "id": "chair"})["ok"]
    assert server.edit(scene, {"op": "room", "width": 5.0})["ok"]
    assert server.edit(scene, {"op": "light", "lighting_scale": 0.6, "light_kelvin": 2700})["ok"]
    assert server.edit(scene, {"op": "density", "density": "normal"})["ok"]
    s = service.load(scene)
    assert {o.id for o in s.spec.objects if not o.decor} == {"desk", "desk_2"}
    r = s.spec.room
    assert (r.width, r.depth, r.lighting_scale, r.light_kelvin, r.density) == (5.0, 3.0, 0.6, 2700, "normal")
    new = hist(scene)[n:]
    assert len(new) == 5 and all(e["by"] == "preview" for e in new)
    assert len(service.preview_edits(scene)) == 5  # the agent sees them on its next turn
    assert server.edit(scene, {"op": "restore"})["ok"]  # undo from the page
    assert service.load(scene).spec.room.density is None
    assert server.edit(scene, {"op": "restore", "version": hist(scene)[n]["version"]})["ok"]
    assert {o.id for o in service.load(scene).spec.objects} == {"desk", "chair", "desk_2"}


def test_page_has_the_controls(scene):
    html = (scene / page.PAGE).read_text()
    assert "id='room-edit'" in html and "data-op='room'" in html and "data-op='light'" in html
    assert html.count("class='dup'") == 2 and html.count("class='del'") == 2
    assert "id='undo'" not in html  # one version: nothing to undo yet
