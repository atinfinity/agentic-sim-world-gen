"""An array of chairs in_front_of an array of desks: `count` chairs at each desk, following the desks' rows and
tucked under them. Offline: the knee grids come from test_knee_tuck's boxes."""

import json
import math

import pytest
from test_knee_tuck import CATALOG, DESK, GRIDS, box, rec

from agentic_sim_world_gen import service
from agentic_sim_world_gen.layout import knee
from agentic_sim_world_gen.layout.array import instances, n_units
from agentic_sim_world_gen.scene.spec import Array, Layout, ObjectSpec

ROOM = {"width": 6.0, "depth": 6.0, "height": 2.7}
SCAN = knee.scan  # the real one: conftest replaces it for every test
TUCK = knee.TUCK_FRAC * 0.55  # the chair (0.55 deep) goes this far under the open desk (0.7 deep)


@pytest.fixture
def fake(monkeypatch):
    """test_knee_tuck's catalog, with every scan of its desk and chair answered by their boxes."""
    service.set_provider(type("P", (), {"name": "f", "search": lambda self, q, limit=10, size=None: CATALOG.get(q, [])})())
    scanned = []

    def scan(uri, cache_dir):
        scanned.append(uri)
        return knee.grid_from_boxes(GRIDS[uri]) if uri in GRIDS else None

    monkeypatch.setattr(knee, "scan", scan)
    yield scanned
    service.set_provider(None)


def make(tmp_path, desks: dict, chairs: dict, name="t"):
    return service.create_scene({"name": name, "room": ROOM, "objects": [
        {"id": "desks", "query": "desk", "layout": {"center": True}, "array": desks},
        {"id": "chairs", "query": "office chair", **chairs},
    ]}, tmp_path / name)


def seats(tmp_path, name="t"):
    kept = json.loads((tmp_path / name / "scene.resolved.json").read_text())
    o = next(o for o in kept["spec"]["objects"] if o["id"] == "chairs")
    return {n: (x, y, yaw) for n, x, y, yaw in o["array"]["seats"]}


def desk_rows(tmp_path, name="t"):
    kept = json.loads((tmp_path / name / "scene.resolved.json").read_text())
    o = ObjectSpec.model_validate(next(o for o in kept["spec"]["objects"] if o["id"] == "desks"))
    return {u.id.split(".")[1]: u for u in instances(o, rec("desk", "desk", 1.4, 0.7, 0.74))}


def test_two_chairs_at_each_desk_tucked_under(tmp_path, fake):
    st = make(tmp_path, {"count": 3, "rows": 3, "aisle": 0.9},
              {"layout": {"in_front_of": "desks"}, "array": {"count": 2}})
    s, d = seats(tmp_path), desk_rows(tmp_path)
    assert len(s) == 18  # 3 x 3 desks, 2 chairs each
    for r in range(3):
        for c in range(3):
            u = d[f"r{r}c{c}"]  # desks face -Y (south): their chairs stand south of them, facing north
            for i, side in enumerate((-1, 1)):
                x, y, yaw = s[f"r{r}c{2 * c + i}"]
                assert x == pytest.approx(u.position[0] + side * 0.35, abs=1e-3)  # a quarter of the desk's width
                assert y == pytest.approx(u.position[1] - 0.35 - 0.275 + TUCK, abs=0.006)
                assert math.cos(math.radians(yaw - u.yaw_deg - 180.0)) == pytest.approx(1.0)  # faces the desk
    assert [i for i in st["issues"] if i["kind"] in ("overlap", "chair_far", "chair_facing", "unseated_chairs",
                                                     "bad_layout")] == []
    assert sorted(set(fake)) == ["https://example.com/chair.usd", "https://example.com/desk.usd"]  # measured


def test_without_grids_or_with_a_gap_they_keep_the_usual_gap(tmp_path, fake):
    GRIDS.pop("https://example.com/desk.usd")
    try:
        make(tmp_path, {"count": 2, "rows": 2}, {"layout": {"in_front_of": "desks"}, "array": {"count": 1}})
    finally:
        GRIDS["https://example.com/desk.usd"] = DESK
    s, d = seats(tmp_path), desk_rows(tmp_path)
    assert s["r1c0"][1] == pytest.approx(d["r1c0"].position[1] - 0.35 - 0.275 - 0.10, abs=1e-3)
    assert s["r1c0"][0] == pytest.approx(d["r1c0"].position[0], abs=1e-3)  # one chair: the desk's middle
    make(tmp_path, {"count": 2, "rows": 2}, {"layout": {"in_front_of": "desks", "gap": 0.2}, "array": {"count": 1}},
         name="g")
    s, d = seats(tmp_path, "g"), desk_rows(tmp_path, "g")
    assert s["r0c1"][1] == pytest.approx(d["r0c1"].position[1] - 0.35 - 0.275 - 0.2, abs=1e-3)


