"""How the camera turned and moved between the chosen frames of a video: feature tracking with OpenCV.

fit_photos places each frame's camera from landmarks picked by hand. On a real video that is weak: a frame sees
few landmarks, some far away, and the cameras end up seconds of walking apart with nothing tying them together.
The video between the frames does tie them: this module decodes it at about `rate` fps (`width` px wide), and for
every pair of neighbouring samples matches ORB features and estimates the relative pose:
  * the camera only turned (the rotation alone maps the matches within ~1 px): rotation from the matched rays
    (Kabsch), no direction of the move,
  * otherwise the essential matrix (RANSAC) gives the rotation and the direction of the move (no length).
The rotations are chained from one chosen frame to the next. Chosen frames that still see the same things are also
matched directly (no drift, and that gives the direction between them). A direct
pair whose every triangle of direct pairs fails to turn back within CYCLE_DEG is dropped. A step whose matches mostly disagree
(a dissolve between two shots, motion blur) is skipped by matching across it; a cut, a dissolve or a blur that keeps
losing the features breaks the chain, and the pair is then left out.

The result is `relative` for fit_photos' request (photo_fit.RelativePose), measured for a field of view
`hfov_deg` (the video's camera; fit_photos estimates it, run again with that value if it differs).

Needs OpenCV (pip install opencv-python-headless, or the `video` extra) and numpy; ffmpeg decodes.
"""

from __future__ import annotations

import math
import subprocess
from pathlib import Path
from typing import Any

from agentic_sim_world_gen import video as vid

RATE = 10.0  # samples per second between the chosen frames
WIDTH = 640  # decoded width [px]
FEATURES = 3000
RATIO = 0.8  # Lowe's ratio test
MIN_MATCHES = 25  # fewer matches: the step is lost (cut, blur, a blank wall)
RANSAC_PX = 1.0
ROTATION_PX = 1.0  # median error of the rotation-only model below this: the camera only turned
WEAK_RATIO = 0.2  # a step with fewer inliers than this share of its matches is not trusted (a dissolve, motion blur)
MAX_WEAK = 3  # this many weak samples in a row (or a weak chosen frame) breaks the chain
DIRECT_MIN = 60  # chosen frames matched directly need this many inliers
DIRECTION_PX = 2.0  # median parallax (after taking out the turn) needed to trust the direction of the move
CYCLE_DEG = 3.0  # direct matches of three frames must turn back to the start within this, else the pair is dropped


class CvMissing(RuntimeError):
    pass


def available() -> bool:
    try:
        import cv2  # noqa: F401
        import numpy  # noqa: F401
    except ImportError:
        return False
    return True


def _cv():
    try:
        import cv2
        import numpy as np
    except ImportError as e:
        raise CvMissing("OpenCV is not installed: pip install opencv-python-headless (or uv sync --extra video)") from e
    return cv2, np


def intrinsics(w: int, h: int, hfov_deg: float):
    _, np = _cv()
    f = w / 2 / math.tan(math.radians(hfov_deg) / 2)
    return np.array([[f, 0, w / 2], [0, f, h / 2], [0, 0, 1]], dtype=np.float64)


def features(gray, n: int = FEATURES):
    cv2, np = _cv()
    orb = cv2.ORB_create(nfeatures=n)
    kp, desc = orb.detectAndCompute(gray, None)
    pts = np.array([k.pt for k in kp], dtype=np.float64).reshape(-1, 2)
    return pts, desc


def match(f1, f2):
    """Matched pixel arrays (N x 2 each) of two feature sets (ratio test, then one match per point of f2)."""
    cv2, np = _cv()
    (p1, d1), (p2, d2) = f1, f2
    if d1 is None or d2 is None or len(p1) < 2 or len(p2) < 2:
        return np.zeros((0, 2)), np.zeros((0, 2))
    bf = cv2.BFMatcher(cv2.NORM_HAMMING)
    best: dict[int, tuple[float, int]] = {}
    for m in bf.knnMatch(d1, d2, k=2):
        if len(m) == 2 and m[0].distance < RATIO * m[1].distance:
            q, t = m[0].queryIdx, m[0].trainIdx
            if t not in best or m[0].distance < best[t][0]:
                best[t] = (m[0].distance, q)
    if not best:
        return np.zeros((0, 2)), np.zeros((0, 2))
    qi = [q for _, q in best.values()]
    ti = list(best)
    return p1[qi], p2[ti]


