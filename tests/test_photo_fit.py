"""fit_photos: project a known room into known cameras, fit it back."""
import json
import math

import pytest

from agentic_sim_world_gen import photo_fit as pf
from agentic_sim_world_gen import service

W, D, H = 4.5, 3.6, 2.4
CAMS = {"s": ((2.6, 0.35, 1.45), (2.3, 3.6, 0.9), 75.0), "ne": ((3.85, 2.85, 1.5), (0.8, 0.9, 0.7), 75.0),
        "w": ((1.35, 2.9, 1.4), (4.5, 1.6, 0.8), 70.0)}
OPS = {"door_s": ("door", "south", 0.7, 0.8, 0.0, 2.0), "win_n": ("window", "north", 2.25, 1.6, 0.8, 2.0),
       "win_e": ("window", "east", 1.0, 0.9, 0.9, 1.9)}
FACING = {"s": "north", "ne": "southwest", "w": "east"}
ROLL = {}  # camera -> roll [deg] (a handheld tilt), 0 when missing


def _wall(wall, a, z):
    return {"south": (a, 0, z), "north": (a, D, z), "west": (0, a, z), "east": (W, a, z)}[wall]


def _proj(cid, X):
    pos, la, hf = CAMS[cid]
    d0 = [la[i] - pos[i] for i in range(3)]
    n = math.sqrt(sum(v * v for v in d0))
    fw = [v / n for v in d0]
    rn = math.hypot(fw[0], fw[1])
    rt = [fw[1] / rn, -fw[0] / rn, 0.0]
    r = math.radians(ROLL.get(cid, 0.0))
    dn = pf._cross(fw, rt)
    rt = [math.cos(r) * rt[i] + math.sin(r) * dn[i] for i in range(3)]
    up = pf._cross(rt, fw)
    f = 640 / math.tan(math.radians(hf) / 2)
    d = [X[i] - pos[i] for i in range(3)]
    z = pf._dot(d, fw)
    if z <= 0.1:
        return None
    u, v = 640 + f * pf._dot(d, rt) / z, 480 - f * pf._dot(d, up) / z
    return [u, v] if 0 <= u <= 1280 and 0 <= v <= 960 else None


def _request(hfov_known=True):
    lm = {f"corner:{c}:{z}": (W * cx, D * cy, 0 if z == "floor" else H)
          for c, (cx, cy) in pf.CORNERS.items() for z in ("floor", "ceiling")}
    for oid, (kind, wall, c, w, s, t) in OPS.items():
        for e, a in (("start", c - w / 2), ("end", c + w / 2)):
            for k, z in (("top", t), ("bottom", s)):
                if not (kind == "door" and k == "bottom"):
                    lm[f"{oid}:{e}:{k}"] = _wall(wall, a, z)
    lm["shelf"] = (4.07, 3.2, 2.0)
    photos = {}
    for cid in CAMS:
        pts = [{"at": at, "px": q} for at, X in lm.items() if (q := _proj(cid, X))]
        lines = []
        for wall in pf.WALLS:
            for k, z in (("floor", 0.0), ("ceiling", H)):
                for a in (0.5, 1.0, 1.5, 2.0, 2.5, 3.0):
                    if q := _proj(cid, _wall(wall, a, z)):
                        lines.append({"along": f"{k}:{wall}", "px": q})
                        break
        photos[cid] = {"size": [1280, 960], "facing": FACING[cid], "points": pts, "lines": lines}
        if hfov_known:
            photos[cid]["hfov_deg"] = CAMS[cid][2]
    return {"photos": photos, "height": H, "hfov_deg": 72,
            "openings": {k: {"kind": v[0], "wall": v[1]} for k, v in OPS.items()},
            "points": {"shelf": {"z": 2.0}},
            "locate": [{"name": "plant", "photo": "w", "px": _proj("w", (4.05, 0.12, 0.0)), "object": "plant"}]}


