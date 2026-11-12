"""preview.html (live page next to the terminal) and its local HTTP server."""

import json
import urllib.request

import pytest

from agentic_sim_world_gen import service
from agentic_sim_world_gen.assets.record import AssetRecord
from agentic_sim_world_gen.preview import page, server


def rec(name, cls, w, d, h, thumb=None):
    return AssetRecord(
        id=name, source="fake", uri=f"https://example.com/{name}.usd", name=name,
        bbox_min=(-w / 2, -d / 2, 0.0), bbox_max=(w / 2, d / 2, h), up_axis="Z", meters_per_unit=1.0,
        semantic_class=cls, license="CC-BY 4.0", thumbnail_url=thumb,
    )


@pytest.fixture
def scene(tmp_path):
    thumb = tmp_path / "lib" / "desk_b.png"
    thumb.parent.mkdir()
    thumb.write_bytes(b"\x89PNG fake")
    catalog = {"desk": [rec("desk_a", "desk", 1.4, 0.7, 0.74, "https://example.com/a.png"),
                        rec("desk_b", "desk", 1.2, 0.6, 0.72, thumb.as_uri())],
               "office chair": [rec("chair", "office chair", 0.55, 0.55, 0.85)]}
    service.set_provider(type("P", (), {"name": "f", "search": lambda self, q, limit=10, size=None: catalog.get(q, [])})())
    d = tmp_path / "out" / "study"
    service.create_scene({"name": "study", "room": {"width": 4, "depth": 3}, "objects": [
        {"id": "desk", "label": "机", "query": "desk", "layout": {"against_wall": "north"}},
        {"id": "chair", "query": "office chair", "layout": {"in_front_of": "desk"}},
    ]}, d)
    yield d
    service.set_provider(None)


def state(d):
    return json.loads((d / page.STATE).read_text())


def test_written_on_every_change(scene):
    html = (scene / page.PAGE).read_text()
    assert "候補" in html and "机" in html and "<svg" in html and "シーンを作成" in html
    assert service.status(scene)["previews"]["page"] == str(scene / page.PAGE)
    v0 = state(scene)["v"]
    service.select_asset(scene, "desk", 1)
    st = state(scene)
    assert st["v"] != v0 and st["event"]["focus"] == "desk" and "desk_b" in st["event"]["text"]
    html = (scene / page.PAGE).read_text()
    assert "id='obj-desk' class='obj focus' open>" in html and "id='obj-chair' class='obj'>" in html and f"data-v='{st['v']}' data-focus='desk'" in html
    # local thumbnails are copied next to the page (file:// does not load from http://)
    copied = [f for f in (scene / page.FILES).iterdir() if f.read_bytes() == b"\x89PNG fake"]  # also the mood pictures
    assert len(copied) == 1 and copied[0].read_bytes() == b"\x89PNG fake" and f"{page.FILES}/{copied[0].name}" in html


def test_refresh_keeps_the_last_event(scene):
    service.select_asset(scene, "desk", 1)
    service.refresh(scene)
    assert "desk_b" in state(scene)["event"]["text"]


def test_view_candidates_highlights_object(scene):
    service.candidate_thumbnails(scene, "chair", timeout_s=0.01)
    st = state(scene)
    assert st["event"]["focus"] == "chair" and "サムネイル" in st["event"]["text"]


def test_usd_and_check_freshness(scene):
    def usd_badge():
        return (scene / page.PAGE).read_text()

    assert "scene.usda" not in usd_badge().split("<section class='layout'>")[0]
    (scene / "scene.usda").write_text("#usda 1.0\n")  # what compose_scene writes, then marks
    page.mark(scene, "usd")
    (scene / "check.json").write_text(json.dumps({"objects": {}, "unstable": ["chair"]}))
    page.mark(scene, "check")
    service.refresh(scene)  # re-saving an unchanged scene keeps both fresh
    html = usd_badge()
    assert "scene.usda 最新" in html and "要修正" in html and "動いた" in html
    service.set_layout(scene, "desk", {"against_wall": "west"})
    html = usd_badge()
    assert "scene.usda は古い" in html and "古い（シーン変更後に未実行）" in html


