"""Layout options to choose from before generating: plans side by side, one kept."""

import json

import pytest

from agentic_sim_world_gen import service
from agentic_sim_world_gen.preview import page

from test_preview_edit import api, post  # noqa: F401  (fixture: the preview server for the study scene)
from test_preview_page import scene  # noqa: F401  (fixture: a 4x3 m study with a desk and a chair)

OPTIONS = [
    {"name": "north", "label": "北の壁に向けて", "note": "窓を背にしない",
     "objects": {"desk": {"layout": {"against_wall": "north"}}}},
    {"name": "east", "label": "東の壁に向けて", "objects": {"desk": {"layout": {"against_wall": "east"}}}},
    {"name": "bad", "label": "椅子と重ねる", "objects": {"desk": {"position": [2.0, 1.5]}, "chair": {"position": [2.0, 1.5]}}},
]


def test_propose_rates_and_draws_each(scene):  # noqa: F811
    st = service.propose_layouts(scene, OPTIONS)
    rated = {r["name"]: r for r in st["layout_options"]}
    assert set(rated) == {"north", "east", "bad"} and "layout_options_hint" in st
    assert rated["bad"]["issues"] > 0 and rated["north"]["issues"] == 0
    assert not rated["bad"]["best"] and sum(r["best"] for r in rated.values()) == 1
    for n in rated:
        assert (scene / "layout_options" / f"{n}.svg").read_text().startswith("<svg")
    s = service.load(scene)
    assert s.obj("desk").layout.against_wall == "north"  # the scene itself is not changed yet
    html = (scene / page.PAGE).read_text()
    assert "配置案" in html and "layout_options/east.svg?v=" in html and "data-name='bad'" in html
    assert "おすすめ" in html and "窓を背にしない" in html


def test_bad_options_are_refused(scene):  # noqa: F811
    with pytest.raises(ValueError):
        service.propose_layouts(scene, OPTIONS[:1])  # one is not a choice
    with pytest.raises(ValueError):
        service.propose_layouts(scene, [OPTIONS[0], {**OPTIONS[1], "name": "north"}])
    with pytest.raises(Exception):
        service.propose_layouts(scene, [OPTIONS[0], {**OPTIONS[1], "objects": {"sofa": {"zone": "x"}}}])
    with pytest.raises(Exception):
        service.propose_layouts(scene, [OPTIONS[0], {**OPTIONS[1], "objects": {"desk": {"asset": 1}}}])
    with pytest.raises(ValueError):
        service.choose_layout(scene, "best")  # nothing proposed


def test_no_compose_before_choosing(scene):  # noqa: F811
    service.propose_layouts(scene, OPTIONS)
    with pytest.raises(ValueError, match="choose_layout"):
        service.compose_scene(scene)


def test_choose_applies_and_drops_the_rest(scene):  # noqa: F811
    service.propose_layouts(scene, OPTIONS)
    st = service.choose_layout(scene, "east")
    s = service.load(scene)
    assert s.obj("desk").layout.against_wall == "east" and s.spec.layout_options == []
    assert "layout_options" not in st and not (scene / "layout_options").exists()
    row = next(a for a in s.spec.assumptions if a.item == "layout")
    assert row.value == "東の壁に向けて" and row.given
    assert "class='opts'" not in (scene / page.PAGE).read_text()


def test_best_takes_fewest_issues(scene):  # noqa: F811
    service.propose_layouts(scene, [OPTIONS[2], OPTIONS[1]])
    service.choose_layout(scene, "best")
    s = service.load(scene)
    assert s.obj("desk").layout.against_wall == "east"
    row = next(a for a in s.spec.assumptions if a.item == "layout")
    assert not row.given and "おまかせ" in row.basis


def test_position_patch_clears_layout(scene):  # noqa: F811
    service.propose_layouts(scene, OPTIONS)
    service.choose_layout(scene, "bad")
    s = service.load(scene)
    assert s.obj("desk").layout is None and tuple(s.obj("desk").position) == (2.0, 1.5)


def test_pick_on_the_page(scene, api):  # noqa: F811
    _, edit = api
    service.propose_layouts(scene, OPTIONS)
    assert post(edit, {"op": "choose_layout", "name": "east"})[0] == 200
    st = service.status(scene)
    assert service.load(scene).obj("desk").layout.against_wall == "east"
    assert any("配置案「東の壁に向けて」" in e.get("text", "") for e in st["preview_edits"])
    hist = json.loads((scene / "preview.json").read_text())["history"]
    assert hist[-1]["by"] == "preview"
    code, _ = post(edit, {"op": "choose_layout", "name": "east"})  # already chosen
    assert code >= 400


def test_patch_survives_save_and_load(scene):  # noqa: F811
    """Fields left out of a patch stay left out after a reload (they used to come back as None = clear them)."""
    service.set_array(scene, "chair", {"count": 2})
    service.propose_layouts(scene, [OPTIONS[0], {"name": "e", "label": "東", "objects": {"chair": {"zone": None}}}])
    raw = json.loads((scene / "scene.resolved.json").read_text())
    assert raw["spec"]["layout_options"][1]["objects"]["chair"] == {"zone": None}
    service.set_room(scene, lighting="office")  # re-rates the options from the reloaded spec
    service.choose_layout(scene, "e")
    assert service.load(scene).obj("chair").array.count == 2
