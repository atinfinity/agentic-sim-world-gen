"""A fitted wall height outside the room's range (or suspect) is fitted again held, in the same call. Offline."""
import json

import pytest
from pydantic import ValidationError

from test_height_suspect import _misread_ceiling
from test_photo_fit import CAMS, D, W
from test_scale_evidence import _req

from agentic_sim_world_gen import photo_fit as pf
from agentic_sim_world_gen import service


def _scale_off(k, one_depth=False, **more):
    """A room k times as big as the photos' (they are the same for any size): each photo's depth and the camera
    height ask for k times the room; no door top, no shared point of known height. The height fits k x 2.4 m. That
    is a classroom whose depth was off, and a real high ceiling when it is right: the pixels do not tell
    them apart, the meters do. one_depth: the depth on photo `s` only (its scale ±15%: loose)."""
    req = _req(k)
    req.pop("points")
    for cid, ph in req["photos"].items():
        ph["points"] = [o for o in ph["points"] if o["at"] != "shelf"]
        if one_depth and cid != "s":
            for o in ph["points"]:
                o.pop("depth", None)
    req.update(door_top=None, eye_height=round(1.45 * k, 2))
    req.pop("height")
    return {**req, **more}


def test_a_scale_off_is_fitted_again_held():
    r = pf.fit(_scale_off(1.25, one_depth=True, place="home"))  # 3.01 m fitted, the depth loose
    hr = r["height_refit"]
    assert hr["adopted"] == "held" and hr["range"] == [2.2, 2.9] and "metric_worse" not in hr, hr
    assert hr["fitted"]["height"] == pytest.approx(3.01, abs=0.03) and hr["fitted"]["width"] > 1.2 * W, hr
    # held at 2.7 (above the typical 2.4-2.7): the room ~1.12x instead of ~1.25x
    assert hr["held_height_m"] == 2.7 and r["room"]["height"] == 2.7 and not r["room"]["height_fitted"], r["room"]
    assert abs(r["room"]["width"] - W * 2.7 / 2.4) < 0.1 and abs(r["room"]["depth"] - D * 2.7 / 2.4) < 0.1, r["room"]
    assert hr["held"]["width"] == r["room"]["width"] and hr["held"]["rms_px"] == r["rms_px"] < 2, hr
    w = r["warnings"][0]
    assert w.startswith("height: the fitted wall height 3.01") and "outside 2.2-2.9 m (home)" in w, w
    assert "held fit is used" in w and "fit_height=false, height=3.01" in w, w
    assert sum("fitted again with the height held" in x for x in r["warnings"]) == 1, r["warnings"]


def test_the_given_height_is_held():
    r = pf.fit(_scale_off(1.25, one_depth=True, place="home", height=2.8))  # given (the fit's start), in the range
    assert r["height_refit"]["adopted"] == "held" and r["height_refit"]["held_height_m"] == 2.8, r["height_refit"]
    assert abs(r["room"]["width"] - W * 2.8 / 2.4) < 0.1, r["room"]


@pytest.mark.parametrize("k, place", [(1.25, "home"), (1.47, "school")])
def test_a_high_ceiling_the_depth_sees_keeps_the_fitted(k, place):
    """The depth on every photo agrees with the fitted height (a real high ceiling, or a depth model off for the
    whole room - a classroom at 3.53 m): the pixels fit the held one as well, the depth's scale does not, so
    the fitted one is kept, and said once (in height_suspect's hint)."""
    r = pf.fit(_scale_off(k, place=place))
    hr = r["height_refit"]
    assert hr["adopted"] == "fitted" and hr["held"]["rms_px"] < 2 and hr["held_height_m"] == 2.7, hr
    assert any(x.startswith("depth asks for") for x in hr["metric_worse"]), hr
    assert r["room"]["height"] == pytest.approx(2.4 * k, abs=0.03) and r["room"]["height_fitted"], r["room"]
    held = [x for x in r["warnings"] if "held at 2.70 m" in x]
    assert len(held) == 1 and held[0].startswith("height: the fitted wall height") and "is suspect" in held[0], held
    assert "fitted again" not in " ".join(r["warnings"]), r["warnings"]


def test_a_misread_ceiling_keeps_the_fitted():
    """The size right, the ceiling's landmarks on a soffit. Held at 2.4 m they pull the room out of shape
    (rms 0 -> ~60 px): the fitted one is kept, still flagged suspect, and the held one is listed."""
    r = pf.fit({**_misread_ceiling(0.75), "place": "office"})
    hr = r["height_refit"]
    assert hr["adopted"] == "fitted" and hr["held_height_m"] == 2.4 and hr["held"]["rms_px"] > 10, hr
    assert "outside 2.3-3.2 m (office)" in hr["reason"], hr
    assert r["room"]["height"] == 1.8 and r["room"]["height_suspect"] and abs(r["room"]["width"] - W) < 0.05, r["room"]
    assert sum("held at 2.40 m" in w for w in r["warnings"]) == 1, r["warnings"]


def test_no_place_is_not_held():
    """No place (and no height_range): shops, halls, stations have high ceilings, so the height is only warned of
    (height_suspect), as before."""
    for req in (_scale_off(1.47), _misread_ceiling(0.75), {**_misread_ceiling(0.75), "place": "shop"}):
        r = pf.fit(req)
        assert "height_refit" not in r and r["room"]["height_suspect"], r.get("height_refit")
        assert any("is suspect" in w for w in r["warnings"]), r["warnings"]