def test_server_serves_and_tracks_open_pages(scene, monkeypatch):
    monkeypatch.setenv("AGENTIC_SIM_WORLD_GEN_PREVIEW", "127.0.0.1:0")
    url = server.url_for(scene)
    assert url.endswith("/study/preview.html")
    assert server.url_for(scene) == url  # one server per root
    assert not server.page_is_open(scene)
    assert "<svg" in urllib.request.urlopen(url, timeout=5).read().decode()
    with urllib.request.urlopen(url.replace("preview.html", "preview.json?t=1"), timeout=5) as r:
        assert json.loads(r.read())["v"] == state(scene)["v"] and r.headers["Cache-Control"] == "no-store"
    assert server.page_is_open(scene)


def test_open_preview_without_display(scene, monkeypatch):
    monkeypatch.setenv("AGENTIC_SIM_WORLD_GEN_PREVIEW", "127.0.0.1:0")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("AGENTIC_SIM_WORLD_GEN_BROWSER", raising=False)  # conftest sets it for every test
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(server.sys, "platform", "linux")
    monkeypatch.setattr(server.subprocess, "Popen", lambda *a, **k: pytest.fail("no browser without a display"))
    r = service.open_preview(scene)
    assert not r["opened"] and not r["already_open"] and "ssh -L" in r["hint"] and r["url"].startswith("http://127.0.0.1:")
    opened = []
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(server.subprocess, "Popen", lambda cmd, **k: opened.append(cmd))
    assert service.open_preview(scene)["opened"] and opened == [["xdg-open", r["url"]]]
    urllib.request.urlopen(r["url"].replace("preview.html", "preview.json"), timeout=5).read()
    r2 = service.open_preview(scene)  # the page is polling: no second tab
    assert r2["already_open"] and not r2["opened"] and len(opened) == 1


def test_custom_browser(monkeypatch):
    opened = []
    monkeypatch.setattr(server.subprocess, "Popen", lambda cmd, **k: opened.append(cmd))
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setenv("AGENTIC_SIM_WORLD_GEN_BROWSER", "chromium --app={url} --window-size=900,1200")
    assert server.open_browser("http://h:1/s/preview.html")
    monkeypatch.setenv("AGENTIC_SIM_WORLD_GEN_BROWSER", "firefox --new-window")
    assert server.open_browser("http://h:1/x")
    assert opened == [["chromium", "--app=http://h:1/s/preview.html", "--window-size=900,1200"],
                      ["firefox", "--new-window", "http://h:1/x"]]


def test_running_check_stays_on_the_page(scene, monkeypatch):
    seen = []

    def fake_check(*a):
        service.license_report(scene)  # another step while the check runs (Claude does this in parallel)
        seen.append((scene / page.PAGE).read_text())
        return {"ok": True, "mode": "headless", "report": {"objects": {}}}

    monkeypatch.setattr(service, "_check_in_isaac", fake_check)
    service.check_in_isaac(scene)
    assert "Isaac Sim でチェック中" in seen[0] and "LICENSES.md" in seen[0]
    html = (scene / page.PAGE).read_text()
    assert "チェック完了" in html and "チェック中" not in html


def test_check_orbit_viewer(scene):
    (scene / "check.png").write_bytes(b"png")
    o = scene / service.ORBIT_DIR
    o.mkdir()
    for i in range(3):
        (o / f"{i:02d}.jpg").write_bytes(b"jpg")
    (scene / "check.json").write_text(json.dumps({"objects": {}, "orbit": {"frames": ["00.jpg", "01.jpg", "02.jpg"]}}))
    page.mark(scene, "check")
    service.refresh(scene)
    html = (scene / page.PAGE).read_text()
    assert "class='orbit'" in html and "check_orbit/02.jpg" in html and "ドラッグで回転（3 方向）" in html
    assert "src='check.png" not in html  # the viewer replaces the still
    (scene / "check.json").write_text(json.dumps({"objects": {}}))  # a check without orbit frames
    service.refresh(scene)
    html = (scene / page.PAGE).read_text()
    assert "class='orbit'" not in html and "src='check.png" in html


