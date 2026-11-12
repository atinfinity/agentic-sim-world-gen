"""fit_photos lists what sets the room's scale and names the one source that disagrees."""
import math

from test_photo_fit import CAMS, D, H, W, _request

from agentic_sim_world_gen import photo_fit as pf
from agentic_sim_world_gen import service

def _zdepth(cid, X):
    pos, la, _ = CAMS[cid]
    d0 = [la[i] - pos[i] for i in range(3)]
    n = math.sqrt(sum(v * v for v in d0))
    return sum((X[i] - pos[i]) * d0[i] / n for i in range(3))


def _req(depth=None):
    """The room of test_photo_fit with the corners' depths times `depth`."""
    req = _request()
    corners = {f"corner:{c}:{z}": (W * cx, D * cy, 0 if z == "floor" else H)
               for c, (cx, cy) in pf.CORNERS.items() for z in ("floor", "ceiling")}
    if depth is not None:
        for cid, ph in req["photos"].items():
            for o in ph["points"]:
                if o["at"] in corners:
                    o["depth"] = depth * _zdepth(cid, corners[o["at"]])
    return req


def _scale(r):
    return {s["by"]: s for s in r["scale"]["sources"]}, [w for w in r["warnings"] if w.startswith("scale:")]


def test_sources_agree():
    r = service.fit_photos(_req(1.0))
    by, warns = _scale(r)
    assert set(by) == {"depth", "door top", "eye height", "ceiling (typical)"}, by
    assert all(abs(by[k]["scale"] - 1) < 0.01 for k in ("depth", "door top", "ceiling (typical)")), by
    assert by["depth"]["n"] == 3 and by["door top"]["of"] == "door_s.top"
    assert abs(by["eye height"]["scale"] - 1.4 / 1.45) < 0.01 and not by["eye height"]["used"], by["eye height"]
    assert by["ceiling (typical)"]["range"] == [1.0, 1.125] and by["ceiling (typical)"]["height_m"] == 2.4
    s = r["scale"]
    assert "outlier" not in s and abs(s["median"] - 1) < 0.01 and s["pm"] < 0.05, s
    assert not warns, warns


def test_the_one_source_apart_is_named():
    r = service.fit_photos(_req(1.25))  # the depth 25% long in every photo
    by, warns = _scale(r)
    assert r["scale"]["outlier"] == "depth" and by["depth"]["scale"] > 1.2, r["scale"]
    assert any("depth model is off" in w for w in warns), warns
    assert abs(r["room"]["width"] - W) < 0.05, r["room"]  # the door and the ceiling held it


def test_landmarks_only():
    """The height given, no depth, no video: the door's top and the eye height are listed beside it, nothing to name."""
    r = service.fit_photos(_request())
    by, warns = _scale(r)
    assert set(by) == {"door top", "eye height", "height (given)"} and by["height (given)"]["used"], by
    assert not warns and "outlier" not in r["scale"], r["scale"]