def test_rows_facing_each_other_get_chairs_on_their_own_side(tmp_path, fake):
    make(tmp_path, {"count": 2, "rows": 2, "alternate": True, "aisle": 1.6},
         {"layout": {"in_front_of": "desks"}, "array": {"count": 1}})
    s, d = seats(tmp_path), desk_rows(tmp_path)
    front, back = d["r0c0"], d["r1c0"]
    assert s["r0c0"][1] == pytest.approx(front.position[1] - 0.35 - 0.275 + TUCK, abs=0.006)
    assert s["r1c0"][1] == pytest.approx(back.position[1] + 0.35 + 0.275 - TUCK, abs=0.006)  # turned around
    assert abs((s["r0c0"][2] - s["r1c0"][2]) % 360.0 - 180.0) < 1e-6


def test_too_many_chairs_for_a_desk_are_reported(tmp_path, fake):
    st = make(tmp_path, {"count": 1}, {"layout": {"in_front_of": "desks"}, "array": {"count": 3}})
    bad = [i for i in st["issues"] if i["kind"] == "bad_layout"]
    assert bad and "lower array.count (chairs per desk)" in bad[0]["detail"]


def test_chairs_laid_out_on_their_own_among_desks_are_pointed_at_in_front_of(tmp_path, fake):
    st = service.create_scene({"name": "t", "room": ROOM, "objects": [
        {"id": "desks", "query": "desk", "position": [3.0, 3.6], "array": {"count": 2, "rows": 2, "aisle": 0.9}},
        {"id": "chairs", "query": "office chair", "position": [3.0, 3.0], "yaw_deg": 180.0,
         "array": {"count": 4, "rows": 2, "aisle": 1.05, "spacing": 0.2}},
    ]}, tmp_path / "t")
    hit = [i for i in st["issues"] if i["kind"] == "unseated_chairs"]
    assert hit and hit[0]["objects"] == ["chairs", "desks"] and "(here 2)" in hit[0]["detail"]


def test_seats_are_used_only_while_the_chairs_are_at_the_desks():
    a = rec("chair", "office chair", 0.55, 0.55, 0.85)
    o = ObjectSpec(id="c", query="chair", position=(1.0, 1.0), array=Array(count=2, seats=[("r0c0", 5.0, 5.0, 90.0)]),
                   layout=Layout(in_front_of="desks"))
    assert [(u.id, u.position) for u in instances(o, a)] == [("c.r0c0", (5.0, 5.0))]
    o.layout = None  # moved by hand: the array's own grid again
    assert [u.id for u in instances(o, a)] == ["c.r0c0", "c.r0c1"]


def test_poly_haven_desks_are_measured_from_the_fetched_model(tmp_path, monkeypatch):
    pxr = pytest.importorskip("pxr")
    from pxr import Usd, UsdGeom

    from agentic_sim_world_gen import polyhaven_models

    f = tmp_path / "model.usda"
    st = Usd.Stage.CreateNew(str(f))
    UsdGeom.SetStageUpAxis(st, "Z")
    UsdGeom.SetStageMetersPerUnit(st, 1.0)
    st.SetDefaultPrim(UsdGeom.Xform.Define(st, "/Desk").GetPrim())
    for i, (_, mn, mx) in enumerate(DESK):
        c = UsdGeom.Cube.Define(st, f"/Desk/P{i}")
        c.CreateSizeAttr(1.0)
        xf = UsdGeom.Xformable(c)
        xf.AddTranslateOp().Set(tuple((a + b) / 2 for a, b in zip(mn, mx, strict=True)))
        xf.AddScaleOp().Set(tuple(b - a for a, b in zip(mn, mx, strict=True)))
    st.Save()
    monkeypatch.setattr(polyhaven_models, "local_usd", lambda uri, timeout=120: f)
    g = SCAN("polyhaven:desk", tmp_path / "cache")
    assert g is not None and knee.at(g, "lo", 0.0, -0.3) == pytest.approx(0.71, abs=0.03)

    def offline(uri, timeout=120):
        raise OSError("offline")

    monkeypatch.setattr(polyhaven_models, "local_usd", offline)
    assert SCAN("polyhaven:other", tmp_path / "cache") is None
    assert pxr


def test_the_count_of_seated_chairs_is_their_seats(tmp_path, fake):
    """The plan's label, the gallery and the body count read the seats, not the per-desk count."""
    make(tmp_path, {"count": 3, "rows": 3, "aisle": 0.9}, {"layout": {"in_front_of": "desks"}, "array": {"count": 2}})
    kept = json.loads((tmp_path / "t" / "scene.resolved.json").read_text())
    by = {o["id"]: ObjectSpec.model_validate(o) for o in kept["spec"]["objects"]}
    assert n_units(by["chairs"]) == 18 and n_units(by["desks"]) == 9
    assert "×18" in (tmp_path / "t" / "layout.svg").read_text()
    assert service._units(by["chairs"]) == 18


