"""Relational layout solver and 3D overlap check (no network)."""

import dataclasses

import pytest

from agentic_sim_world_gen import service
from agentic_sim_world_gen.assets.record import AssetRecord


def rec(name, cls, w, d, h, front_deg=270.0):
    """Fake asset; front -Y (front_deg 270) so the expected layouts read naturally (front = short side)."""
    return AssetRecord(
        id=name, source="fake", uri=f"https://example.com/{name}.usd", name=name,
        bbox_min=(-w / 2, -d / 2, 0.0), bbox_max=(w / 2, d / 2, h), up_axis="Z", meters_per_unit=1.0,
        semantic_class=cls, license="CC-BY 4.0", front_deg=front_deg,
    )


CATALOG = {
    "desk": [rec("desk", "desk", 1.4, 0.7, 0.74)],
    "office chair": [rec("chair", "office chair", 0.55, 0.55, 0.85)],
    "bookshelf": [rec("shelf", "bookcase", 0.8, 0.35, 1.8)],
    "desk lamp": [rec("lamp", "lamp", 0.2, 0.2, 0.4)],
    "sofa": [rec("sofa", "couch", 1.8, 0.9, 0.8)],
    "swivel chair": [dataclasses.replace(rec("swivel", "office chair", 0.6, 0.6, 1.0), rigid_body_count=13)],
    "rack": [dataclasses.replace(rec("rack", "shelving rack", 0.6, 0.4, 1.8), rigid_body_count=9)],
    "tv stand": [rec("stand", "tv stand", 1.3, 0.45, 0.5)],
    "television": [dataclasses.replace(rec("tv", "television", 1.27, 0.106, 0.755), rigid_body_count=1)],
}


@pytest.fixture(autouse=True)
def fake():
    service.set_provider(type("P", (), {"name": "f", "search": lambda self, q, limit=10, size=None: CATALOG.get(q, [])})())
    yield
    service.set_provider(None)


ROOM = {"width": 3.64, "depth": 2.73, "height": 2.4}


def scene(tmp_path, objects):
    return service.create_scene({"name": "t", "room": ROOM, "objects": objects}, tmp_path / "t")


def objs(st):
    return {o["id"]: o for o in st["objects"]}


def test_study_relations(tmp_path):
    st = scene(tmp_path, [
        {"id": "desk", "query": "desk", "layout": {"against_wall": "north"}},
        {"id": "chair", "query": "office chair", "layout": {"in_front_of": "desk"}},
        {"id": "lamp", "query": "desk lamp", "layout": {"on": "desk"}},
        {"id": "shelf", "query": "bookshelf", "layout": {"against_wall": "any"}},
    ])
    assert st["issues"] == []
    o = objs(st)
    assert o["desk"]["position"] == pytest.approx((1.82, 2.73 - 0.35 - 0.02))
    assert o["desk"]["yaw_deg"] == 0  # front (-Y) faces into the room
    # chair faces the desk, NEAR_GAP in front of it (no automatic tuck: it explodes in PhysX)
    assert o["chair"]["yaw_deg"] == 180
    assert o["chair"]["position"][0] == pytest.approx(1.82)
    assert o["chair"]["position"][1] == pytest.approx(2.36 - (0.35 + 0.275 + 0.10))
    assert o["lamp"]["z"] == pytest.approx(0.74) and o["lamp"]["position"] == o["desk"]["position"]
    # shelf finds a free spot (north wall beside the desk)
    sx = o["shelf"]["position"][0]
    assert sx + 0.4 <= 1.12 or sx - 0.4 >= 2.52


def test_wall_sides_and_along(tmp_path):
    st = scene(tmp_path, [
        {"id": "a", "query": "bookshelf", "layout": {"against_wall": "west", "along": 0.0}},
        {"id": "b", "query": "bookshelf", "layout": {"against_wall": "east", "along": 1.0}},
    ])
    o = objs(st)
    assert st["issues"] == []
    assert o["a"]["position"] == pytest.approx((0.175 + 0.02, 0.4))
    assert o["b"]["position"] == pytest.approx((3.64 - 0.195, 2.73 - 0.4))


