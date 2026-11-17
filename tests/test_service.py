"""Service layer and MCP server, with a fake asset provider (no network)."""

import asyncio
import json

import pytest

from agentic_sim_world_gen import service
from agentic_sim_world_gen.assets.record import AssetRecord
from agentic_sim_world_gen.scene.spec import ObjectSpec


def rec(name, w, d, h, caption=""):
    return AssetRecord(
        id=name, source="fake", uri=f"https://example.com/{name}.usd", name=name,
        bbox_min=(-w / 2, -d / 2, 0.0), bbox_max=(w / 2, d / 2, h), up_axis="Z", meters_per_unit=1.0,
        caption=caption, license="CC-BY 4.0",
    )


CATALOG = {
    "office desk": [rec("paper_ream", 0.21, 0.3, 0.06), rec("desk_a", 0.8, 1.8, 0.76), rec("desk_b", 1.4, 0.7, 0.74)],
    "office chair": [rec("chair_a", 0.62, 0.71, 1.09), rec("chair_b", 0.55, 0.55, 0.85)],
    "bookshelf": [rec("shelf_wood", 0.79, 0.35, 1.8), rec("shelf_white", 0.37, 0.83, 2.0)],
    "wooden bookshelf": [rec("shelf_wood", 0.79, 0.35, 1.8)],
    "trash can": [rec("bin", 0.3, 0.3, 0.4)],
}


class FakeProvider:
    name = "fake"

    def __init__(self):
        self.calls = []

    def search(self, query, *, limit=10, size=None):
        self.calls.append((query, limit, size))
        return CATALOG.get(query, [])[:limit]


@pytest.fixture
def fake():
    p = FakeProvider()
    service.set_provider(p)
    yield p
    service.set_provider(None)


SPEC = {
    "name": "study",
    "room": {"name": "書斎", "width": 3.64, "depth": 2.73, "height": 2.4},
    "objects": [
        {"id": "desk", "query": "office desk", "position": [1.0, 2.3], "yaw_deg": 90,
         "min_size": [1.0, 0.5, 0.65], "max_size": [1.9, 1.0, 0.85]},
        {"id": "chair", "query": "office chair", "position": [1.0, 1.4]},
        {"id": "bookshelf", "query": "bookshelf", "position": [3.35, 1.2]},
    ],
}


def test_flow(fake, tmp_path):
    d = tmp_path / "study"
    st = service.create_scene(SPEC, d)
    names = {o["id"]: o["candidates"][o["selected"]]["name"] for o in st["objects"]}
    assert names == {"desk": "desk_a", "chair": "chair_a", "bookshelf": "shelf_wood"}  # paper ream filtered
    assert [i["kind"] for i in st["issues"]] == ["out_of_room"]  # 0.79 m shelf sticks out of the wall
    assert (d / "candidates.html").exists() and (d / "layout.svg").exists()

    st = service.move_object(d, "bookshelf", yaw_deg=90)
    assert st["issues"] == []

    st = service.select_asset(d, "chair", 1)
    assert st["objects"][1]["selected"] == 1
    with pytest.raises(IndexError):
        service.select_asset(d, "chair", 5)

    st = service.research_object(d, "bookshelf", query="wooden bookshelf")
    assert [c["name"] for c in st["objects"][2]["candidates"]] == ["shelf_wood"]

    st = service.add_object(d, {"id": "bin", "query": "trash can", "position": [0.3, 0.3]})
    assert st["objects"][-1]["candidates"][0]["name"] == "bin"
    with pytest.raises(ValueError):
        service.add_object(d, {"id": "bin", "query": "trash can"})
    st = service.remove_object(d, "bin")
    assert [o["id"] for o in st["objects"]] == ["desk", "chair", "bookshelf"]

    # Several in one call and one history entry; all or none on a duplicate id
    import json
    n_hist = len(json.loads((d / "preview.json").read_text(encoding="utf-8"))["history"])
    st = service.add_objects(d, [{"id": "bin", "query": "trash can", "position": [0.3, 0.3]},
                                 {"id": "bin2", "query": "trash can", "position": [0.3, 1.0]}])
    assert st["added"] == ["bin", "bin2"] and [o["id"] for o in st["objects"]][-2:] == ["bin", "bin2"]
    hist = json.loads((d / "preview.json").read_text(encoding="utf-8"))["history"]
    assert len(hist) == n_hist + 1 and "2 点を追加" in hist[-1]["text"]
    with pytest.raises(ValueError):
        service.add_objects(d, [{"id": "bin3", "query": "trash can"}, {"id": "bin", "query": "trash can"}])
    with pytest.raises(ValueError):
        service.add_objects(d, [{"id": "x", "query": "a"}, {"id": "x", "query": "b"}])
    assert "bin3" not in {o.id for o in service.load(d).spec.objects}
    st = service.remove_object(d, ["bin", "bin2"])
    assert st["removed"] == ["bin", "bin2"] and [o["id"] for o in st["objects"]] == ["desk", "chair", "bookshelf"]
    with pytest.raises(KeyError):
        service.remove_object(d, ["desk", "nope"])
    assert "desk" in {o.id for o in service.load(d).spec.objects}

    # Several moves in one save; nothing changes when one of them is wrong
    n_hist = len(json.loads((d / "preview.json").read_text(encoding="utf-8"))["history"])
    st = service.move_objects(d, [{"id": "desk", "position": [1.0, 0.5]}, {"id": "chair", "position": [1.0, 1.2],
                                                                            "facing_deg": 270}])
    assert st["moved"] == ["desk", "chair"]
    objs = {o.id: o for o in service.load(d).spec.objects}
    assert objs["desk"].position == (1.0, 0.5) and objs["chair"].facing_deg == 270 and objs["chair"].layout is None
    hist = json.loads((d / "preview.json").read_text(encoding="utf-8"))["history"]
    assert len(hist) == n_hist + 1 and "2 点" in hist[-1]["text"]
    for bad in ([{"id": "desk", "position": [2, 2]}, {"id": "nope", "position": [1, 1]}],
                [{"id": "desk", "position": [2, 2]}, {"id": "chair", "yaw_deg": 0, "facing_deg": 0}],
                [{"id": "desk", "pos": [2, 2]}], []):
        with pytest.raises((KeyError, ValueError)):
            service.move_objects(d, bad)
    assert service.load(d).obj("desk").position == (1.0, 0.5)
    service.set_variants(d, ["desk", "chair"], {"PhysX": "Enabled"})
    assert all(service.load(d).obj(i).variants == {"PhysX": "Enabled"} for i in ("desk", "chair"))

    pytest.importorskip("pxr")
    r = service.compose_scene(d)
    assert r["usd"].endswith("scene.usda") and service.status(d)["usd"]


