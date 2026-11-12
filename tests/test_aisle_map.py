"""One map for the widths of the walking floor: the widest way from the doors, where it is narrowest, the
same answers as before for narrow_aisle / unreachable, the colours on layout.svg and nav_map's routes. Offline."""

import random
import re

import pytest

from agentic_sim_world_gen import service
from agentic_sim_world_gen.layout import usability
from agentic_sim_world_gen.layout.room import RoomGeometry
from agentic_sim_world_gen.layout.widths import edt, narrowest, ridge, widest
from agentic_sim_world_gen.preview.layout2d import render_svg
from tests.test_plan import l_room
from tests.test_usability import CATALOG, DOOR, make


@pytest.fixture(autouse=True)
def fake():
    service.set_provider(type("P", (), {"name": "f", "search": lambda self, q, limit=10, size=None: CATALOG.get(q, [])})())
    yield
    service.set_provider(None)


def _clear(free):
    pad = [[True] * (len(free[0]) + 2)] + [[True, *(not v for v in row), True] for row in free] + [[True] * (len(free[0]) + 2)]
    return [[x - 0.5 if f else 0.0 for x, f in zip(row[1:-1], fr)] for row, fr in zip(edt(pad)[1:-1], free)]


def _flood(ok, seeds):
    seen, todo = set(), [s for s in seeds if ok[s[1]][s[0]]]
    while todo:
        i, j = todo.pop()
        if (i, j) in seen:
            continue
        seen.add((i, j))
        todo += [(x, y) for x, y in ((i + 1, j), (i - 1, j), (i, j + 1), (i, j - 1))
                 if 0 <= x < len(ok[0]) and 0 <= y < len(ok) and ok[y][x]]
    return seen


def test_widest_is_every_threshold_at_once():
    rnd = random.Random(454)
    for _ in range(5):
        free = [[rnd.random() > 0.25 for _ in range(30)] for _ in range(24)]
        clear = _clear(free)
        seeds = [(i, j) for j in range(24) for i in range(30) if free[j][i] and rnd.random() < 0.02]
        best, _ = widest(clear, seeds)
        for t in (0.5, 1.0, 1.5, 2.5):
            want = _flood([[v >= t for v in row] for row in clear], seeds)
            assert {(i, j) for j in range(24) for i in range(30) if best[j][i] >= t} == want


def test_narrowest_is_the_doorway():
    # two rooms 9 cells wide joined by a doorway 3 cells wide
    free = [[True] * 21 for _ in range(9)]
    for j in range(9):
        free[j][10] = j in (3, 4, 5)
    clear = _clear(free)
    best, prev = widest(clear, [(2, 4)])
    at, low = narrowest(clear, prev, (17, 4))
    assert at == (10, 4) and low == pytest.approx(1.5)  # 3 cells wide: 1.5 cells to each side
    assert best[4][17] == pytest.approx(1.5)
    # a spot by the wall of the far room: the last steps to it do not count
    at, low = narrowest(clear, prev, (19, 0))
    assert at == (10, 4) and low == pytest.approx(1.5)
    # inside the first room nothing is narrower than the seed's own surroundings
    assert narrowest(clear, prev, (5, 4))[1] > 1.5


def test_ridge_is_the_centre_line():
    clear = _clear([[True] * 20 for _ in range(5)])  # a corridor 5 cells wide
    cells = ridge(clear)
    assert {j for _, j in cells if 3 <= _ <= 16} == {2}
    even = ridge(_clear([[True] * 20 for _ in range(4)]))  # an even width: one of the two middle rows
    assert len({j for i, j in even if 3 <= i <= 16}) == 1


