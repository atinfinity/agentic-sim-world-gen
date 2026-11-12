"""Pick a few good frames of a walk-through video, to use them as photos of one room (stage 1).

Not evenly spaced: the video (or the part between `start_s` and `end_s`) is decoded once at a low rate and size
(ffmpeg), each sample gets
  * sharpness: variance of the Laplacian of the gray image (motion blur and defocus make it small); for comparing
    frames it is normalized by the local contrast (per tile, the Laplacian variance over the intensity variance,
    on the tiles that have structure), so a dark, sharp room is not beaten by a bright view out of the window,
  * a room score in 0..1 that is low for views that are not the room: blue sky in the upper half (outdoors, the
    balcony), a close-up (a wall or an object fills the frame: few tiles with structure) and black / faded frames,
  * a tiny color thumbnail, whose mean difference from another sample says how much the view changed.
Samples are cut into "views" wherever the thumbnail has drifted far enough from the view's first sample
(a pan, walking into the next corner, a cut). Each view gives its best sample (sharp and room-like); views that
are only blur are dropped, and so are views that are not the room (unless too few views are left); if there are
more views than `max_frames`, the ones that differ most from what is already chosen are kept (farthest-point,
weighted by sharpness and room score). The chosen times are then extracted at full resolution (long side capped)
as JPEG with a `<stem>.source.json` carrying the video's source (from the video's own `<video stem>.source.json`)
plus `video` and `time_s`.

People: if OpenCV (cv2) happens to be installed, its built-in HOG person detector (no model download) flags
chosen frames that may show a person (`people`); otherwise nothing is detected and the agent has to look.

ffmpeg / ffprobe must be installed (Ubuntu: apt install ffmpeg, Mac: brew install ffmpeg). Pillow only,
no numpy / OpenCV needed. With OpenCV, agentic_sim_world_gen.video_pose measures how the camera turned and moved between the
chosen frames (for fit_photos).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageChops, ImageFilter, ImageStat

from agentic_sim_world_gen.inputs import SIDECAR_FIELDS, sidecar_path

DEFAULT_MAX = 8
DEFAULT_MIN = 4
HARD_MAX = 12  # each frame is read (and later rendered) by the agent: cost grows with the count
LONG_SIDE = 1920  # extracted frames are scaled down to this long side
ANALYSIS_W = 320  # width of the decoded samples (sharpness)
THUMB = (16, 12)  # thumbnail for the view difference
MAX_SAMPLES = 600
CHANGE = 0.12  # mean thumbnail difference (0..1) that starts a new view
# a view is blur only (fast pan, defocus) if its sharpest sample is below BLURRY_LOCAL of the sharpest sample within
# LOCAL_S seconds (content changes little in a second; blur changes a lot) or below BLURRY_ABS of the video's 90th
# percentile
BLURRY_LOCAL, LOCAL_S, BLURRY_ABS = 0.4, 1.0, 0.15
TILES = (8, 6)  # tiles for the contrast-normalized sharpness and the structure spread
TILE_VAR = 20.0  # a tile has texture if its intensity variance is above this (and above TILE_REL of the frame's)
TILE_REL = 0.05
# structure spread: the gray image shrunk to COARSE (fine texture and noise gone), has an edge
# above EDGE in this share of the 8x6 cells. A view into a room has outlines everywhere (furniture, wall/floor
# lines); a close-up (a wall, a switch, a cupboard door) only in a part. SPREAD_LOW or less: not the room at all.
COARSE, EDGE = (40, 30), 40  # not contrast-stretched: a low-contrast frame is a wall
SPREAD_LOW, SPREAD_FULL = 0.3, 0.8
SKY_FROM, SKY_FULL = 0.15, 0.4  # share of the upper half that is blue sky: no penalty below, not the room above
DARK = 40.0  # mean gray below this: a dark / black / faded frame (scaled down)
ROOM_MIN = 0.4  # views with a lower room score are dropped (unless too few views are left)
_LAPLACE = ImageFilter.Kernel((3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], scale=1, offset=128)
_SEARCH = ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin")  # MCP servers may run with a short PATH


class FfmpegMissing(RuntimeError):
    pass


def tool(name: str) -> str:
    """Path of ffmpeg / ffprobe, or FfmpegMissing with how to install it."""
    if p := shutil.which(name):
        return p
    for d in _SEARCH:
        if os.access(c := os.path.join(d, name), os.X_OK):
            return c
    raise FfmpegMissing(f"{name} not found: install ffmpeg (Ubuntu: sudo apt install ffmpeg, Mac: brew install ffmpeg)")


ROTATIONS = {0: "", 90: "transpose=1,", 180: "hflip,vflip,", 270: "transpose=2,"}  # turn clockwise (ffmpeg -vf)


def check_rotate(rotate: int | None) -> int:
    """0, 90, 180 or 270 (degrees clockwise), or ValueError."""
    r = int(rotate or 0) % 360
    if r not in ROTATIONS:
        raise ValueError(f"rotate is 0, 90, 180 or 270 (degrees clockwise), not {rotate}")
    return r


def rotate_vf(rotate: int | None) -> str:
    """The -vf filters (ending with a comma, or "") that turn the decoded frames `rotate` degrees clockwise, after
    ffmpeg's own turn from the video's rotation metadata."""
    return ROTATIONS[check_rotate(rotate)]


def frame_size(info: dict[str, Any], rotate: int | None = 0) -> tuple[int, int]:
    """(w, h) of the decoded frames: the stored size turned by the rotation metadata (ffmpeg applies it) and then
    by `rotate`."""
    w, h = info["width"], info["height"]
    if (info.get("rotation", 0) + check_rotate(rotate)) % 180:
        w, h = h, w
    return w, h


def frames_rotate(frames: list[dict[str, Any]]) -> int:
    """The turn (degrees clockwise) the frames of one video were extracted with, or ValueError if they differ."""
    rs = {check_rotate(f.get("rotate")) for f in frames}
    if len(rs) > 1:
        raise ValueError(f"the frames of one video were extracted with different rotate ({sorted(rs)}): "
                         "extract them again with one rotate")
    return rs.pop() if rs else 0


def probe(video: Path) -> dict[str, Any]:
    """duration [s], width, height (as stored, before rotation metadata), rotation (of the metadata, degrees
    clockwise, that ffmpeg applies when decoding) and fps of the first video stream."""
    r = subprocess.run([tool("ffprobe"), "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height,avg_frame_rate,duration:stream_side_data=rotation:format=duration",
                        "-of", "json", str(video)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise ValueError(f"not a readable video: {video} ({r.stderr.strip()[:200]})")
    d = json.loads(r.stdout)
    if not d.get("streams"):
        raise ValueError(f"no video stream in {video}")
    st = d["streams"][0]
    num, _, den = str(st.get("avg_frame_rate") or "0/1").partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 0.0
    dur = float(st.get("duration") or d.get("format", {}).get("duration") or 0)
    rot = next((float(x["rotation"]) for x in st.get("side_data_list") or [] if "rotation" in x), 0.0)
    # the display matrix turns counterclockwise: -90 shows the stored picture turned 90 degrees clockwise
    return {"duration_s": dur, "width": int(st["width"]), "height": int(st["height"]), "fps": fps,
            "rotation": int(round(-rot / 90)) * 90 % 360}


@dataclass
class Sample:
    t: float
    sharpness: float  # Laplacian variance of the gray image (raw)
    thumb: Image.Image = field(repr=False)
    sharp_n: float = 0.0  # contrast-normalized sharpness (compared across the video)
    spread: float = 1.0  # share of the frame with coarse edges (outlines)
    sky: float = 0.0  # share of the upper half that looks like blue sky
    mean: float = 128.0  # mean gray
    room: float = 1.0  # 0..1: how much this looks like a view of the room (not sky, close-up or black)


def _diff(a: Image.Image, b: Image.Image) -> float:
    return sum(ImageStat.Stat(ImageChops.difference(a, b)).mean) / 3 / 255


def measure(im: Image.Image, t: float = 0.0) -> Sample:
    """Sharpness (raw and normalized), structure spread, sky and room score of one decoded sample."""
    g = im.convert("L")
    lap = g.filter(_LAPLACE)
    st = ImageStat.Stat(g)
    w, h = g.size
    nx, ny = TILES
    floor = max(TILE_VAR, TILE_REL * st.var[0])
    ratios = []
    for j in range(ny):
        for i in range(nx):
            box = (i * w // nx, j * h // ny, (i + 1) * w // nx, (j + 1) * h // ny)
            v = ImageStat.Stat(g.crop(box)).var[0]
            if v > floor:  # flat tiles (a wall, the sky, black) carry only noise
                ratios.append(ImageStat.Stat(lap.crop(box)).var[0] / (v + 25))
    ratios.sort()
    upper = ratios[len(ratios) // 2:]
    spread = structure_spread(g)
    raw = ImageStat.Stat(lap).var[0]
    if upper:
        sharp_n = sum(upper) / len(upper)
    else:  # no tile with structure (a few flat bands, black): normalize over the whole frame, 0 if it is flat
        sharp_n = raw / (st.var[0] + 25) if st.stddev[0] > 10 else 0.0
    s = Sample(t=t, sharpness=raw, thumb=im.resize(THUMB, Image.Resampling.BOX), sharp_n=sharp_n, spread=spread,
               sky=sky_share(im), mean=st.mean[0])
    sky_pen = min(1.0, max(0.0, (s.sky - SKY_FROM) / (SKY_FULL - SKY_FROM)))
    close = min(1.0, max(0.0, (spread - SPREAD_LOW) / (SPREAD_FULL - SPREAD_LOW)))
    s.room = close * (1 - sky_pen) * min(1.0, s.mean / DARK)
    return s


def structure_spread(g: Image.Image) -> float:
    """Share of the 8x6 cells of the frame with a coarse edge (see COARSE / EDGE)."""
    w, h = COARSE
    e = g.resize(COARSE, Image.Resampling.BOX).filter(ImageFilter.FIND_EDGES)
    e = e.crop((1, 1, w - 1, h - 1))  # FIND_EDGES leaves the border
    nx, ny = TILES
    cw, ch = (w - 2) / nx, (h - 2) / ny
    n = sum(1 for j in range(ny) for i in range(nx)
            if e.crop((round(i * cw), round(j * ch), round((i + 1) * cw), round((j + 1) * ch))).getextrema()[1] > EDGE)
    return n / (nx * ny)


def sky_share(im: Image.Image) -> float:
    """Share of the upper half that is bright, blue and smooth, if the top is clearly bluer than the bottom
    (camera white balance can make a whole room bluish; a ceiling or a white wall is not blue)."""
    sm = im.resize((64, 48), Image.Resampling.BOX)
    r, _, b = sm.split()
    br = ImageChops.subtract(b, r, offset=128)
    top = ImageStat.Stat(br.crop((0, 0, 64, 14))).mean[0] - 128
    bottom = ImageStat.Stat(br.crop((0, 24, 64, 48))).mean[0] - 128
    if top - bottom < 15:
        return 0.0
    edges = sm.convert("L").filter(ImageFilter.FIND_EDGES)
    half = sm.crop((0, 0, 64, 24))
    ed = edges.crop((0, 0, 64, 24))
    px = zip(half.get_flattened_data() if hasattr(half, "get_flattened_data") else half.getdata(),
             ed.get_flattened_data() if hasattr(ed, "get_flattened_data") else ed.getdata())
    n = sum(1 for (pr, pg, pb), e in px if pb > pr + 15 and pb >= pg - 5 and pr + pg + pb > 420 and e < 20)
    return n / (64 * 24)


def samples(video: Path, duration_s: float, rate: float | None = None, start_s: float | None = None,
            end_s: float | None = None, rotate: int = 0) -> list[Sample]:
    """Decode the video (between start_s and end_s, if given) once at about `rate` fps (default: 4, fewer for
    long videos) and measure every sample. The samples are original frames (select, not fps: no duplicated or
    merged frames) and keep their own time in the video (showinfo, timestamps kept when seeking), so the frame
    extracted later at that time is the one measured. rotate: turn the frames clockwise (rotate_vf)."""
    a = max(0.0, start_s or 0.0)
    b = min(duration_s, end_s) if end_s is not None and duration_s > 0 else (end_s or duration_s)
    span = max((b or 0) - a, 0.1)
    rate = rate or max(0.2, min(4.0, MAX_SAMPLES / span))
    vf = (f"select='isnan(prev_selected_t)+gte(t-prev_selected_t,{1 / rate:.4f})',"
          f"{rotate_vf(rotate)}scale='min({ANALYSIS_W},iw)':-2,showinfo")
    cut = []
    if a > 0:
        cut += ["-ss", f"{a:.3f}"]
    if end_s is not None:
        cut += ["-t", f"{span:.3f}"]
    with tempfile.TemporaryFile() as log:
        p = subprocess.Popen([tool("ffmpeg"), "-hide_banner", "-nostats", *cut, "-copyts", "-i", str(video), "-an",
                              "-vf", vf, "-fps_mode", "passthrough", "-f", "image2pipe", "-vcodec", "ppm", "-"],
                             stdout=subprocess.PIPE, stderr=log)
        ims: list[Sample] = []
        assert p.stdout is not None
        try:
            while im := _read_ppm(p.stdout):
                ims.append(measure(im))
        finally:
            p.stdout.close()
            p.wait()
        log.seek(0)
        err = log.read().decode(errors="replace")
    times = [float(m) for m in re.findall(r"Parsed_showinfo.*?pts_time:\s*([-0-9.e]+)", err)]
    if p.returncode != 0 and not ims:
        tail = [ln for ln in err.splitlines() if "showinfo" not in ln][-3:]
        raise ValueError(f"ffmpeg could not decode {video}: {' / '.join(tail)[:300]}")
    for k, sm in enumerate(ims):
        sm.t = max(0.0, times[k]) if k < len(times) else a + k / rate
    return ims


def _read_ppm(f) -> Image.Image | None:
    """One binary PPM (P6) from a stream of them, or None at the end."""
    head: list[bytes] = []
    while len(head) < 4:  # P6, width, height, maxval
        tok = b""
        while True:
            c = f.read(1)
            if not c:
                return None
            if c == b"#" and not tok:
                f.readline()
                continue
            if c.isspace():
                if tok:
                    break
                continue
            tok += c
        head.append(tok)
    w, h = int(head[1]), int(head[2])
    data = f.read(w * h * 3)
    if len(data) < w * h * 3:
        return None
    return Image.frombytes("RGB", (w, h), data)


def views(ss: list[Sample], change: float = CHANGE) -> list[list[int]]:
    """Cut the samples into runs that show about the same view (thumbnail drift from the run's first)."""
    runs: list[list[int]] = []
    for k, s in enumerate(ss):
        if runs and _diff(s.thumb, ss[runs[-1][0]].thumb) < change:
            runs[-1].append(k)
        else:
            runs.append([k])
    return runs


def blurry_at(ss: list[Sample], k: int) -> bool:
    """Sample k is much less sharp than its neighbors in time, or than most of the video (normalized sharpness,
    so dark stretches are not all blur next to a bright one)."""
    near = max(s.sharp_n for s in ss if abs(s.t - ss[k].t) <= LOCAL_S)
    p90 = sorted(s.sharp_n for s in ss)[int(0.9 * (len(ss) - 1))]
    return ss[k].sharp_n < BLURRY_LOCAL * near or ss[k].sharp_n < BLURRY_ABS * p90


def _value(s: Sample, top: float) -> float:
    """How good a sample is as a photo of the room: sharp (relative to the sharpest) and room-like."""
    return (s.sharp_n / top) ** 0.5 * s.room if top > 0 else s.room


def choose(ss: list[Sample], max_frames: int = DEFAULT_MAX, min_frames: int = DEFAULT_MIN) -> list[dict[str, Any]]:
    """Indices of the chosen samples with why each was chosen, in time order."""
    if not ss:
        return []
    max_frames = max(1, min(max_frames, HARD_MAX, len(ss)))
    min_frames = max(1, min(min_frames, max_frames))
    change, runs = CHANGE, views(ss)
    while len(runs) < min_frames and change > 0.01:  # a slow pan: smaller steps count as a new view
        change /= 2
        runs = views(ss, change)
    top = max(s.sharp_n for s in ss)
    best = [max(r, key=lambda k: _value(ss[k], top)) for r in runs]
    sharp = [(r, b) for r, b in zip(runs, best) if not blurry_at(ss, b)] or [
        max(zip(runs, best), key=lambda x: ss[x[1]].sharp_n)]
    blurry = len(runs) - len(sharp)
    keep = [(r, b) for r, b in sharp if ss[b].room >= ROOM_MIN]
    if len(keep) < min_frames:  # too few views of the room: take the best of the others back
        rest = sorted((x for x in sharp if x not in keep), key=lambda x: -_value(ss[x[1]], top))
        keep = sorted(keep + rest[:min_frames - len(keep)], key=lambda x: x[0][0])
    not_room = len(sharp) - len(keep)
    # farthest-point: start from the first view of the room (it sets the room's orientation), then the view that
    # differs most from every chosen one, weighted by how sharp and room-like it is
    chosen = [0]
    while len(chosen) < min(max_frames, len(keep)):
        def score(j: int) -> float:
            b = keep[j][1]
            return min(_diff(ss[b].thumb, ss[keep[c][1]].thumb) for c in chosen) * _value(ss[b], top)
        j = max((j for j in range(len(keep)) if j not in chosen), key=score)
        chosen.append(j)
    picks = []
    for j in sorted(chosen):
        r, b = keep[j]
        others = [keep[c][1] for c in chosen if c != j]
        novelty = min((_diff(ss[b].thumb, ss[o].thumb) for o in others), default=1.0)
        picks.append({"sample": b, "view": runs.index(r), "view_s": (ss[r[0]].t, ss[r[-1]].t), "novelty": novelty})
    # a static video gives fewer views than min_frames: add the best samples far in time from the chosen
    taken = {p["sample"] for p in picks}
    while len(picks) < min_frames and len(taken) < len(ss):
        def gap(k: int) -> float:
            return min(abs(ss[k].t - ss[t].t) for t in taken) * _value(ss[k], top)
        k = max((k for k in range(len(ss)) if k not in taken), key=gap)
        taken.add(k)
        picks.append({"sample": k, "view": None, "view_s": None, "novelty": None})
    picks.sort(key=lambda p: ss[p["sample"]].t)
    for p in picks:
        p["dropped_blurry_views"] = blurry
        p["dropped_not_room_views"] = not_room
    return picks


def _notes(s: Sample) -> list[str]:
    """Why a sample may not be a good view of the room."""
    out = []
    if s.sky >= SKY_FROM:
        out.append(f"sky in {s.sky:.0%} of the upper half (outdoors?)")
    if s.spread < 0.6:
        out.append(f"outlines in {s.spread:.0%} of the frame (close-up?)")
    if s.mean < DARK:
        out.append("dark")
    return out


def _why(p: dict[str, Any], s: Sample, sharp_rel: float) -> str:
    notes = "".join(f"; {n}" for n in _notes(s))
    if p["view"] is None:
        return f"filler: the video shows few different views (sharpness {sharp_rel:.0%} of the best{notes})"
    a, b = p["view_s"]
    nov = "" if p["novelty"] is None else f", view differs {p['novelty']:.2f} from the nearest other pick"
    return (f"best frame of view {p['view'] + 1} ({a:.1f}-{b:.1f} s; {sharp_rel:.0%} of the best sharpness, "
            f"room score {s.room:.2f}{notes}){nov}")


def frame_id(stem: str, t: float) -> str:
    """Input id of a frame: '<stem>_t012_5' (letters, digits, _; starts with a letter or _)."""
    s = re.sub(r"[^A-Za-z0-9_]", "_", stem) or "video"
    if not re.match(r"[A-Za-z_]", s):
        s = "v_" + s
    return f"{s}_t{t:06.2f}".replace(".", "_")


def video_source(video: Path) -> dict[str, Any]:
    """url/title/author/license of the video from its `<stem>.source.json`, if present."""
    side = sidecar_path(video)
    if not side.exists():
        return {}
    data = json.loads(side.read_text(encoding="utf-8"))
    return {k: data[k] for k in SIDECAR_FIELDS if data.get(k)}


def people_detector() -> Callable[[Path], int] | None:
    """A person detector if OpenCV happens to be installed (its built-in HOG people detector: no model to
    download), else None. Returns the number of people found in an image file. A hint, not a guarantee: it misses
    people seen in part or close up, so the agent still looks at the frames."""
    try:
        import cv2  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001  (not installed, or broken)
        return None
    if not hasattr(cv2, "HOGDescriptor"):  # OpenCV 5 moved it out of the main module
        return None
    hog = cv2.HOGDescriptor()
    hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())

    def detect(path: Path) -> int:
        img = cv2.imread(str(path))
        if img is None:
            return 0
        if img.shape[1] > 640:
            img = cv2.resize(img, (640, round(640 * img.shape[0] / img.shape[1])))
        _, weights = hog.detectMultiScale(img, winStride=(8, 8), padding=(8, 8), scale=1.05)
        return sum(1 for w in getattr(weights, "ravel", lambda: weights)() if float(w) > 0.5)

    return detect


