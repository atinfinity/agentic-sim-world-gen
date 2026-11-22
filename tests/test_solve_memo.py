"""solver.remembered: inside one save a scene equal to one already solved is not solved again."""

from agentic_sim_world_gen import service
from agentic_sim_world_gen.layout import solver
from agentic_sim_world_gen.scene.spec import Assumption
from tests.test_nav import fake, make  # noqa: F401
from tests.test_usability import DOOR

OBJS = [{"id": "shelf", "query": "shelf", "layout": {"against_wall": "north"}},
        {"id": "desk", "query": "desk", "layout": {"against_wall": "east"}}]


def _count(monkeypatch):
    n = {"solved": 0}
    inner = solver._solve

    def counted(scene):
        n["solved"] += 1
        return inner(scene)

    monkeypatch.setattr(solver, "_solve", counted)
    return n


def test_inside_one_solve_per_scene(tmp_path, monkeypatch):
    s = service.load(make(tmp_path, OBJS, openings=DOOR))
    fresh = s.model_copy(deep=True)
    want = s.model_copy(deep=True)
    want_issues = solver.solve(want)
    n = _count(monkeypatch)
    with solver.remembered():
        first = solver.solve(s)
        first.append("x")  # the issues given out are copies
        s.spec.objects[0].position = (0.1, 0.1)  # and so are the objects' values
        objs = list(fresh.spec.objects)
        assert solver.solve(fresh) == want_issues  # an equal scene, another object
        assert all(a is b for a, b in zip(fresh.spec.objects, objs))  # in place: callers hold the objects
        assert fresh.model_dump_json() == want.model_dump_json()
        assert solver.solve(fresh) == want_issues  # the solved state solves to itself
        assert n["solved"] == 1
    fresh.spec.objects[0].position = (0.1, 0.1)
    solver.solve(fresh)  # outside: solved again
    assert n["solved"] == 2


def test_assumptions_are_no_key(tmp_path, monkeypatch):
    s = service.load(make(tmp_path, OBJS, openings=DOOR))
    n = _count(monkeypatch)
    with solver.remembered():
        solver.solve(s)
        s.spec.assumptions.append(Assumption(item="台数", value="1"))  # the save records them after solving
        solver.solve(s)
    assert n["solved"] == 1


def test_a_save_solves_each_scene_once(tmp_path, monkeypatch):
    d = make(tmp_path, OBJS, openings=DOOR)
    n = _count(monkeypatch)
    service.move_object(d, "desk", position=(3.0, 2.0))  # the save, the auto fix and the status
    once = n["solved"]
    with solver.remembered():  # nested: the outer memo holds
        service.move_object(d, "desk", position=(3.0, 2.5))
    assert once == 1 and n["solved"] == 2


def test_a_solved_layout_solves_to_itself(tmp_path):
    """A desk and three sofas on one wall, solved again, are the same with the same issues (the memo's
    solved-state key)."""
    from tests.test_layout import scene

    objects = [{"id": "desk", "query": "desk", "layout": {"against_wall": "north"}},
               *({"id": f"s{i}", "query": "sofa", "layout": {"against_wall": "north"}} for i in range(3))]
    s = service.load(scene(tmp_path, objects)["scene_dir"])
    first = solver._solve(s)
    again = s.model_copy(deep=True)
    assert solver._solve(again) == first and again.model_dump_json() == s.model_dump_json()


def test_nan_is_another_key(tmp_path):
    s = service.load(make(tmp_path, OBJS, openings=DOOR))
    o = s.spec.objects[0]
    keys = set()
    for v in (None, float("nan"), float("inf")):
        o.yaw_deg = v
        keys.add(solver._key(s))
    assert len(keys) == 3