def test_exact_recovery(tmp_path):
    r = service.fit_photos(_request(), tmp_path)
    assert r["ok"] and r["rms_px"] < 0.1, r
    assert (r["room"]["width"], r["room"]["depth"]) == (4.5, 3.6)
    assert "outside" not in r and not any("beyond" in w for w in r["warnings"])
    ops = {o["id"]: o for o in r["room"]["openings"]}
    assert ops["door_s"]["offset"] == 0.7 and ops["door_s"]["width"] == 0.8 and ops["door_s"]["height"] == 2.0
    assert ops["win_n"]["sill"] == 0.8 and ops["win_n"]["height"] == 1.2
    assert ops["win_e"]["offset"] == 1.0
    for cid, (pos, _, _) in CAMS.items():
        assert math.dist(r["cameras"][cid]["position"], pos) < 0.02, (cid, r["cameras"][cid])
    assert math.dist(r["points"]["shelf"], (4.07, 3.2, 2.0)) < 0.02
    assert math.dist(r["located"][0]["xy"], (4.05, 0.12)) < 0.02
    assert [w for w in r["warnings"] if "plant rest on one point" not in w] == []  # a plant pot: one point is fine
    saved = json.loads((tmp_path / "photo_fit.json").read_text())
    assert saved["result"]["room"]["width"] == 4.5 and "photos" in saved["request"]


def test_roll(monkeypatch):
    """A handheld tilt: each camera's roll is fitted (a weak prior keeps it near 0 when little is seen)."""
    monkeypatch.setitem(ROLL, "s", 4.0)
    monkeypatch.setitem(ROLL, "ne", -3.0)
    r = service.fit_photos(_request())
    assert r["ok"] and r["rms_px"] < 1, r
    for cid in CAMS:
        assert abs(r["cameras"][cid]["roll_deg"] - ROLL.get(cid, 0.0)) < 0.3, (cid, r["cameras"][cid])
        assert math.dist(r["cameras"][cid]["position"], CAMS[cid][0]) < 0.05
    assert abs(r["room"]["width"] - 4.5) < 0.03 and abs(r["room"]["depth"] - 3.6) < 0.03


def test_shared_fov_and_noise():
    req = _request(hfov_known=False)
    for c in req["photos"].values():  # 2 px of picking error
        for i, o in enumerate(c["points"] + c["lines"]):
            o["px"] = [o["px"][0] + (2 if i % 2 else -2), o["px"][1] + (-2 if i % 3 else 2)]
    r = service.fit_photos(req)
    assert r["ok"] and r["rms_px"] < 4
    assert abs(r["room"]["width"] - 4.5) < 0.1 and abs(r["room"]["depth"] - 3.6) < 0.1
    for cid, (pos, _, _) in CAMS.items():
        assert math.dist(r["cameras"][cid]["position"][:2], pos[:2]) < 0.6


def test_mislabel_and_errors():
    req = _request()
    pts = req["photos"]["s"]["points"]
    a = next(o for o in pts if o["at"] == "win_n:start:top")
    b = next(o for o in pts if o["at"] == "win_n:end:top")
    a["px"], b["px"] = b["px"], a["px"]  # start / end swapped
    r = service.fit_photos(req)
    assert r["rms_px"] > 6 and any("mislabeled" in w for w in r["warnings"])
    assert r["worst"][0]["photo"] == "s"
    req = _request()
    req["photos"]["s"]["points"].append({"at": "corner:xx:floor", "px": [1, 1]})
    assert "corner 'xx'" in service.fit_photos(req)["error"]
    req = _request()
    req["photos"]["s"]["facing"] = "up"
    assert "facing" in service.fit_photos(req)["error"]
    assert not service.fit_photos({"photos": "x"})["ok"]


def _no_ceiling(req):
    """Drop every landmark at the ceiling: images alone then cannot tell the room's scale."""
    for c in req["photos"].values():
        c["points"] = [o for o in c["points"] if not o["at"].endswith(":ceiling") and o["at"] != "shelf"]
        c["lines"] = [o for o in c["lines"] if not o["along"].startswith("ceiling:")]
    req["points"] = {}
    return req


def test_scale_from_eye_height_without_ceiling():
    eye = sum(p[2] for p, _, _ in CAMS.values()) / len(CAMS)  # 1.45
    r = service.fit_photos({**_no_ceiling(_request()), "eye_height": eye})
    assert r["ok"] and r["rms_px"] < 2, r
    assert abs(r["room"]["width"] - W) < 0.15 and abs(r["room"]["depth"] - D) < 0.15, r["room"]
    assert any("eye_height" in w for w in r["warnings"])
    big = service.fit_photos({**_no_ceiling(_request()), "eye_height": 2 * eye})  # same pictures, twice the room
    assert abs(big["room"]["width"] - 2 * W) < 0.3, big["room"]
    assert not any("eye_height" in w for w in service.fit_photos(_request())["warnings"])


