"""Outdoor sites: areas (road, sidewalk, ...), buildings, ground placement, sky (no network)."""

import pytest

from agentic_sim_world_gen import service
from agentic_sim_world_gen.assets.record import AssetRecord
from agentic_sim_world_gen.layout.room import RoomGeometry
from agentic_sim_world_gen.scene.site import convex_pieces, signed_area, triangulate
from agentic_sim_world_gen.scene.spec import Room


def rec(name, w, d, h):
    """Fake asset, front +X."""
    return AssetRecord(
        id=name, source="fake", uri=f"https://example.com/{name}.usd", name=name,
        bbox_min=(-w / 2, -d / 2, 0.0), bbox_max=(w / 2, d / 2, h), up_axis="Z", meters_per_unit=1.0,
        semantic_class=name, license="CC-BY 4.0",
    )


CATALOG = {"pallet": [rec("pallet", 1.0, 1.0, 1.0)], "bench": [rec("bench", 0.6, 1.6, 0.8)],
           "cone": [rec("cone", 0.4, 0.4, 0.7)], "car": [rec("car", 4.5, 1.8, 1.5)]}


@pytest.fixture(autouse=True)
def fake():
    service.set_provider(type("P", (), {"name": "f", "search": lambda self, q, limit=10, size=None: CATALOG.get(q, [])})())
    yield
    service.set_provider(None)


SITE = {
    "outdoor": True, "width": 30, "depth": 20,
    "areas": [
        {"id": "road", "kind": "road", "rect": [0, 0, 30, 6]},
        {"id": "walk", "kind": "sidewalk", "rect": [0, 6, 30, 9]},
        {"id": "yard", "kind": "plaza", "polygon": [[0, 9], [0, 12], [30, 12], [30, 9]]},  # clockwise on purpose
    ],
    "buildings": [{"id": "shed", "rect": [5, 12, 25, 20], "height": 5}],
}


def scene(tmp_path, objects, **room):
    return service.create_scene({"name": "t", "room": {**SITE, **room}, "objects": objects}, tmp_path / "t")


def objs(st):
    return {o["id"]: o for o in st["objects"]}


def kinds(st):
    return {(i["kind"], i["objects"][0].split(".")[0]) for i in st["issues"]}


def test_room_defaults_and_validation():
    r = Room(**SITE)
    assert (r.floor, r.lighting, r.height) == ("grass", "sun", 10)
    with pytest.raises(ValueError, match="outdoor"):
        Room(width=5, depth=5, areas=[{"id": "a", "rect": [0, 0, 1, 1]}])
    with pytest.raises(ValueError):
        Room(**SITE, ceiling=True)
    with pytest.raises(ValueError, match="unique|duplicate"):
        Room(**{**SITE, "buildings": [{"id": "road", "rect": [5, 12, 25, 20]}]})
    with pytest.raises(ValueError):
        Room(**{**SITE, "areas": [{"id": "x", "kind": "road"}]})  # no polygon / rect


def test_geometry_faces_and_heights():
    g = RoomGeometry(Room(**SITE))
    assert g.bounds == (0, 0, 30, 20)
    assert g.zone_top("walk") == pytest.approx(0.15) and g.zone_top("road") == 0 and g.zone_top(None) == 0
    assert g.ground_z((10, 7)) == pytest.approx(0.15) and g.ground_z((10, 3)) == 0
    (f,) = g.faces_for("shed:south", None)
    assert f.facing == pytest.approx(-90) and f.p0[1] == pytest.approx(12) and f.length == pytest.approx(20)
    assert len(g.faces_for("shed", None)) == 4
    # area faces look inward: the walk's north edge faces south, the road's north edge too
    (wn,) = g.faces_for("north", "walk")
    assert wn.facing == pytest.approx(-90) and wn.p0[1] == pytest.approx(9)
    # site edges without a zone
    (se,) = g.faces_for("east", None)
    assert se.facing == pytest.approx(180) and se.p0[0] == pytest.approx(30)
    assert {ob.id.split("#")[0] for ob in g.obstacles} >= {"building:shed", "curb:walk", "area:road"}


def test_layout_on_site(tmp_path):
    st = scene(tmp_path, [
        {"id": "pallets", "query": "pallet", "zone": "yard", "layout": {"against_wall": "shed:south"},
         "array": {"count": 4, "spacing": 0.2}},
        {"id": "bench", "query": "bench", "zone": "walk", "layout": {"against_wall": "north"}},
        {"id": "cone", "query": "cone", "zone": "road", "layout": {"against_wall": "north", "gap": 0.3}},
    ])
    assert st["issues"] == []
    o = objs(st)
    assert o["pallets"]["position"][1] == pytest.approx(12 - 0.5 - 0.02)
    assert o["pallets"]["yaw_deg"] == pytest.approx(-90)  # front (+X) turned to face south
    assert o["bench"]["z"] == pytest.approx(0.15)  # on the curb-high sidewalk
    assert o["cone"]["z"] == 0 and o["cone"]["position"][1] == pytest.approx(6 - 0.2 - 0.3)


