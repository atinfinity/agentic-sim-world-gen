"""Shelf boards from geometry, shelf stocking (fill) and ceiling hanging (no network)."""

import math

import pytest

from agentic_sim_world_gen import service
from agentic_sim_world_gen.assets.record import AssetRecord
from agentic_sim_world_gen.assets.shelves import surfaces_from_boxes
from agentic_sim_world_gen.layout.array import instances

# gondola-like fixture, front -Y: base board, two shelves, open top; back panel; side panels
BOXES = [
    ("Base", (-0.45, -0.2, 0.08), (0.45, 0.2, 0.1)),
    ("Shelf1", (-0.45, -0.2, 0.48), (0.45, 0.2, 0.5)),
    ("Shelf2", (-0.45, -0.2, 0.88), (0.45, 0.2, 0.9)),
    ("Back", (-0.45, 0.2, 0.0), (0.45, 0.22, 1.4)),
    ("SideL", (-0.47, -0.2, 0.0), (-0.45, 0.22, 1.4)),
    ("Rail", (-0.45, -0.21, 0.5), (0.45, -0.2, 0.53)),  # price rail: too narrow to be a board
]
SHELVES = surfaces_from_boxes(BOXES)


def rec(name, w, d, h, front_deg=None, shelves=None):
    return AssetRecord(
        id=name, source="fake", uri=f"https://example.com/{name}.usd", name=name,
        bbox_min=(-w / 2, -d / 2, 0.0), bbox_max=(w / 2, d / 2, h), up_axis="Z", meters_per_unit=1.0,
        semantic_class=name, license="CC0-1.0", front_deg=front_deg, shelves=shelves,
    )


CATALOG = {
    "gondola": [rec("gondola", 0.94, 0.44, 1.4, front_deg=270.0, shelves=SHELVES)],
    "cup noodle": [rec("cup", 0.1, 0.1, 0.12)],
    "tall bottle": [rec("bottle", 0.08, 0.08, 0.45)],
    "sign": [rec("sign", 1.2, 0.1, 0.4)],
    "blister": [rec("blister", 0.1, 0.066, 0.18, front_deg=270.0)],  # hanging pack, front -Y
}


@pytest.fixture(autouse=True)
def fake():
    service.set_provider(type("P", (), {"name": "f", "search": lambda self, q, limit=10, size=None: CATALOG.get(q, [])})())
    yield
    service.set_provider(None)


def test_surfaces_from_boxes():
    assert [s["part"] for s in SHELVES] == ["Base", "Shelf1", "Shelf2"]
    assert [s["clear"] for s in SHELVES] == [pytest.approx(0.38), pytest.approx(0.38), None]


def test_buried_board_is_not_a_shelf():
    # produce table: the plinth sits inside a closed skirt; only the top board (and its rim) is a shelf
    boxes = [("Plinth", (-0.58, -0.43, 0.0), (0.58, 0.43, 0.04)),
             ("Skirt", (-0.6, -0.45, 0.04), (0.6, 0.45, 0.68)),
             ("TopBoard", (-0.6, -0.45, 0.68), (0.6, 0.45, 0.72)),
             ("RimFront", (-0.6, -0.45, 0.72), (0.6, -0.43, 0.79))]
    assert [s["part"] for s in surfaces_from_boxes(boxes)] == ["TopBoard"]


def make(tmp_path, objects, **room):
    return service.create_scene({"name": "s", "room": {"width": 6, "depth": 5, "height": 2.7, **room},
                                 "objects": objects}, tmp_path / "s")


def test_fill_rows_and_columns(tmp_path):
    st = make(tmp_path, [
        {"id": "g", "query": "gondola", "layout": {"against_wall": "north"}},
        {"id": "cups", "query": "cup noodle", "fill": {"on": "g", "levels": [1], "rows": 2}},
    ])
    assert st["issues"] == []
    s = service.load(tmp_path / "s")
    g, o = s.obj("g"), s.obj("cups")
    items = instances(o, s.placements[1].asset)
    # 0.9 m board: 8 columns of 0.1 m + 0.01 gaps, 2 rows
    assert len(items) == 16 and next(x for x in st["objects"] if x["id"] == "cups")["fill"]["count"] == 16
    assert all(i.z == pytest.approx(g.z + 0.5 + 0.002) for i in items)
    # the gondola faces south (back on the north wall): rows start at its front (south) edge
    ys = sorted({i.position[1] for i in items})
    assert len(ys) == 2 and ys[1] - ys[0] == pytest.approx(0.11)
    assert ys[0] < g.position[1]  # rows start at the front (south) edge
    assert ys[0] == pytest.approx(g.position[1] - 0.2 + 0.01 + 0.05)  # board front y0 = -0.2, margin, half a cup
    assert all(abs(i.position[0] - g.position[0]) < 0.45 for i in items)