def extract(video: str | Path, out_dir: str | Path, max_frames: int = DEFAULT_MAX, min_frames: int = DEFAULT_MIN,
            source: dict[str, Any] | None = None, rate: float | None = None, start_s: float | None = None,
            end_s: float | None = None, times: list[float] | None = None, rotate: int = 0) -> dict[str, Any]:
    """Choose and write the frames (only from start_s..end_s of the video, if given: one room of a walk through
    several). times: write the frames at exactly these seconds instead of choosing (e.g. a frame moved to where
    video_poses holds it better). Returns {video, duration_s, size, source, range_s, frames [{id, path,
    time_s, sharpness, room, notes, why, people?}], people_check, sheet (contact sheet jpg), rotate, rotation_metadata}.
    `source` overrides the video's sidecar fields. rotate: turn the frames 90, 180 or 270 degrees clockwise, after
    ffmpeg's own turn from the file's rotation metadata (a phone video shot upright whose file has no such metadata
    comes out lying on its side); recorded in each frame's sidecar, so video_poses decodes the video turned the
    same way."""
    v = Path(video).expanduser().resolve()
    if not v.exists():
        raise FileNotFoundError(f"video not found: {v}")
    info = probe(v)
    dur = info["duration_s"]
    if start_s is not None and start_s < 0 or end_s is not None and end_s <= 0:
        raise ValueError("start_s / end_s are seconds from the start of the video (start_s >= 0, end_s > 0)")
    if start_s is not None and end_s is not None and end_s <= start_s:
        raise ValueError(f"end_s ({end_s}) must be after start_s ({start_s})")
    if start_s is not None and dur > 0 and start_s >= dur:
        raise ValueError(f"start_s ({start_s}) is past the end of the video ({dur:.1f} s)")
    rotate = check_rotate(rotate)
    if times:
        return _extract_at(v, info, sorted(set(round(float(t), 3) for t in times)), out_dir, source, rotate)
    ss = samples(v, dur, rate, start_s, end_s, rotate)
    if start_s is not None or end_s is not None:  # seeking is to the keyframe before: keep the asked range only
        lo, hi = start_s or 0.0, end_s if end_s is not None else float("inf")
        ss = [x for x in ss if lo - 1e-3 <= x.t <= hi + 1e-3]
    if not ss:
        raise ValueError(f"no frames decoded from {v}" + (f" between {start_s} and {end_s} s"
                                                           if start_s is not None or end_s is not None else ""))
    picks = choose(ss, max_frames, min_frames)
    src = {**video_source(v), **{k: x for k, x in (source or {}).items() if k in SIDECAR_FIELDS and x}}
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    top = max(s.sharp_n for s in ss) or 1.0
    detect = people_detector()
    frames = []
    for p in picks:
        s = ss[p["sample"]]
        fid, f = _write_frame(v, s.t, out, src, rotate)
        fr = {"id": fid, "path": str(f), "time_s": round(s.t, 3), "sharpness": round(s.sharpness, 1),
              "room": round(s.room, 2), "notes": _notes(s), "why": _why(p, s, s.sharp_n / top)}
        if detect is not None and (n := detect(f)):
            fr["people"] = n
        frames.append(fr)
    sheet = out / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', v.stem)}_frames.jpg"
    contact_sheet([Path(f["path"]) for f in frames], [f"{f['id']}  {f['time_s']:.1f} s" for f in frames], sheet)
    people = [f["id"] for f in frames if f.get("people")]
    return {"video": str(v), "duration_s": round(dur, 2), "size": list(frame_size(info, rotate)),
            "fps": round(info["fps"], 3), "samples": len(ss), "source": src,
            "range_s": [round(ss[0].t, 2), round(ss[-1].t, 2)],
            "dropped_blurry_views": picks[0]["dropped_blurry_views"] if picks else 0,
            "dropped_not_room_views": picks[0]["dropped_not_room_views"] if picks else 0,
            "people_check": ("none" if detect is None else "opencv-hog"), "people_frames": people,
            "frames": frames, "sheet": str(sheet), **_orientation(info, rotate, frames)}


