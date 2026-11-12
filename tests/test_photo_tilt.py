"""The photo's tilt from its own lines, and widths with an error range: offline."""

import math

import pytest

from agentic_sim_world_gen import photo_vanish, service
from agentic_sim_world_gen.photo_measure import HEIGHT_ERR, TILT_ERR, TILT_ERR_UNREAD, View
from agentic_sim_world_gen.scene.spec import PhotoCamera
from tests.test_layout import rec

np = pytest.importorskip("numpy")
SIZE = (1600, 1200)
CATALOG = {"shelf": [rec("shelf", "shelf", 0.9, 0.45, 1.8)]}


@pytest.fixture(autouse=True)
def fake():
    service.set_provider(type("P", (), {"name": "f", "search": lambda self, q, limit=10, size=None: CATALOG.get(q, [])})())
    yield
    service.set_provider(None)


def _edges(view: View):
    """Edges of an aisle between two shelf rows as the view sees them: uprights, boards along and across."""
    segs = []

    def add(a, b):
        p, q = view.project(a), view.project(b)
        if p and q and all(-200 < c[0] < SIZE[0] + 200 and -200 < c[1] < SIZE[1] + 200 for c in (p, q)):
            segs.append((p[0], p[1], q[0], q[1]))

    for x in (1.0, 5.0):
        for k in range(12):
            y = 2.0 + 0.9 * k
            add((x, y, 0.0), (x, y, 1.8))  # uprights
        for z in (0.0, 0.6, 1.2, 1.8):
            for k in range(11):
                add((x, 2.0 + 0.9 * k, z), (x, 2.9 + 0.9 * k, z))  # boards along the aisle
    for y in (6.0, 8.0, 11.0):
        for z in (0.0, 1.0, 2.0):
            add((1.0, y, z), (3.0, y, z))  # edges across (floor joints, the far wall)
            add((3.0, y, z), (5.0, y, z))
    return segs


@pytest.mark.parametrize("pitch,roll,hfov", [(-9.0, 1.5, 70.0), (6.0, -2.0, 60.0), (-15.0, 0.0, 78.0)])
def test_tilt_from_segments(pitch, roll, hfov):
    cam = PhotoCamera(position=(3.0, 0.5, 1.5), look_at=(3.2, 8.0, 1.5), hfov_deg=hfov)
    r = photo_vanish.estimate_from(_edges(View(cam, SIZE, pitch_deg=pitch, roll_deg=roll)), SIZE, hfov)
    assert r["ok"], r
    assert r["pitch_deg"] == pytest.approx(pitch, abs=0.2) and r["roll_deg"] == pytest.approx(roll, abs=0.2)
    # two level directions at right angles and a tilted camera fix the field of view
    assert r["hfov_deg"] == pytest.approx(hfov, abs=2)
    # the horizon: where the view's level forward direction lands
    v = View(cam, SIZE, pitch_deg=pitch, roll_deg=roll)
    hz = v.project((v.pos[0] + 1e4 * v.fw2[0], v.pos[1] + 1e4 * v.fw2[1], v.pos[2]))
    assert r["horizon_v_px"] == pytest.approx(hz[1] - (SIZE[0] / 2 - hz[0]) * math.tan(math.radians(roll)), abs=6)


def test_no_vertical_edges():
    r = photo_vanish.estimate_from([(100.0, 100.0 + 5 * i, 900.0, 110.0 + 5 * i) for i in range(30)], SIZE, 65)
    assert not r["ok"] and "near-vertical" in r["why"]


def _scene(tmp_path, cam, path=None):
    return service.create_scene({
        "name": "aisle", "room": {"width": 6, "depth": 13, "height": 3.0},
        "inputs": [{"id": "photo", "kind": "photo", "camera": cam, "size_px": list(SIZE), "path": path}],
        "objects": [
            {"id": "west", "query": "shelf", "position": [0.775, 7.0], "yaw_deg": 90, "array": {"count": 11}},
            {"id": "east", "query": "shelf", "position": [5.225, 7.0], "yaw_deg": 90, "array": {"count": 11}},
        ]}, tmp_path / "aisle")["scene_dir"]


