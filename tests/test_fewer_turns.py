"""create_scene goes on to the first draft, finish closes, view_candidates shows several objects at once."""

import asyncio
import io
import json

import pytest

from agentic_sim_world_gen import service
from tests.test_layout import rec

CATALOG = {"desk": [rec("desk", "desk", 1.4, 0.7, 0.74), rec("desk_b", "desk", 1.2, 0.6, 0.72)],
           "office chair": [rec("chair", "office chair", 0.55, 0.55, 0.85)]}
SPEC = {"name": "t", "room": {"width": 3.64, "depth": 2.73, "height": 2.4}, "objects": [
    {"id": "desk", "label": "机", "query": "desk", "layout": {"against_wall": "north"}},
    {"id": "chair", "label": "椅子", "query": "office chair", "layout": {"in_front_of": "desk"}},
]}


@pytest.fixture(autouse=True)
def fake(tmp_path, monkeypatch):
    service.set_provider(type("P", (), {"name": "f", "search": lambda self, q, limit=10, size=None: CATALOG.get(q, [])})())
    monkeypatch.setenv("ISAACSIM_PATH", str(tmp_path / "nope"))
    monkeypatch.delenv("ISAACSIM_PYTHON", raising=False)
    monkeypatch.setattr(service, "live_available", lambda timeout_s=0.5: False)
    yield
    service.set_provider(None)


def isaac(monkeypatch, verdicts):
    """An Isaac Sim that answers with these verdicts, one per check; returns the calls it got."""
    calls = []

    def check(scene, steps=240, timeout_s=900, gif=False, live=False, orbit=24, draft=False, force=False):
        from agentic_sim_world_gen.preview.page import mark

        d = service._resolved_path(scene).parent
        v = verdicts[min(len(calls), len(verdicts) - 1)]
        calls.append({"draft": draft, "steps": steps, "orbit": orbit})
        (d / "check.json").write_text(json.dumps({"physics": not draft, "unstable": ["desk"] if v == "needs_fix" else []}))
        mark(d, "check")
        return {"ok": True, "verdict": "draft" if draft else v, "mode": "headless", "screenshot": str(d / "check.png"),
                **({"next": "NOT passed: 1 bodies moved"} if v == "needs_fix" else {})}

    monkeypatch.setattr(service, "isaac_python", lambda: "python")
    monkeypatch.setattr(service, "check_in_isaac", check)
    return calls


def events(d):
    return [e["text"] for e in json.loads((d / "history.json").read_text())] if (d / "history.json").exists() else []


def test_create_scene_goes_on_to_the_draft(tmp_path, monkeypatch):
    pytest.importorskip("pxr")
    calls = isaac(monkeypatch, ["passed"])
    st = service.start_scene(SPEC, tmp_path / "t")
    got = st["started"]
    assert list(got) == ["purpose", "preview", "draft"]  # no window opening, so no windows step
    assert got["purpose"].startswith("demo (a guess")
    assert got["preview"]["url"].startswith("http://") and st["issues"] == []
    assert got["draft"]["usd"].endswith("scene.usda") and got["draft"]["check"]["verdict"] == "draft"
    assert calls == [{"draft": True, "steps": 240, "orbit": 24}] and st["last_check"]["verdict"] == "draft"
    s = service.load(tmp_path / "t")
    assert s.spec.purpose.name == "demo"
    purpose = next(a for a in s.spec.assumptions if a.item == "purpose")
    assert not purpose.given and not purpose.auto

    # the user's own purpose; steps left out
    st = service.start_scene({**SPEC, "name": "u"}, tmp_path / "u", purpose="amr", purpose_given=True, skip=["draft", "preview"])
    assert st["started"] == {"purpose": "amr"} and st["usd"] is None and len(calls) == 1
    assert next(a for a in service.load(tmp_path / "u").spec.assumptions if a.item == "purpose").given
    with pytest.raises(ValueError, match="unknown step"):
        service.start_scene(SPEC, tmp_path / "v", skip=["usd"])
    with pytest.raises(ValueError, match="unknown purpose"):
        service.start_scene(SPEC, tmp_path / "v", purpose="party")
    assert not (tmp_path / "v").exists()


def test_no_draft_while_the_layout_has_issues_and_none_without_isaac(tmp_path):
    pytest.importorskip("pxr")
    clash = {**SPEC, "objects": [{"id": "desk", "query": "desk", "position": [1.0, 1.0]},
                                 {"id": "chair", "query": "office chair", "position": [1.0, 1.0]}]}
    st = service.start_scene(clash, tmp_path / "c")
    assert st["issues"] and st["started"]["draft"] == {"skipped": f"{len(st['issues'])} issue(s): fix them first",
                                                       "stopped": "issues"}
    assert st["usd"] is None
    st = service.start_scene(SPEC, tmp_path / "t")  # no Isaac Sim here: the USD is written, no failed check logged
    assert st["started"]["draft"]["check"] == {"skipped": service.NO_ISAAC} and st["usd"]
    assert "last_check" not in st


