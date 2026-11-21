"""a remembered in_front_of gap does not turn off the measured tuck, and set_layout learns the gap the chair
stood at (not NEAR_GAP)."""

import pytest

from agentic_sim_world_gen import preferences, service
from agentic_sim_world_gen.layout.solver import NEAR_GAP, front_gap
from tests import test_knee_tuck as kt
from tests.test_knee_tuck import DESK, GRIDS, knee, make

DESK_Y = 2.73 - 0.35 - 0.02  # desk against the north wall
TUCK = knee.TUCK_FRAC * 0.55
fake = kt.fake  # the fixture: knee.scan from the test grids


def remember(value):
    for n in ("a", "b"):
        preferences.observe("gap", "chair", f"/scenes/{n}", NEAR_GAP, value)


def test_a_remembered_gap_leaves_the_measured_tuck(tmp_path, fake):
    remember(0.3)
    st = make(tmp_path, {"in_front_of": "desk"})
    o = {x["id"]: x for x in st["objects"]}
    assert o["chair"]["position"][1] == pytest.approx(DESK_Y - 0.35 - 0.275 + TUCK, abs=0.006)  # still under
    s = service.load(st["scene_dir"])
    assert s.obj("chair").layout.gap is None and s.obj("chair").layout.remembered_gap == 0.3
    assert sorted(fake) == ["https://example.com/chair.usd", "https://example.com/desk.usd"]  # measured


def test_a_remembered_gap_applies_where_nothing_is_measured(tmp_path, fake):
    remember(0.3)
    GRIDS.pop("https://example.com/desk.usd")
    try:
        st = make(tmp_path, {"in_front_of": "desk"})
    finally:
        GRIDS["https://example.com/desk.usd"] = DESK
    o = {x["id"]: x for x in st["objects"]}
    assert o["chair"]["position"][1] == pytest.approx(DESK_Y - 0.35 - 0.275 - 0.3)
    assert front_gap(service.load(st["scene_dir"]), "chair") == pytest.approx(0.3)


def test_set_layout_learns_from_where_the_chair_stood(tmp_path, fake):
    """Tucked 0.22 m under, the user pulls it out to 0.05 m: that is up (NEAR_GAP 0.10 -> 0.05 read as down)."""
    d = make(tmp_path, {"in_front_of": "desk"})["scene_dir"]
    stood = front_gap(service.load(d), "chair")
    assert stood == pytest.approx(-TUCK, abs=0.006)
    service.set_layout(d, "chair", {"in_front_of": "desk", "gap": 0.05}, correction=True)
    e = next(x for x in preferences._load()["items"] if x["item"] == "gap")
    assert e["observations"][0]["from"] == pytest.approx(stood) and e["observations"][0]["direction"] == "up"


def test_set_layout_back_to_the_automatic_gap_learns_nothing(tmp_path, fake):
    """Gap left out is the measured tuck here: remembered, it would go under desks nobody measured (review)."""
    d = make(tmp_path, {"in_front_of": "desk", "gap": 0.3})["scene_dir"]
    service.set_layout(d, "chair", {"in_front_of": "desk"}, correction=True)
    assert not any(x["item"] == "gap" for x in preferences._load()["items"])


def test_the_remembered_gap_stays_through_other_layout_changes(tmp_path, fake):
    remember(0.3)
    GRIDS.pop("https://example.com/desk.usd")
    try:
        st = make(tmp_path, {"in_front_of": "desk"})
        d = st["scene_dir"]
        assert any(a["item"] == "gap" and "測った位置" in a["value"] for a in st["assumptions"])
        service.set_layout(d, "chair", {"in_front_of": "desk", "facing": "north"}, correction=True)
        s = service.load(d)
        assert s.obj("chair").layout.remembered_gap == 0.3 and front_gap(s, "chair") == pytest.approx(0.3)
        service.set_layout(d, "chair", {"in_front_of": "desk", "gap": 0.2})
        assert service.load(d).obj("chair").layout.remembered_gap is None  # a given gap replaces it
    finally:
        GRIDS["https://example.com/desk.usd"] = DESK


def test_a_layout_option_keeps_the_remembered_gap():
    from agentic_sim_world_gen.scene.spec import Layout, ObjectPatch, ObjectSpec

    o = ObjectSpec(id="chair", query="chair", layout=Layout(in_front_of="desk"))
    o.layout.remembered_gap = 0.3
    patch = ObjectPatch(layout={"in_front_of": "desk", "facing": "north"})
    patch.apply(o)
    assert o.layout.remembered_gap == 0.3 and patch.layout.remembered_gap is None
    ObjectPatch(layout={"in_front_of": "shelf"}).apply(o)
    assert o.layout.remembered_gap is None  # another target
