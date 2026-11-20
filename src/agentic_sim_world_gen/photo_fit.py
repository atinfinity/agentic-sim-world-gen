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
"""
from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, Field, PrivateAttr, field_validator, model_validator

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

    @model_validator(mode="before")
    @classmethod
    def _no_relative(cls, data):
        if isinstance(data, dict) and data.get("relative"):
            raise ValueError("`relative` (camera turns between the frames of a video) is no longer read: video input "
                             "was removed. Fit the photos without it.")
        return data
    hfov_deg: float | None = Field(default=None, description="rough field of view (soft prior; default 65)")
    hfov_shared: bool = Field(default=True, description="one field of view for all photos without hfov_deg (same "
                              "camera); false: fit one per photo")
    openings: dict[str, OpeningGuess] = Field(default_factory=dict)
    points: dict[str, SharedPoint] = Field(default_factory=dict)
    locate: list[Locate] = Field(default_factory=list)
    place: str | None = Field(default=None, description="what the room is (Room.place: home, office, school ...): "
                              "the wall heights it may have (CEILING_RANGE_BY_PLACE: home, office, school, lab, "
                              "hospital, cafe, restaurant); a fitted height outside is fitted again held at a typical "
                              "one. Other places (shops, factories, halls) and none: not held, only warned of. "
                              "fit_photos takes it from the scene folder")
    height_range: tuple[float, float] | None = Field(default=None, description="wall heights this room may have [m] "
                                                     "(0 < low < high), in place of the place's (a hall: (3, 6))")

    @field_validator("height_range")
    @classmethod
    def _height_range_ok(cls, v):
        if v is not None and not 0 < v[0] < v[1]:
            raise ValueError(f"height_range {list(v)}: needs 0 < low < high [m]")
        return v
    inside: list[str] = Field(default_factory=list, description="photos whose camera stood in the room: held at "
                              "least 0.1 m inside every wall (the fit may move the camera or the walls; when a "
                              "wall is placed wrong, fix its landmarks instead - see the result's `outside`)")
    # (photo, wall, distance to the opposite wall, along) held by the comparison of a camera beyond a wall:
    # the camera keeps its place in the room, so the wall it is beyond has to move out
    _hold: list = PrivateAttr(default_factory=list)


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
MIN_OBS = 6  # a camera has 6 unknowns: fewer measurements leave it free (warned)
PRIOR_PX = 10.0  # the priors above are written so that about 10 px is their 1-sigma (the depth scale's 15%)
# What sets the room's scale, side by side. Each source's own error (share, about 1 sigma):
CEILING_TYPICAL_M = (2.4, 2.7)  # wall heights of homes and offices: a fitted height outside says the scale is off
# wall heights a room may have: a fitted height outside is fitted again held at a typical one (a classroom
# fitted 3.53 m against 2.65 m, its room 13.44 x 8.37 m against 9.59 x 6.19; held at 2.7: 10.27 x 6.39).
# No place, or a place not listed (shops, factories, warehouses, halls, stations: high ceilings, "other"): not held,
# only warned of (height_suspect), unless height_range is given
CEILING_RANGE_BY_PLACE: dict[str, tuple[float, float]] = {
    "home": (2.2, 2.9), "office": (2.3, 3.2), "school": (2.4, 3.3), "lab": (2.3, 3.5), "hospital": (2.3, 3.2),
    "cafe": (2.3, 3.6), "restaurant": (2.3, 3.6),
}
HELD_RMS_GAIN = 1.15  # the held fit is taken when its pixel rms is within 15% ...
HELD_RMS_PX = 1.0  # ... + 1 px of the fitted one's (see _height_refit), and the depth's rms within 15% ...
HELD_DEPTH_RMS = 0.01  # ... + 1 cm (meters, as the photos' depth_rms)
# scale sources measured in meters; not the eye height: a soft prior (a handheld walk 1.06-1.6 m),
# the one most likely off when nothing else is seen (the classroom's scale above)
METRIC_SOURCES = ("depth", "door top", "typical height")
SCALE_ERR = {"depth": 0.15,  # one photo's depth scale (-15 .. +24 %); averages over the photos, never under ...
             "door top": 0.05,  # DOOR_TOP_SIGMA_M of 2.0 m
             "eye height": 0.15}  # a handheld walk's mean camera height: 1.06-1.6 m against 1.4
DEPTH_ERR_FLOOR = 0.06  # ... this: a depth model can be off the same way for every photo of a room
SCALE_OUTLIER = 0.1  # one source this far from the others' median, while the others agree: named
UNMEASURED_SHARE = 0.25  # a width / depth whose 1-sigma is more than this share of it is not measured


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


# ---------- the model ----------

def _has_depth(c: PhotoObs) -> bool:
    """The photo has a landmark or surface pixel with a depth."""
    return any(o.depth for o in [*c.points, *c.lines, *c.surfaces])


def _depth_seen(req: FitRequest) -> bool:
    return any(_has_depth(c) for c in req.photos.values())


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
    errs = [e for _, _, e, k in rows if k != "depth"]
    return dict(zip(m.names, x2)), math.sqrt(sum(e * e for e in errs) / max(1, len(errs)))


def _prior_photos(req: FitRequest) -> dict[str, set[str]]:
    """{size prior's unknown: the photos that see it}, for each door (or opening with a `top`) whose top is seen
    (<opening>:<start|end>:top or the line <opening>:top) and each shared point with a typical height
    (z_sigma) that is seen, in the order of the openings, then the points."""
    tops: dict[str, set[str]] = {}
    pts: dict[str, set[str]] = {}
    for cid, c in req.photos.items():
        for o in c.points:
            parts = o.at.split(":")
            if parts[0] in req.openings and len(parts) == 3 and parts[2] == "top":
                tops.setdefault(parts[0], set()).add(cid)
            elif o.at in req.points:
                pts.setdefault(o.at, set()).add(cid)
        for o in c.lines:
            parts = o.along.split(":")
            if parts[0] in req.openings and len(parts) == 2 and parts[1] == "top":
                tops.setdefault(parts[0], set()).add(cid)
    out = {}
    for oid, o in req.openings.items():
        top = o.top if o.top is not None else req.door_top if o.kind == "door" else None
        if oid in tops and top is not None:
            out[f"{oid}.top"] = tops[oid]
    for pid, sp in req.points.items():
        if pid in pts and sp.z is not None and sp.z_sigma:
            out[f"{pid}.z"] = pts[pid]
    return out


def _size_priors(req: FitRequest) -> list[tuple[str, float, float]]:
    """Typical sizes that hold the scale softly: (unknown, value, sigma) for each door (or opening with a
    `top`) whose top is seen, and each shared point with a typical height (z_sigma) that is seen."""
    out = []
    for u in _prior_photos(req):
        oid, ax = u.rsplit(".", 1)
        if ax == "top":
            o = req.openings[oid]
            out.append((u, o.top if o.top is not None else req.door_top, o.top_sigma))
        else:
            sp = req.points[oid]
            out.append((u, sp.z, sp.z_sigma))
    return out


def _n_obs(c: PhotoObs) -> int:
    """A photo's measurements: 2 per point, 1 per line, 1 per surface pixel with a depth."""
    return 2 * len(c.points) + len(c.lines) + sum(1 for o in c.surfaces if o.depth)


def _ceiling_seen(req: FitRequest) -> bool:
    """Something at the ceiling height is seen: a ceiling corner or line, or a ceiling pixel with a depth."""
    return any(_sees(c, "ceiling") for c in req.photos.values())


def _floor_and_ceiling_tied(req: FitRequest) -> list[str] | None:
    """A photo that sees both the floor and the ceiling: [its id], or None. Its camera's height is then tied to
    both, and the height is fixed as far as the scale is."""
    for cid, c in req.photos.items():
        if _sees(c, "floor") and _sees(c, "ceiling"):
            return [cid]
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
            # a window's top starts at a door's 2.0 m on purpose: in Japanese rooms the window head lines up with
            # the door head. Tried 1.6-2.4 m for windows: 1.8-2.4 gave the same fits; 1.6 sent one into a
            # wrong minimum (test_a_thin_camera_beyond_a_wall_is_loose). The photos set the top; this is only the start
            self._add(f"{oid}.top", 2.0, 1)
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
            if _has_depth(c):
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
        fit a room of any size. Only photos whose camera is held count: 6+ measurements. A photo with a ceiling corner and two lines (4) can sit anywhere, and took the scale from the
        camera heights: the room came out 3x (#27)."""
        if _depth_seen(self.req):
            return True
        held = _cameras_held(self.req)
        for cid, c in self.req.photos.items():
            if cid not in held:
                continue
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
        H = self.h(p)
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
        for u, want, sg in self.size_priors:
            r.append((p[u] - want) / sg * PRIOR_PX)
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


def _ray(m: _Model, p: dict, cid: str, px):
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


def _sight(m: _Model, p: dict, cid: str, wall: str):
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
                "photo was taken there; landmarks of the hallway itself must not be named for this room")
    if o["verdict"] == "loose":
        return (f"{head}, but its {o['measurements']} measurements leave the camera free: "
                "its place says nothing about the walls - add points / lines to it, or leave it out")
    if o["verdict"] == "wall":
        seen = " and ".join(t for t in (f"sees {o['solid']} of its pixels through the wall itself (not an opening)"
                                        if o["solid"] else "",
                                        f"{o['floor_beyond']} floor / ceiling pixels land beyond the wall"
                                        if o["floor_beyond"] else "") if t)
        txt = (f"{head} but {seen}: the "
               f"{wall} wall is likely placed about {d:.2f} m too far in (a corner named for the wrong wall, or "
               f"furniture's foot taken for the wall's floor line) - check the {wall}-wall landmarks of every photo")
        if h := o.get("wall_moved_out"):
            txt += (f". With the {wall} wall moved out past the camera (the camera kept where it is from the "
                    f"{OPPOSITE[wall]} wall): rms {h['rms_px_was']} -> {h['rms_px']} px, depth rms "
                    f"{h['depth_rms_was']} -> {h['depth_rms']}, room {h['room_was']} -> {h['room']} m")
            txt += (": about as good, so the wall is the error - find the landmarks that hold it (the photos whose "
                    f"{wall}-wall landmarks are furniture or another wall), fix them and fit again (until then "
                    "the room is more likely the moved-out size)" if h["about_as_good"] else
                    ": much worse, so the other photos do see that wall there - the camera stood beyond it, or "
                    "this photo's own landmarks are off: check them")
        return txt
    return f"{head} - check the landmarks / facing"


def _mean_depth_rms(cams: dict):
    v = [c["depth_rms"] for c in cams.values() if "depth_rms" in c]
    return round(sum(v) / len(v), 3) if v else None


def fit(req: FitRequest | dict, compare: bool = True) -> dict:
    """compare: a camera fitted beyond a wall it sees the room through is fitted again held inside, and the two
    fits are compared in the warning (the result's `outside`); a fitted wall height outside the room's range or
    suspect is fitted again held (`height_refit`). The fit is solved without the comparisons first; they run
    once, on the fit that is taken."""
    req = req if isinstance(req, FitRequest) else FitRequest.model_validate(req)
    r = _fit(req, compare=False)
    if compare and r.get("ok"):
        winner, solved, info = req, r.pop("_solved"), None
        if (got := _height_refit(req, r)) is not None:
            alt_req, alt, info = got
            if info["adopted"] == "held":
                winner, solved = alt_req, alt.pop("_solved")
        r = _fit(winner, compare=True, solved=solved)
        if r.get("ok"):
            if info is not None:
                _add_height_refit(r, info)
            # "not measured" stays first, ahead of the refit's warning
            r["warnings"].sort(key=lambda w: not w.startswith("not measured"))
    r.pop("_solved", None)
    r.pop("_rms_but_one", None)
    if r.get("ok") and req.place is not None:
        from agentic_sim_world_gen.scene.spec import PLACES

        if req.place not in PLACES:
            r["warnings"].append(f"place '{req.place}' is not a Room.place (home, office, school, shop, factory ...): "
                                 "no wall height range was used - give a known place or height_range")
    return r


def height_range(req: FitRequest) -> tuple[float, float] | None:
    """The wall heights the room may have: height_range, else the listed place's; None (no place, a place
    not listed - shops, factories, halls, stations) = the height is not held, only warned of (height_suspect)."""
    if req.height_range is not None:
        return req.height_range
    return CEILING_RANGE_BY_PLACE.get(req.place) if req.place is not None else None


def _held_height(req: FitRequest, H: float, rng: tuple[float, float]) -> float:
    """The height a refit holds: the request's height when it was given and is in the range, else the typical
    one on H's side (2.4 below, 2.7 above, as _height_suspect's hint), kept in the range."""
    if "height" in req.model_fields_set and rng[0] <= req.height <= rng[1]:
        return req.height
    lo, hi = CEILING_TYPICAL_M
    return min(max(lo if H < lo else hi, rng[0]), rng[1])


def _metric_worse(r: dict, alt: dict) -> list[str]:
    """What is measured in meters that fits the held fit clearly worse than the fitted one: the
    depth's residual (mean of the photos' depth_rms, as wall_moved_out) beyond HELD_RMS_GAIN + HELD_DEPTH_RMS, and
    each source of the scale in METRIC_SOURCES (the depth's scale, a door top, a typical height)
    whose range takes in the fitted room but not the held one. The pixels say nothing of the scale: a really high
    ceiling seen by the depth fits them as well held."""
    out = []
    d0, d1 = _mean_depth_rms(r["cameras"]), _mean_depth_rms(alt["cameras"])
    if d0 is not None and d1 is not None and d1 > HELD_RMS_GAIN * d0 + HELD_DEPTH_RMS:
        out.append(f"depth rms {d0:.3f} -> {d1:.3f}")

    def metric(res):
        return {s["by"]: s for s in (res.get("scale") or {}).get("sources", [])
                if s["by"] in METRIC_SOURCES}

    def takes_in(s):
        lo, hi = s.get("range", (s["scale"], s["scale"]))
        return lo <= 1.0 <= hi

    was = metric(r)
    for by, s in metric(alt).items():
        if not takes_in(s) and (by not in was or takes_in(was[by])):
            out.append(f"{by} asks for {s['scale']:.2f}x the held room")
    return out


def _height_refit(req: FitRequest, r: dict) -> tuple[FitRequest, dict, dict] | None:
    """Fit again with the wall height held, when the fitted height is outside the room's range, or suspect
    (height_suspect) with the place's range (an explicit height_range that takes it in is kept). No range (no place, a place
    not listed): nothing. Returns (the held request, its fit without the comparisons, `height_refit`: both fits, the
    one taken and why), or None. The held fit is taken when its landmarks fit about as well (pixel rms within
    HELD_RMS_GAIN, +HELD_RMS_PX) and nothing measured in meters fits it clearly worse (_metric_worse): a room whose
    scale alone was off fits the pixels as well at any height, while ceiling landmarks on a soffit / beam (height_suspect:
    the size right, the height alone off) pull a held room out of shape (1.8 m held at 2.4: 4.5 x 3.6 -> 7.5 x 6.9
    m, rms 0 -> 62 px)."""
    rng, room = height_range(req), r["room"]
    if rng is None or not room["height_fitted"] or not _ceiling_seen(req):
        return None
    H = room["height"]
    if not rng[0] <= H <= rng[1]:
        where = f" ({req.place})" if req.place and req.height_range is None else ""
        why = f"is outside {rng[0]}-{rng[1]} m{where}"
    elif "height_suspect" in (r.get("scale") or {}) and req.height_range is None:
        why = "is suspect (the room's size agrees without it)"
    else:
        return None
    held = _held_height(req, H, rng)
    alt_req = req.model_copy(update={"fit_height": False, "height": held})
    alt = _fit(alt_req, compare=False)
    if not alt.get("ok"):
        return None

    def brief(res):
        d = _mean_depth_rms(res["cameras"])
        return {"width": res["room"]["width"], "depth": res["room"]["depth"], "height": res["room"]["height"],
                "rms_px": res["rms_px"], "rms_px_but_one": res["_rms_but_one"],
                **({"depth_rms": d} if d is not None else {})}

    was, now = brief(r), brief(alt)
    if "height_suspect" in (r.get("scale") or {}):
        was["height_suspect"] = r["scale"]["height_suspect"]
    # ... or so without the one landmark each fits worst: one desk corner seen in one frame, far off at the held
    # height and on at a fitted one, decided the height alone (misread ceiling landmarks are off in many)
    pixels = any(now[k] <= HELD_RMS_GAIN * was[k] + HELD_RMS_PX for k in ("rms_px", "rms_px_but_one"))
    worse = _metric_worse(r, alt)
    take = pixels and not worse
    info = {"adopted": "held" if take else "fitted", "reason": why, "held_height_m": held, "range": list(rng),
            "fitted": was, "held": now, **({"metric_worse": worse} if worse else {})}
    head = (f"height: the fitted wall height {H:.2f} m {why} - fitted again with the height held at {held:.2f} m: the "
            f"room {was['width']:.2f} x {was['depth']:.2f} -> {now['width']:.2f} x {now['depth']:.2f} m, rms "
            f"{was['rms_px']:.1f} -> {now['rms_px']:.1f} px")
    if take:
        info["warning"] = (head + ". The held fit is used (it fits as well: the scale was off, not the ceiling). If the "
                           f"ceiling really is {H:.2f} m, fit again with fit_height=false, height={H:.2f} (or "
                           "height_range); the fitted one is in height_refit.fitted")
    elif not pixels:
        info["warning"] = (head + ". The fitted one is kept: the landmarks do not fit the held height, so the ceiling's "
                           "landmarks are likely on something else (a soffit, a beam, a corner on the wrong level) - "
                           "check them, or give the real height (fit_height=false); the held fit is in "
                           "height_refit.held")
    else:
        info["warning"] = (head + ". The fitted one is kept: what is measured in meters fits the held one worse ("
                           + "; ".join(worse) + "), so the ceiling may really be that high - give the height if it is "
                           "known (fit_height=false, height=...); the held fit is in height_refit.held")
    info["short"] = (f"held at {held:.2f} m it fits worse: rms {was['rms_px']:.1f} -> {now['rms_px']:.1f} px"
                     + ("; " + "; ".join(worse) if worse else "") + ", so the fitted one is kept (height_refit)")
    return alt_req, alt, info


def _add_height_refit(r: dict, info: dict) -> None:
    """height_refit and its one warning into the result: when the fitted one is kept and height_suspect's hint is
    there, the refit is said in that hint instead of in a second warning."""
    text, short = info.pop("warning"), info.pop("short")
    r["height_refit"] = info
    hint = ((r.get("scale") or {}).get("height_suspect") or {}).get("hint")
    if info["adopted"] == "fitted" and hint is not None and hint in r["warnings"]:
        r["warnings"][r["warnings"].index(hint)] = hint + f" ({short})"
    else:
        r["warnings"].insert(0, text)


def _fit(req: FitRequest, compare: bool, solved: tuple | None = None) -> dict:
    """One fit and its report. `solved` (`_solved` of the same request's fit without the comparisons) skips the
    solving: the comparisons run on it."""
    if bad := [cid for cid in req.inside if cid not in req.photos]:
        return {"ok": False, "error": f"inside: no photo {', '.join(bad)}"}
    for oid in req.openings:
        if ":" in oid:
            return {"ok": False, "error": f"opening id '{oid}' must not contain ':'"}
    try:
        m = _Model(req)
        m.residuals(m.init)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    if solved is None:
        m, x, cost, JtJ = _fit_once(req, m)
        solved = (m, x, cost, JtJ)
    else:
        m, x, cost, JtJ = solved
    p = dict(zip(m.names, x))
    r, rows = m.residuals(x, detail=True)
    depth_rows = [row for row in rows if row[3] == "depth"]
    rows = [row for row in rows if row[3] != "depth"]
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
    size = {"width": W, "depth": D}
    unmeasured = [k for k in size if k in sigma and sigma[k] > UNMEASURED_SHARE * size[k]]

    warnings = []
    if unmeasured:  # first: the agent must not report such a size as measured
        warnings.append(
            "not measured: the room's " + " and ".join(f"{k} {size[k]:.2f} m (±{min(sigma[k], 99):.2f} m)"
                                                        for k in unmeasured) +
            " - the photos do not hold it (it stays near the start, size_guess or the last fit); report it as "
            "unmeasured, not as the room's size, and add landmarks that tie it (both of its walls in two or more "
            "photos, a shared point seen from far apart)")
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
                            f"the {other} end) in a photo that shows it")
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
        nobs = _n_obs(c)
        if nobs < MIN_OBS:
            warnings.append(f"photo {cid}: only {nobs} measurements; a camera has 6 unknowns - add points / lines")
        if out := _beyond(pos, W, D):
            through, solid, floor_out = _sight(m, p, cid, out[0])
            n_in = sum(through.values())
            # mostly through an opening: it stood there (a few pixels near the opening's edge cross the wall)
            # floor / ceiling pixels beyond the wall (and nothing through an opening): the room goes on there
            # too few measurements: the camera is free, its place says nothing of the walls
            verdict = ("loose" if nobs < MIN_OBS else
                       "doorway" if n_in and n_in >= 2 * solid else "wall" if solid or floor_out else "unknown")
            outside.append({"photo": cid, "wall": out[0], "beyond_m": round(out[1], 2),
                            "position": cams[cid]["position"], "through": through, "solid": solid,
                            "floor_beyond": floor_out, "measurements": nobs,
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
                    # one photo's east wall pixels moved the width 3.0 -> 3.85 m (truth 3.66 m)
                    warnings.append(f"the room's {dim}{sd_txt} rests on one photo for the {w} wall "
                                    f"({seen_by[w][0]}): a mislabeled landmark or an off depth there moves the whole "
                                    f"{dim} - add the {w} wall (a corner, a line or wall pixels) from another photo")
    for cid, at, e, behind in rows:
        if behind:
            warnings.append(f"photo {cid}: '{at}' is behind the camera - check the facing / landmark name")
    h_sd = sd("height", floor=1.0) if m.fit_H else None  # at least 1 px of noise: exact data still says if it is fixed
    loose = m.fit_H and _ceiling_seen(req) and (h_sd is None or h_sd > HEIGHT_LOOSE_M)
    tied = _floor_and_ceiling_tied(req) if loose else None
    if tied:
        # the shape is fixed and the room grows and shrinks as a whole
        ds = [c["depth_scale"] for c in cams.values() if "depth_scale" in c]
        spread = f" (its scale per photo came out {min(ds):.2f}-{max(ds):.2f})" if len(ds) > 1 else ""
        warnings.append(f"the room's scale is loose (height ±{h_sd or 0:.2f} m, width ±{sigma.get('width', 0):.2f} m)"
                        f": the floor and the ceiling are tied (photo {tied[0]} sees both), so the shape is fixed, "
                        "but the length comes only from the depth" + spread +
                        " - give the height if it is known (fit_height=false), or a shared point at a known "
                        "height (points: z)")
    elif loose:
        warnings.append(f"the wall height is not fixed by the photos (±{h_sd or 0:.2f} m), it stays near the guess "
                        f"{req.height} m: no photo sees both the floor and the ceiling, so the cameras' heights "
                        "and the ceiling move together - pick a photo that sees a "
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
    scale = _scale_evidence(m, req, p, cams, H)  # its warnings join below, after the size priors'
    on_eye, priors_reach = scale.pop("on_eye"), scale.pop("priors_reach")
    if not m.scale_seen:  # the typical sizes are named only when they reach the width or depth
        warnings.append(f"no landmark at the ceiling is seen: the room's scale comes from the camera height "
                        f"{req.eye_height} m (eye_height)"
                        + (" and the typical sizes (size_priors)" if priors_reach else "")
                        + (f"; the camera height alone holds the {' and '.join(on_eye)} (see below)" if on_eye else "")
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
    warnings += scale.pop("warnings")
    worst = sorted(rows, key=lambda t: -t[2])[:5]
    return {
        "ok": True,
        "rms_px": round(rms, 1),
        "_rms_but_one": round(math.sqrt(sum(e * e for e in sorted(errs)[:-1]) / max(1, len(errs) - 1)), 1),
        "room": {"width": round(W, 2), "depth": round(D, 2), "height": round(H, 2),
                 "height_fitted": m.fit_H, **({"height_suspect": True} if "height_suspect" in scale else {}),
                 **({"unmeasured": unmeasured} if unmeasured else {}),
                 "openings": openings},
        "sigma_m": sigma,
        "cameras": cams,
        "points": points,
        "located": located,
        **({"objects": objects} if objects else {}),
        "worst": [{"photo": c, "landmark": a, "err_px": round(e, 1)} for c, a, e, _ in worst],
        "measurements": len(r), "unknowns": len(x),
        **({"size_priors": size_priors} if size_priors else {}),
        **({"scale": scale} if scale["sources"] else {}),
        **({"outside": outside} if outside else {}),
        **({"suspects": suspects} if suspects else {}),
        "warnings": warnings,
        **({} if compare else {"_solved": solved}),  # fit() solves without the comparisons first, then reuses it
    }


def _holds_camera(c: PhotoObs) -> bool:
    """A photo with enough measurements to hold its camera (MIN_OBS), or with a depth: thinner ones can sit
    anywhere and neither measure the room nor carry a scale."""
    return _n_obs(c) >= MIN_OBS or _has_depth(c)


def _cameras_held(req: FitRequest) -> set[str]:
    """The photos whose camera is held (_holds_camera). One rule for whether the scale is seen
    (_Model._scale_seen) and where it reaches (_scale_reach)."""
    return {cid for cid, c in req.photos.items() if _holds_camera(c)}


DIM_WALLS = {"width": ("west", "east"), "depth": ("south", "north")}


def _scale_reach(m: _Model, req: FitRequest
                 ) -> tuple[dict[str, set[str]], dict[str, tuple[list[str], bool]], dict[str, set[str]]]:
    """Which photos each source of the scale holds, and which photos measure the width / depth.

    Without a ceiling at a known height every photo can carry its own scale (the camera height trades off with
    the distances), so a source holds only the photos that see it - a ceiling corner / line (the height given),
    a shared point with a fixed z, a size prior (a door's top, a typical height), a depth, a fitted ceiling (its
    height held near the guess: without a depth that holds the scale, not the eye height) - and, to a fixed
    point, every photo that shares something whose size the held ones fix: an opening, a shared point, the
    ceiling (when its height is fitted), the width or depth (measured by both photos). A room corner or a floor
    line seen by both is not such a link: it fixes where each camera is, not its scale (a fit: a window's top in
    photo w set the depth, the width of photo s, which shares the north-east corner with w, stayed at the eye
    height's). A dimension's photos are those whose camera is held (_cameras_held) and that see both of its
    walls; when none does, those that see one of them (`one_wall` True), and it is held only when the held ones
    among them see both walls.
    Returns ({source: held photos}, {dimension: (its photos, one_wall)}, {photo: its walls})."""
    held_cams = _cameras_held(req)
    photos = {cid: c for cid, c in req.photos.items() if cid in held_cams}
    walls = {cid: _walls_of(req, c) for cid, c in photos.items()}
    dims: dict[str, tuple[list[str], bool]] = {}
    for d, pair in DIM_WALLS.items():
        both = [cid for cid, w in walls.items() if set(pair) <= w]
        dims[d] = (both, False) if both else ([cid for cid, w in walls.items() if w & set(pair)], True)
    links: dict[str, set[str]] = {}  # what a held photo sizes -> the photos that share it
    for cid, c in photos.items():
        objs = {_landmark_object(req, n) for n in [o.at for o in c.points] + [o.along for o in c.lines]}
        sh = {o for o in objs if o in req.openings or o in req.points}
        if m.fit_H and _sees(c, "ceiling"):
            sh.add("ceiling")
        sh |= {"dim:" + d for d, (ph, one) in dims.items() if not one and cid in ph}
        for t in sh:
            links.setdefault(t, set()).add(cid)
    by_photo: dict[str, set[str]] = {cid: set() for cid in photos}
    for t, ph in links.items():
        for cid in ph:
            by_photo[cid].add(t)
    seeds: dict[str, set[str]] = {}
    for cid, c in photos.items():
        if _has_depth(c):
            seeds.setdefault("depth", set()).add(cid)
        if _sees(c, "ceiling"):  # the height given; a fitted one is held near the guess (HEIGHT_PX_PER_M)
            seeds.setdefault("height (guess)" if m.fit_H else "ceiling", set()).add(cid)
        if any((sp := req.points.get(o.at)) is not None and (sp.z or 0) > 0 and not sp.z_sigma for o in c.points):
            seeds.setdefault("point", set()).add(cid)
    for u, seen in _prior_photos(req).items():
        if seen & photos.keys():
            seeds[u] = seen & photos.keys()

    def spread(start: set[str]) -> set[str]:
        held, todo, done = set(start), list(start), set()
        while todo:
            for t in by_photo[todo.pop()] - done:
                done.add(t)
                for o in links[t] - held:
                    held.add(o)
                    todo.append(o)
        return held

    return {k: spread(v) for k, v in seeds.items()}, dims, walls


def _dims_on_eye(m: _Model, req: FitRequest):
    """({source: held photos}, {source: the dimensions it reaches}, {dimension: (its photos, one_wall)} for the
    measured dimensions no source reaches: those rest on the camera height, only when something of known height
    is seen; else the general warning says it), whether any dimension is measured. A dimension measured only by
    photos on one of its walls is reached when the held ones among them see both walls."""
    metric, dim_photos, walls = _scale_reach(m, req)
    dims = {d: v for d, v in dim_photos.items() if v[0]}

    def reaches(held: set[str], d: str) -> bool:
        ph, one = dims[d]
        if not one:
            return bool(held & set(ph))
        seen = set().union(*(walls[cid] for cid in held & set(ph)))
        return set(DIM_WALLS[d]) <= seen

    reach = {k: [d for d in dims if reaches(held, d)] for k, held in metric.items()}
    on_eye = {d: v for d, v in dims.items() if not any(d in r for r in reach.values())} if metric else {}
    return metric, reach, on_eye, bool(dims)


def _median(v: list[float]) -> float:
    s = sorted(v)
    return s[len(s) // 2] if len(s) % 2 else (s[len(s) // 2 - 1] + s[len(s) // 2]) / 2


def _scale_evidence(m: _Model, req: FitRequest, p, cams: dict, H: float) -> dict[str, Any]:
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

    # which sources reach the photos that measure the width / depth; a dimension none reaches rests on the
    # camera height (each photo's own, near eye_height), whatever else is seen elsewhere
    metric, reach, on_eye, measured = _dims_on_eye(m, req)
    priors_reach = False  # a door top / typical height reaches the room (not only its own photos)
    ds = [c["depth_scale"] for c in cams.values() if "depth_scale" in c]
    if ds:
        add("depth", _median(ds), max(DEPTH_ERR_FLOOR, SCALE_ERR["depth"] / math.sqrt(len(ds))), n=len(ds),
            spread=[round(min(ds), 3), round(max(ds), 3)], used=True)
    for u, v, sg in m.size_priors:
        if p[u] > 0.05:
            # used: it holds the fit (the outlier check below weighs it); reaches: the measured width / depth it
            # sets; nothing measures the width or depth: a prior in a held photo holds it as before
            r = reach.get(u)
            ok = bool(r) or (not measured and u in metric)
            priors_reach |= ok
            more = {"reaches": r} if r else {} if ok else {"holds": "only the photos it is in"}
            add("door top" if u.endswith(".top") else "typical height", v / p[u], sg / v, of=u, used=True, **more)
    zs = [p[f"{cid}.z"] for cid in req.photos]
    mean_z = sum(zs) / len(zs)
    if mean_z > 0.05:  # always listed; used (weighed for the outlier) only when nothing better is seen: handheld
        # cameras are anywhere from 1.0 to 1.6 m. `sets` = the dimensions it alone holds, whether used or not
        add("eye height", req.eye_height / mean_z, SCALE_ERR["eye height"], camera_height_m=round(mean_z, 2),
            used=bool(not m.scale_seen or m.depth_only), **({"sets": sorted(on_eye)} if on_eye else {}))
    if _ceiling_seen(req):
        if m.fit_H:
            lo, hi = CEILING_TYPICAL_M
            src.append({"by": "ceiling (typical)", "scale": round(min(max(1.0, lo / H), hi / H), 3),
                        "range": [round(lo / H, 3), round(hi / H, 3)], "height_m": round(H, 2), "used": False})
        else:
            src.append({"by": "height (given)", "scale": 1.0, "range": [1.0, 1.0], "height_m": round(H, 2), "used": True})
    out: dict[str, Any] = {"sources": src, "warnings": [], "on_eye": sorted(on_eye), "priors_reach": priors_reach}
    for d, (ph, one) in on_eye.items():  # something of known height is seen, but not by the photos measuring these
        far = " and ".join(DIM_WALLS[d]) + " walls"
        if one:
            who = f"only photos that see one of its walls measure it ({', '.join(ph)})"
        else:
            who = (f"photos {', '.join(ph)} measure it but see" if len(ph) > 1 else
                   f"photo {ph[0]} measures it but sees") + " nothing of known height"
        out["warnings"].append(
            f"the room's {d} rests on the camera height (eye_height {req.eye_height} m): {who} - give "
            f"eye_height if known, or mark a door's top / a ceiling corner in a photo that also sees the {far}")
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
                       "ceiling (typical)": "the fitted wall height is suspect (see the height warning)"}[s["by"]]
                out["warnings"].append(
                    f"scale: {s['by']} asks for a room {s['scale']:.2f}x the fit while the others agree ("
                    + ", ".join(f"{o['by']} {o['scale']:.2f}" for o in others) + f") - {why}")
                if s["by"] == "ceiling (typical)":
                    out["height_suspect"] = _height_suspect(req, H)
                    out["warnings"].append(out["height_suspect"]["hint"])
                break
    return out


def _height_suspect(req: FitRequest, H: float) -> dict[str, Any]:
    """The fitted wall height when the typical wall height is the one source apart and the others agree on the
    room's size: the floor plan's scale holds, the height alone came out wrong - the ceiling landmarks are most
    likely misread (a soffit, a beam, a lower or raised part of the ceiling, a corner put on the wrong level).
    fit_photos(apply) keeps the scene's height; the photos that saw the ceiling are listed to check."""
    lo, hi = CEILING_TYPICAL_M
    photos = [cid for cid, c in req.photos.items() if _sees(c, "ceiling")]
    guess = lo if H < lo else hi
    return {"height_m": round(H, 2), "typical": [lo, hi], "photos": photos,
            "hint": (f"height: the fitted wall height {H:.2f} m is suspect - the room's size agrees without it, so the "
                     f"ceiling's landmarks are likely misread (a soffit, a beam, a lower part of the ceiling, a corner on "
                     f"the wrong level) in {', '.join(photos)}. Check them, or fit again with fit_height=false and "
                     f"height = the real height if known (else about {guess} m); apply keeps the scene's height. A ceiling "
                     f"that really is {H:.2f} m (a hall, a shop, a factory) is not told apart from the photos: then "
                     f"set_room(height={H:.2f})")}


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


def _fit_once(req: FitRequest, m: _Model):
    """Levenberg-Marquardt from a few starts (pitches); the best of them."""
    n_obs = sum(2 * len(c.points) + len(c.lines) for c in req.photos.values())
    starts = []
    for pitch in (8.0, 0.0, 20.0):  # a few starts; keep the best
        starts.append([pitch if n.endswith(".pitch") else v for n, v in zip(m.names, m.init)])
    best = None
    for x0 in starts:
        x, cost, JtJ = _lm(m.residuals, x0, m.scale)
        if best is None or cost < best[1]:
            best = (x, cost, JtJ)
        if math.sqrt(cost / max(1, n_obs)) < 3:
            break
    return m, *best