def test_fill_skips_low_levels_and_reports(tmp_path):
    st = make(tmp_path, [
        {"id": "g", "query": "gondola", "layout": {"center": True}},
        {"id": "b", "query": "tall bottle", "fill": {"on": "g", "rows": 1}},
    ])
    s = service.load(tmp_path / "s")
    lv = {n.split("_")[1] for n, *_ in s.obj("b").fill.slots}
    assert lv == {"l2"}  # 0.45 m bottles only fit on the open top board
    assert st["issues"] == []
    st = service.set_fill(tmp_path / "s", "b", {"on": "g", "levels": [0]})
    assert [i["kind"] for i in st["issues"]] == ["shelf_fit"]


def test_fill_span_and_array_units(tmp_path):
    st = make(tmp_path, [
        {"id": "g", "query": "gondola", "layout": {"center": True},
         "array": {"count": 2, "rows": 2, "back_to_back": True, "back_gap": 0.05}},
        {"id": "a", "query": "cup noodle", "fill": {"on": "g", "levels": [0], "span": [0, 0.5], "rows": 1}},
        {"id": "b", "query": "cup noodle", "fill": {"on": "g", "levels": [0], "span": [0.5, 1], "rows": 1,
                                                    "units": ["r1c0"]}},
    ])
    assert st["issues"] == []
    s = service.load(tmp_path / "s")
    a = s.obj("a").fill.slots
    assert len(a) == 4 * 4  # 4 units x 4 columns in half a board
    assert {n.split("_")[0] for n, *_ in s.obj("b").fill.slots} == {"r1c0"}
    # products face like their unit: flipped rows hold products turned 180 deg
    g = s.obj("g")
    yaws = {round(math.sin(math.radians(y)), 3) for *_, y, _z in a}
    assert len(yaws) == 2 and g.array is not None


def test_fill_compose(tmp_path):
    pytest.importorskip("pxr")
    from pxr import Usd

    make(tmp_path, [
        {"id": "g", "query": "gondola", "layout": {"center": True}},
        {"id": "cups", "query": "cup noodle", "fill": {"on": "g", "levels": [0], "rows": 1}},
    ])
    out = service.compose_scene(tmp_path / "s")
    stage = Usd.Stage.Open(out["usd"])
    kids = stage.GetPrimAtPath("/World/Objects/cups").GetChildren()
    assert len(kids) == 8 and kids[0].GetName().startswith("cups_u_l0_")
    assert stage.GetPrimAtPath("/World/Objects/cups").GetCustomDataByKey("agentic_sim_world_gen:fill")["on"] == "g"

    # view_fronts leaves the products out unless asked
    sel = service.front_targets(out["usd"])
    assert sel["ids"] == ["g"] and sel["skipped_fill"] == 8
    assert len(service.front_targets(out["usd"], fill=True)["ids"]) == 9
    assert len(service.front_targets(out["usd"], ids=["cups"])["ids"]) == 8
    one = kids[0].GetName()
    assert service.front_targets(out["usd"], ids=["g", f"cups/{one}"])["ids"] == ["g", f"cups/{one}"]
    assert "unknown object ids ['nope']" in service.front_targets(out["usd"], ids=["nope"])["error"]

    # Only what is shot is loaded: the others are deactivated in a layer over the scene
    from pathlib import Path

    usd = Path(out["usd"])
    layer = usd.with_name("front_views_stage.usda")
    assert service.front_stage(usd, ["g", f"cups/{one}"], layer) == 7
    st = Usd.Stage.Open(str(layer))
    cups = st.GetPrimAtPath("/World/Objects/cups")
    assert st.GetPrimAtPath("/World/Objects/g").IsActive() and cups.IsActive()
    assert [k.GetName() for k in cups.GetChildren() if k.IsActive()] == [one]
    layer = usd.with_name("g_only.usda")
    assert service.front_stage(usd, ["g"], layer) == 1
    assert not Usd.Stage.Open(str(layer)).GetPrimAtPath("/World/Objects/cups").IsActive()