def test_check_passes_orbit_to_isaac(scene, monkeypatch):
    (scene / "scene.usda").write_text("#usda 1.0\n")
    (scene / service.ORBIT_DIR).mkdir()
    (scene / service.ORBIT_DIR / "00.jpg").write_bytes(b"old")
    calls = []

    def fake_run(script, args, timeout_s):
        calls.append(args)
        return None, {"ok": False, "error": "no isaac"}

    monkeypatch.setattr(service, "_run_isaac", fake_run)
    service.check_in_isaac(scene, orbit=12)
    assert calls[0][-4:] == ["--orbit-dir", str((scene / service.ORBIT_DIR).resolve()), "--orbit-frames", "12"]
    assert not (scene / service.ORBIT_DIR).exists()  # stale frames are gone before the run
    service.check_in_isaac(scene, orbit=0)
    assert "--orbit-dir" not in calls[1]


def test_draft_check(scene, monkeypatch):
    """A draft look runs no physics; the page says so until a full check."""
    (scene / "scene.usda").write_text("#usda 1.0\n")
    calls = []

    def fake_run(script, args, timeout_s):
        calls.append(args)
        (scene / "check.json").write_text(json.dumps({"objects": {}, "physics": False, "unstable": [], "sunk": [],
                                                       "no_collider": [], "static": []}))
        (scene / "check.png").write_bytes(b"png")
        return type("P", (), {"returncode": 0})(), {}

    monkeypatch.setattr(service, "_run_isaac", fake_run)
    r = service.check_in_isaac(scene, draft=True, gif=True)
    assert calls[0][calls[0].index("--steps") + 1] == "0" and "--gif" not in calls[0]
    assert r["ok"] and "without draft" in r["next"]
    html = (scene / page.PAGE).read_text()
    assert "叩き台（物理は未確認）" in html and "叩き台の 3D を撮影" in html and "badge ok'>OK" not in html
    (scene / "check.json").write_text(json.dumps({"objects": {}, "unstable": []}))  # the full check
    service.refresh(scene)
    assert "叩き台（物理は未確認）" not in (scene / page.PAGE).read_text()


def test_license_badges():
    b = page._license_badge
    assert "badge ok" in b("CC0") and "CC0 1.0" in b("CC0")
    assert "badge lic" in b("CC-BY 4.0") and "CC BY 4.0" in b("cc by 4.0")
    assert "badge warn" in b("CC-BY-NC-4.0") and "badge warn" in b("CC-BY-SA-4.0")
    assert "badge warn" in b("NVIDIA-Isaac-Sim") and "Isaac Sim 同梱" in b("NVIDIA-Isaac-Sim")
    assert "badge bad" in b(None) and "ライセンス不明" in b("")
    assert "badge bad" in b("see /x/LICENSE") and "see /x/LICENSE" in b("see /x/LICENSE")


def test_page_updates_in_place():
    js = page._JS
    assert "location.reload()" in js  # only as the fallback when patching fails
    assert "morph(document.body, doc.body)" in js and "DOMParser" in js


def test_candidates_are_compact_rows(scene):
    html = (scene / page.PAGE).read_text()
    assert html.count("<details id='obj-") == 2 and "id='only-attn'" in html and "要確認のみ（0 / 2）" in html
    service.select_asset(scene, "chair", 0)  # focus moves: only the chair is open
    html = (scene / page.PAGE).read_text()
    assert "id='obj-chair' class='obj focus' open>" in html and "id='obj-desk' class='obj'>" in html
    # the row shows the pick, its license and the number of candidates without opening it
    row = html[html.index("id='obj-desk'"):html.index("<div class='body'>", html.index("id='obj-desk'"))]
    assert "desk_a" in row and "CC BY 4.0" in row and "2 件" in row