def test_next_to_and_resolve_after_swap(tmp_path):
    st = scene(tmp_path, [
        {"id": "desk", "query": "desk", "layout": {"against_wall": "north", "along": 0.5}},
        {"id": "shelf", "query": "bookshelf", "layout": {"next_to": "desk", "side": "right"}},
    ])
    o = objs(st)
    # desk front faces -Y; a person sitting at it looks +Y, so their right is +X
    assert o["shelf"]["position"][0] - o["desk"]["position"][0] == pytest.approx(0.7 + 0.4 + 0.05)
    assert o["shelf"]["position"][1] == pytest.approx(o["desk"]["position"][1])
    assert st["issues"] == []


def test_overlap_is_3d_and_allows_tuck(tmp_path):
    d = tmp_path / "t"
    scene(tmp_path, [
        {"id": "desk", "query": "desk", "position": [1.8, 2.0]},
        {"id": "chair", "query": "office chair", "position": [1.8, 1.6]},  # 0.17 m under the desk: OK
        {"id": "lamp", "query": "desk lamp", "position": [1.8, 2.0], "layout": {"on": "desk"}},
    ])
    assert service.status(d)["issues"] == []
    st = service.move_object(d, "chair", position=(1.8, 1.9))  # 0.47 m: too deep
    assert [i["kind"] for i in st["issues"]] == ["overlap"]
    st = service.add_object(d, {"id": "sofa", "query": "sofa", "position": [1.8, 1.3]})
    assert {tuple(i["objects"]) for i in st["issues"]} >= {("chair", "sofa")}  # sofa is not a desk


def test_chair_at_a_table_corner_is_an_overlap():
    """A chair tucked under a table through a side is fine; one straddling the corner hits the leg there
    (physics pushed four dining chairs 0.15 m out)."""
    from agentic_sim_world_gen.layout.validate import footprint, through_side
    table = footprint((4.3, 1.65), (1.134, 0.706), 90.0)  # long side along y: x 3.95-4.65, y 1.08-2.22
    assert through_side(footprint((3.8, 1.65), (0.45, 0.47), 0.0), table)  # from the west side, mid-way
    assert through_side(footprint((4.3, 1.0), (0.45, 0.47), 90.0), table)  # from the south end, centered
    assert not through_side(footprint((3.95, 1.0), (0.45, 0.47), 90.0), table)  # at the south-west corner
    assert not through_side(footprint((4.3, 0.9), (0.45, 0.8), 90.0), table)  # wider than the end


@pytest.mark.no_auto_fix
def test_flat_tv_on_a_stand_tips_over(tmp_path):
    """A TV with no foot (0.11 m deep, 0.76 m tall) on a TV stand falls over in physics; hung on the
    wall above it, it is fine."""
    d = tmp_path / "t"
    st = scene(tmp_path, [{"id": "stand", "query": "tv stand", "layout": {"against_wall": "north"}},
                          {"id": "tv", "query": "television", "layout": {"on": "stand"}}])
    assert [(i["kind"], i["objects"]) for i in st["issues"]] == [("tips_over", ["tv"])]
    assert "wall_height" in st["issues"][0]["detail"]
    st = service.set_layout(d, "tv", {"against_wall": "north", "wall_height": 0.95})
    assert st["issues"] == []


def test_move_clears_layout_and_bad_refs(tmp_path):
    d = tmp_path / "t"
    st = scene(tmp_path, [{"id": "desk", "query": "desk", "layout": {"against_wall": "south"}},
                          {"id": "chair", "query": "office chair", "layout": {"in_front_of": "nope"}}])
    assert any(i["kind"] == "bad_layout" for i in st["issues"])
    st = service.move_object(d, "desk", position=(1.0, 1.0))
    assert objs(st)["desk"]["layout"] is None and tuple(objs(st)["desk"]["position"]) == (1.0, 1.0)
    st = service.set_layout(d, "desk", {"against_wall": "south"}, front_offset_deg=180)
    assert objs(st)["desk"]["yaw_deg"] == 0  # 180 without the correction (south wall, front -Y)


RACK = rec("rack", "shelving rack", 2.7, 1.1, 2.5)
FORKLIFT = rec("forklift", "forklift", 2.2, 1.2, 2.1)