def test_an_unknown_place_is_warned():
    r = pf.fit({**_misread_ceiling(0.75), "place": "classroom"})
    assert "height_refit" not in r, r["height_refit"]
    assert any("place 'classroom' is not a Room.place" in w for w in r["warnings"]), r["warnings"]
    assert not any("is not a Room.place" in w for w in pf.fit({**_misread_ceiling(1.0), "place": "shop"})["warnings"])


@pytest.mark.parametrize("bad", [(3, 2), (0, 3), (-1, 3), (2.5, 2.5)])
def test_the_height_range_is_checked(bad):
    with pytest.raises(ValidationError, match="0 < low < high"):
        pf.FitRequest.model_validate({**_misread_ceiling(1.0), "height_range": bad})
    r = service.fit_photos({**_misread_ceiling(1.0), "height_range": bad})
    assert not r["ok"] and "0 < low < high" in r["error"], r


def test_in_range_and_suspect():
    # the place's range takes 3.0 m in, but it is suspect: tried (the depth keeps the fitted one)
    r = pf.fit(_scale_off(1.25, place="cafe"))
    assert r["room"]["height_suspect"] and r["height_refit"]["adopted"] == "fitted", r["height_refit"]
    assert "is suspect" in r["height_refit"]["reason"], r["height_refit"]
    # an explicit height_range that takes the fitted height in is the caller's word: not tried
    r = pf.fit({**_misread_ceiling(0.85), "height_range": (1.8, 3.2)})  # 2.04 m: in the range, suspect
    assert r["room"]["height_suspect"] and "height_refit" not in r, r.get("height_refit")


@pytest.mark.parametrize("extra, refit", [({}, False), ({"place": "factory"}, False), ({"place": "home"}, True),
                                          ({"height_range": (2.4, 2.8)}, True), ({"place": "home", "height_range": (2, 4)}, False)])
def test_the_range_by_place(extra, refit):
    r = pf.fit({**_misread_ceiling(1.25), **extra})  # 3.0 m with the size right: a high ceiling
    assert ("height_refit" in r) == refit, (extra, r.get("height_refit"))
    assert r["room"]["height"] == 3.0 and r["room"]["height_fitted"]  # the landmarks hold it: kept either way
    if refit and "place" in extra:
        assert "outside 2.2-2.9 m (home)" in r["height_refit"]["reason"], r["height_refit"]


def test_no_refit_when_right_or_held():
    assert "height_refit" not in pf.fit({**_misread_ceiling(1.0), "place": "office"})
    assert "height_refit" not in pf.fit({**_scale_off(1.47, place="school"), "fit_height": False, "height": 2.4})
    assert "height_refit" not in pf.fit(_scale_off(1.47, place="school"), compare=False)  # nor the comparisons' fits


def test_solved_once(monkeypatch):
    """The fit is solved without the comparisons, then they run on it: no second solve."""
    calls = []
    once = pf._fit_once
    monkeypatch.setattr(pf, "_fit_once", lambda *a: calls.append(1) or once(*a))
    pf.fit(_misread_ceiling(1.0))
    n = len(calls)
    calls.clear()
    pf.fit(_misread_ceiling(1.0), compare=False)
    assert n == len(calls) >= 1, (n, len(calls))
    req = _scale_off(1.25, one_depth=True, place="home")
    calls.clear()
    pf.fit(req, compare=False)
    n = len(calls)
    calls.clear()
    pf.fit(req)  # the fitted, the held: each solved once
    assert len(calls) == 2 * n >= 2, (n, len(calls))


def _scene(tmp_path, height=2.6, place="home"):
    spec = {"name": "r", "room": {"width": 4, "depth": 3, "height": height, "place": place},
            "inputs": [{"id": c, "kind": "photo", "path": f"{c}.jpg"} for c in CAMS]}
    return service.create_scene(spec, tmp_path / "r")["scene_dir"]


def test_apply_keeps_the_scenes_height_unless_given(tmp_path):
    d = _scene(tmp_path)  # set at 2.6 m (set_room, say); the scene's place sets the range
    r = service.fit_photos(_scale_off(1.25, one_depth=True), d)
    assert r["height_refit"]["adopted"] == "held" and "(home)" in r["height_refit"]["reason"], r["height_refit"]
    room = service.load(d).spec.room
    assert room.height == 2.6 and room.width == pytest.approx(r["room"]["width"]), room
    assert any("the fit used the height 2.7 m but the scene's room is 2.6 m (kept)" in w for w in r["warnings"]), r["warnings"]
    # the height given in the request: the scene gets it with the size fitted at it
    r = service.fit_photos(_scale_off(1.25, one_depth=True, height=2.8), d)
    assert r["height_refit"]["adopted"] == "held", r["height_refit"]
    room = service.load(d).spec.room
    assert room.height == 2.8 and r["applied"]["room"]["height"] == 2.8, room
    a = next(a for a in service.load(d).spec.assumptions if a.item == "size")
    assert "高さを固定" in a.value, a


def test_a_broken_scene_is_an_error(tmp_path):
    d = _scene(tmp_path)
    f = next(p for p in (tmp_path / "r").rglob("scene.resolved.json"))
    f.write_text(json.dumps({"spec": {"room": "broken"}}))
    r = service.fit_photos(_scale_off(1.25, one_depth=True), d)
    assert r["ok"] is False and r["error"].startswith("out_dir: the scene could not be read"), r