def test_check_without_isaac(fake, tmp_path, monkeypatch):
    monkeypatch.setenv("ISAACSIM_PATH", str(tmp_path / "nope"))
    monkeypatch.delenv("ISAACSIM_PYTHON", raising=False)
    d = tmp_path / "s"
    service.create_scene(SPEC, d)
    assert "compose_scene" in service.check_in_isaac(d)["error"]
    (d / "scene.usda").write_text("#usda 1.0\n")
    assert "Isaac Sim not found" in service.check_in_isaac(d)["error"]
    assert "Isaac Sim not found" in service.front_views(d)["error"]

    from agentic_sim_world_gen.mcp_server import mcp
    res = asyncio.run(mcp.call_tool("view_fronts", {"scene_dir": str(d)}))
    assert "Isaac Sim not found" in str(res)


def test_isaac_python_pip_venv_wins(tmp_path, monkeypatch):
    (tmp_path / "isaacsim").mkdir()
    (tmp_path / "isaacsim" / "python.sh").write_text("")
    (tmp_path / "python").write_text("")
    monkeypatch.setenv("ISAACSIM_PATH", str(tmp_path / "isaacsim"))
    monkeypatch.delenv("ISAACSIM_PYTHON", raising=False)
    assert service.isaac_python() == tmp_path / "isaacsim" / "python.sh"
    monkeypatch.setenv("ISAACSIM_PYTHON", str(tmp_path / "python"))
    assert service.isaac_python() == tmp_path / "python"
    monkeypatch.setenv("ISAACSIM_PYTHON", str(tmp_path / "missing"))
    assert service.isaac_python() is None


def test_mcp_tools(fake, tmp_path, monkeypatch):
    from agentic_sim_world_gen.mcp_server import mcp

    monkeypatch.setenv("ISAACSIM_PATH", str(tmp_path / "nope"))  # create_scene goes on to a draft look
    monkeypatch.delenv("ISAACSIM_PYTHON", raising=False)
    monkeypatch.setattr(service, "live_available", lambda timeout_s=0.5: False)

    async def run():
        tools = {t.name: t for t in await mcp.list_tools()}
        assert {"search_assets", "create_scene", "get_scene", "select_asset", "move_object",
                "research_object", "add_object", "add_objects", "remove_object", "move_objects", "compose_scene", "check_in_isaac"} <= set(tools)
        assert "room" in str(tools["create_scene"].input_schema)
        res = await mcp.call_tool("create_scene", {"spec": SPEC, "out_dir": str(tmp_path / "m")})
        return res

    res = asyncio.run(run())
    assert not getattr(res, "is_error", False), res
    assert "desk_a" in str(res)


