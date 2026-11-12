"""The fixes that need no judgement are made at the save (narrow aisle, touching jointed chairs, a TV with
no foot), reported and recorded; what the user placed stays, and a fix that adds a problem is not made."""

import json

import pytest

import test_layout as tl
import test_usability as tu
from agentic_sim_world_gen import service
from agentic_sim_world_gen.preview.page import STATE

CATALOG = {**tu.CATALOG, **tl.CATALOG, "shelf": tu.CATALOG["shelf"]}
SHOP = {"openings": tu.DOOR, "place": "convenience_store"}
ROWS = [{"id": "a", "query": "shelf", "position": (1.5, 1.5), "yaw_deg": 180},
        {"id": "b", "query": "shelf", "position": (1.5, 2.8), "yaw_deg": 0}]  # 0.8 m between them: a shop needs 1.0


@pytest.fixture(autouse=True)
def fake():
    service.set_provider(type("P", (), {"name": "f", "search": lambda self, q, limit=10, size=None: CATALOG.get(q, [])})())
    yield
    service.set_provider(None)


def make(tmp_path, objects, width=4.0, depth=4.0, name="t", **more):
    spec = {"name": name, "room": {"width": width, "depth": depth, "height": 2.6,
                                   **{k: more.pop(k) for k in ("openings", "place") if k in more}}, "objects": objects, **more}
    return service.create_scene(spec, tmp_path / name)


def by_id(st):
    return {o["id"]: o for o in st["objects"]}


def kinds(st):
    return sorted(i["kind"] for i in st["issues"] + st["usability"])


def test_a_narrow_aisle_between_two_fixtures_is_widened(tmp_path):
    st = make(tmp_path, ROWS, **SHOP)
    assert "narrow_aisle" not in kinds(st) and st["issues"] == []
    (f,) = st["auto_fixed"]
    assert f["issue"] == "narrow_aisle" and f["object"] == "b" and f["moved_m"] == [0.0, 0.2]
    assert by_id(st)["b"]["position"] == pytest.approx((1.5, 3.0)) and by_id(st)["a"]["position"] == pytest.approx((1.5, 1.5))
    assert "words" not in f and "auto_fixed_hint" in st
    # recorded as an assumption on the object (it stays over the next saves) and in the preview's history
    row = next(a for a in st["assumptions"] if a["item"] == service.AUTO_FIX_ITEM)
    assert row["object"] == "b" and "0.20 m" in row["value"] and row["basis"] == service.FIX_WHY["narrow_aisle"]
    hist = json.loads((tmp_path / "t" / STATE).read_text())["history"]
    assert "自動で直した" in hist[-1]["text"] and "0.20 m" in hist[-1]["text"]
    st = service.set_room(tmp_path / "t", height=2.7)
    assert "auto_fixed" not in st and any(a["item"] == service.AUTO_FIX_ITEM for a in st["assumptions"])
    assert st["assumptions_auto"]["count"] == 5 + 2  # the room's five and one row per object; the fix's row is not counted


def test_an_array_gets_its_aisle_and_a_wall_in_front_moves_the_fixture_back(tmp_path):
    st = make(tmp_path, [{"id": "r", "query": "shelf", "position": (2.0, 2.0),
                          "array": {"count": 1, "rows": 2, "aisle": 0.8, "alternate": True}}], **SHOP)
    assert "narrow_aisle" not in kinds(st)
    assert st["auto_fixed"][0]["aisle"] == [0.8, 1.0] and by_id(st)["r"]["array"]["aisle"] == 1.0
    st = make(tmp_path, [{"id": "a", "query": "shelf", "position": (2.0, 2.95), "yaw_deg": 180}], name="w", **SHOP)
    assert "narrow_aisle" not in kinds(st) and by_id(st)["a"]["position"] == pytest.approx((2.0, 2.75))


def test_what_the_user_placed_stays(tmp_path):
    d = tmp_path / "t"
    # the user gave b's position: a is moved instead
    given = [{"item": "位置", "value": "奥の壁から 1 m", "given": True, "object": "b"}]
    st = make(tmp_path, ROWS, assumptions=given, **SHOP)
    assert st["auto_fixed"][0]["object"] == "a" and by_id(st)["b"]["position"] == pytest.approx((1.5, 2.8))
    # both given: nothing moves, the warning stays with its hint
    both = given + [{"item": "position", "value": "手前", "given": True, "object": "a"}]
    st = make(tmp_path, ROWS, assumptions=both, name="both", **SHOP)
    assert "auto_fixed" not in st and "narrow_aisle" in kinds(st)
    # a correction and a drag on the preview pin the object; the object a call moves is not moved back by it
    make(tmp_path, [ROWS[0], {**ROWS[1], "position": (1.5, 3.2)}], **SHOP)
    st = service.move_object(d, "b", (1.5, 2.8), correction=True)
    assert by_id(st)["b"]["position"] == pytest.approx((1.5, 2.8)) and st["auto_fixed"][0]["object"] == "a"
    make(tmp_path, [ROWS[0], {**ROWS[1], "position": (1.5, 3.2)}], **SHOP)
    token = service.ACTOR.set("preview")
    try:
        st = service.move_object(d, "b", (1.5, 2.8))
    finally:
        service.ACTOR.reset(token)
    assert "auto_fixed" not in st and "narrow_aisle" in kinds(st)  # nothing moves under the user's hands
    assert service.load(d).obj("b").placed_by_user
    st = service.move_object(d, "a", (1.5, 1.5), yaw_deg=180)  # the agent's next save: a was touched, b is the user's
    assert "auto_fixed" not in st and "narrow_aisle" in kinds(st)


