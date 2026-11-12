"""Editing objects on the live preview page: token-protected POST api/edit on the preview server."""

import json
import urllib.error
import urllib.request

import pytest

from agentic_sim_world_gen import service
from agentic_sim_world_gen.preview import page, server
from agentic_sim_world_gen.scene.spec import ResolvedScene

from test_preview_page import scene  # noqa: F401  (fixture: a 4x3 m study with a desk and a chair)


def post(url, body, token=server.TOKEN, headers=None):
    req = urllib.request.Request(url, json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "X-Agentic-Sim-World-Gen-Token": token, **(headers or {})})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


@pytest.fixture
def api(scene, monkeypatch):  # noqa: F811
    monkeypatch.setenv("AGENTIC_SIM_WORLD_GEN_PREVIEW", "127.0.0.1:0")
    url = server.url_for(scene)
    return url, url.rsplit("/", 1)[0] + "/api/edit"


def test_svg_marks_editable_objects(scene):  # noqa: F811
    svg = (scene / "layout.svg").read_text()
    assert 'class="wg-plan"' in svg and 'data-ppm="120.0000"' in svg
    assert '<g class="wg-obj edit" data-id="desk" data-label="机"' in svg and 'data-layout="1"' in svg
    html = (scene / page.PAGE).read_text()
    assert "data-obj='desk' data-i='1'" in html and '"overlap": "重なり"' in html  # issue names for the drag


def test_token_only_in_the_served_page(scene, api):  # noqa: F811
    url, _ = api
    assert server.TOKEN not in (scene / page.PAGE).read_text()
    with urllib.request.urlopen(url) as r:
        assert f"<meta name='wg-token' content='{server.TOKEN}'>" in r.read().decode()


def test_reads_need_a_local_host(scene, api):  # noqa: F811
    url, _ = api
    req = urllib.request.Request(url.replace(page.PAGE, "scene.resolved.json"), headers={"Host": "evil.example:8765"})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req)
    assert e.value.code == 403  # DNS rebinding cannot read the scenes
    with urllib.request.urlopen(url.replace(page.PAGE, "scene.resolved.json")) as r:
        assert r.status == 200


def test_writes_need_the_token_and_a_local_host(scene, api):  # noqa: F811
    _, edit = api
    body = {"op": "move", "id": "chair", "position": [1, 1]}
    assert post(edit, body, token="nope")[0] == 403
    assert post(edit, body, headers={"Host": "evil.example:8765"})[0] == 403  # DNS rebinding
    assert post(edit, body, headers={"Origin": "http://evil.example"})[0] == 403
    assert post(edit.replace("/study/", "/../study/"), body)[0] in (403, 404)
    assert service.load(scene).obj("chair").position != (1, 1)


def test_try_reports_issues_without_saving(scene, api):  # noqa: F811
    _, edit = api
    before = (scene / "scene.resolved.json").read_text()
    code, j = post(edit, {"op": "try", "id": "chair", "position": [-0.5, 1.5], "yaw_deg": 0})
    assert code == 200 and {i["kind"] for i in j["issues"]} == {"out_of_room"}
    desk = service.load(scene).obj("desk").position
    code, j = post(edit, {"op": "try", "id": "chair", "position": list(desk), "yaw_deg": 0})
    assert "overlap" in {i["kind"] for i in j["issues"]}
    assert (scene / "scene.resolved.json").read_text() == before


def test_move_and_turn_are_saved_and_seen_by_the_agent(scene, api):  # noqa: F811
    _, edit = api
    code, j = post(edit, {"op": "move", "id": "chair", "position": [1.0, 0.8], "facing_deg": 180})
    assert code == 200 and j["ok"] and j["issues"] == [] and j["usd"] is False
    o = ResolvedScene.load(scene / "scene.resolved.json").obj("chair")
    assert o.position == (1.0, 0.8) and o.layout is None and o.facing_deg == 180
    hist = json.loads((scene / page.STATE).read_text())["history"]
    assert hist[-1]["by"] == "preview" and hist[-1]["text"].startswith("プレビューで操作：")
    assert any("位置" in c or "向き" in c for c in hist[-1]["changes"])
    st = service.status(scene)
    assert [e["object"] for e in st["preview_edits"]] == ["chair"] and "preview_edits_hint" in st
    assert "手動" in (scene / page.PAGE).read_text()
    service.set_room(scene, lighting="home")  # the agent's next step: the edits are no longer new
    assert "preview_edits" not in service.status(scene)


def test_select_from_the_page_and_usd_follows(scene, api):  # noqa: F811
    pytest.importorskip("pxr")
    service.compose_scene(scene)
    _, edit = api
    code, j = post(edit, {"op": "select", "id": "desk", "index": 1})
    assert code == 200 and j["usd"] is True
    from agentic_sim_world_gen.preview import server

    server.wait_composed(30)  # the USD follows in the background
    assert service.load(scene).placements[0].selected == 1
    assert "desk_b" in (scene / "scene.usda").read_text()
    st = json.loads((scene / page.STATE).read_text())
    assert "desk_b" in st["history"][-1]["text"] and st["event"]["by"] == "preview"  # the edit, not "USD を書き出し"
    assert "scene.usda 最新" in (scene / page.PAGE).read_text()
    assert post(edit, {"op": "select", "id": "desk", "index": 9})[0] == 400


def test_usd_follows_in_one_background_compose(tmp_path, monkeypatch):
    # edits that come while the USD is written fold into one more compose, not one each
    import threading

    from agentic_sim_world_gen.preview import server

    (tmp_path / "scene.usda").write_text("#usda 1.0\n")
    go, calls = threading.Event(), []

    def compose(d, log=True):
        calls.append(d)
        go.wait(5)

    monkeypatch.setattr(service, "compose_scene", compose)
    assert server._compose_later(tmp_path)
    while not calls:
        threading.Event().wait(0.01)
    for _ in range(3):  # while the first compose runs
        assert server._compose_later(tmp_path)
    go.set()
    server.wait_composed(5)
    assert len(calls) == 2 and tmp_path not in server._composing
    assert server._compose_later(tmp_path / "none") is False
