"""Judging the result: request checks with gaps, opening spans and site areas, objects standing on site
tables, assumptions after an asset swap, and the check verdict in status (no network, no Isaac Sim)."""

import json

import pytest

from agentic_sim_world_gen import service
from tests.test_layout import rec

CATALOG = {
    "counter": [rec("counter", "counter", 1.8, 0.6, 0.9)],
    "bookshelf": [rec("shelf", "bookshelf", 0.8, 0.3, 1.8)],
    "desk": [rec("desk", "desk", 1.4, 0.7, 0.74), rec("desk2", "desk", 1.2, 0.6, 0.72)],
    "picnic table": [rec("ptable", "picnic table", 1.8, 0.8, 0.75)],
    "cup": [rec("cup", "cup", 0.1, 0.1, 0.12)],
}


@pytest.fixture(autouse=True)
def fake():
    service.set_provider(type("P", (), {"name": "f", "search": lambda self, q, limit=10, size=None: CATALOG.get(q, [])})())
    yield
    service.set_provider(None)


def by_text(st):
    return {r["text"]: r for r in st["requirements"]}


ROOM = {"width": 6, "depth": 5, "height": 2.7,
        "openings": [{"id": "door", "kind": "door", "wall": "south", "offset": 3, "width": 0.9},
                     {"id": "win", "kind": "window", "wall": "north", "offset": 3.0, "width": 2.4}]}


def test_against_wall_counts_the_layout_gap(tmp_path):
    d = tmp_path / "t"
    service.create_scene({"name": "t", "room": ROOM, "objects": [
        {"id": "counter", "query": "counter", "layout": {"against_wall": "west", "gap": 0.9}}]}, d)
    st = service.check_request(d, [{"text": "カウンターは西の壁", "checks": [
        {"kind": "against_wall", "object": "counter", "target": "west"}]}])
    r = by_text(st)["カウンターは西の壁"]
    assert r["status"] == "met"
    assert "0.9" in r["checks"][0]["detail"]


def test_near_an_opening_measures_to_its_span(tmp_path):
    """The window is 2.4 m wide: a shelf by its east end is near it although 1.2 m from its centre."""
    d = tmp_path / "t"
    service.create_scene({"name": "t", "room": ROOM, "objects": [
        {"id": "shelf", "query": "bookshelf", "position": [4.3, 4.7], "facing_deg": 270}]}, d)
    st = service.check_request(d, [{"text": "窓のそばに本棚", "checks": [
        {"kind": "near", "object": "shelf", "target": "win", "value": 0.5}]}])
    assert by_text(st)["窓のそばに本棚"]["status"] == "met"


SITE = {"rect": [-6, -8, 12, 11],
        "areas": [{"id": "terrace", "kind": "plaza", "rect": [-5, -6, 11, -1]}]}


def site_scene(tmp_path):
    d = tmp_path / "t"
    service.create_scene({"name": "t", "room": {**ROOM, "site": SITE}, "objects": [
        {"id": "ptable", "query": "picnic table", "zone": "terrace", "position": [2, -3.5]},
        {"id": "cup", "query": "cup", "layout": {"on": "ptable"}},
    ]}, d)
    return d


def test_on_a_site_table_is_not_out_of_room(tmp_path):
    d = site_scene(tmp_path)
    st = service.status(d)
    assert not [i for i in st["issues"] if i["kind"] == "out_of_room"]


def test_in_zone_sees_site_areas(tmp_path):
    d = site_scene(tmp_path)
    st = service.check_request(d, [
        {"text": "テラスにテーブル", "checks": [{"kind": "in_zone", "object": "ptable", "target": "terrace"}]},
        {"text": "外にテーブル", "checks": [{"kind": "in_zone", "object": "ptable", "target": "outside"}]},
    ])
    r = by_text(st)
    assert r["テラスにテーブル"]["status"] == "met"
    assert r["外にテーブル"]["status"] == "met"


def test_assumptions_marked_after_an_asset_swap(tmp_path):
    d = tmp_path / "t"
    service.create_scene({"name": "t", "room": ROOM, "objects": [
        {"id": "desk", "query": "desk", "layout": {"against_wall": "north"}}]}, d)
    service.set_assumptions(d, [{"item": "寸法", "value": "1.4 × 0.7 m", "object": "desk", "basis": "候補 1 位"}])
    st = service.select_asset(d, "desk", 1)
    rows = st["assumptions_review"]
    assert [r["item"] for r in rows] == ["寸法"] and "差し替えた" in rows[0]["why"]
    assert "assumptions_review_hint" in st
    assert "要見直し" in (d / "preview.html").read_text()
    st = service.set_assumptions(d, [{"item": "寸法", "value": "1.2 × 0.6 m", "object": "desk", "basis": "候補 2 位"}])
    assert "assumptions_review" not in st


def test_check_verdict_in_status(tmp_path, monkeypatch):
    d = tmp_path / "t"
    service.create_scene({"name": "t", "room": ROOM, "objects": [
        {"id": "desk", "query": "desk", "layout": {"against_wall": "north"}}]}, d)
    service.compose_scene(d)
    rep = {"unstable": ["desk"], "sunk": [], "no_collider": [], "objects": {"desk": {"stable": False}}}

    def fake_check(scene, *a, **k):
        (d / "check.json").write_text(json.dumps(rep))
        return {"ok": True, "mode": "headless", "report": dict(rep)}

    monkeypatch.setattr(service, "_check_in_isaac", fake_check)
    r = service.check_in_isaac(d)
    assert r["ok"] and r["verdict"] == "needs_fix" and "NOT passed" in r["next"]
    assert service.status(d)["last_check"] == {"verdict": "needs_fix", "unstable": 1}
    service.move_object(d, "desk", position=[2, 3])
    assert "stale" in service.status(d)["last_check"]
    rep.update(unstable=[], objects={"desk": {"stable": True}})
    r = service.check_in_isaac(d)
    assert r["verdict"] == "passed" and service.status(d)["last_check"] == {"verdict": "passed"}
