"""A 「なんか違う」 with no reason: the scene as it is next to one changed in one thing, the user says which
is closer; the answer is recorded and the step is not offered again."""

import pytest

from agentic_sim_world_gen import service
from agentic_sim_world_gen.preview import page

from test_preview_page import scene  # noqa: F401  (fixture: a 4x3 m study with a desk and a chair)


def test_size_pair_is_drawn_and_the_other_is_applied(scene):  # noqa: F811
    st = service.propose_contrast(scene)
    step = st["contrast"]["step"]
    assert st["contrast"]["axis"] == "size" and step in ("size+", "size-")
    rows = st["layout_options"]
    assert [r["name"] for r in rows] == ["now", "other"] and all(r["contrast"] == "size" for r in rows)
    assert not any(r["best"] for r in rows)  # a question, not a recommendation
    assert rows[0]["room"] == "4 × 3 m" and rows[1]["room"] == ("5 × 3.8 m" if step == "size+" else "3.2 × 2.4 m")
    html = (scene / page.PAGE).read_text()
    assert "どちらが近いですか" in html and "広さだけが違う" in html and "叩き台" not in html
    s = service.load(scene)
    assert (s.spec.room.width, s.spec.room.depth) == (4.0, 3.0)  # the scene waits for the answer

    st = service.choose_contrast(scene, "other")
    s = service.load(scene)
    assert s.spec.room.width != 4.0 and not s.spec.layout_options and s.spec.contrast.step is None
    row = next(a for a in s.spec.assumptions if a.item == "広さ")
    assert row.given and row.basis == "見本の二択で選択"
    assert st["contrast"]["found"] == "size" and step in st["contrast"]["tried"] and step not in st["contrast"]["left"]
    assert "layout_options" not in st


def test_now_and_neither_move_on_to_the_next_step(scene):  # noqa: F811
    first = service.propose_contrast(scene)["contrast"]["step"]
    st = service.choose_contrast(scene, "now")
    s = service.load(scene)
    assert (s.spec.room.width, s.spec.room.depth) == (4.0, 3.0) and st["contrast"]["found"] is None
    assert next(a for a in s.spec.assumptions if a.item == "広さ").value.startswith("今のまま")
    nxt = service.propose_contrast(scene)["contrast"]
    assert nxt["step"] != first and first in nxt["tried"]  # not the same question again
    before = len(service.load(scene).spec.assumptions)
    st = service.choose_contrast(scene, "neither")
    assert len(service.load(scene).spec.assumptions) == before  # neither: nothing the user decided
    assert nxt["step"] in st["contrast"]["tried"]


def test_the_page_pick_goes_through_choose_layout(scene):  # noqa: F811
    service.propose_contrast(scene, "size", "more")
    with pytest.raises(ValueError, match="choose_contrast"):
        service.choose_layout(scene, "best")
    st = service.choose_layout(scene, "other")  # 「この案にする」 on the preview
    s = service.load(scene)
    assert (s.spec.room.width, s.spec.room.depth) == (5.0, 3.8) and st["contrast"]["found"] == "size"


def test_layout_and_amount_take_the_agents_option(scene):  # noqa: F811
    with pytest.raises(ValueError, match="needs option"):
        service.propose_contrast(scene, "layout")
    with pytest.raises(ValueError, match="unknown object"):
        service.propose_contrast(scene, "amount", option={"label": "机だけ", "without": ["sofa"]})
    st = service.propose_contrast(scene, "amount", option={"label": "机だけ", "without": ["chair"]})
    assert [r["objects"] for r in st["layout_options"]] == [2, 1]
    service.choose_contrast(scene, "other")
    s = service.load(scene)
    assert [o.id for o in s.spec.objects] == ["desk"]
    assert next(a for a in s.spec.assumptions if a.item == "物の量").value == "机だけ"


def test_mood_pair_shows_sample_pictures_and_applies(scene):  # noqa: F811
    st = service.propose_contrast(scene, "mood")
    c = st["contrast"]
    assert c["axis"] == "mood" and c["other"]["name"] and "layout_options" not in st
    assert service.load(scene).spec.mood is None
    st = service.choose_contrast(scene, "other")
    s = service.load(scene)
    assert s.spec.mood.name == c["other"]["name"] and st["contrast"]["found"] == "mood"
    assert next(a for a in s.spec.assumptions if a.item == "雰囲気" and a.basis == "見本の二択で選択").value == c["other"]["label"]


def test_errors(scene):  # noqa: F811
    with pytest.raises(ValueError, match="propose_contrast first"):
        service.choose_contrast(scene, "now")
    with pytest.raises(ValueError, match="unknown axis"):
        service.propose_contrast(scene, "color")
    service.propose_drafts(scene, "size")
    with pytest.raises(ValueError, match="waiting for a pick"):
        service.propose_contrast(scene)