@pytest.mark.no_auto_fix
def test_swept_floor_is_what_walk_gave(tmp_path):
    objects = [
        {"id": "a", "query": "shelf", "position": (1.5, 1.5), "yaw_deg": 180},
        {"id": "b", "query": "shelf", "position": (1.5, 2.8), "yaw_deg": 0},
        {"id": "wall_shelf", "query": "wide shelf", "position": (2.0, 3.6), "yaw_deg": 0},
    ]
    for room in ({"openings": DOOR}, {}):  # from the door, and from the largest open area
        s = service.load(make(tmp_path / str(len(room)), objects, place="convenience_store", **room)["scene_dir"])
        m = usability.aisle_map(s)
        assert m.by_doors == bool(room)
        for w in (usability.SQUEEZE, 0.75, m.need, 1.4):
            assert m.swept(w) == m.grid.walk(m.clear, w, m.starts)


@pytest.mark.no_auto_fix
def test_narrow_aisle_says_where(tmp_path):
    st = make(tmp_path, [
        {"id": "a", "query": "shelf", "position": (1.5, 1.5), "yaw_deg": 180},
        {"id": "b", "query": "shelf", "position": (1.5, 2.8), "yaw_deg": 0},
    ], openings=DOOR, place="convenience_store")
    row = next(i for i in st["usability"] if i["kind"] == "narrow_aisle")
    m = re.search(r"about ([0-9.]+) m at \(([0-9.]+), ([0-9.]+)\)", row["detail"])
    # the shelves' fronts are 0.8 m apart, at y = 1.75 and 2.55
    assert m and 0.65 <= float(m.group(1)) <= 0.8 and 1.75 < float(m.group(3)) < 2.55
    s = service.load(st["scene_dir"])
    spots = usability.aisle_map(s).spots()
    assert spots[0]["narrow"] and set(spots[0]["to"]) == {"a", "b"} and len([r for r in spots if r["narrow"]]) == 1
    svg = render_svg(s)
    assert 'class="wg-widths"' in svg and svg.count('class="wg-narrowest"') == 1
    assert f'data-width="{spots[0]["width"]:g}"' in svg
    assert "#f57c00" in svg and "狭い" in svg  # the aisle's centre line is painted as narrow


def test_wide_room_marks_the_narrowest_way_in_green(tmp_path):
    st = make(tmp_path, [{"id": "a", "query": "shelf", "layout": {"against_wall": "north"}}], openings=DOOR,
              place="convenience_store")
    assert "narrow_aisle" not in [i["kind"] for i in st["usability"]]
    svg = render_svg(service.load(st["scene_dir"]))
    assert svg.count('class="wg-narrowest"') == 1 and "#2e7d32" in svg and "#f57c00" not in svg


def test_empty_and_outdoor_have_no_widths(tmp_path):
    st = make(tmp_path / "e", [])
    assert "wg-widths" not in render_svg(service.load(st["scene_dir"]))
    out = service.create_scene({"name": "o", "room": {"width": 20, "depth": 20, "outdoor": True}, "objects": [
        {"id": "a", "query": "shelf", "position": (5, 5)}]}, tmp_path / "o")
    assert "wg-widths" not in render_svg(service.load(out["scene_dir"]))


def test_nav_routes_into_rooms(tmp_path):
    d = service.create_scene({"name": "p", "room": l_room(), "objects": [
        {"id": "desk", "query": "desk", "zone": "ldk", "layout": {"against_wall": "s"}}]}, tmp_path / "p")["scene_dir"]
    r = service.nav_map(d, robot_radius=0.2, start=(6.0, 2.0))
    ways = {x["to"]: x for x in r["routes"]}
    # the bedroom is reached through the 0.8 m door in the wall at y = 4
    bed = ways["bed"]
    assert 0.6 <= bed["width_m"] <= 0.8 and bed["at"][1] == pytest.approx(4.0, abs=0.15)
    assert 1.0 <= bed["at"][0] <= 1.8
    assert ways["ldk"]["width_m"] > bed["width_m"]
    assert r["need_width"] >= 0.4
    assert bed.get("tight", False) == (bed["width_m"] < r["need_width"])
    # a robot too wide for the door: the room is unreachable, and the report says how wide the way is
    big = service.nav_map(d, robot_radius=0.45, start=(6.0, 2.0))
    z = next(x for x in big["unreachable_zones"] if x["id"] == "bed")
    assert z["widest_m"] == bed["width_m"] and z["narrowest_at"] == bed["at"]
