"""Usability warnings: aisles, reachability, chairs, TV, windows, register sight line (no network)."""

import pytest

from agentic_sim_world_gen import service
from agentic_sim_world_gen.layout.usability import aisle_width
from tests.test_layout import rec

CATALOG = {
    "desk": [rec("desk", "desk", 1.4, 0.7, 0.74)],
    "office chair": [rec("chair", "office chair", 0.55, 0.55, 0.85)],
    "tv": [rec("tv", "television", 1.2, 0.3, 0.7)],
    "sofa": [rec("sofa", "couch", 1.8, 0.9, 0.8)],
    "shelf": [rec("shelf", "shelf", 1.8, 0.5, 1.6)],
    "wide shelf": [rec("wshelf", "shelf", 3.9, 0.5, 1.6)],
    "register": [rec("register", "register", 1.2, 0.6, 1.0)],
    "door": [rec("door", "door", 1.0, 0.2, 2.05)],
}


@pytest.fixture(autouse=True)
def fake():
    service.set_provider(type("P", (), {"name": "f", "search": lambda self, q, limit=10, size=None: CATALOG.get(q, [])})())
    yield
    service.set_provider(None)


def make(tmp_path, objects, width=4.0, depth=4.0, **room):
    room = {"width": width, "depth": depth, "height": 2.6, **room}
    return service.create_scene({"name": "t", "room": room, "objects": objects}, tmp_path / "t")


def kinds(st):
    return sorted(i["kind"] for i in st["usability"])


DOOR = [{"id": "door", "kind": "door", "wall": "south", "offset": 2.0, "width": 0.9, "height": 2.0}]


def test_aisle_width_by_place():
    assert aisle_width("home") == 0.75
    assert aisle_width("convenience_store") == 1.0
    assert aisle_width("supermarket") == 1.2
    assert aisle_width(None) == 0.9


def test_study_is_clean(tmp_path):
    st = make(tmp_path, [
        {"id": "desk", "query": "desk", "layout": {"against_wall": "north"}},
        {"id": "chair", "query": "office chair", "layout": {"in_front_of": "desk"}},
        {"id": "shelf", "query": "shelf", "layout": {"against_wall": "west"}},
    ], openings=DOOR)
    assert st["issues"] == [] and st["usability"] == []


def test_chair_facing_away(tmp_path):
    # desk on the north wall faces south; a chair facing south too turns its back to it
    st = make(tmp_path, [
        {"id": "desk", "query": "desk", "layout": {"against_wall": "north"}},
        {"id": "chair", "query": "office chair", "position": (2.0, 2.9), "facing_deg": 270},
    ])
    assert kinds(st) == ["chair_facing"]
    assert st["usability"][0]["objects"] == ["chair", "desk"]


def test_chair_far(tmp_path):
    st = make(tmp_path, [
        {"id": "desk", "query": "desk", "layout": {"against_wall": "north"}},
        {"id": "chair", "query": "office chair", "layout": {"in_front_of": "desk", "gap": 0.9}},
    ])
    assert kinds(st) == ["chair_far"]


def test_tv_and_sofa(tmp_path):
    ok = make(tmp_path, [
        {"id": "tv", "query": "tv", "layout": {"against_wall": "north"}},
        {"id": "sofa", "query": "sofa", "layout": {"against_wall": "south"}},
    ])
    assert ok["usability"] == []
    side = make(tmp_path, [
        {"id": "tv", "query": "tv", "layout": {"against_wall": "north"}},
        {"id": "sofa", "query": "sofa", "layout": {"against_wall": "west"}},
    ])
    assert "tv_facing" in kinds(side)


def test_unreachable_behind_a_shelf_wall(tmp_path):
    # a shelf row across the whole room cuts the north side off from the door
    st = make(tmp_path, [
        {"id": "wall_shelf", "query": "wide shelf", "position": (2.0, 2.0), "yaw_deg": 0},
        {"id": "back", "query": "shelf", "layout": {"against_wall": "north"}},
    ], openings=DOOR, place="convenience_store")
    assert st["issues"] == []
    assert ("unreachable", ["back"]) in [(i["kind"], i["objects"]) for i in st["usability"]]


@pytest.mark.no_auto_fix
def test_narrow_aisle_in_a_shop(tmp_path):
    # 0.8 m between two shelf rows: fine at home (0.75 m), too narrow for a shop (1.0 m)
    objects = [
        {"id": "a", "query": "shelf", "position": (1.5, 1.5), "yaw_deg": 180},
        {"id": "b", "query": "shelf", "position": (1.5, 2.8), "yaw_deg": 0},
    ]
    shop = make(tmp_path, objects, openings=DOOR, place="convenience_store")
    home = make(tmp_path, objects, openings=DOOR, place="home")
    assert shop["issues"] == []
    assert "narrow_aisle" in kinds(shop)
    assert "narrow_aisle" not in kinds(home)