def test_site_issues(tmp_path):
    st = scene(tmp_path, [
        {"id": "car", "query": "car", "position": [10, 3]},  # on the road without zone "road"
        {"id": "box", "query": "pallet", "position": [10, 12.2]},  # into the shed
        {"id": "low", "query": "pallet", "position": [3, 9]},  # across the curb at ground level
        {"id": "far", "query": "pallet", "position": [29.8, 15]},
    ])
    k = kinds(st)
    assert ("in_keep_clear", "car") in k and ("hits_building", "box") in k
    assert ("on_curb", "low") in k and ("out_of_room", "far") in k
    st = scene(tmp_path, [{"id": "car", "query": "car", "zone": "road", "position": [10, 3]}])
    assert st["issues"] == []  # a car whose zone is the road may stand on it


def test_triangulate_concave():
    poly = [(0, 0), (4, 0), (4, 1), (1, 1), (1, 3), (0, 3)]  # L shape, ccw
    for p in (poly, list(reversed(poly))):
        tris = triangulate(p)
        assert len(tris) == len(p) - 2
        assert sum(abs(signed_area([p[i], p[j], p[k]])) for i, j, k in tris) == pytest.approx(6)
        assert all(signed_area([p[i], p[j], p[k]]) > 0 for i, j, k in tris)  # ccw
    assert len(convex_pieces(poly)) == 4 and convex_pieces(poly[:4]) == [poly[:4]]


def test_preview_and_reports(tmp_path):
    from agentic_sim_world_gen import license, sdg
    from agentic_sim_world_gen.preview.layout2d import render_svg

    scene(tmp_path, [{"id": "bench", "query": "bench", "zone": "walk", "layout": {"against_wall": "north"}}])
    s = service.load(tmp_path / "t")
    svg = render_svg(s)
    assert "屋外" in svg and "shed" in svg and "空 clear" in svg
    r = license.report(s)
    assert r["environment"][0]["license"] == "NVIDIA-Isaac-Sim" and "noon_grass" in r["environment"][0]["uri"]
    p = sdg.plan(s)
    assert p["stuff"] == ["ground", "road", "sidewalk", "plaza", "building"]


def test_compose_site(tmp_path):
    pytest.importorskip("pxr")
    from pxr import UsdLux, UsdPhysics, UsdSemantics

    from agentic_sim_world_gen.usd.compose import compose

    scene(tmp_path, [{"id": "bench", "query": "bench", "zone": "walk", "layout": {"against_wall": "north"}}],
          lighting="street_night")
    stage = compose(service.load(tmp_path / "t"), tmp_path / "scene.usda")
    room = stage.GetPrimAtPath("/World/Room")
    assert list(room.GetCustomDataByKey("agentic_sim_world_gen:site")) == [0, 0, 30, 20]
    for path, cls in (("/World/Room/Ground", "ground"), ("/World/Room/Areas/walk", "sidewalk"),
                      ("/World/Room/Buildings/shed", "building")):
        prim = stage.GetPrimAtPath(path)
        assert prim and prim.HasAPI(UsdPhysics.CollisionAPI), path
        # triangle meshes: convex hulls of the big ground / buildings made objects next to them slide
        assert prim.GetAttribute("physics:approximation").Get() == "none", path
        assert list(UsdSemantics.LabelsAPI(prim, "class").GetLabelsAttr().Get()) == [cls]
    assert not stage.GetPrimAtPath("/World/Room/Ceiling") and not stage.GetPrimAtPath("/World/Room/Wall_S")
    dome = UsdLux.DomeLight(stage.GetPrimAtPath("/World/Lights/Dome"))
    assert "moonlit_golf" in dome.GetTextureFileAttr().Get().path
    lamps = [p for p in stage.GetPrimAtPath("/World/Lights").GetChildren() if p.GetName() not in ("Dome", "Sun")]
    assert lamps and all(p.GetAttribute("xformOp:translate").Get()[2] == pytest.approx(6.0) for p in lamps)
    walk = stage.GetPrimAtPath("/World/Room/Areas/walk")
    zs = [v[2] for v in walk.GetAttribute("points").Get()]
    assert max(zs) == pytest.approx(0.15)


def test_street_lamps_avoid_buildings():
    g = RoomGeometry(Room(**SITE, lighting="street_night"))
    pts = g.fixture_points(6.0)
    assert pts and not any(5 < x < 25 and 12 < y < 20 for x, y in pts)


def test_flat_areas_not_above_ground_level(tmp_path):
    pytest.importorskip("pxr")
    from agentic_sim_world_gen.usd.compose import compose

    scene(tmp_path, [])
    stage = compose(service.load(tmp_path / "t"), tmp_path / "scene.usda")
    tops = {p.GetName(): max(v[2] for v in p.GetAttribute("points").Get())
            for p in stage.GetPrimAtPath("/World/Room/Areas").GetChildren()}
    assert tops["walk"] == pytest.approx(0.15)
    assert max(tops["road"], tops["yard"]) == pytest.approx(0) and tops["road"] < tops["yard"]  # objects at z=0 don't penetrate
    ground = max(v[2] for v in stage.GetPrimAtPath("/World/Room/Ground").GetAttribute("points").Get())
    assert ground < min(tops.values())
