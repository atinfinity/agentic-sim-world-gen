"""MCP server exposing agentic-sim-world-gen to Claude Code (stdio).

    claude mcp add agentic-sim-world-gen -- /path/to/repo/.venv/bin/agentic-sim-world-gen-mcp

Tools mirror agentic_sim_world_gen.service. Scenes are directories; pass the directory path
returned by create_scene to every other tool.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError
from pydantic import ValidationError

from agentic_sim_world_gen import mcp_schema, service
from agentic_sim_world_gen.photo_fit import FitRequest
from agentic_sim_world_gen.scene.fixtures import CeilingFeature, Fixtures
from agentic_sim_world_gen.scene.columns import Column, FloorArea
from agentic_sim_world_gen.scene.levels import Level, Stairs
from agentic_sim_world_gen.scene.looks import Surface
from agentic_sim_world_gen.scene.plan import Opening
from agentic_sim_world_gen.scene.site import Area, Building, Site
from agentic_sim_world_gen.scene.walls import Baseboard, Wainscot
from agentic_sim_world_gen.scene.spec import Array, Assumption, Fill, InputRef, Layout, LayoutOption, ObjectSpec, Requirement, SceneSpec, SdgSpec

INSTRUCTIONS = """\
Build Isaac Sim worlds (USD) from furniture/props chosen from SimReady Central, the Isaac Sim
bundled assets (source isaac-6.1) and the user's own USD folders (source local:<folder>, if configured).

