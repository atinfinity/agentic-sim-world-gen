"""Relative camera poses between video frames: OpenCV tracking, and fit_photos using them."""

import json
import math
import shutil
import subprocess

import pytest

from agentic_sim_world_gen import photo_fit as pf
from agentic_sim_world_gen import service, video_pose

from test_photo_fit import CAMS, _request

cv = pytest.mark.skipif(not video_pose.available(), reason="OpenCV not installed")


def _rot(yaw, pitch, roll=0.0):
    """World (reference camera, OpenCV axes) -> camera: turn right by yaw, down by pitch [deg]."""
    import numpy as np

    y, p, r = (math.radians(v) for v in (yaw, pitch, roll))
    Ry = np.array([[math.cos(y), 0, -math.sin(y)], [0, 1, 0], [math.sin(y), 0, math.cos(y)]])
    Rx = np.array([[1, 0, 0], [0, math.cos(p), math.sin(p)], [0, -math.sin(p), math.cos(p)]])
    Rz = np.array([[math.cos(r), math.sin(r), 0], [-math.sin(r), math.cos(r), 0], [0, 0, 1]])
    return Rz @ Rx @ Ry


@cv
def test_pose_from_matches_essential_and_rotation():
    import numpy as np

    rng = np.random.default_rng(1)
    K = video_pose.intrinsics(640, 360, 70)
    X = np.column_stack([rng.uniform(-3, 3, 300), rng.uniform(-1.5, 1.5, 300), rng.uniform(3, 8, 300)])
    R = _rot(8, 3)
    C2 = np.array([0.4, 0.0, 0.1])  # camera 2 center in camera 1's axes

    def px(Rc, C):
        q = (K @ (Rc @ (X - C).T)).T
        return q[:, :2] / q[:, 2:3]

    p1, p2 = px(np.eye(3), np.zeros(3)), px(R, C2)
    d = video_pose.pose_from_matches(p1, p2, K)
    assert d["model"] == "essential"
    assert video_pose._angle(d["R"].T @ R) < 0.2
    t = -R @ C2
    assert math.degrees(math.acos(float(np.dot(d["t"], t / np.linalg.norm(t))))) < 2

    p2 = px(R, np.zeros(3))  # only turned
    d = video_pose.pose_from_matches(p1, p2, K)
    assert d["model"] == "rotation" and d["t"] is None
    assert video_pose._angle(d["R"].T @ R) < 0.2
    assert video_pose.pose_from_matches(p1[:10], p2[:10], K) is None


def _texture(rng, w=2400, h=1400):
    import cv2
    import numpy as np

    im = np.zeros((h, w), np.float32)
    for s in (3, 9, 27):
        n = cv2.resize(rng.random((h // s, w // s)).astype(np.float32), (w, h), interpolation=cv2.INTER_CUBIC)
        im += n * s
    im = cv2.normalize(im, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    for _ in range(500):
        x, y = int(rng.integers(0, w)), int(rng.integers(0, h))
        cv2.rectangle(im, (x, y), (x + int(rng.integers(8, 60)), y + int(rng.integers(8, 60))),
                      int(rng.integers(0, 255)), -1)
    return im


@pytest.fixture(scope="module")
def pan(tmp_path_factory):
    """A camera turning in place (a homography of one big picture), 4 s at 10 fps, and 3 chosen frames as
    extract_frames writes them (inputs/<id>.jpg + .source.json)."""
    if not video_pose.available() or not shutil.which("ffmpeg"):
        pytest.skip("OpenCV / ffmpeg not installed")
    import cv2
    import numpy as np

    d = tmp_path_factory.mktemp("pan")
    rng = np.random.default_rng(3)
    tex = _texture(rng)
    W, H, hfov = 640, 360, 70.0
    K = video_pose.intrinsics(W, H, hfov)
    Kb = np.array([[K[0, 0], 0, tex.shape[1] / 2], [0, K[0, 0], tex.shape[0] / 2], [0, 0, 1]])
    n = 40
    poses = [_rot(45 * i / n - 10, 6 * math.sin(i / 8), 0) for i in range(n)]
    for i, R in enumerate(poses):
        Hm = Kb @ R.T @ np.linalg.inv(K)  # view pixel -> picture pixel
        cv2.imwrite(str(d / f"f{i:03d}.png"),
                    cv2.warpPerspective(tex, Hm, (W, H), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP))
    v = d / "pan.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-framerate", "10", "-i", str(d / "f%03d.png"),
                    "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", str(v)], check=True)
    noisy = d / "noisy"  # the same pan with one sample of pure noise (like the middle of a dissolve)
    noisy.mkdir()
    for i in range(n):
        im = cv2.imread(str(d / f"f{i:03d}.png"), cv2.IMREAD_GRAYSCALE)
        cv2.imwrite(str(noisy / f"f{i:03d}.png"), (rng.random(im.shape) * 255).astype(np.uint8) if i == 9 else im)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-framerate", "10", "-i", str(noisy / "f%03d.png"),
                    "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", str(d / "noisy.mp4")], check=True)
    (d / "scene" / "inputs").mkdir(parents=True)
    keys = []
    for i in (0, 18, 39):
        p = d / "scene" / "inputs" / f"k{i}.jpg"
        cv2.imwrite(str(p), cv2.imread(str(d / f"f{i:03d}.png")))
        p.with_name(f"k{i}.source.json").write_text(json.dumps({"video": str(v), "time_s": i / 10}))
        keys.append({"id": f"k{i}", "time_s": i / 10, "path": str(p)})
    return {"video": v, "noisy": d / "noisy.mp4", "keys": keys, "poses": poses, "hfov": hfov, "scene": d / "scene"}