def _axes(cid):
    """Camera axes (OpenCV: right, down, forward) in room coordinates: rows of room -> camera."""
    pos, la, _ = CAMS[cid]
    d0 = [la[i] - pos[i] for i in range(3)]
    n = math.sqrt(sum(v * v for v in d0))
    fw = [v / n for v in d0]
    rn = math.hypot(fw[0], fw[1])
    rt = [fw[1] / rn, -fw[0] / rn, 0.0]
    up = pf._cross(rt, fw)
    return [rt, [-v for v in up], fw]


def _relative(a, b, turn_off_deg=0.0, **kw):
    """True relative pose of photos a -> b (b's pose turned by turn_off_deg about its forward axis)."""
    Ba, Bb = _axes(a), _axes(b)
    c, s = math.cos(math.radians(turn_off_deg)), math.sin(math.radians(turn_off_deg))
    Bb = [[c * Bb[0][i] + s * Bb[1][i] for i in range(3)], [-s * Bb[0][i] + c * Bb[1][i] for i in range(3)], Bb[2]]
    R = [[sum(Bb[i][k] * Ba[j][k] for k in range(3)) for j in range(3)] for i in range(3)]
    d = [CAMS[a][0][i] - CAMS[b][0][i] for i in range(3)]
    n = math.sqrt(sum(v * v for v in d))
    return {"a": a, "b": b, "rotation": R, "direction": [sum(Bb[i][k] * d[k] / n for k in range(3)) for i in range(3)],
            "hfov_deg": CAMS[a][2], **kw}


def test_relative_exact_and_robust():
    r = service.fit_photos({**_request(), "relative": [_relative("s", "ne"), _relative("ne", "w")]})
    assert r["ok"] and r["rms_px"] < 0.1 and all(p["used"] and p["turn_error_deg"] < 0.1 for p in r["relative"]), r
    # a pair 10 deg off: given up by default, kept in full when robust=False
    bad = service.fit_photos({**_request(), "relative": [_relative("s", "ne", 10)]})
    assert not bad["relative"][0]["used"] and bad["rms_px"] < 0.1
    kept = service.fit_photos({**_request(), "relative": [_relative("s", "ne", 10, robust=False)]})
    assert kept["relative"][0]["used"] and kept["rms_px"] > 1


def test_photo_in_two_groups():
    """ne (and s) are in two models; model b's pose of ne is 10 deg off: ne is kept in model a."""
    rel = [_relative("s", "ne", robust=False, group="a"), _relative("ne", "w", 10, robust=False, group="b"),
           _relative("w", "s", robust=False, group="b")]
    r = service.fit_photos({**_request(), "relative": rel})
    assert r["ok"] and r["rms_px"] < 0.5, r
    used = {(p["a"], p["b"]): p["used"] for p in r["relative"]}
    assert used[("s", "ne")] and not used[("ne", "w")]
    assert any("photo ne is in 2 models (a, b): kept in a" in w for w in r["warnings"])
    assert not any("deg away" in w for w in r["warnings"])


def test_sigma_with_an_opening_no_photo_sees():
    """An opening nobody sees left the normal matrix singular and every sigma missing ("±0.00 m")."""
    req = _request()
    req["openings"]["win_w"] = {"kind": "window", "wall": "west"}
    r = service.fit_photos(req)
    assert r["ok"] and {"width", "depth"} <= set(r["sigma_m"]), r["sigma_m"]
    assert any("win_w" in w and "not seen" in w for w in r["warnings"])


def test_wall_seen_by_one_photo():
    """The south wall only in photo ne: the room's depth rests on that one photo, so say so."""
    assert not any("rests on one photo" in w for w in service.fit_photos(_request())["warnings"])
    req = _request()
    w = req["photos"]["w"]
    w["points"] = [o for o in w["points"] if not o["at"].startswith("corner:se")]
    r = service.fit_photos(req)
    assert any("depth" in x and "one photo for the south wall (ne)" in x for x in r["warnings"]), r["warnings"]


def test_door_top_holds_the_scale():
    """No ceiling seen and the eye height 20% off: a door's top (2.0 m, seen) sets the scale."""
    rel = [_relative("s", "ne", robust=False), _relative("ne", "w", robust=False), _relative("s", "w", robust=False)]
    for eye in (1.2, 1.75):
        req = {**_no_ceiling(_request()), "eye_height": eye, "relative": rel}
        off = service.fit_photos({**req, "door_top": None})
        assert abs(off["room"]["width"] - W) > 0.6 and "size_priors" not in off, off["room"]
        on = service.fit_photos(req)
        # the eye height still pulls (it is a prior too): most of the 20% comes back
        assert abs(on["room"]["width"] - W) < 0.25 and abs(on["room"]["depth"] - D) < 0.2, on["room"]
        assert [d["of"] for d in on["size_priors"]] == ["door_s.top"] and abs(on["size_priors"][0]["fitted"] - 2) < 0.05


