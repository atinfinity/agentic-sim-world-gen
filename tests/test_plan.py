"""Floor plans: openings, plan walls/zones, the solver/checks on them, USD walls and the importers."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from agentic_sim_world_gen import service
from agentic_sim_world_gen.layout.room import RoomGeometry
from agentic_sim_world_gen.plan.json_plan import from_dict
from agentic_sim_world_gen.scene.spec import Room

from test_layout import CATALOG, fake, objs  # noqa: F401  (fixture: fake asset provider)

T = 0.12
# L-shaped flat: LDK 8 x 4 m (south), bedroom 4 x 3 m (north-west), partition with a door between
L_PLAN = {
    "units": "m",
    "walls": [
        {"id": "s", "start": [0, 0], "end": [8, 0]},
        {"id": "e", "start": [8, 0], "end": [8, 4]},
        {"id": "n1", "start": [8, 4], "end": [4, 4]},
        {"id": "e2", "start": [4, 4], "end": [4, 7]},
        {"id": "n2", "start": [4, 7], "end": [0, 7]},
        {"id": "w", "start": [0, 7], "end": [0, 0]},
        {"id": "mid", "start": [0, 4], "end": [4, 4]},
    ],
    "openings": [
        {"kind": "door", "wall": "mid", "offset": 1.0, "width": 0.8},
        {"kind": "window", "wall": "s", "offset": 4.0, "width": 1.6},
    ],
    "zones": [
        {"id": "ldk", "name": "LDK", "polygon": [[0.06, 0.06], [7.94, 0.06], [7.94, 3.94], [0.06, 3.94]]},
        {"id": "bed", "name": "寝室", "polygon": [[0.06, 4.06], [3.94, 4.06], [3.94, 6.94], [0.06, 6.94]]},
    ],
}


def l_room(**kw) -> dict:
    return {**from_dict(L_PLAN), "height": 2.4, **kw}


def test_normalize_and_size():
    f = from_dict({**L_PLAN, "walls": [{**w, "start": [w["start"][0] - 10, w["start"][1] + 5],
                                         "end": [w["end"][0] - 10, w["end"][1] + 5]} for w in L_PLAN["walls"]]})
    assert f["plan"]["walls"][0]["start"] == [0.06, 0.06]  # shifted so the outer faces start at 0
    r = Room.model_validate({**f, "height": 2.4})
    assert (r.width, r.depth) == pytest.approx((8.12, 7.12))
    assert [o.id for o in r.openings] == ["door_0", "window_1"]
    with pytest.raises(ValueError, match="shift it"):
        Room.model_validate({"plan": {"walls": [{"start": [-1, 0], "end": [3, 0]}]}})
    cm = from_dict({**L_PLAN, "units": "cm", "walls": [{"start": [0, 0], "end": [400, 0], "thickness": 15}]})
    assert cm["plan"]["walls"][0]["end"] == [4.075, 0.075]


def test_faces_split_by_zone():
    g = RoomGeometry(Room.model_validate(l_room()))
    west = g.faces_for("west")
    assert {f.zone for f in west} == {"ldk", "bed"}  # the outer west wall is cut by the partition
    (bed_north,) = g.faces_for("north", "bed")
    assert bed_north.wall == "n2" and bed_north.length == pytest.approx(4 - T)
    ldk_north = g.faces_for("north", "ldk")
    assert {f.wall for f in ldk_north} == {"n1", "mid"}
    assert all(f.facing == pytest.approx(-90) for f in ldk_north)
    assert g.center("bed") == pytest.approx((2.06, 5.56))
    # the window is cut out (sill + above), the door too (above only)
    s = next(w for w in g.walls if w.id == "s")
    assert sorted(k for *_, k in s.pieces()) == ["above", "below", "wall", "wall"]
    mid = next(w for w in g.walls if w.id == "mid")
    assert sorted(k for *_, k in mid.pieces()) == ["above", "wall", "wall"]


def test_solver_uses_zones_and_keeps_doors_free(tmp_path):
    st = service.create_scene({"name": "flat", "room": l_room(), "objects": [
        {"id": "sofa", "query": "sofa", "zone": "ldk", "layout": {"against_wall": "mid"}},
        {"id": "shelf", "query": "bookshelf", "zone": "bed", "layout": {"against_wall": "west"}},
        {"id": "desk", "query": "desk", "zone": "bed", "layout": {"against_wall": "north"}},
        {"id": "chair", "query": "office chair", "zone": "bed", "layout": {"center": True}},
    ]}, tmp_path / "flat")
    assert st["issues"] == []
    o = objs(st)
    sx, sy = o["sofa"]["position"]
    assert sy == pytest.approx(4.06 - T / 2 - 0.45 - 0.02)  # backed against the partition, LDK side
    assert abs(sx - 1.0) >= 0.4 + 0.9 - 1e-6  # moved off the door (door 0.8 wide, sofa 1.8 wide)
    assert o["shelf"]["position"][1] > 4.06 and o["shelf"]["position"][0] == pytest.approx(0.06 + T / 2 + 0.175 + 0.02)
    assert o["desk"]["position"][1] == pytest.approx(7.06 - T / 2 - 0.35 - 0.02)
    assert o["chair"]["position"] == pytest.approx((2.06, 5.56))
    assert {z["id"] for z in st["room"]["layout"]["zones"]} == {"ldk", "bed"}
    svg = (tmp_path / "flat" / "layout.svg").read_text()
    assert "寝室" in svg and " A" in svg  # zone name, door swing arc


def test_checks_wall_door_and_outside(tmp_path):
    service.create_scene({"name": "flat", "room": l_room(), "objects": [
        {"id": "a", "query": "desk", "position": [1.0, 3.5]},   # in the door's way (LDK side)
        {"id": "b", "query": "desk", "position": [6.0, 6.0]},    # in the missing corner of the L
        {"id": "c", "query": "desk", "position": [4.06, 2.0], "yaw_deg": 90},  # fine
        {"id": "d", "query": "desk", "position": [3.0, 4.06]},   # straddles the partition
    ]}, tmp_path / "flat")
    kinds = {(i["kind"], i["objects"][0]) for i in service.status(tmp_path / "flat")["issues"]}
    assert ("blocks_door", "a") in kinds
    assert ("out_of_room", "b") in kinds
    assert ("hits_wall", "d") in kinds
    assert not any(obj == "c" for _, obj in kinds)


def test_rect_room_door(tmp_path):
    room = {"width": 4.0, "depth": 3.0, "height": 2.4,
            "openings": [{"kind": "door", "wall": "south", "offset": 1.0, "width": 0.9}]}
    st = service.create_scene({"name": "r", "room": room, "objects": [
        {"id": "sofa", "query": "sofa", "layout": {"against_wall": "south"}},
        {"id": "x", "query": "desk", "layout": {"against_wall": "garage"}},
    ]}, tmp_path / "r")
    o = objs(st)
    assert abs(o["sofa"]["position"][0] - 1.0) >= 0.45 + 0.9 - 1e-6
    assert {i["kind"] for i in st["issues"]} == {"bad_layout", "no_position"}  # unknown wall name
    bad = Room.model_validate({**room, "openings": [{"wall": "w9", "offset": 1}]})
    assert RoomGeometry(bad).unknown_openings


def test_compose_plan_walls(tmp_path):
    pytest.importorskip("pxr")
    from pxr import Usd, UsdGeom

    service.create_scene({"name": "flat", "room": l_room(ceiling=True), "objects": []}, tmp_path / "flat")
    service.compose_scene(tmp_path / "flat")
    stage = Usd.Stage.Open(str(tmp_path / "flat" / "scene.usda"))
    room = stage.GetPrimAtPath("/World/Room")
    names = {c.GetName() for c in room.GetChildren()}
    assert {"Floor", "Ceiling", "Wall_s", "Wall_mid", "Wall_e2"} <= names
    parts = stage.GetPrimAtPath("/World/Room/Wall_s").GetChildren()
    assert len(parts) == 4  # two solid parts + below/above the window
    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ["default"])
    box = cache.ComputeWorldBound(stage.GetPrimAtPath("/World/Room/Wall_e2")).ComputeAlignedRange()
    assert box.GetMin()[0] == pytest.approx(4.06 - T / 2, abs=1e-5) and box.GetMax()[1] == pytest.approx(7.06 + T / 2, abs=1e-5)  # L corner: reaches the outer face


def test_rect_walls_unchanged(tmp_path):
    pytest.importorskip("pxr")
    from pxr import Usd, UsdGeom

    service.create_scene({"name": "r", "room": {"width": 4.0, "depth": 3.0, "height": 2.4}, "objects": []}, tmp_path / "r")
    service.compose_scene(tmp_path / "r")
    stage = Usd.Stage.Open(str(tmp_path / "r" / "scene.usda"))
    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ["default"])
    want = {"Wall_S": ((-T, -T), (4 + T, 0)), "Wall_N": ((-T, 3), (4 + T, 3 + T)),
            "Wall_W": ((-T, 0), (0, 3)), "Wall_E": ((4, 0), (4 + T, 3)), "Floor": ((-T, -T), (4 + T, 3 + T))}
    for name, (lo, hi) in want.items():
        r = cache.ComputeWorldBound(stage.GetPrimAtPath(f"/World/Room/{name}")).ComputeAlignedRange()
        assert tuple(r.GetMin())[:2] == pytest.approx(lo, abs=1e-6), name
        assert tuple(r.GetMax())[:2] == pytest.approx(hi, abs=1e-6), name


def test_traced_pixels_with_underlay(tmp_path):
    png = tmp_path / "scan.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0\0\0\rIHDR" + (1000).to_bytes(4, "big") + (600).to_bytes(4, "big") + b"\0" * 8)
    plan = {"units": "px", "underlay": {"image": "scan.png", "m_per_px": 0.01},
            "walls": [{"start": [100, 500], "end": [500, 500], "thickness": 12},
                      {"start": [500, 500], "end": [500, 200], "thickness": 12},
                      {"start": [500, 200], "end": [100, 200], "thickness": 12},
                      {"start": [100, 200], "end": [100, 500], "thickness": 12}],
            "openings": [{"kind": "window", "wall": "w2", "offset": 200, "width": 120, "sill": 0.9}]}
    (tmp_path / "trace.json").write_text(json.dumps(plan))
    r = service.import_floor_plan(tmp_path / "trace.json", tmp_path / "out")
    assert r["size"] == pytest.approx([4.12, 3.12])  # 400 x 300 px at 1 cm/px + wall thickness
    w0 = r["room"]["plan"]["walls"][0]
    assert w0["start"] == [0.06, 0.06] and w0["thickness"] == pytest.approx(0.12)  # y flipped: bottom wall is south
    (win,) = r["room"]["openings"]
    assert (win["offset"], win["width"], win["sill"]) == pytest.approx((2.0, 1.2, 0.9))  # sill stays meters
    again = Room.model_validate({"plan_file": r["files"]["plan"]})  # round trip
    assert again.plan.walls[0].start == pytest.approx((0.06, 0.06)) and again.openings[0].width == pytest.approx(1.2)
    assert again.plan.underlay.image == str(png.resolve())
    svg = (tmp_path / "out" / "plan.svg").read_text()
    assert "<image" in svg and 'width="1200.0"' in svg  # 1000 px * 0.01 m * 120 px/m


def _dxf_flat(path, *, units=4, broken_at_door=True):
    """6 x 4 m, double-line walls 150 mm, a partition at x=3.5 m with a door, a window block, two rooms."""
    ezdxf = pytest.importorskip("ezdxf")
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = units
    msp = doc.modelspace()
    k = 1000 if units == 4 else 1
    t = 150 / 1000 * k

    def double(a, b, layer="A-WALL"):  # wall as two lines t apart, centered on a-b
        (x0, y0), (x1, y1) = a, b
        L = math.hypot(x1 - x0, y1 - y0)
        nx, ny = -(y1 - y0) / L * t / 2, (x1 - x0) / L * t / 2
        msp.add_line((x0 + nx, y0 + ny), (x1 + nx, y1 + ny), dxfattribs={"layer": layer})
        msp.add_line((x0 - nx, y0 - ny), (x1 - nx, y1 - ny), dxfattribs={"layer": layer})

    W, D, X = 6 * k, 4 * k, 3.5 * k
    h = t / 2
    # outer walls as closed rectangles (outer and inner outline)
    msp.add_lwpolyline([(-h, -h), (W + h, -h), (W + h, D + h), (-h, D + h)], close=True, dxfattribs={"layer": "A-WALL"})
    msp.add_lwpolyline([(h, h), (W - h, h), (W - h, D - h), (h, D - h)], close=True, dxfattribs={"layer": "A-WALL"})
    # partition x = X from y = h to D - h, broken at the door (y 1.6 .. 2.4 m)
    if broken_at_door:
        double((X, h), (X, 1.6 * k))
        double((X, 2.4 * k), (X, D - h))
    else:
        double((X, h), (X, D - h))
    door = doc.blocks.new("DOOR_800")
    door.add_line((0, 0), (0, 0.8 * k))
    door.add_arc((0, 0), 0.8 * k, 0, 90)
    msp.add_blockref("DOOR_800", (X, 1.6 * k), dxfattribs={"layer": "A-DOOR"})
    win = doc.blocks.new("窓1600")
    win.add_lwpolyline([(0, -h), (1.6 * k, -h), (1.6 * k, h), (0, h)], close=True)
    msp.add_blockref("窓1600", (1.0 * k, 0), dxfattribs={"layer": "A-GLAZ"})
    msp.add_lwpolyline([(h, h), (X - h, h), (X - h, D - h), (h, D - h)], close=True, dxfattribs={"layer": "ROOM"})
    msp.add_lwpolyline([(X + h, h), (W - h, h), (W - h, D - h), (X + h, D - h)], close=True, dxfattribs={"layer": "ROOM"})
    msp.add_text("洋室", dxfattribs={"layer": "ROOM", "insert": (1 * k, 2 * k)})
    msp.add_text("DK", dxfattribs={"layer": "ROOM", "insert": (4.5 * k, 2 * k)})
    msp.add_line((0, -2 * k), (W, -2 * k), dxfattribs={"layer": "DIM"})  # ignored layer
    doc.saveas(path)


def _wall_at(walls, x=None, y=None):
    for w in walls:
        (x0, y0), (x1, y1) = w["start"], w["end"]
        if x is not None and abs(x0 - x) < 0.02 and abs(x1 - x) < 0.02:
            return w
        if y is not None and abs(y0 - y) < 0.02 and abs(y1 - y) < 0.02:
            return w
    raise AssertionError(f"no wall at x={x} y={y}: {walls}")


@pytest.mark.parametrize("broken", [True, False])
def test_dxf_import(tmp_path, broken):
    _dxf_flat(tmp_path / "flat.dxf", broken_at_door=broken)
    r = service.import_floor_plan(tmp_path / "flat.dxf", tmp_path / "out")
    assert r["size"] == pytest.approx([6.15, 4.15], abs=1e-3)
    walls = r["room"]["plan"]["walls"]
    assert len(walls) == 5  # 4 outer + partition (joined through the door gap)
    assert all(w["thickness"] == pytest.approx(0.15) for w in walls)
    part = _wall_at(walls, x=3.575)
    assert sorted(p[1] for p in (part["start"], part["end"])) == pytest.approx([0.075 + 0.075, 4.075 - 0.075], abs=1e-3)
    south = _wall_at(walls, y=0.075)
    assert sorted(p[0] for p in (south["start"], south["end"])) == pytest.approx([0.075, 6.075], abs=1e-3)
    ops = {o["kind"]: o for o in r["room"]["openings"]}
    assert ops["door"]["wall"] == part["id"] and ops["door"]["width"] == pytest.approx(0.8)
    y0, y1 = part["start"][1], part["end"][1]
    d_y = y0 + ops["door"]["offset"] if y0 < y1 else y0 - ops["door"]["offset"]
    assert d_y == pytest.approx(2.0 + 0.075, abs=0.01)
    assert ops["window"]["wall"] == south["id"] and ops["window"]["width"] == pytest.approx(1.6)
    assert {z["name"] for z in r["zones"]} == {"洋室", "DK"}
    assert r["warnings"] == []
    g = RoomGeometry(Room.model_validate(r["room"]))
    assert len(g.faces_for("east", "z0")) == 1  # 洋室's east side is the partition


def test_dxf_units_guess(tmp_path):
    _dxf_flat(tmp_path / "m.dxf", units=0)  # unitless, drawn in mm -> guessed from the size
    r = service.import_floor_plan(tmp_path / "m.dxf")
    assert r["size"] == pytest.approx([6.15, 4.15], abs=1e-3)


def _ifc_flat(path, furniture=False):
    """5 x 4 m room, walls 0.2 m, a door in the south wall, a window in the north wall, one space. mm units.
    furniture: also 5 rotated pieces (see test_ifc_furniture)."""
    np = pytest.importorskip("numpy")
    ifcopenshell = pytest.importorskip("ifcopenshell")
    import ifcopenshell.api as api

    m = ifcopenshell.file(schema="IFC4")
    proj = api.run("root.create_entity", m, ifc_class="IfcProject", name="P")
    api.run("unit.assign_unit", m)  # millimeters
    ctx = api.run("context.add_context", m, context_type="Model")
    body = api.run("context.add_context", m, context_type="Model", context_identifier="Body",
                   target_view="MODEL_VIEW", parent=ctx)
    site = api.run("root.create_entity", m, ifc_class="IfcSite")
    bld = api.run("root.create_entity", m, ifc_class="IfcBuilding")
    st = api.run("root.create_entity", m, ifc_class="IfcBuildingStorey", name="1F")
    api.run("aggregate.assign_object", m, relating_object=proj, products=[site])
    api.run("aggregate.assign_object", m, relating_object=site, products=[bld])
    api.run("aggregate.assign_object", m, relating_object=bld, products=[st])

    def place(e, x, y, z=0.0, ang=0.0, contain=True):
        c, s = math.cos(math.radians(ang)), math.sin(math.radians(ang))
        M = np.eye(4)
        M[:3, :3] = [[c, -s, 0], [s, c, 0], [0, 0, 1]]
        M[:3, 3] = [x, y, z]
        api.run("geometry.edit_object_placement", m, product=e, matrix=M)
        if contain:
            api.run("spatial.assign_container", m, relating_structure=st, products=[e])
        else:  # spaces are aggregated, not contained
            api.run("aggregate.assign_object", m, relating_object=st, products=[e])

    # wall local frame: x along the wall 0..L, y = thickness 0..t -> start the outline at the outer face
    for (x, y, ang, L) in ((0, 0, 0, 5.2), (5.2, 0, 90, 4.2), (5.2, 4.2, 180, 5.2), (0, 4.2, 270, 4.2)):
        w = api.run("root.create_entity", m, ifc_class="IfcWall", name="W")
        rep = api.run("geometry.add_wall_representation", m, context=body, length=L, height=2.5, thickness=0.2)
        api.run("geometry.assign_representation", m, product=w, representation=rep)
        place(w, x, y, 0, ang)
    door = api.run("root.create_entity", m, ifc_class="IfcDoor", name="D1")
    rep = api.run("geometry.add_wall_representation", m, context=body, length=0.9, height=2.0, thickness=0.05)
    api.run("geometry.assign_representation", m, product=door, representation=rep)
    place(door, 0.6, 0.05)  # simple box bodies: only the extents matter
    win = api.run("root.create_entity", m, ifc_class="IfcWindow", name="W1")
    rep = api.run("geometry.add_wall_representation", m, context=body, length=1.5, height=1.2, thickness=0.05)
    api.run("geometry.assign_representation", m, product=win, representation=rep)
    place(win, 4.25, 4.15, 0.9, 180)  # north wall, from outside (box spans x 2.75..4.25)
    sp = api.run("root.create_entity", m, ifc_class="IfcSpace", name="101")
    sp.LongName = "居間"
    rep = api.run("geometry.add_slab_representation", m, context=body, depth=2.5,
                  polyline=[(0.2, 0.2), (5.0, 0.2), (5.0, 4.0), (0.2, 4.0)])
    api.run("geometry.assign_representation", m, product=sp, representation=rep)
    place(sp, 0, 0, contain=False)
    if furniture:
        # box bodies span local x 0..L, y 0..t (t = depth), z 0..h
        items = (("IfcFurniture", "デスク", "DESK", 1.2, 0.6, 0.72, 1.0, 1.4, 0.0, 0),
                 ("IfcFurniture", "Office Chair", "CHAIR", 0.5, 0.5, 0.9, 2.0, 2.0, 0.0, 90),
                 ("IfcSystemFurnitureElement", "P-1", "PANEL", 1.6, 0.05, 1.5, 3.0, 3.5, 0.0, 180),
                 ("IfcFurniture", "本棚", None, 0.8, 0.3, 1.8, 4.6, 3.0, 0.3, 270),
                 ("IfcFurniture", None, None, 0.4, 0.4, 0.4, 0.5, 3.0, 0.0, 0))
        for cls, name, pre, L, t, h, x, y, z, ang in items:
            e = api.run("root.create_entity", m, ifc_class=cls, name=name, predefined_type=pre)
            rep = api.run("geometry.add_wall_representation", m, context=body, length=L, height=h, thickness=t)
            api.run("geometry.assign_representation", m, product=e, representation=rep)
            place(e, x, y, z, ang, contain=name is not None)
            if name is None:  # placed in the room (IfcSpace) instead of the storey
                api.run("spatial.assign_container", m, relating_structure=sp, products=[e])
    m.write(str(path))


def test_ifc_import(tmp_path):
    _ifc_flat(tmp_path / "flat.ifc")
    r = service.import_floor_plan(tmp_path / "flat.ifc", tmp_path / "out")
    assert r["size"] == pytest.approx([5.2, 4.2], abs=1e-3)
    walls = r["room"]["plan"]["walls"]
    assert len(walls) == 4 and all(w["thickness"] == pytest.approx(0.2) for w in walls)
    assert r["room"]["height"] == pytest.approx(2.5)
    ops = {o["kind"]: o for o in r["room"]["openings"]}
    south = _wall_at(walls, y=0.1)
    north = _wall_at(walls, y=4.1)
    assert ops["door"]["wall"] == south["id"] and ops["door"]["width"] == pytest.approx(0.9, abs=0.01)
    assert ops["window"]["wall"] == north["id"] and ops["window"]["sill"] == pytest.approx(0.9, abs=0.01)
    assert ops["window"]["height"] == pytest.approx(1.2, abs=0.01)
    (z,) = r["zones"]
    assert z["name"] == "居間" and z["area_m2"] == pytest.approx(4.8 * 3.8, rel=0.01)


def test_ifc_furniture(tmp_path):
    """Furniture: footprint center, size along its own axes, facing (front = local -Y), z above the floor,
    zone, GlobalId, guessed query; drafts with ±20 % size bounds; drawn in plan.svg, kept in plan.json."""
    _ifc_flat(tmp_path / "f.ifc", furniture=True)
    r = service.import_floor_plan(tmp_path / "f.ifc", tmp_path / "out")
    fs = {f["name"]: f for f in r["furniture"]}
    assert len(r["furniture"]) == 5
    desk, chair, panel, shelf, other = fs["デスク"], fs["Office Chair"], fs["P-1"], fs["本棚"], fs[None]
    assert desk["position"] == pytest.approx([1.6, 1.7], abs=1e-3) and desk["facing_deg"] == pytest.approx(-90)
    assert desk["size"] == pytest.approx([1.2, 0.6, 0.72], abs=1e-3)
    assert (desk["query"], desk["predefined_type"], desk["ifc_class"]) == ("desk", "DESK", "IfcFurniture")
    assert chair["position"] == pytest.approx([1.75, 2.25], abs=1e-3) and chair["facing_deg"] == pytest.approx(0)
    assert chair["query"] == "office chair" and chair["query_from"] == "name"
    assert panel["position"] == pytest.approx([2.2, 3.475], abs=1e-3) and panel["facing_deg"] == pytest.approx(90)
    assert panel["query"] == "office partition panel" and panel["query_from"] == "predefined_type"
    assert panel["ifc_class"] == "IfcSystemFurnitureElement"
    assert shelf["position"] == pytest.approx([4.75, 2.6], abs=1e-3) and shelf["facing_deg"] == pytest.approx(180)
    assert shelf["size"] == pytest.approx([0.8, 0.3, 1.8], abs=1e-3) and shelf["z"] == pytest.approx(0.3)
    assert shelf["query"] == "bookshelf"
    assert other["query"] == "furniture" and other["query_from"] == "class"
    assert other["position"] == pytest.approx([0.7, 3.2], abs=1e-3)
    zone = r["zones"][0]["id"]
    assert all(f["zone"] == zone for f in r["furniture"])
    assert len({f["id"] for f in r["furniture"]}) == 5 and all(len(f["ifc_guid"]) == 22 for f in r["furniture"])
    # drafts: fixed spot / facing, footprint (long, short) ±20 %, height -30 % .. +50 %
    (d,) = [o for o in r["objects"] if o["ifc_guid"] == desk["ifc_guid"]]
    assert d["position"] == desk["position"] and d["facing_deg"] == -90 and d["query"] == "desk"
    assert d["min_size"] == pytest.approx([0.96, 0.48, 0.504]) and d["max_size"] == pytest.approx([1.44, 0.72, 1.08])
    (s,) = [o for o in r["objects"] if o["ifc_guid"] == shelf["ifc_guid"]]
    assert s["z"] == pytest.approx(0.3) and "z" not in d
    from agentic_sim_world_gen.scene.spec import ObjectSpec

    for o in r["objects"]:
        ObjectSpec.model_validate(o)
    svg = (tmp_path / "out" / "plan.svg").read_text()
    assert "office partition panel" in svg and "本棚" in svg
    plan = json.loads((tmp_path / "out" / "plan.json").read_text())
    assert len(plan["furniture"]) == 5
    # the plan file still loads as a room, furniture ignored
    assert service.import_floor_plan(tmp_path / "out" / "plan.json")["size"] == pytest.approx([5.2, 4.2], abs=1e-3)
    assert "furniture" not in service.import_floor_plan(tmp_path / "f.ifc", include_furniture=False)


def test_ifc_units_with_project_library(tmp_path):
    """A second IfcContext (IfcProjectLibrary) made the geometry kernel skip the mm -> m conversion."""
    _ifc_flat(tmp_path / "a.ifc")
    import ifcopenshell
    import ifcopenshell.api as api

    m = ifcopenshell.open(str(tmp_path / "a.ifc"))
    api.run("root.create_entity", m, ifc_class="IfcProjectLibrary", name="L")
    m.write(str(tmp_path / "b.ifc"))
    r = service.import_floor_plan(tmp_path / "b.ifc")
    assert r["size"] == pytest.approx([5.2, 4.2], abs=1e-3)
    assert {o["kind"]: o["width"] for o in r["room"]["openings"]}["door"] == pytest.approx(0.9, abs=0.01)


def test_dxf_single_line_rooms(tmp_path):
    """Rooms drawn as separate closed rectangles on a wall layer, names on A-ROOM: shared edges are
    merged into one wall and each rectangle with one name becomes a zone."""
    ezdxf = pytest.importorskip("ezdxf")
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()
    for x0, name in ((0, "LDK"), (3000, "洋室")):
        msp.add_lwpolyline([(x0, 0), (x0 + 3000, 0), (x0 + 3000, 3000), (x0, 3000)], close=True,
                           dxfattribs={"layer": "A-WALL-INT"})
        msp.add_text(name, dxfattribs={"layer": "A-ROOM", "insert": (x0 + 1500, 1500)})
    msp.add_text("NOTE", dxfattribs={"layer": "A-NOTE", "insert": (0, 4000)})
    doc.saveas(tmp_path / "rooms.dxf")
    r = service.import_floor_plan(tmp_path / "rooms.dxf")
    walls = r["room"]["plan"]["walls"]
    assert len(walls) == 5  # bottom, top (each merged across both rooms), left, middle (shared), right
    assert {z["name"] for z in r["zones"]} == {"LDK", "洋室"}
    assert r["warnings"] == []


def test_dxf_wall_layer_patterns(tmp_path):
    """Walls on layers without WALL in the name (old US layer names): the warning lists the busy layers,
    and wall_layers patterns pick them."""
    ezdxf = pytest.importorskip("ezdxf")
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (8000, 0), (8000, 6000), (0, 6000)], close=True, dxfattribs={"layer": "E-B-SHEL"})
    msp.add_line((4000, 0), (4000, 6000), dxfattribs={"layer": "E-B-CORE"})
    msp.add_line((100, 100), (600, 100), dxfattribs={"layer": "IWALLE"})  # a stray line on a "wall" layer
    doc.saveas(tmp_path / "us.dxf")
    r = service.import_floor_plan(tmp_path / "us.dxf")
    assert any("E-B-SHEL" in w and "wall_layers" in w for w in r["warnings"])
    r = service.import_floor_plan(tmp_path / "us.dxf", wall_layers=["e-b-*"])
    assert r["size"] == pytest.approx([8.12, 6.12], abs=1e-3) and len(r["room"]["plan"]["walls"]) == 5
    assert r["warnings"] == []


def test_dxf_wrong_insunits(tmp_path):
    """$INSUNITS says meters but the drawing is in mm: warn, and units='mm' fixes it."""
    ezdxf = pytest.importorskip("ezdxf")
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = 6
    doc.modelspace().add_lwpolyline([(0, 0), (4000, 0), (4000, 3000), (0, 3000)], close=True, dxfattribs={"layer": "WALL"})
    doc.saveas(tmp_path / "mm.dxf")
    r = service.import_floor_plan(tmp_path / "mm.dxf")
    assert any("units look wrong" in w for w in r["warnings"])
    r = service.import_floor_plan(tmp_path / "mm.dxf", units="mm")
    assert r["size"] == pytest.approx([4.12, 3.12], abs=1e-3) and r["warnings"] == []


def test_plan_corners_closed():
    """Outer corners of the 1LDK plan are filled; T joins and free ends are not extended."""
    from agentic_sim_world_gen.layout.room import RoomGeometry
    from agentic_sim_world_gen.scene.spec import Room

    plan = Path(__file__).parents[1] / "examples" / "plans" / "1ldk.json"
    room = Room.model_validate({"plan_file": str(plan)})
    g = RoomGeometry(room)
    by = {w.id: w for w in g.walls}

    def covered(x, y):
        for w in g.walls:
            for u0, u1, *_ in w.pieces():
                dx, dy = x - w.a[0], y - w.a[1]
                u = dx * w.dir[0] + dy * w.dir[1]
                n = dx * w.normal[0] + dy * w.normal[1]
                if u0 - 1e-9 <= u <= u1 + 1e-9 and abs(n) <= w.thickness / 2 + 1e-9:
                    return True
        return False

    x0, y0, x1, y1 = g.bounds
    for x, y in [(x0 + 0.01, y0 + 0.01), (x1 - 0.01, y0 + 0.01), (x1 - 0.01, y1 - 0.01), (x0 + 0.01, y1 - 0.01)]:
        assert covered(x, y), (x, y)
    assert by["mid"].ext_a == 0 and by["mid"].ext_b == 0  # T joins on s and n
    assert by["bath"].ext_a == 0 and by["bath"].ext_b == 0
    assert by["s"].ext_a == pytest.approx(by["w"].thickness / 2)