def test_rack_rows_with_aisles(tmp_path):
    CATALOG["rack"], CATALOG["forklift"] = [RACK], [FORKLIFT]
    st = service.create_scene({"name": "wh", "room": {"width": 20, "depth": 12, "height": 6}, "objects": [
        {"id": "racks", "query": "rack", "layout": {"against_wall": "south"},
         "array": {"count": 5, "rows": 4, "aisle": 3.0, "alternate": True}},
        {"id": "fl", "query": "forklift", "position": (10.0, 2.65), "yaw_deg": 0},  # parked in the first aisle
    ]}, tmp_path / "wh")
    assert st["issues"] == []
    o = objs(st)
    # block depth: 4 rows of 1.1 + aisle 3 + back gap 0.1 + aisle 3 = 10.5, back on the south wall
    assert o["racks"]["position"] == pytest.approx((10.0, 0.02 + 10.5 / 2))
    from agentic_sim_world_gen.layout.array import instances

    s = service.load(tmp_path / "wh")
    units = instances(s.obj("racks"), s.placements[0].asset)
    assert len(units) == 20
    r0 = [u for u in units if u.id.startswith("racks.r0")]
    r1 = [u for u in units if u.id.startswith("racks.r1")]
    assert r0[0].position[1] == pytest.approx(0.02 + 0.55) and r0[0].yaw_deg == 180  # faces north
    assert r1[0].position[1] == pytest.approx(0.02 + 1.1 + 3.0 + 0.55) and r1[0].yaw_deg == 0  # faces south
    assert sorted(u.position[0] for u in r0) == pytest.approx([4.6, 7.3, 10.0, 12.7, 15.4])

    # a forklift standing on a rack is reported per unit
    st = service.move_object(tmp_path / "wh", "fl", position=(10.0, 0.6))
    assert any(i["kind"] == "overlap" and "racks.r0c2" in i["objects"] for i in st["issues"])


def test_back_to_back_island(tmp_path):
    CATALOG["rack"] = [RACK]
    st = service.create_scene({"name": "st", "room": {"width": 12, "depth": 10, "height": 3}, "objects": [
        {"id": "isle", "query": "rack", "layout": {"center": True},
         "array": {"count": 2, "rows": 4, "aisle": 1.2, "back_to_back": True, "back_gap": 0.05}},
    ]}, tmp_path / "st")
    assert st["issues"] == []
    from agentic_sim_world_gen.layout.array import instances

    s = service.load(tmp_path / "st")
    o = s.obj("isle")
    units = instances(o, s.placements[0].asset)
    yaw = {r: {u.yaw_deg for u in units if u.id.startswith(f"isle.r{r}")} for r in range(4)}
    flip = (o.yaw_deg + 180) % 360
    # <- r0 r1 -> | aisle | <- r2 r3 ->: the outer rows face out, each pair stands back to back
    assert yaw == {0: {flip}, 1: {o.yaw_deg % 360}, 2: {flip}, 3: {o.yaw_deg % 360}}
    c = {r: next(u.position for u in units if u.id.startswith(f"isle.r{r}")) for r in range(4)}
    d = lambda a, b: ((c[a][0] - c[b][0]) ** 2 + (c[a][1] - c[b][1]) ** 2) ** 0.5  # noqa: E731
    assert d(0, 1) == pytest.approx(1.1 + 0.05) and d(1, 2) == pytest.approx(1.1 + 1.2)


def test_array_compose(tmp_path):
    pytest.importorskip("pxr")
    from pxr import Usd

    CATALOG["rack"] = [RACK]
    service.create_scene({"name": "wh", "room": {"width": 10, "depth": 6, "height": 4}, "objects": [
        {"id": "racks", "query": "rack", "layout": {"center": True}, "array": {"count": 2, "rows": 2}},
    ]}, tmp_path / "wh")
    out = service.compose_scene(tmp_path / "wh")
    stage = Usd.Stage.Open(out["usd"])
    kids = stage.GetPrimAtPath("/World/Objects/racks").GetChildren()
    assert sorted(k.GetName() for k in kids) == ["racks_r0c0", "racks_r0c1", "racks_r1c0", "racks_r1c1"]
    assert all(k.GetCustomDataByKey("agentic_sim_world_gen:uri") for k in kids)