def test_attention_rows():
    # unknown license and objects named in layout issues are marked for the 要確認のみ filter
    assert page._license_level(None) == "bad" and page._license_level("CC-BY-NC-4.0") == "nc"
    assert page._license_level("NVIDIA-Isaac-Sim") == "warn" and page._license_level("CC0") == "ok"
    assert page._issue_objects([{"objects": ["rack.r0c1", "desk"]}]) == {"rack", "desk"}


def test_licenses_grouped(scene):
    html = (scene / page.PAGE).read_text()
    lic = html[html.index("<h2>ライセンス"):]
    lic = lic[:lic.index("</table>")]
    assert lic.count("<tr>") == 2 and "<td>2</td>" in lic and ("机、chair" in lic or "chair、机" in lic)


def test_change_history(scene):
    service.select_asset(scene, "desk", 1)
    service.select_asset(scene, "chair", 0)
    hist = state(scene)["history"]
    assert [h["focus"] for h in hist][-2:] == ["desk", "chair"] and all(h["text"] for h in hist)
    html = (scene / page.PAGE).read_text()
    h = html[html.index("id='history'"):html.index("</details>", html.index("id='history'"))]
    assert f"{len(hist)} 件" in h and "href='#obj-chair'" in h
    assert h.index("href='#obj-chair'") < h.index("href='#obj-desk'")  # newest first
    service.refresh(scene)  # no new event: nothing added
    assert len(state(scene)["history"]) == len(hist)
    sc = service.load(scene)
    for i in range(page.HISTORY + 5):
        page.write(sc, scene, [], {"text": f"e{i}"})
    hist = state(scene)["history"]
    assert len(hist) == page.HISTORY and hist[-1]["text"] == f"e{page.HISTORY + 4}"
    page.write(sc, scene, [], {"text": "busy", "busy": True})  # in-progress steps are not logged
    assert state(scene)["history"][-1]["text"] != "busy"


def test_issues_in_japanese(scene):
    sc = service.load(scene)
    line = lambda kind, objs, detail: page._issue_line(sc, {"kind": kind, "objects": objs, "detail": detail})  # noqa: E731
    assert line("overlap", ["desk", "chair"], "penetration 0.12 m") == "<li><b>重なり</b>：机・chair 0.12 m めり込み</li>"
    assert "部屋からはみ出し" in line("out_of_room", ["desk.r0c1"], "footprint x[-0.10,1.30] y[0.00,0.70] vs room 4x3")
    assert "机 r0c1" in line("out_of_room", ["desk.r0c1"], "") and "x -0.10〜1.30" in line(
        "out_of_room", ["desk"], "footprint x[-0.10,1.30] y[0.00,0.70] vs room 4x3")
    assert "壁 N に 0.05 m" in line("hits_wall", ["desk"], "inside the wall N by 0.05 m")
    assert "開口 d1 の前に 0.30 m" in line("blocks_door", ["desk"], "in the way of the door d1 by 0.30 m")
    assert "上端 2.60 m ＞ 天井 2.4 m" in line("too_tall", ["desk"], "top 2.60 m > room 2.4 m")
    # unknown messages stay as they are, greyed
    assert "<span class='muted'>something new</span>" in line("weird", ["gone"], "something new")
    service.move_object(scene, "chair", (0.1, 2.95))  # half out of the room: the page says so in Japanese
    html = (scene / page.PAGE).read_text()
    assert "<b>部屋からはみ出し</b>：chair 足元" in html and "out_of_room" not in html.split("<script>")[0]


