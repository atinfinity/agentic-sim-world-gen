"""Free widths on the floor straight from a photo's depth map (no pixels read off the photo). The depth map
is rendered from the scene's own boxes, so the answer is known."""

import json

import numpy as np
import pytest

from agentic_sim_world_gen import depth, service
from agentic_sim_world_gen.photo_measure import View, depth_floor
from agentic_sim_world_gen.scene.spec import PhotoCamera
from tests.test_layout import rec

CATALOG = {"shelf": [rec("shelf", "shelf", 0.9, 0.45, 1.8)], "table": [rec("table", "table", 1.2, 0.8, 0.75)]}
CAM = {"position": [3.0, 0.6, 1.5], "look_at": [3.0, 8.0, 0.2], "hfov_deg": 70}
SIZE = (1600, 1200)
ROOM = (6.0, 10.0, 3.0)
SHELVES = [((0.275, 2.3, 0.0), (0.725, 7.7, 1.8)), ((5.275, 2.3, 0.0), (5.725, 7.7, 1.8))]


@pytest.fixture(autouse=True)
def fake():
    service.set_provider(type("P", (), {"name": "f", "search": lambda self, q, limit=10, size=None: CATALOG.get(q, [])})())
    yield
    service.set_provider(None)


def render(cam: dict, boxes, scale: float = 1.0, noise: float = 0.0, w: int = 320, h: int = 240) -> np.ndarray:
    """z-depth of the room (seen from inside) and the boxes, from the pinhole View the tools use."""
    view = View(PhotoCamera(**cam), SIZE)
    vs, us = np.mgrid[0:h, 0:w]
    x = ((us + 0.5) / w * SIZE[0] - SIZE[0] / 2) / view.f
    y = ((vs + 0.5) / h * SIZE[1] - SIZE[1] / 2) / view.f
    ray = np.stack([view.fw[i] + x * view.rt[i] - y * view.up[i] for i in range(3)], axis=-1)  # ray . fw = 1
    o = np.array(view.pos)
    with np.errstate(divide="ignore", invalid="ignore"):
        t1, t2 = (np.zeros(3) - o) / ray, (np.array(ROOM) - o) / ray
        best = np.max(np.maximum(t1, t2), axis=-1) * 0 + np.min(np.maximum(t1, t2), axis=-1)  # leaving the room
        for lo, hi in boxes:
            a, b = (np.array(lo) - o) / ray, (np.array(hi) - o) / ray
            near, far = np.max(np.minimum(a, b), axis=-1), np.min(np.maximum(a, b), axis=-1)
            hit = (near < far) & (near > 0)
            best = np.where(hit & (near < best), near, best)
    d = best * scale
    if noise:
        d = d * (1 + np.random.default_rng(1).normal(0, noise, d.shape))
    return d.astype("float32")


def scene_with_map(tmp_path, objects, boxes, **kw):
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"")
    np.savez_compressed(depth.map_path(photo), depth=render(CAM, boxes, **kw).astype(np.float16),
                        meta=np.array(json.dumps({"model": "rendered", "hfov_deg": 70, "size": list(SIZE)})))
    return service.create_scene({
        "name": "aisle", "room": {"width": ROOM[0], "depth": ROOM[1], "height": ROOM[2]},
        "inputs": [{"id": "photo", "kind": "photo", "path": str(photo), "camera": CAM, "size_px": list(SIZE)}],
        "objects": objects}, tmp_path / "aisle")["scene_dir"]


AISLE = [{"id": "west", "query": "shelf", "position": [0.5, 5.0], "yaw_deg": 90, "array": {"count": 6}},
         {"id": "east", "query": "shelf", "position": [5.5, 5.0], "yaw_deg": 90, "array": {"count": 6}}]


def rows(m):
    return {r["ahead_m"]: r for r in m["depth_floor"]["across"]}