def test_find_shelves_from_usd(tmp_path):
    pytest.importorskip("pxr")
    from pxr import Usd, UsdGeom

    from agentic_sim_world_gen.assets.shelves import find_shelves

    path = tmp_path / "shelf.usda"
    stage = Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
    UsdGeom.SetStageMetersPerUnit(stage, 0.01)
    root = UsdGeom.Xform.Define(stage, "/Shelf")
    stage.SetDefaultPrim(root.GetPrim())
    for name, (a, b) in {"Board0": ((-45, 10, -20), (45, 12, 20)), "Board1": ((-45, 50, -20), (45, 52, 20)),
                         "Side": ((-47, 0, -20), (-45, 100, 20))}.items():
        c = UsdGeom.Cube.Define(stage, f"/Shelf/{name}")
        c.CreateSizeAttr(1.0)
        xf = UsdGeom.Xformable(c)
        xf.AddTranslateOp().Set(tuple((p + q) / 2 for p, q in zip(a, b)))
        xf.AddScaleOp().Set(tuple(q - p for p, q in zip(a, b)))
    stage.GetRootLayer().Save()
    out = find_shelves(str(path), tmp_path / "cache")
    assert [round(s["z"], 3) for s in out] == [0.12, 0.52]  # Y-up cm -> Z-up m, from the bottom
    assert out[0]["clear"] == pytest.approx(0.38) and out[1]["clear"] is None
    assert out[0]["x0"] == pytest.approx(-0.44) and out[0]["y1"] - out[0]["y0"] == pytest.approx(0.4)


def test_find_shelves_rescans_a_rebuilt_asset(tmp_path):
    """A rebuilt local asset is scanned again, also when only its payload layer changed(a dropped
    straw layer in the wine crate stayed a fill level because the scan was cached by path)."""
    pytest.importorskip("pxr")
    import os

    from pxr import Usd, UsdGeom

    from agentic_sim_world_gen.assets.shelves import find_shelves

    asset = tmp_path / "crate"
    (asset / "payloads").mkdir(parents=True)
    meshes = asset / "payloads" / "meshes.usda"

    def build(boards):
        stage = Usd.Stage.CreateNew(str(meshes)) if not meshes.exists() else Usd.Stage.Open(str(meshes))
        stage.RemovePrim("/Crate")
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
        UsdGeom.SetStageMetersPerUnit(stage, 1.0)
        stage.SetDefaultPrim(UsdGeom.Xform.Define(stage, "/Crate").GetPrim())
        for name, (a, b) in {"Side": ((-0.5, -0.2, 0), (-0.48, 0.2, 1.0)), **boards}.items():
            c = UsdGeom.Cube.Define(stage, f"/Crate/{name}")
            c.CreateSizeAttr(1.0)
            xf = UsdGeom.Xformable(c)
            xf.AddTranslateOp().Set(tuple((p + q) / 2 for p, q in zip(a, b)))
            xf.AddScaleOp().Set(tuple(q - p for p, q in zip(a, b)))
        stage.GetRootLayer().Save()

    build({"Board0": ((-0.45, -0.2, 0.1), (0.45, 0.2, 0.12)), "Board1": ((-0.45, -0.2, 0.5), (0.45, 0.2, 0.52))})
    main = asset / "crate.usda"
    stage = Usd.Stage.CreateNew(str(main))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    root = UsdGeom.Xform.Define(stage, "/Crate").GetPrim()
    root.GetPayloads().AddPayload("./payloads/meshes.usda")
    stage.SetDefaultPrim(root)
    stage.GetRootLayer().Save()
    cache = tmp_path / "cache"
    assert len(find_shelves(str(main), cache)) == 2

    build({"Board0": ((-0.45, -0.2, 0.1), (0.45, 0.2, 0.12))})
    st = meshes.stat()
    os.utime(meshes, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))  # same second on a coarse clock
    assert len(find_shelves(str(main), cache)) == 1


