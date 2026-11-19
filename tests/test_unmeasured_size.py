"""A width / depth the photos do not hold is reported as not measured, first; the held-height refit also compares
the fits without each one's worst landmark (one landmark seen in one photo must not decide the height). Offline."""
from test_height_refit import _scale_off
from test_photo_fit import _request

from agentic_sim_world_gen import photo_fit as pf


def _without_east(req):
    """No landmark on the east wall (its corners, its window, its floor / ceiling line) and no shared point."""
    req["openings"].pop("win_e")
    req["points"].pop("shelf")
    for c in req["photos"].values():
        c["points"] = [p for p in c["points"]
                       if p["at"] != "shelf" and not any(k in p["at"] for k in ("corner:ne", "corner:se", "win_e"))]
        c["lines"] = [x for x in c["lines"] if not x["along"].endswith("east")]
    return req


def test_a_size_the_photos_do_not_hold_is_not_measured():
    r = pf.fit(_without_east(_request()))
    assert r["ok"] and r["room"]["unmeasured"], r["room"]
    for k in r["room"]["unmeasured"]:
        assert r["sigma_m"][k] > pf.UNMEASURED_SHARE * r["room"][k], (k, r["sigma_m"])
    w = r["warnings"][0]
    assert w.startswith("not measured: the room's ") and "report it as unmeasured" in w, r["warnings"]


def test_a_measured_room_has_no_unmeasured():
    r = pf.fit(_request())
    assert "unmeasured" not in r["room"] and not any(w.startswith("not measured") for w in r["warnings"]), r
    assert "_rms_but_one" not in r


def test_the_height_refit_lists_both_pixel_measures():
    req = _scale_off(1.25, one_depth=True, place="home")
    r = pf.fit(req)
    assert r["height_refit"]["adopted"] == "held", r["height_refit"]
    assert "unmeasured" not in r["room"]
    for k in ("fitted", "held"):
        assert r["height_refit"][k]["rms_px_but_one"] <= r["height_refit"][k]["rms_px"], r["height_refit"]


def test_one_landmark_off_at_the_held_height_does_not_keep_the_fitted(monkeypatch):
    """The held fit's rms is high only through one landmark (one point seen in one photo, far off at the held
    height and on at the fitted one): without it the held fit is as good, so it is taken. Several landmarks off
    (a misread ceiling) raise both measures, and the fitted one is kept."""
    orig = pf._fit

    def one_off(req, compare, solved=None):
        r = orig(req, compare, solved)
        if r.get("ok") and req.fit_height is False and not compare:  # the held fit: one landmark 60 px off
            r["rms_px"] = 12.0
        return r

    monkeypatch.setattr(pf, "_fit", one_off)
    hr = pf.fit(_scale_off(1.25, one_depth=True, place="home"))["height_refit"]
    assert hr["held"]["rms_px"] == 12.0 and hr["held"]["rms_px_but_one"] < 1, hr
    assert hr["adopted"] == "held", hr

    def many_off(req, compare, solved=None):
        r = orig(req, compare, solved)
        if r.get("ok") and req.fit_height is False and not compare:
            r["rms_px"], r["_rms_but_one"] = 12.0, 11.0
        return r

    monkeypatch.setattr(pf, "_fit", many_off)
    hr = pf.fit(_scale_off(1.25, one_depth=True, place="home"))["height_refit"]
    assert hr["adopted"] == "fitted", hr