def test_size_misses(fake, tmp_path, monkeypatch):
    monkeypatch.setitem(CATALOG, "office desk", [rec("paper_ream", 0.21, 0.3, 0.06),
                                                 rec("desk_a", 0.8, 1.8, 0.76, "A wooden office desk"),
                                                 rec("desk_b", 1.4, 0.7, 0.74, "A white desk")])
    spec = {**SPEC, "objects": [{**SPEC["objects"][0], "max_size": [1.3, 1.0, 0.85]}, SPEC["objects"][1]]}
    st = service.create_scene(spec, tmp_path / "s")
    desk, chair = st["objects"]
    assert desk["candidates"] == []
    assert [a["name"] for a in desk["size_misses"]["assets"]] == ["desk_a", "desk_b"]  # 1.8 / 1.4 m, dropped by max_size
    assert [a["off"] for a in desk["size_misses"]["assets"]] == [{"long": "+38% over max 1.3 m"},
                                                                  {"long": "+8% over max 1.3 m"}]
    assert "size_misses" not in chair  # no size limits
    b = service.brief(st)  # one line per object instead of two hints inside it
    line = b["substitute"][0]
    assert line["id"] == "desk" and line["nearest"] == ("desk_a 0.8×1.8×0.76 m (outside min/max_size: long +38% over max 1.3 m)"
                                                  "; also desk_b")  # every size miss by name
    assert "widen min_size/max_size" in line["try"] and "size_misses" not in b["objects"][0]
    assert "hint" not in service.brief(st, "desk")["objects"][0]["size_misses"]
    st = service.research_object(tmp_path / "s", "desk", max_size=[1.9, 1.0, 0.85])
    assert st["objects"][0]["candidates"][0]["name"] == "desk_a" and "size_misses" not in st["objects"][0]


def test_research_keeps_selection_until_user_picks(fake, tmp_path, monkeypatch):
    monkeypatch.setitem(CATALOG, "wooden bookshelf", [rec("cabinet_oak", 0.79, 0.35, 1.8, "A tall wooden storage cabinet")])
    monkeypatch.setitem(CATALOG, "blue office chair", [rec("chair_blue", 0.6, 0.6, 1.0, "A blue office chair")])
    d = tmp_path / "s"
    service.create_scene(SPEC, d)
    service.select_asset(d, "bookshelf", 1)  # shelf_white

    # Only substitutes (a cabinet, not a bookshelf): keep the current asset, offer the new ones.
    st = service.research_object(d, "bookshelf", query="wooden bookshelf")
    shelf = st["objects"][2]
    assert [c["name"] for c in shelf["candidates"]] == ["cabinet_oak", "shelf_white"]
    assert shelf["candidates"][shelf["selected"]]["name"] == "shelf_white"
    assert st["research"] == {"object_id": "bookshelf", "kept_current": True, "selected": "shelf_white"}
    assert shelf["candidates"][1]["match"]["weak"] and shelf["substitute"]  # re-scored against the new query

    # select=True takes the top anyway; a good match is taken by default.
    st = service.research_object(d, "bookshelf", query="wooden bookshelf", select=True)
    assert st["research"]["selected"] == "cabinet_oak" and not st["research"]["kept_current"]
    st = service.research_object(d, "chair", query="blue office chair")
    assert st["research"]["selected"] == "chair_blue"

    # select=False keeps it even when the new top matches.
    service.select_asset(d, "chair", 0)
    st = service.research_object(d, "chair", query="office chair", select=False)
    assert st["research"]["selected"] == "chair_blue" and st["research"]["kept_current"]


def test_size_off():
    # Register counter of the BIM example: BIM 1.5 x 0.6 x 1.0 m, the asset 1.54 x 0.66 x 1.39 m
    assert service.size_off((0.66, 1.54, 1.39), (1.2, 0.48, 0.8), (1.8, 0.72, 1.2)) == {"height": "+16% over max 1.2 m"}
    assert service.size_off((0.3, 0.2, 0.5), (1.2, 0.48, 0.8), None) == {
        "long": "-75% under min 1.2 m", "short": "-58% under min 0.48 m", "height": "-38% under min 0.8 m"}
    assert service.size_off((1.0, 0.5, 0.9), (0.8, 0.4, 0.7), (1.2, 0.6, 1.1)) == {}


def test_brief_keeps_selection_and_shortens_others():
    from agentic_sim_world_gen.service import brief

    cap = "あ" * 200
    st = {"scene_dir": "x", "objects": [
        {"id": "a", "label": "A", "z": 0.0, "layout": None, "selected": 1, "candidates": [
            {"index": 0, "name": "n0", "caption": cap, "size_m": [1.0, 0.5, 0.75], "license": "CC0-1.0",
             "match": {"score": 3.5, "matched": ["x"], "weak": False}},
            {"index": 1, "name": "n1", "caption": cap, "size_m": [1.2, 0.6, 0.7], "license": "CC0-1.0",
             "match": {"score": 1.0, "matched": [], "weak": True}},
        ] + [{"index": i, "name": f"n{i}", "size_m": [1, 1, 1], "match": None} for i in range(2, 9)]},
        {"id": "b", "selected": 0, "candidates": [{"index": 0, "name": "m0", "caption": cap, "size_m": [1, 1, 1]}]},
        {"id": "c", "selected": None, "candidates": []},
    ]}
    b = brief(st, focus="b")
    a = b["objects"][0]
    assert "candidates" not in a and "z" not in a and "layout" not in a
    assert a["asset"].startswith("[1] n1 ") and a["asset"].endswith("…") and "matched" not in a["asset"]
    assert a["others"][0] == "[0] n0 1×0.5×0.75 m score 3.5" and a["others"][-1] == "+5 more"
    assert b["objects"][1] is st["objects"][1]  # the focus object stays in full
    assert "asset" not in b["objects"][2] and "others" not in b["objects"][2]
    assert brief({"usd": "x"}) == {"usd": "x"}
    assert " weak CC0-1.0 — あ" in a["asset"]