def test_ceiling_hanging(tmp_path):
    st = make(tmp_path, [
        {"id": "g", "query": "gondola", "layout": {"center": True}},
        {"id": "sign", "query": "sign", "layout": {"center": True, "ceiling": True}},
    ])
    assert st["issues"] == []  # above the gondola, not overlapping it
    s = service.load(tmp_path / "s")
    assert s.obj("sign").z == pytest.approx(2.7 - 0.4)
    with pytest.raises(Exception):
        make(tmp_path, [{"id": "sign", "query": "sign", "layout": {"center": True, "ceiling": True},
                         "fill": {"on": "x"}}])


def test_auto_door(tmp_path):
    pytest.importorskip("pxr")
    from pxr import Usd, UsdPhysics

    st = make(tmp_path, [], openings=[{"kind": "door", "wall": "south", "offset": 2.0, "width": 1.2,
                                       "style": "auto_sliding", "sensor": 1.2}])
    assert st["issues"] == []
    svg = (tmp_path / "s" / "layout.svg").read_text()
    assert "自動ドア" in svg
    out = service.compose_scene(tmp_path / "s")
    stage = Usd.Stage.Open(out["usd"])
    base = "/World/Room/Doors/door_0"
    info = stage.GetPrimAtPath(base).GetCustomDataByKey("agentic_sim_world_gen:door")
    assert info["travel"] == pytest.approx(0.62) and info["sensor"] == 1.2
    for side in "LR":
        p = stage.GetPrimAtPath(f"{base}/Panel_{side}")
        assert p.HasAPI(UsdPhysics.RigidBodyAPI)
        assert p.GetAttribute("xformOp:translate").Get()[1] < -0.05  # outside the south wall
        j = UsdPhysics.PrismaticJoint(stage.GetPrimAtPath(f"{base}/Joint_{side}"))
        assert j.GetUpperLimitAttr().Get() == pytest.approx(0.62)
        assert j.GetPrim().GetAttribute("drive:linear:physics:targetPosition").Get() == 0
    g = stage.GetPrimAtPath(f"{base}/Sensor")
    assert g.GetTypeName() == "OmniGraph"
    types = [c.GetAttribute("node:type").Get() for c in g.GetChildren()]
    assert types.count("omni.physx.graph.SceneQueryOverlapBoxAny") == 3
    assert types.count("omni.graph.nodes.WritePrimAttribute") == 2
    w = stage.GetPrimAtPath(f"{base}/Sensor/writeR")
    assert w.GetAttribute("inputs:primPath").Get() == f"{base}/Joint_R"
    with pytest.raises(Exception):
        make(tmp_path, [], openings=[{"kind": "window", "wall": "south", "offset": 2, "style": "auto_sliding"}])


def test_find_shelves_single_mesh(tmp_path):
    """A rack modeled as one instanced mesh: two wire-deck layers found from its upward faces."""
    pytest.importorskip("pxr")
    from pxr import Usd, UsdGeom

    from agentic_sim_world_gen.assets.shelves import find_shelves

    pts, counts, idx = [], [], []

    def quad(x0, y0, x1, y1, z, up=True):
        n = len(pts)
        pts.extend([(x0, y0, z), (x1, y0, z), (x1, y1, z), (x0, y1, z)])
        counts.append(4)
        idx.extend([n, n + 1, n + 2, n + 3] if up else [n + 3, n + 2, n + 1, n])

    for z in (1.2, 2.6):  # wire decks: 10 slats over 2.8 x 1.0 (~30 % covered)
        for i in range(10):
            quad(-1.4 + i * 0.28, -0.5, -1.4 + i * 0.28 + 0.08, 0.5, z)
    quad(-1.45, -0.55, -1.35, 0.55, 3.0)  # post tops: too little cover
    quad(1.35, -0.55, 1.45, 0.55, 3.0)
    quad(-1.45, -0.55, 1.45, 0.55, 0.0, up=False)  # bottom faces down

    path = tmp_path / "rack.usda"
    stage = Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    root = UsdGeom.Xform.Define(stage, "/Rack")
    stage.SetDefaultPrim(root.GetPrim())
    proto = UsdGeom.Xform.Define(stage, "/Proto")
    mesh = UsdGeom.Mesh.Define(stage, "/Proto/Mesh")
    mesh.CreatePointsAttr(pts)
    mesh.CreateFaceVertexCountsAttr(counts)
    mesh.CreateFaceVertexIndicesAttr(idx)
    inst = UsdGeom.Xform.Define(stage, "/Rack/Geom")
    inst.GetPrim().GetReferences().AddInternalReference(proto.GetPath())
    inst.GetPrim().SetInstanceable(True)
    stage.GetRootLayer().Save()
    out = find_shelves(str(path), tmp_path / "cache")
    assert [round(s["z"], 3) for s in out] == [1.2, 2.6]
    assert out[0]["clear"] == pytest.approx(1.4) and out[1]["clear"] is None
    assert out[0]["x0"] == pytest.approx(-1.4) and out[0]["y1"] == pytest.approx(0.5)


