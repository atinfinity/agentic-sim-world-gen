"""Frames of a walk-through video as photo inputs: sharp frames of different views, provenance."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image, ImageFilter, ImageStat

from agentic_sim_world_gen import service, video
from agentic_sim_world_gen.inputs import frame_note
from agentic_sim_world_gen.scene.spec import InputRef

from test_service import SPEC, fake  # noqa: F401  (fixture)

pytestmark = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg not installed")

# 0-2 s testsrc | 2-4 s color bars | 4-5 s mandelbrot blurred, 5-6 s the same sharp | 6-8 s testsrc2 blurred only
# | 8-10 s rgb test pattern
VIEWS = {"testsrc": (0, 2), "bars": (2, 4), "mandelbrot": (5, 6), "rgb": (8, 10)}
BLURRED = [(4, 5), (6, 8)]


@pytest.fixture(scope="module")
def walk(tmp_path_factory):
    d = tmp_path_factory.mktemp("video")
    v = d / "walk.mp4"
    src = ["testsrc", "smptehdbars", "mandelbrot", "testsrc2", "rgbtestsrc"]
    cmd = ["ffmpeg", "-v", "error", "-y"]
    for s in src:
        cmd += ["-f", "lavfi", "-i", f"{s}=size=640x360:rate=10" + (":duration=2" if s != "mandelbrot" else "")]
    cmd += ["-filter_complex",
            "[2]trim=duration=2,setpts=PTS-STARTPTS,split[m1][m2];[m1]trim=duration=1,boxblur=8[mb];"
            "[m2]trim=start=1,setpts=PTS-STARTPTS[ms];[3]boxblur=12[b];"
            "[0][1][mb][ms][b][4]concat=n=6:v=1:a=0,format=yuv420p[v]",
            "-map", "[v]", "-c:v", "libx264", str(v)]
    subprocess.run(cmd, check=True)
    (d / "walk.source.json").write_text(json.dumps(
        {"url": "https://example.com/walk", "title": "Walk", "author": "A", "license": "CC0-1.0"}))
    return v


def test_choose_sharp_frames_of_each_view(walk, tmp_path):
    r = video.extract(walk, tmp_path / "inputs")
    times = [f["time_s"] for f in r["frames"]]
    assert len(times) == 4  # one per sharp view, the blur-only view dropped
    for (name, (a, b)), t in zip(VIEWS.items(), times):
        assert a <= t < b, (name, times)
    assert not any(a <= t < b for t in times for a, b in BLURRED)
    assert r["dropped_blurry_views"] == 1
    assert r["source"]["license"] == "CC0-1.0"
    f = tmp_path / "inputs" / f"{r['frames'][2]['id']}.jpg"
    assert f.exists() and "best frame of view" in r["frames"][2]["why"]
    side = json.loads(f.with_name(f.stem + ".source.json").read_text())
    assert side["url"] == "https://example.com/walk" and side["video"] == str(walk.resolve())
    assert side["time_s"] == r["frames"][2]["time_s"]
    assert (tmp_path / "inputs" / "walk_frames.jpg").exists()
    # the written frame is the one measured (same time, not a blurred neighbor)
    for fr in r["frames"]:
        im = Image.open(fr["path"]).convert("L")
        im = im.resize((video.ANALYSIS_W, round(video.ANALYSIS_W * im.height / im.width)))
        assert ImageStat.Stat(im.filter(video._LAPLACE)).var[0] == pytest.approx(fr["sharpness"], rel=0.3)


def test_frame_counts(walk, tmp_path):
    assert len(video.extract(walk, tmp_path / "a", max_frames=2)["frames"]) == 2
    many = video.extract(walk, tmp_path / "b", max_frames=50, min_frames=6)["frames"]
    assert 6 <= len(many) <= video.HARD_MAX  # the pattern has 4 views: smaller view steps / fillers make up 6
    assert [f["time_s"] for f in many] == sorted(f["time_s"] for f in many)
    assert len({f["id"] for f in many}) == len(many)


def test_frame_id():
    assert video.frame_id("walk", 5.75) == "walk_t005_75"
    assert video.frame_id("2024 room-tour", 12.0) == "v_2024_room_tour_t012_00"
    InputRef(id=video.frame_id("2024 room-tour", 12.0))  # a valid input id


def test_extract_into_scene(fake, walk, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.chdir(tmp_path)
    d = tmp_path / "s"
    pre = service.extract_frames(walk, tmp_path / "pre")  # no scene yet: inputs for create_scene
    assert pre["ok"] and not pre["recorded"] and len(pre["inputs"]) == 4
    assert pre["inputs"][0]["video"] == str(walk.resolve()) and pre["inputs"][0]["license"] == "CC0-1.0"

    service.create_scene(SPEC, d)
    r = service.extract_frames(walk, d, max_frames=4)
    assert r["ok"] and r["recorded"] and "missing_source" not in r
    st = service.status(d)
    ins = st["inputs"]
    assert [i["id"] for i in ins] == [f["id"] for f in r["frames"]]
    assert all(i["kind"] == "photo" and i["time_s"] == f["time_s"] for i, f in zip(ins, r["frames"]))
    assert all("missing" not in i for i in ins)

    # a camera set on a frame survives extracting again
    fid = ins[1]["id"]
    service.set_input(d, {"id": fid, "camera": {"position": [1, 0.3, 1.4], "look_at": [1, 2, 1.2]}})
    service.extract_frames(walk, d, max_frames=4)
    ins = service.status(d)["inputs"]
    assert len(ins) == 4 and next(i for i in ins if i["id"] == fid)["camera"]["position"] == [1, 0.3, 1.4]

    service.license_report(d)
    md = (d / "LICENSES.md").read_text()
    assert f"frame at {r['frames'][1]['time_s']:.1f} s of walk.mp4" in md and "https://example.com/walk" in md

    pxr = pytest.importorskip("pxr")
    service.compose_scene(d)
    stage = pxr.Usd.Stage.Open(str(d / "scene.usda"))
    data = stage.GetPrimAtPath("/World").GetCustomDataByKey("agentic_sim_world_gen:inputs")
    assert data[fid]["video"] == str(walk.resolve()) and float(data[fid]["time_s"]) == pytest.approx(
        next(f["time_s"] for f in r["frames"] if f["id"] == fid), abs=0.01)
    assert stage.GetPrimAtPath(f"/World/Cameras/{fid}")


def test_missing_source(walk, tmp_path):
    v = tmp_path / "nosrc.mp4"
    shutil.copy(walk, v)
    r = service.extract_frames(v, tmp_path / "s", max_frames=4)
    assert r["ok"] and r["missing_source"] == ["license", "url"]
    r = service.extract_frames(v, tmp_path / "s2", max_frames=4, url="https://example.com/v", license="own video")
    assert "missing_source" not in r and r["inputs"][0]["license"] == "own video"


def test_errors(tmp_path, monkeypatch):
    assert not service.extract_frames(tmp_path / "none.mp4", tmp_path)["ok"]
    (tmp_path / "bad.mp4").write_text("not a video")
    r = service.extract_frames(tmp_path / "bad.mp4", tmp_path)
    assert not r["ok"] and "video" in r["error"]
    monkeypatch.setattr(video.shutil, "which", lambda _: None)
    monkeypatch.setattr(video, "_SEARCH", ())
    r = service.extract_frames(tmp_path / "bad.mp4", tmp_path)
    assert not r["ok"] and "install ffmpeg" in r["error"]


def test_frame_note():
    assert frame_note(InputRef(path="a.jpg")) is None
    assert frame_note(InputRef(video="/x/walk.mp4", time_s=3.25)) == "frame at 3.2 s of walk.mp4"


def test_pan_skips_motion_blur(tmp_path):
    """Hold - fast blurred pan - hold - fast pan - hold over a CC0 room photo, then a cut to another: the frames
    come from the holds, none from the pans (many blurred pan samples must not lower the bar)."""
    photos = Path(__file__).resolve().parents[1] / "examples" / "photos"
    v = tmp_path / "pan.mp4"
    x = "if(lt(t,2),0,if(lt(t,3),(t-2)*600,if(lt(t,5),600,if(lt(t,6),600+(t-5)*600,1200))))"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-framerate", "30", "-t", "8",
                    "-i", str(photos / "living_room.jpg"), "-loop", "1", "-framerate", "30", "-t", "3",
                    "-i", str(photos / "dining_kitchen.jpg"), "-filter_complex",
                    f"[0]crop=w=720:h=540:x='{x}':y=400,boxblur=10:enable='between(t,2,3)+between(t,5,6)',setsar=1[a];"
                    "[1]crop=w=720:h=540:x='600+t*30':y=500,setsar=1[b];[a][b]concat=n=2:v=1:a=0,format=yuv420p[v]",
                    "-map", "[v]", "-c:v", "libx264", str(v)], check=True)
    r = video.extract(v, tmp_path / "inputs")
    times = [f["time_s"] for f in r["frames"]]
    assert len(times) == 4 and r["dropped_blurry_views"] >= 2
    assert not any(a <= t <= b for t in times for a, b in ((2, 3), (5, 6))), times
    assert [sum(a <= t < b for t in times) for a, b in ((0, 2), (3, 5), (6, 8), (8, 11))] == [1, 1, 1, 1]


def _room_photo() -> Image.Image:
    im = Image.open(Path(__file__).resolve().parents[1] / "examples" / "photos" / "living_room.jpg").convert("RGB")
    return im.resize((video.ANALYSIS_W, round(video.ANALYSIS_W * im.height / im.width)))


def test_sharpness_normalized_by_contrast():
    """A dark (or low-contrast) but sharp frame is not taken for a blurred one: the raw Laplacian variance drops
    with the contrast, the normalized sharpness does not."""
    from PIL import ImageEnhance

    bright = _room_photo()
    dark = ImageEnhance.Brightness(bright).enhance(0.4)
    blurred = bright.filter(ImageFilter.GaussianBlur(3))
    b, d, bl = video.measure(bright), video.measure(dark), video.measure(blurred)
    assert d.sharpness < 0.3 * b.sharpness  # raw: looks blurred
    assert d.sharp_n > 0.7 * b.sharp_n  # normalized: as sharp as the bright one
    assert bl.sharp_n < 0.5 * d.sharp_n  # a really blurred frame still is
    assert b.room > 0.8 and "dark" not in " ".join(video._notes(b))


def test_room_score_sky_and_close_up():
    room = video.measure(_room_photo())
    # a balcony view: smooth blue sky in the upper part, the room's floor below
    im = _room_photo()
    w, h = im.size
    sky = Image.new("RGB", (w, h * 3 // 5))
    sky.putdata([(100 + y // 4, 150 + y // 4, 230) for y in range(sky.height) for _ in range(w)])
    im.paste(sky, (0, 0))
    out = video.measure(im)
    assert out.sky > 0.3 and out.room < 0.6 * room.room
    assert any("sky" in n for n in video._notes(out))
    # a close-up of a plain wall with a switch: outlines only in the middle
    wall = Image.new("RGB", (w, h))
    wall.putdata([(200 + x // 40, 198 + x // 40, 190 + y // 40) for y in range(h) for x in range(w)])
    wall.paste((250, 250, 245), (w // 2 - 20, h // 2 - 30, w // 2 + 20, h // 2 + 30))
    cu = video.measure(wall)
    assert cu.spread < video.SPREAD_LOW + 0.1 and cu.room < 0.2
    assert any("close-up" in n for n in video._notes(cu))


def test_start_end(walk, tmp_path):
    r = video.extract(walk, tmp_path / "a", start_s=4.5, end_s=10)
    times = [f["time_s"] for f in r["frames"]]
    assert times and all(4.5 <= t <= 10 for t in times)
    assert r["range_s"][0] >= 4.5
    assert any(5 <= t < 6 for t in times) and any(8 <= t < 10 for t in times)  # mandelbrot, rgb
    with pytest.raises(ValueError):
        video.extract(walk, tmp_path / "b", start_s=6, end_s=5)
    with pytest.raises(ValueError):
        video.extract(walk, tmp_path / "b", start_s=99)
    r = service.extract_frames(walk, tmp_path / "s", start_s=0, end_s=4)
    assert r["ok"] and all(f["time_s"] <= 4 for f in r["frames"])
    assert not service.extract_frames(walk, tmp_path / "s", start_s=-1)["ok"]


def test_times(fake, walk, tmp_path, monkeypatch):  # noqa: F811
    """Frames at asked times replace the scene's frames of the video (a weak frame moved)."""
    monkeypatch.chdir(tmp_path)
    d = tmp_path / "s"
    service.create_scene(SPEC, d)
    r = service.extract_frames(walk, d, max_frames=4)
    old = [f["time_s"] for f in r["frames"]]
    want = [*old[:-1], round(old[-1] - 0.7, 3)]
    r = service.extract_frames(walk, d, times=want)
    assert r["ok"] and [f["time_s"] for f in r["frames"]] == sorted(want)
    assert all(f["why"] == "asked for this time" for f in r["frames"])
    ins = service.status(d)["inputs"]
    assert sorted(i["time_s"] for i in ins) == sorted(want) and len(ins) == 4
    gone = video.frame_id(walk.stem, old[-1])
    assert (d / "inputs" / f"{gone}.jpg").exists() and gone not in {i["id"] for i in ins}  # video_poses skips it
    assert not service.extract_frames(walk, d, times=[999])["ok"]