def _rays(K, pts):
    _, np = _cv()
    h = np.hstack([pts, np.ones((len(pts), 1))])
    r = (np.linalg.inv(K) @ h.T).T
    return r / np.linalg.norm(r, axis=1, keepdims=True)


def _kabsch(a, b):
    """Rotation R minimizing |b - R a| over unit rays (rows)."""
    _, np = _cv()
    u, _, vt = np.linalg.svd(b.T @ a)
    d = np.sign(np.linalg.det(u @ vt))
    return u @ np.diag([1.0, 1.0, d]) @ vt


def _rotation_px(K, R, p1, p2):
    """Pixel error of mapping p1 to p2 by the turn R alone (per match)."""
    _, np = _cv()
    q = (K @ R @ np.linalg.inv(K) @ np.hstack([p1, np.ones((len(p1), 1))]).T).T
    q = q[:, :2] / q[:, 2:3]
    return np.linalg.norm(q - p2, axis=1)


def pose_from_matches(p1, p2, K) -> dict[str, Any] | None:
    """Relative pose of two views from matched pixels: {R (3x3, x2 = R x1 + t), t (unit, or None when the camera
    only turned / barely moved), inliers, model rotation|essential, parallax_px}, or None if lost."""
    cv2, np = _cv()
    if len(p1) < MIN_MATCHES:
        return None
    # rotation-only model, robustly: homography inliers, then the best rotation of their rays
    H, hmask = cv2.findHomography(p1, p2, cv2.RANSAC, 2.0)
    rot = None
    if H is not None and hmask is not None and int(hmask.sum()) >= MIN_MATCHES:
        m = hmask.ravel() > 0
        Rr = _kabsch(_rays(K, p1[m]), _rays(K, p2[m]))
        err = _rotation_px(K, Rr, p1, p2)
        inl = err < 2.0
        if inl.sum() >= MIN_MATCHES:
            Rr = _kabsch(_rays(K, p1[inl]), _rays(K, p2[inl]))
            err = _rotation_px(K, Rr, p1[inl], p2[inl])
            rot = (Rr, int(inl.sum()), float(np.median(err)))
    E, emask = cv2.findEssentialMat(p1, p2, K, method=cv2.RANSAC, prob=0.999, threshold=RANSAC_PX)
    ess = None
    if E is not None and E.shape == (3, 3) and emask is not None and int(emask.sum()) >= MIN_MATCHES:
        n, R, t, pmask = cv2.recoverPose(E, p1, p2, K, mask=emask.copy())
        if n >= MIN_MATCHES:
            m = pmask.ravel() > 0
            par = float(np.median(_rotation_px(K, R, p1[m], p2[m])))
            ess = (R, t.ravel() / (np.linalg.norm(t) or 1.0), int(n), par)
    if rot is not None and (ess is None or rot[2] < ROTATION_PX or ess[3] < DIRECTION_PX):
        # the turn explains the matches (or the move is too small to tell): the essential matrix is degenerate
        return {"R": rot[0], "t": None, "inliers": rot[1], "model": "rotation", "parallax_px": round(rot[2], 2)}
    if ess is None:
        return None
    return {"R": ess[0], "t": ess[1], "inliers": ess[2], "model": "essential", "parallax_px": round(ess[3], 2)}