def _write_frame(v: Path, t: float, out: Path, src: dict[str, Any], rotate: int = 0) -> tuple[str, Path]:
    """The frame at t s as <out>/<id>.jpg (long side LONG_SIDE) with its sidecar (the video's source + time)."""
    fid = frame_id(v.stem, t)
    f = out / f"{fid}.jpg"
    scale = (f"scale='if(gte(iw,ih),min({LONG_SIDE},iw),-2)':'if(gte(iw,ih),-2,min({LONG_SIDE},ih))'")
    r = subprocess.run([tool("ffmpeg"), "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", str(v), "-frames:v", "1",
                        "-an", "-vf", rotate_vf(rotate) + scale, "-q:v", "2", str(f)], capture_output=True, text=True)
    if r.returncode != 0 or not f.exists():
        raise ValueError(f"ffmpeg could not write the frame at {t:.2f} s: {r.stderr.strip()[:300]}")
    side = {**src, "video": str(v), "time_s": round(t, 3), **({"rotate": rotate} if rotate else {})}
    sidecar_path(f).write_text(json.dumps(side, ensure_ascii=False, indent=1), encoding="utf-8")
    return fid, f


def _extract_at(v: Path, info: dict[str, Any], times: list[float], out_dir: str | Path,
                source: dict[str, Any] | None, rotate: int = 0) -> dict[str, Any]:
    dur = info["duration_s"]
    bad = [t for t in times if t < 0 or dur > 0 and t >= dur]
    if bad:
        raise ValueError(f"times {bad} are outside the video (0-{dur:.1f} s)")
    src = {**video_source(v), **{k: x for k, x in (source or {}).items() if k in SIDECAR_FIELDS and x}}
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    detect = people_detector()
    frames = []
    for t in times:
        fid, f = _write_frame(v, t, out, src, rotate)
        fr = {"id": fid, "path": str(f), "time_s": t, "why": "asked for this time"}
        if detect is not None and (n := detect(f)):
            fr["people"] = n
        frames.append(fr)
    sheet = out / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', v.stem)}_frames.jpg"
    contact_sheet([Path(f["path"]) for f in frames], [f"{f['id']}  {f['time_s']:.1f} s" for f in frames], sheet)
    return {"video": str(v), "duration_s": round(dur, 2), "size": list(frame_size(info, rotate)),
            "fps": round(info["fps"], 3), "samples": 0, "source": src, "range_s": [times[0], times[-1]],
            "dropped_blurry_views": 0, "dropped_not_room_views": 0,
            "people_check": ("none" if detect is None else "opencv-hog"),
            "people_frames": [f["id"] for f in frames if f.get("people")], "frames": frames, "sheet": str(sheet),
            **_orientation(info, rotate, frames)}