def test_board_top_skips_specks_but_not_bars():
    """The uprights' hole edges (specks) 4 cm above a rack's deck set the board height and 100 boxes
    dropped 4 cm; a cross bar above the deck still does (boxes started inside it and were thrown)."""
    from agentic_sim_world_gen.assets.shelves import board_layers

    pts, counts, idx = [], [], []

    def quad(x0, y0, x1, y1, z):
        n = len(pts)
        pts.extend([(x0, y0, z), (x1, y0, z), (x1, y1, z), (x0, y1, z)])
        counts.append(4)
        idx.extend([n, n + 1, n + 2, n + 3])

    for i in range(10):  # deck at 1.2
        quad(-1.4 + i * 0.28, -0.5, -1.4 + i * 0.28 + 0.08, 0.5, 1.2)
    for z in (1.22, 1.24):  # hole edges of the four uprights: 1 cm x 1 cm each
        for x in (-1.42, 1.41):
            for y in (-0.52, 0.51):
                quad(x, y, x + 0.01, y + 0.01, z)
    assert [round(b[1][2], 3) for b in board_layers(pts, counts, idx)] == [1.2]
    quad(-1.4, -0.03, 1.4, 0.03, 1.23)  # a cross bar over the deck: 2.8 m x 6 cm
    assert [round(b[1][2], 3) for b in board_layers(pts, counts, idx)] == [1.23]


def test_board_ends_inside_the_uprights():
    """A rack's deck layer reaches into its uprights; boxes stocked to its ends were pushed in by them."""
    from agentic_sim_world_gen.assets.shelves import board_layers

    pts, counts, idx = [], [], []

    def face(a, b, c, d):
        n = len(pts)
        pts.extend([a, b, c, d])
        counts.append(4)
        idx.extend([n, n + 1, n + 2, n + 3])

    for i in range(10):  # deck at 1.2 from -1.4 to 1.4
        x = -1.4 + i * 0.3
        face((x, -0.5, 1.2), (x + 0.08, -0.5, 1.2), (x + 0.08, 0.5, 1.2), (x, 0.5, 1.2))
    for x in (-1.35, 1.35):  # inner faces of the four uprights, 0 to 3 m
        for y in (-0.52, 0.45):
            face((x, y, 0), (x, y + 0.07, 0), (x, y + 0.07, 3), (x, y, 3))
    face((-1.4, 0.5, 1.15), (1.4, 0.5, 1.15), (1.4, 0.5, 1.3), (-1.4, 0.5, 1.3))  # a back panel: not an end
    (lo, hi), = board_layers(pts, counts, idx)
    assert (round(lo[0], 3), round(hi[0], 3)) == (-1.35, 1.35)
    assert (round(lo[1], 3), round(hi[1], 3)) == (-0.5, 0.5)


