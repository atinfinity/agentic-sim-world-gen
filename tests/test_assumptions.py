"""Assumptions shown before generating: given vs guessed, merged by item, in status and the preview."""

import pytest

from agentic_sim_world_gen import service
from tests.test_layout import rec

CATALOG = {"desk": [rec("desk", "desk", 1.4, 0.7, 0.74)], "office chair": [rec("chair", "office chair", 0.55, 0.55, 0.85)]}


@pytest.fixture(autouse=True)
def fake():
    service.set_provider(type("P", (), {"name": "f", "search": lambda self, q, limit=10, size=None: CATALOG.get(q, [])})())
    yield
    service.set_provider(None)


def make(tmp_path):
    return service.create_scene({"name": "t", "room": {"width": 3.64, "depth": 2.73, "height": 2.4}, "objects": [
        {"id": "desk", "label": "机", "query": "desk", "layout": {"against_wall": "north"}},
        {"id": "chair", "label": "椅子", "query": "office chair", "layout": {"in_front_of": "desk"}},
    ]}, tmp_path / "t")


ROWS = [
    {"item": "部屋の寸法", "value": "3.64 × 2.73 m、天井 2.4 m", "basis": "6畳の標準"},
    {"item": "place", "value": "home", "given": True},
    {"item": "床", "value": "明るい木目", "basis": "書斎によくある"},
    {"item": "位置", "value": "北の壁", "object": "desk", "basis": "窓側と仮定"},
]


def test_the_service_records_what_nobody_did(tmp_path):
    """No long set_assumptions to fill assumptions_missing; the rows come from the scene and follow it."""
    st = make(tmp_path)
    assert st["assumptions"] == [] and st["assumptions_auto"]["count"] == 7 and "assumptions_missing" not in st
    rows = {(a.object, a.item): a for a in service.load(tmp_path / "t").spec.assumptions}
    assert list(rows) == [(None, "size"), (None, "place"), (None, "floor"), (None, "walls"), (None, "lighting"),
                          ("desk", "asset"), ("chair", "asset")]
    assert all(a.auto and not a.given and a.basis == service.AUTO_BASIS for a in rows.values())
    assert rows[(None, "size")].value == "3.64 × 2.73 m、天井 2.4 m" and rows[(None, "place")].value == "指定なし"
    assert rows[("desk", "asset")].value.startswith("desk、1.40 × 0.70 × 0.74 m、位置 (")
    before = rows[("desk", "asset")].value
    service.move_object(tmp_path / "t", "desk", position=(1.0, 1.0))
    after = next(a for a in service.load(tmp_path / "t").spec.assumptions if a.object == "desk")
    assert after.value != before and "位置 (1.00, 1.00)" in after.value  # rewritten at every save

    # the user's 「違う」 on an automatic row stays on it, and the agent sees that row
    st = service.assumption_feedback(tmp_path / "t", "asset", "もっと小さい机に", object="desk")
    assert [a["object"] for a in st["assumptions"]] == ["desk"] and st["assumptions"][0]["feedback"]["text"] == "もっと小さい机に"
    st = service.move_object(tmp_path / "t", "desk", position=(1.2, 1.0))
    assert st["assumptions"][0]["feedback"]["text"] == "もっと小さい机に"
    # recording the row answers it, and takes the automatic row's place
    st = service.set_assumptions(tmp_path / "t", [{"item": "asset", "object": "desk", "value": "小さい机", "given": True}])
    assert [(a["object"], a["given"], "feedback" in a) for a in st["assumptions"]] == [("desk", True, False)]
    assert st["assumptions_auto"]["count"] == 6


def test_set_and_merge(tmp_path):
    d = make(tmp_path)["scene_dir"]
    st = service.set_assumptions(d, ROWS)
    assert [a["given"] for a in st["assumptions"]] == [False, False, False, True]  # guesses first
    assert st["assumptions_auto"]["count"] == 3  # walls, lighting, chair: nobody recorded them
    auto = [(a.object, a.item) for a in service.load(d).spec.assumptions if a.auto]
    assert auto == [(None, "walls"), (None, "lighting"), ("chair", "asset")]  # 部屋の寸法 / 床 cover size / floor
    # the user corrects one guess: only that row changes
    st = service.set_assumptions(d, [{"item": "位置", "value": "東の壁", "object": "desk", "given": True}])
    rows = {(a.get("object"), a["item"]): a for a in st["assumptions"]}
    assert len(rows) == 4 and rows[("desk", "位置")]["value"] == "東の壁" and rows[("desk", "位置")]["given"]
    assert rows[(None, "床")]["value"] == "明るい木目"
    st = service.set_assumptions(d, [], remove=["床", "desk:位置"])
    assert {a["item"] for a in st["assumptions"]} == {"部屋の寸法", "place"}
    st = service.set_assumptions(d, ROWS[:1], replace=True)
    assert [a["item"] for a in st["assumptions"]] == ["部屋の寸法"]


def test_preview_and_changes(tmp_path):
    d = make(tmp_path)["scene_dir"]
    service.set_assumptions(d, ROWS)
    html = (tmp_path / "t" / "preview.html").read_text(encoding="utf-8")
    assert "<h2>仮定" in html and "推測 6 件 / 全 7 件" in html and "机：位置" in html and "6畳の標準" in html
    service.set_assumptions(d, [{"item": "位置", "value": "東の壁", "object": "desk", "given": True}])
    import json
    hist = json.loads((tmp_path / "t" / "preview.json").read_text(encoding="utf-8"))["history"]
    assert any("仮定 机：位置：北の壁 → 東の壁（指示どおりに）" in line for h in hist for line in h.get("changes", []))