def test_off_by_the_spec_the_environment_and_the_switch(tmp_path, monkeypatch):
    st = make(tmp_path, ROWS, auto_fix=False, **SHOP)
    assert "auto_fixed" not in st and "narrow_aisle" in kinds(st)
    st = service.set_auto_fix(tmp_path / "t", True)
    assert st["auto_fixed"][0]["object"] == "b" and "narrow_aisle" not in kinds(st)
    monkeypatch.setenv("AGENTIC_SIM_WORLD_GEN_AUTO_FIX", "0")
    assert "narrow_aisle" in kinds(make(tmp_path, ROWS, name="env", **SHOP))


def test_a_fix_that_adds_a_problem_is_not_made(tmp_path):
    # b has a wall 5 cm behind it and a (given) cannot move: moving b back would put it in the wall
    rows = [ROWS[0], {**ROWS[1], "position": (1.5, 2.8)}]
    st = make(tmp_path, rows, depth=3.1, assumptions=[{"item": "position", "value": "ここ", "given": True, "object": "a"}], **SHOP)
    assert "auto_fixed" not in st and "narrow_aisle" in kinds(st)
    assert by_id(st)["b"]["position"] == pytest.approx((1.5, 2.8))


def test_touching_jointed_chairs_are_parted(tmp_path):
    st = make(tmp_path, [{"id": "chairs", "query": "swivel chair", "position": [1.8, 1.0], "array": {"count": 3, "spacing": 0.0}}])
    assert "too_close" not in kinds(st)
    (f,) = st["auto_fixed"]
    assert f["issue"] == "too_close" and f["object"] == "chairs" and f["spacing"] == [0.0, 0.04]
    # two rows that touch part by the aisle
    st = make(tmp_path, [{"id": "chairs", "query": "swivel chair", "position": [1.8, 1.5],
                          "array": {"count": 3, "rows": 2, "spacing": 0.1, "aisle": 0.0}}], name="rows")
    assert "too_close" not in kinds(st) and st["auto_fixed"][0]["aisle"] == [0.0, 0.04]
    # two chairs of their own
    st = make(tmp_path, [{"id": "c1", "query": "swivel chair", "position": [1.5, 1.0]},
                         {"id": "c2", "query": "swivel chair", "position": [2.11, 1.0]}], name="two")
    assert "too_close" not in kinds(st) and st["auto_fixed"][0]["object"] == "c2"
    assert by_id(st)["c2"]["position"][0] == pytest.approx(2.14, abs=0.011) and by_id(st)["c1"]["position"] == pytest.approx((1.5, 1.0))


def test_a_tv_with_no_foot_is_hung_behind_its_stand(tmp_path):
    objects = [{"id": "stand", "query": "tv stand", "layout": {"against_wall": "north", "along": 0.3}},
               {"id": "tv", "query": "television", "layout": {"on": "stand"}}]
    st = make(tmp_path, objects, width=3.64, depth=2.73)
    assert st["issues"] == []
    (f,) = st["auto_fixed"]
    assert f["issue"] == "tips_over" and f["hung"] == {"wall": "north", "wall_height": 0.55, "above": "stand"}
    tv, stand = by_id(st)["tv"], by_id(st)["stand"]
    assert tv["layout"]["wall_height"] == 0.55 and "on" not in {k for k, v in tv["layout"].items() if v}
    assert tv["position"][0] == pytest.approx(stand["position"][0], abs=0.01) and tv["position"][1] > stand["position"][1]
    # the agent puts it back on the stand (the user asked for it there): the fix is not made a second time
    d = tmp_path / "t"
    st = service.set_layout(d, "tv", {"on": "stand"})
    assert "auto_fixed" not in st and [i["kind"] for i in st["issues"]] == ["tips_over"]
    st = service.set_room(d, height=2.5)
    assert "auto_fixed" not in st and service.load(d).obj("tv").layout.on == "stand"
    # a stand in the middle of the room has no wall behind it: the issue stays
    st = make(tmp_path, [{"id": "stand", "query": "tv stand", "position": [1.8, 1.3]}, objects[1]], width=3.64, depth=2.73, name="mid")
    assert "auto_fixed" not in st and [i["kind"] for i in st["issues"]] == ["tips_over"]


def test_mcp_tools(tmp_path):
    from agentic_sim_world_gen import mcp_server

    make(tmp_path, ROWS, auto_fix=False, **SHOP)
    d = str(tmp_path / "t")
    out = mcp_server.set_auto_fix(d, True)
    assert out["auto_fixed"][0]["object"] == "b" and "auto_fixed_hint" in out
    out = mcp_server.move_object(d, "b", (1.5, 2.9), correction=True)
    assert service.load(d).obj("b").placed_by_user and out["auto_fixed"][0]["object"] == "a"


def test_cli(tmp_path, capsys):
    from agentic_sim_world_gen import cli

    make(tmp_path, ROWS, auto_fix=False, **SHOP)
    assert cli.main(["auto-fix", str(tmp_path / "t"), "on"]) == 0
    out = capsys.readouterr().out
    assert '"object": "b"' in out and "auto fix on: 0 issue(s), 0 usability warning(s)" in out
    assert cli.main(["auto-fix", str(tmp_path / "t"), "off"]) == 0 and not service.load(tmp_path / "t").spec.auto_fix