def test_fill_lay_flat(tmp_path):
    pytest.importorskip("pxr")
    from pxr import Gf, Usd, UsdGeom

    # 0.1 x 0.066 x 0.18 pack laid on its back: 0.18 m deep and 0.066 m high on the board
    st = make(tmp_path, [
        {"id": "g", "query": "gondola", "layout": {"against_wall": "north"}},
        {"id": "p", "query": "blister", "fill": {"on": "g", "levels": [1], "lay_flat": True}},
    ])
    assert st["issues"] == []
    s = service.load(tmp_path / "s")
    g, o = s.obj("g"), s.obj("p")
    items = instances(o, s.placements[1].asset)
    ys = sorted({i.position[1] for i in items})
    assert len(ys) == 2 and ys[1] - ys[0] == pytest.approx(0.19)  # 0.4 m board: 2 rows of 0.18 + gap
    assert len(items) == 8 * 2
    from agentic_sim_world_gen.layout.array import placed_size
    assert placed_size(o, s.placements[1].asset) == pytest.approx((0.1, 0.18, 0.066))

    out = service.compose_scene(tmp_path / "s")
    stage = Usd.Stage.Open(out["usd"])
    first = stage.GetPrimAtPath("/World/Objects/p").GetChildren()[0]
    m = UsdGeom.Xformable(first).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
    pt = lambda x, y, z: m.Transform(Gf.Vec3d(x, y, z))  # noqa: E731
    corners = [pt(x, y, z) for x in (-0.05, 0.05) for y in (-0.033, 0.033) for z in (0.0, 0.18)]
    zs = [c[2] for c in corners]
    i0 = next(i for i in items if first.GetName().endswith(i.id.split(".", 1)[1]))
    assert min(zs) == pytest.approx(i0.z, abs=1e-6) and max(zs) - min(zs) == pytest.approx(0.066, abs=1e-6)
    cx = sum(c[0] for c in corners) / 8
    cy = sum(c[1] for c in corners) / 8
    assert (cx, cy) == pytest.approx(i0.position, abs=1e-6)
    # the front (-Y, the blister) faces up; the card's top points to the back of the board (north)
    assert pt(0, -0.033, 0.09)[2] > pt(0, 0.033, 0.09)[2]
    assert pt(0, 0, 0.18)[1] > pt(0, 0, 0.0)[1]


def test_request_on_counts_shelf_boards(tmp_path):
    """Products filled onto a lower board are 'on' the fixture; a unit id narrows the target."""
    make(tmp_path, [
        {"id": "g", "query": "gondola", "layout": {"center": True}, "array": {"count": 2, "rows": 2}},
        {"id": "a", "query": "cup noodle", "fill": {"on": "g", "levels": [0], "rows": 1, "units": ["r0c0"]}},
        {"id": "s", "query": "sign", "layout": {"against_wall": "north"}},
    ])
    st = service.check_request(tmp_path / "s", [{"text": "棚に商品", "checks": [
        {"kind": "on", "object": "a", "target": "g"},
        {"kind": "on", "object": "a", "target": "g.r0c0"},
        {"kind": "on", "object": "a", "target": "g.r1c0"},
        {"kind": "on", "object": "s", "target": "g"},
        {"kind": "on", "object": "a", "target": "g.r9c9"},
    ]}])
    ok = [(c["ok"], c["detail"]) for c in st["requirements"][0]["checks"]]
    assert ok[0][0] is True and "棚板（0 段目" in ok[0][1]
    assert ok[1][0] is True
    assert ok[2][0] is False
    assert ok[3][0] is False
    assert ok[4][0] is None and "見つからない" in ok[4][1]


def test_fill_static_rows_behind_the_front(tmp_path, monkeypatch):
    """Fill.physics "front": only the front row is simulated; the rows behind keep their colliders but
    their rigid bodies are disabled (the asset's root, and bodies further down when the asset resolves)."""
    pytest.importorskip("pxr")
    from pxr import Usd, UsdGeom, UsdPhysics

    src = Usd.Stage.CreateNew(str(tmp_path / "cup.usda"))
    UsdGeom.SetStageUpAxis(src, UsdGeom.Tokens.z)
    root = UsdGeom.Xform.Define(src, "/Cup").GetPrim()
    src.SetDefaultPrim(root)
    body = UsdGeom.Xform.Define(src, "/Cup/Body").GetPrim()
    UsdPhysics.RigidBodyAPI.Apply(body)
    UsdPhysics.CollisionAPI.Apply(UsdGeom.Cube.Define(src, "/Cup/Body/Shape").GetPrim())
    src.GetRootLayer().Save()
    cup = rec("cup", 0.1, 0.1, 0.12)
    cup.uri = str(tmp_path / "cup.usda")
    monkeypatch.setitem(CATALOG, "cup noodle", [cup])

    st = make(tmp_path, [
        {"id": "g", "query": "gondola", "layout": {"center": True}},
        {"id": "cups", "query": "cup noodle", "fill": {"on": "g", "levels": [0], "physics": "front"}},
    ])
    assert st["issues"] == []
    s = service.load(tmp_path / "s")
    f = s.obj("cups").fill
    front = [n for n, *_ in f.slots if f.simulated(n)]
    assert front and len(front) < len(f.slots) and all(n.endswith("_0") for n in front)
    assert service._bodies(s) == 1 + len(front)
    stage = Usd.Stage.Open(service.compose_scene(tmp_path / "s")["usd"])
    for p in stage.GetPrimAtPath("/World/Objects/cups").GetChildren():
        sim = f.simulated(p.GetName().removeprefix("cups_"))
        enabled = UsdPhysics.RigidBodyAPI(p.GetPrimAtPath("Asset/Body")).GetRigidBodyEnabledAttr().Get()
        assert enabled is sim  # the schema default is True
        assert bool(p.GetCustomDataByKey("agentic_sim_world_gen:display")) is not sim
        assert p.GetPrimAtPath("Asset/Body/Shape").HasAPI(UsdPhysics.CollisionAPI)  # still a static collider


