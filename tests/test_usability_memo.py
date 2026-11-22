"""usability inside solver.remembered: the auto fix and the status of one save share the warnings."""

from agentic_sim_world_gen import service
from agentic_sim_world_gen.layout import solver
from agentic_sim_world_gen.layout import usability as U
from tests.test_nav import fake, make  # noqa: F401
from tests.test_usability import DOOR

OBJS = [{"id": "desk", "query": "desk", "layout": {"against_wall": "north"}},
        {"id": "sofa", "query": "sofa", "layout": {"against_wall": "north"}}]


def _count(monkeypatch):
    n = {"made": 0}
    inner = U._warnings

    def counted(scene):
        n["made"] += 1
        return inner(scene)

    monkeypatch.setattr(U, "_warnings", counted)
    return n


def test_once_per_scene_inside(tmp_path, monkeypatch):
    s = service.load(make(tmp_path, OBJS, openings=DOOR))
    solver.solve(s)
    want = U.usability(s)
    n = _count(monkeypatch)
    with solver.remembered():
        a = U.usability(s)
        a.append("x")  # the warnings given out are copies
        assert U.usability(s.model_copy(deep=True)) == want
        assert n["made"] == 1
        s.spec.objects[0].position = (0.5, 0.5)
        U.usability(s)  # another scene
        assert n["made"] == 2
    U.usability(s)
    U.usability(s)  # outside: made each time
    assert n["made"] == 4


def test_a_save_makes_them_once(tmp_path, monkeypatch):
    d = make(tmp_path, OBJS, openings=DOOR)
    n = _count(monkeypatch)
    service.move_object(d, "sofa", position=(1.0, 1.5))  # the auto fix and the status
    assert n["made"] == 1