def _check_turns(rel, poses):
    import numpy as np

    got = {(e["a"], e["b"]): e for e in rel}
    assert ("k0", "k18") in got and ("k18", "k39") in got
    for (a, b), e in got.items():
        true = poses[int(b[1:])] @ poses[int(a[1:])].T
        err = video_pose._angle(np.array(e["rotation"]).T @ true)
        assert err < 1.0, (a, b, e["source"], err)


def test_relative_poses_of_a_pan(pan):
    """The turns between chosen frames come back, chained through the video and matched directly."""
    r = video_pose.relative_poses(pan["video"], pan["keys"], hfov_deg=pan["hfov"])
    assert r["ok"] and not r["breaks"], r
    _check_turns(r["relative"], pan["poses"])
    assert {e["source"] for e in r["relative"]} >= {"direct"}
    assert all(c["chain_vs_direct_deg"] < 1.5 for c in r["checks"])
    assert all("direction" not in e for e in r["relative"])  # only turned


def test_relative_poses_of_a_video_on_its_side(pan, tmp_path):
    """The video stored on its side (no rotation metadata) and the frames extracted turned upright: the samples
    between them are turned the same way."""
    side = tmp_path / "side.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(pan["video"]), "-vf", "transpose=2", "-c:v", "libx264",
                    "-crf", "16", "-pix_fmt", "yuv420p", str(side)], check=True)
    keys = [{**k, "rotate": 90} for k in pan["keys"]]
    r = video_pose.relative_poses(side, keys, hfov_deg=pan["hfov"])
    assert r["ok"] and not r["breaks"] and r["size"][0] > r["size"][1], r
    _check_turns(r["relative"], pan["poses"])
    with pytest.raises(ValueError, match="different rotate"):
        video_pose.relative_poses(side, [keys[0], *pan["keys"][1:]], hfov_deg=pan["hfov"])


def test_relative_poses_skips_a_weak_step(pan):
    """One sample that matches nothing (noise, the middle of a dissolve) is stepped over, not chained."""
    r = video_pose.relative_poses(pan["noisy"], pan["keys"], hfov_deg=pan["hfov"])
    check = {(c["a"], c["b"]): c for c in r["checks"]}
    assert not r["breaks"] and check[("k0", "k18")]["skipped"] == 1, r
    assert check[("k0", "k18")]["chain_vs_direct_deg"] < 1.5
    _check_turns(r["relative"], pan["poses"])


def test_service_video_poses(pan):
    r = service.video_poses(pan["scene"], pan["hfov"])
    assert r["ok"] and (pan["scene"] / "video_poses.json").exists(), r
    _check_turns(r["relative"], pan["poses"])
    pf.FitRequest.model_validate({"photos": {}, "relative": r["relative"]})  # pastes into fit_photos as it is
    assert not service.video_poses(pan["scene"], video="other.mp4")["ok"]


def test_video_poses_without_opencv(tmp_path, monkeypatch):
    monkeypatch.setattr(video_pose, "available", lambda: False)
    r = service.video_poses(tmp_path)
    assert not r["ok"] and "OpenCV" in r["error"]


def _cam_basis(cid):
    pos, la, _ = CAMS[cid]
    d = [la[i] - pos[i] for i in range(3)]
    yaw = math.degrees(math.atan2(d[1], d[0]))
    pitch = -math.degrees(math.atan2(d[2], math.hypot(d[0], d[1])))
    return pf._basis(yaw, pitch)