def test_windows_are_filled(tmp_path, monkeypatch):
    monkeypatch.setitem(CATALOG, "window", [rec("window_a", "window", 1.6, 0.1, 1.1)])
    seen = {}
    monkeypatch.setattr(service, "fill_windows", lambda d, *a, **k: seen.setdefault("st", {"added": ["win_w1"]}))
    spec = {**SPEC, "room": {**SPEC["room"], "openings": [{"id": "w1", "kind": "window", "wall": "south", "offset": 1.0,
                                                           "width": 1.6, "height": 1.1, "sill": 0.9}]}}
    st = service.start_scene(spec, tmp_path / "w", skip=["draft", "preview"])
    assert st["started"]["windows"] == {"added": ["win_w1"]}


def test_finish(tmp_path, monkeypatch):
    pytest.importorskip("pxr")
    calls = isaac(monkeypatch, ["needs_fix", "passed"])
    d = tmp_path / "t"
    service.start_scene(SPEC, d, skip=["draft", "preview"])
    r = service.finish(d)
    assert r["stopped"] == "check" and not r["done"] and r["check"]["verdict"] == "needs_fix"
    assert r["next"].startswith("NOT passed") and "licenses" not in r and r["composed"] is True

    service.check_request(d, [{"text": "机は北の壁", "checks": [{"kind": "against_wall", "object": "desk", "target": "north"}]},
                              {"text": "椅子 2 脚", "checks": [{"kind": "count", "objects": ["chair"], "value": 2}]}])
    r = service.finish(d)
    assert "stopped" not in r and r["check"]["verdict"] == "passed" and "composed" not in r  # the USD was current
    assert r["requirements_summary"]["met"] == 1 and [x["text"] for x in r["requirements_open"]] == ["椅子 2 脚"]
    assert not r["done"] and "1 request item(s) not met" in r["next"]
    assert r["licenses"]["objects"] == {"CC-BY-4.0": ["机", "椅子"]} and (d / "LICENSES.md").exists()

    service.check_request(d, [{"text": "机は北の壁", "checks": [{"kind": "against_wall", "object": "desk", "target": "north"}]}])
    n = len(calls)
    r = service.finish(d)  # nothing changed since the check that passed: not run again
    assert r["done"] and len(calls) == n and "reused" in r["check"] and "unverified" not in r

    service.move_object(d, "chair", position=(2.6, 1.0))
    r = service.finish(d, draft=True)
    assert calls[-1]["draft"] and r["composed"] and not r["done"] and "first draft" in r["next"] and "licenses" not in r
    r = service.finish(d)
    assert r["done"] and not calls[-1]["draft"]


def test_finish_stops_at_issues_and_says_what_was_not_checked(tmp_path):
    pytest.importorskip("pxr")
    d = tmp_path / "t"
    service.start_scene(SPEC, d, skip=["draft", "preview"])
    r = service.finish(d)  # no Isaac Sim
    assert r["done"] and r["check"] == {"skipped": service.NO_ISAAC} and r["unverified"][0].startswith("physics")
    assert "requirements_hint" in r and "physics was not checked" in r["next"] and r["licenses"]["file"].endswith("LICENSES.md")
    assert not any("チェック失敗" in t for t in events(d))

    clash = {**SPEC, "objects": [{"id": "desk", "query": "desk", "position": [1.0, 1.0]},
                                 {"id": "chair", "query": "office chair", "position": [1.0, 1.0]}]}
    service.start_scene(clash, tmp_path / "c", skip=["draft", "preview"])
    r = service.finish(tmp_path / "c")
    assert not r["done"] and r["issues"] and "issue(s): fix them first" in r["next"] and "check" not in r


