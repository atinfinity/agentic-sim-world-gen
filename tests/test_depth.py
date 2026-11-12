"""Monocular depth in fit_photos: per-photo scale, surfaces, and filling depth from <stem>.depth.npz."""
import json
import math

import pytest

from agentic_sim_world_gen import depth as dp
from agentic_sim_world_gen import photo_fit as pf
from agentic_sim_world_gen import service

from test_photo_fit import CAMS, D, H, W, _no_ceiling, _proj, _relative, _request

BIAS = {"s": 1.12, "ne": 0.9, "w": 1.05}  # the model's scale per photo (MoGe-2 is off by up to ~20 %)
MEAN = math.exp(sum(math.log(b) for b in BIAS.values()) / len(BIAS))  # the scale the prior settles on
VIDEO = [_relative("s", "ne", robust=False), _relative("ne", "w", robust=False)]  # poses held in full (robust=False)


def _z(cid, X):
    pos, la, _ = CAMS[cid]
    d0 = [la[i] - pos[i] for i in range(3)]
    n = math.sqrt(sum(v * v for v in d0))
    return sum((X[i] - pos[i]) * d0[i] / n for i in range(3))


def _landmark(at):
    p = at.split(":")
    if p[0] == "corner":
        cx, cy = pf.CORNERS[p[1]]
        return (W * cx, D * cy, 0.0 if p[2] == "floor" else H)
    return None


def _with_depth(req, bias=None):
    for cid, c in req["photos"].items():
        for o in c["points"]:
            if X := _landmark(o["at"]):
                o["depth"] = _z(cid, X) * (bias or BIAS)[cid]
    return req


def _surfaces(cid):
    """Pixels on the floor and the walls with their (biased) depth."""
    out = []
    for on, pts in (("floor", [(x, y, 0.0) for x in (1.0, 2.0, 3.0) for y in (1.0, 2.0)]),
                    ("wall:north", [(x, D, z) for x in (1.0, 3.0) for z in (0.8, 1.6)]),
                    ("wall:east", [(W, y, z) for y in (0.8, 2.4) for z in (0.8, 1.6)]),
                    ("wall:west", [(0.0, y, z) for y in (0.8, 2.4) for z in (0.8, 1.6)]),
                    ("wall:south", [(x, 0.0, z) for x in (1.0, 3.0) for z in (0.8, 1.6)])):
        for X in pts:
            if q := _proj(cid, X):
                out.append({"on": on, "px": q, "depth": _z(cid, X) * BIAS[cid]})
    return out


EYE = sum(p[2] for p, _, _ in CAMS.values()) / len(CAMS)  # 1.45


def test_depth_sets_the_scale_without_ceiling():
    """No ceiling landmark: the depth at a few floor corners and the eye height set the scale together, and the
    warning says so (nothing of known length is seen)."""
    req = {**_with_depth(_no_ceiling(_request()), {c: 1.0 for c in CAMS}), "eye_height": EYE}
    r = service.fit_photos(req)
    assert r["ok"] and r["rms_px"] < 0.5, r
    assert abs(r["room"]["width"] - W) < 0.05 and abs(r["room"]["depth"] - D) < 0.05, r["room"]
    assert any("nothing of known length" in w and "eye_height" in w for w in r["warnings"]), r["warnings"]
    high = service.fit_photos({**req, "eye_height": 2.0})  # a wrong eye height pulls, the depth holds it back
    plain = service.fit_photos({**_no_ceiling(_request()), "eye_height": 2.0})  # without depth: all the way
    assert W + 0.1 < high["room"]["width"] < plain["room"]["width"] - 0.5, (high["room"], plain["room"])
    known = service.fit_photos(_with_depth(_request(), {c: 1.0 for c in CAMS}) | {"fit_height": False})
    assert not any("nothing of known length" in w for w in known["warnings"])  # the given height is seen


def test_depth_far_in_every_photo_without_ceiling():
    """The depth model 13% far in every frame (a rendered room): the per-photo scales do not average it out,
    the camera height pulls the room back toward its size."""
    req = {**_with_depth(_no_ceiling(_request()), {c: 1.13 for c in CAMS}), "eye_height": EYE}
    r = service.fit_photos(req)
    far = service.fit_photos({**req, "eye_height": EYE * 1.13})  # the eye height agrees with the depth: 13% big
    assert r["ok"] and far["room"]["width"] > W * 1.11, far["room"]
    assert r["room"]["width"] < far["room"]["width"] - 0.1 and r["room"]["depth"] < far["room"]["depth"] - 0.08
    assert any("nothing of known length" in w for w in r["warnings"])