def test_short_replies(tmp_path):
    """Fill and assumptions without defaults / preview options, the license report without per-asset origins,
    thumbnails no larger than THUMB_PX."""
    import io

    from PIL import Image

    from agentic_sim_world_gen.service import brief, license_brief

    st = {"objects": [{"id": "s", "fill": {"on": "shelf", "levels": None, "rows": None, "lay_flat": False, "count": 9,
                                           "gap": 0.01}, "selected": None, "candidates": []}],
          "assumptions": [{"item": "x", "value": "y", "given": False, "basis": "b", "options": ["y", "z"], "object": None}]}
    b = brief(st)
    assert b["objects"][0]["fill"] == {"on": "shelf", "count": 9, "gap": 0.01}
    assert b["assumptions"] == [{"item": "x", "value": "y", "given": False, "basis": "b"}]
    assert st["assumptions"][0]["options"] == ["y", "z"]  # the status itself is not changed

    origin = {"sha256": "0" * 64, "url": "https://example.com/" + "x" * 100}
    r = {"assets": {"CC0-1.0": [{"object_id": "a", "label": "棚", "origin": origin, "attribution": None},
                                {"object_id": "b", "label": "棚", "origin": origin, "attribution": None}],
                    "CC-BY-4.0": [{"object_id": "c", "label": "箱", "origin": origin, "attribution": '"box" by N, CC BY 4.0'}]},
         "sources": [{"from": "hf", "versions": {"abc": ["棚", "箱"]}, "count": 2}],
         "credits": [{"title": "Font", "license": "OFL", "used_by": ["棚", "箱"]}],
         "licenses": {"CC0-1.0": 2, "CC-BY-4.0": 1}, "warnings": ["w"], "file": "LICENSES.md"}
    lb = license_brief(r)
    assert lb["objects"] == {"CC0-1.0": ["棚"], "CC-BY-4.0": ["箱"]}
    assert lb["attribution_lines"] == ['"box" by N, CC BY 4.0'] and lb["warnings"] == ["w"] and lb["file"] == "LICENSES.md"
    assert lb["sources"] == [{"from": "hf", "versions": ["abc"], "count": 2}] and lb["credits"][0]["used_by"] == 2
    assert "sha256" not in json.dumps(lb)

    buf = io.BytesIO()
    Image.new("RGB", (1024, 512), "red").save(buf, "JPEG")
    small = service._shrink(buf.getvalue(), service.THUMB_PX)
    assert small[:3] == b"\xff\xd8\xff" and Image.open(io.BytesIO(small)).size == (256, 128)
    buf = io.BytesIO()
    Image.new("RGBA", (200, 100)).save(buf, "PNG")
    assert service._shrink(buf.getvalue(), 256) == buf.getvalue() and service._shrink(b"junk", 256) == b"junk"


def test_reply_shows_only_what_changed(fake, tmp_path):
    """An editing tool returns the objects and sections that changed since the last result, the focus
    object, the issues, hints and counts; get_scene / create_scene return everything."""
    d = tmp_path / "study"
    full = service.reply(service.create_scene(SPEC, d), full=True)
    assert len(full["objects"]) == 3 and "summary" not in full
    r = service.reply(service.move_object(d, "chair", position=[1.2, 1.4]), "chair")
    assert [o["id"] for o in r["objects"]] == ["chair"] and r["summary"]["unchanged"] == 2
    assert "issues" in r and "room" not in r and "room" in r["summary"]["unchanged_sections"]
    r = service.reply(service.status(d), "desk")  # nothing changed: only the focus object
    assert [o["id"] for o in r["objects"]] == ["desk"]
    r = service.reply(service.remove_object(d, "bookshelf"))
    assert r["objects"] == [] and r["removed"] == ["bookshelf"]
    assert len(service.reply(service.status(d), full=True)["objects"]) == 2
    assert len(json.dumps(r, ensure_ascii=False)) < len(json.dumps(full, ensure_ascii=False)) / 2


def test_decor_in_one_line():
    st = {"scene_dir": "x", "objects": [
        {"id": "decor_stock_shelf", "label": "チップス", "decor": True, "fill": {"on": "shelf", "count": 48},
         "selected": 0, "candidates": [{"index": 0, "name": "Chips Bag"}]},
        {"id": "decor_mug", "query": "mug", "decor": True, "layout": {"on": "desk"}, "selected": None, "candidates": []}]}
    b = service.brief(st)
    assert b["objects"] == [] and b["decor"] == {"decor_stock_shelf": "チップス (Chips Bag) ×48 on shelf",
                                                 "decor_mug": "mug (no asset) on desk"}