# Pegboard, front -Y: the board at the back, hooks sticking out toward the front in 3 rows of 3
PEG_BOXES = [("Board", (-0.45, 0.24, 0.6), (0.45, 0.26, 1.8)), ("Base", (-0.45, -0.3, 0.0), (0.45, 0.3, 0.1))] + [
    (f"Hook{r}{c}", (x - 0.0025, 0.0, z - 0.0025), (x + 0.0025, 0.24, z + 0.0025))
    for r, z in enumerate((1.0, 1.25, 1.5)) for c, x in enumerate((-0.3, 0.0, 0.3))
] + [("Tip00", (-0.305, 0.0, 0.995), (-0.295, 0.01, 1.005))]  # a tip cap: too small to hold or take a hook


def test_hooks_from_boxes():
    from agentic_sim_world_gen.assets.shelves import hooks_from_boxes

    hooks = hooks_from_boxes(PEG_BOXES)
    assert len(hooks) == 9 and not any(h["busy"] for h in hooks)
    assert hooks[0]["base"] == [-0.3, 0.24] and hooks[0]["tip"] == [-0.3, 0.0] and hooks[0]["z"] == 1.0
    # wire mesh rods along the width are not hooks; a card hanging under a hook takes it
    taken = hooks_from_boxes(PEG_BOXES + [("Wire", (-0.45, 0.235, 1.1), (0.45, 0.24, 1.105)),
                                          ("Card", (-0.35, 0.02, 0.83), (-0.25, 0.05, 1.012))])
    assert len(taken) == 9 and [h["part"] for h in taken if h["busy"]] == ["Hook00"]


def test_fill_hang(tmp_path):
    from agentic_sim_world_gen.assets.shelves import hooks_from_boxes

    hooks = hooks_from_boxes(PEG_BOXES)
    CATALOG["pegboard"] = [AssetRecord(
        id="peg", source="fake", uri="https://example.com/peg.usd", name="peg", bbox_min=(-0.45, -0.3, 0.0),
        bbox_max=(0.45, 0.3, 1.8), up_axis="Z", meters_per_unit=1.0, semantic_class="pegboard", license="CC0-1.0",
        front_deg=270.0, shelves=[], hooks=hooks)]
    CATALOG["tall blister"] = [rec("tall", 0.1, 0.03, 0.3, front_deg=270.0)]
    st = make(tmp_path, [
        {"id": "p", "query": "pegboard", "layout": {"against_wall": "north"}},
        {"id": "b", "query": "blister", "fill": {"on": "p", "hang": True}},
    ])
    assert st["issues"] == []
    s = service.load(tmp_path / "s")
    p, b = s.obj("p"), s.obj("b")
    slots = b.fill.slots
    # 9 hooks x 2 packs (0.24 m hook - 0.015 at the tip - 0.01 at the base: 2 x 0.066 + 0.01 gap; 3 need 0.218)
    assert len(slots) == 18 and {n.split("_")[1] for n, *_ in slots} == {"h0", "h1", "h2"}
    # the pegboard faces south (back on the north wall): packs face south too, their top just above the hook,
    # the first one just behind the tip (north of the board's middle, where the hooks end)
    assert {round(z - p.z, 3) for *_, z in slots} == {round(z + 0.012 - 0.18, 3) for z in (1.0, 1.25, 1.5)}
    first = next(sl for sl in slots if sl[0] == "u_h2_1_0")
    assert first[1] == pytest.approx(p.position[0]) and first[2] == pytest.approx(p.position[1] + 0.015 + 0.033)
    assert not b.fill.simulated("u_h2_1_0")
    # every hook is taken by "b": nothing more hangs there
    st = service.add_object(tmp_path / "s", {"id": "t", "query": "tall blister", "fill": {"on": "p", "hang": True}})
    assert any(i["kind"] == "shelf_fit" and "nothing hangs" in i["detail"] for i in st["issues"])
    service.remove_object(tmp_path / "s", "t")
    service.remove_object(tmp_path / "s", "b")
    # packs too tall for the row pitch: the rows below the top one give way
    st = service.add_object(tmp_path / "s", {"id": "t", "query": "tall blister",
                                             "fill": {"on": "p", "hang": True, "levels": [2, 1]}})
    t = service.load(tmp_path / "s").obj("t")
    assert {n.split("_")[1] for n, *_ in t.fill.slots} == {"h2"}
    assert any(i["kind"] == "shelf_fit" and "hook rows [1]" in i["detail"] for i in st["issues"])


