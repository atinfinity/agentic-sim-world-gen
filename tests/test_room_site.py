"""The outside around an indoor room (Room.site): faces, layout outside, issues, USD, preview (no network)."""

import pytest

from agentic_sim_world_gen import service
from agentic_sim_world_gen.layout.room import RoomGeometry
from agentic_sim_world_gen.scene.spec import Room

from test_outdoor import rec

CATALOG = {"vending machine": [rec("vending", 0.9, 0.8, 1.8)], "car": [rec("car", 4.5, 1.8, 1.5)],
           "bench": [rec("bench", 0.6, 1.6, 0.8)], "shelf": [rec("shelf", 0.9, 0.4, 1.8)],
           "sign": [rec("sign", 0.5, 0.5, 5.0)]}


@pytest.fixture(autouse=True)
def fake():
    service.set_provider(type("P", (), {"name": "f", "search": lambda self, q, limit=10, size=None: CATALOG.get(q, [])})())
    yield
    service.set_provider(None)


SITE = {
    "rect": [-6, -10, 14, 12],
    "areas": [
        {"id": "road", "kind": "road", "rect": [-6, -10, 14, -5]},
        {"id": "walk", "kind": "sidewalk", "rect": [-6, -5, 14, -2.5]},
        {"id": "parking", "kind": "parking", "rect": [9, -2.5, 14, 6]},
    ],
    "buildings": [{"id": "next", "rect": [-6, 1, -2, 8], "height": 6}],
}
ROOM = {"width": 8, "depth": 6, "height": 2.7, "place": "convenience_store", "lighting": "office",
        "openings": [{"kind": "door", "wall": "south", "offset": 4, "width": 1.6}], "site": SITE}


def scene(tmp_path, objects, **room):
    return service.create_scene({"name": "t", "room": {**ROOM, **room}, "objects": objects}, tmp_path / "t")


def objs(st):
    return {o["id"]: o for o in st["objects"]}


def kinds(st):
    return {(i["kind"], i["objects"][0].split(".")[0]) for i in st["issues"]}


def test_room_validation():
    r = Room(**ROOM)
    assert r.footprint() == pytest.approx((-0.12, -0.12, 8.12, 6.12), abs=0.05)
    assert Room(**{**ROOM, "site": False}).site is None
    assert Room(**{**ROOM, "site": {}}).site.bounds(r.footprint()) == pytest.approx(
        (r.footprint()[0] - 8, r.footprint()[1] - 8, r.footprint()[2] + 8, r.footprint()[3] + 8))
    with pytest.raises(ValueError):
        Room(**{**ROOM, "outdoor": True})
    with pytest.raises(ValueError):
        Room(**{**ROOM, "site": {**SITE, "rect": [0, 0, 5, 5]}})  # does not hold the room
    with pytest.raises(ValueError):
        Room(**{**ROOM, "site": {"areas": [{"id": "outside", "kind": "plaza", "rect": [-5, -5, -1, -1]}]}})


def test_geometry():
    g = RoomGeometry(Room(**ROOM))
    assert g.outside("outside") and g.outside("walk") and not g.outside(None) and not g.outside("next")
    assert {o.id for o in g.obstacles} >= {"area:road", "building:next"}
    (f,) = g.faces_for("outside:south", "outside")
    assert f.p0[1] == pytest.approx(f.p1[1]) and f.p0[1] < 0 and f.facing == pytest.approx(-90)
    (b,) = g.faces_for("next:east", "outside")
    assert b.p0[0] == pytest.approx(-2) and b.facing == pytest.approx(0)
    assert g.faces_for("north", "walk")[0].p0[1] == pytest.approx(-2.5)
    cx, cy = g.center("parking")
    assert (cx, cy) == pytest.approx((11.5, 1.75))
    s = g.summary()["site"]
    assert s["outer_walls"] == ["east", "north", "south", "west"]
    assert [a["id"] for a in s["areas"]] == ["road", "walk", "parking"] and s["buildings"][0]["sides"]
    # inside the room (no zone) stays as before
    assert g.inside([(1, 1), (2, 1), (2, 2), (1, 2)])
    assert not g.inside([(1, 1), (2, 1), (2, 2), (1, 2)], zone="outside")  # an outside object in the room
    assert g.inside([(1, -2), (2, -2), (2, -1), (1, -1)], zone="outside")
    assert not g.inside([(13, -2), (15, -2), (15, -1), (13, -1)], zone="outside")  # off the site


def test_layout_outside(tmp_path):
    st = scene(tmp_path, [
        {"id": "shelf", "query": "shelf", "layout": {"against_wall": "north"}},
        {"id": "vending", "query": "vending machine", "zone": "outside",
         "layout": {"against_wall": "outside:south", "along": 0.2}},
        {"id": "bench", "query": "bench", "zone": "walk", "layout": {"against_wall": "north"}},
        {"id": "car", "query": "car", "zone": "parking", "layout": {"center": True}},
        {"id": "sign", "query": "sign", "zone": "outside", "layout": {"against_wall": "next:east"}},
    ])
    assert st["issues"] == [], st["issues"]
    o = objs(st)
    x, y = o["vending"]["position"]
    assert y == pytest.approx(-0.12 - 0.45 - 0.02) and x < 4  # its back to the outer wall, west of the door
    assert o["vending"]["yaw_deg"] == pytest.approx(-90)  # front (+X) to the south, out to the street
    assert o["bench"]["z"] == pytest.approx(0.15)  # on the curb-high sidewalk
    assert o["car"]["position"] == pytest.approx([11.5, 1.75])
    assert o["sign"]["position"][0] > -2  # 5 m tall outside: no too_tall
    assert 0 < o["shelf"]["position"][1] < 6
    assert "site" in st["room"]["layout"]