def test_depth_scale_per_photo_needs_the_relative_poses():
    """Each photo's depth is off by its own scale. A photo that sees one or two walls takes its scale by moving
    its camera, so the depth alone does not fix the room; with the relative poses (one frame up to scale) the
    room comes out at the photos' mean scale and each photo's depth_scale near its own."""
    req = {**_no_ceiling(_request()), "eye_height": EYE * MEAN}  # the eye height of the room at the mean scale
    for cid, c in req["photos"].items():
        c["surfaces"] = _surfaces(cid)
    r = service.fit_photos({**req, "relative": VIDEO})
    assert r["ok"] and r["rms_px"] < 0.5, r
    assert abs(r["room"]["width"] - W * MEAN) < 0.1 and abs(r["room"]["depth"] - D * MEAN) < 0.1, r["room"]
    for cid in CAMS:
        assert abs(r["cameras"][cid]["depth_scale"] - BIAS[cid] / MEAN) < 0.03, (cid, r["cameras"][cid])
        assert r["cameras"][cid]["depth_rms"] < 0.02
    bad = {**req, "photos": {**req["photos"], "s": {**req["photos"]["s"], "surfaces": [{"on": "roof", "px": [1, 1],
                                                                                         "depth": 1.0}]}}}
    assert "surface 'roof'" in service.fit_photos(bad)["error"]


def test_fill_from_depth_map(tmp_path, monkeypatch):
    np = pytest.importorskip("numpy")
    req = _no_ceiling(_request())
    for cid in CAMS:  # a depth map of each photo at 1/4 size: the true depth x the bias
        img = tmp_path / f"{cid}.jpg"
        img.write_bytes(b"")
        pos, la, hf = CAMS[cid]
        d0 = np.array(la) - np.array(pos)
        fw = d0 / np.linalg.norm(d0)
        rt = np.array([fw[1], -fw[0], 0.0]) / math.hypot(fw[0], fw[1])
        up = np.cross(rt, fw)
        f = 640 / math.tan(math.radians(hf) / 2)
        u, v = np.meshgrid((np.arange(320) + 0.5) * 4, (np.arange(240) + 0.5) * 4)
        ray = fw[None, None] + ((u - 640) / f)[..., None] * rt + (-(v - 480) / f)[..., None] * up
        z = np.full(u.shape, np.inf)
        for ax, val in ((2, 0.0), (2, H), (1, 0.0), (1, D), (0, 0.0), (0, W)):  # the room box
            with np.errstate(divide="ignore", invalid="ignore"):
                t = (val - pos[ax]) / ray[..., ax]
            z = np.where((t > 0) & (t < z), t, z)
        meta = {"model": "test", "hfov_deg": hf + 8, "size": [1280, 960]}
        np.savez_compressed(dp.map_path(img), depth=(z * BIAS[cid]).astype(np.float16), meta=np.array(json.dumps(meta)))
        req["photos"][cid]["image"] = str(img)
        req["photos"][cid]["surfaces"] = [{"on": s["on"], "px": s["px"]} for s in _surfaces(cid)]
    plain = service.fit_photos({**req, "eye_height": 2.0})  # surface pixels without a depth say nothing
    assert any("no depth and were ignored" in w for w in plain["warnings"])
    monkeypatch.delenv("AGENTIC_SIM_WORLD_GEN_DEPTH_PYTHON", raising=False)  # the maps exist: the model does not run
    r = service.fit_photos({**req, "eye_height": EYE * MEAN, "relative": VIDEO}, depth=True)
    assert r["ok"], r
    assert r["depth"]["photos"]["s"]["filled"] > 5 and r["depth"]["missing"] == []
    assert abs(r["room"]["width"] - W * MEAN) < 0.15 and abs(r["room"]["depth"] - D * MEAN) < 0.15, r["room"]
    assert abs(r["cameras"]["ne"]["depth_scale"] - BIAS["ne"] / MEAN) < 0.05
    assert any("the depth model sees a field of view" in w for w in r["warnings"]), r["warnings"]


