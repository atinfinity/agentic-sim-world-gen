"""Variations of a scene, no network."""
import json
import random

import pytest

from test_preview_page import scene  # noqa: F401  (fixture: a 4x3 m study with a desk and a chair)
from agentic_sim_world_gen import service, variants


def test_variants_change_and_are_written(scene):  # noqa: F811
    service.set_room(scene, place="home", floor="wood_light", walls="paint_white")
    r = service.make_variants(scene, count=3, seed=1, compose=False)
    assert r["ok"] and len(r["variants"]) == 3 and r["source_issues"] == 0
    out = scene / service.VARIANTS_DIR
    assert (out / "index.html").read_text().count("<section>") == 3
    for v in r["variants"]:
        d = out / v["name"]
        s = service.load(d)
        assert s.spec.name.startswith("study_v") and (d / "layout.svg").exists()
        assert v["issues"] == 0 and v["changes"], v
        meta = json.loads((d / "variant.json").read_text())
        assert meta["seed"] == 1 and meta["changes"]
        assert any(a.item == "variant" for a in s.spec.assumptions)
        room = s.spec.room
        assert 3.6 <= room.width <= 4.4 and 2.7 <= room.depth <= 3.3
        assert room.floor in variants.FLOORS["home"] and room.floor != "wood_light"
        assert room.walls in variants.WALLS["home"] and room.walls != "paint_white"
        assert s.obj("chair").layout.in_front_of == "desk"  # relations are kept
    # the source is not changed
    assert service.load(scene).spec.room.width == 4 and service.load(scene).spec.room.floor == "wood_light"
    # the same seed gives the same variants
    again = service.make_variants(scene, count=3, seed=1, compose=False)
    assert [v["changes"] for v in again["variants"]] == [v["changes"] for v in r["variants"]]


def test_assets_are_drawn_among_good_candidates(scene):  # noqa: F811
    s = service.load(scene)
    p = next(p for p in s.placements if p.object_id == "desk")
    assert len(p.candidates) == 2
    seen = set()
    for k in range(20):
        c = s.model_copy(deep=True)
        variants.vary(c, random.Random(k), ["assets"])
        seen.add(next(p for p in c.placements if p.object_id == "desk").selected)
    assert seen == {0, 1}
    p.candidates[1]["match"] = {**(p.candidates[1].get("match") or {}), "weak": True}
    for k in range(10):
        c = s.model_copy(deep=True)
        assert variants.vary(c, random.Random(k), ["assets"]) == []


def test_layout_moves_wall_objects_only(scene):  # noqa: F811
    s = service.load(scene)
    ch = variants.vary(s, random.Random(3), ["layout"])
    assert [x["object"] for x in ch] == ["desk"]
    assert 0.1 <= s.obj("desk").layout.along <= 0.9


def test_more_issues_are_drawn_again(scene, monkeypatch):  # noqa: F811
    calls = []
    real = service._issues

    def fake(s):
        calls.append(s.spec.room.width)
        return real(s) + ([{"kind": "x", "objects": [], "detail": ""}] if len(calls) in (2, 3) else [])

    monkeypatch.setattr(service, "_issues", fake)
    r = service.make_variants(scene, count=1, seed=0, compose=False, tries=4)
    assert r["variants"][0]["tries"] == 3 and not r["variants"][0].get("more_issues")


def test_bad_vary_and_nothing_to_vary(scene):  # noqa: F811
    with pytest.raises(ValueError):
        service.make_variants(scene, count=1, vary=["colors"], compose=False)
    r = service.make_variants(scene, count=1, vary=["size"], size_jitter=0.0, compose=False)
    assert any("nothing to vary" in w for w in r["warnings"])


def test_relative_paths_become_absolute(tmp_path):
    (tmp_path / "tex.png").write_bytes(b"x")
    d = {"room": {"floor": {"texture": "tex.png"}, "walls": {"texture": "builtin:brick"}},
         "inputs": [{"path": "missing.jpg"}]}
    out = variants.absolute_paths(d, tmp_path)
    assert out["room"]["floor"]["texture"] == str(tmp_path / "tex.png")
    assert out["room"]["walls"]["texture"] == "builtin:brick" and out["inputs"][0]["path"] == "missing.jpg"


def test_old_candidates_are_checked_again(scene):  # noqa: F811
    """An older scene has no off_size on its candidates: a 10 cm "desk" is still not drawn."""
    s = service.load(scene)
    p = next(p for p in s.placements if p.object_id == "desk")
    p.candidates[1]["bbox_min"], p.candidates[1]["bbox_max"] = [0, 0, 0], [0.1, 0.08, 0.05]
    p.candidates[1].get("match", {}).pop("off_size", None)
    for k in range(10):
        c = s.model_copy(deep=True)
        assert variants.vary(c, random.Random(k), ["assets"]) == []


def test_lower_matches_are_not_drawn(scene):  # noqa: F811
    s = service.load(scene)
    p = next(p for p in s.placements if p.object_id == "desk")
    p.candidates[1]["match"] = {**p.candidates[0]["match"], "score": p.candidates[0]["match"]["score"] - 0.5}
    for k in range(10):
        c = s.model_copy(deep=True)
        assert variants.vary(c, random.Random(k), ["assets"]) == []