def test_people_check(walk, tmp_path, monkeypatch):
    r = service.extract_frames(walk, tmp_path / "a", max_frames=4)
    if r["people_check"] == "none":
        assert "check the frames for people yourself" in r["hint_people"]
    assert "anyone is visible" in r["hint"]
    monkeypatch.setattr(video, "people_detector", lambda: (lambda f: 2 if "t005" in Path(f).name else 0))
    r = service.extract_frames(walk, tmp_path / "b", max_frames=4)
    hit = [f["id"] for f in r["frames"] if f.get("people")]
    assert hit and r["people_frames"] == hit and hit[0] in r["hint_people"]


def _diff(a: Image.Image, b: Image.Image) -> float:
    a, b = a.convert("L").resize((64, 36)), b.convert("L").resize((64, 36))
    from PIL import ImageChops
    return ImageStat.Stat(ImageChops.difference(a, b)).mean[0]


def test_rotate(fake, walk, tmp_path, monkeypatch):  # noqa: F811
    """A video without rotation metadata that comes out on its side: rotate turns the frames, the sidecar keeps it
    (video_poses decodes the same way) and frames at asked times keep the turn."""
    up = video.extract(walk, tmp_path / "up", times=[1.0, 5.5])
    r = video.extract(walk, tmp_path / "cw", times=[1.0, 5.5], rotate=90)
    assert r["rotate"] == 90 and r["rotation_metadata"] == 0 and r["size"] == [360, 640]
    for a, b in zip(up["frames"], r["frames"]):
        im = Image.open(b["path"])
        assert im.height > im.width
        turned = Image.open(a["path"]).transpose(Image.Transpose.ROTATE_270)  # 90 degrees clockwise
        assert _diff(im, turned) < 3 < _diff(im, Image.open(a["path"]).transpose(Image.Transpose.ROTATE_90))
        assert json.loads(Path(b["path"]).with_suffix(".source.json").read_text())["rotate"] == 90
    assert "rotate" not in json.loads(Path(up["frames"][0]["path"]).with_suffix(".source.json").read_text())
    r = video.extract(walk, tmp_path / "flip", times=[1.0], rotate=180)
    turned = Image.open(up["frames"][0]["path"]).transpose(Image.Transpose.ROTATE_180)
    assert _diff(Image.open(r["frames"][0]["path"]), turned) < 3
    with pytest.raises(ValueError):
        video.extract(walk, tmp_path / "x", times=[1.0], rotate=45)
    assert video.check_rotate(-90) == 270 and video.check_rotate(None) == 0
    # the scene's frames of a video keep their turn when moved to other times
    monkeypatch.chdir(tmp_path)
    d = tmp_path / "s"
    service.create_scene(SPEC, d)
    r = service.extract_frames(walk, d, max_frames=4, rotate=270)
    assert r["ok"] and r["rotate"] == 270 and "turned 270 degrees" in r["hint"]
    r = service.extract_frames(walk, d, times=[f["time_s"] for f in r["frames"]][:2])
    assert r["ok"] and r["rotate"] == 270
    assert not service.extract_frames(walk, d, times=[1.0], rotate=30)["ok"]
    assert service.extract_frames(walk, d, times=[1.0], rotate=0)["rotate"] == 0


def test_rotation_metadata(walk, tmp_path):
    """A video whose file says how to turn it: ffmpeg turns the frames itself, rotate turns them further."""
    v = tmp_path / "tagged.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-display_rotation:v:0", "270", "-i", str(walk), "-map", "0:v:0",
                    "-c", "copy", str(v)], check=True)
    info = video.probe(v)
    assert info["rotation"] == 90 and (info["width"], info["height"]) == (640, 360)
    assert video.frame_size(info) == (360, 640) and video.frame_size(info, 90) == (640, 360)
    r = video.extract(v, tmp_path / "a", times=[1.0])
    assert r["rotation_metadata"] == 90 and r["size"] == [360, 640]
    im = Image.open(r["frames"][0]["path"])
    assert im.height > im.width
    r = video.extract(v, tmp_path / "b", times=[1.0], rotate=270)
    assert r["size"] == [640, 360] and Image.open(r["frames"][0]["path"]).width == 640
