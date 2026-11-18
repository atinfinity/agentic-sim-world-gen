"""A chair in front of a desk goes under it as far as the measured knee space allows. Offline."""

import dataclasses
import json

import pytest

from agentic_sim_world_gen import service
from agentic_sim_world_gen.assets.record import AssetRecord
from agentic_sim_world_gen.layout import knee
from agentic_sim_world_gen.layout.validate import LEG_MARGIN


def rec(name, cls, w, d, h, front_deg=270.0):
    return AssetRecord(
        id=name, source="fake", uri=f"https://example.com/{name}.usd", name=name,
        bbox_min=(-w / 2, -d / 2, 0.0), bbox_max=(w / 2, d / 2, h), up_axis="Z", meters_per_unit=1.0,
        semantic_class=cls, license="CC-BY 4.0", front_deg=front_deg,
    )


def box(x0, y0, z0, x1, y1, z1):
    return ("p", (x0, y0, z0), (x1, y1, z1))


# a desk 1.4 x 0.7 x 0.74 (front -Y): a top 0.03 thick and a leg panel at each end
DESK = [box(-0.7, -0.35, 0.71, 0.7, 0.35, 0.74), box(-0.7, -0.35, 0, -0.66, 0.35, 0.71),
        box(0.66, -0.35, 0, 0.7, 0.35, 0.71)]
# the same with a modesty panel just behind the front edge
PANEL = DESK + [box(-0.66, -0.32, 0.1, 0.66, -0.3, 0.71)]
# a chair 0.55 x 0.55 x 0.85 (front -Y): a seat at 0.45-0.5, a base, a back at +Y
CHAIR = [box(-0.27, -0.27, 0.45, 0.27, 0.27, 0.5), box(-0.27, -0.27, 0, 0.27, 0.27, 0.08),
         box(-0.03, -0.03, 0.08, 0.03, 0.03, 0.45), box(-0.25, 0.22, 0.5, 0.25, 0.275, 0.85)]
# arms up to 0.70: they would hit the top's underside (0.71) with the clearance
ARMS = CHAIR + [box(-0.275, -0.2, 0.5, -0.23, 0.15, 0.70), box(0.23, -0.2, 0.5, 0.275, 0.15, 0.70)]


def grid(parts):
    return json.loads(json.dumps(knee.grid_from_boxes(parts)))  # as kept in the scene


def depth(desk, chair, yaw=0.0, desk_side=0.7, chair_side=0.275):
    tf = yaw + 270.0  # the desk's front (-Y) turned by yaw; the chair faces it
    return knee.tuck_depth(grid(desk), (2.0, 2.0), yaw, 0.35, desk_side, grid(chair), yaw + 180.0, 0.275,
                           chair_side, tf)


def test_open_desk_takes_the_chair_up_to_the_limit_in_any_direction():
    for yaw in (0.0, 90.0, 180.0, -90.0, 30.0):
        assert depth(DESK, CHAIR, yaw) == pytest.approx(knee.TUCK_FRAC * 0.55, abs=0.006), yaw


def test_what_stops_it():
    assert depth(PANEL, CHAIR) == 0.0  # a modesty panel at 0.03-0.05 m behind the edge: not even MIN_TUCK
    # the arms (from 0.075 m behind the seat's front) reach the top's underside: only the seat's front goes under
    assert 0.05 <= depth(DESK, ARMS) < 0.075
    assert depth(DESK, CHAIR, desk_side=0.275 + LEG_MARGIN - 0.01) == 0.0  # it would run into the corner legs
    one_mesh = [box(-0.7, -0.35, 0, 0.7, 0.35, 0.74)]  # a desk known only as one box
    assert depth(one_mesh, CHAIR) == 0.0


def test_panel_further_back_lets_it_in_up_to_the_panel():
    back = DESK + [box(-0.66, 0.1, 0.1, 0.66, 0.12, 0.71)]  # panel 0.45 m behind the front edge: past the limit
    assert depth(back, CHAIR) == pytest.approx(knee.TUCK_FRAC * 0.55, abs=0.006)
    near = DESK + [box(-0.66, -0.25, 0.1, 0.66, -0.23, 0.71)]  # 0.10-0.12 m behind it
    assert 0.05 <= depth(near, CHAIR) <= 0.10 - knee.SIDE_CLEAR


def test_floor_step_counts():
    t = knee.tuck_depth(grid(DESK), (2, 2), 0, 0.35, 0.7, grid(CHAIR), 180, 0.275, 0.275, 270, z_step=0.3)
    assert t == 0.0  # the chair stands 0.3 m higher: its seat is at the desk's underside


