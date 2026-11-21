"""the solver measures a part stretched by fit_scale at the size compose gives it, and does not tuck it by its
knee grid (measured at the asset's own size)."""

import pytest

from agentic_sim_world_gen import service
from agentic_sim_world_gen.layout.array import fitted
from agentic_sim_world_gen.layout.solver import NEAR_GAP, front_gap, solve
from tests import test_knee_tuck as kt
from tests.test_knee_tuck import make, rec

fake = kt.fake  # the fixture: knee.scan from the test grids
DESK_Y = 2.73 - 0.35 - 0.02  # desk against the north wall


def test_fitted_is_the_stretched_size():
    a = rec("c", "curtain", 1.0, 0.1, 2.0)
    assert fitted(service.ObjectSpec(id="c", query="curtain"), a) is a
    o = service.ObjectSpec(id="c", query="curtain", fit_scale=(1.5, 2.0, 1.1))
    assert fitted(o, a).size_m == pytest.approx((1.5, 0.2, 2.2))


def test_a_stretched_chair_stands_at_its_stretched_depth_and_is_not_tucked(tmp_path, fake):
    d = make(tmp_path, {"in_front_of": "desk"})["scene_dir"]
    s = service.load(d)
    s.obj("chair").fit_scale = (1.0, 2.0, 1.0)  # 0.55 -> 1.1 m deep
    assert solve(s) == []
    assert s.obj("chair").position[1] == pytest.approx(DESK_Y - 0.35 - 0.55 - NEAR_GAP)
    assert front_gap(s, "chair") == pytest.approx(NEAR_GAP)


def test_stretched_chairs_at_an_array_of_desks_stand_at_their_stretched_depth(tmp_path):
    """The _seat path: chairs stretched to 0.825 m deep stand NEAR_GAP off the desks, not tucked by their grid."""
    from tests import test_seated_chairs as sc

    service.set_provider(type("P", (), {"name": "f", "search": lambda self, q, limit=10, size=None:
                                         kt.CATALOG.get(q, [])})())
    try:
        d = sc.make(tmp_path, {"count": 1, "rows": 1}, {"layout": {"in_front_of": "desks"}, "array": {"count": 2}})
        s = service.load(d["scene_dir"])
        s.obj("chairs").fit_scale = (1.0, 1.5, 1.0)
        solve(s)
        assert front_gap(s, "chairs") == pytest.approx(NEAR_GAP)
        (_, _, y0, _), *_ = s.obj("chairs").array.seats
        desk_y = s.obj("desks").position[1]
        assert abs(y0 - desk_y) == pytest.approx(0.35 + 0.4125 + NEAR_GAP)
    finally:
        service.set_provider(None)


def test_a_curtain_is_measured_at_the_stretch_this_solve_gives_it(tmp_path):
    """ext follows the fit_scale the solve sets (here the first one, nothing saved yet), not the one saved last
    (review): a plant next_to the curtain stands off its stretched width (1.6 + 0.2 m), not its own 1.9 m."""
    from agentic_sim_world_gen.layout.solver import SIDE_GAP
    from tests import test_window_fit as wf

    cat = {"curtain": wf.CATALOG["curtain"], "plant": [rec("plant", "plant", 0.3, 0.3, 0.8)]}
    service.set_provider(type("P", (), {"name": "f", "search": lambda self, q, limit=10, size=None: cat.get(q, [])})())
    try:
        st = service.create_scene({"name": "w", "room": wf.ROOM, "objects": [
            {"id": "c", "query": "curtain", "covers": "win"},
            {"id": "p", "query": "plant", "layout": {"next_to": "c"}},
        ]}, tmp_path / "w")
    finally:
        service.set_provider(None)
    o = {x["id"]: x for x in st["objects"]}
    assert o["c"]["fit_scale"][0] == pytest.approx(1.8 / 1.9, abs=1e-3)
    assert abs(o["p"]["position"][0] - o["c"]["position"][0]) == pytest.approx(0.9 + 0.15 + SIDE_GAP, abs=1e-3)
    s = service.load(st["scene_dir"])  # the window made narrower: one solve, the saved stretch is the old one
    s.spec.room.openings[0].width = 1.0
    solve(s)
    c, p = s.obj("c"), s.obj("p")
    assert c.fit_scale[0] == pytest.approx(1.2 / 1.9, abs=1e-3)
    assert abs(p.position[0] - c.position[0]) == pytest.approx(0.6 + 0.15 + SIDE_GAP, abs=1e-3)