def test_change_summary(scene):
    """Each logged step says in Japanese what it changed (asset, place, room, added / removed)."""
    service.select_asset(scene, "desk", 1)
    h = state(scene)["history"][-1]
    assert any(c.startswith("机：アセット ") and " → " in c for c in h["changes"]), h
    service.move_object(scene, "chair", (1.5, 1.0))
    h = state(scene)["history"][-1]
    assert any(c.startswith("chair：位置 ") and "→ (1.50, 1.00)" in c for c in h["changes"]), h
    service.move_object(scene, "chair", (1.8, 1.0))  # same step text again: still logged, it changed something
    assert state(scene)["history"][-1]["changes"] != h["changes"]
    html = (scene / page.PAGE).read_text()
    assert "<div class='changes'>" in html and "(1.80, 1.00)" in html.split("<script>")[0]
    service.set_room(scene, width=5.0)
    assert any(c.startswith("部屋：4 × ") and "→ 5 × " in c for c in state(scene)["history"][-1]["changes"])
    service.refresh(scene)  # nothing changed: nothing logged
    n = len(state(scene)["history"])
    service._event(scene, "机 の候補を確認中", "desk")  # looking changes nothing: logged without changes, box gone
    assert state(scene)["history"][-1]["changes"] == [] and "<div class='changes'>" not in (scene / page.PAGE).read_text()
    assert len(state(scene)["history"]) == n + 1


def test_check_result_stands_out(scene, monkeypatch):
    """A check that found problems is marked 要修正 in red, with object labels instead of prim names."""
    fake = {"ok": True, "mode": None, "report": {"unstable": ["desk", "chair"], "sunk": [], "no_collider": [],
                                                  "objects": {"desk": {}, "chair": {}}}}
    def run(*a, **k):
        (scene / "check.json").write_text(json.dumps(fake["report"]))
        return fake

    monkeypatch.setattr(service, "_check_in_isaac", run)
    service.check_in_isaac(scene)
    h = state(scene)["history"][-1]
    assert h["level"] == "bad" and h["text"] == "チェックで問題 2 件（headless）：物理で動いた 机・chair", h
    top = (scene / page.PAGE).read_text().split("<script>")[0]
    assert "class='now-line lv-bad'><span class='badge bad'>要修正</span>" in top
    assert "<li><b>物理で動いた</b>：机・chair<span class='muted'>（Isaac Sim チェック）</span></li>" in top
    shot = top[top.index("<section class='shot ng'>"):]  # the check box: red frame, problems above the picture
    assert shot.index("<ul class='issues'><li><b>物理で動いた</b>：机・chair</li></ul>") < shot.index("</section>")
    fake["report"]["unstable"] = []
    service.check_in_isaac(scene)
    assert state(scene)["history"][-1]["level"] == "ok" and "問題なし" in state(scene)["history"][-1]["text"]
    assert page.prim_label({"rack": "ラック"}, "rack/rack_r0c1") == "ラック r0c1"


def test_names_and_hints_read_by_the_user(scene):
    # Stock reads 「陳列：<product> ×N（<fixture>）」, decor has no label on the top view,
    # an English item in a Japanese scene gets a hint
    from agentic_sim_world_gen.preview import layout2d
    from agentic_sim_world_gen.scene.spec import Fill, ObjectSpec

    s = service.load(scene)
    o = ObjectSpec(id="decor_stock_desk", query="chips bag", label="陳列（机）2", decor=True, fill=Fill(on="desk"))
    o.fill.slots = [(f"s{i}", 0.0, 0.0, 0.7, 0.0) for i in range(3)]
    assert page.obj_name(s, o) == "陳列：chips bag ×3（机）"
    assert ">chair<" in layout2d.render_svg(s)
    s.obj("chair").decor = True
    assert ">chair<" not in layout2d.render_svg(s) and page.obj_name(s, s.obj("chair")) == "chair"
    st = service.set_assumptions(scene, [{"item": "pink wall", "value": "ピンクの壁", "given": True}])
    assert "pink wall" in st["hint"]
    st = service.set_assumptions(scene, [{"item": "壁の色", "value": "ピンク", "given": True}], remove=["pink wall"])
    assert "pink wall" not in (st.get("hint") or "")
