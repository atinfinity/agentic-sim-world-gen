"""Room size, openings and photo cameras from landmarks picked in several photos of one room (no SfM).

The room is a box: origin at the south-west floor corner, x east (width), y north (depth), z up.
For each photo the caller gives pixel positions of landmarks it can see:

  points ("at"):  corner:<sw|se|nw|ne>:<floor|ceiling>
                  <opening id>:<start|end>:<top|bottom>   (start = west end on north/south walls,
                                                           south end on east/west walls)
                  <point name>                            (a shared point declared in `points`)
  lines ("along"): any pixel on the image of a 3D line
                  floor:<wall>, ceiling:<wall>            (where the wall meets the floor / ceiling)
                  corner:<sw|se|nw|ne>                    (the vertical edge between two walls)
                  <opening id>:<start|end|top|bottom>     (one edge of a window / door)

Unknowns: width, depth, the openings, the shared points, each camera (x, y, z, yaw, pitch, roll; the roll
held near 0 by a weak prior, a handheld phone tilts a few degrees) and one horizontal field of view. The wall height fixes the scale (photos alone cannot).
Solved with Levenberg-Marquardt in plain Python (a few dozen unknowns).

Frames of one video can also carry `relative` poses measured from the video between them (agentic_sim_world_gen.video_pose:
how far the camera turned, and in which direction it moved). They are soft constraints on the cameras' yaw / pitch
(and the direction from one camera to the other), and they seed the cameras' starting yaw / pitch, so a frame with
few or ambiguous landmarks still gets its view direction from the frames around it.
"""
from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, Field, PrivateAttr

WALLS = ("south", "north", "west", "east")
COMPASS = {"east": 0, "northeast": 45, "north": 90, "northwest": 135, "west": 180, "southwest": 225,
           "south": 270, "southeast": 315}
CORNERS = {"sw": (0, 0), "se": (1, 0), "nw": (0, 1), "ne": (1, 1)}  # (x is W?, y is D?)


class PointObs(BaseModel):
    at: str = Field(description="landmark: corner:sw:floor, win_n:start:top, or a shared point name")
    px: tuple[float, float] = Field(description="pixel (u, v), origin top-left")
    depth: float | None = Field(default=None, gt=0, description="distance in front of the camera [m] (along the "
                                "view axis) from a monocular depth model at this pixel (agentic_sim_world_gen.depth); only the "
                                "ratios within a photo are trusted, the photo's scale is fitted")


class LineObs(BaseModel):
    along: str = Field(description="3D line: floor:north, ceiling:east, corner:ne, win_n:top, door_s:start ...")
    px: tuple[float, float] = Field(description="any pixel on that line in the photo")
    depth: float | None = Field(default=None, gt=0, description="depth at this pixel [m], as for a point")


class SurfaceObs(BaseModel):
    """A pixel on a plain part of the floor, the ceiling or a wall (not furniture), with its depth from a
    monocular depth model. Without the depth such a pixel says nothing; with it, it says how far the camera is
    from that surface (up to the photo's depth scale, which is fitted)."""
    on: str = Field(description="floor, ceiling or wall:<south|north|west|east>")
    px: tuple[float, float]
    depth: float | None = Field(default=None, gt=0, description="distance in front of the camera [m] at this pixel "
                                "(fit_photos(depth=true) fills it from the photo's depth map)")


class PhotoObs(BaseModel):
    size: tuple[int, int] = Field(description="image width, height [px]")
    facing: str | float = Field(description="rough view direction: compass word (north, southwest ...) or degrees "
                                "(0 = east, 90 = north); only a starting guess")
    near: tuple[float, float] | None = Field(default=None, description="rough camera x, y [m] (default: room center)")
    hfov_deg: float | None = Field(default=None, description="known field of view of this photo (else shared, fitted)")
    image: str | None = Field(default=None, description="path of the photo: with depth=true, fit_photos fills the "
                              "depth of its points / lines / surfaces from <stem>.depth.npz (agentic_sim_world_gen.depth)")
    points: list[PointObs] = Field(default_factory=list)
    lines: list[LineObs] = Field(default_factory=list)
    surfaces: list[SurfaceObs] = Field(default_factory=list, description="pixels on the floor / ceiling / walls "
                                       "with depth (agentic_sim_world_gen.depth): set the room's size from one photo")


class OpeningGuess(BaseModel):
    kind: Literal["door", "window", "opening"] = "window"
    wall: Literal["south", "north", "west", "east"]
    top: float | None = Field(default=None, gt=0, description="typical height of its top [m]: a soft prior on "
                              "the scale when the top is seen (doors: FitRequest.door_top by default)")
    top_sigma: float = Field(default=0.1, gt=0, description="how far the top may be from `top` [m] (1 sigma)")


class SharedPoint(BaseModel):
    """A point seen in two or more photos (a cabinet's top corner ...). Fix what you know."""
    x: float | None = None
    y: float | None = None
    z: float | None = Field(default=None, description="height if known (0 = on the floor)")
    z_sigma: float | None = Field(default=None, gt=0, description="z is a typical height, not exact: held within "
                                  "this [m] (1 sigma), e.g. a desk top 0.73 ±0.03, a kitchen counter 0.9 ±0.03")
    wall: Literal["south", "north", "west", "east"] | None = Field(default=None, description="lies on this wall")


class Locate(BaseModel):
    """After the fit: where a pixel lies on the horizontal plane z (a furniture foot on the floor ...)."""
    name: str
    photo: str
    px: tuple[float, float]
    z: float = 0.0
    object: str | None = None  # the scene object this pixel belongs to (its id): the fit sums each object up
    edge: Literal["front", "back"] | None = Field(default=None, description="a foot / corner on the side the object "
                                                  "faces (front: a sofa's seat side, a shelf's open side, a desk's "
                                                  "knee side) or on its back: place_from_photos then knows its "
                                                  "front")


class RelativePose(BaseModel):
    """Relative pose of two photos measured from the video between them (agentic_sim_world_gen.video_pose). OpenCV camera
    axes (x right, y down, z forward): a point's coordinates in b = rotation @ (its coordinates in a) + t."""
    a: str
    b: str
    rotation: list[list[float]] = Field(description="3x3 rotation from a's camera axes to b's")
    direction: tuple[float, float, float] | None = Field(
        default=None, description="t normalized: from b's camera to a's, in b's camera axes (None: the camera only "
        "turned, or the move was too small to tell)")
    weight: float = Field(default=1.0, gt=0, description="1 for a direct match of the two frames, less for a chain")
    hfov_deg: float | None = Field(default=None, description="field of view the rotation was measured with")
    robust: bool = Field(default=True, description="true: a pair far off the landmarks is given up (Cauchy) and left "
                         "out of the second pass; false: kept in full (poses known to be more accurate than a "
                         "few picked landmarks)")
    group: str | None = Field(default=None, description="poses measured in one frame (one reconstruction). A photo "
                              "in two groups is kept in the one whose poses fit its landmarks best (the edge of a "
                              "model can be off)")


class FitRequest(BaseModel):
    photos: dict[str, PhotoObs]
    height: float = Field(default=2.4, gt=1.5, lt=10, description="wall height [m]; sets the scale (without depth), "
                          "else the start of the fitted height")
    fit_height: bool | None = Field(default=None, description="fit the wall height: None = when a depth sets the "
                                    "scale and something at the ceiling is seen; false = keep `height` fixed")
    door_top: float | None = Field(default=2.0, gt=1.5, lt=3, description="typical height of a door's top [m] "
                                   "(the frame's opening): a soft prior (±0.1 m) on the scale for each door whose "
                                   "top is seen; Japan 2.0, US 2.03 (80 in), None = no prior")
    eye_height: float = Field(default=1.4, gt=0.2, lt=5, description="camera height above the floor [m] when not "
                              "known; sets the scale instead of the wall height when no landmark at the ceiling "
                              "(or a shared point of known height) is seen")
    size_guess: tuple[float, float] | None = Field(default=None, description="rough width, depth [m] (start)")
    hfov_deg: float | None = Field(default=None, description="rough field of view (soft prior; default 65)")
    hfov_shared: bool = Field(default=True, description="one field of view for all photos without hfov_deg (same "
                              "camera); false: fit one per photo")
    openings: dict[str, OpeningGuess] = Field(default_factory=dict)
    points: dict[str, SharedPoint] = Field(default_factory=dict)
    locate: list[Locate] = Field(default_factory=list)
    relative: list[RelativePose] = Field(default_factory=list, description="relative poses of video frames "
                                         "(video_poses): constrain and seed the cameras' view directions")
    inside: list[str] = Field(default_factory=list, description="photos whose camera stood in the room: held at "
                              "least 0.1 m inside every wall (the fit may move the camera or the walls; when a "
                              "wall is placed wrong, fix its landmarks instead - see the result's `outside`)")
    # (photo, wall, distance to the opposite wall, along) held by the comparison of a camera beyond a wall:
    # the camera keeps its place in the room, so the wall it is beyond has to move out
    _hold: list = PrivateAttr(default_factory=list)


