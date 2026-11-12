"""Photo inputs: provenance from sidecars, cameras in the USD, the license report."""

import json
import math
import struct
import zlib

import pytest

from agentic_sim_world_gen import service
from agentic_sim_world_gen.inputs import focal_mm, image_size, resolve
from agentic_sim_world_gen.scene.spec import InputRef, PhotoCamera

from test_service import SPEC, fake  # noqa: F401  (fixture)


def png(path, w, h):
    def chunk(t, data):
        return struct.pack(">I", len(data)) + t + data + struct.pack(">I", zlib.crc32(t + data))
    raw = b"".join(b"\x00" + b"\x00" * 3 * w for _ in range(h))
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def jpeg(path, w, h):
    # SOI, APP0 (JFIF), SOF0 with the size, EOI: enough for the header reader
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, h, w, 1) + b"\x01\x11\x00"
    path.write_bytes(b"\xff\xd8" + app0 + sof + b"\xff\xd9")


def test_image_size(tmp_path):
    png(tmp_path / "a.png", 7, 5)
    jpeg(tmp_path / "b.jpg", 1920, 1281)
    (tmp_path / "c.txt").write_text("hello")
    assert image_size(tmp_path / "a.png") == (7, 5)
    assert image_size(tmp_path / "b.jpg") == (1920, 1281)
    assert image_size(tmp_path / "c.txt") is None
    assert image_size(tmp_path / "missing.jpg") is None


def test_resolve_sidecar(tmp_path):
    jpeg(tmp_path / "room.jpg", 1600, 1200)
    (tmp_path / "room.source.json").write_text(json.dumps(
        {"url": "https://commons.wikimedia.org/wiki/File:Room.jpg", "author": "A", "license": "CC0-1.0", "title": "Room"}))
    r = resolve(InputRef(path="room.jpg", author="given"), tmp_path)
    assert r.path == str((tmp_path / "room.jpg").resolve())
    assert r.size_px == (1600, 1200)
    assert r.url.endswith("Room.jpg") and r.license == "CC0-1.0"
    assert r.author == "given"  # explicit fields win over the sidecar


def test_focal():
    assert focal_mm(PhotoCamera(position=(0, 0, 1.5), look_at=(0, 3, 1.2), hfov_deg=90)) == pytest.approx(18.0)