def test_chairs_its_legs_keep_out_stand_at_the_desks_edge(tmp_path, fake):
    """a classroom's dining tables with wide corner legs: two chairs per desk cannot go under, and the usual
    0.10 m gap read as far off in the render. Measured and kept out, the seat comes to the desk's edge."""
    legs = [box(sx * 0.7 - (0.2 if sx > 0 else 0), sy * 0.35 - (0.15 if sy > 0 else 0), 0,
                sx * 0.7 + (0.2 if sx < 0 else 0), sy * 0.35 + (0.15 if sy < 0 else 0), 0.71)
            for sx in (-1, 1) for sy in (-1, 1)]
    GRIDS["https://example.com/desk.usd"] = [DESK[0], *legs]
    try:
        make(tmp_path, {"count": 2, "rows": 2}, {"layout": {"in_front_of": "desks"}, "array": {"count": 2}})
        make(tmp_path, {"count": 2, "rows": 2}, {"layout": {"in_front_of": "desks"}, "array": {"count": 1}}, "one")
    finally:
        GRIDS["https://example.com/desk.usd"] = DESK
    s, d = seats(tmp_path), desk_rows(tmp_path)
    assert s["r1c0"][1] == pytest.approx(d["r1c0"].position[1] - 0.35 - 0.275 - 0.03, abs=1e-3)
    one = seats(tmp_path, "one")  # one chair in the middle passes between the legs
    assert one["r1c0"][1] == pytest.approx(d["r1c0"].position[1] - 0.35 - 0.275 + TUCK, abs=0.006)


def test_legs_in_the_way_of_the_even_spread_chairs_move_inward(tmp_path, fake):
    """legs 0.04-0.10 m in from the desk's ends: two chairs spread evenly (offsets +-0.35) run into them and
    stood at the edge; moved toward the middle (still apart) they pass between the legs and go under."""
    legs = [box(x0, y0, 0, x0 + 0.06, y0 + 0.05, 0.71) for x0 in (-0.66, 0.6) for y0 in (-0.35, 0.3)]
    GRIDS["https://example.com/desk.usd"] = [DESK[0], *legs]
    try:
        make(tmp_path, {"count": 2, "rows": 2}, {"layout": {"in_front_of": "desks"}, "array": {"count": 2}})
    finally:
        GRIDS["https://example.com/desk.usd"] = DESK
    s, d = seats(tmp_path), desk_rows(tmp_path)
    desk = d["r1c0"].position
    left, right = sorted((s["r1c0"], s["r1c1"]))
    for x, y, _ in (left, right):
        assert y == pytest.approx(desk[1] - 0.35 - 0.275 + TUCK, abs=0.006)  # under, not at the edge
        assert abs(x - desk[0]) < 0.35 - 0.03  # moved in from +-0.35 ...
    assert right[0] - left[0] >= 0.55 + 0.01 - 1e-6  # ... and still apart


def test_both_cannot_move_in_the_one_moved_further_stays_at_the_edge(tmp_path, fake):
    """the left legs 0.12 m further in: the left chair would have to come to -0.23 and the right one to +0.305,
    closer than a chair's width + SEAT_SPACING. The left one (moved further) goes back to the even spread, at the
    desk's edge; the right one goes under. They never overlap."""
    legs = [box(x0, y0, 0, x0 + 0.06, y0 + 0.05, 0.71) for x0 in (-0.585, 0.6) for y0 in (-0.35, 0.3)]
    GRIDS["https://example.com/desk.usd"] = [DESK[0], *legs]
    try:
        make(tmp_path, {"count": 2, "rows": 2}, {"layout": {"in_front_of": "desks"}, "array": {"count": 2}})
    finally:
        GRIDS["https://example.com/desk.usd"] = DESK
    s, d = seats(tmp_path), desk_rows(tmp_path)
    desk = d["r1c0"].position
    left, right = sorted((s["r1c0"], s["r1c1"]))
    edge = desk[1] - 0.35 - 0.275 - 0.03
    assert left[0] == pytest.approx(desk[0] - 0.35, abs=1e-3) and left[1] == pytest.approx(edge, abs=1e-3)
    assert right[0] < desk[0] + 0.35 - 0.03 and right[1] == pytest.approx(edge + 0.03 + TUCK, abs=0.006)
    assert right[0] - left[0] >= 0.55 + 0.01 - 1e-6


def test_inward_keeps_out_what_moving_does_not_help():
    """a chair moved all the way still kept out (its arms under the apron) is not tried step by step."""
    from agentic_sim_world_gen.layout.solver import _inward

    tried = []

    def tuck(off):
        tried.append(off)
        return 0.0
    offs, deeps = _inward([-0.35, 0.35], [0.0, 0.0], tuck, 0.55, 0.7)
    assert offs == [-0.35, 0.35] and deeps == [0.0, 0.0] and len(tried) == 2
