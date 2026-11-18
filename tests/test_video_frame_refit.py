"""A photo whose landmarks turn it away from every video pair of it is fitted again with the video's turn kept,
and a width / depth the photos do not hold is reported as not measured. Offline."""
import json
import math
from pathlib import Path

from test_photo_fit import _relative, _request

from agentic_sim_world_gen import photo_fit as pf

# a recorded fit_photos request from an agent run: 7 frames of MuSHRoom's `computer` room (CC BY 4.0, Ren et al. WACV 2024),
# the landmarks the agent picked and video_poses' pairs. The room is 9.59 x 6.19 m, 2.65 m high (the LiDAR scan).
# Frame t028 is the only one of the east wall; its landmarks turn it 16 deg away from its 5 pairs.
REQ = json.loads((Path(__file__).parent / "fixtures" / "photo_fit_video_frame.json").read_text())
T028 = "computer_t028_50"


def test_classroom_frame_off_the_video():
    r = pf.fit({**REQ, "height": 2.7})
    v = r["video_frames"]
    assert v["adopted"] == "video" and list(v["photos"]) == [T028] and v["photos"][T028] > 10, v
    assert v["video"]["turn_error_deg"][1] < 5 < v["landmarks"]["turn_error_deg"][0], v
    assert v["video"]["rms_px"] <= pf.VIDEO_FRAME_GAIN * v["landmarks"]["rms_px"] + pf.VIDEO_FRAME_PX
    # held at 2.7 m (school: the fitted 3.35 m is outside 2.4-3.3): within 0.3 m of the scan (was 10.76 x 5.83)
    assert r["height_refit"]["adopted"] == "held" and r["room"]["height"] == 2.7, r["height_refit"]
    assert abs(r["room"]["width"] - 9.59) < 0.3 and abs(r["room"]["depth"] - 6.19) < 0.3, r["room"]
    assert r["warnings"][0].startswith(f"video: the landmarks turn {T028}") and "so it is used" in r["warnings"][0]
    assert all(d["used"] for d in r["relative"] if T028 in (d["a"], d["b"]))


def test_classroom_without_height_holds_against_one_landmark():
    """No height given: free, the room fits 3.33 m (11.67 x 7.67) because one desk corner seen in t028 alone is 141 px
    off at 2.7 m; without the one worst landmark both fit (3.4 / 3.9 px), so the held 2.7 m is taken."""
    q = {k: v for k, v in REQ.items() if k != "height"}
    r = pf.fit(q)
    h = r["height_refit"]
    assert h["adopted"] == "held" and h["held"]["rms_px"] > 3 * h["fitted"]["rms_px"], h
    assert h["held"]["rms_px_but_one"] <= pf.HELD_RMS_GAIN * h["fitted"]["rms_px_but_one"] + pf.HELD_RMS_PX, h
    assert abs(r["room"]["width"] - 9.59) < 0.3 and abs(r["room"]["depth"] - 6.19) < 0.3, r["room"]
    assert "_rms_but_one" not in r


def test_classroom_landmarks_fit_leaves_the_width_unmeasured():
    """The first fit (the video given up for t028): the width's 1-sigma is far beyond the room, said first, and the
    one photo of the east wall is named as the one the video disagrees with."""
    r = pf._fit(pf.FitRequest.model_validate(REQ), compare=False)
    assert "width" in r["room"]["unmeasured"], (r["room"], r["sigma_m"])
    assert r["warnings"][0].startswith("not measured: the room's width"), r["warnings"][0]
    one = next(w for w in r["warnings"] if "rests on one photo for the east wall" in w)
    assert f"({T028}), and the video disagrees with that photo by 16 deg" in one, one


def _rolled(cid, deg):
    """Photo cid's landmarks turned deg about the image's center: they roll its camera away from the video's (the
    true) pose, and nothing else in the room moves (a sideways shift of the pixels is taken up by the camera's place)."""
    req = _request()
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    for o in req["photos"][cid]["points"] + req["photos"][cid]["lines"]:
        u, v = o["px"][0] - 640, o["px"][1] - 480
        o["px"] = [640 + c * u - s * v, 480 + s * u + c * v]
    return req


def test_landmarks_that_hold_their_turn_are_kept():
    """Photo w's landmarks rolled 10 deg hold its turn: kept with the video they do not fit (rms 0.2 -> ~95 px), so
    the landmarks' fit stays and the warning says to look at the frame."""
    rel = [_relative("s", "ne"), _relative("s", "w"), _relative("ne", "w")]
    r = pf.fit({**_rolled("w", 10), "relative": rel})
    v = r["video_frames"]
    assert v["adopted"] == "landmarks" and v["photos"] == {"w": 9.9}, v
    assert v["video"]["rms_px"] > 10 * v["landmarks"]["rms_px"], v
    assert r["room"]["width"] == v["landmarks"]["width"] == 4.5 and r["room"]["depth"] == 3.6, r["room"]
    assert not any(d["used"] for d in r["relative"] if "w" in (d["a"], d["b"]))
    assert "so they are kept" in r["warnings"][0], r["warnings"][0]


def test_one_pair_off_is_not_a_frame_off():
    """One of w's two pairs off: the video's error, not the frame's (no refit); no pairs: nothing."""
    rel = [_relative("s", "ne"), _relative("s", "w", 10), _relative("ne", "w")]
    r = pf.fit({**_request(), "relative": rel})
    assert "video_frames" not in r and not r["relative"][1]["used"], r["relative"]
    # w's pairs left out for being of another SfM model (group), not for their turn: no refit
    rel = [_relative("s", "ne", robust=False, group="a"), _relative("s", "w", 10, robust=False, group="b"),
           _relative("ne", "w", 10, robust=False, group="b")]
    r = pf.fit({**_request(), "relative": rel})
    assert "video_frames" not in r and not any(d["used"] for d in r["relative"][1:]), r["relative"]
    assert not any("the video disagrees" in w for w in r["warnings"])
    r = pf.fit(_request())
    assert "video_frames" not in r and "unmeasured" not in r["room"]
    assert not any(w.startswith("not measured") for w in r["warnings"])