def test_photo_scene(fake, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.chdir(tmp_path)
    jpeg(tmp_path / "room.jpg", 1600, 1200)
    spec = {**SPEC, "inputs": [{"id": "photo", "path": "room.jpg",
                                "camera": {"position": [1.8, 0.3, 1.4], "look_at": [1.8, 2.7, 1.1], "hfov_deg": 70}}]}
    d = tmp_path / "s"
    st = service.create_scene(spec, d)
    assert st["inputs"][0]["size_px"] == [1600, 1200]
    assert st["inputs"][0]["missing"] == ["url", "license"]

    st = service.set_input(d, {"id": "photo", "url": "https://example.com/p", "license": "CC0"})
    (i,) = st["inputs"]
    assert "missing" not in i and i["camera"]["hfov_deg"] == 70  # camera kept

    r = service.license_report(d)
    assert r["inputs"][0]["source"] == "https://example.com/p" and r["inputs"][0]["license"] == "CC0-1.0"
    assert "## Inputs" in (d / "LICENSES.md").read_text()

    pxr = pytest.importorskip("pxr")
    service.compose_scene(d)
    stage = pxr.Usd.Stage.Open(str(d / "scene.usda"))
    cam = pxr.UsdGeom.Camera(stage.GetPrimAtPath("/World/Cameras/photo"))
    assert cam
    gc = cam.GetCamera()
    hfov = 2 * math.degrees(math.atan(gc.horizontalAperture / 2 / gc.focalLength))
    assert hfov == pytest.approx(70, abs=0.01)
    assert gc.verticalAperture / gc.horizontalAperture == pytest.approx(0.75)
    eye = gc.transform.ExtractTranslation()
    assert tuple(eye) == pytest.approx((1.8, 0.3, 1.4))
    view = gc.frustum.ComputeViewDirection()  # camera looks at look_at
    assert view.GetNormalized() * pxr.Gf.Vec3d(0, 2.4, -0.3).GetNormalized() == pytest.approx(1.0)
    src = stage.GetPrimAtPath("/World").GetCustomDataByKey("agentic_sim_world_gen:inputs")
    assert src["photo"]["url"] == "https://example.com/p"


def test_compare_photo_errors(fake, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setenv("ISAACSIM_PATH", str(tmp_path / "nope"))
    monkeypatch.delenv("ISAACSIM_PYTHON", raising=False)
    d = tmp_path / "s"
    service.create_scene(SPEC, d)
    assert "no input" in service.compare_photo(d)["error"]
    jpeg(tmp_path / "p.jpg", 400, 300)
    service.set_input(d, {"path": str(tmp_path / "p.jpg"),
                          "camera": {"position": [1, 0.3, 1.4], "look_at": [1, 2, 1]}})
    assert "compose_scene" in service.compare_photo(d)["error"]
    (d / "scene.usda").write_text("#usda 1.0\n")
    assert "Isaac Sim not found" in service.compare_photo(d)["error"]


def test_several_photos(fake, tmp_path, monkeypatch):  # noqa: F811
    """Photos from several sides of one room, one camera each (layout.svg, one compare run)."""
    monkeypatch.setenv("ISAACSIM_PATH", str(tmp_path / "nope"))
    monkeypatch.delenv("ISAACSIM_PYTHON", raising=False)
    jpeg(tmp_path / "a.jpg", 1600, 1200)
    jpeg(tmp_path / "b.jpg", 1200, 1600)
    spec = {**SPEC, "inputs": [
        {"id": "south", "path": str(tmp_path / "a.jpg"), "camera": {"position": [1.8, 0.3, 1.4], "look_at": [1.8, 2.7, 1.1]}},
        {"id": "north", "path": str(tmp_path / "b.jpg"), "camera": {"position": [1.8, 2.6, 1.4], "look_at": [1.8, 0.2, 1.1]}},
    ]}
    d = tmp_path / "s"
    service.create_scene(spec, d)
    svg = (d / "layout.svg").read_text()
    assert "📷 south" in svg and "📷 north" in svg
    (d / "scene.usda").write_text("#usda 1.0\n")
    assert "Isaac Sim not found" in service.compare_photo(d)["error"]
    jobs = json.loads((d / "compare_jobs.json").read_text())  # both photos in one Isaac Sim run
    assert [j["camera"] for j in jobs] == ["/World/Cameras/south", "/World/Cameras/north"]
    assert jobs[1]["size"] == [1200, 1600]
    service.compare_photo(d, "north")
    assert [j["camera"] for j in json.loads((d / "compare_jobs.json").read_text())] == ["/World/Cameras/north"]
    (tmp_path / "b.jpg").unlink()
    assert "['north'] not found" in service.compare_photo(d)["error"]


TASTE = {"floor": "light oak herringbone parquet", "walls": "white paint", "lighting": "home", "light_kelvin": 4500,
         "lighting_scale": 1.2, "furniture": {"desk": "white lacquer", "chair": "white plastic"},
         "style": ["scandinavian", "minimal"], "density": "sparse", "note": "明るい北欧風"}


def test_reference_has_no_camera():
    with pytest.raises(ValueError, match="no camera"):
        InputRef(id="ref", kind="reference", camera={"position": [1, 0.3, 1.4], "look_at": [1, 2, 1]})
    r = InputRef(id="ref", kind="reference", taste=TASTE)
    assert r.taste.furniture["desk"] == "white lacquer" and r.camera is None


def test_taste_rows():
    from agentic_sim_world_gen.inputs import taste_rows, taste_summary
    from agentic_sim_world_gen.scene.spec import Taste

    rows = dict(taste_rows(Taste.model_validate(TASTE)))
    assert rows["床"] == "light oak herringbone parquet" and rows["色温度"] == "4500 K" and rows["明るさ"] == "×1.2"
    assert rows["家具：chair"] == "white plastic" and rows["様式"] == "scandinavian, minimal" and rows["物の量"] == "sparse"
    assert taste_summary(Taste.model_validate(TASTE)).startswith("明るい北欧風 / 床 light oak")
    assert taste_summary(Taste()) == ""


def test_reference_scene(fake, tmp_path, monkeypatch):  # noqa: F811
    """A reference image is recorded with its source and taste, but makes no camera and is not compared."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ISAACSIM_PATH", str(tmp_path / "nope"))
    monkeypatch.delenv("ISAACSIM_PYTHON", raising=False)
    jpeg(tmp_path / "mood.jpg", 1920, 1281)
    spec = {**SPEC, "inputs": [{"id": "living", "kind": "reference", "path": "mood.jpg", "taste": TASTE}]}
    d = tmp_path / "s"
    st = service.create_scene(spec, d)
    (i,) = st["inputs"]
    assert i["kind"] == "reference" and i["taste"]["floor"].startswith("light oak")
    assert i["missing"] == ["url", "license"]  # a web image needs its source like a photo
    page = (d / "preview.html").read_text()
    assert "参考画像" in page and "light oak herringbone parquet" in page and "明るい北欧風" in page
    assert ".preview/" in page  # the image is copied next to the page
    assert "📷" not in (d / "layout.svg").read_text()

    # updating keeps the taste; giving a camera to a reference is refused
    st = service.set_input(d, {"id": "living", "url": "https://example.com/mood", "license": "CC0-1.0"})
    assert st["inputs"][0]["taste"]["style"] == ["scandinavian", "minimal"] and "missing" not in st["inputs"][0]
    with pytest.raises(ValueError, match="no camera"):
        service.set_input(d, {"id": "living", "camera": {"position": [1, 0.3, 1.4], "look_at": [1, 2, 1]}})

    (d / "scene.usda").write_text("#usda 1.0\n")
    assert "no input" in service.compare_photo(d)["error"]

    service.license_report(d)
    md = (d / "LICENSES.md").read_text()
    assert "living (reference image for the look only / 参考画像)" in md and "https://example.com/mood" in md

    pxr = pytest.importorskip("pxr")
    service.compose_scene(d)
    stage = pxr.Usd.Stage.Open(str(d / "scene.usda"))
    assert not stage.GetPrimAtPath("/World/Cameras/living")
    src = stage.GetPrimAtPath("/World").GetCustomDataByKey("agentic_sim_world_gen:inputs")
    assert src["living"]["kind"] == "reference" and "床 light oak" in src["living"]["taste"]


def test_light_kelvin(fake, tmp_path):  # noqa: F811
    """Room.light_kelvin sets the fixtures' color temperature instead of the preset's."""
    d = tmp_path / "s"
    service.create_scene({**SPEC, "room": {**SPEC["room"], "lighting": "home"}}, d)
    st = service.set_room(d, light_kelvin=4500)
    assert st["room"]["light_kelvin"] == 4500
    assert "4500 K" in (d / "layout.svg").read_text()
    pxr = pytest.importorskip("pxr")
    service.compose_scene(d)
    stage = pxr.Usd.Stage.Open(str(d / "scene.usda"))
    fx = pxr.UsdLux.SphereLight(stage.GetPrimAtPath("/World/Lights/Fixture_00"))
    assert fx.GetColorTemperatureAttr().Get() == 4500 and fx.GetEnableColorTemperatureAttr().Get()


def test_camera_inside_an_object(fake, tmp_path):  # noqa: F811
    """A photo camera in the chair renders the chair's inside (all black): set_input says so."""
    d = tmp_path / "s"
    service.create_scene(SPEC, d)
    jpeg(tmp_path / "p.jpg", 400, 300)
    ok = service.set_input(d, {"id": "p", "path": str(tmp_path / "p.jpg"),
                               "camera": {"position": [2.0, 0.3, 1.4], "look_at": [2.0, 2.0, 1.0]}})
    assert not any("inside" in w for w in ok.get("warnings", []))
    st = service.set_input(d, {"id": "p", "camera": {"position": [1.0, 1.4, 0.6], "look_at": [1.0, 2.5, 0.6]}})
    assert any("camera of p is inside chair" in w for w in st["warnings"]), st.get("warnings")
    high = service.set_input(d, {"id": "p", "camera": {"position": [1.0, 1.4, 1.9], "look_at": [1.0, 2.5, 1.0]}})
    assert not any("inside" in w for w in high.get("warnings", []))  # above the chair


def test_fit_photos_apply(fake, tmp_path):  # noqa: F811
    """fit_photos with the scene folder writes the room and every photo's camera into the scene (no set_room /
    set_input), in the history and as assumptions 「写真から当てはめ」."""
    from test_photo_fit import CAMS, _request

    d = tmp_path / "s"
    for cid in CAMS:
        jpeg(tmp_path / f"{cid}.jpg", 1280, 960)
    service.create_scene({**SPEC, "objects": [], "room": {**SPEC["room"], "openings": [
        {"id": "door_s", "kind": "door", "wall": "south", "offset": 2.0, "style": "auto_sliding"}]},
        "inputs": [{"id": cid, "path": str(tmp_path / f"{cid}.jpg")} for cid in CAMS]}, d)
    only = service.fit_photos(_request(), d, apply=False)
    assert "applied" not in only and service.load(d).spec.room.width == 3.64
    r = service.fit_photos(_request(), d)
    assert r["applied"]["room"]["width"] == 4.5 and sorted(r["applied"]["cameras"]) == sorted(CAMS)
    s = service.load(d)
    assert (s.spec.room.width, s.spec.room.depth, s.spec.room.height) == (4.5, 3.6, 2.4)
    ops = {o.id: o for o in s.spec.room.openings}
    assert set(ops) == {"door_s", "win_n", "win_e"} and ops["door_s"].offset == 0.7
    assert ops["door_s"].style == "auto_sliding"  # what the fit does not know is kept
    for cid, (pos, _, hfov) in CAMS.items():
        cam = next(i.camera for i in s.spec.inputs if i.id == cid)
        assert math.dist(cam.position, pos) < 0.02 and abs(cam.hfov_deg - hfov) < 0.5
    rows = {a.item: a for a in s.spec.assumptions}
    assert {"size", "openings", "cameras"} <= set(rows) and all(not rows[k].given and rows[k].basis == "写真から当てはめ"
                                                               for k in ("size", "openings", "cameras"))
    assert "4.50 × 3.60 m" in rows["size"].value
    hist = json.loads((d / "preview.json").read_text())["history"]
    assert any(h["text"].startswith("写真から当てはめ：部屋 4.50 × 3.60 m") for h in hist), hist[-1]
    assert not any("outside the room" in w for w in r["warnings"])
    # a photo the scene does not have is named, the rest still applied
    req = _request()
    req["photos"]["extra"] = req["photos"]["s"]
    r2 = service.fit_photos(req, d)
    assert "extra" not in r2["applied"]["cameras"] and any("no photo input with the id of extra" in w for w in r2["warnings"])
    # a camera beyond a wall is warned
    st = service.set_input(d, {"id": "s", "camera": {"position": [2.0, -0.4, 1.4], "look_at": [2.0, 2.0, 1.0]}})
    assert any("camera of s is outside the room" in w for w in st["warnings"]), st.get("warnings")


def test_compare_is_tracked(fake, tmp_path, monkeypatch):  # noqa: F811
    """Never compared, compared, then the scene or the photo's camera changed."""
    jpeg(tmp_path / "p.jpg", 400, 300)
    d = tmp_path / "s"
    service.create_scene({**SPEC, "inputs": [{"id": "p", "path": str(tmp_path / "p.jpg"), "license": "CC0-1.0",
                                              "camera": {"position": [1, 0.3, 1.4], "look_at": [1, 2, 1]}}]}, d)
    st = service.status(d)
    assert st["photo_compare"] == {"compared": False} and "compare_photo" in st["photo_compare_hint"]
    (d / "scene.usda").write_text("#usda 1.0\n")

    def fake_run(script, args, timeout_s):
        open(args[args.index("--compare") + 1], "wb").write(b"png")
        return type("P", (), {"returncode": 0})(), {}

    monkeypatch.setattr(service, "_run_isaac", fake_run)
    r = service.compare_photo(d)
    assert r["ok"] and r["round"] == 1 and "fix them all" in r["hint"]
    assert service.compare_photo(d)["round"] == 1  # the same scene again is not a new round
    st = service.status(d)
    assert st["photo_compare"] == {"compared": True, "rounds": 1} and "photo_compare_hint" not in st
    # an object moved: compare again
    oid = st["objects"][0]["id"]
    service.move_object(d, oid, position=[1.5, 1.5])
    st = service.status(d)
    assert st["photo_compare"]["stale"] and "camera" not in st["photo_compare_hint"]
    # the photo's camera moved: it is where the photo was taken
    r = service.set_input(d, {"id": "p", "camera": {"position": [2, 0.3, 1.4], "look_at": [1, 2, 1]}})
    assert any("where the photo was taken" in w for w in r["warnings"])
    st = service.status(d)
    assert st["photo_compare"]["camera_moved"] == ["p"] and "move the objects" in st["photo_compare_hint"]
    # Rounds count up; from the third on, report instead of fixing small offsets
    assert service.compare_photo(d)["round"] == 2
    service.move_object(d, oid, position=[1.2, 1.5])
    r = service.compare_photo(d)
    assert r["round"] == 3 and "then report" in r["hint"]
    service.move_object(d, oid, position=[1.3, 1.5])
    assert "the last one" in service.status(d)["photo_compare_hint"]


def test_request_with_a_photo():
    from agentic_sim_world_gen.scene.questions import plan_questions

    out = plan_questions("この写真 /tmp/a/shop.JPG みたいな酒屋を作って")
    assert out["photos"] == ["/tmp/a/shop.JPG"] and "set_input" in out["hint"] and "compare_photo" in out["hint"]
    assert "photos" not in plan_questions("酒屋を作って")