def test_front_convention():
    from agentic_sim_world_gen.layout.frame import asset_front_deg, front_local_deg, yaw_for_facing
    from agentic_sim_world_gen.scene.spec import ObjectSpec

    def r(uri, front=None):
        return AssetRecord(id=uri, source="fake", uri=uri, name=uri, bbox_min=(-1, -0.3, 0), bbox_max=(1, 0.3, 1),
                           front_deg=front)

    hf = "https://huggingface.co/datasets/nvidia/simready-assets/resolve/abc/sm_chair_office_amf_black_a01_01.usd"
    assert asset_front_deg(r("https://example.com/sm_sofa_soft_b01_01.usd")) == 0  # default +X
    assert asset_front_deg(r(hf)) == 270  # known exception (-Y)
    assert asset_front_deg(r("https://example.com/sm_rack_composition_l06_01.usd")) == 270  # rack family pattern
    assert asset_front_deg(r(hf, front=90)) == 90  # the record wins (local agentic-sim-world-gen.json)
    o = ObjectSpec(id="s", query="sofa", front_offset_deg=180)
    a = r("/x/sofa.usda")
    assert front_local_deg(a, o) == 180
    assert yaw_for_facing(a, ObjectSpec(id="s", query="sofa"), -90) == -90  # +X front turned to face -Y


def test_array_front_on_long_side(tmp_path):
    """Units sit side by side across their front even when the front is the long side (a row of
    chairs 0.55 wide and 0.62 deep ended up spaced by their depth and the block left the room)."""
    from agentic_sim_world_gen.layout.array import block_record, instances

    CATALOG["chair row"] = [rec("deep_chair", "chair", 0.55, 0.62, 0.9)]  # front -Y, the long side
    st = service.create_scene({"name": "row", "room": {"width": 4, "depth": 4, "height": 2.5}, "objects": [
        {"id": "chairs", "query": "chair row", "layout": {"against_wall": "north", "along": 0.5},
         "array": {"count": 5, "spacing": 0.1}},
    ]}, tmp_path / "row")
    assert st["issues"] == []
    s = service.load(tmp_path / "row")
    o, a = s.obj("chairs"), s.placements[0].asset
    assert block_record(o, a).size_m[:2] == pytest.approx((5 * 0.55 + 4 * 0.1, 0.62))
    xs = sorted(u.position[0] for u in instances(o, a))
    assert [b - a_ for a_, b in zip(xs, xs[1:])] == pytest.approx([0.65] * 4)
    assert all(u.position[1] == pytest.approx(4 - 0.02 - 0.31) for u in instances(o, a))


DOOR_ROOM = {**ROOM, "openings": [
    {"id": "toilet", "kind": "door", "wall": "west", "offset": 1.2, "width": 0.9, "height": 2.0},
    {"id": "back", "kind": "door", "wall": "north", "offset": 2.5, "width": 0.9, "height": 2.0},
]}


def door_scene(tmp_path, objects):
    CATALOG["door"] = [rec("door", "door", 1.0, 0.2, 2.05)]  # leaf + frame, deeper than the wall
    return service.create_scene({"name": "t", "room": DOOR_ROOM, "objects": objects}, tmp_path / "t")


def test_door_fills_its_opening(tmp_path):
    st = door_scene(tmp_path, [
        {"id": "toilet_door", "query": "door", "opening": "toilet"},
        {"id": "back_door", "query": "door", "opening": "back"},
    ])
    assert st["issues"] == []  # no out_of_room / blocks_door / hits_wall for its own opening
    o, t = objs(st), 0.12
    assert o["toilet_door"]["position"] == pytest.approx((-t / 2, 1.2))
    assert o["toilet_door"]["yaw_deg"] == pytest.approx(90)  # front (-Y) turned to +X: into the room
    assert o["back_door"]["position"] == pytest.approx((2.5, 2.73 + t / 2))
    assert o["back_door"]["yaw_deg"] == pytest.approx(0)  # front -Y: into the room
    assert o["toilet_door"]["z"] == 0


def test_door_by_hand_is_flagged(tmp_path):
    # The same door with a hand-written position
    st = door_scene(tmp_path, [{"id": "d", "query": "door", "position": [-0.06, 1.2], "yaw_deg": 90}])
    assert {i["kind"] for i in st["issues"]} >= {"out_of_room", "blocks_door"}


def test_door_keeps_others_out_of_the_opening(tmp_path):
    st = door_scene(tmp_path, [
        {"id": "toilet_door", "query": "door", "opening": "toilet"},
        {"id": "shelf", "query": "bookshelf", "position": [0.3, 1.2], "yaw_deg": 90},
    ])
    assert [(i["kind"], i["objects"]) for i in st["issues"]] == [("blocks_door", ["shelf"])]