def test_typical_height_point():
    """A shared point at a typical height (z with z_sigma) holds the scale, and is fitted, not fixed."""
    req = _request()
    for c in req["photos"].values():
        c["points"] = [o for o in c["points"] if not o["at"].endswith(":ceiling")]
        c["lines"] = [o for o in c["lines"] if not o["along"].startswith("ceiling:")]
    req["points"] = {"shelf": {"z": 2.0, "z_sigma": 0.05}}
    r = service.fit_photos({**req, "eye_height": 1.2, "door_top": None})
    assert abs(r["room"]["width"] - W) < 0.05 and abs(r["room"]["depth"] - D) < 0.05, r["room"]
    assert [d["of"] for d in r["size_priors"]] == ["shelf.z"]
    req["points"] = {"shelf": {"z": 1.8, "z_sigma": 0.05}}  # 10% low: the room comes out ~10% small, with a warning
    r = service.fit_photos({**req, "door_top": None})
    assert 0.85 * W < r["room"]["width"] < 0.95 * W, r["room"]


def test_untypical_door_warns():
    """The ceiling sets the scale; a door_top far from the fitted door is reported, not forced."""
    r = service.fit_photos({**_request(), "door_top": 2.4})
    assert abs(r["room"]["width"] - W) < 0.05, r["room"]
    assert any("door_s.top came out" in w for w in r["warnings"]), r["warnings"]
    assert not any("came out" in w for w in service.fit_photos(_request())["warnings"])
    # a door whose top is not seen gets no prior
    req = _request()
    for c in req["photos"].values():
        c["points"] = [o for o in c["points"] if not o["at"].startswith("door_s:")]
    assert "size_priors" not in service.fit_photos(req)


def test_camera_in_the_doorway(monkeypatch):
    """A frame taken from beyond the door, looking in through it: outside, but told apart as a doorway."""
    monkeypatch.setitem(CAMS, "h", ((0.7, -0.6, 1.45), (0.9, 3.6, 1.2), 75.0))
    monkeypatch.setitem(FACING, "h", "north")
    req = _request()
    pos = CAMS["h"][0]
    lm = {"corner:nw:floor": (0, D, 0), "corner:nw:ceiling": (0, D, H), "corner:ne:floor": (W, D, 0),
          **{f"win_n:{e}:{k}": (2.25 + (0.8 if e == "end" else -0.8), D, 2.0 if k == "top" else 0.8)
             for e in ("start", "end") for k in ("top", "bottom")}}

    def through_door(X):
        t = -pos[1] / (X[1] - pos[1])
        a, z = pos[0] + t * (X[0] - pos[0]), pos[2] + t * (X[2] - pos[2])
        return 0.3 <= a <= 1.1 and 0 <= z <= 2.0

    req["photos"]["h"]["points"] = [{"at": at, "px": _proj("h", X)} for at, X in lm.items() if through_door(X)]
    req["photos"]["h"]["lines"] = []
    assert len(req["photos"]["h"]["points"]) >= 5
    r = service.fit_photos(req)
    assert r["rms_px"] < 0.5, r["worst"]
    (o,) = r["outside"]
    assert (o["photo"], o["wall"], o["verdict"], o["solid"]) == ("h", "south", "doorway", 0), o
    assert o["through"]["door_s"] >= 5 and abs(o["beyond_m"] - 0.6) < 0.05 and "held_inside" not in o
    assert any("looking in through door_s" in w for w in r["warnings"]), r["warnings"]


