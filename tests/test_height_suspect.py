"""A fitted wall height the room's size disagrees with is flagged and not applied. Offline."""
import pytest

from test_photo_fit import CAMS, D, H, W, _proj, _wall
from test_scale_evidence import _req

from agentic_sim_world_gen import photo_fit as pf
from agentic_sim_world_gen import service


def _misread_ceiling(k):
    """The room of test_scale_evidence (the depth right) with every ceiling corner and line labeled at k times
    the real height: a soffit or a beam taken for the ceiling."""
    req = _req(1.0)
    for cid, ph in req["photos"].items():
        for o in ph["points"]:
            if o["at"].startswith("corner:") and o["at"].endswith(":ceiling"):
                cx, cy = pf.CORNERS[o["at"].split(":")[1]]
                o["px"] = _proj(cid, (W * cx, D * cy, H * k))
                o.pop("depth", None)
        ph["points"] = [o for o in ph["points"] if o["px"]]
        for ln in ph["lines"]:
            if ln["along"].startswith("ceiling:"):
                wall = ln["along"].split(":")[1]
                ln["px"] = next((q for a in (0.5, 1.0, 1.5, 2.0, 2.5, 3.0) if (q := _proj(cid, _wall(wall, a, H * k)))),
                                None)
        ph["lines"] = [ln for ln in ph["lines"] if ln["px"]]
    return req


def test_misread_ceiling_is_suspect():
    r = service.fit_photos(_misread_ceiling(0.75))  # 1.8 m: the size is held by the depth and the door
    s = r["scale"]
    assert s["outlier"] == "ceiling (typical)" and r["room"]["height_fitted"] and r["room"]["height_suspect"], s
    hs = s["height_suspect"]
    assert hs["height_m"] == pytest.approx(1.8, abs=0.05) and hs["typical"] == list(pf.CEILING_TYPICAL_M)
    assert set(hs["photos"]) == set(CAMS) and abs(r["room"]["width"] - W) < 0.05, (hs, r["room"])
    assert any(w.startswith("height: the fitted wall height 1.8") and "fit_height=false" in w
               and "about 2.4 m" in w for w in r["warnings"]), r["warnings"]


def test_a_right_or_plausible_height_is_not_suspect():
    for k in (1.0, 1.25):  # 3.0 m is a high ceiling, not a misread one
        r = service.fit_photos(_misread_ceiling(k))
        assert "height_suspect" not in r["scale"] and "height_suspect" not in r["room"], r["scale"]


def test_apply_keeps_the_scenes_height(tmp_path):
    spec = {"name": "r", "room": {"width": 4, "depth": 3, "height": 2.4},
            "inputs": [{"id": c, "kind": "photo", "path": f"{c}.jpg"} for c in CAMS]}
    d = service.create_scene(spec, tmp_path / "r")["scene_dir"]
    r = service.fit_photos(_misread_ceiling(0.75), d)
    s = service.load(d).spec.room
    assert (s.width, s.depth, s.height) == (pytest.approx(W, abs=0.05), pytest.approx(D, abs=0.05), 2.4), s
    assert r["applied"]["room"]["height"] == 2.4 and r["applied"]["cameras"], r["applied"]
    assert any("is suspect" in w and "2.4 m was kept" in w for w in r["warnings"]), r["warnings"]
    a = next(a for a in service.load(d).spec.assumptions if a.item == "size")
    assert "高さも推定" not in a.value, a


def test_one_warning_with_the_way_to_keep_a_real_height():
    """The outlier line points at the height warning instead of saying something else."""
    r = service.fit_photos(_misread_ceiling(0.75))
    w = [x for x in r["warnings"] if "ceiling (typical)" in x]
    assert len(w) == 1 and "see the height warning" in w[0] and "high or low" not in w[0], w


def test_a_high_fit_says_how_to_keep_it():
    """3.3 m: a real high ceiling is not told apart from a misread one, so the hint guesses the typical top and
    says how to keep the fitted height (the fixture cannot reach it)."""
    hs = pf._height_suspect(pf.FitRequest.model_validate(_req(1.0)), 3.3)
    assert hs["height_m"] == 3.3 and "about 2.7 m" in hs["hint"] and "set_room(height=3.30)" in hs["hint"], hs