TURN_WEIGHT = 0.5  # a turn error of 1 deg costs as much as ~0.5 deg of image (~7 px at 75 deg / 1280 px): on a
#                   rendered walk the measured direct turns were 0.4-4 deg off
DIRECTION_WEIGHT = 0.25  # the direction of the move is noisier than the turn
DROP_DEG = 5.0  # a relative pose this far from the first fit is left out of the second (an outlier of the video)
ROBUST_DEG = 2.0  # Cauchy scale of the relative terms: a video pose far off the landmarks is given up, not followed
MAX_GROUP_CHOICES = 16  # photos shared by two models: choices fitted at most
DIRECTION_SOFT_M = 0.05  # keeps the direction smooth when two cameras meet (much shorter than a move between frames)
HEIGHT_LOOSE_M = 0.15  # a fitted height this uncertain is reported as a guess
HEIGHT_PX_PER_M = 2.0  # weak prior that holds a fitted wall height near `height` (a guess)
EYE_PX_PER_M = 50.0  # weight of the eye height when it sets the scale
ROLL_PX_PER_DEG = 1.0  # prior that holds the roll near 0: a 5 deg tilt costs as much as 5 px of picking; a handheld
#                        recording (measured on ARKitScenes, non-commercial data) tilts 4 deg (median), which
#                        without a roll left ~10 px of landmark error
DOOR_TOP_SIGMA_M = 0.1  # doors are 1.98-2.1 m high (Japan 2.0, US 80" = 2.03 m); ARKitScenes doors fitted 1.76-1.93 m
#                        where the room came out 5-16% small
DEPTH_PX_PER_LOG = 200.0  # a 5% depth error (log 0.05) costs 10 px: MoGe-2 ViT-S was within 2-6% (median abs) of
#                           the mesh inside a frame of a handheld recording (ARKitScenes)
DEPTH_SCALE_SIGMA = 0.15  # each photo's depth scale is off by up to 15-25% (MoGe-2 on ARKitScenes: -15 .. +24%)
DEPTH_SCALE_PX = 10.0 / DEPTH_SCALE_SIGMA  # prior on log(scale): 15% off costs as much as 10 px
EYE_SIGMA_M = 0.1  # when only the depth sets the scale, the mean camera height is held within ~0.1 m of
#                    eye_height: the depth's scale per photo (±15%) averages over the photos, but on a rendered room
#                    every frame came out ~13% far; the mean of a handheld walk was 1.34-1.39 m (ARKitScenes)
EYE_PHOTO_SIGMA_M = 0.25  # and each photo's within ~0.25 m (a handheld walk: 0.83-1.66 m, mostly 1.3-1.6 m); without
#                           it the photos' heights spread apart to keep their mean while the room stays big
INSIDE_M = 0.1  # a camera held inside (FitRequest.inside) stays this far from the walls
INSIDE_SIGMA_M = 0.05  # ... within this (1 sigma of the hinge)
HOLD_SIGMA_M = 0.02  # the comparison keeps the camera this close to its place (from the far wall)
MOVED_RMS_GAIN = 1.1  # the fit with the wall moved out is "about as good" within 10% of rms (and of depth rms)
OPPOSITE = {"south": "north", "north": "south", "west": "east", "east": "west"}
MIN_WIDTH_M = 0.05  # an opening's width is linear in its unknown above this, exponential toward 0 below
ONE_END_SIGMA = 0.3  # an opening with one edge marked: its width is held near the guess, ±30 %
NARROW_M = 0.4  # a door / window narrower than this: its start / end landmarks are likely swapped
OUTSIDE_M = 0.05  # a camera farther than this beyond a wall is outside the room
SIGHT_MARGIN_M = 0.1  # a sight line this close to an opening's edge counts as through it
SUSPECT_RMS_PX = 10.0  # above this rms the worst landmarks' objects are each fitted without ...
SUSPECT_TRIES = 3  # ... the first few objects of the worst landmarks
SUSPECT_GAIN = 0.75  # ... and one whose leaving out cuts the rms to this fraction is likely on something else
SUSPECT_NEAR = 1.5  # ... when no other cuts it to within this factor (or 2 px) of the best
PRIOR_PX = 10.0  # the priors above are written so that about 10 px is their 1-sigma (the depth scale's 15%)
# What sets the room's scale, side by side. Each source's own error (share, about 1 sigma):
CEILING_TYPICAL_M = (2.4, 2.7)  # wall heights of homes and offices: a fitted height outside says the scale is off
SCALE_ERR = {"depth": 0.15,  # one photo's depth scale (-15 .. +24 %); averages over the photos, never under ...
             "door top": 0.05,  # DOOR_TOP_SIGMA_M of 2.0 m
             "eye height": 0.15}  # a handheld walk's mean camera height: 1.06-1.6 m against 1.4
DEPTH_ERR_FLOOR = 0.06  # ... this: a depth model can be off the same way for every photo of a room
SCALE_OUTLIER = 0.1  # one source this far from the others' median, while the others agree: named


# ---------- small linear algebra ----------

def _solve(a: list[list[float]], b: list[float]) -> list[float] | None:
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(m[r][c]))
        if abs(m[p][c]) < 1e-15:
            return None
        m[c], m[p] = m[p], m[c]
        for r in range(c + 1, n):
            f = m[r][c] / m[c][c]
            if f:
                for k in range(c, n + 1):
                    m[r][k] -= f * m[c][k]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        x[r] = (m[r][n] - sum(m[r][k] * x[k] for k in range(r + 1, n))) / m[r][r]
    return x


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _axes(yaw_deg: float, pitch_deg: float, roll_deg: float = 0.0):
    """forward, right, up; pitch > 0 looks down; roll turns right / up about forward (right-handed about the
    view direction: roll > 0 tips the right side of the image down)."""
    y, p = math.radians(yaw_deg), math.radians(pitch_deg)
    fw = (math.cos(y) * math.cos(p), math.sin(y) * math.cos(p), -math.sin(p))
    rt = (math.sin(y), -math.cos(y), 0.0)
    if roll_deg:
        r = math.radians(roll_deg)
        k = _cross(fw, rt)  # = down without roll
        rt = tuple(math.cos(r) * rt[i] + math.sin(r) * k[i] for i in range(3))
    return fw, rt, _cross(rt, fw)


def _basis(yaw_deg: float, pitch_deg: float, roll_deg: float = 0.0):
    """Camera axes in world coordinates, OpenCV order (x right, y down, z forward): rows of the matrix whose
    columns are the camera axes, i.e. B^T. A world vector d has camera coordinates B^T d."""
    fw, rt, up = _axes(yaw_deg, pitch_deg, roll_deg)
    return (rt, (-up[0], -up[1], -up[2]), fw)


def _rel_rotation(bt_a, bt_b):
    """R = B_b^T B_a (a's camera axes -> b's)."""
    return [[_dot(bt_b[i], bt_a[j]) for j in range(3)] for i in range(3)]


def _rot_log(m) -> tuple[float, float, float]:
    """Axis-angle vector [rad] of a rotation matrix."""
    tr = m[0][0] + m[1][1] + m[2][2]
    c = max(-1.0, min(1.0, (tr - 1) / 2))
    th = math.acos(c)
    w = (m[2][1] - m[1][2], m[0][2] - m[2][0], m[1][0] - m[0][1])
    if th < 1e-6:
        return (w[0] / 2, w[1] / 2, w[2] / 2)
    if math.pi - th < 1e-4:  # 180 degrees: axis from the diagonal
        ax = [math.sqrt(max(0.0, (m[i][i] + 1) / 2)) for i in range(3)]
        return (ax[0] * th, ax[1] * th, ax[2] * th)
    k = th / (2 * math.sin(th))
    return (w[0] * k, w[1] * k, w[2] * k)


