"""Furniture poses refined in the photos, no network."""
import json
import math

from tests.test_photo_place import _scene, fake  # noqa: F401  (the fixture)
from tests.test_photo_placement import DESK
from agentic_sim_world_gen import service
from agentic_sim_world_gen.photo_refine import LOOSE, camera, corners, project, refine

SIZE = (0.6, 1.2, 0.74)
CAMS = {"a": camera({"position": [0.5, 0.5, 1.5], "yaw_deg": 35, "pitch_deg": 20, "hfov_deg": 70}, (1280, 960)),
        "b": camera({"position": [4.0, 0.4, 1.4], "yaw_deg": 140, "pitch_deg": 18, "hfov_deg": 70}, (1280, 960))}


def seen(cams, pose, size, feet_of=(), clip=True):
    """(feet, outlines) of a box as the cameras see it."""
    cs = corners(pose[0], pose[1], pose[2], size)
    feet, outlines = [], []
    for cid, c in cams.items():
        ps = [project(c, X) for X, _, _ in cs]
        W, H = c["size"]
        if any(q is None for q in ps) or sum(0 <= q[0] <= W and 0 <= q[1] <= H for q in ps) < 4:
            continue  # not in this photo
        box = (min(p[0] for p in ps), min(p[1] for p in ps), max(p[0] for p in ps), max(p[1] for p in ps))
        outlines.append((cid, (max(0.0, box[0]), max(0.0, box[1]), min(W, box[2]), min(H, box[3])) if clip else box))
        if cid in feet_of:
            feet += [(cid, p, side) for (X, side, top), p in zip(cs, ps) if not top]
    return feet, outlines


def test_outlines_move_the_box_back():
    true = (2.4, 2.2, 30.0)
    _, outlines = seen(CAMS, true, SIZE)
    r = refine(SIZE, (2.8, 1.9, 30.0), [], outlines, CAMS)
    assert math.dist(r["center"], true[:2]) < 0.02 and r["rms_px"] < 1 < r["rms_px_was"] and not r["turned"]
    assert r["outlines"] == 2 and r["photos"] == ["a", "b"]


def test_feet_and_outlines_turn_it_when_that_fits_clearly_better():
    true = (2.4, 2.2, 30.0)
    feet, outlines = seen(CAMS, true, SIZE, feet_of=("a",))
    r = refine(SIZE, (2.7, 2.0, 10.0), feet, outlines, CAMS)
    assert math.dist(r["center"], true[:2]) < 0.03 and r["turned"] and abs(r["facing_deg"] - 30) < 2
    # an asset 15 % bigger than the real thing: still near, and the fit says how loose
    big = tuple(s * 1.15 for s in SIZE)
    r = refine(big, (2.7, 2.0, 30.0), feet, outlines, CAMS)
    assert math.dist(r["center"], true[:2]) < 0.12 and r["rms_px"] > 3


def test_a_side_at_the_image_border_is_not_compared():
    cams = {"a": CAMS["a"]}
    true = (1.6, 1.0, 0.0)  # near the camera: runs out of the bottom of the frame
    _, outlines = seen(cams, true, SIZE)
    assert outlines[0][1][3] == 960
    r = refine(SIZE, (1.9, 1.3, 0.0), [], outlines, cams)
    assert math.dist(r["center"], true[:2]) < 0.05 and r["rms_px"] < 2


def test_nothing_to_go_on():
    assert "error" in refine(SIZE, (1, 1, 0), [], [], CAMS)
    assert "error" in refine(SIZE, (1, 1, 0), [("a", (10, 10), None)], [], CAMS)
    assert "error" in refine(SIZE, (1, 1, 0), [], [("zz", (0, 0, 5, 5))], CAMS)
    # from far away nothing pulls it (behind the cameras): it stays, and the fit says it is loose
    _, outlines = seen(CAMS, (2.4, 2.2, 0.0), SIZE)
    r = refine(SIZE, (40.0, 40.0, 0.0), [], outlines, CAMS)
    assert "error" in r or r["rms_sigma"] > LOOSE


def _desk_outlines(d, at=DESK, noise=0.0):
    j = json.loads((d / "photo_fit.json").read_text())
    views = {k: camera(v, tuple(j["request"]["photos"][k]["size"])) for k, v in j["result"]["cameras"].items()}
    _, outlines = seen(views, (at[0], at[1], -90.0), (0.6, 1.2, 0.74))  # the fake desk: 1.2 wide, front -y
    return [{"object": "desk", "photo": c, "box": [x + noise for x in b]} for c, b in outlines]


def test_place_from_photos_with_outlines(tmp_path):
    d = _scene(tmp_path)
    outs = _desk_outlines(d)
    assert all("px" not in p for p in service.place_from_photos(d, apply=False)["placed"])  # feet alone: as before
    r = service.place_from_photos(d, outlines=outs + [{"object": "desk", "photo": "nope", "box": [0, 0, 9, 9]}])
    p = r["placed"][0]
    # the fake desk (1.2 x 0.6) is bigger than its feet (1.1 x 0.5): a few pixels off, not loose
    assert p["id"] == "desk" and p["outlines"] == len(outs) and p["px"] < 12 and "loose" not in p
    assert math.dist(p["position"], DESK) < 0.05 and "left" not in r
    assert r["outlines_skipped"][0]["photo"] == "nope"
    kept = json.loads((d / "photo_fit.json").read_text())["outlines"]
    assert len(kept) == len(outs)
    a = [x for x in service.load(d).spec.assumptions if x.object == "desk" and x.item == "位置"][0]
    assert "写真上" in a.value and "輪郭" in a.value
    again = service.place_from_photos(d)["placed"][0]  # the outlines are remembered
    assert again["outlines"] == len(outs) and math.dist(again["position"], p["position"]) < 0.02
    # outlines of something 0.6 m from the feet: the box cannot fit both, and the desk is left to look at
    r = service.place_from_photos(d, outlines=_desk_outlines(d, at=(2.7, 0.7)), apply=False)
    assert r["placed"][0]["loose"] and r["left"] == ["desk"] and any("px off" in w for w in r["warnings"])
    assert len(json.loads((d / "photo_fit.json").read_text())["outlines"]) == len(outs)  # a dry run keeps nothing


def test_outline_alone_refines_where_it_stands(tmp_path):
    d = _scene(tmp_path)
    j = json.loads((d / "photo_fit.json").read_text())
    j["request"]["locate"] = []
    j["result"]["located"] = []
    (d / "photo_fit.json").write_text(json.dumps(j))
    o = service.load(d).obj("desk")
    start = (o.position[0], o.position[1])
    target = (start[0] + 0.3, start[1] + 0.25)
    views = {k: camera(v, tuple(j["request"]["photos"][k]["size"])) for k, v in j["result"]["cameras"].items()}
    front = service.load(d).obj("desk")
    from agentic_sim_world_gen.layout.frame import front_local_deg, norm_deg
    pl = [p for p in service.load(d).placements if p.object_id == "desk"][0]
    facing = norm_deg(front.yaw_deg + front_local_deg(pl.asset, front))
    _, outlines = seen(views, (target[0], target[1], facing), (0.6, 1.2, 0.74))
    r = service.place_from_photos(d, outlines=[{"object": "desk", "photo": c, "box": list(b)} for c, b in outlines])
    p = r["placed"][0]
    assert math.dist(p["position"], target) < 0.05 and p["facing_from"] == "kept" and p["points"] == 0