def test_reply_language(fake, tmp_path):
    """The user's language from what the agent wrote in their words, with a hint to answer in it."""
    d = tmp_path / "study"
    st = service.create_scene({**SPEC, "room": {**SPEC["room"], "name": "room"}}, d)
    assert "reply_language" not in st
    st = service.set_assumptions(d, [{"item": "size", "value": "6 畳", "given": True}])
    assert st["reply_language"] == "ja" and "日本語" in st["reply_language_hint"] and "圧縮" in st["reply_language_hint"]
    r = service.reply(service.move_object(d, "chair", position=[1.2, 1.4]), "chair")
    assert list(r)[:2] == ["reply_language", "reply_language_hint"] and r["reply_language"] == "ja"  # first
    assert list(service.reply(service.status(d), full=True))[0] == "reply_language"
    st = service.create_scene({**SPEC, "lang": "en"}, tmp_path / "en")
    assert st["reply_language"] == "en" and "English" in st["reply_language_hint"]
    assert "German" in service.language_hint("German") and "German" in service.language_hint("German", short=True)


def test_reply_names_the_hints_already_shown(fake, tmp_path, monkeypatch):
    """every hint went out again on every reply (window_view_hint 47 times in one run), after 5k characters
    of usability warnings, and was read past. A hint seen word for word is named in open_hints near the top."""
    d = tmp_path / "study"
    room = {**SPEC["room"], "name": "書斎", "openings": [{"kind": "window", "wall": "N", "offset": 1.0, "width": 1.2}]}
    full = service.reply(service.create_scene({**SPEC, "room": room}, d), full=True)
    keys = list(full)
    assert keys[:4] == ["reply_language", "reply_language_hint", "scene_dir", "issues"]
    assert keys.index("window_view_hint") < keys.index("usability") and "圧縮" in full["reply_language_hint"]
    r = service.reply(service.move_object(d, "chair", position=[1.2, 1.4]), "chair")
    assert "window_view_hint" not in r and "window_view_hint" in r["open_hints"]
    assert list(r)[:5] == ["reply_language", "reply_language_hint", "scene_dir", "issues", "open_hints"]
    assert r["reply_language_hint"] == service.language_hint("ja", short=True)  # in full on the first result only
    rows = [{"kind": "tight", "objects": ["chair"], "detail": f"warning {i}"} for i in range(8)]
    monkeypatch.setattr(service, "_usability", lambda s: rows)
    r = service.reply(service.status(d))
    assert len(r["usability"]) == 5 and r["usability_more"].startswith("+3 more (tight 3)")
    assert r["summary"]["usability"] == 8 and "window_view_hint" in r["open_hints"]
    assert "usability" not in service.reply(service.status(d))  # unchanged: not sent again
    assert len(service.reply(service.status(d), full=True)["usability"]) == 8  # get_scene lists them all
    service.set_room(d, place="office")  # the hint's text changes with the place: sent in full again
    r = service.reply(service.status(d))
    assert "window_view_hint" in r and "window_view_hint" not in r.get("open_hints", [])


def test_check_results_carry_reply_language(fake, tmp_path):
    """Results without the scene status (check_in_isaac, view_fronts, ...) repeat the language too."""
    d = tmp_path / "study"
    service.create_scene({**SPEC, "room": {**SPEC["room"], "name": "room"}}, d)
    assert service.with_language(d, {"ok": True}) == {"ok": True}  # no language known yet
    service.set_assumptions(d, [{"item": "size", "value": "6 畳", "given": True}])
    r = service.with_language(d, {"ok": True, "verdict": "passed"})
    assert r["reply_language"] == "ja" and "Passed." in r["reply_language_hint"] and r["verdict"] == "passed"
    assert list(r)[:2] == ["reply_language", "reply_language_hint"]  # before the English of the result
    assert "日本語" in r["reply_language_hint"] and "圧縮" in r["reply_language_hint"]  # in the user's language
    assert service.with_language(d, {"reply_language": "en"}) == {"reply_language": "en"}
    assert service.with_language(d, ["text"]) == ["text"]
    assert service.with_language(tmp_path / "missing", {"ok": False}) == {"ok": False}
    assert service.latest_language(tmp_path) == "ja" and service.latest_language(tmp_path / "missing") is None
    service.create_scene({**SPEC, "lang": "en"}, tmp_path / "newer")
    assert service.latest_language(tmp_path) == "en"


def test_move_object_facing_deg(fake, tmp_path, monkeypatch):
    """facing_deg is a world direction: a chair whose front is -Y and a desk whose front is +X both face east
    with facing_deg=0; swapping the asset keeps the facing, a raw yaw_deg clears it."""
    chair_back = rec("chair_back", 0.62, 0.71, 1.09)
    chair_back.front_deg = 270.0
    monkeypatch.setitem(CATALOG, "office chair", [chair_back, rec("chair_b", 0.55, 0.55, 0.85)])
    d = tmp_path / "study"
    service.create_scene(SPEC, d)
    with pytest.raises(ValueError):
        service.move_object(d, "chair", yaw_deg=0, facing_deg=0)

    st = service.move_object(d, "chair", position=[1.0, 1.0], facing_deg=0)
    chair = next(o for o in st["objects"] if o["id"] == "chair")
    assert (chair["yaw_deg"], chair["facing_deg"], chair["asset_front_deg"]) == (90.0, 0.0, 270.0)
    st = service.move_object(d, "desk", facing_deg=0)
    desk = next(o for o in st["objects"] if o["id"] == "desk")
    assert (desk["yaw_deg"], desk["facing_deg"]) == (0.0, 0.0)

    st = service.select_asset(d, "chair", 1)  # front +X: the yaw follows, the facing stays
    chair = next(o for o in st["objects"] if o["id"] == "chair")
    assert (chair["yaw_deg"], chair["facing_deg"]) == (0.0, 0.0)

    service.select_asset(d, "chair", 0)
    st = service.move_object(d, "chair", yaw_deg=0)  # raw yaw: the -Y front now faces south
    chair = next(o for o in st["objects"] if o["id"] == "chair")
    assert (chair["yaw_deg"], chair["facing_deg"]) == (0.0, -90.0)
    assert service.load(d).obj("chair").facing_deg is None