def test_mesh_faces_show_the_knee_space_of_a_one_mesh_desk():
    pxr = pytest.importorskip("pxr")
    from pxr import Usd, UsdGeom

    st = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageUpAxis(st, "Z")
    UsdGeom.SetStageMetersPerUnit(st, 1.0)
    root = UsdGeom.Xform.Define(st, "/Desk")
    st.SetDefaultPrim(root.GetPrim())
    pts, counts, idx = [], [], []
    for _, mn, mx in DESK:  # the three parts as one mesh (six quads each)
        b = len(pts)
        pts += [(x, y, z) for z in (mn[2], mx[2]) for y in (mn[1], mx[1]) for x in (mn[0], mx[0])]
        for q in ((0, 1, 3, 2), (4, 5, 7, 6), (0, 1, 5, 4), (2, 3, 7, 6), (0, 2, 6, 4), (1, 3, 7, 5)):
            counts.append(4)
            idx += [b + i for i in q]
    m = UsdGeom.Mesh.Define(st, "/Desk/Mesh")
    m.CreatePointsAttr(pts)
    m.CreateFaceVertexCountsAttr(counts)
    m.CreateFaceVertexIndicesAttr(idx)
    m.CreateExtentAttr(UsdGeom.PointBased.ComputeExtent(pts))
    g = knee.heights_of_stage(st)
    assert knee.at(g, "lo", 0.0, -0.3) == pytest.approx(0.71)  # under the top: clear down to its underside
    assert knee.at(g, "lo", -0.68, 0.0) == pytest.approx(0.0)  # a leg
    assert knee.tuck_depth(g, (0, 0), 0, 0.35, 0.7, grid(CHAIR), 180, 0.275, 0.275, 270) > 0.2
    assert pxr


CATALOG = {"desk": [rec("desk", "desk", 1.4, 0.7, 0.74)], "office chair": [rec("chair", "office chair", 0.55, 0.55, 0.85)],
           "shelf": [rec("shelf", "bookcase", 0.8, 0.35, 1.8)]}
GRIDS = {"https://example.com/desk.usd": DESK, "https://example.com/chair.usd": CHAIR,
         "https://example.com/shelf.usd": DESK}


@pytest.fixture
def fake(monkeypatch):
    service.set_provider(type("P", (), {"name": "f", "search": lambda self, q, limit=10, size=None: CATALOG.get(q, [])})())
    scanned = []

    def scan(uri, cache_dir):
        scanned.append(uri)
        return knee.grid_from_boxes(GRIDS[uri]) if uri in GRIDS else None

    monkeypatch.setattr(knee, "scan", scan)
    yield scanned
    service.set_provider(None)


def make(tmp_path, chair_layout):
    return service.create_scene({"name": "t", "room": {"width": 3.64, "depth": 2.73, "height": 2.4}, "objects": [
        {"id": "desk", "query": "desk", "layout": {"against_wall": "north"}},
        {"id": "chair", "query": "office chair", "layout": chair_layout},
        {"id": "shelf", "query": "shelf", "layout": {"against_wall": "west"}},
    ]}, tmp_path / "t")


def test_scene_tucks_the_chair_and_check_accepts_it(tmp_path, fake):
    st = make(tmp_path, {"in_front_of": "desk"})
    o = {x["id"]: x for x in st["objects"]}
    desk_y = 2.73 - 0.35 - 0.02
    t = knee.TUCK_FRAC * 0.55
    assert o["chair"]["position"][1] == pytest.approx(desk_y - 0.35 - 0.275 + t, abs=0.006)
    assert st["issues"] == []
    assert sorted(fake) == ["https://example.com/chair.usd", "https://example.com/desk.usd"]  # not the shelf
    kept = json.loads((tmp_path / "t" / "scene.resolved.json").read_text())
    assert all(p["candidates"][p["selected"]].get("knee") for p in kept["placements"] if p["object_id"] != "shelf")


def test_given_gap_or_no_grids_keep_the_old_layout(tmp_path, fake):
    desk_y = 2.73 - 0.35 - 0.02
    st = make(tmp_path, {"in_front_of": "desk", "gap": 0.2})
    o = {x["id"]: x for x in st["objects"]}
    assert o["chair"]["position"][1] == pytest.approx(desk_y - 0.35 - 0.275 - 0.2)
    assert fake == []  # a given gap is not measured for
    GRIDS.pop("https://example.com/desk.usd")
    try:
        st = make(tmp_path / "b", {"in_front_of": "desk"})
    finally:
        GRIDS["https://example.com/desk.usd"] = DESK
    o = {x["id"]: x for x in st["objects"]}
    assert o["chair"]["position"][1] == pytest.approx(desk_y - 0.35 - 0.275 - 0.10)


