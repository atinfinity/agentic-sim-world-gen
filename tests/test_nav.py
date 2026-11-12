"""Navigation map and robot reach (no network, no Isaac Sim)."""

import json

import pytest
import yaml

from agentic_sim_world_gen import nav, service
from tests.test_usability import CATALOG, DOOR


@pytest.fixture(autouse=True)
def fake():
    service.set_provider(type("P", (), {"name": "f", "search": lambda self, q, limit=10, size=None: CATALOG.get(q, [])})())
    yield
    service.set_provider(None)


def make(tmp_path, objects, width=4.0, depth=4.0, **room):
    room = {"width": width, "depth": depth, "height": 2.6, **room}
    service.create_scene({"name": "t", "room": room, "objects": objects}, tmp_path / "t")
    return tmp_path / "t"


def test_empty_room_map_files(tmp_path):
    d = make(tmp_path, [], openings=DOOR)
    r = service.nav_map(d)
    assert r["ok"] and r["from"] == "doors"
    assert r["reachable"] > 0.97 and abs(r["free_m2"] - 16.0) < 0.5
    pgm = (d / "nav" / "map.pgm").read_bytes()
    assert pgm.startswith(b"P5")
    y = yaml.safe_load((d / "nav" / "map.yaml").read_text())
    assert y["image"] == "map.pgm" and y["resolution"] == 0.05 and y["mode"] == "trinary"
    # origin = lower-left of the grid, a little outside the room (walls + padding)
    assert y["origin"][0] < 0 and y["origin"][1] < 0
    js = json.loads((d / "nav" / "nav.json").read_text())
    assert js["source"] == "bbox" and js["files"]["yaml"] == "map.yaml"
    assert service.status(d)["nav"]["bbox"]["reachable"] == r["reachable"]


def test_pgm_rows_top_is_north(tmp_path):
    d = make(tmp_path, [{"id": "shelf", "query": "shelf", "layout": {"against_wall": "north"}}], openings=DOOR)
    service.nav_map(d)
    g, _ = nav.build(service.load(d), nav.Robot(), 0.05)
    data = (d / "nav" / "map.pgm").read_bytes()
    hdr, body = data.split(b"255\n", 1)
    assert len(body) == g.nx * g.ny
    top = body[: g.nx * (g.ny // 2)]
    bottom = body[g.nx * (g.ny // 2):]
    # the shelf on the north wall is occupied (0) in the upper half of the image
    assert top.count(0) > bottom.count(0)


def test_shelf_blocks_and_low_object_does_not(tmp_path):
    d = make(tmp_path, [{"id": "shelf", "query": "shelf", "layout": {"center": True}}], openings=DOOR)
    full = service.nav_map(d)["free_m2"]
    low = service.nav_map(d, robot_height=2.0, min_height=1.7)  # the shelf (1.6 m) is below the band
    assert low["free_m2"] > full + 0.8


def test_blocked_corner_is_a_pocket(tmp_path):
    # a wide shelf across the room leaves the north part closed off
    d = make(tmp_path, [{"id": "wshelf", "query": "wide shelf", "position": (2.0, 2.5)}], openings=DOOR)
    r = service.nav_map(d)
    assert r["pockets"] and r["pockets"][0]["at"][1] > 2.5
    assert r["reachable"] < 0.8


def test_small_robot_reaches_more(tmp_path):
    objs = [{"id": "desk", "query": "desk", "layout": {"against_wall": "north"}},
            {"id": "shelf", "query": "shelf", "position": (2.0, 1.6)}]
    d = make(tmp_path, objs, openings=DOOR)
    big = service.nav_map(d, robot_radius=0.45)["reachable_m2"]
    small = service.nav_map(d, robot_radius=0.15)["reachable_m2"]
    assert small > big


def test_start_and_no_doors(tmp_path):
    d = make(tmp_path, [])
    r = service.nav_map(d)
    assert r["from"] == "largest open area" and r["reachable"] > 0.97
    r = service.nav_map(d, start=(2.0, 2.0))
    assert r["from"] == "start" and r["starts"][0]["at"]
    r = service.nav_map(d, start=(-5.0, -5.0))
    assert r["reachable"] == 0 and r["warnings"]


def test_bad_heights(tmp_path):
    d = make(tmp_path, [])
    assert not service.nav_map(d, robot_height=0.1, min_height=0.2)["ok"]


def test_isaac_needs_usd(tmp_path):
    d = make(tmp_path, [], openings=DOOR)
    r = service.nav_map(d, isaac=True)
    assert not r["ok"] and "compose_scene" in r["error"] and (d / "nav" / "map.pgm").exists()


def test_with_occupancy_maps_onto_grid(tmp_path):
    d = make(tmp_path, [], openings=DOOR)
    s = service.load(d)
    g, _ = nav.build(s, nav.Robot(), 0.1)
    x0, y0, x1, y1 = g.bounds()
    w, h = g.nx, g.ny
    # Isaac sees the walls and a post in the middle of the room that the bbox map does not have
    occ = [j * w + i for j in range(h) for i in range(w)
           if g.cells[j][i] == nav.OCCUPIED
           or (abs(g.center(i, j)[0] - 2.0) < 0.3 and abs(g.center(i, j)[1] - 2.0) < 0.3)]
    gi = nav.with_occupancy(g, {"min": [x0, y0, 0.05], "res": 0.1, "dims": [w, h, 1], "occupied": occ})
    assert gi.source == "isaac" and gi.owner
    r0, ri = nav.reach(g, s), nav.reach(gi, s)
    assert ri["free_m2"] < r0["free_m2"] - 0.2
    assert gi.count(nav.UNKNOWN) == g.count(nav.UNKNOWN)  # outside the room stays unknown
    # a cell the Isaac map leaves free becomes free even if the bbox map had it occupied
    gi = nav.with_occupancy(g, {"min": [x0, y0, 0.05], "res": 0.1, "dims": [w, h, 1], "occupied": []})
    assert gi.count(nav.OCCUPIED) == 0


def test_stale_after_edit(tmp_path):
    d = make(tmp_path, [{"id": "shelf", "query": "shelf", "layout": {"against_wall": "north"}}], openings=DOOR)
    service.nav_map(d)
    assert "stale" not in service.status(d)["nav"]["bbox"]
    service.move_object(d, "shelf", position=(1.0, 1.0))
    assert "stale" in service.status(d)["nav"]["bbox"]


def test_outdoor_curbs(tmp_path):
    room = {"outdoor": True, "areas": [{"id": "walk", "kind": "sidewalk", "rect": [0, 0, 10, 2]},
                                       {"id": "road", "kind": "road", "rect": [0, 2, 10, 8]}]}
    d = make(tmp_path, [], width=10.0, depth=8.0, **room)
    r = service.nav_map(d)
    assert r["ok"] and r["reachable"] < 0.9  # the curb (0.15 m) splits the sidewalk from the road
    hi = service.nav_map(d, step=0.2)
    assert hi["reachable"] > 0.97