Conventions: Z-up, meters. Room interior spans x in [0, width], y in [0, depth]; floor top z = 0.
position = footprint center (x, y). yaw_deg rotates about +Z (counter-clockwise seen from above).
Walls: south y=0, north y=depth, west x=0, east x=width.
Doors / windows: room.openings [{kind: door|window|opening, wall, offset (center, from the wall
start: west end of south/north, south end of west/east), width, height, sill}]. Space in front of
doors is kept free (issue blocks_door otherwise). style "auto_sliding" (shop entrances) adds two
glass panels that slide open in Isaac Sim playback while anything with a collider is within `sensor`
(default 1.0 m) of the doorway; leave that space free too. A door asset that fills an opening:
object {"opening": "<opening id>"} instead of position/layout (centered in the wall, front into the room).
Floor plans: import_floor_plan(path) reads .json / .dxf / .ifc (an image or PDF: trace it into floor
plan JSON first, see the world-gen skill) and returns wall ids, faces and zones (rooms). Then use
room {"plan_file": path, floor/walls/lighting...} in create_scene, or set_room(plan_file=...).
With a plan, against_wall takes a compass side or a wall id, ObjectSpec.zone picks the room
(zone id), center means the zone center, and room.layout in the status lists the ids.
Prefer `layout` (relations) over raw coordinates: against_wall (+along 0..1), in_front_of (chair at
desk: placed just in front; a negative gap tucks it under, only if the knee space is open), next_to (+side), on (lamp on desk; +on_height = surface above the support's bottom, +offset = (right, front) from its
center as seen facing its front, +facing to turn it), facing (object id or east/west/north/south), center. The solver
fills position/yaw_deg/z on every change (e.g. after select_asset changes the size).
move_object = manual override (clears layout). Asset fronts are +X of the asset (checked in Isaac Sim
6.1; a few known exceptions are built in). If one still faces the wrong way, call set_layout with
front_offset_deg (180 = backwards, 90 / -90 = sideways).
Repeated objects (rack rows with aisles, rows of desks): one object with `array` {count, rows,
spacing, aisle, alternate} (or set_array). The block is placed by its layout like one big object;
alternate=true makes rows face each other across aisles and stand back to back otherwise.
back_to_back=true starts with a back-to-back pair (store gondola islands, double-sided shelves).
Units get ids <id>.r<row>c<col> in issues.
Stocked shelves (products on a gondola, drinks in a cooler): one object per product with `fill`
{on: fixture id, levels (0 = lowest board), span (part of the board width, e.g. [0, 0.5]), rows,
units} (or set_fill). Shelf boards are found from the fixture's geometry; products stand in rows
from the front edge, facing like the fixture; levels too low for the product are skipped (issue
shelf_fit). Mix products by giving each its own levels / span / units. Keep counts sane (a full
gondola island is hundreds of rigid bodies): use rows 1-2 and a few levels.
Hanging things (ceiling signage, pendant lamps): layout.ceiling = true puts the top at the room
height; x-y comes from against_wall / center / position as usual.
Room look: room.floor / room.walls / room.ceiling (surface presets, or color/texture/MDL; ceiling true =
its default ceiling, never the wall surface; walls {"*": ..., "N": ...} for an accent wall),
room.wainscot (腰壁) / room.baseboard (巾木), room.zone_floors {plan zone id: floor} (default guessed from the
zone name: 和室 -> tatami, 洗面 / 浴室 / トイレ / 玄関 -> tile_white), room.lighting preset
(studio default, daylight, home, office, warehouse, night) and lighting_scale; set_room changes them
(and the room size) later. Pick presets that fit the place: home -> wood_light/tatami + home,
office -> carpet_gray + office, warehouse -> epoxy_gray/concrete + metal_panel + warehouse.
Outdoor (yards, streets, parking lots): room {"outdoor": true, width, depth = the site, areas (road,
sidewalk, grass ... as zones), buildings (closed blocks)}; lighting sun / overcast / dusk / street_night.
Areas are zones: zone=<area id> + against_wall "north" etc. uses that area's edges; against_wall
"<building id>:south" stands against a building. Roads keep clear unless the object's zone is the road.
The overlap check is 3D (stacked objects are fine; nothing overlaps what it rests on through layout.on / fill.on)
and allows a chair partly under a desk/table.
min_size / max_size = (footprint long side, short side, height) in meters; orientation independent.
Search queries must be English (translate the user's Japanese). Put a short noun phrase in `query`
and 1-3 English synonyms in `alt_queries` (e.g. "bookshelf" + ["bookcase", "shelving unit"]).
Candidates are re-ranked by how well class/tags/caption match; each has match.score and
match.weak (true = probably not the requested thing). Use view_candidates to look at thumbnails
before trusting the top pick for anything important or weak. Results list each object's selected `asset`
and the other candidates in one line each (`others`); get_scene with object_id gives one object in full. Weak hits are dropped when a good match
exists, so a short list is normal. If every candidate is weak, the object shows `substitute` (the
catalog has no such object; report the substitute to the user). substitute.not_in_catalog = the kind table says
no source has this kind at all: do not search again, ask the user once (substitute / own USD via local: / drop it;
kind_availability answers the same before create_scene). It may also list size_misses:
good matches that min_size/max_size dropped. Widen the limits with research_object when one would
do rather than settling for a substitute (size bounds are loose ranges).
Photos: look at the photo (Read), estimate the room and the objects from it (see the world-gen skill),
and record it in spec.inputs [{id, kind: photo, path, url, title, author, license, camera}] - the
source is required (sidecar <stem>.source.json fills url/title/author/license). camera = where the
photo was taken {position, look_at, hfov_deg}; compare_photo renders that view next to the photo.
Reference images (「こんな感じ」, a moodboard, a web photo of another room): only the taste is taken, not the
room. Record it as an input with kind "reference" (no camera; the source is required as for photos) and
`taste` {floor, walls, ceiling, lighting, light_kelvin, lighting_scale, furniture {kind: color/material},
style, density, note} read off the image, then apply it with set_room and the queries (see the skill).

Videos (a walk-through of the room): extract_frames picks 4-8 sharp frames where the view changed and records
them as photo inputs (each with the video's source and its time); start_s/end_s limit it to one room.
Look at every frame first: if anyone is visible, stop and do not use the video (no detector is guaranteed).
Then treat them as several photos of one room: video_poses (if OpenCV is installed) measures how the camera
turned between the frames - pass its `relative` to fit_photos - then fit_photos for the room and every frame's
camera (the first frame sets the orientation), one object per real object even if several frames show it,
compare_photo.

Synthetic data: generate_data runs Replicator on the scene (Isaac Sim): random camera poses in free floor
space aimed at objects, light intensity / color temperature (optionally floor/wall colors), and writes
RGB, 2D boxes, segmentation, depth (BasicWriter; COCO / KITTI on request). Labels are the objects'
semantic_class (default: the asset's class); set ObjectSpec.semantic_class for your own class names.
Try dry_run first to see the plan; check the returned contact sheet before generating many frames.

Typical flow: plan_questions (ask the user the 2-3 items it returns, unless they said not to ask) ->
create_scene -> open_preview (once per scene: a page next to the terminal that follows
every step: candidates, layout, issues, the Isaac Sim check, licenses) -> check_taste (an asset that clashes
with the rest: offer its alternatives, never swap silently) -> tell the user the chosen
assets and stop to ask the user (unless they said not to ask) -> select_asset / research_object /
move_object until issues == [] and the user agrees -> compose_scene ->
check_in_isaac (Ubuntu with Isaac Sim only) -> fix and repeat -> license_report.
If no candidate truly matches, pick the closest and tell the user it is a substitute.
"""

# Names the agent reaches for instead of the parameter's own: tool -> {wrong name: right name}.
ALIASES = {
    "add_objects": {"objects": "objs"},
    "set_assumptions": {"items": "assumptions"},
    "kind_availability": {"kinds": "queries"},
}
# Errors raised on purpose in agentic-sim-world-gen with a message for the caller; the SDK would hide them as crashes.
EXPECTED = (ValueError, LookupError, FileNotFoundError, NotADirectoryError, TypeError)
UNEXPECTED_CHARS = 300  # the end of the last line of any other exception's text (pxr puts the reason there)
ERROR_LOG = "errors.log"  # in AGENTIC_SIM_WORLD_GEN_CACHE: the tracebacks of those
ERROR_LOG_MAX = 1 << 20  # bytes; started again when larger


def log_unexpected(name: str, exc: BaseException) -> str | None:
    """Append the traceback of an exception no tool should raise to AGENTIC_SIM_WORLD_GEN_CACHE/errors.log; the path, or None
    when it cannot be written (the tool's error is what matters then)."""
    import time
    import traceback

    try:
        p = Path(os.environ.get("AGENTIC_SIM_WORLD_GEN_CACHE", "~/.cache/agentic-sim-world-gen")).expanduser() / ERROR_LOG
        p.parent.mkdir(parents=True, exist_ok=True)
        text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        with open(p, "w" if p.exists() and p.stat().st_size > ERROR_LOG_MAX else "a", encoding="utf-8") as f:
            f.write(f"--- {time.strftime('%Y-%m-%dT%H:%M:%S')} pid {os.getpid()} tool {name}\n{text}\n")
        return str(p)
    except OSError:
        return None


def _row_key(r: dict[str, Any]) -> str:
    return f"{r['object']}:{r['item']}" if r.get("object") else r["item"]


def fix_arguments(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Undo the argument slips seen in the trials before the SDK validates them: a known wrong name,
    set_array's / set_fill's fields given at the top level instead of inside `array` / `fill`, set_assumptions'
    remove given as rows ({item, object}) instead of "item" / "object:item", a string sent JSON-quoted twice
    ('"#4a2418"'). Anything else is left to the schema check."""
    args = dict(arguments)
    for wrong, right in ALIASES.get(name, {}).items():
        if wrong in args and right not in args:
            args[right] = args.pop(wrong)
    for tool, key, model in (("set_array", "array", Array), ("set_fill", "fill", Fill)):
        if name == tool and key not in args:
            loose = {k: args.pop(k) for k in list(args) if k in model.model_fields}
            if loose:
                args[key] = loose
    if name == "set_assumptions" and isinstance(args.get("remove"), list):  # rows given as {item, object}
        args["remove"] = [_row_key(r) if isinstance(r, dict) and r.get("item") else r for r in args["remove"]]
    for k, v in args.items():
        if isinstance(v, str) and len(v) >= 2 and v[0] == v[-1] == '"':
            try:
                inner = json.loads(v)
            except ValueError:
                continue
            if isinstance(inner, str):
                args[k] = inner
    if isinstance(args.get("queries"), str) and not args["queries"].lstrip().startswith("["):
        args["queries"] = [args["queries"]]
    return args


class AgenticSimWorldGenServer(MCPServer):
    """MCPServer that forgives the common argument slips and passes agentic-sim-world-gen's own error messages to the model
    (the SDK reports any exception other than ToolError as a bare "Error executing tool <name>")."""

    async def call_tool(self, name: str, arguments: dict[str, Any], context: Any = None) -> Any:
        try:
            return await super().call_tool(name, fix_arguments(name, arguments or {}), context)
        except UnexpectedToolError as exc:
            cause = exc.__cause__
            if isinstance(cause, EXPECTED):
                text = cause.args[0] if isinstance(cause, KeyError) and cause.args else str(cause)
                raise ToolError(f"Error executing tool {name}: {type(cause).__name__}: {text}") from cause
            if cause is not None:  # not one of ours (a pxr error, a bug): say what, and keep the traceback
                first = (str(cause).strip().splitlines() or [""])[-1].strip()
                if len(first) > UNEXPECTED_CHARS:  # pxr: "Error in '<function>' at line N in file <path> : '<reason>'"
                    first = "…" + first[-UNEXPECTED_CHARS:]
                where = log_unexpected(name, cause)
                raise ToolError(f"Error executing tool {name}: unexpected {type(cause).__name__}: {first}"
                                + (f" (traceback in {where})" if where else "")) from cause
            raise
        except ToolError as exc:
            if isinstance(exc.__cause__, ValidationError):
                tool = self._tool_manager.get_tool(name)
                if tool is not None:
                    props = tool.parameters.get("properties", {})
                    need = set(tool.parameters.get("required", []))
                    sig = ", ".join(k if k in need else f"{k}?" for k in props)
                    bad = [str(e["loc"][0]) for e in exc.__cause__.errors() if e.get("loc")]
                    types = mcp_schema.argument_help(tool, bad)  # the right shape, without reading tools.md
                    said = "\n".join(ln for ln in str(exc).splitlines() if "errors.pydantic.dev" not in ln)
                    raise ToolError(f"{said}\n{name} takes: {sig}" + (f"\n{types}" if types else "")
                                    + f'\n(notes and examples: tool_help("{name}"))') from exc.__cause__
            raise


mcp = AgenticSimWorldGenServer(name="agentic-sim-world-gen", instructions=INSTRUCTIONS)
Size = tuple[float, float, float]


def _scene_dir(name: str) -> str:
    return os.path.join(os.environ.get("AGENTIC_SIM_WORLD_GEN_OUT", os.path.expanduser("~/agentic_sim_world_gen_scenes")), name)


@mcp.tool()
def search_assets(
    query: str,
    limit: int = 8,
    min_size: Size | None = None,
    max_size: Size | None = None,
    alt_queries: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Search the asset sources (SimReady Central, Isaac Sim bundled assets, local USD folders). query/alt_queries must be English. Returns name, caption,
    size_m (w, d, h), license, thumbnail_url and match (re-rank score)."""
    return service.search_assets(query, limit, min_size, max_size, alt_queries)


@mcp.tool()
def kind_availability(queries: list[str]) -> dict[str, Any]:
    """Does the catalog have this kind of object at all? queries = English kinds (or one object's query +
    alt_queries). Returns per query {kind, counts per source, absent, other_kinds}. Use it before create_scene for
    unusual objects or when a search found only substitutes: absent = do not search again; ask the user:
    substitute / their own USD (local:) / drop it.

    Does the catalog have this kind of object at all? queries = English kinds ("floor lamp", or one object's
    query + alt_queries). Returns per query {kind, counts {source: assets that match it strongly}, absent,
    other_kinds (same head noun, e.g. desk lamp for floor lamp), table {built}}: SimReady Central from the shipped
    kind table (its whole catalog on the build date), the Isaac index and local: folders scored in full. Use it
    before create_scene for unusual objects, or when a search found only substitutes: absent = do not search
    again; ask the user: substitute / their own USD (local:) / drop it."""
    return {q: service.kind_availability([q]) for q in queries}


@mcp.tool()
def plan_questions(request: str, place: str | None = None, known: list[str] | None = None,
                   limit: int = 3, ask_first: bool = False, purpose: str | None = None) -> dict[str, Any]:
    """Skill step 1, before create_scene: the few questions that change the result most for this place, minus what
    the request already says (pass it as written; known = items you already have). Default: do not ask yet - build
    the draft with `defaults`, record each as a guess (set_assumptions, basis '叩き台の既定', options) and ask while
    showing it; ask only `ask_now` first. ask_first=true asks first. Includes the purpose when unsaid.
    `ask_photo` (a vague request): add its `say` to your first reply once, then pass known=['reference'].
    Home / office / school / lab: the activity (who, how many, doing what) stands for the size and the
    number of people - `activity` when the request says it (room, objects, assumptions), else the first question;
    derive_activity turns the answer into the room and the main furniture.
    Order: impact (`rework`: how much is rebuilt when the default was wrong) x uncertainty, biggest first.
    `later` (colors, small things: fixed on the preview afterwards) and `preferred` (answered by the user's
    earlier scenes) are never asked: record `later` defaults as guesses, leave `preferred` fields out.

    Before create_scene (skill step 1): the items that change the result most for this kind of place
    (size, entrance, register, people, desk layout, rack rows...), minus what the user's words already say.
    Pass the user's request as written (Japanese is fine); place is guessed from it when left out; known =
    keys/items you already have from earlier turns. Draft first: do not ask the returned questions yet;
    build with their `defaults`, record each as a guess (set_assumptions, basis '叩き台の既定', options) and ask
    while showing the draft. `ask_now` (the place, when the request names none) is the only thing to ask before.
    ask_first=true (the user wants to be asked first): ask the questions in one message, then
    set_assumptions(given=true). The questions include the purpose when the request does not say what the
    scene is for (`purpose` is set when it does); pass it to create_scene (purpose=..., purpose_given=true when the
    user said it; left out = demo as a guess). Returns no questions when the user said おまかせ / 確認は不要 (`no_questions`, `defaults`).
    Two more, each only when its condition holds and asked before the draft in `ask_now`: the robot's size
    (the purpose is amr / manipulation and the request names no machine or size; options with their `radius`;
    `robot.radius` when the request says it; `after_purpose` = ask it together with the purpose's answer when that
    is amr / manipulation, or call again with purpose=<the answer>) -> create_scene / set_purpose robot_radius;
    and whether there is a plan / photo / video (`existing_place`: the request is about a place that exists, e.g.
    うちの / 実際の / この店, and comes with none) -> with one, read it first (set_input) instead of drafting from words."""
    from agentic_sim_world_gen.scene.questions import plan_questions as plan

    return plan(request, place, known, limit, ask_first, purpose)


@mcp.tool()
def derive_activity(activity: str, place: str | None = None, people: int | None = None) -> dict[str, Any]:
    """The room size, the main furniture and their counts from what happens in the place: the answer to
    plan_questions' activity question (「誰が・何人で・何をする場所ですか？」: an option or free words such as
    「6人で会議、ホワイトボードも」). Places: home, office, school, lab. Returns `room` {width, depth}, `objects`
    [{label, query, count}] and `assumptions` to record with set_assumptions as they are (basis 「活動から」:
    guesses the user can correct). What the user said about size or furniture wins over these."""
    from agentic_sim_world_gen.scene import activity as act
    from agentic_sim_world_gen.scene.questions import guess_place

    place = place or guess_place(activity)
    got = act.derive(place, activity, people)
    if got is None:
        names = [a.option() for a in act.for_place(place)]
        return {"error": (f"no activity of {place!r} in {activity!r}; one of: " + " / ".join(names)) if names else
                f"the place {place!r} has no activities (home, office, school, lab): use its plan_questions"}
    return {"place": place, **got}


@mcp.tool()
def create_scene(spec: SceneSpec, out_dir: str | None = None, candidates: int = 6, preferences: bool = True,
                 purpose: str | None = None, purpose_given: bool = False,
                 skip: list[Literal["purpose", "windows", "preview", "draft"]] | None = None,
                 drafts: Literal["auto", "size", "amount", "composition"] | None = None,
                 robot_radius: float | None = None) -> dict[str, Any]:
    """Create a scene from a SceneSpec and bring it to a first draft in this one call: candidates for every object
    (top one selected), the purpose (yours, else demo as a guess), windows in the window openings, the preview page
    opened, and - when the layout has no issue - scene.usda and a look in Isaac Sim without physics. Returns the
    full status plus `started` (what each step did). Leave out_dir unset; use the returned scene_dir from then on.

    Create a scene from a SceneSpec: search candidates for every object (top one selected),
    write scene.resolved.json + previews, and return the scene status (objects, candidates, layout issues).
    Leave out_dir unset unless the user names a folder: the server puts the scene in its output root
    (AGENTIC_SIM_WORLD_GEN_OUT) under <spec.name>; the returned scene_dir is the path to use from then on.
    preferences (default true): the user's remembered preferences (get_preferences `defaults`) fill what the spec
    leaves out (room lighting / lighting_scale / floor / walls of the same place, in_front_of gap and array
    aisle / spacing per kind, the asset per query); each one is recorded as an assumption with basis 前回の好み
    and listed in `preferences_applied`. Leave those fields out of the spec to use them.
    The steps that always followed are done here, so do not call them again: set_purpose (purpose = demo /
    amr / manipulation / sdg, purpose_given=true when the user said it; left out = demo recorded as a guess;
    robot_radius = the robot's size when the user said it, else 0.3 m as a guess),
    fill_windows, open_preview, and for the first draft compose_scene + check_in_isaac(draft=true). `started`
    reports each: draft.skipped says why there is no draft yet (layout issues: fix them, then finish(draft=true);
    no Isaac Sim: the 2D preview is the draft). skip=["draft"] when the user wants no stop for a draft (おまかせ、
    仕上げまで): finish() does the full check anyway.
    `conflicts` when what was asked cannot all hold with the chosen assets (the room too small for its
    fixtures, a robot wider than the aisles, fewer seats than people, a fixture taller than the ceiling): no draft
    is made; ask its one question (`ask`), or when the user left it to you apply each `left_to_us` (set_room /
    set_array / add_objects) and say what you changed. It leaves the status once the scene holds. The room, each object's asset, size, count and position are
    recorded as guesses by the server (status assumptions_auto); record only what the user said (set_assumptions).
    drafts: when the user did not say how big / how full (plan_questions `drafts`), "size" (or "auto" = the
    first undecided axis) makes three plans that differ in it instead of the 3D draft: `started.drafts`, status
    `layout_options`. Ask which is closest, choose_layout, then finish(draft=true). See propose_drafts."""
    st = service.start_scene(spec, out_dir or _scene_dir(spec.name), candidates, preferences, purpose, purpose_given,
                             list(skip or []), drafts=drafts, robot_radius=robot_radius)
    return service.reply(st, full=True) | {"started": st["started"]}


@mcp.tool()
def show_gallery(request: str = "", place: str | None = None) -> dict[str, Any]:
    """The examples and the user's past scenes as a page of top views to pick a start from. Use it when the
    request is vague, and first of all when plan_questions returns `gallery` (the place has no question table of
    its own). Returns `url` (show it), `near` (the nearest entries: id, title, place, size, objects, fixtures,
    why near) and `picked` (what the user clicked on the page). Then start_from_example."""
    return service.show_gallery(request, place)


@mcp.tool()
def start_from_example(example: str | None = None, name: str | None = None, room: dict[str, Any] | None = None,
                       drop: list[str] | None = None, purpose: str | None = None, purpose_given: bool = False,
                       skip: list[Literal["purpose", "windows", "preview", "draft"]] | None = None) -> dict[str, Any]:
    """Start a scene from a gallery entry instead of writing a SceneSpec: create_scene with the entry's spec
    under a new name. example: an id from show_gallery (`example:bakery`, `scene:<folder>`); left out = what the
    user picked on the page. room: fields to override (width, depth, height, place, name, floor, walls, lighting);
    drop: object ids to leave out (what stands on them goes too). What came from the example (size, place, floor,
    walls, lighting, fixtures) is recorded as guesses with the basis 作例「…」から: say them, and record what the
    user said with set_assumptions(given=true). `started.from` lists them. The rest is as create_scene."""
    st = service.start_from_example(example, name, room, drop, purpose=purpose, purpose_given=purpose_given,
                                    skip=list(skip or []))
    return service.reply(st, full=True) | {"started": st["started"]}


@mcp.tool()
def get_preferences(place: str | None = None) -> dict[str, Any]:
    """The user's remembered preferences (from their corrections in earlier scenes): `defaults` (2+ scenes
    agree: create_scene applies them) and `pending` (seen once). Each: item, target ("place:<place>" / "room" for
    room items, the kind "chair" for gap / aisle / spacing, the query for asset), value, count, last_used.
    Read before create_scene (skill step 2). place: only the room items for that place."""
    return service.get_preferences(place)


@mcp.tool()
def forget_preference(item: str | None = None, target: str | None = None) -> dict[str, Any]:
    """Forget a remembered preference when the user says so (「照明の好みは忘れて」): item (lighting,
    lighting_scale, floor, walls, gap, aisle, spacing, asset), optionally one target; item "all" forgets everything."""
    return service.forget_preference(item, target)


@mcp.tool()
def get_scene(scene_dir: str, object_id: str | None = None) -> dict[str, Any]:
    """Current status of a scene: objects, selection, layout issues, preview/USD paths. Each object shows its
    selected asset and the other candidates in one line each; object_id shows that object's candidates in full."""
    return service.reply(service.status(scene_dir), object_id, full=True)


@mcp.tool(structured_output=False)  # returns text + images, not JSON
def view_candidates(scene_dir: str, object_id: str | list[str] | None = None) -> list[Any]:
    """Show candidate thumbnails so you can judge them by eye, then select_asset. One id: every candidate of that
    object with its own image. A list of ids, or nothing (= the objects whose asset is a substitute, a weak match
    or off size): one contact sheet per object, candidates left to right with the index in the corner and the
    selected one framed - look at several objects in one call."""
    out: list[Any] = []
    if isinstance(object_id, str):
        for short, img in service.candidate_thumbnails(scene_dir, object_id):
            out.append(json.dumps(short, ensure_ascii=False))
            if img:
                fmt = "jpeg" if img[:3] == b"\xff\xd8\xff" else "png"
                out.append(Image(data=img, format=fmt))
            else:
                out.append("(no thumbnail)")
        return out
    for info, sheet in service.candidate_sheets(scene_dir, object_id):
        out.append(json.dumps(info, ensure_ascii=False))
        if sheet:
            out.append(Image(data=sheet, format="jpeg"))
        elif info.get("candidates"):
            out.append("(no thumbnails)")
    return out


@mcp.tool(structured_output=False)  # returns text + images, not JSON
def view_pictures(scene_dir: str) -> list[Any]:
    """Show the printed pictures and screen images the scene's assets bring with them (shop section
    headers, POPs, posters, signage; status `pictures`), one image each with the objects showing it, so
    you can read their wording. Blank the ones that do not fit the request or the input photos
    (set_variants with the entry's `blank`, e.g. {"Picture": "Blank"}) or give them an image (set_screen)."""
    out: list[Any] = []
    for short, img in service.picture_images(scene_dir):
        out.append(json.dumps(short, ensure_ascii=False))
        out.append(Image(data=img, format="png") if img else "(image not found)")
    return out


@mcp.tool()
def open_preview(scene_dir: str) -> dict[str, Any]:
    """Open the scene's live preview page in the user's browser (served on local HTTP; it reloads
    itself after every tool call, so open it once right after create_scene). Shows progress, the
    candidates (selected one framed, the object you are looking at highlighted), the top-down layout,
    issues, the Isaac Sim screenshot and licenses. Returns the URL; if no browser could be opened
    (ssh, no desktop), give the user the URL and the hint."""
    return service.with_language(scene_dir, service.open_preview(scene_dir))


@mcp.tool()
def select_asset(scene_dir: str, object_id: str, index: int | None = None, correction: bool = False,
                 uri: str | None = None, reason: str | None = None) -> dict[str, Any]:
    """Use candidate `index` (from get_scene) for an object, or `uri` (from search_assets / research_object /
    check_taste, added to the candidates). reason: "match" (the search ranked the right thing too low) / "look" /
    "variety" / "fit" / "other". correction=true when the user asked for this asset (remembered per query).

    Use candidate `index` (from get_scene) for an object. correction=true when the user asked for this asset
    instead of yours: remembered per query (picks on the preview page are remembered anyway). uri instead of
    index: an asset from search_assets, research_object or an alternative check_taste suggested with index null
    (it is added to the candidates).
    reason: why the new one is better — "match" (it is the thing the query names and the search ranked it too
    low), "look" (style / color), "variety" (differ from a twin), "fit" (size / space), "other". Swaps are
    logged per scene to tune the search; only "match" (and the user's own picks) count as ranking errors."""
    return service.reply(service.select_asset(scene_dir, object_id, index, correction, uri, reason), object_id)


@mcp.tool()
def move_object(
    scene_dir: str,
    object_id: str,
    position: tuple[float, float] | None = None,
    yaw_deg: float | None = None,
    facing_deg: float | None = None,
    correction: bool = False,
) -> dict[str, Any]:
    """Set an object's footprint center (x, y) [m] and direction by hand; clears its layout. Prefer facing_deg =
    the world direction its front faces (0 = east +X, 90 = north, 180 = west, 270 = south), worked out from the
    selected asset's front; yaw_deg is the raw rotation (facing_deg = yaw_deg + asset_front_deg).

    Manually set an object's footprint center (x, y) [m] and its direction. Clears its layout.
    Prefer facing_deg: the world direction the asset's front faces (0 = east +X, 90 = north +Y, 180 = west,
    270 = south); the yaw is worked out from the selected asset's front, which differs between assets
    (an office chair's front is -Y, a desk's +X), so a desk and a chair with the same facing_deg face the same
    way. yaw_deg is the raw rotation of the asset (status shows facing_deg = yaw_deg + asset_front_deg).
    correction=true when the user asked for this spot: the automatic fixes (auto_fixed) never move it again."""
    return service.reply(service.move_object(scene_dir, object_id, position, yaw_deg, facing_deg, correction), object_id)


@mcp.tool()
def set_auto_fix(scene_dir: str, on: bool) -> dict[str, Any]:
    """Turn the automatic fixes on or off for this scene (default on; SceneSpec.auto_fix=false creates it off).

    At every save the server makes the fixes that need no judgement and reports them as `auto_fixed`: a narrow
    aisle is widened (the fixture across, or the array's aisle), jointed chairs that touch are parted (array
    spacing), a TV with no foot is hung on the wall behind its stand. A fix is kept only when its issue goes and
    no issue or usability warning is new; objects the user placed (moved on the preview, correction=true, a given
    assumption about its position) and the object the call itself changed are never moved. Turn it off when the
    user wants nothing moved for them; on applies the fixes now."""
    return service.reply(service.set_auto_fix(scene_dir, on))


@mcp.tool()
def move_objects(scene_dir: str, moves: list[dict[str, Any]]) -> dict[str, Any]:
    """move_object for several objects in one call and one save (moving rows to match a photo):
    moves = [{"id": "shelf_a", "position": [x, y], "facing_deg": 90}, ...] (facing_deg or yaw_deg, as in
    move_object; leave position out to only turn). Clears their layouts; nothing changes if one move is wrong."""
    return service.reply(service.move_objects(scene_dir, moves))


@mcp.tool()
def set_layout(scene_dir: str, object_id: str, layout: Layout | None, front_offset_deg: float | None = None,
               correction: bool = False) -> dict[str, Any]:
    """Set an object's relational placement (null clears it and keeps the current position).
    front_offset_deg corrects the asset front (180 if it faces backwards, +-90 if sideways).
    correction=true when the user asked for it (e.g. 椅子を机に入れて -> gap < 0): remembered as a preference."""
    return service.reply(service.set_layout(scene_dir, object_id, layout, front_offset_deg, correction), object_id)


@mcp.tool()
def set_variants(scene_dir: str, object_id: str | list[str], variants: dict[str, str] | None) -> dict[str, Any]:
    """Choose USD variants of this object's asset, e.g. {"Lighting": "Off"} (lights / signage / display
    variants); a list of ids sets the same variants on all of them in one save. The asset's variant sets are in
    the candidate summary ("variants"). null / {} clears."""
    return service.reply(service.set_variants(scene_dir, object_id, variants),
                         object_id if isinstance(object_id, str) else None)


@mcp.tool()
def set_screen(scene_dir: str, object_id: str, screen: dict[str, Any] | None) -> dict[str, Any]:
    """Show an image on a signage's screens or a poster's printed picture (only assets listing "screens"):
    screen {image: "<file>" | {"<material>": "<file>"}, fit contain|cover|stretch, background, url, title, author,
    license} (source recorded in LICENSES.md; <stem>.source.json fills the rest). Keep Screen On / Picture Default.
    null = the asset's own picture.

    Show an image on this signage's screens (or a poster's / board's printed picture) instead of the asset's own
    picture (a café ad in a factory looks wrong). Only assets whose candidate summary lists "screens" support it. screen: {"image": "<file>" (every
    screen) or {"<screen material>": "<file>"}, "fit": "contain" (default, background around) | "cover" (crop) |
    "stretch", "background": "#000000", "url", "title", "author", "license"}: the image's source, recorded in
    LICENSES.md; fields left out are read from <stem>.source.json next to the image. The Screen variant must stay
    On (Picture: Default). null goes back to the asset's own picture."""
    return service.reply(service.set_screen(scene_dir, object_id, screen), object_id)


@mcp.tool()
def set_tint(scene_dir: str, object_id: str, tint: list[dict[str, Any]] | dict[str, Any] | str | None) -> dict[str, Any]:
    """Repaint an object: tint "#rrggbb" = its main color (largest painted area, not glass / metal / screens), or
    {color, materials: [names or fnmatch], texture: false to paint flat}; a list applies in order. Textures are
    multiplied (only darker). null = the asset's colors.

    Repaint an object (a red rack where the asset is white, the colors of a reference image's furniture).
    tint: "#rrggbb" = the asset's main color (largest painted area, glass / bare metal / screens left out, and every
    material of that same color); {"color": "#rrggbb", "materials": ["Tray*Mat", ...]} = those materials (names or
    fnmatch patterns); a list applies in order. A textured material is multiplied by the color (it can only get
    darker); add "texture": false to drop its texture and paint it flat (a printed sign made plain white). null goes back to
    the asset's colors. After compose_scene, the object's customData agentic_sim_world_gen:tint lists the materials changed."""
    return service.reply(service.set_tint(scene_dir, object_id, tint), object_id)


@mcp.tool()
def set_collider(scene_dir: str, object_id: str, collider: Literal["auto", "box", "none"]) -> dict[str, Any]:
    """Static box collider (the asset's bounding box, invisible, never moves) so robots and other objects do not
    pass through. auto (default): added only for assets known to have no physics (status shows "collider").
    box: always, when check_in_isaac reports no_collider for an asset auto did not catch. none: never."""
    return service.reply(service.set_collider(scene_dir, object_id, collider), object_id)


@mcp.tool()
def fill_windows(scene_dir: str, covers: Literal["auto", "curtain", "blind"] | None = "auto") -> dict[str, Any]:
    """Put a window asset in every empty window opening (stretched to fit; opening_fit beyond 0.75-1.33x), chosen
    by shape (shop window / floor-length / small / casement / fixed / sliding), plus a curtain or blind: covers auto = curtains
    at home, none elsewhere. Swap them like any object.

    Put a window asset (sash, frame and glass) in every window opening that has none, stretched to the
    opening (fit_scale; opening_fit if it had to stretch more than 0.75-1.33x), and a curtain or blind over it.
    The asset is chosen by the opening's shape: floor-length (sill ~0) / small frosted (<= 0.8 x 0.8 m) /
    casement (<= 0.5 m wide) / fixed (offices, shops) / sliding; a shop's opening 1.8 m+ wide is its storefront
    (shop window: a wide FIX window or a shop-front sash). covers: auto = curtains at home, none
    elsewhere; small windows never get one. Swap them like any object (view_candidates, select_asset).
    An opening whose best asset is no window / curtain (only weak matches, or made for another line of work: a
    car door tagged window) is left empty and listed in not_found: tell the user."""
    st = service.fill_windows(scene_dir, covers)
    out = {**service.reply(st), "added": st["added"]}
    if st.get("not_found"):
        out["not_found"], out["not_found_hint"] = st["not_found"], st["not_found_hint"]
    return out


@mcp.tool()
def set_array(scene_dir: str, object_id: str, array: Array | None, correction: bool = False) -> dict[str, Any]:
    """Repeat an object in rows: count units per row, rows, spacing between units, aisle in front of
    rows, alternate (face-to-face rows / back-to-back, for warehouse racks), back_to_back (pairs back to back
    first: gondola islands), back_gap. null clears it."""
    return service.reply(service.set_array(scene_dir, object_id, array, correction), object_id)


@mcp.tool()
def set_fill(scene_dir: str, object_id: str, fill: Fill | None) -> dict[str, Any]:
    """Stock the shelves of fill.on (a shelf / cooler / showcase, every array unit or fill.units) with this object:
    levels (0 = lowest board; default all that fit), span (0..1 of each board from the left), rows from the front,
    gap, lay_flat (blister packs on their backs), hang (on the fixture's hooks; levels = hook rows, rows = packs per
    hook). Replaces the object's layout / array; null clears.

    Stock the shelves of fill.on (a shelf, cooler, showcase; every array unit, or fill.units) with
    this object: levels (0 = lowest board, default all that fit), span (0..1 part of each board's
    width from the left seen from the front), rows from the front (default as deep as fits), gap,
    lay_flat (lay products on their backs, front up: hanging blister packs, which fall over when stood),
    hang (hang them on the fixture's hooks instead: pegboards, hook racks; levels = hook rows, rows = packs per hook;
    status shows a fixture's hooks). Replaces the object's layout / array. null clears it."""
    return service.reply(service.set_fill(scene_dir, object_id, fill), object_id)


@mcp.tool()
def set_room(
    scene_dir: str,
    floor: str | Surface | None = None,
    walls: str | Surface | dict[str, str | Surface] | None = None,
    wainscot: Wainscot | bool | None = None,
    baseboard: Baseboard | bool | None = None,
    lighting: str | None = None,
    lighting_scale: float | None = None,
    light_kelvin: float | None = None,
    ceiling: bool | str | Surface | None = None,
    width: float | None = None,
    depth: float | None = None,
    height: float | None = None,
    openings: list[Opening] | None = None,
    plan_file: str | None = None,
    outdoor: bool | None = None,
    sky: str | None = None,
    areas: list[Area] | None = None,
    buildings: list[Building] | None = None,
    site: Site | bool | None = None,
    place: str | None = None,
    density: str | None = None,
    goods: list[str] | None = None,
    goods_words: list[str] | None = None,
    fixtures: Fixtures | str | bool | None = None,
    ceiling_features: list[CeilingFeature] | None = None,
    levels: list[Level] | None = None,
    stairs: list[Stairs] | None = None,
    columns: list[Column] | None = None,
    floor_areas: list[FloorArea] | None = None,
    floor_alt_queries: list[str] | None = None,
    walls_alt_queries: list[str] | None = None,
    ceiling_alt_queries: list[str] | None = None,
    zone_floors: dict[str, str | Surface | None] | None = None,
    zone_floor_alt_queries: dict[str, list[str]] | None = None,
    correction: bool = False,
) -> dict[str, Any]:
    """Change the room; only the fields given change, and the layout is solved again. Fields: place (home, office, shop,
    convenience_store, supermarket, drugstore, home_center, hundred_yen_shop, bookstore, electronics_store,
    apparel_store, bakery, variety_store, florist, greengrocer, toy_store, pet_shop, garden_center, shoe_store,
    optician, liquor_store, bicycle_shop, wagashi_shop, record_store, sporting_goods_store, cosmetics_store,
    furniture_store, asian_grocery, cafe, restaurant, factory, warehouse, datacenter, lab, hospital, school, station,
    public, outdoor, other), size, floor / walls / ceiling, lighting (studio, daylight, home, office, warehouse, night;
    outdoor sun, overcast, dusk, street_night), lighting_scale, light_kelvin, density (sparse / normal / lived_in),
    openings, plan_file, zone_floors, wainscot, baseboard, fixtures, ceiling_features, levels, stairs, columns,
    floor_areas, site, outdoor areas / buildings / sky.
    Surfaces: a preset (floor wood_light, wood_dark, tatami, carpet_gray, tile_white, concrete, epoxy_gray,
    raised_floor; walls paint_white, paint_beige, drywall_gray, brick, metal_panel, concrete; outdoor grass, asphalt,
    paving, gravel, soil), a surface object, "mdl:<id>" / "pbr:<id>", or English words (searched; see
    surface_candidates, view_surface_candidates, select_surface). walls per wall: {"*": "paint_white", "N": "brick"}.
    ceiling: true = picked by place, false removes. List fields (openings, levels, stairs, columns, floor_areas,
    ceiling_features, areas, buildings) replace the whole list; [] / false removes.
    correction=true when the user asked for it (lighting / floor / walls remembered per place).

    correction=true when the user asked for this change (not your own adjustment): lighting, lighting_scale,
    floor and walls are remembered per place and become defaults once 2+ scenes agree (get_preferences).
    Change the room: place (what the space is: home, office, shop, convenience_store, supermarket, drugstore,
    home_center, hundred_yen_shop, bookstore, electronics_store, apparel_store, bakery, variety_store, florist, greengrocer, toy_store, pet_shop, garden_center, shoe_store, optician, liquor_store, bicycle_shop, wagashi_shop, record_store, sporting_goods_store, cosmetics_store, furniture_store, asian_grocery, cafe, restaurant, factory, warehouse, datacenter, lab, hospital, school, station, public, outdoor, other), size, floor/walls surface (preset name or {preset, color, roughness, texture, tile_m,
    mdl, static_friction}), lighting preset (studio, daylight, home, office, warehouse, night; outdoor: sun,
    overcast, dusk, street_night), lighting_scale, light_kelvin (color temperature of the fixtures and the sun
    [K] instead of the preset's: warm 2700, neutral 4000, cool 6500), ceiling. Floor presets: wood_light, wood_dark, tatami,
    carpet_gray, tile_white, concrete, epoxy_gray, raised_floor (outdoor ground: grass, asphalt, paving,
    gravel, soil, concrete). Wall presets: paint_white, paint_beige, drywall_gray, brick, metal_panel, concrete.
    ceiling: true adds a ceiling with its own surface picked by place (home / cafe / restaurant: ceiling_board =
    white gypsum board; warehouse / factory / home_center: metal_deck = folded steel deck; other indoor: ceiling_tile
    = office grid; no place: by lighting), false removes it, or give its surface (a preset,
    a surface object, or words like the walls) to add it with that look.
    density (生活感): sparse = only what was asked for, normal = add a few small things per host (books / pen holder /
    plant at a study desk, cushions / magazine / rug at the sofa and table, monitor / document tray on office desks,
    products on every unstocked shop shelf), lived_in = more (mug, plant by the wall, remote). Re-made on each call;
    the added objects have decor=true, sit where they add no issue or usability warning (else left out, listed in
    the event), and are recorded as guessed assumptions 「生活感のため追加」 (remove_object takes one out).
    goods: a shop of a kind not in the place list (sporting goods, wagashi, records ...): English product
    queries its fixtures hold with density (["soccer ball", "basketball", "tennis racket"]), with goods_words the
    kind's own words (["sports"]); stock must name one of these words. [] removes them.
    walls per wall (accent wall): {"*": "paint_white", "N": "brick"} (rectangle S/N/W/E, plan wall ids; "*" = the
    other walls; values are presets, surfaces or words, searched per wall as target "walls:<wall>").
    wainscot (腰壁) {height 0.9, surface wood_light, color, thickness 0.01, walls [ids]} and baseboard (巾木)
    {height 0.06, thickness 0.01, surface trim_white | trim_wood | ..., color, walls}: true = defaults,
    false removes; thin boxes on the inner faces, cut at doors (part of the wall, against_wall keeps clear).
    openings replaces the doors/windows list; plan_file imports a floor plan (.json/.dxf/.ifc).
    zone_floors {plan zone id: floor} gives plan zones their own floor (a preset, a surface or words like the
    floor; "room" = the room floor, null = back to the default). Default: guessed from the zone name
    (和室 / 畳 -> tatami; 洗面 / 浴室 / トイレ / 玄関 / bath / toilet / entrance -> tile_white), else the room floor.
    Words search target "floor:<zone id>" (synonyms in zone_floor_alt_queries[zone id]).
    Outdoor site (outdoor=true; width x depth = the site): areas replaces the ground areas
    [{id, kind road|sidewalk|crosswalk|parking|plaza|path|grass|gravel|soil|water|other, name, rect [x0,y0,x1,y1]
    or polygon, surface, height, keep_clear}], buildings replaces the blocks [{id, name, rect|polygon, height,
    surface}]. sky: clear, partly_cloudy, overcast, evening, night, none, or an HDRI path/URL.
    site (外まわり): the outside around an indoor room, for a shop with its sidewalk, road and parking or a house
    with its garden: {rect [x0,y0,x1,y1] in room coordinates (may be negative; default the room grown by around 8 m),
    ground (asphalt), areas and buildings as for outdoor, lighting sun|overcast|dusk|street_night (the sky and sun;
    the room keeps its own lighting inside: use home / office, not studio), sky}; false removes it. Objects outside
    take zone = an area id or "outside"; against_wall "outside:south" stands with its back to the room's south outer
    wall (a vending machine by the entrance), "<building>:<side>" by a neighbor. Doors in the room's walls connect
    inside and outside. Issues: site_in_room (an area / building reaches into the room), out_of_room (an outside
    object off the site or into the room).
    Bundled MDL materials (Isaac Sim 6.1 NVIDIA/Materials Base + vMaterials 2, ~2600 looks): give floor / walls
    as English words instead of a preset (also the ceiling, with ceiling_alt_queries) ("light oak planks", "white glazed tiles", "red brick", "polished
    concrete"; color + material + pattern) with synonyms in floor_alt_queries / walls_alt_queries. The best
    match is applied (RTX MDL + its thumbnail color as the preview color + friction by category) and
    surface_candidates lists the candidates (index, id, title, color, match; substitute = only weak
    matches): check them with view_surface_candidates, pick another with select_surface, or pass
    "mdl:<id>" directly. CC0 PBR textures of ambientCG / Poly Haven (~2500, ids "ambientCG/..." /
    "PolyHaven/...", source in each candidate) are searched with them: applying one downloads its 1K color /
    roughness / normal maps into AGENTIC_SIM_WORLD_GEN_CACHE once (offline and not cached -> the next candidate, in
    `skipped`); "pbr:<id>" directly. Licenses and download sources go into license_report / LICENSES.md.
    fixtures (照明器具, replaces the preset's ceiling lights with fixtures that have a body): a kind
    "downlight" (recessed round, 3500 K) | "pendant" (hanging shade, 2700 K, kept 2.1 m above the floor) | "line" (long
    linear light, 5000 K, along the longer side) | "panel" (600 square, 5000 K), or {kind, spacing m, size (diameter or
    [length, width]), drop (pendant cord m), axis x|y, color #rrggbb (the body)}; false = back to the preset's lights.
    The preset still sets the brightness (lighting_scale) and kelvin (light_kelvin overrides the kind's).
    ceiling_features (梁・下がり壁) replaces the list: beams {kind "beam", axis "x"|"y" (the direction it runs),
    at m (its center across) or spacing m (repeated, centered), width 0.3, drop 0.4 below the ceiling, surface, color}
    and soffits {kind "soffit", wall S|N|W|E (or plan wall id), width (out from the wall), drop}. They are obstacles
    (issue hits_beam: lower the object or move it); hanging pendants are too (hits_fixture). [] removes them.
    Wall posters / POP: place the poster asset with layout {against_wall: <wall>, wall_height: <m from the floor to its
    bottom>} (hung flat on the wall, above wainscot / baseboard), its picture with set_screen.
    levels (段差・小上がり・奥の一段高い床) replaces the list: [{id, name, rect [x0,y0,x1,y1] | polygon, height m (0-2),
    floor (surface; default the room floor), rail [edges with a railing: "south" etc. for a rect, edge numbers for a
    polygon]}]. Objects inside stand on it (z = height); one straddling its edge is on_step_edge.
    stairs (階段) replaces the list: onto a level {id, level, edge, offset (m along the edge from its west / south end,
    the stairs' center), width 1.0} or freestanding (to an upper floor not modelled) {id, at [x,y] (foot center),
    up (compass or degrees), rise m}; count (default ceil(rise / 0.18) risers), going 0.28, rail none|left|right|both
    (handrail 0.9 m above the nosings, left = left going up), surface / color. Steps are solid boxes (colliders);
    objects on them are hits_stairs, on railings hits_rail. [] removes them.
    columns (柱) replaces the list: [{id, at [x,y] (center), size (side m or [x, y], default 0.5) or diameter (round),
    yaw_deg, height (default to the ceiling), surface / color (default the walls)}]; static colliders, objects in them
    are hits_column, the nav map and walking checks go around them.
    floor_areas (床の一部: タイルの入口・カーペットの一角) replaces the list, in any room: [{id, name, rect [x0,y0,x1,y1] |
    polygon, floor (preset / surface / words, searched as target "floor:<id>"), color}]; later ones lie over earlier
    ones. For a whole plan zone use zone_floors. [] removes them."""
    return service.reply(service.set_room(
        scene_dir, floor=floor, walls=walls, wainscot=wainscot, baseboard=baseboard, lighting=lighting, lighting_scale=lighting_scale,
        light_kelvin=light_kelvin, ceiling=ceiling, width=width, depth=depth, height=height, openings=openings, plan_file=plan_file,
        outdoor=outdoor, sky=sky, areas=areas, buildings=buildings, site=site, place=place, density=density,
        goods=goods, goods_words=goods_words, fixtures=fixtures, ceiling_features=ceiling_features, levels=levels, stairs=stairs, columns=columns, floor_areas=floor_areas, floor_alt_queries=floor_alt_queries, walls_alt_queries=walls_alt_queries,
        ceiling_alt_queries=ceiling_alt_queries, zone_floors=zone_floors, zone_floor_alt_queries=zone_floor_alt_queries,
        correction=correction,
    ))


@mcp.tool(structured_output=False)  # returns text + images, not JSON
def view_surface_candidates(scene_dir: str, target: str = "floor") -> list[Any]:
    """Show the thumbnails of the material candidates (bundled MDL and CC0 PBR) of the last set_room floor / walls words
    (target "floor", "walls", "ceiling", "walls:<wall>" for one wall of a per-wall map, or "floor:<zone id>"),
    so you can judge them and call select_surface."""
    out: list[Any] = []
    for short, img in service.surface_thumbnails(scene_dir, target):
        out.append(json.dumps(short, ensure_ascii=False))
        fmt = "jpeg" if img and img[:2] == b"\xff\xd8" else "webp" if img and img[8:12] == b"WEBP" else "png"
        out.append(Image(data=img, format=fmt) if img else "(no thumbnail)")
    return out


@mcp.tool()
def select_surface(scene_dir: str, target: str, index: int) -> dict[str, Any]:
    """Use material candidate `index` (from surface_candidates; a CC0 PBR one downloads its textures once) for the floor, walls, ceiling, one wall
    ("walls:<wall>", e.g. walls:N) or a zone floor ("floor:<zone id>") (target)."""
    return service.reply(service.select_surface(scene_dir, target, index))


@mcp.tool()
def import_floor_plan(path: str, out_dir: str | None = None, units: str | None = None,
                      storey: str | None = None, wall_layers: list[str] | None = None,
                      furniture: bool = True) -> dict[str, Any]:
    """Read a floor plan (agentic-sim-world-gen plan JSON, DXF, IFC): returns room fields, wall ids, faces and zones, and writes
    plan.json + plan.svg (out_dir unset = the output root). units / wall_layers for DXF, storey for IFC; IFC
    furniture comes back as `furniture` and draft `objects` for create_scene (furniture=false skips it). Show
    plan.svg to the user before building the scene.

    Read a floor plan: agentic-sim-world-gen plan JSON, DXF (wall layers + door/window blocks) or IFC (walls, doors,
    windows, spaces). Returns room fields, wall ids, wall faces (direction + zone) and zones, and writes
    plan.json + plan.svg into out_dir (leave it unset: the server's output root, <file stem>_plan). units: DXF without $INSUNITS (m/cm/mm).
    storey: IFC storey name. wall_layers: DXF layers holding the walls (names or patterns like "E-B-*")
    when the default (names with WALL / 壁) misses them; warnings then list the busiest layers.
    IFC furniture (IfcFurniture / IfcSystemFurnitureElement ...; furniture=false skips it): `furniture` lists
    each piece (ifc_guid, class, predefined_type, name, type_name, zone, position, z, facing_deg, size along its
    own X / Y + height, a guessed English query and where it came from) and `objects` holds draft ObjectSpecs
    (fixed position + facing_deg, size bounds: footprint ±20 %, height -30 % .. +50 %, ifc_guid): refine the queries, then pass them to create_scene.
    Show plan.svg to the user and check it before building the scene."""
    from pathlib import Path

    opts: dict[str, Any] = {}
    ext = Path(path).suffix.lower()
    if units and ext == ".dxf":
        opts["units"] = units
    if storey and ext == ".ifc":
        opts["storey"] = storey
    if wall_layers and ext == ".dxf":
        opts["wall_layers"] = wall_layers
    if ext == ".ifc" and not furniture:
        opts["include_furniture"] = False
    out = service.import_floor_plan(path, out_dir or _scene_dir(Path(path).stem + "_plan"), **opts)
    out.pop("room")  # large; use plan_file instead
    out["plan_file"] = out["files"]["plan"]
    return out


@mcp.tool()
def research_object(
    scene_dir: str,
    object_id: str,
    query: str | None = None,
    min_size: Size | None = None,
    max_size: Size | None = None,
    candidates: int = 6,
    alt_queries: list[str] | None = None,
    select: bool | None = None,
) -> dict[str, Any]:
    """Search again for one object with a new English query, synonyms and/or size bounds; replaces its candidates.
    select: true = take the new top candidate, false = keep the current asset (it stays in the list),
    omitted = take the top only if it matches; if every new candidate is a substitute the current asset is kept
    (research.kept_current). Use false when you will ask the user to choose; then select_asset."""
    return service.reply(service.research_object(scene_dir, object_id, query, min_size, max_size, candidates, alt_queries, select), object_id)


@mcp.tool()
def add_object(scene_dir: str, obj: ObjectSpec, candidates: int = 6) -> dict[str, Any]:
    """Add an object to the scene and search candidates for it. For two or more, use add_objects."""
    return service.reply(service.add_object(scene_dir, obj, candidates), obj.id)


@mcp.tool()
def add_objects(scene_dir: str, objs: list[ObjectSpec], candidates: int = 6) -> dict[str, Any]:
    """Add several objects in one call and one save (all or none on a duplicate id). Prefer this to repeated
    add_object (e.g. the fills of several shelves)."""
    return service.reply(service.add_objects(scene_dir, objs, candidates), [o.id for o in objs])


@mcp.tool()
def remove_object(scene_dir: str, object_id: str | list[str]) -> dict[str, Any]:
    """Remove an object, or several with a list of ids (one save). Decor on / beside it goes too."""
    return service.reply(service.remove_object(scene_dir, object_id), object_id if isinstance(object_id, str) else None)


@mcp.tool()
def duplicate_object(scene_dir: str, object_id: str) -> dict[str, Any]:
    """One more of an object with the same asset, standing free beside it (no new search); returns added [id].
    Move it from there. Not for arrays, shelf stock, door / window assets."""
    return service.reply(service.duplicate_object(scene_dir, object_id), object_id)


@mcp.tool()
def restore(scene_dir: str, version: int | None = None, list_only: bool = False) -> dict[str, Any]:
    """Go back to an earlier saved version of the scene. Every change keeps one (the last 30); the user
    can do the same on the preview (「取り消す」, 「ここに戻す」 in the change history).
    version None = undo the last change (again = redo). list_only=true: only list {version, text} (oldest first,
    the last is the scene now). The room, objects and assumptions come back exactly as saved; the user's notes
    stay. Use it when the user says 「さっきの方がよかった」「元に戻して」 instead of rebuilding by hand, then
    compose_scene again."""
    from pathlib import Path

    if list_only:
        return {"versions": service.version_rows(Path(scene_dir).expanduser())}
    return service.reply(service.restore(scene_dir, version))


@mcp.tool()
def compose_scene(scene_dir: str, localize: bool = False,
                  not_in_photos: dict[str, str] | None = None) -> dict[str, Any]:
    """Write scene.usda. Assets are referenced by URL; localize=True downloads them and references local copies.
    With photos fitted (photo_fit.json in the scene) it stops while floor furniture is not placed by them
    (status photo_placement: misplaced, unlocated, one_point, located_unknown): locate 2-4 feet of each and
    fit_photos again. not_in_photos={id: why} only for an object no frame shows on the floor (kept; "" removes)."""
    r = service.compose_scene(scene_dir, localize=localize, not_in_photos=not_in_photos)
    return service.with_language(scene_dir, r)


@mcp.tool()
def check_taste(scene_dir: str) -> dict[str, Any]:
    """Does any chosen asset clash with the rest (one dark-steel shelf in a white study)? Compares the selected
    assets' thumbnails with an image model (optional extra `taste`). Returns `taste` outliers with alternatives
    (added to the candidates), or `skipped`. A warning only: offer the alternatives; never swap without the
    user's pick. Run after the assets are chosen, before asking for agreement.

    Does any chosen asset clash with the rest (a white study with one dark-steel shelf)? Embeds the selected
    assets' thumbnails with an image model (SigLIP2, optional: extra `taste` and the model in the Hugging Face
    cache) and compares each object with the others. Returns `taste`: `outliers` [{object, asset, fit,
    median_fit, looks, others_look, alternatives [{index, name, fit}]}] (alternatives are assets of the same kind
    that fit the others better, from a wider search of the object's query + alt_queries; suggested ones are added
    to its candidates, so index works with view_candidates / select_asset), or `skipped` with the reason. A warning only: tell the user which
    object stands out and offer the alternatives (view_candidates); swap with select_asset only after the
    user picks one. Never swap on your own. Run it after the assets are chosen, before asking for agreement."""
    return service.with_language(scene_dir, service.check_taste(scene_dir))


@mcp.tool()
def license_report(scene_dir: str, full: bool = False) -> dict[str, Any]:
    """Licenses of the selected assets with attribution lines and warnings (non-commercial,
    share-alike, unknown). Also writes LICENSES.md next to the scene. Report this to the user.
    Returns the objects per license, attribution lines, warnings and sources; full=true adds every asset's
    origin, version and hash (also in LICENSES.md)."""
    r = service.license_report(scene_dir)
    return service.with_language(scene_dir, r if full else service.license_brief(r))


@mcp.tool()
def check_in_isaac(scene_dir: str, steps: int = 240, gif: bool = False, live: bool = False,
                   orbit: int = 24, draft: bool = False, force: bool = False) -> dict[str, Any]:
    """Open scene.usda in Isaac Sim (headless; live=true uses the user's running Isaac Sim with its python_server)
    and run physics for `steps` (60 Hz); saves check.png (gif=true: check.gif) and an orbit of `orbit` shots
    (0 = skip). Returns a summary: verdict passed / needs_fix (unstable, sunk or no_collider) / draft / failed,
    groups and problems (full report in check.json - no need to open it). draft=true: no physics, pictures only.
    A scene over the draw budget (status draw_load) is refused unless force=true.

    Open scene.usda in Isaac Sim headless (live=true: in the Isaac Sim the user has open, if it was
    started with --enable isaacsim.code_editor.python_server; seconds instead of a minute, the user sees
    the settle and the failed objects get selected; falls back to headless), run physics for `steps` (60 Hz), report per-object
    displacement / stability / sinking and save check.png (gif=true: also check.gif of the settle).
    orbit: also shoot the settled room from that many directions (check_orbit/, the preview page lets the
    user drag to rotate it; about 6 s for 24 on an RTX 2080 Ti; 0 = skip, e.g. when re-checking only physics).
    Report lists: unstable (moved), sunk (fell through the floor), no_collider (robots pass through:
    pick another candidate), static (no rigid body: fixed in place, fine for furniture).
    The result is a summary (objects_total, per-object-id groups with count / max_displacement_m, problems =
    details of unstable / sunk / no_collider bodies, long lists grouped as "id/* (n)"); the full per-body
    report is in full_report (check.json). Needs Isaac Sim (ISAACSIM_PATH, default ~/isaacsim; pip install: ISAACSIM_PYTHON).
    draft=true: no physics, just the screenshot and the orbit of the room as laid out (a first draft to show the
    user, skill step 4); the preview marks it 叩き台 until a full check runs.
    ok = the run worked; verdict = passed / needs_fix (anything unstable, sunk or without a collider) / draft /
    failed. Read the summary here (and status): no need to open check.json.
    A scene that draws too many faces (status draw_load: thousands of scanned products) is not opened, as Isaac
    Sim would run out of memory: lighten it as draw_hint says (force=true tries anyway)."""
    r = service.check_in_isaac(scene_dir, steps, gif=gif, live=live, orbit=orbit, draft=draft, force=force)
    return service.with_language(scene_dir, r)


@mcp.tool()
def finish(scene_dir: str, draft: bool = False, steps: int = 240, gif: bool = False, orbit: int = 24,
           force: bool = False) -> dict[str, Any]:
    """The closing steps in one call, after the user agrees (or おまかせ): compose_scene if the USD is behind,
    check_in_isaac, check_request (re-measured) and license_report. Stops at the first thing to fix (`stopped`,
    `next`); `done` when nothing is left. draft=true: only the USD and pictures without physics (a first draft
    after you fixed the layout issues create_scene reported).

    Runs, in order: (1) layout issues or open layout options -> stops (`issues`); (2) compose_scene when
    scene.usda is not the scene as it is now (a refusal, e.g. photos not placed, stops with the reason);
    (3) check_in_isaac (live when the user's Isaac Sim is open; a check that already passed on the unchanged
    scene is reused) - needs_fix / failed stops with `check` (verdict, report.problems) and `next`: fix and call
    finish again; (4) the recorded request items measured again (`requirements_summary`, `requirements_open` =
    the ones not met or not measurable; none recorded: `requirements_hint`); (5) the licenses (`licenses`:
    objects per license, attribution lines, warnings; LICENSES.md written). No Isaac Sim on this machine: the
    check is `skipped`, the rest runs, and `unverified` says what to tell the user. steps / gif / orbit / force
    are check_in_isaac's. Use the single tools only to redo one step."""
    return service.with_language(scene_dir, service.finish(scene_dir, draft, steps, gif, orbit, force))


@mcp.tool(structured_output=False)  # returns text + image
def view_fronts(scene_dir: str, around: bool = False, ids: list[str] | None = None,
                fill: bool = False) -> list[Any]:
    """Render each object alone from the side its front is assumed to face (Isaac Sim, ~15-30 s) and return the
    contact sheet. Use after compose_scene when a thumbnail does not show the front. A back panel in a tile ->
    set_layout(front_offset_deg=180, or +-90); around=true shoots 4 sides. ids: only these; fill=true: shelf items too.

    Render each object alone from the side its front is assumed to face (Isaac Sim headless,
    ~15-30 s; only the objects shot are loaded) and return the contact sheet image. Fill items (products on shelves) are skipped: they
    follow their shelf, and hundreds of tiles take many minutes. ids: only these objects (an id of
    an array / fill group shoots its items, "<group>/<item>" one item); fill=true: all fill items too. Use it after compose_scene when a thumbnail does not
    show which side is the front (cabinets, shelves, chairs). Each tile should show the object's
    front (seat, doors, open shelves); a back panel means the front convention is wrong for that
    asset -> set_layout(front_offset_deg=180), or 90 / -90 for a side. around=true: 4 tiles per
    object (front, +90, +180, +270 deg counter-clockwise) to find the right offset."""
    r = service.front_views(scene_dir, around, ids=ids, fill=fill)
    if not r.get("ok"):
        return [json.dumps(service.with_language(scene_dir, r), ensure_ascii=False)]
    with open(r["image"], "rb") as f:
        return [json.dumps(service.with_language(scene_dir, r), ensure_ascii=False), Image(data=f.read(), format="png")]


@mcp.tool()
def set_assumptions(scene_dir: str, assumptions: list[Assumption], replace: bool = False,
                    remove: list[str] | None = None) -> dict[str, Any]:
    """Record each decision (room size, place, floor, walls, lighting; per object asset, count, position, facing,
    size) before asking for agreement (skill step 5): given=true if the user asked for it, else a guess with a
    short basis ('6畳の標準'); options let the user pick on the preview (「違う」, then status
    assumption_feedback). Same object+item replaces; replace=true starts over; remove=["item" | "<object>:item"].

    Record what you decided for the scene before asking the user to agree (skill step 5): each row is
    one decision (room size, place, floor, walls, lighting; per object asset, count, position, facing, size)
    with given=true if the user asked for it, else a guess with a short basis ('6畳の標準'). Rows with the
    same object+item are replaced; replace=true starts over; remove=["item"] or ["<object>:item"].
    The preview shows guesses first (「仮定」). What you do not record the server records as a guess from the
    scene (status `assumptions_auto`): pass only what the user said and guesses that have a reason. Give a row `options` (e.g. the
    plan_questions options) and the user can pick one with 「違う」 on the preview; what they send shows in status
    `assumption_feedback` until you fix it and re-record that row with given=true."""
    return service.reply(service.set_assumptions(scene_dir, assumptions, replace, remove))


@mcp.tool()
def moods(scene_dir: str) -> dict[str, Any]:
    """The looks to pick from for the scene's place (home, office, shops, cafes): each with a sample picture
    (a standard room drawn once in Isaac Sim — not this scene; open it with Read to see it), its floor / walls /
    light color and furniture colors. The preview page shows them as cards the user can click. Offer them when the
    user cannot say what look they want; apply one with apply_mood."""
    return service.with_language(scene_dir, service.moods(scene_dir))


@mcp.tool()
def apply_mood(scene_dir: str, name: str) -> dict[str, Any]:
    """Apply a look from moods: floor, walls, trim, ceiling, light color and brightness at once (as assumptions).
    Furniture is not swapped: try the `mood_furniture` words with research_object(select=false) and offer the
    swaps; then call again with the same name to clear status mood_hint. `overwrote` = user decisions it replaced
    (tell them); name "undo" restores the room from before the first mood.

    Apply a look from moods: floor, walls, trim, ceiling, light color and brightness change at once
    (recorded as assumptions). Furniture is not swapped: mood_furniture lists objects with words to try — look
    with research_object(select=false) + view_candidates and offer the swaps. When status has mood_hint the user
    picked it on the preview page: do the furniture, then call apply_mood with the same name to clear it.
    overwrote lists what the user had decided (floor / walls / light as asked, reference images): say so.
    name "undo" puts back the room as it was before the first mood."""
    return service.with_language(scene_dir, service.apply_mood(scene_dir, name))


@mcp.tool()
def set_purpose(scene_dir: str, purpose: str, robot_radius: float | None = None, targets: list[str] | None = None,
                given: bool = True) -> dict[str, Any]:
    """What the scene is for: demo, amr (robot driving), manipulation (grasping) or sdg (synthetic data). Applies the
    defaults the user has not decided (density, the robot's aisle width from robot_radius, SceneSpec.sdg) as
    guesses 「用途から」; given=false when you chose it. targets: what an sdg detector should find. Status
    `purpose_next` says what to do next.

    What the scene is for: demo (見た目・デモ), amr (移動ロボットの走行テスト), manipulation (アームでつかむ)
    or sdg (合成データ); the answer to the plan_questions purpose question works too. Applies the defaults the
    user has not decided: Room.density (amr sparse, others lived_in), the robot's walking width for the usability
    check (amr / manipulation, from robot_radius, default 0.3 m; a robot_radius you pass is recorded as what the
    user said) and SceneSpec.sdg (sdg), each recorded as a guess
    「用途から」. given=false when you chose the purpose (おまかせ). targets: for sdg, what the detector should find
    (English words); status `purpose_missing` lists the ones no object stands for. `purpose_next` says what to do
    next for the purpose (nav_map for amr, things to grasp for manipulation, generate_data dry run for sdg)."""
    return service.reply(service.set_purpose(scene_dir, purpose, robot_radius, targets, given))


@mcp.tool()
def resolve_notes(scene_dir: str, ids: list[str], reply: str = "") -> dict[str, Any]:
    """The user can double-click a spot on the preview's top view or 3D viewer and write there (「ここに棚」
    「この辺が狭い」). They show in status `notes` (id, text, position in meters, z, the object at the spot,
    `where` in words, `near` objects). Act on each as its words say, then call this with their ids and a short
    Japanese reply (what you did); they disappear from the page. Ask in chat only when a note is unclear."""
    return service.reply(service.resolve_notes(scene_dir, ids, reply or None))


@mcp.tool()
def propose_layouts(scene_dir: str, options: list[LayoutOption]) -> dict[str, Any]:
    """When the arrangement is a real choice, offer 2-4 layouts: each {name (ascii), label (Japanese), note,
    objects: id -> patch of layout / array / position / yaw_deg / facing_deg / zone}. Each is solved and rated
    (`best`) and shown on the preview (「配置案」). Ask the user, then choose_layout. options=[] withdraws them.

    Before generating, when the arrangement is a real choice (gondolas lengthwise or crosswise, desks as an
    island or facing the wall), offer 2-4 layouts. Each option has a name (ascii), a Japanese label, a
    short note and `objects`: object id -> patch of layout / array / position / yaw_deg / facing_deg / zone
    (assets are shared; objects not named keep their placement). Each is solved and rated (issues, usability,
    requirements_unmet; `best` marks the one choose_layout('best') would take) and its plan is shown side by
    side on the preview (「配置案」). Ask the user which one, then choose_layout. options=[] withdraws them."""
    st = service.propose_layouts(scene_dir, options)
    out = service.reply(st)
    return out


@mcp.tool()
def propose_drafts(scene_dir: str, axis: Literal["size", "amount", "composition"] | None = None,
                   options: list[LayoutOption] | None = None) -> dict[str, Any]:
    """Three drafts that differ on purpose in ONE thing the user did not say, as plans only (no Isaac Sim):
    axis size (small / middle / large, made by the server), amount or composition (3 options from you). Shown on
    the preview (「叩き台 3 案」); ask which is closest, then choose_layout.

    For a user who cannot put the scene into words: three plans far apart say more than a question. axis left
    out = the first of size / amount / composition the user has not decided (`draft_axes` in the result: a given
    assumption row or a floor plan decides one). amount / composition: the scene as it is must hold everything
    of the fullest option; each option = {name, label, note, without: [ids left out], objects: {id: {array:
    {count, rows, ...}, layout, ...}}, room: {width, depth}}. Each draft is solved on a copy, rated (issues,
    usability, room, objects) and drawn; choose_layout(name) applies it and records the axis as given
    (「叩き台 3 案から選択」; 'best' = a guess). propose_layouts([]) withdraws them."""
    st = service.propose_drafts(scene_dir, axis, options)
    return service.reply(st) | {"draft_axes": st["draft_axes"]}


@mcp.tool()
def choose_layout(scene_dir: str, name: str) -> dict[str, Any]:
    """Keep one of the proposed layouts: its placement is applied and the other options are dropped. Use the
    user's pick, or name='best' when they left it to you (おまかせ: fewest issues, then unmet request items,
    then usability warnings). Records the assumption row 'layout'. The user can also pick on the preview
    (「この案にする」), which shows up in status preview_edits."""
    return service.reply(service.choose_layout(scene_dir, name))


@mcp.tool()
def propose_contrast(scene_dir: str, axis: str | None = None, direction: str | None = None,
                     option: dict[str, Any] | str | None = None) -> dict[str, Any]:
    """After a 「なんか違う」 / 「イメージと違う」 with NO reason: do not ask "what is wrong?" - show the scene
    as it is next to one that differs in ONE thing and ask which is closer. Two or three rounds find what differed.
    axis: size (the room 1.25x / 0.8x each side, made here), amount (fewer things = without what density added, made
    here; or your option {label, without / objects}), mood (another look's sample picture; option = the mood's
    name), layout (your option {label, objects: {id: {layout / array / position / facing_deg / zone}}}: another
    type of arrangement of the same things). Left out: the next step nobody answered (size first, then amount,
    mood). direction: "more" / "less" for size and amount. The pair shows on the preview as 「どちらが近いですか」
    (plans only, no 3D); pass the user's answer to choose_contrast. `contrast.left`: the steps not answered yet."""
    return service.reply(service.propose_contrast(scene_dir, axis, direction, option))


@mcp.tool()
def choose_contrast(scene_dir: str, pick: str) -> dict[str, Any]:
    """The user's answer to propose_contrast. pick: "now" (as it is is closer), "other" (the changed one is closer:
    it is applied to the scene) or "neither". now / other are recorded as the user's (assumption of that axis,
    given, 「見本の二択で選択」) and the step is not offered again. `contrast.found`: the axis that differed (pick =
    other) - show the result and ask if it is closer; otherwise propose_contrast again (`contrast.left`). When
    nothing is left, ask in words or for a reference picture."""
    return service.reply(service.choose_contrast(scene_dir, pick))


@mcp.tool()
def check_request(scene_dir: str, requirements: list[Requirement] | None = None, replace: bool = True) -> dict[str, Any]:
    """Skill step 7: split the request into items in the user's words with measurable checks (against_wall /
    next_to / near / count / facing / in_zone / on); give your verdict (met / unmet / unknown) for items without.
    Returns `requirements` and `requirements_summary`; fix unmet ones and call again with no arguments to re-measure.

    After generating (skill step 7): split the user's request into items in their words and attach the
    relations that can be measured (checks: against_wall / next_to / near / count / facing / in_zone / on).
    The service measures them on the placed scene; items without checks need your verdict (met / unmet /
    unknown, e.g. style or color judged from view_candidates). Returns `requirements` (status met / unmet /
    unknown, by measured / judged) and `requirements_summary`. Fix unmet items, then call again with no
    requirements to re-measure. The preview shows the table (「指示との突き合わせ」)."""
    st = service.check_request(scene_dir, requirements, replace)
    out = service.reply(st)
    for k in ("requirements", "requirements_summary", "requirements_hint", "requirements_error"):
        if k in st:
            out[k] = st[k]
    return out


@mcp.tool()
def set_input(scene_dir: str, input: InputRef, seen_in: list[str] | None = None) -> dict[str, Any]:
    """Add or update (same id; fields left out are kept) an input photo / drawing with its source and camera; run
    compose_scene afterwards. seen_in = the objects this photo shows (status warns in_photo_view about others
    covering its view). kind "reference" = a mood image: no camera, carries `taste` (apply it with set_room /
    queries yourself).

    Add an input photo/drawing to the scene, or update the one with the same id (fields left out
    are kept), e.g. to correct its camera after compare_photo. Run compose_scene afterwards.
    seen_in = ids of the objects this photo shows. Then status warns (in_photo_view) about any
    other object that covers 3 %+ of the photo's view - the compare render would show it in front of what
    the photo shows; move it behind or beside that camera.
    A reference image (kind "reference") has no camera and carries `taste` (what you read off it:
    floor / walls / lighting words, furniture colors, style, density); the preview shows it (「参考画像」) and
    LICENSES.md lists its source. It changes nothing by itself: apply the taste with set_room / queries."""
    return service.reply(service.set_input(scene_dir, input, seen_in))


@mcp.tool()
def fit_photos(request: FitRequest, out_dir: str | None = None, depth: bool = False,
               apply: bool | None = None) -> dict[str, Any]:
    """Several photos of one room -> room size, openings and each photo's camera, by least squares over landmarks you
    read off the photos (corners, wall lines, opening edges, shared points; video_poses' `relative` for video
    frames; depth=true for monocular depth). `locate` turns pixels into floor positions (give `object` ids for
    place_from_photos). Read `worst`, `warnings`, `suspects`, `outside`. With out_dir = the scene folder it writes
    photo_fit.json and (apply, default) the fitted room and cameras into the scene. Read the details before the
    first call: the landmark names and request fields are there.

    Several photos of one room -> room size, openings and each photo's camera, by least squares over
    landmarks you pick in the photos (no SfM; < 1 s). Origin: south-west floor corner, x east, y north.
    Per photo give size, a rough `facing` (compass word) and pixels of what it shows:
    points `at`: corner:<sw|se|nw|ne>:<floor|ceiling>, <opening>:<start|end>:<top|bottom> (start = west
    end of a north/south wall, south end of an east/west wall), or a shared point name (declare it in
    `points`, fixing what you know, e.g. z); lines `along` (any pixel on the line, good when the corner is
    hidden): floor:<wall>, ceiling:<wall>, corner:<sw..> (vertical), <opening>:<start|end|top|bottom>.
    Declare openings (kind, wall). height (wall height, default 2.4) sets the scale when a ceiling landmark is
    seen; otherwise eye_height (camera height, default 1.4) does - give it if known. A seen door top
    (<door>:start|end:top) is held near door_top (2.0 m, null = off), and a shared point with z_sigma is a
    typical height (a counter 0.9 ±0.03): both hold the scale better than eye_height (`size_priors`).
    The result's `scale` lists what sets the size: `sources` [{by: depth | door top | typical height |
    eye height | ceiling (typical) | height (given), scale, range, used}] where scale = how many times the fitted
    room that source alone asks for (1.0 = agrees), `median`, `pm` and `outlier` (the one source apart while the
    others agree, also a "scale: ..." warning saying what to do). Aim for 6+
    measurements per photo and at least one landmark shared with another photo (a corner, an opening).
    `locate` turns pixels into floor positions afterwards (furniture feet at z=0, a table top at its height);
    give each its `object` (the scene object id) and the result's `objects` has each one's floor center, and
    `edge` front / back for a foot on the side it faces / its back (place_from_photos then knows the front).
    With out_dir = the scene folder, place_from_photos puts each object there, and status / compare_photo flag
    objects placed away from their located feet (photo_placement).
    Frames of a video: pass video_poses' `relative` (measured turns / moves between the frames) as well.
    Read `worst` and `warnings`: a large error usually means a mislabeled landmark (start/end swapped,
    wrong corner). Returns room (Room fields), cameras (PhotoCamera fields + yaw/pitch, rms, ±sigma),
    points, located, and `outside` for a camera fitted beyond a wall: verdict doorway (it looks in through an
    opening - a hallway frame) or wall (it sees through the wall: the wall is likely too far in; `wall_moved_out`
    is the fit with that wall moved out - about as good means fix that wall's landmarks). `inside` = photo ids
    whose camera stood in the room. Mark both edges of each opening: one edge alone leaves its width at the guess
    (warned), and an opening under 0.4 m wide is refitted with start / end swapped (`swapped`) to tell a swap. Writes photo_fit.json into out_dir (e.g. the scene folder) when given.
    depth=true (needs AGENTIC_SIM_WORLD_GEN_DEPTH_PYTHON, a venv with MoGe-2): give each photo its `image` path; every point /
    line gets the monocular depth at its pixel, and `surfaces` [{on: floor|ceiling|wall:<side>, px}] - pixels on a
    plain, unoccluded part of the floor / ceiling / a wall (5-10 per surface seen) - get one too. The depth sets
    the scale (no ceiling landmark needed). Each photo's depth scale is fitted (`depth_scale`, the model is
    off by up to ~20 % per photo); without relative poses a photo that sees only one or two walls cannot fix
    the room's size by depth alone. Pick only landmarks you can see (not hidden behind furniture).
    apply (default: when out_dir is a scene folder) writes the fitted room (size, the fitted openings, the height
    when fitted) and every photo's camera into that scene - the photo ids must be its input ids - and records them
    in the history and as assumptions (「写真から当てはめ」); no set_room / set_input needed. `applied` says what,
    and warnings name a camera inside an object or outside the room. apply=false only fits."""
    return service.fit_photos(request, out_dir, depth, apply)


@mcp.tool(structured_output=False)  # returns text + image
def extract_frames(video: str, out_dir: str, max_frames: int = 8, min_frames: int = 4, url: str | None = None,
                   title: str | None = None, author: str | None = None, license: str | None = None,
                   start_s: float | None = None, end_s: float | None = None,
                   times: list[float] | None = None, rotate: int | None = None) -> list[Any]:
    """Walk-through video -> a few sharp, different frames as photos (max_frames default 8, at most 12; start_s /
    end_s for one room). Writes <out_dir>/inputs/*.jpg with source sidecars (url/title/author/license needed like a
    photo's; in a scene they become photo inputs) and a contact sheet. Never use a video with people. times = exact
    frames (to move a weak frame); rotate 90/180/270 for a sideways phone video.

    Walk-through video of a room -> a few good frames to use as photos. Not evenly spaced: the
    best frame of each view where the picture changed a lot (sharpness normalized by contrast; blurred
    stretches dropped; sky / close-up / dark views down-weighted, see each frame's room score and notes), first frame
    always kept (it sets the room's orientation). start_s / end_s: only that part of the video (one
    room of a walk through several; look at the sheet and narrow it). max_frames (default 8, at most 12; cost grows with the
    count) / min_frames (4). Writes <out_dir>/inputs/<stem>_tNNN_NN.jpg, each with <stem>.source.json =
    the video's source (from <video stem>.source.json next to the video, or url/title/author/license
    here; required like a photo's) + video + time_s. If out_dir is a scene the frames become photo inputs
    (no camera yet); otherwise put the returned `inputs` into create_scene's spec.inputs. Returns the frames
    (id, path, time_s, sharpness, room, notes, why) and a contact sheet image. Do not use a video showing
    people: look at every frame; people_check says whether a detector ran (usually "none").
    times: write exactly the frames at these seconds instead of choosing (all of them; they replace the scene's
    frames of this video). Use it to move a frame (blurred, or one video_poses loses the track at): the same times
    with it moved; pick the new frame's landmarks.
    rotate (0/90/180/270, degrees clockwise): a phone video whose file has no rotation metadata comes out lying on
    its side. Look at the sheet: if the floor is not at the bottom, extract again with rotate (+90: floor on the
    right, +270: on the left, +180: upside down). None keeps the turn of this video's frames already in the scene.
    It is recorded in each frame's sidecar, so video_poses decodes the video turned the same way."""
    r = service.extract_frames(video, out_dir, max_frames, min_frames, url=url, title=title, author=author,
                               license=license, start_s=start_s, end_s=end_s, times=times, rotate=rotate)
    if not r.get("ok"):
        return [json.dumps(r, ensure_ascii=False)]
    with open(r["sheet"], "rb") as f:
        return [json.dumps(r, ensure_ascii=False), Image(data=f.read(), format="jpeg")]


@mcp.tool()
def video_poses(scene_dir: str, hfov_deg: float = 65.0, video: str | None = None) -> dict[str, Any]:
    """After extract_frames: the camera's turns (and move directions) between the frames, measured on the video.
    Put the returned `relative` into the fit_photos request. hfov_deg: the camera's field of view (phone ~65).

    After extract_frames: how the camera turned (and which way it moved) between the frames, measured from
    the video between them (ORB feature tracking with OpenCV, ~10-30 s). Returns `relative`: put it into the
    fit_photos request as it is. It ties the frames together, so a frame with few or far landmarks still gets
    its view direction, and fit_photos starts each camera from the measured turns. hfov_deg: the video
    camera's field of view (guess 65 for a phone, then run again with fit_photos' hfov if it differs by more
    than 5 degrees). Needs OpenCV (the `video` extra); without it fit from landmarks only. The camera's
    positions are not solved over the whole video: the frames are fitted as photos, from their landmarks."""
    r = service.video_poses(scene_dir, hfov_deg, video)
    return service.with_language(scene_dir, r)


@mcp.tool(structured_output=False)  # returns text + image
def compare_photo(scene_dir: str, input_id: str | None = None) -> list[Any]:
    """Render scene.usda from every input photo's camera (Isaac Sim, ~1 min) and return each photo beside its render.
    Compare room size, what is where and the viewpoint; fix objects / room / camera, compose_scene, compare again.
    `round` + `hint`: fix all differences of a round together, about 3 rounds, then report what still differs.

    Render scene.usda from the cameras of the input photos (Isaac Sim headless, ~1 min, +~10 s per
    extra photo) and return each photo and its render side by side, one row per photo (input_id: just
    that one). Compare the room size, what is where and the viewpoint; fix objects (set_layout /
    select_asset), the room (set_room) or a camera (set_input), then compose_scene and compare again.
    Run compose_scene first. layout.svg shows every photo camera (position and field of view).
    `round` counts the comparisons and `hint` says what to do next: fix every difference of one round
    together, about 3 rounds, then report what still differs."""
    r = service.compare_photo(scene_dir, input_id)
    if not r.get("ok"):
        return [json.dumps(service.with_language(scene_dir, r), ensure_ascii=False)]
    with open(r["image"], "rb") as f:
        return [json.dumps(service.with_language(scene_dir, r), ensure_ascii=False), Image(data=f.read(), format="png")]


@mcp.tool()
def make_variants(scene_dir: str, count: int = 4, seed: int = 0, vary: list[str] | None = None,
                  size_jitter: float = 0.1, compose: bool = True) -> dict[str, Any]:
    """Make `count` variations of a finished scene in <scene>/variants/NN/ (RL / synthetic data): vary any of
    assets (good candidates only), layout (along the wall), materials (presets usual for the place), size
    (±size_jitter, rectangles). Same seed, same variants; show variants/index.html.

    Make `count` variations of a finished scene, each a scene of its own in <scene>/variants/NN/ (for RL
    environments or synthetic data; SDG varies only lights / materials / cameras inside one scene). Per seed it
    changes: assets (among each object's first good candidates; substitutes and weak / off-place / off-size ones
    are never drawn), objects against a wall moved along it (relations such as a chair at its desk follow), floor
    and wall presets usual for Room.place (custom surfaces are kept), and the room size within ±size_jitter
    (rectangular rooms only). vary: any of "assets", "layout", "materials", "size" (default all). A draw with more
    layout issues than the source is drawn again; `more_issues` marks one that still has more. Each variant has
    scene.usda (compose=true) and LICENSES.md; show the user variants/index.html (open it in a browser, or the
    preview server's /<scene>/variants/index.html). Same seed, same variants; a new call replaces the folder."""
    r = service.make_variants(scene_dir, count, seed, vary, size_jitter, compose)
    return service.with_language(scene_dir, r)


@mcp.tool()
def place_from_photos(scene_dir: str, objects: list[str] | None = None, apply: bool = True,
                      outlines: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Place each object located in photo_fit.json (locate with `object`) by fitting its selected asset's footprint
    to its 2+ feet (center and facing; edge front / back sets the front); with `outlines` it is then refined in the
    photos. Clears its layout, records assumption 位置. `left` = the objects still to look at (loose /
    skipped); the others need no compare-and-move. objects: only these; apply=false reports only.

    outlines: [{object, photo, box: [u0, v0, u1, v1]}] - a rectangle drawn tightly around the whole object in a
    photo (pixels, origin top-left; a side at the image border = it runs out of the frame there). Give one for each
    object in 1-3 photos that show it whole or nearly, all objects in one call: the asset's box is then moved until
    it looks like that from the fitted cameras, which fixes most of what compare_photo + move_object would (in a
    simulation on 31 objects of 4 rooms the center came 0.17 m off instead of 0.30 m with feet alone). They are kept
    in photo_fit.json: a later call (after swapping an asset) uses them again. Each row has `px` (rms in the photos,
    `px_was` before refining); `loose` = the box does not fit its picks (the asset's size is wrong, or a pick is on
    something else). An object with outlines but fewer than 2 feet is refined from where it stands.

    Put the furniture where the photos show it: for each object located in the scene folder's photo_fit.json
    (fit_photos with out_dir = the scene folder and `locate` with `object`), the selected asset's footprint is
    fitted to its 2+ feet / base corners (least squares: center and facing). Its layout is cleared (position +
    facing_deg, kept when the asset is swapped) and an assumption 位置 (basis 写真から当てはめ) records it.
    Mark feet `edge`: front / back in locate to tell its front; without them the front faces the cameras that saw
    its front feet, else its facing now is kept (`facing_from`: points / cameras / kept / unknown; `ties` = the
    facings that fit alike). rms_m over 0.15 m = the asset's size is not the real one's (warned: set min/max_size
    and select another) or a point is on something else. Skips arrays, objects on something / hung / in an opening
    and one-point objects. objects: only these ids; apply=false only reports. Swap an asset first, then run this
    (the footprint is the selected asset's)."""
    return service.with_language(scene_dir, service.place_from_photos(scene_dir, objects, apply, outlines))


@mcp.tool(structured_output=False)  # returns text + image
def nav_map(scene_dir: str, robot_radius: float = 0.3, robot_height: float = 1.2, min_height: float = 0.05,
            resolution: float = 0.05, start: tuple[float, float] | None = None, isaac: bool = False) -> list[Any]:
    """2D navigation map for a robot (ROS / Nav2 map_server: nav/map.pgm + map.yaml) and where a robot of
    robot_radius / robot_height can drive from the doors (or start): reachable share, unreachable zones and
    objects, pockets, with a picture. isaac=true also uses Isaac Sim's Occupancy Map (needs compose_scene).
    Tell the user what is out of reach and widen the way.

    A 2D navigation map for a robot in ROS / Nav2 map_server format (<scene>/nav/map.pgm + map.yaml, in the
    stage's XY = the map frame) and where a robot of that size can drive. Occupied: walls (doors cut out) and
    objects whose height range overlaps [min_height, robot_height] (by bounding box: under a desk is blocked);
    outside the room is unknown. The robot (a circle of robot_radius) starts at the doors (or `start`, else the
    largest open area); returns how much of the floor it reaches, zones it cannot enter, floor objects it cannot
    get near and open pockets behind furniture, with a picture (dark green: where its center goes, light: what
    it sweeps, white: floor it never reaches, red rings: pockets, blue: start). Tell the user about anything out
    of reach that a robot should get to; widen the way with set_layout / move_object and run it again.
    isaac=true also makes the map from Isaac Sim's Occupancy Map (rays against the colliders, ~1 min, needs
    compose_scene): table legs instead of boxes, so a low robot drives under tables (nav/map_isaac.*,
    compare.png). Use the robot's own size when the user names one."""
    r = service.nav_map(scene_dir, robot_radius, robot_height, min_height, resolution, start, isaac)
    img = r.get("compare") or (r.get("files") or {}).get("png")
    if not img:
        return [json.dumps(service.with_language(scene_dir, r), ensure_ascii=False)]
    with open(img, "rb") as f:
        return [json.dumps(service.with_language(scene_dir, r), ensure_ascii=False), Image(data=f.read(), format="png")]


@mcp.tool()
def measure_photo(scene_dir: str, input_id: str | None = None,
                  floor_pairs: list[list[float]] | None = None,
                  known: list[dict[str, Any]] | None = None, aisle_pair: int | None = None) -> dict[str, Any]:
    """Compare sizes with a photo: free width across the camera's view (at it and 1-10 m ahead), what it sees left
    to right, notes. floor_pairs [[u1, v1, u2, v2(, z)], ...] = pixel pairs on the floor at one depth -> photo_m vs
    scene_m and ratio (> 1.2: the scene is too wide), with range_m [lo, hi]. Run after set_input, before compare_photo.

    Compare sizes with a photo, not only colors: from the photo's camera, the free width across the view
    at the camera (where it stands in the aisle: m to the left / right blocker) and 1-10 m ahead, what the camera
    sees left to right (pixel boxes in the photo's size, array units in view), and `notes`.
    floor_pairs: [[u1, v1, u2, v2], ...] pixels read off the photo on the floor at the same depth (the bottom of the
    left and right shelf fronts, the two sides of an aisle; a 5th value = floor height for a raised floor). Each
    gives the photo's metric distance (`photo_m`, from the camera's height and tilt) next to the scene's free width
    on the same line (`scene_m`, `ratio`). ratio > 1.2: the scene is too wide - move the shelves in or narrow the
    room (set_room / set_layout / set_array), then compose_scene and compare_photo again. Run after set_input
    (camera) and before compare_photo; compare_photo also returns this per photo.
    `photo_lines` (when the photo's file is there and OpenCV is installed): the camera's pitch / roll read
    off the photo's own vertical edges (`pitch_deg`, `roll_deg`, `horizon_v_px`), how far the camera as set is off
    (`camera_off_deg`) and the look_at height that matches (`look_at_z`: set it with set_input when the note says
    so). floor_pairs then use the photo's tilt (`tilt_from`). `range_m` = photo_m with the tilt off by +-2 deg
    (+-5 when it could not be read off the lines) and the camera height by +-10 %: a scene_m inside it is not a
    difference; when the notes call the range too loose, count shelf units / floor tiles across the aisle instead.
    Several estimates per pair: `estimates` lists the floor geometry (photo's tilt / camera as set), the
    photo's depth map when it has one, and your own count from `known` = [{"pair": 0, "m": 2.7, "from": "3 shelf
    units of 0.9 m"}] (pair = index in floor_pairs). `best_m` is their median, `outliers` names estimates more
    than 25 % from it (fix that source), `disagree` = two estimates apart (add a third), `single` = only one (a
    wrong one cannot be noticed: add a known size), `typical_m` / `typical_off` = the usual aisle of that kind of
    store. Resize the scene toward best_m (`scene_vs_best`), not toward a single estimate.
    `depth_floor` (when the photo has a depth map: estimate_layout depth=true): the floor plane fitted to
    the map's points, with no pixels from you - `camera_height_m` (the map's scale; next to `camera_height_set`),
    `pitch_deg`, and `across` = the free floor width 1-10 m ahead of the camera between the things standing on the
    floor (`width` in the map's scale with range_m, `width_at_set_height` rescaled to the camera height as set,
    `scene_width` on the same line; `open` = a side runs out of the image, `at_least` then; `on` = something on
    the center line). A note names a row that differs from the scene by more than 25 %: confirm it with
    floor_pairs or a known size before resizing. `ok: false` + `why` when no floor is seen.
    Known sizes, computed here: `known` also takes {"pair": 0, "kind": "shelf_unit", "count": 2.5} (so many
    across the pair; kinds shelf_unit 0.9 m, door 0.85, basket 0.48, cart 0.5, pallet 1.1, floor_tile 0.3 / 0.45 /
    0.6: give "size_m" for the tile or another size) and things seen in the photo {"kind": "door", "line": [u1, v1,
    u2, v2]} (its two ends on the floor; or "box": its pixel box when seen square-on; "count" units in the line;
    "pair" to use it for one pair only). Each thing is listed in `known` (`geometry_m`, `scale` = real size /
    geometry) and gives every pair a "known size" estimate (the camera's height cancels) with its own range;
    `scale.camera_height_m` is the height the things imply (set position's z when the note says so). Pick a thing
    seen across the view near the pair's depth: one running away from the camera or far in depth comes out
    `loose` and is not counted in best_m, like a tile whose size the geometry had to pick.
    aisle_pair = the index of the floor pair that spans an aisle: its best_m (+ range_m) becomes what the
    layout aims at instead of the kind of store's usual - recorded as the guess 「aisle」 (basis 写真の実測), set as
    the `aisle` of the array whose rows stand on both sides of the pair (rows of separate objects: move them
    yourself), and used by the usability check (a tighter real aisle
    lowers narrow_aisle's width, wide_aisle compares with the photo, and issue `photo_aisle` stays while the
    scene's width on that line is outside the range). `aisle` in the result: m, range_m, arrays changed, and
    scene_m / ratio on that line before (`ratio_was`) and after; status `photo_aisle` keeps reporting the ratio.
    Not applied when the user stated the aisle (`kept`). Pass it once the pair's estimates agree."""
    return service.with_language(scene_dir, service.measure_photo(scene_dir, input_id, floor_pairs, known, aisle_pair))


@mcp.tool(structured_output=False)  # returns text + image
def generate_data(scene_dir: str, sdg: SdgSpec | None = None, dry_run: bool = False) -> list[Any]:
    """Generate an annotated synthetic dataset with Replicator (Isaac Sim headless; ~1 min start-up
    plus ~1 s per frame). sdg replaces the saved settings (frames, resolution, seed, annotators, writers
    basic/coco/kitti, camera ranges and targets, randomize). Recomposes scene.usda first. Returns
    per-run checks (frames written, boxes per class, warnings) and a contact sheet (RGB + boxes,
    semantic segmentation). dry_run returns only the planned camera poses (no Isaac Sim)."""
    r = service.generate_data(scene_dir, sdg, dry_run=dry_run)
    if not r.get("image"):
        return [json.dumps(service.with_language(scene_dir, r), ensure_ascii=False)]
    with open(r["image"], "rb") as f:
        return [json.dumps(service.with_language(scene_dir, r), ensure_ascii=False), Image(data=f.read(), format="jpeg")]


@mcp.tool()
def tool_help(name: str, model: str | None = None) -> str:
    """The full notes and argument fields of one agentic-sim-world-gen tool: what a description ending with (More: ...) left
    out. model: one of the nested types the result names (Room, ObjectSpec, Layout ...), for its fields.
    Ask for the one tool you are about to call; do not read tools.md."""
    return mcp_schema.section(mcp._tool_manager, name, model)


mcp_schema.compact(mcp._tool_manager)  # smaller tool list; validation keeps the full models


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