def _matmul_t(a, b):
    """a^T b for 3x3 lists."""
    return [[sum(a[k][i] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def _angle_deg(m) -> float:
    return math.degrees(math.sqrt(sum(v * v for v in _rot_log(m))))


def _yaw_pitch(fw) -> tuple[float, float]:
    n = math.sqrt(_dot(fw, fw)) or 1.0
    return math.degrees(math.atan2(fw[1], fw[0])), -math.degrees(math.asin(max(-1.0, min(1.0, fw[2] / n))))


def _yaw_pitch_roll(bt) -> tuple[float, float, float]:
    """Yaw, pitch, roll of camera axes given as rows (right, down, forward)."""
    yaw, pitch = _yaw_pitch(bt[2])
    _, rt0, _ = _axes(yaw, pitch)
    rt = bt[0]
    return yaw, pitch, math.degrees(math.atan2(_dot(_cross(rt0, rt), bt[2]), _dot(rt0, rt)))


def _robust(e: list[float], c: float) -> list[float]:
    """Scale a residual block so its squared norm is the Cauchy cost c^2 log(1 + |e|^2 / c^2)."""
    s2 = sum(v * v for v in e)
    if s2 < 1e-18:
        return e
    k = math.sqrt(c * c * math.log1p(s2 / (c * c)) / s2)
    return [v * k for v in e]


# ---------- the model ----------

def _depth_seen(req: FitRequest) -> bool:
    return any(o.depth for c in req.photos.values() for o in [*c.points, *c.lines, *c.surfaces])


def _sees(c: PhotoObs, level: str) -> bool:
    """The photo sees something at the floor or the ceiling (level): a corner, a line, or a pixel with a depth."""
    return (any(o.at.startswith("corner:") and o.at.endswith(":" + level) for o in c.points) or
            any(o.along.startswith(level + ":") for o in c.lines) or
            any(o.on == level and o.depth for o in c.surfaces))


def _walls_of(req: FitRequest, c: PhotoObs) -> set[str]:
    """The walls a photo has a landmark on: wall pixels with a depth, floor / ceiling lines along a wall, corners
    (both of their walls), openings and shared points on a wall."""
    side = {"s": "south", "n": "north", "w": "west", "e": "east"}
    out: set[str] = set()

    def name(s: str):
        parts = s.split(":")
        if parts[0] == "corner" and len(parts) > 1 and parts[1] in CORNERS:
            out.update(side[ch] for ch in parts[1])
        elif parts[0] in ("floor", "ceiling") and len(parts) == 2 and parts[1] in WALLS:
            out.add(parts[1])
        elif parts[0] in req.openings:
            out.add(req.openings[parts[0]].wall)
        elif s in req.points and req.points[s].wall:
            out.add(req.points[s].wall)

    for o in c.points:
        name(o.at)
    for o in c.lines:
        name(o.along)
    out.update(o.on.split(":", 1)[1] for o in c.surfaces if o.on.startswith("wall:") and o.depth)
    return out


def _ends_seen(req: FitRequest) -> dict[str, set[str]]:
    """{opening: the ends (start / end) some photo marks}: one end alone does not measure the width."""
    out: dict[str, set[str]] = {}
    for c in req.photos.values():
        for name in [o.at for o in c.points] + [o.along for o in c.lines]:
            parts = name.split(":")
            if parts[0] in req.openings and len(parts) > 1 and parts[1] in ("start", "end"):
                out.setdefault(parts[0], set()).add(parts[1])
    return out


def _swap_ends(req: FitRequest, oid: str) -> FitRequest:
    """The request with start / end of one opening swapped in every photo."""
    d = req.model_dump(mode="json")
    for c in d["photos"].values():
        for k, f in (("points", "at"), ("lines", "along")):
            for o in c[k]:
                parts = o[f].split(":")
                if parts[0] == oid and len(parts) > 1 and parts[1] in ("start", "end"):
                    parts[1] = "end" if parts[1] == "start" else "start"
                    o[f] = ":".join(parts)
    return FitRequest.model_validate(d)


def _landmark_object(req: FitRequest, name: str) -> str:
    """What a landmark is on: an opening (door), a corner (corner:sw), else the landmark itself (floor:north,
    a shared point). Mislabeling is per object: a cabinet door taken for the room's door gets all its marks."""
    parts = name.removeprefix("line ").split(":")
    if parts[0] in req.openings:
        return parts[0]
    if parts[0] == "corner" and len(parts) > 1:
        return ":".join(parts[:2])
    return ":".join(parts)


def _without_object(req: FitRequest, cid: str, obj: str) -> FitRequest:
    """The request without one photo's points and lines on one object."""
    d = req.model_dump(mode="json")
    c = d["photos"][cid]
    c["points"] = [o for o in c["points"] if _landmark_object(req, o["at"]) != obj]
    c["lines"] = [o for o in c["lines"] if _landmark_object(req, o["along"]) != obj]
    return FitRequest.model_validate(d)


def _warm_fit(req: FitRequest, names: list[str], x: list[float]):
    """One Levenberg-Marquardt from x (the full fit's answer) on the request with a few landmarks less (much
    faster than fit's starts): (unknowns by name, rms of the landmarks [px]) or None."""
    m = _Model(req)
    if m.names != names:
        return None
    x2, _, _ = _lm(m.residuals, x, m.scale)
    _, rows = m.residuals(x2, detail=True)
    errs = [e for _, _, e, k in rows if k not in ("relative", "depth")]
    return dict(zip(m.names, x2)), math.sqrt(sum(e * e for e in errs) / max(1, len(errs)))


def _size_priors(req: FitRequest) -> list[tuple[str, float, float]]:
    """Typical sizes that hold the scale softly: (unknown, value, sigma) for each door (or opening with a
    `top`) whose top is seen, and each shared point with a typical height (z_sigma) that is seen."""
    tops, pts = set(), set()
    for c in req.photos.values():
        for o in c.points:
            parts = o.at.split(":")
            if parts[0] in req.openings and len(parts) == 3 and parts[2] == "top":
                tops.add(parts[0])
            elif o.at in req.points:
                pts.add(o.at)
        for o in c.lines:
            parts = o.along.split(":")
            if parts[0] in req.openings and len(parts) == 2 and parts[1] == "top":
                tops.add(parts[0])
    out = []
    for oid, o in req.openings.items():
        top = o.top if o.top is not None else req.door_top if o.kind == "door" else None
        if oid in tops and top is not None:
            out.append((f"{oid}.top", top, o.top_sigma))
    for pid, sp in req.points.items():
        if pid in pts and sp.z is not None and sp.z_sigma:
            out.append((f"{pid}.z", sp.z, sp.z_sigma))
    return out


def _ceiling_seen(req: FitRequest) -> bool:
    """Something at the ceiling height is seen: a ceiling corner or line, or a ceiling pixel with a depth."""
    return any(_sees(c, "ceiling") for c in req.photos.values())


def _floor_and_ceiling_tied(req: FitRequest, unused: set[int]) -> list[str] | None:
    """Photos joined by the relative poses kept (or one photo) that see both the floor and the ceiling: their ids,
    or None. Their cameras' heights are then tied to both, and the height is fixed as far as the scale is."""
    up = {cid: cid for cid in req.photos}

    def root(a):
        while up[a] != a:
            up[a] = up[up[a]]
            a = up[a]
        return a

    for i, rp in enumerate(req.relative):
        if i not in unused and rp.a in up and rp.b in up:
            up[root(rp.a)] = root(rp.b)
    parts: dict[str, list[str]] = {}
    for cid in req.photos:
        parts.setdefault(root(cid), []).append(cid)
    for ids in parts.values():
        if any(_sees(req.photos[c], "floor") for c in ids) and any(_sees(req.photos[c], "ceiling") for c in ids):
            return ids
    return None


def _width(p, oid) -> float:
    """An opening's width: the unknown itself above MIN_WIDTH_M, a smooth exponential toward 0 below it.
    A log width kept the fit from growing a door back once a step had shrunk it (47204394: rms 17.6 -> 46 px)."""
    v = p[f"{oid}.width"]
    return v if v >= MIN_WIDTH_M else MIN_WIDTH_M * math.exp(max((v - MIN_WIDTH_M) / MIN_WIDTH_M, -50.0))


class _Model:
    def __init__(self, req: FitRequest):
        self.req = req
        self.names: list[str] = []
        self.init: list[float] = []
        self.scale: list[float] = []
        w, d = req.size_guess or (4.0, 4.0)
        self._add("width", w, 1)
        self._add("depth", d, 1)
        self.fit_H = _ceiling_seen(req) and _depth_seen(req) if req.fit_height is None else req.fit_height
        if self.fit_H:
            self._add("height", req.height, 0.3)
        self.free_hfov = [cid for cid, c in req.photos.items() if c.hfov_deg is None]
        for k in (["hfov"] if req.hfov_shared and self.free_hfov else
                  [f"{cid}.hfov" for cid in self.free_hfov] if not req.hfov_shared else []):
            self._add(k, req.hfov_deg or 65.0, 10)
        for oid, o in req.openings.items():
            length = w if o.wall in ("south", "north") else d
            self._add(f"{oid}.offset", length / 2, 1)
            self._add(f"{oid}.width", 0.9 if o.kind == "door" else 1.2, 1)  # see _width: never below 0
            if o.kind == "window":
                self._add(f"{oid}.sill", 0.9, 1)
            self._add(f"{oid}.top", 2.0 if o.kind == "door" else 2.0, 1)
        for pid, p in req.points.items():
            for ax, g in (("x", w / 2), ("y", d / 2), ("z", p.z if p.z is not None else 1.0)):
                if (getattr(p, ax) is None or (ax == "z" and p.z_sigma)) and not (p.wall and ax == ("y" if p.wall in ("south", "north") else "x")):
                    self._add(f"{pid}.{ax}", g, 1)
        for cid, c in req.photos.items():
            x, y = c.near or (w / 2, d / 2)
            f = c.facing if isinstance(c.facing, (int, float)) else COMPASS.get(str(c.facing).lower().replace("-", ""))
            if f is None:
                raise ValueError(f"photo {cid}: facing '{c.facing}' is not a compass word or degrees")
            for k, v, s in (("x", x, 1), ("y", y, 1), ("z", req.eye_height, 1), ("yaw", float(f), 30),
                            ("pitch", 8.0, 10), ("roll", 0.0, 3)):
                self._add(f"{cid}.{k}", v, s)
            if any(o.depth for o in [*c.points, *c.lines, *c.surfaces]):
                self._add(f"{cid}.dscale", 0.0, 0.1)  # log of (model depth / true depth) for this photo
        self.index = {n: i for i, n in enumerate(self.names)}
        self.inside = [cid for cid in req.inside if cid in req.photos]
        ends = _ends_seen(req)
        self.one_end = [oid for oid, e in ends.items() if len(e) == 1]  # width held near the guess
        self.size_priors = _size_priors(req)  # (unknown, typical value, sigma): door tops, typical heights
        self.used: set[str] = set()
        self.scale_seen = self._scale_seen()
        # nothing of known length is seen: the depth's scale (±15%) is all there is, so the eye height joins in
        self.depth_only = _depth_seen(req) and not self._length_seen()
        # the mean camera height is a guess (ARKitScenes walks: 1.06-1.6 m): a door's top or a typical height
        # seen holds the scale better, so it replaces that row
        self.eye_mean = self.depth_only and not self.size_priors

    def _length_seen(self) -> bool:
        """Whether a known length above the floor is seen: the ceiling with the height given, or a shared point
        with a fixed z > 0."""
        if not self.fit_H and _ceiling_seen(self.req):
            return True
        return any((sp := self.req.points.get(o.at)) is not None and (sp.z or 0) > 0 and not sp.z_sigma
                   for c in self.req.photos.values() for o in c.points)

    def _scale_seen(self) -> bool:
        """Whether a landmark of known height above the floor is seen: a ceiling corner or line, or a shared
        point with a fixed z > 0, or a depth (its scale per photo is loose, but not free). Without one, the photos
        fit a room of any size."""
        if _depth_seen(self.req):
            return True
        for c in self.req.photos.values():
            if any(o.at.startswith("corner:") and o.at.endswith(":ceiling") for o in c.points):
                return True
            if any(o.along.startswith("ceiling:") for o in c.lines):
                return True
            if any((sp := self.req.points.get(o.at)) is not None and (sp.z or 0) > 0 for o in c.points):
                return True
        return False

    def h(self, p):
        """Wall height: fitted or given."""
        return p["height"] if self.fit_H else self.req.height

    def _add(self, name, v, s):
        self.names.append(name)
        self.init.append(v)
        self.scale.append(s)

    # 3D landmarks as functions of the parameters
    def _wall_pt(self, p, wall, a, z):
        W, D = p["width"], p["depth"]
        return {"south": (a, 0.0, z), "north": (a, D, z), "west": (0.0, a, z), "east": (W, a, z)}[wall]

    def _corner(self, p, c, z):
        if c not in CORNERS:
            raise ValueError(f"corner '{c}': use sw, se, nw, ne")
        cx, cy = CORNERS[c]
        self.used.update(("width",) if cx else ())
        self.used.update(("depth",) if cy else ())
        return (p["width"] * cx, p["depth"] * cy, z)

    def _opening(self, p, oid):
        o = self.req.openings[oid]
        c, w = p[f"{oid}.offset"], _width(p, oid)
        sill = p[f"{oid}.sill"] if o.kind == "window" else 0.0
        return o, c, w, sill, p[f"{oid}.top"]

    def point(self, p, at: str):
        parts = at.split(":")
        if parts[0] == "corner" and len(parts) == 3:
            return self._corner(p, parts[1], 0.0 if parts[2] == "floor" else self.h(p))
        if parts[0] in self.req.openings and len(parts) == 3:
            o, c, w, sill, top = self._opening(p, parts[0])
            a = c - w / 2 if parts[1] == "start" else c + w / 2
            z = top if parts[2] == "top" else sill
            self.used.update((f"{parts[0]}.offset", f"{parts[0]}.width",
                              f"{parts[0]}.top" if parts[2] == "top" else f"{parts[0]}.sill"))
            return self._wall_pt(p, o.wall, a, z)
        if at in self.req.points:
            sp = self.req.points[at]
            v = []
            for ax in ("x", "y", "z"):
                fixed = getattr(sp, ax)
                if fixed is not None and not (ax == "z" and sp.z_sigma):
                    v.append(fixed)
                elif sp.wall and ax == ("y" if sp.wall in ("south", "north") else "x"):
                    v.append({"south": 0.0, "north": p["depth"], "west": 0.0, "east": p["width"]}[sp.wall])
                else:
                    v.append(p[f"{at}.{ax}"])
                    self.used.add(f"{at}.{ax}")
            return tuple(v)
        raise ValueError(f"unknown landmark '{at}' (corner:sw:floor, <opening>:start:top, or a name in points)")

    def line(self, p, along: str):
        parts = along.split(":")
        W, D, H = p["width"], p["depth"], self.h(p)
        if parts[0] in ("floor", "ceiling") and len(parts) == 2 and parts[1] in WALLS:
            z = 0.0 if parts[0] == "floor" else H
            self.used.update(("depth",) if parts[1] == "north" else ("width",) if parts[1] == "east" else ())
            return self._wall_pt(p, parts[1], 0.0, z), self._wall_pt(p, parts[1], 1.0, z)
        if parts[0] == "corner" and len(parts) == 2:
            a = self._corner(p, parts[1], 0.0)
            return a, (a[0], a[1], H)
        if parts[0] in self.req.openings and len(parts) == 2:
            o, c, w, sill, top = self._opening(p, parts[0])
            e = parts[1]
            if e in ("start", "end"):
                a = c - w / 2 if e == "start" else c + w / 2
                self.used.update((f"{parts[0]}.offset", f"{parts[0]}.width"))
                return self._wall_pt(p, o.wall, a, 0.0), self._wall_pt(p, o.wall, a, 1.0)
            z = top if e == "top" else sill
            self.used.add(f"{parts[0]}.top" if e == "top" else f"{parts[0]}.sill")
            return self._wall_pt(p, o.wall, c - 1, z), self._wall_pt(p, o.wall, c + 1, z)
        raise ValueError(f"unknown line '{along}' (floor:north, ceiling:east, corner:ne, <opening>:top ...)")

    def hfov(self, p, cid):
        c = self.req.photos[cid]
        if c.hfov_deg is not None:
            return c.hfov_deg
        return p["hfov"] if self.req.hfov_shared else p[f"{cid}.hfov"]

    def project(self, p, cid, X):
        c = self.req.photos[cid]
        pos = (p[f"{cid}.x"], p[f"{cid}.y"], p[f"{cid}.z"])
        fw, rt, up = _cam_axes(p, cid)
        f = c.size[0] / 2 / math.tan(math.radians(self.hfov(p, cid)) / 2)
        d = _sub(X, pos)
        depth = _dot(d, fw)
        if depth < 0.05:
            return None
        return (c.size[0] / 2 + f * _dot(d, rt) / depth, c.size[1] / 2 - f * _dot(d, up) / depth)

    def line_dist(self, p, cid, A, B, px):
        """Signed pixel distance from px to the image of the infinite 3D line AB (the plane through the
        camera and the line; works when part of the line is behind the camera)."""
        c = self.req.photos[cid]
        pos = (p[f"{cid}.x"], p[f"{cid}.y"], p[f"{cid}.z"])
        fw, rt, up = _cam_axes(p, cid)
        f = c.size[0] / 2 / math.tan(math.radians(self.hfov(p, cid)) / 2)
        n = _cross(_sub(A, pos), _sub(B, pos))
        nr, nu, nf = _dot(n, rt), _dot(n, up), _dot(n, fw)
        # ray of pixel (u, v): rt*(u-cx)/f - up*(v-cy)/f + fw  ->  nr*(u-cx) - nu*(v-cy) + nf*f = 0
        g = math.hypot(nr, nu) or 1e-12
        return (nr * (px[0] - c.size[0] / 2) - nu * (px[1] - c.size[1] / 2) + nf * f) / g

    def depth_of(self, p, cid, X):
        pos = (p[f"{cid}.x"], p[f"{cid}.y"], p[f"{cid}.z"])
        return _dot(_sub(X, pos), _cam_axes(p, cid)[0])

    def line_depth(self, p, cid, A, B, px):
        """Depth of the point of line AB nearest to the ray of pixel px."""
        c = self.req.photos[cid]
        pos = (p[f"{cid}.x"], p[f"{cid}.y"], p[f"{cid}.z"])
        fw, rt, up = _cam_axes(p, cid)
        f = self.focal(p, cid)
        a, b = (px[0] - c.size[0] / 2) / f, -(px[1] - c.size[1] / 2) / f
        r = tuple(fw[i] + a * rt[i] + b * up[i] for i in range(3))
        u = _sub(B, A)
        w0 = _sub(A, pos)
        # pos + s r nearest to A + t u:  s rr - t ru = r.w0,  s ru - t uu = u.w0
        rr, ru, uu, rw, uw = _dot(r, r), _dot(r, u), _dot(u, u), _dot(r, w0), _dot(u, w0)
        den = rr * uu - ru * ru
        if abs(den) < 1e-12:
            return None
        t = (ru * rw - rr * uw) / den
        return self.depth_of(p, cid, tuple(A[i] + t * u[i] for i in range(3)))

    def surface_depth(self, p, cid, on, px):
        """Depth at which the ray of pixel px meets the plane `on` (None: parallel or behind)."""
        c = self.req.photos[cid]
        pos = (p[f"{cid}.x"], p[f"{cid}.y"], p[f"{cid}.z"])
        fw, rt, up = _cam_axes(p, cid)
        f = self.focal(p, cid)
        a, b = (px[0] - c.size[0] / 2) / f, -(px[1] - c.size[1] / 2) / f
        r = tuple(fw[i] + a * rt[i] + b * up[i] for i in range(3))  # forward component 1: t is the depth
        planes = {"floor": (2, 0.0), "ceiling": (2, self.h(p)), "wall:south": (1, 0.0), "wall:north": (1, p["depth"]),
                  "wall:west": (0, 0.0), "wall:east": (0, p["width"])}
        if on not in planes:
            raise ValueError(f"surface '{on}': use floor, ceiling or wall:<south|north|west|east>")
        self.used.update(("depth",) if on == "wall:north" else ("width",) if on == "wall:east" else ())
        ax, v = planes[on]
        if abs(r[ax]) < 1e-9:
            return None
        t = (v - pos[ax]) / r[ax]
        return t if t > 0.05 else None

    def depth_error(self, p, cid, z, depth):
        """log(model depth) = log(true depth) + dscale (the photo's depth scale)."""
        return (math.log(depth) - math.log(max(z, 0.05)) - p[f"{cid}.dscale"]) * DEPTH_PX_PER_LOG

    def focal(self, p, cid):
        c = self.req.photos[cid]
        return c.size[0] / 2 / math.tan(math.radians(self.hfov(p, cid)) / 2)

    def relative_error(self, p, rp: RelativePose) -> list[float]:
        """Turn error (axis-angle of R_meas^T R_pred, in pixels at a's focal length), if measured, the
        error of the move's direction (scaled down)."""
        bt_a, bt_b = _cam_basis(p, rp.a), _cam_basis(p, rp.b)
        f = self.focal(p, rp.a) * rp.weight * TURN_WEIGHT
        e = [v * f for v in _rot_log(_matmul_t(rp.rotation, _rel_rotation(bt_a, bt_b)))]
        if rp.direction is not None:
            d = _sub((p[f"{rp.a}.x"], p[f"{rp.a}.y"], p[f"{rp.a}.z"]), (p[f"{rp.b}.x"], p[f"{rp.b}.y"], p[f"{rp.b}.z"]))
            n = math.sqrt(_dot(d, d))
            m = math.sqrt(sum(v * v for v in rp.direction)) or 1.0
            # unit move - measured direction: does not change with the scale of the room (a residual that grows
            # with the move pulled the room and the cameras smaller when nothing at the ceiling fixed the scale)
            g = math.sqrt(n * n + DIRECTION_SOFT_M ** 2)
            e += [(_dot(bt_b[i], d) / g - rp.direction[i] / m) * f * DIRECTION_WEIGHT for i in range(3)]
        return _robust(e, f * math.radians(ROBUST_DEG)) if rp.robust else e

    def residuals(self, v, detail=False):
        p = dict(zip(self.names, v))
        r, rows = [], []
        for cid, c in self.req.photos.items():
            for o in c.points:
                X = self.point(p, o.at)
                q = self.project(p, cid, X)
                e = (100.0, 100.0) if q is None else (q[0] - o.px[0], q[1] - o.px[1])
                r += e
                rows.append((cid, o.at, math.hypot(*e), q is None))
                if o.depth:
                    e = self.depth_error(p, cid, self.depth_of(p, cid, X), o.depth)
                    r.append(e)
                    rows.append((cid, "depth " + o.at, abs(e), "depth"))
            for o in c.lines:
                A, B = self.line(p, o.along)
                e = self.line_dist(p, cid, A, B, o.px)
                r.append(e)
                rows.append((cid, "line " + o.along, abs(e), False))
                if o.depth:
                    z = self.line_depth(p, cid, A, B, o.px)
                    e = 100.0 if z is None else self.depth_error(p, cid, z, o.depth)
                    r.append(e)
                    rows.append((cid, "depth line " + o.along, abs(e), "depth"))
            for o in c.surfaces:
                if not o.depth:
                    continue  # no depth (not filled): says nothing
                z = self.surface_depth(p, cid, o.on, o.px)
                e = 100.0 if z is None else self.depth_error(p, cid, z, o.depth)
                r.append(e)
                rows.append((cid, "depth " + o.on, abs(e), "depth"))
        for rp in self.req.relative:
            e = self.relative_error(p, rp)
            r += e
            if detail:
                rows.append((rp.a, f"relative {rp.a}->{rp.b}", math.sqrt(sum(x * x for x in e)), "relative"))
        for k in self.names:  # soft prior on the field of view: keeps a weak view set from running away
            if k == "hfov" or k.endswith(".hfov"):
                r.append((p[k] - (self.req.hfov_deg or 65.0)) * (0.5 if self.req.hfov_deg else 0.1))
        # eye height: very weak when a known length is seen; without one it sets the scale (floor landmarks,
        # walls and openings look the same in a room scaled about the floor), alone or with the depth
        if not self.scale_seen:
            k = EYE_PX_PER_M / math.sqrt(len(self.req.photos))
        else:
            k = PRIOR_PX / EYE_PHOTO_SIGMA_M if self.depth_only else 0.05
        if self.fit_H:
            r.append((p["height"] - self.req.height) * HEIGHT_PX_PER_M)
        for u, v, sg in self.size_priors:
            r.append((p[u] - v) / sg * PRIOR_PX)
        for oid in self.one_end:
            g = self.init[self.index[f"{oid}.width"]]
            r.append(math.log(_width(p, oid) / g) / ONE_END_SIGMA * PRIOR_PX)
        for cid in self.req.photos:
            r.append((p[f"{cid}.z"] - self.req.eye_height) * k)
            r.append(p[f"{cid}.roll"] * ROLL_PX_PER_DEG)
            if f"{cid}.dscale" in self.index:
                r.append(p[f"{cid}.dscale"] * DEPTH_SCALE_PX)
        if self.eye_mean:  # one row on the mean camera height (the photos' heights share the scale's error)
            zs = [p[f"{cid}.z"] for cid in self.req.photos]
            r.append((sum(zs) / len(zs) - self.req.eye_height) / EYE_SIGMA_M * PRIOR_PX)
        for cid in self.inside:  # a hinge per wall: nothing while the camera is inside, pushes it back in else
            x, y = p[f"{cid}.x"], p[f"{cid}.y"]
            for d in (x, y, p["width"] - x, p["depth"] - y):
                r.append(max(0.0, INSIDE_M - d) / INSIDE_SIGMA_M * PRIOR_PX)
        for cid, wall, far, along in self.req._hold:
            pos = (p[f"{cid}.x"], p[f"{cid}.y"])
            r.append((_inward(OPPOSITE[wall], pos, p["width"], p["depth"]) - far) / HOLD_SIGMA_M * PRIOR_PX)
            r.append((pos[0 if wall in ("south", "north") else 1] - along) / HOLD_SIGMA_M * PRIOR_PX)
        return (r, rows) if detail else r

    def n_priors(self) -> int:
        """How many rows at the end of residuals() are priors (field of view, height, eye height, roll, depth
        scale), not measurements."""
        n = sum(k == "hfov" or k.endswith(".hfov") for k in self.names) + (1 if self.fit_H else 0)
        n += self.eye_mean + len(self.size_priors) + len(self.one_end) + 4 * len(self.inside) + 2 * len(self.req._hold)
        return n + sum(2 + (f"{cid}.dscale" in self.index) for cid in self.req.photos)


def _lm(f, x0, scale, iters=200):
    x = list(x0)
    r = f(x)
    cost = sum(e * e for e in r)
    lam = 1e-3
    n = len(x)
    for _ in range(iters):
        J = []
        for i in range(n):
            h = 1e-6 * max(scale[i], abs(x[i]))
            xi = x[:]
            xi[i] += h
            J.append([(a - b) / h for a, b in zip(f(xi), r)])  # column i
        JtJ = [[sum(a * b for a, b in zip(J[i], J[j])) for j in range(n)] for i in range(n)]
        Jtr = [sum(a * b for a, b in zip(J[i], r)) for i in range(n)]
        improved = False
        for _ in range(10):
            A = [[JtJ[i][j] + (lam * (JtJ[i][i] + 1e-9) if i == j else 0.0) for j in range(n)] for i in range(n)]
            dx = _solve(A, [-g for g in Jtr])
            if dx is None:
                lam *= 10
                continue
            xn = [a + b for a, b in zip(x, dx)]
            rn = f(xn)
            cn = sum(e * e for e in rn)
            if cn < cost:
                done = cost - cn < 1e-10 * (1 + cost)
                x, r, cost, lam, improved = xn, rn, cn, max(lam / 5, 1e-9), True
                break
            lam *= 8
        if not improved or done:
            break
    return x, cost, JtJ


def _normal(f, x, scale, n_prior, noise):
    """Normal matrix with the measurement rows over their noise (px^2) and the priors over PRIOR_PX^2: a prior
    holds a direction only as tightly as it says, however small the landmarks' residual."""
    r = f(x)
    cols = []
    for i in range(len(x)):
        h = 1e-6 * max(scale[i], abs(x[i]))
        xi = x[:]
        xi[i] += h
        cols.append([(a - b) / h for a, b in zip(f(xi), r)])
    nd = len(r) - n_prior
    w = [1.0 / max(noise, 1e-12)] * nd + [1.0 / PRIOR_PX ** 2] * n_prior
    return [[sum(a * b * c for a, b, c in zip(cols[i], cols[j], w)) for j in range(len(x))] for i in range(len(x))]


def _look_at(pos, fw, W, D, H):
    """Where the view axis hits the room box (walls, floor, ceiling)."""
    hits = []
    for k, hi in ((0, W), (1, D), (2, H)):
        if abs(fw[k]) > 1e-6:
            for plane in (0.0, hi):
                t = (plane - pos[k]) / fw[k]
                q = [pos[i] + t * fw[i] for i in range(3)]
                if t > 0.3 and -0.01 <= q[0] <= W + 0.01 and -0.01 <= q[1] <= D + 0.01 and -0.01 <= q[2] <= H + 0.01:
                    hits.append(t)
    t = min(hits) if hits else 3.0
    return [round(pos[i] + t * fw[i], 2) for i in range(3)]


def _beyond(pos, W, D):
    """(wall, metres) of the wall a camera is farthest beyond, or None when it is inside."""
    b = {"south": -pos[1], "north": pos[1] - D, "west": -pos[0], "east": pos[0] - W}
    w = max(b, key=b.get)
    return (w, b[w]) if b[w] > OUTSIDE_M else None


def _inward(wall, X, W, D):
    """How far X is inside `wall` [m] (negative beyond it)."""
    return {"south": X[1], "north": D - X[1], "west": X[0], "east": W - X[0]}[wall]


def _ray(m: "_Model", p: dict, cid: str, px):
    c = m.req.photos[cid]
    fw, rt, up = _cam_axes(p, cid)
    f = c.size[0] / 2 / math.tan(math.radians(m.hfov(p, cid)) / 2)
    a, b = (px[0] - c.size[0] / 2) / f, -(px[1] - c.size[1] / 2) / f
    return tuple(fw[i] + a * rt[i] + b * up[i] for i in range(3))


def _on_plane(pos, ray, axis, value):
    """The point where pos + t * ray (t > 0) meets the plane x[axis] = value, or None."""
    if abs(ray[axis]) < 1e-9 or (t := (value - pos[axis]) / ray[axis]) <= 0:
        return None
    return tuple(pos[i] + t * ray[i] for i in range(3))


def _sight(m: "_Model", p: dict, cid: str, wall: str):
    """Where the sight lines of a camera beyond `wall` cross that wall, for each of its pixels (points, lines,
    surfaces) that sees something in the room: ({opening id: n} through an opening, n through the wall itself,
    n floor / ceiling pixels beyond the wall). Pixels on that wall tell nothing."""
    W, D, H = p["width"], p["depth"], m.h(p)
    pos = (p[f"{cid}.x"], p[f"{cid}.y"], p[f"{cid}.z"])
    axis, along = (1, 0) if wall in ("south", "north") else (0, 1)
    plane = {"south": 0.0, "north": D, "west": 0.0, "east": W}[wall]
    holes = {oid: m._opening(p, oid) for oid, o in m.req.openings.items() if o.wall == wall}
    c = m.req.photos[cid]
    targets = []  # (pixel, a 3D point it sees)
    floor_out = 0  # floor / ceiling pixels that land beyond the wall: the room goes on there
    for o in c.points:
        targets.append((o.px, m.point(p, o.at)))
    for o in c.lines:
        A, B = m.line(p, o.along)
        ray = _ray(m, p, cid, o.px)
        u = _sub(B, A)
        # the line's point nearest to the ray
        w0 = _sub(A, pos)
        aa, bb, cc, dd, ee = _dot(u, u), _dot(u, ray), _dot(ray, ray), _dot(u, w0), _dot(ray, w0)
        den = aa * cc - bb * bb
        if den > 1e-12:
            s = (bb * ee - cc * dd) / den
            targets.append((o.px, tuple(A[i] + s * u[i] for i in range(3))))
    for o in c.surfaces:
        on = o.on.split(":")
        ax, val = (2, 0.0) if on[0] == "floor" else (2, H) if on[0] == "ceiling" else (
            (1, 0.0) if on[-1] == "south" else (1, D) if on[-1] == "north" else (0, 0.0) if on[-1] == "west" else (0, W))
        if (X := _on_plane(pos, _ray(m, p, cid, o.px), ax, val)) is not None:
            targets.append((o.px, X))
            if ax == 2 and _inward(wall, X, W, D) < -OUTSIDE_M:
                floor_out += 1
    through: dict[str, int] = {}
    solid = 0
    for px, X in targets:
        if _inward(wall, X, W, D) < OUTSIDE_M:
            continue
        hit = _on_plane(pos, _ray(m, p, cid, px), axis, plane)
        if hit is None:
            continue
        a, z = hit[along], hit[2]
        name = next((oid for oid, (_, oc, ow, sill, top) in holes.items()
                     if oc - ow / 2 - SIGHT_MARGIN_M <= a <= oc + ow / 2 + SIGHT_MARGIN_M
                     and sill - SIGHT_MARGIN_M <= z <= top + SIGHT_MARGIN_M), None)
        if name:
            through[name] = through.get(name, 0) + 1
        else:
            solid += 1
    return through, solid, floor_out


def _outside_text(o: dict) -> str:
    cid, wall, d = o["photo"], o["wall"], o["beyond_m"]
    head = f"photo {cid}: camera ends up {d:.2f} m beyond the {wall} wall {o['position']}"
    if o["verdict"] == "doorway":
        via = ", ".join(f"{k} ({n} pixels)" for k, n in o["through"].items())
        return (f"{head}, looking in through {via}: it stood in that opening or beyond it (a hallway) - fine if the "
                "frame was taken there; landmarks of the hallway itself must not be named for this room")
    if o["verdict"] == "wall":
        seen = " and ".join(t for t in (f"sees {o['solid']} of its pixels through the wall itself (not an opening)"
                                        if o["solid"] else "",
                                        f"{o['floor_beyond']} floor / ceiling pixels land beyond the wall"
                                        if o["floor_beyond"] else "") if t)
        txt = (f"{head} but {seen}: the "
               f"{wall} wall is likely placed about {d:.2f} m too far in (a corner named for the wrong wall, or "
               f"furniture's foot taken for the wall's floor line) - check the {wall}-wall landmarks of every frame")
        if h := o.get("wall_moved_out"):
            txt += (f". With the {wall} wall moved out past the camera (the camera kept where it is from the "
                    f"{OPPOSITE[wall]} wall): rms {h['rms_px_was']} -> {h['rms_px']} px, depth rms "
                    f"{h['depth_rms_was']} -> {h['depth_rms']}, room {h['room_was']} -> {h['room']} m")
            txt += (": about as good, so the wall is the error - find the landmarks that hold it (the frames whose "
                    f"{wall}-wall landmarks are furniture or another wall), fix them and fit again (until then "
                    "the room is more likely the moved-out size)" if h["about_as_good"] else
                    ": much worse, so the other frames do see that wall there - the camera stood beyond it, or "
                    "this frame's own landmarks are off: check them")
        return txt
    return f"{head} - check the landmarks / facing"


def _mean_depth_rms(cams: dict):
    v = [c["depth_rms"] for c in cams.values() if "depth_rms" in c]
    return round(sum(v) / len(v), 3) if v else None


def fit(req: FitRequest | dict, compare: bool = True) -> dict:
    """compare: a camera fitted beyond a wall it sees the room through is fitted again held inside, and the two
    fits are compared in the warning (the result's `outside`)."""
    req = req if isinstance(req, FitRequest) else FitRequest.model_validate(req)
    if bad := [cid for cid in req.inside if cid not in req.photos]:
        return {"ok": False, "error": f"inside: no photo {', '.join(bad)}"}
    for oid in req.openings:
        if ":" in oid:
            return {"ok": False, "error": f"opening id '{oid}' must not contain ':'"}
    for rp in req.relative:
        bad = [k for k in (rp.a, rp.b) if k not in req.photos]
        if bad:
            return {"ok": False, "error": f"relative {rp.a}->{rp.b}: no photo {', '.join(bad)}"}
        if len(rp.rotation) != 3 or any(len(row) != 3 for row in rp.rotation):
            return {"ok": False, "error": f"relative {rp.a}->{rp.b}: rotation must be 3x3"}
    try:
        m = _Model(req)
        m.residuals(m.init)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    unused, group_notes = _pick_groups(req)
    other_model = set(unused)

    def without(drop):
        return req.model_copy(update={"relative": [rp for i, rp in enumerate(req.relative) if i not in drop]})

    first = without(unused) if unused else req
    m, x, cost, JtJ, seed_notes = _fit_once(first, _Model(first) if unused else m)
    if req.relative:  # second pass without the relative poses that disagree with the first fit
        p = dict(zip(m.names, x))
        off = {i for i, rp in enumerate(req.relative) if i not in unused and rp.robust and _turn_error(p, rp) > DROP_DEG}
        if off:
            unused |= off
            kept = without(unused)
            m, x, cost, JtJ, seed_notes = _fit_once(kept, _Model(kept))
    seed_notes = group_notes + seed_notes
    p = dict(zip(m.names, x))
    r, rows = m.residuals(x, detail=True)
    depth_rows = [row for row in rows if row[3] == "depth"]
    rows = [row for row in rows if row[3] not in ("relative", "depth")]
    errs = [e for _, _, e, _ in rows]
    rms = math.sqrt(sum(e * e for e in errs) / max(1, len(errs)))
    W, D, H = p["width"], p["depth"], m.h(p)

    # 1-sigma of width / depth from the normal matrix (scaled by the residual)
    sigma = {}
    dof = max(1, len(r) - len(x))
    s2 = cost / dof

    # leave out the unknowns nothing measures (an opening seen in no photo): they make the matrix singular and
    # every sigma came out missing (a run showed "width ±0.00 m")
    top = max((JtJ[i][i] for i in range(len(x))), default=0.0)
    seen = [i for i in range(len(x)) if JtJ[i][i] > 1e-12 * top]
    in_seen = {i: j for j, i in enumerate(seen)}
    normal = {}

    def sd(k, floor=0.0):
        """1-sigma of unknown k: the landmarks' noise is their residual (at least `floor` px^2), the priors keep
        their own (a scale held only by the depth's 15 % prior is that loose, not as tight as the residual)."""
        i = in_seen.get(m.index[k])
        if i is None:
            return None
        if floor not in normal:
            N = _normal(m.residuals, x, m.scale, m.n_priors(), max(s2, floor))
            normal[floor] = [[N[a][b] for b in seen] for a in seen]
        col = _solve(normal[floor], [1.0 if j == i else 0.0 for j in range(len(seen))])
        return math.sqrt(col[i]) if col and col[i] > 0 else None

    for k in ("width", "depth", *(("height",) if m.fit_H else ())):
        if (v := sd(k)) is not None:
            sigma[k] = round(v, 3)

    warnings = []
    openings = []
    narrow = []  # openings narrower than NARROW_M with both edges marked
    for oid, o in req.openings.items():
        d = {"id": oid, "kind": o.kind, "wall": o.wall, "offset": round(p[f"{oid}.offset"], 2),
             "width": max(round(_width(p, oid), 2), 0.01)}  # > 0: Room.openings rejects 0
        sill = p[f"{oid}.sill"] if o.kind == "window" else 0.0
        if o.kind == "window":
            d["sill"] = round(sill, 2)
        d["height"] = round(p[f"{oid}.top"] - sill, 2)
        missing = [k for k in ("offset", "width", "sill", "top") if f"{oid}.{k}" in m.index and f"{oid}.{k}" not in m.used]
        if oid in m.one_end:
            edge = next(iter(_ends_seen(req)[oid]))
            other = {"start": "west" if o.wall in ("south", "north") else "south",
                     "end": "east" if o.wall in ("south", "north") else "north"}["end" if edge == "start" else "start"]
            warnings.append(f"{o.kind} {oid}: only its {edge} edge is marked, so its width is not measured (held near "
                            f"{_width(p, oid):.2f} m) - mark its other edge ({oid}:{'end' if edge == 'start' else 'start'}, "
                            f"the {other} end) in a frame that shows it")
        elif f"{oid}.width" in m.used and d["width"] < NARROW_M:
            narrow.append(d)
        if missing:
            warnings.append(f"opening {oid}: {', '.join(missing)} not seen in any photo (left at the guess)")
        openings.append(d)
    cams = {}
    outside = []
    for cid, c in req.photos.items():
        pos = [p[f"{cid}.x"], p[f"{cid}.y"], p[f"{cid}.z"]]
        fw, _, _ = _cam_axes(p, cid)
        cams[cid] = {"position": [round(v, 2) for v in pos], "look_at": _look_at(pos, fw, W, D, H),
                     "hfov_deg": round(m.hfov(p, cid), 1),
                     "yaw_deg": round((p[f"{cid}.yaw"] + 180) % 360 - 180, 1), "pitch_deg": round(p[f"{cid}.pitch"], 1),
                     "roll_deg": round(p[f"{cid}.roll"], 1)}
        sx, sy = sd(f"{cid}.x"), sd(f"{cid}.y")
        if sx is not None and sy is not None:
            cams[cid]["sigma_m"] = round(math.hypot(sx, sy), 2)
            if cams[cid]["sigma_m"] > 0.3:
                warnings.append(f"photo {cid}: camera position uncertain (±{cams[cid]['sigma_m']} m) - add landmarks "
                                "at different depths (near floor corners, a shared point)")
        if f"{cid}.dscale" in m.index:
            ds = math.exp(p[f"{cid}.dscale"])
            de = [e / DEPTH_PX_PER_LOG for cc, _, e, _ in depth_rows if cc == cid]
            cams[cid]["depth_scale"] = round(ds, 3)
            cams[cid]["depth_rms"] = round(math.sqrt(sum(e * e for e in de) / max(1, len(de))), 3)
            if abs(math.log(ds)) > 2.5 * DEPTH_SCALE_SIGMA:
                warnings.append(f"photo {cid}: the depth model is {ds:.2f}x the fitted distances - its depth may "
                                "be off for this view (a mirror, glass, a close-up)")
        mine = [e for cc, _, e, _ in rows if cc == cid]
        cams[cid]["rms_px"] = round(math.sqrt(sum(e * e for e in mine) / max(1, len(mine))), 1)
        if bare := sum(1 for o in c.surfaces if not o.depth):
            warnings.append(f"photo {cid}: {bare} surface pixels have no depth and were ignored "
                            "(give `depth`, or `image` with fit_photos(depth=true))")
        nobs = 2 * len(c.points) + len(c.lines) + sum(1 for o in c.surfaces if o.depth)
        if nobs < 6:
            warnings.append(f"photo {cid}: only {nobs} measurements; a camera has 6 unknowns - add points / lines")
        if out := _beyond(pos, W, D):
            through, solid, floor_out = _sight(m, p, cid, out[0])
            n_in = sum(through.values())
            # mostly through an opening: it stood there (a few pixels near the opening's edge cross the wall)
            # floor / ceiling pixels beyond the wall (and nothing through an opening): the room goes on there
            verdict = ("doorway" if n_in and n_in >= 2 * solid else "wall" if solid or floor_out else "unknown")
            outside.append({"photo": cid, "wall": out[0], "beyond_m": round(out[1], 2),
                            "position": cams[cid]["position"], "through": through, "solid": solid,
                            "floor_beyond": floor_out,
                            "verdict": verdict})
        elif not 0 < pos[2] < H:
            warnings.append(f"photo {cid}: camera ends up outside the room {cams[cid]['position']} (its height) - "
                            "check the landmarks / facing")
    if compare and (moved := [o["photo"] for o in outside if o["verdict"] == "wall"]):
        alt_req = req.model_copy(update={"inside": [*req.inside, *moved]})
        alt_req._hold = [(o["photo"], o["wall"], _inward(OPPOSITE[o["wall"]], o["position"], W, D),
                          o["position"][0 if o["wall"] in ("south", "north") else 1])
                         for o in outside if o["verdict"] == "wall"]
        alt = fit(alt_req, compare=False)
        if alt.get("ok"):
            for o in outside:
                if o["verdict"] == "wall":
                    o["wall_moved_out"] = {
                        "rms_px_was": round(rms, 1), "rms_px": alt["rms_px"],
                        "depth_rms_was": _mean_depth_rms(cams), "depth_rms": _mean_depth_rms(alt["cameras"]),
                        "room_was": [round(W, 2), round(D, 2), round(H, 2)],
                        "room": [alt["room"][k] for k in ("width", "depth", "height")],
                        "position": alt["cameras"][o["photo"]]["position"]}
                    h = o["wall_moved_out"]
                    h["about_as_good"] = (h["rms_px"] <= MOVED_RMS_GAIN * h["rms_px_was"] + 0.5 and
                                          (h["depth_rms"] is None or h["depth_rms_was"] is None or
                                           h["depth_rms"] <= MOVED_RMS_GAIN * h["depth_rms_was"] + 0.005))
    warnings += [_outside_text(o) for o in outside]
    for d in narrow:  # swapped start / end, or the edges really are that close: fit with them swapped and compare
        o = req.openings[d["id"]]
        start = "west" if o.wall in ("south", "north") else "south"
        text = (f"{o.kind} {d['id']}: only {d['width']:.2f} m wide - check its start / end landmarks (start is the "
                f"{start} end of the {o.wall} wall whichever way the camera looks)")
        alt = fit(_swap_ends(req, d["id"]), compare=False) if compare else {}
        if alt.get("ok"):
            w2 = next(x["width"] for x in alt["room"]["openings"] if x["id"] == d["id"])
            d["swapped"] = {"rms_px_was": round(rms, 1), "rms_px": alt["rms_px"], "width": w2}
            if alt["rms_px"] <= MOVED_RMS_GAIN * rms + 0.5 and w2 >= NARROW_M:
                text += (f". Swapped they fit as well or better (rms {rms:.1f} -> {alt['rms_px']} px, width {w2:.2f} m)"
                         ": they are likely swapped - fix them and fit again")
            elif alt["rms_px"] > MOVED_RMS_GAIN * rms + 0.5:
                text += (f". Swapped they fit worse (rms {rms:.1f} -> {alt['rms_px']} px), so they are labelled right: "
                         "the edges are that close in the fit - check that both are this opening's edges (not a "
                         "frame or a reveal) and the scale (a door top, the eye height)")
            else:
                text += (f". Swapped it stays narrow ({w2:.2f} m, rms {rms:.1f} -> {alt['rms_px']} px): its marks do not "
                         "hold the width apart - check that both edges are marked on this opening")
        warnings.append(text)
    suspects = []
    if compare and rms > SUSPECT_RMS_PX:
        # one object mislabeled (a wardrobe's door 0.8 m in front of the south wall marked as the room's
        # door pulled that camera 0.65 m and the room's depth 0.39 m short): fit without each of the worst
        tried = []
        for cid, at, _, _ in sorted(rows, key=lambda t: -t[2]):
            key = (cid, _landmark_object(req, at))
            if key not in tried:
                tried.append(key)
            if len(tried) == SUSPECT_TRIES:
                break
        alts = []
        for cid, obj in tried:
            alt = _warm_fit(_without_object(m.req, cid, obj), m.names, x)
            if alt is not None and alt[1] <= SUSPECT_GAIN * rms:
                alts.append((alt[1], cid, obj, alt[0]))
        alts.sort(key=lambda t: t[0])
        # leaving out a landmark next to the culprit also helps a little (its wall's line in the same photo): keep
        # the ones that explain about as much as the best
        for alt_rms, cid, obj, q in (a for a in alts if a[0] <= max(SUSPECT_NEAR * alts[0][0], alts[0][0] + 2.0)):
            marks = list(dict.fromkeys(at for cc, at, _, _ in rows if cc == cid and _landmark_object(req, at) == obj))
            d = {"photo": cid, "object": obj, "landmarks": marks, "rms_px_was": round(rms, 1),
                 "rms_px": round(alt_rms, 1), "room_was": [round(W, 2), round(D, 2), round(H, 2)],
                 "room": [round(q["width"], 2), round(q["depth"], 2), round(m.h(q), 2)],
                 "position_was": cams[cid]["position"],
                 "position": [round(q[f"{cid}.{k}"], 2) for k in ("x", "y", "z")]}
            suspects.append(d)
            warnings.append(f"photo {cid}: its {obj} landmarks ({', '.join(marks)}) do not fit the rest - without "
                            f"them rms {rms:.1f} -> {d['rms_px']} px, the room {d['room_was'][0]:.2f} x "
                            f"{d['room_was'][1]:.2f} -> {d['room'][0]:.2f} x {d['room'][1]:.2f} m and the camera "
                            f"{d['position_was']} -> {d['position']}: they are likely on something else (a cabinet "
                            "or wardrobe door, a frame, a reveal) or on another wall - look at the photo, relabel "
                            "or remove them, and fit again")
    if len(req.photos) > 1:  # a single photo carries every wall alone; nothing to add
        seen_by = {w: [cid for cid, c in req.photos.items() if w in _walls_of(req, c)] for w in WALLS}
        for dim, pair in (("width", ("west", "east")), ("depth", ("south", "north"))):
            for w in pair:
                sd_txt = f" (±{sigma[dim]:.2f} m)" if dim in sigma else ""
                if not seen_by[w]:
                    warnings.append(f"no photo has a landmark on the {w} wall: the room's {dim} is not measured, it "
                                    f"stays near the guess - add a corner, a floor / ceiling line or wall pixels of it")
                elif len(seen_by[w]) == 1:
                    # one frame's east wall pixels moved the width 3.0 -> 3.85 m (truth 3.66 m)
                    warnings.append(f"the room's {dim}{sd_txt} rests on one photo for the {w} wall "
                                    f"({seen_by[w][0]}): a mislabeled landmark or an off depth there moves the whole "
                                    f"{dim} - add the {w} wall (a corner, a line or wall pixels) from another frame")
    for cid, at, e, behind in rows:
        if behind:
            warnings.append(f"photo {cid}: '{at}' is behind the camera - check the facing / landmark name")
    warnings += seed_notes
    relative = []
    for i, rp in enumerate(req.relative):
        bt_b = _cam_basis(p, rp.b)
        d = {"a": rp.a, "b": rp.b, "turn_deg": round(_angle_deg(rp.rotation), 1),
             "turn_error_deg": round(_turn_error(p, rp), 1), "used": i not in unused}
        if rp.group is not None:
            d["group"] = rp.group
        if rp.direction is not None:
            dv = _sub((p[f"{rp.a}.x"], p[f"{rp.a}.y"], p[f"{rp.a}.z"]), (p[f"{rp.b}.x"], p[f"{rp.b}.y"], p[f"{rp.b}.z"]))
            n = math.sqrt(_dot(dv, dv))
            m_ = math.sqrt(sum(v * v for v in rp.direction)) or 1.0
            if n > 1e-6:
                c = sum(_dot(bt_b[i], dv) / n * rp.direction[i] / m_ for i in range(3))
                d["direction_error_deg"] = round(math.degrees(math.acos(max(-1.0, min(1.0, c)))), 1)
        relative.append(d)
        if d["turn_error_deg"] > DROP_DEG and i not in other_model:
            warnings.append(f"relative {rp.a}->{rp.b}: the landmarks turn the camera {d['turn_error_deg']} deg away "
                            "from what the video shows" + (" (left out)" if i in unused else "") +
                            " - check the landmarks of these frames (or the field of view); on a rendered walk such "
                            "a pair was the video's error (blank walls)")
        if rp.hfov_deg is not None and abs(rp.hfov_deg - m.hfov(p, rp.a)) > 5:
            warnings.append(f"relative {rp.a}->{rp.b} was measured with hfov {rp.hfov_deg} but the fit gives "
                            f"{m.hfov(p, rp.a):.0f}: run video_poses again with hfov_deg={m.hfov(p, rp.a):.0f}")
    h_sd = sd("height", floor=1.0) if m.fit_H else None  # at least 1 px of noise: exact data still says if it is fixed
    loose = m.fit_H and _ceiling_seen(req) and (h_sd is None or h_sd > HEIGHT_LOOSE_M)
    tied = _floor_and_ceiling_tied(req, unused) if loose else None
    if tied:
        # the shape is fixed and the room grows and shrinks as a whole: on ARKitScenes (run 2) height / width
        # stayed 0.86-0.88 while the relative poses' weight moved the height 2.49-3.12 m
        ds = [c["depth_scale"] for c in cams.values() if "depth_scale" in c]
        spread = f" (its scale per photo came out {min(ds):.2f}-{max(ds):.2f})" if len(ds) > 1 else ""
        warnings.append(f"the room's scale is loose (height ±{h_sd or 0:.2f} m, width ±{sigma.get('width', 0):.2f} m)"
                        f": the floor and the ceiling are tied ({len(tied)} photos joined by video_poses, or one "
                        "photo), so the shape is fixed, but the length comes only from the depth" + spread +
                        " - give the height if it is known (fit_height=false), or a shared point at a known "
                        "height (points: z)")
    elif loose:
        warnings.append(f"the wall height is not fixed by the photos (±{h_sd or 0:.2f} m), it stays near the guess "
                        f"{req.height} m: no photo (nor photos joined by video_poses) sees both the floor and the "
                        "ceiling, so the cameras' heights and the ceiling move together - pick a frame that sees a "
                        "ceiling corner and the floor below it, or give the height")
    if m.fit_H and not _ceiling_seen(req):
        warnings.append("fit_height: nothing at the ceiling is seen, the height stays near the guess - add a "
                        "ceiling corner / line")
    elif not m.fit_H and _depth_seen(req) and req.fit_height is None:
        warnings.append(f"the wall height is the given {req.height} m (nothing at the ceiling is seen): add a "
                        "ceiling corner / line to fit it")
    if m.depth_only:
        zs = [cams[cid]["position"][2] for cid in req.photos]
        warnings.append("nothing of known length is seen: the room's scale comes from the depth (each photo's scale "
                        "±15%, and a depth model can be off for all of them) and the camera height (mean "
                        f"{sum(zs) / len(zs):.2f} m against eye_height {req.eye_height} m) - give eye_height or the "
                        "room height if known "
                        "(fit_height=false with a ceiling corner seen), or a shared point at a known height"
                        + (" (a door's top or a typical height held it too)" if m.size_priors else
                           " - a door's top (seen) or a shared point at a typical height (a desk or counter top, "
                           "points: z with z_sigma) holds it within a few %"))
    if not m.scale_seen:
        warnings.append(f"no landmark at the ceiling is seen: the room's scale comes from the camera height "
                        f"{req.eye_height} m (eye_height)"
                        + (" and the typical sizes (size_priors)" if m.size_priors else "")
                        + " - add a ceiling corner / line, a door's top, or give eye_height if known")
    if rms > 6:
        warnings.append(f"rms {rms:.1f} px is high: a landmark may be mislabeled (see worst)")
    if len(r) < len(x):
        warnings.append(f"{len(r)} measurements for {len(x)} unknowns: under-determined, add landmarks")

    points = {}
    for pid in req.points:
        points[pid] = [round(v, 2) for v in m.point(p, pid)]
    located = []
    for lo in req.locate:
        if lo.photo not in req.photos:
            located.append({"name": lo.name, "error": f"no photo '{lo.photo}'"})
            continue
        c = req.photos[lo.photo]
        pos = (p[f"{lo.photo}.x"], p[f"{lo.photo}.y"], p[f"{lo.photo}.z"])
        fw, rt, up = _cam_axes(p, lo.photo)
        f = c.size[0] / 2 / math.tan(math.radians(m.hfov(p, lo.photo)) / 2)
        a, b = (lo.px[0] - c.size[0] / 2) / f, -(lo.px[1] - c.size[1] / 2) / f
        ray = tuple(fw[i] + a * rt[i] + b * up[i] for i in range(3))
        if abs(ray[2]) < 1e-6 or (lo.z - pos[2]) / ray[2] <= 0:
            located.append({"name": lo.name, "error": f"the pixel does not look at height {lo.z}"})
            continue
        t = (lo.z - pos[2]) / ray[2]
        located.append({"name": lo.name, "photo": lo.photo, "xy": [round(pos[0] + t * ray[0], 2),
                                                                   round(pos[1] + t * ray[1], 2)], "z": lo.z,
                        **({"object": lo.object} if lo.object else {}), **({"edge": lo.edge} if lo.edge else {})})
    objects, far = located_objects(located)
    if one := sorted(k for k, v in objects.items() if len(v["points"]) == 1):
        warnings.append(f"locate: {', '.join(one)} rest on one point each - that is a foot or corner, not the "
                        "middle, so `center` is off by half the size: give 2-4 feet / corners (diagonal ones best)")
    for oid, name, d in far:
        warnings.append(f"locate: '{name}' of {oid} is {d:.2f} m apart between photos - one pixel is on another "
                        "object or the cameras disagree (check that photo's landmarks)")
    # typical sizes that held the scale: what the landmarks alone would give is what the prior pulled against
    size_priors = [{"of": k, "typical": v, "sigma": sg, "fitted": round(p[k], 2)} for k, v, sg in m.size_priors]
    for d in size_priors:
        if abs(d["fitted"] - d["typical"]) > 2 * d["sigma"]:
            warnings.append(f"{d['of']} came out {d['fitted']} m against the typical {d['typical']} m: the scale is "
                            "off, or this one is not typical (give its `top` / `z_sigma`, or label the landmark again)")
    scale = _scale_evidence(m, req, p, cams, unused, H)
    warnings += scale.pop("warnings")
    worst = sorted(rows, key=lambda t: -t[2])[:5]
    return {
        "ok": True,
        "rms_px": round(rms, 1),
        "room": {"width": round(W, 2), "depth": round(D, 2), "height": round(H, 2),
                 "height_fitted": m.fit_H, "openings": openings},
        "sigma_m": sigma,
        "cameras": cams,
        "points": points,
        "located": located,
        **({"objects": objects} if objects else {}),
        "worst": [{"photo": c, "landmark": a, "err_px": round(e, 1)} for c, a, e, _ in worst],
        "measurements": len(r), "unknowns": len(x),
        **({"relative": relative} if relative else {}),
        **({"size_priors": size_priors} if size_priors else {}),
        **({"scale": scale} if scale["sources"] else {}),
        **({"outside": outside} if outside else {}),
        **({"suspects": suspects} if suspects else {}),
        "warnings": warnings,
    }


def _median(v: list[float]) -> float:
    s = sorted(v)
    return s[len(s) // 2] if len(s) % 2 else (s[len(s) // 2 - 1] + s[len(s) // 2]) / 2


def _scale_evidence(m: _Model, req: FitRequest, p, cams: dict, unused: set[int], H: float) -> dict[str, Any]:
    """Everything that says how big the room is, each turned into `scale` = the factor the fitted room would
    have to grow by to match that source alone (1.0 = it agrees with the fit), with the source's own error as
    `range`: the depth maps, door tops / typical heights, the eye height, the typical wall height, the given
    height. Their median and spread, and the one source that stands apart from the others. The fit has already weighed the sources it used against each other, so a source that is 20%
    off shows here as less than 20% (the fit moved toward it): the scales say who disagrees, not by how much
    the room is wrong."""
    src: list[dict[str, Any]] = []

    def add(by: str, scale: float, err: float | None, **more):
        d = {"by": by, "scale": round(scale, 3), **more}
        if err is not None:
            d["range"] = [round(scale * (1 - err), 3), round(scale * (1 + err), 3)]
        src.append(d)

    ds = [c["depth_scale"] for c in cams.values() if "depth_scale" in c]
    if ds:
        add("depth", _median(ds), max(DEPTH_ERR_FLOOR, SCALE_ERR["depth"] / math.sqrt(len(ds))), n=len(ds),
            spread=[round(min(ds), 3), round(max(ds), 3)], used=True)
    for u, v, sg in m.size_priors:
        if p[u] > 0.05:
            add("door top" if u.endswith(".top") else "typical height", v / p[u], sg / v, of=u, used=True)
    zs = [p[f"{cid}.z"] for cid in req.photos]
    mean_z = sum(zs) / len(zs)
    if mean_z > 0.05:  # always listed; it holds the fit only when nothing better is seen
        add("eye height", req.eye_height / mean_z, SCALE_ERR["eye height"], camera_height_m=round(mean_z, 2),
            used=bool(not m.scale_seen or m.depth_only))
    if _ceiling_seen(req):
        if m.fit_H:
            lo, hi = CEILING_TYPICAL_M
            src.append({"by": "ceiling (typical)", "scale": round(min(max(1.0, lo / H), hi / H), 3),
                        "range": [round(lo / H, 3), round(hi / H, 3)], "height_m": round(H, 2), "used": False})
        else:
            src.append({"by": "height (given)", "scale": 1.0, "range": [1.0, 1.0], "height_m": round(H, 2), "used": True})
    out: dict[str, Any] = {"sources": src, "warnings": []}
    if not src:
        return out
    free = [s for s in src if s["by"] != "height (given)"]  # the given height is the fit itself
    # the typical wall height is a range, not a value: it joins the median only when the fitted height is outside
    vals = [s["scale"] for s in free if s["by"] != "ceiling (typical)" or s["scale"] != 1.0]
    if vals:
        med = _median(vals)
        out["median"] = round(med, 3)
        one = next(s for s in free if s["scale"] == vals[0])
        out["pm"] = round(max(abs(v - med) for v in vals) if len(vals) > 1 else (one["range"][1] - one["range"][0]) / 2, 3)
    # one source apart from the others, which agree among themselves: the others' ranges share an interval,
    # this one's range misses it. Among the sources that hold the fit (an eye height that is only listed
    # neither is named nor has a say: handheld cameras are anywhere from 1.0 to 1.6 m)
    held = [s for s in free if s["used"] or s["by"] == "ceiling (typical)"]
    if len(held) >= 3:
        for s in held:
            others = [o for o in held if o is not s]
            mid = _median([o["scale"] for o in others])
            lo, hi = s["range"]
            both = max(o["range"][0] for o in others), min(o["range"][1] for o in others)
            if both[0] <= both[1] and (hi < both[0] or lo > both[1]) and abs(s["scale"] - mid) > SCALE_OUTLIER * mid:
                out["outlier"] = s["by"]
                why = {"depth": "the depth model is off for this room (mirrors, glass, close-ups)",
                       "door top": "this door is not a typical one: give its `top`, or label its top again",
                       "typical height": "this point is not at its typical height: give its z",
                       "eye height": "the camera was not held at eye_height: give eye_height if known",
                       "ceiling (typical)": "a high or low ceiling: give the height if known (fit_height=false)"}[s["by"]]
                out["warnings"].append(
                    f"scale: {s['by']} asks for a room {s['scale']:.2f}x the fit while the others agree ("
                    + ", ".join(f"{o['by']} {o['scale']:.2f}" for o in others) + f") - {why}")
                break
    return out


LOCATE_SPREAD_M = 0.4  # the same point of an object seen in two photos farther apart than this is suspect


def located_objects(located: list[dict]) -> tuple[dict, list[tuple[str, str, float]]]:
    """Each object's located points summed up: {id: {center, points, photos}} (center = the mean of its points,
    one point per name: a name seen in several photos is their mean), and the (object, name, metres) of names whose
    photos disagree by more than LOCATE_SPREAD_M."""
    by: dict[str, dict[str, list]] = {}
    for lo in located:
        if lo.get("object") and "xy" in lo:
            by.setdefault(lo["object"], {}).setdefault(lo["name"], []).append(lo)
    objects, far = {}, []
    for oid, names in by.items():
        pts = {}
        for name, los in names.items():
            xs = [lo["xy"] for lo in los]
            pts[name] = [round(sum(x[0] for x in xs) / len(xs), 2), round(sum(x[1] for x in xs) / len(xs), 2)]
            d = max(math.dist(a, b) for a in xs for b in xs)
            if d > LOCATE_SPREAD_M:
                far.append((oid, name, d))
        c = [round(sum(v[i] for v in pts.values()) / len(pts), 2) for i in (0, 1)]
        edges = {name: los[0]["edge"] for name, los in names.items() if los[0].get("edge")}
        objects[oid] = {"center": c, "points": pts, **({"edges": edges} if edges else {}),
                        "photos": sorted({lo["photo"] for los in names.values() for lo in los}), **_spread(pts)}
    return objects, far


def _spread(pts: dict[str, list[float]]) -> dict:
    """What the points say about the object's footprint: one point is a corner / foot, not the middle;
    two or more give the span; three or more the main axis (axis_deg, 0 = along x, 0-180) and extent along /
    across it."""
    v = list(pts.values())
    if len(v) == 1:
        return {"note": "one point: a corner or foot, not the middle - add 2-4 feet / corners"}
    if len(v) == 2:
        return {"span_m": round(math.dist(*v), 2)}
    mx, my = sum(p[0] for p in v) / len(v), sum(p[1] for p in v) / len(v)
    sxx = sum((p[0] - mx) ** 2 for p in v)
    syy = sum((p[1] - my) ** 2 for p in v)
    sxy = sum((p[0] - mx) * (p[1] - my) for p in v)
    a = 0.5 * math.atan2(2 * sxy, sxx - syy)
    ca, sa = math.cos(a), math.sin(a)
    along = [(p[0] - mx) * ca + (p[1] - my) * sa for p in v]
    across = [-(p[0] - mx) * sa + (p[1] - my) * ca for p in v]
    return {"axis_deg": round(math.degrees(a) % 180, 1),
            "extent_m": [round(max(along) - min(along), 2), round(max(across) - min(across), 2)]}


def _cam_axes(p: dict, cid: str):
    return _axes(p[f"{cid}.yaw"], p[f"{cid}.pitch"], p[f"{cid}.roll"])


def _cam_basis(p: dict, cid: str):
    return _basis(p[f"{cid}.yaw"], p[f"{cid}.pitch"], p[f"{cid}.roll"])


def _turn_error(p: dict, rp: RelativePose) -> float:
    """Degrees between the measured turn and the turn of the fitted cameras."""
    pred = _rel_rotation(_cam_basis(p, rp.a), _cam_basis(p, rp.b))
    return _angle_deg(_matmul_t(rp.rotation, pred))


def _pick_groups(req: FitRequest) -> tuple[set[int], list[str]]:
    """A photo in the relative poses of two groups (two structure-from-motion models sharing a frame) is kept in
    one: each choice is fitted and the one with the smallest landmark residual wins (on a handheld video the
    wrong model's pose was 11.7 px off, the right one 2.6 px, both together 15.4 px). Returns the indices of the
    relative poses left out and notes."""
    groups: dict[str, set[str]] = {}
    for rp in req.relative:
        if rp.group is not None:
            for k in (rp.a, rp.b):
                groups.setdefault(k, set()).add(rp.group)
    shared = sorted(k for k, g in groups.items() if len(g) > 1)
    if not shared:
        return set(), []
    choices = [[]]
    for k in shared:
        choices = [c + [(k, g)] for c in choices for g in sorted(groups[k])]
    notes = []
    if len(choices) > MAX_GROUP_CHOICES:
        notes.append(f"{len(shared)} photos are in two models: {len(choices)} choices, only the first "
                     f"{MAX_GROUP_CHOICES} are tried")
        choices = choices[:MAX_GROUP_CHOICES]
    best = None
    for c in choices:
        keep = dict(c)
        drop = {i for i, rp in enumerate(req.relative)
                if rp.group is not None and any(k in keep and keep[k] != rp.group for k in (rp.a, rp.b))}
        kept = req.model_copy(update={"relative": [rp for i, rp in enumerate(req.relative) if i not in drop]})
        m, x, _, _, _ = _fit_once(kept, _Model(kept))
        _, rows = m.residuals(x, detail=True)
        errs = [e for _, _, e, kind in rows if kind not in ("relative", "depth")]
        rms = math.sqrt(sum(e * e for e in errs) / max(1, len(errs)))
        if best is None or rms < best[0]:
            best = (rms, drop, c)
    for k, g in best[2]:
        notes.append(f"photo {k} is in {len(groups[k])} models ({', '.join(sorted(groups[k]))}): kept in {g}, "
                     f"whose poses fit the landmarks best (rms {best[0]:.1f} px)")
    return best[1], notes


def _fit_once(req: FitRequest, m: _Model):
    """Levenberg-Marquardt from a few starts (the video's turns, then pitches); the best of them."""
    n_obs = sum(2 * len(c.points) + len(c.lines) for c in req.photos.values())
    starts = []
    seeded, seed_notes = _seed(req, m)
    if seeded is not None:
        starts.append(seeded)
    for pitch in (8.0, 0.0, 20.0):  # a few starts; keep the best
        starts.append([pitch if n.endswith(".pitch") else v for n, v in zip(m.names, m.init)])
    best = None
    for x0 in starts:
        x, cost, JtJ = _lm(m.residuals, x0, m.scale)
        if best is None or cost < best[1]:
            best = (x, cost, JtJ)
        if math.sqrt(cost / max(1, n_obs)) < 3:
            break
    return m, *best, seed_notes


def _seed(req: FitRequest, m: _Model):
    """Starting yaw / pitch of the cameras from the measured turns: from the photo with the most landmarks
    (its yaw from `facing`), through the relative poses. Returns (start vector or None, warnings)."""
    if not req.relative:
        return None, []
    nobs = {cid: 2 * len(c.points) + len(c.lines) for cid, c in req.photos.items()}
    links: dict[str, list[tuple[str, RelativePose, bool]]] = {}
    for rp in req.relative:
        links.setdefault(rp.a, []).append((rp.b, rp, True))
        links.setdefault(rp.b, []).append((rp.a, rp, False))
    x0 = list(m.init)
    notes = []
    done: set[str] = set()
    for root in sorted(req.photos, key=lambda k: -nobs[k]):
        if root in done or root not in links:
            continue
        bt = {root: _basis(x0[m.index[f"{root}.yaw"]], x0[m.index[f"{root}.pitch"]], x0[m.index[f"{root}.roll"]])}
        queue = [root]
        done.add(root)
        while queue:
            k = queue.pop(0)
            for other, rp, forward in sorted(links[k], key=lambda t: -t[1].weight):
                if other in done:
                    continue
                # B_b = B_a R^T  (forward: k is a);  B_a = B_b R  (k is b). Rows of B^T are the camera axes.
                R = rp.rotation
                cols = [[sum(bt[k][i][c] * (R[j][i] if forward else R[i][j]) for i in range(3)) for c in range(3)]
                        for j in range(3)]
                bt[other] = cols
                yaw, pitch, roll = _yaw_pitch_roll(cols)
                x0[m.index[f"{other}.yaw"]], x0[m.index[f"{other}.pitch"]] = yaw, pitch
                x0[m.index[f"{other}.roll"]] = roll
                given = m.init[m.index[f"{other}.yaw"]]
                if abs((yaw - given + 180) % 360 - 180) > 60:
                    notes.append(f"photo {other}: facing {req.photos[other].facing!r} is "
                                 f"{abs((yaw - given + 180) % 360 - 180):.0f} deg from the turn the video shows "
                                 f"from {k} (yaw {yaw:.0f})")
                done.add(other)
                queue.append(other)
    return x0, notes