def _decode(video: Path, a: float, b: float, rate: float, size: tuple[int, int], rotate: int = 0):
    """Gray samples [(t, image)] between a and b at about `rate` fps, `size` (w, h), turned `rotate` degrees
    clockwise like the chosen frames (video.rotate_vf)."""
    cv2, np = _cv()
    w, h = size
    vf = (f"select='isnan(prev_selected_t)+gte(t-prev_selected_t,{0.9 / rate:.4f})',{vid.rotate_vf(rotate)}"
          f"scale={w}:{h},showinfo")
    cmd = [vid.tool("ffmpeg"), "-hide_banner", "-nostats", "-ss", f"{max(0.0, a):.3f}", "-t", f"{max(0.1, b - a):.3f}",
           "-copyts", "-i", str(video), "-an", "-vf", vf, "-fps_mode", "passthrough", "-f", "image2pipe",
           "-vcodec", "ppm", "-"]
    import re
    import tempfile

    out = []
    with tempfile.TemporaryFile() as log:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=log)
        assert p.stdout is not None
        try:
            while im := vid._read_ppm(p.stdout):
                out.append(cv2.cvtColor(np.asarray(im), cv2.COLOR_RGB2GRAY))
        finally:
            p.stdout.close()
            p.wait()
        log.seek(0)
        err = log.read().decode(errors="replace")
    times = [float(m) for m in re.findall(r"Parsed_showinfo.*?pts_time:\s*([-0-9.e]+)", err)]
    return [(times[k] if k < len(times) else a + k / rate, g) for k, g in enumerate(out)]


def _frame(path: Path, size: tuple[int, int]):
    cv2, _ = _cv()
    im = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if im is None:
        raise ValueError(f"cannot read frame {path}")
    return cv2.resize(im, size, interpolation=cv2.INTER_AREA)


def _rlist(R) -> list[list[float]]:
    return [[round(float(v), 6) for v in row] for row in R]


def _angle(R) -> float:
    _, np = _cv()
    return math.degrees(math.acos(max(-1.0, min(1.0, (float(np.trace(R)) - 1) / 2))))


def _cycle_filter(ids: list[str], direct: dict[tuple[str, str], dict[str, Any]]):
    """Drop the direct pairs that are only in triangles that do not turn back within CYCLE_DEG.
    Returns (kept, dropped [{a, b, cycle_deg}])."""
    worst: dict[tuple[str, str], list[float]] = {}
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            for k in range(j + 1, len(ids)):
                ab, bc, ac = (ids[i], ids[j]), (ids[j], ids[k]), (ids[i], ids[k])
                if ab in direct and bc in direct and ac in direct:
                    c = _angle((direct[bc]["R"] @ direct[ab]["R"]).T @ direct[ac]["R"])
                    for p in (ab, bc, ac):
                        worst.setdefault(p, []).append(c)
    bad = {p: min(c) for p, c in worst.items() if min(c) > CYCLE_DEG}
    return ({p: d for p, d in direct.items() if p not in bad},
            [{"a": a, "b": b, "cycle_deg": round(c, 1)} for (a, b), c in bad.items()])