def test_site_issues(tmp_path):
    st = scene(tmp_path, [
        {"id": "in", "query": "bench", "zone": "outside", "position": [4, 3]},  # outside object in the room
        {"id": "off", "query": "bench", "zone": "outside", "position": [13.9, 11]},  # off the site
        {"id": "road", "query": "car", "zone": "outside", "position": [3, -7]},  # on the road
    ])
    k = kinds(st)
    assert ("out_of_room", "in") in k and ("out_of_room", "off") in k and ("in_keep_clear", "road") in k
    st = scene(tmp_path, [], site={**SITE, "buildings": [{"id": "x", "rect": [6, 4, 10, 9], "height": 4}]})
    assert any(i["kind"] == "site_in_room" for i in st["issues"])


def test_set_room_site(tmp_path):
    scene(tmp_path, [], site=None)
    d = tmp_path / "t"
    assert service.load(d).spec.room.site is None
    service.set_room(d, site={"around": 5, "lighting": "dusk"})
    room = service.load(d).spec.room
    assert room.site.around == 5 and room.site.lighting == "dusk"
    hist = (d / "preview.json").read_text()
    assert "敷地" in hist
    service.set_room(d, site=False)
    assert service.load(d).spec.room.site is None


def test_preview_and_license(tmp_path):
    from agentic_sim_world_gen import license
    from agentic_sim_world_gen.preview.layout2d import render_svg

    scene(tmp_path, [{"id": "vending", "query": "vending machine", "zone": "outside",
                      "layout": {"against_wall": "outside:south"}}])
    s = service.load(tmp_path / "t")
    svg = render_svg(s)
    assert "外 asphalt" in svg and "next" in svg
    # the page's 「部屋だけ」 view: the room plus 1 m, in the SVG's own coordinates
    import re
    ppm, x0, y1, m = (float(re.search(f'data-{k}="([^"]+)"', svg).group(1)) for k in ("ppm", "x0", "y1", "margin"))
    bx, by, bw, bh = map(float, re.search('data-room="([^"]+)"', svg).group(1).split())
    sx0, sy0, sx1, sy1 = RoomGeometry(s.spec.room).slab
    assert bw == pytest.approx((sx1 - sx0 + 2) * ppm, abs=2) and bh == pytest.approx((sy1 - sy0 + 2) * ppm, abs=2)
    assert bx == pytest.approx(m + (sx0 - 1 - x0) * ppm, abs=1) and by == pytest.approx(m + (y1 - sy1 - 1) * ppm, abs=1)
    from agentic_sim_world_gen.preview import page
    html = (tmp_path / "t" / "preview.html").read_text()
    assert "id='room-only'" in html and "roomOnly" in page._JS
    assert 'data-room=' not in render_svg(service.load(tmp_path / "t").model_copy(
        update={"spec": s.spec.model_copy(update={"room": s.spec.room.model_copy(update={"site": None})})}))
    r = license.report(s)
    assert r["environment"] and "noon_grass" in r["environment"][0]["uri"]


def test_compose(tmp_path):
    pytest.importorskip("pxr")
    from pxr import UsdLux, UsdPhysics

    from agentic_sim_world_gen.usd.compose import compose

    scene(tmp_path, [{"id": "vending", "query": "vending machine", "zone": "outside",
                      "layout": {"against_wall": "outside:south"}}], ceiling=True)
    stage = compose(service.load(tmp_path / "t"), tmp_path / "scene.usda")
    room = stage.GetPrimAtPath("/World/Room")
    assert list(room.GetCustomDataByKey("agentic_sim_world_gen:site")) == pytest.approx([-6, -10, 14, 12])
    for path in ("/World/Room/Ground", "/World/Room/Areas/walk", "/World/Room/Buildings/next"):
        prim = stage.GetPrimAtPath(path)
        assert prim and prim.HasAPI(UsdPhysics.CollisionAPI), path
    assert stage.GetPrimAtPath("/World/Room/Ceiling") and stage.GetPrimAtPath("/World/Room/Floor")
    ground = stage.GetPrimAtPath("/World/Room/Ground")
    assert max(v[2] for v in ground.GetAttribute("points").Get()) < 0  # under the room floor
    dome = UsdLux.DomeLight(stage.GetPrimAtPath("/World/Lights/Dome"))
    assert "noon_grass" in dome.GetTextureFileAttr().Get().path  # the sky of the site's lighting (sun)
    from agentic_sim_world_gen.scene.looks import LIGHTING
    from agentic_sim_world_gen.usd.look import SITE_LIGHT

    sun = UsdLux.DistantLight(stage.GetPrimAtPath("/World/Lights/Sun"))
    assert sun.GetIntensityAttr().Get() == pytest.approx(LIGHTING["sun"]["sun"] * SITE_LIGHT)  # not burned out
    assert dome.GetIntensityAttr().Get() == pytest.approx(LIGHTING["sun"]["dome"] * SITE_LIGHT)
    fixtures = [p for p in stage.GetPrimAtPath("/World/Lights").GetChildren() if p.GetName() not in ("Dome", "Sun")]
    assert fixtures  # the room keeps its office lights