def test_range_without_the_photo(tmp_path):
    """No file to read lines from: the camera as set, with the wider tilt error."""
    cam = {"position": [3.0, 0.5, 1.5], "look_at": [3.0, 8.0, 0.6], "hfov_deg": 70}
    d = _scene(tmp_path, cam)
    v = View(PhotoCamera(**cam), SIZE)
    a, b = v.project((1.0, 3.5, 0.0)), v.project((5.0, 3.5, 0.0))
    m = service.measure_photo(d, floor_pairs=[[a[0], a[1], b[0], b[1]]])
    p = m["floor_pairs"][0]
    assert "photo_lines" not in m and p["tilt_from"] == "camera as set"
    assert p["photo_m"] == pytest.approx(4.0, abs=0.02)
    lo, hi = p["range_m"]
    assert lo < 4.0 < hi
    # the same pixels 5 deg further down and 10 % lower are nearer together
    w = View(PhotoCamera(**cam), SIZE, pitch_deg=math.degrees(v.pitch) - TILT_ERR_UNREAD, height=1.5 * (1 - HEIGHT_ERR))
    assert lo == pytest.approx(math.dist(w.floor_point(a[0], a[1]), w.floor_point(b[0], b[1])), abs=0.02)
    assert m["camera"]["pitch_deg"] == pytest.approx(math.degrees(math.atan2(-0.9, 7.5)), abs=0.1)


def test_photo_lines_correct_a_guessed_tilt(tmp_path):
    """A drawn aisle taken 8 deg down; the camera was set level. floor_pairs go through the photo's tilt."""
    cv2 = pytest.importorskip("cv2")
    true = View(PhotoCamera(position=(3.0, 0.5, 1.5), look_at=(3.0, 8.0, 1.5), hfov_deg=70), SIZE, pitch_deg=-8.0)
    img = np.full((SIZE[1], SIZE[0], 3), 235, np.uint8)
    for x1, y1, x2, y2 in _edges(true):
        cv2.line(img, (round(x1), round(y1)), (round(x2), round(y2)), (30, 30, 30), 3, cv2.LINE_AA)
    path = tmp_path / "aisle.png"
    cv2.imwrite(str(path), img)
    cam = {"position": [3.0, 0.5, 1.5], "look_at": [3.0, 8.0, 1.5], "hfov_deg": 70}  # level: 8 deg off
    d = _scene(tmp_path, cam, str(path))
    a, b = true.project((1.0, 4.5, 0.0)), true.project((5.0, 4.5, 0.0))
    m = service.measure_photo(d, floor_pairs=[[a[0], a[1], b[0], b[1]]])
    pl, p = m["photo_lines"], m["floor_pairs"][0]
    assert pl["ok"] and pl["pitch_deg"] == pytest.approx(-8.0, abs=0.5)
    assert pl["camera_off_deg"] == pytest.approx(8.0, abs=0.5)
    assert pl["look_at_z"] == pytest.approx(1.5 - 7.5 * math.tan(math.radians(8)), abs=0.08)
    assert p["tilt_from"] == "photo lines" and p["photo_m"] == pytest.approx(4.0, abs=0.15)
    assert p["range_m"][0] < 4.0 < p["range_m"][1]
    # +-2 deg is a tighter range than the unread +-5
    v = View(PhotoCamera(**cam), SIZE, pitch_deg=pl["pitch_deg"] + TILT_ERR, height=1.5 * (1 + HEIGHT_ERR))
    assert p["range_m"][1] == pytest.approx(math.dist(v.floor_point(a[0], a[1]), v.floor_point(b[0], b[1])), abs=0.02)
    assert any("set look_at's height" in n for n in m["notes"])
    # through the level camera as set the same pixels land further away: 4 m reads as more than 6
    lvl = View(PhotoCamera(**cam), SIZE)
    assert math.dist(lvl.floor_point(a[0], a[1]), lvl.floor_point(b[0], b[1])) > 6.0