def test_run_needs_the_depth_python(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENTIC_SIM_WORLD_GEN_DEPTH_PYTHON", raising=False)
    img = tmp_path / "a.jpg"
    img.write_bytes(b"")
    r = service.fit_photos({"photos": {"a": {"size": [10, 10], "facing": "north", "image": str(img)}}}, depth=True)
    assert not r["ok"] and "AGENTIC_SIM_WORLD_GEN_DEPTH_PYTHON" in r["error"]
    assert "image" in service.fit_photos({"photos": {"a": {"size": [10, 10], "facing": "north"}}}, depth=True)["error"]


def test_depth_fits_the_wall_height():
    """With a depth the scale no longer comes from the wall height: a wrong guess is fitted back."""
    req = {**_with_depth(_request(), {c: 1.0 for c in CAMS}), "height": 2.1}
    r = service.fit_photos(req)
    assert r["ok"] and r["rms_px"] < 0.5, r
    assert r["room"]["height_fitted"] and abs(r["room"]["height"] - H) < 0.03, r["room"]
    assert abs(r["room"]["width"] - W) < 0.05 and "height" in r["sigma_m"]
    fixed = service.fit_photos({**req, "fit_height": False})  # the guess kept: the landmarks do not fit
    assert fixed["room"]["height"] == 2.1 and not fixed["room"]["height_fitted"] and fixed["rms_px"] > 2
    no_ceiling = service.fit_photos({**_with_depth(_no_ceiling(_request()), {c: 1.0 for c in CAMS}), "height": 2.1})
    assert not no_ceiling["room"]["height_fitted"] and any("wall height is the given" in w
                                                           for w in no_ceiling["warnings"])
    plain = service.fit_photos(_request())  # no depth: the height sets the scale, as before
    assert not plain["room"]["height_fitted"] and plain["room"]["height"] == H


def test_fitted_height_with_biased_depth_and_video_poses():
    """Biased depth per photo + relative poses: the height comes out at the photos' mean scale like the walls."""
    req = {**_request(), "height": 2.1}
    for cid, c in req["photos"].items():
        c["surfaces"] = _surfaces(cid)
    r = service.fit_photos({**req, "relative": VIDEO})
    assert r["ok"] and r["rms_px"] < 1.0, r
    assert abs(r["room"]["height"] - H * MEAN) < 0.1 and abs(r["room"]["width"] - W * MEAN) < 0.1, r["room"]


def test_height_not_fixed_when_no_photo_sees_floor_and_ceiling():
    """Each photo sees only the floor or only the ceiling: camera heights and the ceiling move together."""
    req = _with_depth(_request(), {c: 1.0 for c in CAMS})
    for cid, c in req["photos"].items():
        keep = "floor" if cid == "s" else "ceiling"
        c["points"] = [o for o in c["points"] if o["at"].endswith(keep)]
        c["lines"] = [o for o in c["lines"] if o["along"].startswith(keep)]
    r = service.fit_photos({**req, "height": 2.1})
    assert r["ok"] and r["room"]["height_fitted"]
    assert any("not fixed by the photos" in w for w in r["warnings"]), r["warnings"]
    good = service.fit_photos({**_with_depth(_request(), {c: 1.0 for c in CAMS}), "height": 2.1})
    assert not any("not fixed by the photos" in w for w in good["warnings"])


def test_loose_scale_when_floor_and_ceiling_are_tied():
    """Floor-only and ceiling-only photos joined by the video's poses: the shape is fixed and only the depth's
    scale is loose, so the warning says that and not that the floor and the ceiling are unseen together."""
    req = {**_request(), "height": 2.1}
    for cid, c in req["photos"].items():
        keep = "floor" if cid == "s" else "ceiling"
        c["points"] = [o for o in c["points"] if o["at"].endswith(keep)]
        c["lines"] = [o for o in c["lines"] if o["along"].startswith(keep)]
        c["surfaces"] = [o for o in _surfaces(cid) if o["on"] != "floor" or keep == "floor"]
    tied = service.fit_photos({**req, "relative": VIDEO})
    assert tied["ok"] and tied["room"]["height_fitted"]
    # the eye height now joins the depth when nothing of known length is seen, so the scale is no longer loose
    assert any("nothing of known length" in w for w in tied["warnings"]), tied["warnings"]
    assert not any("not fixed by the photos" in w for w in tied["warnings"])
    apart = service.fit_photos(req)
    assert any("not fixed by the photos" in w for w in apart["warnings"]), apart["warnings"]
    assert not pf._floor_and_ceiling_tied(pf.FitRequest.model_validate(req), set())
    assert pf._floor_and_ceiling_tied(pf.FitRequest.model_validate({**req, "relative": VIDEO}), {0}) is None  # s alone