def test_door_unknown_opening(tmp_path):
    st = door_scene(tmp_path, [{"id": "d", "query": "door", "opening": "nope"}])
    assert [i["kind"] for i in st["issues"]] == ["bad_opening"]
    assert "toilet" in st["issues"][0]["detail"]


def test_door_opening_excludes_layout():
    from agentic_sim_world_gen.scene.spec import ObjectSpec

    with pytest.raises(ValueError, match="opening places the object itself"):
        ObjectSpec(id="d", query="door", opening="toilet", layout={"against_wall": "west"})


def test_on_surface_offset_and_facing(tmp_path):
    # desk against north: front -Y (facing -90). A person facing its front looks north; their right is east.
    st = scene(tmp_path, [
        {"id": "desk", "query": "desk", "layout": {"against_wall": "north"}},
        {"id": "lamp", "query": "desk lamp", "layout": {"on": "desk", "on_height": 0.7, "offset": [0.5, -0.1]}},
        {"id": "lamp2", "query": "desk lamp", "layout": {"on": "desk", "offset": [-0.5, 0], "facing": "east"}},
    ])
    o = objs(st)
    dx, dy = o["desk"]["position"]
    assert o["lamp"]["position"] == pytest.approx((dx + 0.5, dy + 0.1))  # front is -Y: -0.1 front = +0.1 y
    assert o["lamp"]["z"] == pytest.approx(0.7)
    assert o["lamp"]["yaw_deg"] == o["desk"]["yaw_deg"]
    assert o["lamp2"]["position"] == pytest.approx((dx - 0.5, dy))
    assert o["lamp2"]["z"] == pytest.approx(0.74)
    assert o["lamp2"]["yaw_deg"] == pytest.approx(90)  # front -Y turned to +X (east)


def test_on_offset_off_the_support(tmp_path):
    st = scene(tmp_path, [
        {"id": "desk", "query": "desk", "layout": {"against_wall": "north"}},
        {"id": "lamp", "query": "desk lamp", "layout": {"on": "desk", "offset": [1.0, 0]}},
    ])
    assert ("unsupported", ["lamp", "desk"]) in [(i["kind"], i["objects"]) for i in st["issues"]]


def test_on_height_needs_on():
    from agentic_sim_world_gen.scene.spec import Layout

    with pytest.raises(ValueError, match="need layout.on"):
        Layout(against_wall="north", on_height=0.5)


def test_overlap_skips_what_it_rests_on(tmp_path):
    # sofa sunk into the desk (surface below the bbox top) and a lamp sunk into the sofa: the lamp
    # overlaps the desk in 3D but rests on it through the sofa -> no overlap. Two lamps on one spot do.
    st = scene(tmp_path, [
        {"id": "desk", "query": "desk", "layout": {"center": True}},
        {"id": "top", "query": "bookshelf", "layout": {"on": "desk", "on_height": 0.5}},
        {"id": "lamp", "query": "desk lamp", "layout": {"on": "top", "on_height": 0.0}},
        {"id": "lamp2", "query": "desk lamp", "layout": {"on": "top", "on_height": 0.0}},
    ])
    assert [(i["kind"], i["objects"]) for i in st["issues"]] == [("overlap", ["lamp", "lamp2"])]


def test_variants_checked_against_the_asset(tmp_path):
    lamp = rec("lit", "lamp", 0.2, 0.2, 0.4)
    lamp.variant_sets = {"Lighting": ["Off", "On"]}
    CATALOG["lit lamp"] = [lamp]
    st = scene(tmp_path, [
        {"id": "a", "query": "lit lamp", "position": [0.5, 0.5], "variants": {"Lighting": "Off"}},
        {"id": "b", "query": "lit lamp", "position": [1.5, 0.5], "variants": {"Lighting": "Dim"}},
        {"id": "c", "query": "lit lamp", "position": [2.5, 0.5], "variants": {"Color": "Red"}},
        {"id": "d", "query": "desk lamp", "position": [3.0, 1.5], "variants": {"Any": "Thing"}},  # sets not known
    ])
    assert [(i["kind"], i["objects"]) for i in st["issues"]] == [("bad_variant", ["b"]), ("bad_variant", ["c"])]
    assert "values: ['Off', 'On']" in st["issues"][0]["detail"]
    d = tmp_path / "t"
    service.set_variants(d, "b", {"Lighting": "On"})
    service.set_variants(d, "c", None)
    assert service.status(d)["issues"] == []