def _relative(a, b, with_direction=True):
    bt_a, bt_b = _cam_basis(a), _cam_basis(b)
    e = {"a": a, "b": b, "rotation": pf._rel_rotation(bt_a, bt_b)}
    if with_direction:
        d = pf._sub(CAMS[a][0], CAMS[b][0])
        n = math.sqrt(pf._dot(d, d))
        e["direction"] = [pf._dot(bt_b[i], d) / n for i in range(3)]
    return e


def test_relative_fixes_a_weak_camera():
    """Camera w sees only 3 landmarks (6 measurements for 5 unknowns, and a wrong facing): alone it goes wrong,
    with the turns and moves measured from the video it lands where it was."""
    req = _request()
    req["photos"]["w"]["points"] = req["photos"]["w"]["points"][:3]
    req["photos"]["w"]["lines"] = []
    req["photos"]["w"]["facing"] = "north"  # really east-south-east
    alone = service.fit_photos(req)
    req["relative"] = [_relative("s", "w"), _relative("ne", "w")]
    r = service.fit_photos(req)
    assert r["ok"] and r["rms_px"] < 0.5, r
    assert math.dist(r["cameras"]["w"]["position"], CAMS["w"][0]) < 0.05, r["cameras"]["w"]
    assert math.dist(alone["cameras"]["w"]["position"], CAMS["w"][0]) > 0.2
    assert all(e["turn_error_deg"] < 0.5 for e in r["relative"])
    assert any("facing 'north'" in w for w in r["warnings"])  # the seed says the facing was off


def test_moves_do_not_shrink_a_room_without_ceiling():
    """The direction of a move says nothing about how far: it must not pull the room's scale."""
    from test_photo_fit import _no_ceiling

    req = {**_no_ceiling(_request()), "eye_height": 1.45}
    req["relative"] = [_relative("s", "w"), _relative("ne", "w"), _relative("s", "ne")]
    r = service.fit_photos(req)
    assert abs(r["room"]["width"] - 4.5) < 0.15 and abs(r["room"]["depth"] - 3.6) < 0.15, r["room"]
    assert all(e["used"] for e in r["relative"])


def test_relative_disagreeing_and_errors():
    req = _request()
    bad = _relative("s", "w", with_direction=False)
    bad["rotation"] = pf._rel_rotation(_cam_basis("s"), pf._basis(20, 10))  # far from w's real turn
    req["relative"] = [bad]
    r = service.fit_photos(req)
    assert any("relative s->w" in w and "turn" in w and "left out" in w for w in r["warnings"])
    assert r["relative"][0]["used"] is False  # the second pass fits without it
    for cid, (pos, _, _) in CAMS.items():
        assert math.dist(r["cameras"][cid]["position"], pos) < 0.05
    req["relative"] = [{"a": "s", "b": "zz", "rotation": [[1, 0, 0], [0, 1, 0], [0, 0, 1]]}]
    assert "no photo zz" in service.fit_photos(req)["error"]
    req["relative"] = [{"a": "s", "b": "w", "rotation": [[1, 0], [0, 1]]}]
    assert "3x3" in service.fit_photos(req)["error"]
    req["relative"] = [{**_relative("s", "w"), "hfov_deg": 50}]
    assert any("video_poses again" in w for w in service.fit_photos(req)["warnings"])


@cv
def test_cycle_filter_drops_a_pair_that_does_not_turn_back():
    import numpy as np

    R = {"a": _rot(0, 0), "b": _rot(20, 0), "c": _rot(50, 5), "d": _rot(70, 0)}
    direct = {(x, y): {"R": R[y] @ R[x].T} for x, y in (("a", "b"), ("b", "c"), ("a", "c"), ("c", "d"))}
    direct[("a", "c")] = {"R": _rot(10, 0) @ R["c"] @ R["a"].T}  # 10 deg off: the triangle a-b-c no longer closes
    kept, dropped = video_pose._cycle_filter(list(R), direct)
    assert {(x["a"], x["b"]) for x in dropped} == {("a", "b"), ("b", "c"), ("a", "c")}
    assert set(kept) == {("c", "d")}  # in no triangle: kept
    direct[("a", "c")] = {"R": R["c"] @ R["a"].T}
    kept, dropped = video_pose._cycle_filter(list(R), direct)
    assert not dropped and len(kept) == 4
    assert np.allclose(kept[("a", "b")]["R"], R["b"] @ R["a"].T)