def test_misaligned_arrays(fake, tmp_path, monkeypatch):
    """A desk island and its chair rows on one spot with the same yaw but different asset fronts."""
    chair_back = rec("chair_back", 0.62, 0.71, 1.09)
    chair_back.front_deg = 270.0
    monkeypatch.setitem(CATALOG, "office chair", [chair_back])
    spec = {
        "name": "office", "room": {"name": "office", "width": 8, "depth": 8, "height": 2.7},
        "objects": [
            {"id": "desks", "query": "office desk", "position": [4, 4], "array": {"count": 2, "rows": 2, "back_to_back": True},
             "min_size": [1.0, 0.5, 0.65], "max_size": [1.9, 1.0, 0.85]},
            {"id": "chairs", "query": "office chair", "position": [4, 4],
             "array": {"count": 2, "rows": 2, "alternate": True, "aisle": 3.0}},
        ],
    }
    st = service.create_scene(spec, tmp_path / "o")
    assert [(i["kind"], i["objects"]) for i in st["issues"] if i["kind"] == "misaligned_array"] == [
        ("misaligned_array", ["desks", "chairs"])]
    service.move_object(tmp_path / "o", "desks", facing_deg=0)
    st = service.move_object(tmp_path / "o", "chairs", facing_deg=0)
    assert not [i for i in st["issues"] if i["kind"] == "misaligned_array"]


def test_set_collider(fake, tmp_path):
    d = service.create_scene(SPEC, tmp_path / "c")["scene_dir"]
    oid = service.status(d)["objects"][0]["id"]
    st = service.set_collider(d, oid, "box")
    assert next(o for o in st["objects"] if o["id"] == oid)["collider"] == "box (set_collider)"
    with pytest.raises(ValueError):
        service.set_collider(d, oid, "mesh")


def test_choices_log(fake, tmp_path, monkeypatch):
    """Every swap goes to choices.jsonl with the query, the candidate order and why."""
    import importlib.util
    import json

    d = tmp_path / "study"
    service.create_scene(SPEC, d)
    service.select_asset(d, "chair", 1, reason="match")
    service.select_asset(d, "chair", 0, reason="look")
    with pytest.raises(ValueError):
        service.select_asset(d, "chair", 1, reason="nicer")
    recs = [json.loads(x) for x in (d / service.CHOICES).read_text().splitlines()]
    assert [(r["from"], r["to"], r["reason"], r["by"]) for r in recs] == [(0, 1, "match", "agent"), (1, 0, "look", "agent")]
    assert recs[0]["query"] == "office chair" and len(recs[0]["candidates"]) >= 2

    spec = importlib.util.spec_from_file_location("choices", "scripts/search_eval/choices.py")
    ch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ch)
    got, skipped = ch.entries_from_log(d / service.CHOICES)
    assert skipped == 1 and [e["source"] for e in got] == ["reason:match"]
    assert ch.is_signal({"by": "preview"}) and ch.is_signal({"correction": True}) and not ch.is_signal({"reason": "fit"})
    merged = ch.merge([], got + got)
    assert len(merged) == 1 and merged[0]["count"] == 2

    home = str(__import__("pathlib").Path.home())
    assert service._anon(home + "/assets/a.usd") == "~/assets/a.usd" and service._anon("https://x/a.usd") == "https://x/a.usd"


def test_windows_with_nothing_outside(fake, tmp_path):
    # A window in a room without a site shows a gray void; site=true adds the default outside
    d = tmp_path / "s"
    st = service.create_scene(SPEC, d)
    assert "window_view" not in st
    room = {**SPEC["room"], "openings": [{"kind": "window", "wall": "N", "offset": 1.0, "width": 1.2}]}
    st = service.create_scene({**SPEC, "room": room}, d)
    assert st["window_view"] == {"openings": ["window_0"], "stops_finish": True}
    assert "site=true" in st["window_view_hint"] and "grass" in st["window_view_hint"]
    assert 'item: "window_view"' in st["window_view_hint"]  # the way out when the windows are not seen
    service.set_assumptions(d, [{"item": "window_view", "value": "窓はカーテンで隠れる"}])
    assert "window_view" not in service.status(d)
    service.set_assumptions(d, [], remove=["window_view"])
    service.set_room(d, site=True)
    st = service.status(d)
    assert "window_view" not in st and service.load(d).spec.room.site is not None