def test_ifc_furniture_scene(tmp_path):
    """Drafts from an IFC become objects that stay where the BIM put them, turned so the asset's
    front faces facing_deg; the USD prim keeps the GlobalId."""
    pytest.importorskip("ifcopenshell")
    from pxr import Usd

    from test_plan import _ifc_flat

    _ifc_flat(tmp_path / "f.ifc", furniture=True)
    imp = service.import_floor_plan(tmp_path / "f.ifc", tmp_path / "plan")
    objs = [o for o in imp["objects"] if o["query"] in CATALOG]
    assert {o["query"] for o in objs} == {"desk", "office chair", "bookshelf"}
    st = service.create_scene({"name": "bim", "room": {"plan_file": imp["files"]["plan"]}, "objects": objs},
                              tmp_path / "bim")
    assert st["issues"] == []
    s = service.load(tmp_path / "bim")
    by_q = {o["query"]: o for o in objs}
    for p in s.placements:
        o = s.obj(p.object_id)
        assert p.asset is not None and o.position == pytest.approx(by_q[o.query]["position"])
    # fake assets face local -Y (270): desk facing -90 -> yaw 0, chair facing 0 -> yaw 90
    assert s.obj(by_q["desk"]["id"]).yaw_deg % 360 == pytest.approx(0)
    assert s.obj(by_q["office chair"]["id"]).yaw_deg % 360 == pytest.approx(90)
    assert s.obj(by_q["bookshelf"]["id"]).z == pytest.approx(0.3)
    out = service.compose_scene(tmp_path / "bim")
    stage = Usd.Stage.Open(out["usd"])
    prim = stage.GetPrimAtPath(f"/World/Objects/{by_q['desk']['id']}")
    assert prim.GetCustomDataByKey("agentic_sim_world_gen:ifc_guid") == by_q["desk"]["ifc_guid"]


def test_overlap_sweep_matches_every_pair(tmp_path):
    # Check() sweeps along x; it must find the same overlaps, in the same order, as comparing every pair
    import random

    from agentic_sim_world_gen.layout.validate import check, footprint, overlap_depth

    rnd = random.Random(7)
    room = {"width": 8.0, "depth": 6.0, "height": 2.4}
    objects = [{"id": f"o{i}", "query": rnd.choice(["bookshelf", "sofa"]),
                "position": [round(rnd.uniform(1, 7), 2), round(rnd.uniform(1, 5), 2)], "yaw_deg": rnd.choice([0, 30, 90])}
               for i in range(40)]
    service.create_scene({"name": "t", "room": room, "objects": objects}, tmp_path / "t")
    s = service.load(tmp_path / "t")
    got = [i.objects for i in check(s) if i.kind == "overlap"]
    polys = []
    for p in s.placements:
        o = s.obj(p.object_id)
        polys.append((o.id, footprint(o.position, p.asset.size_m[:2], o.yaw_deg)))
    want = [(a, b) for i, (a, pa) in enumerate(polys) for b, pb in polys[i + 1:] if overlap_depth(pa, pb) > 0.01]
    assert len(want) > 5 and got == want


@pytest.mark.no_auto_fix
def test_jointed_chairs_side_by_side_need_a_gap(tmp_path):
    """Office chairs (13 jointed bodies) side by side with no gap blew up and stopped the GPU physics."""
    d = tmp_path / "t"
    st = scene(tmp_path, [{"id": "chairs", "query": "swivel chair", "position": [1.8, 1.0],
                           "array": {"count": 3, "spacing": 0.0}},
                          {"id": "racks", "query": "rack", "position": [1.8, 2.3], "array": {"count": 3, "spacing": 0.0}}])
    close = [i for i in st["issues"] if i["kind"] == "too_close"]
    assert {tuple(sorted(i["objects"])) for i in close} == {("chairs.r0c0", "chairs.r0c1"), ("chairs.r0c1", "chairs.r0c2")}
    st = service.set_array(d, "chairs", {"count": 3, "spacing": 0.65})  # 5 cm apart; racks touching stay fine
    assert not [i for i in st["issues"] if i["kind"] == "too_close"]
