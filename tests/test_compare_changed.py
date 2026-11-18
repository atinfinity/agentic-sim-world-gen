"""compare_photo returns only the rows whose render changed since the last round. Offline."""
import json

import pytest
from PIL import Image, ImageDraw

from agentic_sim_world_gen import service
from tests.test_fewer_turns import CATALOG, SPEC, fake  # noqa: F401


def _scene(tmp_path, n=3):
    photos = []
    for k in range(n):
        p = tmp_path / f"p{k}.jpg"
        Image.new("RGB", (300, 400), (90, 90, 90)).save(p)
        photos.append({"id": f"p{k}", "path": str(p), "license": "CC0-1.0",
                       "camera": {"position": [1 + 0.2 * k, 0.3, 1.4], "look_at": [1, 2, 1]}})
    d = tmp_path / "s"
    service.create_scene({**SPEC, "inputs": photos}, d)
    (d / "scene.usda").write_text("#usda 1.0\n")
    return d


def _renderer(monkeypatch, changed, box=None, fail=None):
    """A fake Isaac Sim: every render is the same gray, the ones in changed[0] get a white box (box[0] tall).
    fail[0] = True: it writes the renders, then fails (no compare sheet)."""
    def run(script, args, timeout_s):
        jobs = json.loads(open(args[args.index("--jobs") + 1]).read())
        for j in jobs:
            im = Image.new("RGB", (300, 400), (120, 120, 120))
            if j["camera"].rsplit("/", 1)[-1] in changed[0]:
                ImageDraw.Draw(im).rectangle((100, 100, 130 if box else 200, 100 + (box[0] if box else 150)), fill="white")
            im.save(j["out"])
        if fail and fail[0]:
            return type("P", (), {"returncode": 1, "stdout": "", "stderr": "crash"})(), {}
        open(args[args.index("--compare") + 1], "wb").write(b"png")
        return type("P", (), {"returncode": 0})(), {}
    monkeypatch.setattr(service, "_run_isaac", run)


def test_only_changed_rows_come_back(tmp_path, monkeypatch):
    d = _scene(tmp_path)
    changed = [set()]
    _renderer(monkeypatch, changed)
    r = service.compare_photo(d)
    assert "unchanged" not in r and "image_changed" not in r  # the first round shows every row
    changed[0] = {"p1"}
    r = service.compare_photo(d)
    assert r["unchanged"] == ["p0", "p2"] and "p0, p2" in r["unchanged_hint"]
    im = Image.open(r["image_changed"])
    assert im.height < 400 + 40  # one row
    changed[0] = {"p1"}
    r = service.compare_photo(d)  # nothing changed since: no image at all
    assert r["unchanged"] == ["p0", "p1", "p2"] and "image_changed" not in r and "none changed" in r["unchanged_hint"]
    assert not (d / service.CHANGED_SHEET).exists()


def test_noise_is_not_a_change(tmp_path):
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    im = Image.new("RGB", (640, 480), (100, 100, 100))
    im.save(a)
    im.point(lambda v: v + 8).save(b)  # a little brighter all over (denoiser noise is far less)
    assert service._render_diff(a, b) == 0
    ImageDraw.Draw(im).rectangle((300, 200, 340, 240), fill="white")  # a small object appeared
    im.save(b)
    assert service._render_diff(a, b) > service.RENDER_DIFF_FRAC


def test_mcp_returns_the_changed_sheet(tmp_path, monkeypatch):
    pytest.importorskip("mcp")
    from agentic_sim_world_gen import mcp_server

    d = _scene(tmp_path)
    changed = [set()]
    _renderer(monkeypatch, changed)
    assert len(mcp_server.compare_photo(str(d))) == 2
    changed[0] = {"p0"}
    out = mcp_server.compare_photo(str(d))
    assert len(out) == 2 and json.loads(out[0])["unchanged"] == ["p1", "p2"]
    out = mcp_server.compare_photo(str(d))
    assert len(out) == 1  # nothing changed: the text only


def test_a_failed_round_does_not_hide_a_row(tmp_path, monkeypatch):
    """The renders of a failed round were never seen: the next round compares with the last one shown."""
    d = _scene(tmp_path)
    changed, fail = [set()], [False]
    _renderer(monkeypatch, changed, fail=fail)
    service.compare_photo(d)
    changed[0], fail[0] = {"p1"}, True
    assert not service.compare_photo(d)["ok"]
    fail[0] = False
    r = service.compare_photo(d)
    assert r["unchanged"] == ["p0", "p2"] and "image_changed" in r


def test_small_changes_add_up(tmp_path, monkeypatch):
    """A hidden row keeps the render last shown, so changes too small for one round come back together."""
    d = _scene(tmp_path)
    changed, box = [{"p1"}], [50]
    _renderer(monkeypatch, changed, box=box)
    service.compare_photo(d)
    shown = []
    for _ in range(4):
        box[0] += 1  # one row of 30 px: under RENDER_DIFF_FRAC a step
        shown.append("p1" not in service.compare_photo(d).get("unchanged", []))
    assert shown[0] is False and any(shown)


def test_a_moved_camera_is_shown(tmp_path, monkeypatch):
    d = _scene(tmp_path)
    changed = [set()]
    _renderer(monkeypatch, changed)
    service.compare_photo(d)
    inp = next(i for i in service.load(d).spec.inputs if i.id == "p2").model_dump()
    inp["camera"]["position"] = [inp["camera"]["position"][0] + 0.05, *inp["camera"]["position"][1:]]
    service.set_input(d, inp)
    r = service.compare_photo(d)  # the same pixels, another camera
    assert r["unchanged"] == ["p0", "p1"]