def test_camera_beyond_a_misplaced_wall(monkeypatch):
    """A frame fitted beyond the south wall that sees the room through the wall itself: the wall is placed wrong
    (or the frame's landmarks are). The fit held inside is compared in the warning."""
    monkeypatch.setitem(CAMS, "s", ((2.6, -0.35, 1.45), (2.3, 3.6, 0.9), 75.0))
    r = service.fit_photos(_request())
    (o,) = r["outside"]
    assert (o["photo"], o["wall"], o["verdict"]) == ("s", "south", "wall") and o["solid"] >= 5, o
    h = o["wall_moved_out"]
    # the other photos see this south wall: moved out past the camera, their landmarks no longer fit
    assert h["rms_px_was"] < 0.5 < h["rms_px"] and h["position"][1] > -0.05, h
    assert h["room"][1] > h["room_was"][1] + 0.3, h  # the wall moved, not the camera (kept from the north wall)
    assert abs((h["room"][1] - h["position"][1]) - (D - CAMS["s"][0][1])) < 0.05, h
    assert not h["about_as_good"] and any("0.35 m too far in" in w and "south wall moved out" in w and "much worse" in w for w in r["warnings"]), r["warnings"]
    held = service.fit_photos({**_request(), "inside": ["s"]})
    assert "outside" not in held and held["cameras"]["s"]["position"][1] >= 0.0
    assert not service.fit_photos({**_request(), "inside": ["x"]})["ok"]


def test_swapped_opening_ends_stay_positive():
    """start / end of a window swapped in every photo: the width stays > 0 and the fit warns."""
    req = _request()
    for c in req["photos"].values():
        for o in c["points"]:
            if o["at"].startswith("win_e:"):
                o["at"] = o["at"].replace(":start:", ":x:").replace(":end:", ":start:").replace(":x:", ":end:")
    r = service.fit_photos(req)
    assert r["ok"], r
    w = {o["id"]: o for o in r["room"]["openings"]}["win_e"]["width"]
    assert 0 < w < pf.NARROW_M, w
    assert any("win_e: only" in s and "likely swapped" in s and "south end of the east wall" in s for s in r["warnings"]), r["warnings"]
    sw = {o["id"]: o for o in r["room"]["openings"]}["win_e"]["swapped"]
    assert sw["rms_px"] < 0.5 and abs(sw["width"] - OPS["win_e"][3]) < 0.02
    ok = service.fit_photos(_request())
    assert not any("wide" in s or "edge is marked" in s for s in ok["warnings"])


def test_one_edge_of_an_opening():
    """Only the end of a window is marked: its width is held near the guess, not pulled to 0."""
    req = _request()
    for c in req["photos"].values():
        c["points"] = [o for o in c["points"] if not o["at"].startswith("win_e:start:")]
    r = service.fit_photos(req)
    assert r["ok"], r
    w = {o["id"]: o for o in r["room"]["openings"]}["win_e"]["width"]
    assert abs(w - 1.2) < 0.05, w
    assert any("win_e: only its end edge is marked" in s and "win_e:start, the south end" in s for s in r["warnings"])
    assert not any("win_e: only 0" in s for s in r["warnings"])


def test_room_rejects_a_non_positive_opening_width():
    from pydantic import ValidationError

    from agentic_sim_world_gen.scene.spec import Room

    for w in (0.0, -0.26):
        with pytest.raises(ValidationError):
            Room(width=3, depth=3, openings=[{"kind": "window", "wall": "east", "offset": 1.35, "width": w}])


def test_landmarks_on_something_else():
    """One photo marks a corner of a cabinet 0.8 m in front of a wall as the room's corner (a wardrobe's door
    taken for the room's door): fitted without that corner the rest agree, so the fit names it and what the room
    would be. Its own lines on the same wall help a little too, but only the corner explains it."""
    def rich():  # floor / ceiling lines every 0.5 m, so one landmark less still leaves the room measured
        req = _request()
        for cid, p in req["photos"].items():
            p["lines"] = [{"along": f"{k}:{w}", "px": q} for w in pf.WALLS for k, z in (("floor", 0.0), ("ceiling", H))
                          for a in (0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0) if (q := _proj(cid, _wall(w, a, z)))]
        return req
    req, cid, corner = rich(), "w", "se"
    for o in req["photos"][cid]["points"]:
        if o["at"].startswith(f"corner:{corner}:"):
            q = _proj(cid, (W, 0.8, 0.0 if o["at"].endswith(":floor") else H))
            assert q
            o["px"] = q
    r = service.fit_photos(req)
    assert r["ok"] and r["rms_px"] > pf.SUSPECT_RMS_PX, r["rms_px"]
    (sus,) = r["suspects"]
    assert (sus["photo"], sus["object"]) == (cid, f"corner:{corner}"), sus
    assert sus["rms_px"] < 1 and abs(sus["room"][1] - D) < 0.05 and math.dist(sus["position"], CAMS[cid][0]) < 0.05, sus
    assert any(f"photo {cid}: its corner:{corner} landmarks" in w and "relabel" in w for w in r["warnings"]), r["warnings"]
    assert "suspects" not in service.fit_photos(rich())
