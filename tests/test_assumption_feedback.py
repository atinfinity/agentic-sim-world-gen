"""Feedback on the 「仮定」 rows from the live preview page: kept on the row until the agent re-records it."""

import json

import pytest

from agentic_sim_world_gen import service
from agentic_sim_world_gen.preview import page, server
from agentic_sim_world_gen.scene.questions import options_for

from test_preview_edit import api, post  # noqa: F401  (fixture: the preview server for the study scene)
from test_preview_page import scene  # noqa: F401  (fixture: a 4x3 m study with a desk and a chair)

ROWS = [
    {"item": "window", "value": "南の壁", "basis": "おまかせ（既定）"},
    {"item": "floor", "value": "明るい木目", "basis": "書斎によくある"},
    {"item": "position", "value": "北の壁", "object": "desk", "basis": "窓の反対"},
    {"item": "place", "value": "home", "given": True},
]


@pytest.fixture
def rows(scene):  # noqa: F811
    service.set_room(scene, place="home")
    service.set_assumptions(scene, ROWS)
    return scene


def test_feedback_is_kept_for_the_agent(rows, api):  # noqa: F811
    _, edit = api
    before = service.load(rows).spec.model_dump(exclude={"assumptions"})
    assert post(edit, {"op": "feedback", "item": "window", "text": "  西の壁にして "})[0] == 200
    assert post(edit, {"op": "feedback", "item": "position", "object": "desk", "text": "東の壁"})[0] == 200
    s = service.load(rows)
    assert s.spec.model_dump(exclude={"assumptions"}) == before  # the scene itself is not changed
    st = service.status(rows)
    fb = {(r.get("object"), r["item"]): r for r in st["assumption_feedback"]}
    assert set(fb) == {(None, "window"), ("desk", "position")} and "assumption_feedback_hint" in st
    assert fb[(None, "window")]["feedback"] == "西の壁にして" and fb[(None, "window")]["value"] == "南の壁"
    assert "preview_edits" not in st  # feedback is not an edit of the scene
    hist = json.loads((rows / page.STATE).read_text())["history"]
    assert hist[-2]["text"] == "プレビューで操作：仮定 窓：「西の壁にして」" and hist[-2]["by"] == "preview"
    assert hist[-1]["text"] == "プレビューで操作：仮定 机：位置：「東の壁」"
    html = (rows / page.PAGE).read_text()
    assert "「西の壁にして」" in html and "Claude の対応待ち" in html and "指摘 2 件" in html

    # the agent fixes one row and re-records it: only that feedback clears, the options stay
    service.set_assumptions(rows, [{"item": "window", "value": "西の壁", "given": True}], replace=False)
    st = service.status(rows)
    assert [(r.get("object"), r["item"]) for r in st["assumption_feedback"]] == [("desk", "position")]
    assert {a["item"]: a for a in st["assumptions"]}["floor"].get("feedback") is None
    service.set_assumptions(rows, [{"item": "position", "object": "desk", "value": "東の壁", "given": True}],
                            replace=False)
    assert "assumption_feedback" not in service.status(rows)


def test_withdraw_and_unknown_rows(rows, api):  # noqa: F811
    _, edit = api
    post(edit, {"op": "feedback", "item": "floor", "text": "畳"})
    assert post(edit, {"op": "feedback", "item": "floor", "text": ""})[0] == 200
    assert "assumption_feedback" not in service.status(rows)
    assert json.loads((rows / page.STATE).read_text())["history"][-1]["text"].endswith("床：指摘を取り消し")
    assert post(edit, {"op": "feedback", "item": "ceiling", "text": "高く"})[0] == 400
    assert post(edit, {"op": "feedback", "item": "floor", "text": "畳"}, token="nope")[0] == 403
    assert "assumption_feedback" not in service.status(rows)


def test_options_on_the_page(rows):  # noqa: F811
    assert options_for("home", "window") == ["南の壁", "東の壁", "西の壁", "窓なし"]
    assert options_for("office", "window") == [] and options_for(None, "size")
    html = (rows / page.PAGE).read_text()
    tr = next(line for line in html.split("<tr") if "data-item='window'" in line)
    assert "&quot;西の壁&quot;" in tr and "fb-open" in tr  # the question's choices, and the 「違う」 button
    service.set_assumptions(rows, [{"item": "floor", "value": "明るい木目", "options": ["畳", "フローリング（濃い木目）"]}],
                            replace=False)
    service.set_assumptions(rows, [{"item": "floor", "value": "畳", "given": True}], replace=False)
    a = {a.item: a for a in service.load(rows).spec.assumptions}["floor"]
    assert a.options == ["畳", "フローリング（濃い木目）"]  # kept when re-recorded without them
    assert server.TOKEN not in (rows / page.PAGE).read_text()