def test_candidate_sheets(tmp_path, monkeypatch):
    from PIL import Image

    from agentic_sim_world_gen import taste

    def png(color):
        buf = io.BytesIO()
        Image.new("RGB", (300, 200), color).save(buf, "PNG")
        return buf.getvalue()

    fetched = []
    monkeypatch.setattr(taste, "fetch", lambda url, timeout_s=20: fetched.append(url) or (None if "desk_b" in url else png("red")))
    d = tmp_path / "t"
    service.create_scene(SPEC, d)
    s = service.load(d)
    for p in s.placements:
        for c in p.candidates:
            c["thumbnail_url"] = f"https://example.com/{c['name']}.png"
    s.placements[1].candidates[0]["match"] = {"score": 0.1, "matched": [], "weak": True}
    s.save(d / "scene.resolved.json")

    sheets = service.candidate_sheets(d, ["desk", "chair"])
    assert [i["object"] for i, _ in sheets] == ["desk", "chair"] and len(fetched) == 3
    info, img = sheets[0]
    assert [c["index"] for c in info["candidates"]] == [0, 1] and info["selected"] == 0
    im = Image.open(io.BytesIO(img))
    assert im.format == "JPEG" and im.size == (2 * service.SHEET_TILE, service.SHEET_TILE)
    r, g, b = im.getpixel((service.SHEET_TILE // 2, service.SHEET_TILE // 2))
    assert r > 200 and g < 60 and b < 60  # the thumbnail; the one that could not be fetched is a crossed box
    assert max(im.getpixel((service.SHEET_TILE + 60, 20))) > 200

    auto = service.candidate_sheets(d)  # nothing named: the doubtful ones
    assert [i["object"] for i, _ in auto] == ["chair"]
    monkeypatch.setattr(service, "SHEET_OBJECTS", 0)
    assert service.candidate_sheets(d, limit=0)[0][0]["objects"] == []
    with pytest.raises(Exception):
        service.candidate_sheets(d, ["nope"])

    from agentic_sim_world_gen.mcp_server import mcp

    async def run():
        many = await mcp.call_tool("view_candidates", {"scene_dir": str(d), "object_id": ["desk", "chair"]})
        one = await mcp.call_tool("view_candidates", {"scene_dir": str(d), "object_id": "desk"})
        none = await mcp.call_tool("view_candidates", {"scene_dir": str(d)})
        return many, one, none

    many, one, none = asyncio.run(run())
    kinds = lambda res: [c.type for c in res.content]  # noqa: E731
    assert kinds(many) == ["text", "image", "text", "image"]  # an image per object
    assert kinds(one) == ["text", "image", "text", "text"]  # as before: an image per candidate (desk_b has none)
    assert kinds(none) == ["text", "image"]


def test_mcp_create_scene_and_finish(tmp_path):
    pytest.importorskip("pxr")
    from agentic_sim_world_gen.mcp_server import mcp

    async def run():
        made = await mcp.call_tool("create_scene", {"spec": SPEC, "out_dir": str(tmp_path / "m"), "skip": ["preview"]})
        done = await mcp.call_tool("finish", {"scene_dir": str(tmp_path / "m")})
        return made, done

    made, done = asyncio.run(run())
    st = json.loads(made.content[0].text)
    assert st["started"]["draft"]["check"] == {"skipped": service.NO_ISAAC} and st["assumptions_auto"]["count"] == 7
    r = json.loads(done.content[0].text)
    assert r["done"] and r["unverified"] and "composed" not in r


WINDOW = {"id": "w1", "kind": "window", "wall": "south", "offset": 1.0, "width": 1.2, "height": 1.1, "sill": 0.9}


def test_finish_stops_at_windows_onto_nothing(tmp_path):
    """window_view_hint went out 47 times in one run and was left alone; finish stops on it until a site
    shows something outside or an assumption says the windows are not seen."""
    pytest.importorskip("pxr")
    d = tmp_path / "w"
    service.start_scene({**SPEC, "room": {**SPEC["room"], "openings": [WINDOW]}}, d, skip=["draft", "preview", "windows"])
    r = service.finish(d)
    assert r["stopped"] == "window_view" and not r["done"] and "set_room(site=true)" in r["next"]
    assert 'item: "window_view"' in r["next"] and "check" not in r and "usd" not in r
    service.set_assumptions(d, [{"item": "window_view", "value": "窓はカーテンで隠れて映らない"}])
    r = service.finish(d)
    assert "stopped" not in r and r["done"]


def _photo_scene(tmp_path):
    from PIL import Image

    p = tmp_path / "p.jpg"
    Image.new("RGB", (400, 300)).save(p)
    d = tmp_path / "p"
    photo = {"id": "p", "path": str(p), "license": "CC0-1.0", "camera": {"position": [1, 0.3, 1.4], "look_at": [1, 2, 1]}}
    service.start_scene({**SPEC, "inputs": [photo]}, d, skip=["draft", "preview"])
    return d, p


def test_finish_stops_until_the_photos_are_compared(tmp_path, monkeypatch):
    """the photo_compare_hint was read past; finish stops on it while Isaac Sim's Python is at hand."""
    pytest.importorskip("pxr")
    d, _ = _photo_scene(tmp_path)
    isaac(monkeypatch, ["passed"])
    r = service.finish(d)
    assert r["stopped"] == "compare" and not r["done"] and "compare_photo" in r["next"] and "check" not in r
    assert 'item: "photo_compare"' in r["next"]  # the way out when compare_photo cannot run
    service.set_assumptions(d, [{"item": "photo_compare", "value": "写真の画角が分からない"}])
    assert "stopped" not in service.finish(d)


def test_finish_does_not_wait_for_a_compare_that_cannot_run(tmp_path, monkeypatch):
    """finish must not stop on compare forever."""
    pytest.importorskip("pxr")
    d, p = _photo_scene(tmp_path)
    service.compose_scene(d)
    # the user's Isaac Sim is open (live) but there is no python.sh / pip python: compare_photo cannot render
    monkeypatch.setattr(service, "live_available", lambda timeout_s=0.5: True)
    monkeypatch.setattr(service, "check_in_isaac", lambda *a, **k: {"ok": True, "verdict": "passed", "mode": "live"})
    assert service.isaac_python() is None and service.finish(d).get("stopped") != "compare"
    monkeypatch.setattr(service, "live_available", lambda timeout_s=0.5: False)
    isaac(monkeypatch, ["passed"])
    assert service.finish(d)["stopped"] == "compare"
    # compare_photo fails (the render did not come out): after COMPARE_FAILURES tries finish goes on
    monkeypatch.setattr(service, "_run_isaac", lambda *a, **k: (None, {"ok": False, "error": "render failed"}))
    for n in range(service.COMPARE_FAILURES):
        assert service.finish(d)["stopped"] == "compare"
        assert service.compare_photo(d)["ok"] is False
    assert service.status(d)["photo_compare"]["failed"] == service.COMPARE_FAILURES
    assert "stopped" not in service.finish(d)


def test_finish_does_not_wait_for_a_missing_photo(tmp_path, monkeypatch):
    """The scene was made on another machine: the photo file is not here, so compare_photo cannot run."""
    pytest.importorskip("pxr")
    d, p = _photo_scene(tmp_path)
    isaac(monkeypatch, ["passed"])
    assert service.finish(d)["stopped"] == "compare"
    p.unlink()
    assert "stopped" not in service.finish(d)


def test_finish_says_what_the_report_must_include(tmp_path, monkeypatch):
    """a run ended with 18 usability warnings its report never named."""
    pytest.importorskip("pxr")
    d = tmp_path / "t"
    service.start_scene(SPEC, d, skip=["draft", "preview"])
    r = service.finish(d)  # the demo's decor found no asset in this catalog: the report names what was left out
    assert r["done"] and r["report_must_include"] == {"decor_skipped": [
        "本（机） (no_asset)", "ペン立て（机） (no_asset)", "観葉植物（机の横） (no_asset)", "マグカップ（机） (no_asset)",
        "観葉植物（壁際） (no_asset)"]} and "report_must_include" in r["next"]
    rows = [{"kind": "tight_chair" if i % 2 else "blocked_door", "objects": ["chair"], "detail": "x" * 200}
            for i in range(7)]
    monkeypatch.setattr(service, "_usability", lambda s: rows)
    r = service.finish(d)
    assert r["report_must_include"]["usability"] == {"count": 7, "kinds": {"blocked_door": 4, "tight_chair": 3}}


def test_report_names_the_empty_windows_from_the_spec(tmp_path, monkeypatch):
    """windows_not_found came from the not-found log, where a window found hid the missing curtain of
    the same opening (and the log may be off). It is read from the spec now; the log matches by role."""
    pytest.importorskip("pxr")
    from agentic_sim_world_gen import missing
    from agentic_sim_world_gen.scene.spec import ObjectSpec

    monkeypatch.setenv("WORLDGEN_CACHE", str(tmp_path / "cache"))
    d = tmp_path / "s"
    service.start_scene({**SPEC, "room": {**SPEC["room"], "openings": [WINDOW]}}, d, skip=["draft", "preview", "windows"])
    s = service.load(d)
    s.spec.objects.append(ObjectSpec(id="win_w1", query="window", opening="w1"))  # a window found, no curtain
    s.save(service._resolved_path(d))
    missing.record(d, service.load(d), service.status(d),
                   [{"id": "curtain_w1", "query": "curtain", "nearest": None, "why": "x", "opening": "w1"}])
    service.move_object(d, "chair", position=[1.2, 1.4])  # any save keeps the curtain's row
    assert [r["id"] for r in missing.windows(d)] == ["curtain_w1"]
    service.set_assumptions(d, [{"item": "window_view", "value": "窓はカーテンで隠れて映らない"}])
    monkeypatch.setenv("WORLDGEN_MISSING", "0")  # the report does not need the log
    assert service.finish(d)["report_must_include"]["windows_not_found"] == ["curtain / blind (w1)"]