def _orientation(info: dict[str, Any], rotate: int, frames: list[dict[str, Any]]) -> dict[str, Any]:
    """How the frames were turned. Whether they are upright is not measured: counting the edges along the image's
    vertical and horizontal axes (vertical lines of the room stay vertical) got 2 of 6 upright videos wrong (blinds,
    a radiator, patterned bedding); the agent looks at the contact sheet instead."""
    return {"rotate": rotate, "rotation_metadata": info.get("rotation", 0)}


def contact_sheet(paths: list[Path], labels: list[str], out: Path, cell_w: int = 480, cols: int = 3) -> Path:
    """All frames in a grid with their id and time (one image for the agent to look at)."""
    from PIL import ImageDraw

    ims = [Image.open(p).convert("RGB") for p in paths]
    cell_h = max((round(cell_w * im.height / im.width) for im in ims), default=cell_w * 9 // 16)
    cols = max(1, min(cols, len(ims)))
    rows = max(1, -(-len(ims) // cols))
    pad, bar = 6, 22
    sheet = Image.new("RGB", (cols * (cell_w + pad) + pad, rows * (cell_h + bar + pad) + pad), (32, 32, 32))
    d = ImageDraw.Draw(sheet)
    for k, (im, lab) in enumerate(zip(ims, labels)):
        x, y = pad + (k % cols) * (cell_w + pad), pad + (k // cols) * (cell_h + bar + pad)
        im.thumbnail((cell_w, cell_h))
        sheet.paste(im, (x, y + bar))
        d.text((x + 4, y + 5), lab, fill=(240, 240, 240))
    sheet.save(out, quality=85)
    return out
