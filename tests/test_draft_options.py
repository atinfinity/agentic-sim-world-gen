"""Three drafts far apart in one thing nobody decided: plans only, the pick is recorded as the user's."""

import pytest

from agentic_sim_world_gen import service
from agentic_sim_world_gen.preview import page
from agentic_sim_world_gen.scene.questions import plan_questions

from test_preview_page import scene  # noqa: F401  (fixture: a 4x3 m study with a desk and a chair)


def test_size_drafts_are_made_drawn_and_chosen(scene):  # noqa: F811
    st = service.propose_drafts(scene)
    rows = st["layout_options"]
    assert [r["axis"] for r in rows] == ["size"] * 3 and st["draft_axes"][0] == {"axis": "size", "label": "広さ", "decided": None}
    assert [r["best"] for r in rows] == [False, True, False]  # nothing to choose between them: the middle one
    assert [r["room"] for r in rows] == ["3.2 × 2.4 m", "4 × 3 m", "5 × 3.8 m"]  # 0.64x / 1x / 1.56x the floor
    assert all((scene / "layout_options" / f"{r['name']}.svg").read_text().startswith("<svg") for r in rows)
    html = (scene / page.PAGE).read_text()
    assert "叩き台 3 案" in html and "広さだけを変えた" in html and "5 × 3.8 m" in html
    s = service.load(scene)
    assert (s.spec.room.width, s.spec.room.depth) == (4.0, 3.0)  # the scene itself waits for the pick
    assert service._draft_look(scene)["stopped"] == "layout_options"  # no 3D before the pick

    st = service.choose_layout(scene, "large")
    s = service.load(scene)
    assert (s.spec.room.width, s.spec.room.depth) == (5.0, 3.8) and s.spec.options_axis is None
    row = next(a for a in s.spec.assumptions if a.item == "広さ")
    assert row.given and row.basis == "叩き台 3 案から選択" and "5 × 3.8 m" in row.value
    assert not st["issues"] and "layout_options" not in st
    assert not any(a.auto and a.item == "size" for a in s.spec.assumptions)  # the pick replaces the automatic guess
    assert service.draft_axes(s)[0]["decided"] == "given: 広さ"


def test_left_to_us_takes_the_middle_as_a_guess(scene):  # noqa: F811
    service.propose_drafts(scene, "size")
    service.choose_layout(scene, "best")
    s = service.load(scene)
    row = next(a for a in s.spec.assumptions if a.item == "広さ")
    assert (s.spec.room.width, s.spec.room.depth) == (4.0, 3.0) and not row.given and "おまかせ" in row.basis


def test_amount_drafts_leave_things_out(scene):  # noqa: F811
    with pytest.raises(ValueError, match="needs 3 options"):
        service.propose_drafts(scene, "amount")
    opts = [{"name": "few", "label": "机だけ", "without": ["chair"]},
            {"name": "std", "label": "机と椅子"},
            {"name": "row", "label": "机を 2 台", "objects": {"desk": {"array": {"count": 2}}}}]
    with pytest.raises(ValueError, match="unknown object"):
        service.propose_drafts(scene, "amount", [{**opts[0], "without": ["sofa"]}, *opts[1:]])
    with pytest.raises(ValueError, match="the same"):
        service.propose_drafts(scene, "amount", [opts[1], {**opts[1], "name": "again"}, opts[2]])
    with pytest.raises(ValueError, match="propose_drafts"):
        service.propose_layouts(scene, opts[:2])  # `without` is not a layout
    st = service.propose_drafts(scene, "amount", opts)
    assert [r["objects"] for r in st["layout_options"]] == [1, 2, 3]
    service.choose_layout(scene, "few")
    s = service.load(scene)
    assert [o.id for o in s.spec.objects] == ["desk"]
    assert next(a for a in s.spec.assumptions if a.item == "物の量").value == "机だけ"


def test_a_given_size_is_not_offered(scene):  # noqa: F811
    service.set_assumptions(scene, [{"item": "部屋の寸法", "value": "4 × 3 m", "given": True}])
    st = service.propose_drafts(scene, "size")  # asked for by name: still made
    assert st["draft_axes"][0]["decided"] == "given: 部屋の寸法"
    service.propose_layouts(scene, [])
    with pytest.raises(ValueError, match="needs 3 options"):
        service.propose_drafts(scene)  # by itself it goes to the next axis, which needs options


def test_plan_questions_suggests_drafts_when_the_size_is_unsaid():
    assert plan_questions("リビングを作りたい")["drafts"]["axis"] == "size"
    assert "drafts" not in plan_questions("8畳のリビングを作りたい")
    assert "drafts" not in plan_questions("リビングを作って。おまかせで")