def test_heavy_candidates_go_after_lighter_ones(fake, monkeypatch):
    """A 2-million-face scanned ball went first; a lighter ball of the same standing should."""
    scan = rec("ball_scan", 0.22, 0.22, 0.22, caption="soccer ball")
    plain = rec("ball_plain", 0.22, 0.22, 0.22, caption="soccer ball")
    sofa = rec("sofa_scan", 2.0, 0.9, 0.8, caption="sofa")
    scan.polygons, plain.polygons, sofa.polygons = 2_000_000, 8_000, 1_500_000
    monkeypatch.setitem(CATALOG, "soccer ball", [scan, plain])
    monkeypatch.setitem(CATALOG, "sofa", [sofa])
    got = service._find_candidates(ObjectSpec(id="b", query="soccer ball"), 4)
    assert [c["name"] for c in got] == ["ball_plain", "ball_scan"]
    assert got[1]["match"]["heavy"] == 2_000_000 and "heavy" not in got[0]["match"]
    assert service._find_candidates(ObjectSpec(id="s", query="sofa"), 4)[0]["match"].get("heavy") is None  # big things may
    assert scan.summary()["faces"] == 2_000_000
    c = {**got[1], "index": 1, "size_m": [0.22, 0.22, 0.22]}
    assert "heavy:2.0M faces" in service._one_line(c)


def test_status_shows_sources(fake, tmp_path):
    """Status says which asset sources are searched."""
    st = service.create_scene(SPEC, tmp_path / "s")
    assert st["sources"] == {"used": [service._SOURCES["custom"]]}


def test_select_a_searched_asset(fake, tmp_path):
    """An asset from search_assets can be selected by uri although the object's own search did not find it."""
    st = service.create_scene(SPEC, tmp_path / "s")
    found = service.search_assets("trash can")
    st = service.select_asset(st["scene_dir"], "bookshelf", uri=found[0]["uri"])
    p = next(x for x in service.load(st["scene_dir"]).placements if x.object_id == "bookshelf")
    assert p.candidates[p.selected]["uri"] == found[0]["uri"]
    with pytest.raises(KeyError, match="search_assets"):
        service.select_asset(st["scene_dir"], "bookshelf", uri="https://example.com/never_searched.usd")


def test_reply_language_from_the_room_name(fake, tmp_path):
    """#29: the room can be fitted before any furniture is placed, so its name tells the language."""
    st = service.create_scene({**SPEC, "objects": []}, tmp_path / "coffee")  # the room is 書斎
    assert st["reply_language"] == "ja"
    st = service.create_scene({**SPEC, "objects": [], "room": {**SPEC["room"], "name": "room"}}, tmp_path / "plain")
    assert "reply_language" not in st  # the default room name says nothing


def test_blind_over_a_wide_window():
    """a 2.5 m classroom window got a wall display for a blind. The 2 m blinds fell under the size limit
    (stretched x1.35, past the window's 1.33) and "roll screen" matched the display's 'screen' tag."""
    from agentic_sim_world_gen.scene.plan import Opening
    from agentic_sim_world_gen.scene.spec import Placement

    op = Opening(id="win_a", kind="window", wall="west", offset=3.4, width=2.49, height=1.43, sill=0.9)
    lo, hi = service._cover_size(op, 2.9)
    assert lo[0] < 2.0 < hi[0]  # a 2 m blind fits
    q, alt = service.cover_query(op, 2.9, "blind")
    assert not any("screen" in w for w in [q, *alt])
    ok = {"score": 2.9, "weak": False}
    # as SimReady returns it: no class, the caption says what it is
    display = {"name": "sm_digitalSystem_displayPanel_wall_a01_01.usd", "class": None, "match": ok,
               "caption": "Black wall-mounted flat-panel display with a thin bezel"}
    assert "another kind" in service._unfit_window_asset(Placement(object_id="b", candidates=[display]))
    # windows / covers, and third-party windows with a generic class stay
    for c in ({"name": "sm_blinds_window_2m_open_a01_01.usd", "class": "window shade"},
              {"name": "SM_Window_Sash_01.usd", "class": "sash"}, {"name": "mado_01.usd"},
              {"name": "mado_01.usd", "class": "environment"}, {"name": "glass_front_02.usd", "class": "facade"},
              {"name": "jidou_01.usd", "class": "door"},
              {"name": "sm_blinds_window_4m_a01.usd", "caption": "Display of white window blinds"}):
        assert service._unfit_window_asset(Placement(object_id="b", candidates=[{**c, "match": ok}])) is None


def test_reply_sends_the_tools_own_hint(fake, tmp_path):
    """a tool's own `hint` / `next` is about this call: always sent, never only named."""
    d = tmp_path / "s"
    service.reply(service.create_scene(SPEC, d), full=True)
    r1 = service.reply(service.set_assumptions(d, [{"item": "pink wall", "value": "ピンク"}]))
    r2 = service.reply(service.set_assumptions(d, [{"item": "pink wall", "value": "ピンク"}]))
    assert "hint" in r1 and r2.get("hint") == r1["hint"] and "hint" not in r2.get("open_hints", {})