def test_width_between_the_shelves(tmp_path):
    m = service.measure_photo(scene_with_map(tmp_path, AISLE, SHELVES))
    df = m["depth_floor"]
    assert df["ok"] and df["camera_height_m"] == pytest.approx(1.5, abs=0.03)
    assert df["pitch_deg"] == pytest.approx(m["camera"]["pitch_deg"], abs=0.5)
    r = rows(m)
    for t in (3.5, 5.0):  # between the shelf fronts: 5.0 - 0.45 m, as the scene's own row says
        assert r[t]["width"] == pytest.approx(4.55, abs=0.12) and r[t]["scene_width"] == pytest.approx(4.55, abs=0.01)
        assert r[t]["range_m"][0] < 4.55 < r[t]["range_m"][1]
    assert not any(n.startswith("depth map:") and "too" in n for n in m["notes"])


def test_scale_is_the_maps_own(tmp_path):
    """A map 15 % too far: the depth-only width is 15 % wide, the camera height tells and the rescaled one is right."""
    m = service.measure_photo(scene_with_map(tmp_path, AISLE, SHELVES, scale=1.15, noise=0.01))
    df = m["depth_floor"]
    assert df["camera_height_m"] == pytest.approx(1.5 * 1.15, abs=0.05)
    r = rows(m)[3.5]
    assert r["width"] == pytest.approx(4.55 * 1.15, abs=0.2) and r["width_at_set_height"] == pytest.approx(4.55, abs=0.15)


def test_scene_wider_than_the_photo_is_named(tmp_path):
    """The photo's shelves stand 1 m further in on each side than the scene's."""
    narrow = [((1.275, 2.3, 0.0), (1.725, 7.7, 1.8)), ((4.275, 2.3, 0.0), (4.725, 7.7, 1.8))]
    m = service.measure_photo(scene_with_map(tmp_path, AISLE, narrow))
    assert rows(m)[3.5]["width"] == pytest.approx(2.55, abs=0.12)
    assert any("depth map: 3.5 m ahead" in n and "too wide" in n for n in m["notes"])


def test_open_side_and_blocked_line(tmp_path):
    # one shelf row only: the right side runs to the wall at 3 m; a table across the center line 5 m ahead
    table = ((2.4, 5.2, 0.0), (3.6, 6.0, 0.75))
    m = service.measure_photo(scene_with_map(tmp_path, AISLE[:1], [SHELVES[0], table]))
    r = rows(m)
    assert r[3.5]["width"] is None and r[3.5]["open"] == ["right"]  # the east wall is outside the image there
    assert r[7.0]["width"] == pytest.approx(2.275 + 3.0, abs=0.15)  # shelf front to the east wall
    assert r[5.0]["width"] is None and "on" in r[5.0]
    # 2 m ahead the image is narrower than the aisle: a lower bound, not a width (1 m ahead the floor is below the image)
    assert r[2.0]["width"] is None and set(r[2.0]["open"]) == {"left", "right"} and r[2.0]["at_least"] < 3.5
    assert 1.0 not in r


def test_no_map_no_key(tmp_path):
    d = service.create_scene({"name": "a", "room": {"width": 6, "depth": 10},
                              "inputs": [{"id": "photo", "kind": "photo", "camera": CAM, "size_px": list(SIZE)}],
                              "objects": []}, tmp_path / "a")["scene_dir"]
    assert "depth_floor" not in service.measure_photo(d)


def test_floor_not_seen(tmp_path):
    up = dict(CAM, look_at=[3.0, 8.0, 6.0])  # looking at the ceiling
    photo = tmp_path / "p.jpg"
    photo.write_bytes(b"")
    np.savez_compressed(depth.map_path(photo), depth=render(up, []).astype(np.float16), meta=np.array(json.dumps({"hfov_deg": 70})))
    inp = type("I", (), {"path": str(photo)})()
    got = depth_floor(inp, View(PhotoCamera(**up), SIZE))
    assert got["ok"] is False and "why" in got
