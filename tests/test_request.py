"""Matching the request against the result: measured checks, judged items, preview and changes (no network)."""

import pytest

from agentic_sim_world_gen import service
from tests.test_layout import rec

CATALOG = {
    "desk": [rec("desk", "desk", 1.4, 0.7, 0.74)],
    "office chair": [rec("chair", "office chair", 0.55, 0.55, 0.85)],
    "bookshelf": [rec("shelf", "bookshelf", 0.8, 0.3, 1.8)],
    "lamp": [rec("lamp", "desk lamp", 0.2, 0.2, 0.4)],
}


@pytest.fixture(autouse=True)
def fake():
    service.set_provider(type("P", (), {"name": "f", "search": lambda self, q, limit=10, size=None: CATALOG.get(q, [])})())
    yield
    service.set_provider(None)


DOOR = [{"id": "door", "kind": "door", "wall": "south", "offset": 0.6, "width": 0.8, "height": 2.0},
        {"id": "win", "kind": "window", "wall": "north", "offset": 1.8, "width": 1.2}]


def study(tmp_path, shelf_wall="west"):
    return service.create_scene({"name": "t", "room": {"width": 3.6, "depth": 2.7, "height": 2.4, "openings": DOOR},
                                 "objects": [
        {"id": "desk", "query": "desk", "layout": {"against_wall": "north"}},
        {"id": "chair", "query": "office chair", "layout": {"in_front_of": "desk"}},
        {"id": "shelf", "query": "bookshelf", "layout": {"against_wall": shelf_wall}},
        {"id": "lamp", "query": "lamp", "layout": {"on": "desk"}},
    ]}, tmp_path / "t")


REQS = [
    {"text": "本棚は東の壁", "checks": [{"kind": "against_wall", "object": "shelf", "target": "east"}]},
    {"text": "机は窓に向けて", "checks": [{"kind": "against_wall", "object": "desk", "target": "north"},
                                    {"kind": "facing", "object": "chair", "target": "desk"}]},
    {"text": "椅子 1 脚", "checks": [{"kind": "count", "objects": ["chair"], "value": 1}]},
    {"text": "机の上にライト", "checks": [{"kind": "on", "object": "lamp", "target": "desk"}]},
    {"text": "ドアの近くに本棚", "checks": [{"kind": "near", "object": "shelf", "target": "door", "value": 1.0}]},
    {"text": "落ち着いた雰囲気", "verdict": "met", "note": "木目の机と暗めの床"},
    {"text": "木目の本棚"},
]


def by_text(st):
    return {r["text"]: r for r in st["requirements"]}


def test_measured_and_judged(tmp_path):
    study(tmp_path, shelf_wall="west")
    st = service.check_request(tmp_path / "t", REQS)
    r = by_text(st)
    assert r["本棚は東の壁"]["status"] == "unmet" and r["本棚は東の壁"]["by"] == "measured"
    assert "東の壁から" in r["本棚は東の壁"]["checks"][0]["detail"]
    assert r["机は窓に向けて"]["status"] == "met"
    assert r["椅子 1 脚"]["status"] == "met"
    assert r["机の上にライト"]["status"] == "met"
    assert r["落ち着いた雰囲気"]["status"] == "met" and r["落ち着いた雰囲気"]["by"] == "judged"
    assert r["木目の本棚"]["status"] == "unknown"
    assert st["requirements_summary"]["unmet"] >= 1 and "requirements_hint" in st


def test_fix_and_remeasure(tmp_path):
    study(tmp_path)
    service.check_request(tmp_path / "t", REQS[:1])
    service.set_layout(tmp_path / "t", "shelf", {"against_wall": "east"})
    st = service.check_request(tmp_path / "t")  # re-measure the stored items
    assert by_text(st)["本棚は東の壁"]["status"] == "met"
    assert st["requirements_summary"] == {"met": 1, "unmet": 0, "unknown": 0, "total": 1}
    assert "requirements_hint" not in st
    page = (tmp_path / "t" / "preview.html").read_text()
    assert "指示との突き合わせ" in page and "満たした 1" in page


def test_count_and_missing(tmp_path):
    study(tmp_path)
    st = service.check_request(tmp_path / "t", [
        {"text": "椅子 2 脚", "checks": [{"kind": "count", "objects": ["chair"], "value": 2}]},
        {"text": "観葉植物", "checks": [{"kind": "next_to", "object": "plant", "target": "desk"}]},
    ])
    r = by_text(st)
    assert r["椅子 2 脚"]["status"] == "unmet" and "合計 1（指示 2）" in r["椅子 2 脚"]["checks"][0]["detail"]
    assert r["観葉植物"]["status"] == "unknown"  # no such object: cannot measure


def test_replace_false_keeps_others(tmp_path):
    study(tmp_path)
    service.check_request(tmp_path / "t", REQS[:2])
    st = service.check_request(tmp_path / "t", [{"text": "本棚は東の壁", "verdict": "unmet"}], replace=False)
    assert [r["text"] for r in st["requirements"]] == ["机は窓に向けて", "本棚は東の壁"]


def test_change_history_shows_status(tmp_path):
    import json

    study(tmp_path)
    service.check_request(tmp_path / "t", REQS[:1])
    service.set_layout(tmp_path / "t", "shelf", {"against_wall": "east"})
    hist = json.loads((tmp_path / "t" / "preview.json").read_text())["history"]
    assert any("指示「本棚は東の壁」：満たしていない → 満たした" in c for h in hist for c in h.get("changes") or [])