def test_reply_starts_over_in_a_new_server_process(fake, tmp_path, monkeypatch):
    """.last_reply.json outlives the MCP server; a new process (a new session) gets every hint in full,
    and a hint only named keeps its first sentence."""
    d = tmp_path / "w"
    room = {**SPEC["room"], "openings": [{"kind": "window", "wall": "N", "offset": 1.0, "width": 1.2}]}
    service.reply(service.create_scene({**SPEC, "room": room}, d), full=True)
    r = service.reply(service.move_object(d, "chair", position=[1.2, 1.4]), "chair")
    assert r["open_hints"]["window_view_hint"].startswith("window_view")
    assert len(r["open_hints"]["window_view_hint"]) <= service.REPLY_GIST
    monkeypatch.setattr(service, "_PROCESS", "another")
    r = service.reply(service.move_object(d, "chair", position=[1.3, 1.4]), "chair")
    assert "window_view_hint" in r and "open_hints" not in r


def test_window_view_assumption_wording(fake, tmp_path):
    """「窓の外の景色」 and a row on one window count as the window_view assumption too."""
    room = {**SPEC["room"], "openings": [{"id": "w1", "kind": "window", "wall": "N", "offset": 1.0, "width": 1.2}]}
    for i, row in enumerate([{"item": "窓の外の景色", "value": "カーテンで隠れる"},
                             {"item": "window_view", "object": "w1", "value": "隠れる"}]):
        d = tmp_path / f"w{i}"
        service.create_scene({**SPEC, "room": room}, d)
        assert "window_view" in service.status(d)
        service.set_assumptions(d, [row])
        assert "window_view" not in service.status(d)


def test_reply_is_short_on_repeated_edits(fake, tmp_path):
    """each edit sent the same issues with their advice and the same brief again (3-4.5k characters a call,
    50 edits in a 46-minute run, 4 compactions). Unchanged issues go as one short line each, the brief once."""
    d = tmp_path / "study"
    service.reply(service.create_scene(SPEC, d), full=True)
    rows = [{"kind": "overlap", "objects": ["desk", "chair"], "detail": "penetration 0.44 m: move one of them"}]
    import agentic_sim_world_gen.service as svc

    st = svc.status(d)
    r1 = service.reply({**st, "issues": rows})
    assert r1["issues"] == rows and "only what changed" in r1["brief"]  # new issues in full; the brief once after the full result
    r2 = service.reply({**svc.status(d), "issues": rows})
    assert r2["issues"] == ["overlap: desk, chair (as before)"] and "only what changed" not in r2.get("brief", "")
    assert r2["summary"]["issues"] == 1
    assert service.reply({**svc.status(d), "issues": []})["issues"] == []  # a change is sent as it is


def test_several_focus_objects_are_one_line_per_candidate(fake, tmp_path):
    """add_objects of 15 objects returned every candidate with its match and size misses (25k characters).
    With several new objects each candidate is one line; one object (add_object, get_scene) stays in full."""
    d = tmp_path / "study"
    st = service.create_scene(SPEC, d)
    ids = [o["id"] for o in st["objects"]][:2]
    b = service.brief(st, ids)
    for o in (x for x in b["objects"] if x["id"] in ids):
        assert "candidates" not in o and all(isinstance(c, str) for c in o.get("others", []))
    one = service.brief(st, ids[0])
    assert "candidates" in next(o for o in one["objects"] if o["id"] == ids[0])


def test_surface_candidates_are_one_line_each():
    found = {"floor": {"query": "beige linoleum", "alt_queries": [], "selected": 1, "candidates": [
        {"index": 0, "id": "PolyHaven/wood_table_001", "license": "CC0-1.0", "keywords": ["wood"] * 12,
         "url": "https://polyhaven.com/a/wood_table_001", "match": {"score": 0.6, "matched": ["linoleum"]}},
        {"index": 1, "id": "Base/Carpet/Carpet_Beige", "color": "#b9af9f", "license": "NVIDIA-Omniverse-Materials",
         "match": {"score": 0.4, "weak": True}}]}}
    s = service._surfaces_short(found)["floor"]
    assert s["candidates"] == ["[0] PolyHaven/wood_table_001 CC0-1.0 score 0.6",
                               "[1] Base/Carpet/Carpet_Beige #b9af9f NVIDIA-Omniverse-Materials score 0.4 weak"]
    assert s["selected"] == 1 and s["query"] == "beige linoleum" and "alt_queries" not in s


def test_compare_measure_leaves_the_gaps_to_the_file():
    m = {"camera": {"position": [1, 2, 1.5], "stands": {"width": 1.45, "left_by": "a", "right_by": "b",
                                                        "gaps": [{"from": -1, "to": 0, "width": 1}]}},
         "across": [{"ahead_m": 1.0, "width": 1.45, "left_by": "a", "right_by": "b", "gaps": [{"width": 1}]}],
         "arrays_in_view": {"desks": "3 of 12 units"}, "notes": ["n"]}
    s = service._measure_short(m)
    assert "gaps" not in s["camera"]["stands"] and "gaps" not in s["across"][0]
    assert s["camera"]["stands"]["width"] == 1.45 and s["notes"] == ["n"] and "gaps" in m["across"][0]