def test_a_part_ending_inside_a_cell_marks_it():
    g = knee.grid_from_boxes([box(0, 0, 0.7, 0.3, 0.3, 0.72), box(0, 0, 0, 0.05, 0.05, 0.7)])
    assert knee.at(g, "lo", 0.045, 0.045) == pytest.approx(0.0)  # the leg's last 0.02 m in the second cell
    assert knee.at(g, "lo", 0.065, 0.065) == pytest.approx(0.7)
    assert isinstance(g["lo"][0], str)  # kept as millimeters per row


def test_capped_like_validate():
    assert depth(DESK, CHAIR) > 0.15
    t = knee.tuck_depth(grid(DESK), (2, 2), 0, 0.35, 0.7, grid(CHAIR), 180, 0.275, 0.275, 270, cap=0.12)
    assert t == pytest.approx(0.12)


def test_a_level_under_both_is_one_floor(tmp_path, fake):
    """Both on a 0.3 m level: the chair's z is the level's only after it is placed (the arms still stop it)."""
    GRIDS["https://example.com/chair.usd"] = ARMS
    try:
        y = {}
        for name, levels in (("flat", []), ("level", [{"id": "up", "rect": [0, 1.0, 3.64, 2.73], "height": 0.3}])):
            st = service.create_scene({"name": "t", "room": {"width": 3.64, "depth": 2.73, "height": 2.4, "levels": levels},
                                       "objects": [{"id": "desk", "query": "desk", "layout": {"against_wall": "north"}},
                                                   {"id": "chair", "query": "office chair",
                                                    "layout": {"in_front_of": "desk"}}]}, tmp_path / name)
            o = {x["id"]: x for x in st["objects"]}
            y[name] = o["chair"]["position"][1]
            assert o["chair"].get("z", 0) == o["desk"].get("z", 0)
        assert y["level"] == pytest.approx(y["flat"])
        desk_y = 2.73 - 0.35 - 0.02
        assert desk_y - 0.35 - 0.275 + 0.05 <= y["flat"] < desk_y - 0.35 - 0.275 + 0.075
    finally:
        GRIDS["https://example.com/chair.usd"] = CHAIR


def test_unreadable_is_tried_again_later_and_arrays_are_not_measured(tmp_path, fake, monkeypatch):
    GRIDS.pop("https://example.com/desk.usd")
    try:
        make(tmp_path, {"in_front_of": "desk"})
    finally:
        GRIDS["https://example.com/desk.usd"] = DESK
    kept = json.loads((tmp_path / "t" / "scene.resolved.json").read_text())
    desk = next(p for p in kept["placements"] if p["object_id"] == "desk")
    assert desk["candidates"][desk["selected"]]["knee"]["failed"]
    fake.clear()
    service.move_object(tmp_path / "t", "shelf", position=(0.3, 1.0))  # saved again soon: not fetched again
    assert fake == []
    monkeypatch.setattr(service, "KNEE_RETRY_S", -1)
    st = service.move_object(tmp_path / "t", "shelf", position=(0.3, 1.2))  # later: measured, and tucked
    assert "https://example.com/desk.usd" in fake
    o = {x["id"]: x for x in st["objects"]}
    assert o["chair"]["position"][1] == pytest.approx(2.73 - 0.35 - 0.02 - 0.35 - 0.275 + knee.TUCK_FRAC * 0.55,
                                                      abs=0.006)
    fake.clear()
    service.create_scene({"name": "t", "room": {"width": 3.64, "depth": 2.73, "height": 2.4}, "objects": [
        {"id": "desk", "query": "desk", "layout": {"against_wall": "north"}, "array": {"count": 2}},
        {"id": "chair", "query": "office chair", "layout": {"in_front_of": "desk"}},
    ]}, tmp_path / "arr")
    assert fake == []


def test_record_keeps_the_grid():
    r = dataclasses.replace(rec("desk", "desk", 1, 1, 1), knee={"v": 1})
    from agentic_sim_world_gen.scene.spec import record_from_dict, record_to_dict

    assert record_from_dict(record_to_dict(r)).knee == {"v": 1}