def test_stock_sdf_colliders_become_boxes(tmp_path, monkeypatch):
    """Products on a shelf get bounding boxes in place of SDF colliders; the fixture keeps its own."""
    pytest.importorskip("pxr")
    from pxr import Sdf, Usd, UsdGeom, UsdPhysics

    from agentic_sim_world_gen.usd import reset_parts

    # the asset: an SDF mesh in an instance and one on the root (what sdf_paths finds)
    src = Usd.Stage.CreateNew(str(tmp_path / "cup.usda"))
    UsdGeom.Mesh.Define(src, "/Proto/Mesh")
    src.GetPrimAtPath("/Proto").SetSpecifier(Sdf.SpecifierClass)
    proto = src.GetPrimAtPath("/Proto/Mesh")
    UsdPhysics.CollisionAPI.Apply(proto)
    UsdPhysics.MeshCollisionAPI.Apply(proto).CreateApproximationAttr("sdf")
    root = UsdGeom.Mesh.Define(src, "/Cup").GetPrim()
    UsdPhysics.CollisionAPI.Apply(root)
    UsdPhysics.MeshCollisionAPI.Apply(root).CreateApproximationAttr("sdf")
    src.SetDefaultPrim(root)
    lid = UsdGeom.Xform.Define(src, "/Cup/Lid").GetPrim()
    lid.GetReferences().AddInternalReference("/Proto")
    lid.SetInstanceable(True)
    hull = UsdGeom.Mesh.Define(src, "/Cup/Handle").GetPrim()
    UsdPhysics.MeshCollisionAPI.Apply(hull).CreateApproximationAttr("convexHull")
    assert reset_parts.sdf_paths(src) == [{"path": ".", "instance": None},
                                          {"path": "Lid/Mesh", "instance": "Lid"}]

    seen = []
    monkeypatch.setattr(reset_parts, "scan_sdf", lambda uri, cache: seen.append(uri) or reset_parts.sdf_paths(src))
    make(tmp_path, [
        {"id": "g", "query": "gondola", "layout": {"center": True}},
        {"id": "cups", "query": "cup noodle", "fill": {"on": "g", "levels": [0], "rows": 1}},
    ])
    stage = Usd.Stage.Open(service.compose_scene(tmp_path / "s")["usd"])
    assert seen == ["https://example.com/cup.usd"]  # once per stocked object, not for the gondola
    group = stage.GetPrimAtPath("/World/Objects/cups")
    assert group.GetCustomDataByKey("agentic_sim_world_gen:box_colliders") == 2
    layer = stage.GetRootLayer()
    for kid in group.GetChildren():
        ref = f"{kid.GetPath()}/Asset"
        assert layer.GetAttributeAtPath(f"{ref}.physics:approximation").default == "boundingCube"
        assert layer.GetAttributeAtPath(f"{ref}/Lid/Mesh.physics:approximation").default == "boundingCube"
        assert layer.GetPrimAtPath(f"{ref}/Lid").instanceable is False
    assert not stage.GetPrimAtPath("/World/Objects/g").GetCustomDataByKey("agentic_sim_world_gen:box_colliders")