def test_outdoor_has_no_usability(tmp_path):
    st = service.create_scene({"name": "t", "room": {"width": 10, "depth": 10, "outdoor": True},
                               "objects": [{"id": "desk", "query": "desk", "position": (5, 5)}]}, tmp_path / "t")
    assert st["usability"] == []


def test_empty_shop_floor_and_wide_aisle(tmp_path):
    """A big shop with two rows of shelves in one corner: the rest of the floor is empty, and the
    rows face each other across 2.4 m (a konbini needs 1 m)."""
    shelves = [
        {"id": "row_a", "query": "shelf", "position": (2.0, 1.5), "facing_deg": 90},
        {"id": "row_b", "query": "shelf", "position": (2.0, 4.4), "facing_deg": 270},
        {"id": "register", "query": "register", "layout": {"against_wall": "east"}},
    ]
    st = make(tmp_path, shelves, width=12.0, depth=9.0, place="convenience_store", openings=DOOR)
    empty = [i for i in st["usability"] if i["kind"] == "empty_floor"]
    assert 1 <= len(empty) <= 3 and all(i["objects"] == [] or i["objects"] == () for i in empty)
    assert "m across around" in empty[0]["detail"]
    wide = [i for i in st["usability"] if i["kind"] == "wide_aisle"]
    assert len(wide) == 1 and sorted(wide[0]["objects"]) == ["row_a", "row_b"]
    assert "2.4 m wide" in wide[0]["detail"]

    # the same rows in a small shop at a 1 m aisle: neither warning
    shelves[1]["position"] = (2.0, 3.0)
    st = make(tmp_path / "s", shelves, width=4.0, depth=4.0, place="convenience_store", openings=DOOR)
    assert not {"empty_floor", "wide_aisle"} & set(kinds(st))
    # not a shop: no such warnings
    st = make(tmp_path / "h", [{"id": "shelf", "query": "shelf", "layout": {"against_wall": "west"}}],
              width=12.0, depth=9.0, place="home", openings=DOOR)
    assert not {"empty_floor", "wide_aisle"} & set(kinds(st))


def detail(st, kind, obj):
    return next(u["detail"] for u in st["usability"] if u["kind"] == kind and obj in u["objects"])


@pytest.mark.no_auto_fix
def test_narrow_aisle_says_how_to_widen(tmp_path):
    """Who to move, which way and how far."""
    objects = [
        {"id": "a", "query": "shelf", "position": (1.5, 1.5), "yaw_deg": 180},
        {"id": "b", "query": "shelf", "position": (1.5, 2.8), "yaw_deg": 0},
    ]
    st = make(tmp_path, objects, openings=DOOR, place="convenience_store")
    assert "only 0.80 m to b in front: move b 0.20 m north (+y) or a 0.20 m south (-y)" in detail(st, "narrow_aisle", "a")
    # facing a wall 0.8 m away: move it back
    wall = make(tmp_path, [{"id": "a", "query": "shelf", "position": (2.0, 2.95), "yaw_deg": 180}],
                openings=DOOR, place="convenience_store")
    assert "to the wall in front: move a 0.20 m south (-y)" in detail(wall, "narrow_aisle", "a")
    # rows of one array facing each other: widen its aisle
    rows = make(tmp_path, [{"id": "r", "query": "shelf", "position": (2.0, 2.0),
                            "array": {"count": 1, "rows": 2, "aisle": 0.8, "alternate": True}}],
                openings=DOOR, place="convenience_store")
    assert "widen the array aisle by 0.20 m (set_array aisle=1.00)" in detail(rows, "narrow_aisle", "r.r0c0")


def test_thing_not_in_the_photo_standing_in_its_view(tmp_path):
    """Once objects name the photo they are seen in, the others in front of its camera are warned about."""
    cam = {"position": [2.0, 0.3, 1.4], "look_at": [2.0, 4.0, 1.0], "hfov_deg": 65}
    photo = {"id": "p1", "kind": "photo", "license": "CC0-1.0", "size_px": [1600, 1200], "camera": cam}
    objects = [
        {"id": "desk", "query": "desk", "position": [2.0, 3.5], "yaw_deg": 0},
        {"id": "shelf", "query": "shelf", "position": [2.0, 1.6], "yaw_deg": 0},
        {"id": "sofa", "query": "sofa", "position": [0.6, 0.6], "yaw_deg": 0},  # beside the camera
    ]
    d = tmp_path / "t"
    st = service.create_scene({"name": "t", "room": {"width": 4.0, "depth": 4.0, "height": 2.6},
                               "objects": objects, "inputs": [photo]}, d)
    assert "in_photo_view" not in kinds(st)  # no object names the photo yet
    service.set_input(d, {"id": "p1"}, seen_in=["desk"])
    hits = [i for i in service.status(d)["usability"] if i["kind"] == "in_photo_view"]
    assert [i["objects"][0] for i in hits] == ["shelf"]
    assert "p1" in hits[0]["detail"]
    service.set_input(d, {"id": "p1"}, seen_in=["desk", "shelf"])
    assert "in_photo_view" not in kinds(service.status(d))
    assert service.load(d).spec.objects[1].seen_in == ["p1"]