def relative_poses(video: str | Path, frames: list[dict[str, Any]], hfov_deg: float = 65.0, rate: float = RATE,
                   width: int = WIDTH) -> dict[str, Any]:
    """frames: the chosen frames [{id, time_s, path, rotate?}] of this video (rotate: as extracted, the same for all).
    Returns {ok, hfov_deg, size,
    matcher, relative [{a, b, rotation, direction?, weight, hfov_deg, source direct|chain, inliers, steps?,
    turn_deg}], breaks, checks [{a, b, chain_vs_direct_deg}], dropped [{a, b, cycle_deg}], warnings}."""
    cv2, np = _cv()
    v = Path(video).expanduser()
    if not v.exists():
        raise FileNotFoundError(f"video not found: {v}")
    keys = sorted(frames, key=lambda f: f["time_s"])
    if len(keys) < 2:
        raise ValueError("need at least two frames of the video")
    info = vid.probe(v)
    rotate = vid.frames_rotate(keys)
    W, H = vid.frame_size(info, rotate)
    w = int(width) // 2 * 2
    h = int(round(H * w / W)) // 2 * 2
    K = intrinsics(w, h, hfov_deg)
    t0, t1 = keys[0]["time_s"], keys[-1]["time_s"]
    seq = [(t, g, None) for t, g in _decode(v, t0, t1 + 0.5 / rate, rate, (w, h), rotate)
           if t0 < t < t1 and all(abs(t - k["time_s"]) > 0.5 / rate for k in keys)]
    kf = {}
    for k in keys:
        g = _frame(Path(k["path"]), (w, h))
        kf[k["id"]] = features(g, FEATURES * 2)
        seq.append((k["time_s"], g, k["id"]))
    seq.sort(key=lambda s: s[0])

    # chain the turns from one chosen frame to the next. A weak step (a dissolve between shots, motion blur) is
    # skipped: the next sample is matched to the last good one instead, and the chain breaks if that keeps failing.
    chains: dict[tuple[str, str], dict[str, Any]] = {}
    breaks = []
    prev_key, R_acc, steps, ok, min_inl, weak, skipped = None, np.eye(3), 0, True, 10 ** 9, 0, 0
    prev_f = None
    for t, g, kid in seq:
        f = kf[kid] if kid is not None else features(g)
        if prev_f is not None and ok:
            p1, p2 = match(prev_f, f)
            pose = pose_from_matches(p1, p2, K)
            if pose is None or pose["inliers"] < WEAK_RATIO * len(p1):
                weak += 1
                if kid is not None or weak > MAX_WEAK:
                    ok = False
                    breaks.append({"after": prev_key, "at_s": round(t, 2)})
            else:
                R_acc = pose["R"] @ R_acc
                steps += 1
                skipped += weak
                weak = 0
                min_inl = min(min_inl, pose["inliers"])
        if kid is not None:
            if prev_key is not None and ok:
                chains[(prev_key, kid)] = {"R": R_acc.copy(), "steps": steps, "inliers": min_inl, "skipped": skipped}
            prev_key, R_acc, steps, ok, min_inl, weak, skipped = kid, np.eye(3), 0, True, 10 ** 9, 0, 0
            prev_f = f
        elif weak == 0 or prev_f is None:
            prev_f = f

    # chosen frames that still overlap: match them directly
    direct: dict[tuple[str, str], dict[str, Any]] = {}
    ids = [k["id"] for k in keys]
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            pose = pose_from_matches(*match(kf[ids[i]], kf[ids[j]]), K)
            if pose is not None and pose["inliers"] >= DIRECT_MIN:
                direct[(ids[i], ids[j])] = pose
    direct, dropped = _cycle_filter(ids, direct)

    rel, checks, warnings = [], [], []
    for x in dropped:
        warnings.append(f"{x['a']}->{x['b']}: the direct match is left out (with the other frames it turns "
                        f"{x['cycle_deg']} deg off going round)")
    for (a, b), d in direct.items():
        e = {"a": a, "b": b, "rotation": _rlist(d["R"]), "weight": 1.0, "hfov_deg": hfov_deg, "source": "direct",
             "model": d["model"], "inliers": d["inliers"], "turn_deg": round(_angle(d["R"]), 1)}
        if d["t"] is not None:
            e["direction"] = [round(float(x), 4) for x in d["t"]]
        rel.append(e)
        if (a, b) in chains:
            c = chains[(a, b)]
            checks.append({"a": a, "b": b, "chain_vs_direct_deg": round(_angle(c["R"].T @ d["R"]), 1),
                           "steps": c["steps"], "skipped": c["skipped"]})
    for (a, b), c in chains.items():
        if (a, b) in direct:
            continue
        rel.append({"a": a, "b": b, "rotation": _rlist(c["R"]), "weight": round(1 / (1 + 0.02 * c["steps"]), 3),
                    "hfov_deg": hfov_deg, "source": "chain", "steps": c["steps"], "skipped": c["skipped"],
                    "inliers": c["inliers"],
                    "turn_deg": round(_angle(c["R"]), 1)})
    linked = {x for e in rel for x in (e["a"], e["b"])}
    lone = [i for i in ids if i not in linked]
    if lone:
        warnings.append(f"no relative pose for {', '.join(lone)} (the video loses track around it: a cut or blur)")
    bad = [c for c in checks if c["chain_vs_direct_deg"] > 5]
    for c in bad:
        warnings.append(f"{c['a']}->{c['b']}: the chained turn differs {c['chain_vs_direct_deg']} deg from the direct "
                        "match (drift or a wrong field of view)")
    rel.sort(key=lambda e: (ids.index(e["a"]), ids.index(e["b"])))
    return {"ok": True, "video": str(v), "hfov_deg": hfov_deg, "size": [w, h], "rate": rate,
            "matcher": {"name": "orb"}, "samples": len(seq), "relative": rel, "breaks": breaks,
            "checks": checks, "dropped": dropped, "warnings": warnings}
