"""Operations shared by the CLI and the MCP server.

A "scene" is a directory holding scene.resolved.json plus its previews
(candidates.html, layout.svg) and, after compose, scene.usda. Every mutating
operation saves the JSON and re-renders the previews, then returns `status()`.
All return values are JSON-friendly dicts so they can go straight to an agent.
"""

from __future__ import annotations

import contextlib
import contextvars
import dataclasses
import hashlib
import json
import logging
import math
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from agentic_sim_world_gen import UA, isaac_worker, missing
from agentic_sim_world_gen.assets import cards
from agentic_sim_world_gen.assets.providers.base import AssetProvider, fits, size_filter_for
from agentic_sim_world_gen.assets.domain import off_place
from agentic_sim_world_gen.assets.sizes import kind_of, off_size
from agentic_sim_world_gen.assets.record import AssetRecord, wants_box_collider
from agentic_sim_world_gen.assets.rerank import rerank
from agentic_sim_world_gen.layout.solver import solve
from agentic_sim_world_gen.layout.validate import check
from agentic_sim_world_gen.scene.spec import Array, Assumption, Fill, InputRef, Layout, LayoutOption, ObjectSpec, Placement, Requirement, ResolvedScene, SceneSpec, Screen, SdgSpec, STORE_PLACES, record_from_dict, record_to_dict

RESOLVED = "scene.resolved.json"
DEFAULT_CANDIDATES = 6
DEFAULT_SOURCES = "simready-central,isaac,polyhaven"

log = logging.getLogger(__name__)

_provider: AssetProvider | None = None


def get_provider() -> AssetProvider:
    """Default: SimReady Central, then the Isaac Sim bundled-asset index, then Poly Haven's CC0 models (offline
    indexes, skipped when missing). AGENTIC_SIM_WORLD_GEN_SOURCES selects/orders the sources (see source_names;
    +local:/path/to/usd adds a folder to the defaults; local:<dir> = third-party USD folder, see agentic_sim_world_gen.assets.providers.local_folder;
    polyhaven = agentic_sim_world_gen.assets.providers.polyhaven, files fetched when composed)."""
    global _provider
    if _provider is None:
        from agentic_sim_world_gen.assets.providers.composite import CompositeProvider
        from agentic_sim_world_gen.assets.providers.isaac_index import IsaacIndexProvider
        from agentic_sim_world_gen.assets.providers.simready_central import SimReadyCentralProvider

        names = source_names()
        from agentic_sim_world_gen.assets.providers.local_folder import LocalFolderProvider
        from agentic_sim_world_gen.assets.providers.polyhaven import PolyHavenProvider

        make = {"simready-central": SimReadyCentralProvider, "isaac": IsaacIndexProvider, "polyhaven": PolyHavenProvider}
        unknown = {n for n in names if n not in make and not n.startswith("local:")}
        if unknown:
            raise ValueError(f"unknown AGENTIC_SIM_WORLD_GEN_SOURCES {sorted(unknown)}; choose from {sorted(make)} or local:<dir>")
        providers = [LocalFolderProvider(n[len("local:"):]) if n.startswith("local:") else make[n]() for n in names]
        _SOURCES["unavailable"] = [n for n, p in zip(names, providers) if not getattr(p, "available", True)]
        providers = [p for p in providers if getattr(p, "available", True)]
        _provider = providers[0] if len(providers) == 1 else CompositeProvider(providers)
    return _provider


_SOURCES: dict[str, Any] = {}


def source_names(value: str | None = None) -> list[str]:
    """AGENTIC_SIM_WORLD_GEN_SOURCES as a list. `default` stands for the default sources at that place, and an entry
    written `+name` adds to the defaults instead of replacing them: `+local:/data/usd` = that folder first, then
    simready-central, isaac and polyhaven (a tie in the re-rank goes to the earlier source)."""
    raw = os.environ.get("AGENTIC_SIM_WORLD_GEN_SOURCES", DEFAULT_SOURCES) if value is None else value
    items = [n.strip() for n in raw.split(",") if n.strip()]
    default = DEFAULT_SOURCES.split(",")
    plus = any(n.startswith("+") for n in items)
    out: list[str] = []
    for n in items:
        for m in default if n == "default" else [n.lstrip("+").strip()]:
            if m and m not in out:
                out.append(m)
    if plus and "default" not in items:
        out += [m for m in default if m not in out]
    return out


def sources_view() -> dict[str, Any]:
    """The asset sources in use: the set AGENTIC_SIM_WORLD_GEN_SOURCES=local:<dir>,simready-central,isaac and
    lost Poly Haven's beds and sofas without knowing; the agent then searched it from Python outside the tools."""
    get_provider()
    if "custom" in _SOURCES:
        return {"used": [_SOURCES["custom"]]}
    used = source_names()
    out: dict[str, Any] = {"used": used}
    if _SOURCES.get("unavailable"):
        out["unavailable"] = _SOURCES["unavailable"]  # index missing or folder not found
    left = [m for m in DEFAULT_SOURCES.split(",") if m not in used]
    if left:
        out["left_out"] = left
        out["note"] = (f"AGENTIC_SIM_WORLD_GEN_SOURCES leaves out the default {', '.join(left)}. Do not search them outside the "
                       "agentic-sim-world-gen tools (nothing would reach the history, preview or choices.jsonl): tell the user, "
                       "who can write AGENTIC_SIM_WORLD_GEN_SOURCES=+local:<dir> (= the defaults plus that folder) and restart")
    return out


def set_provider(p: AssetProvider | None) -> None:
    """Swap the asset source (tests, or a different provider)."""
    global _provider
    _provider = p
    _SOURCES.clear()
    if p is not None:
        _SOURCES["custom"] = getattr(p, "name", type(p).__name__)


# --- helpers ----------------------------------------------------------------


def _resolved_path(scene: str | Path) -> Path:
    p = Path(scene).expanduser()
    return p / RESOLVED if p.is_dir() or p.suffix != ".json" else p


def load(scene: str | Path) -> ResolvedScene:
    path = _resolved_path(scene)
    if not path.exists():
        raise FileNotFoundError(f"{path} not found; create the scene first")
    return ResolvedScene.load(path)


def _placement(scene: ResolvedScene, object_id: str) -> Placement:
    for p in scene.placements:
        if p.object_id == object_id:
            return p
    raise KeyError(f"unknown object id '{object_id}'; ids: {[p.object_id for p in scene.placements]}")


PICTURE_PENALTY = 1.0  # match score an asset loses when its own picture is made for another place
HEAVY_FACES_SMALL = 500_000  # a candidate up to SMALL_M long with more faces goes after lighter ones
HEAVY_FACES = 2_000_000  # and a bigger one
SMALL_M = 1.0


def heavy_faces(r: AssetRecord) -> int | None:
    """The asset's face count when it is far more than its size needs (a 2-million-face scanned ball, a 2.1-million
    face woven basket that put the souvenir shop over decor.DRAW_BUDGET), else None."""
    if r.polygons is None:
        return None
    small = max(r.size_m or (0.0,)) <= SMALL_M
    return r.polygons if r.polygons > (HEAVY_FACES_SMALL if small else HEAVY_FACES) else None


def _wrong_picture(r: AssetRecord, o: ObjectSpec, place: str | None) -> bool:
    """The asset would show its own picture (lit screen, printed sign) made for another kind of place."""
    from agentic_sim_world_gen.layout.validate import SHOP_PLACES, place_fits

    if place is None or not r.screens:
        return False
    x = r.screens[0]
    vset, on = x.get("variant_set"), x.get("on")
    if vset and on is not None and o.variants.get(vset, on) != on:
        return False
    return not place_fits(place, x.get("places") or list(SHOP_PLACES))


SEARCH_WORKERS = 4  # searches of one call sent at once; AGENTIC_SIM_WORLD_GEN_SEARCH_WORKERS, 1 = one after another
_FOUND: contextvars.ContextVar[dict | None] = contextvars.ContextVar("agentic_sim_world_gen_found", default=None)


def _search_key(q: str, limit: int, size) -> tuple:
    return (q, limit, dataclasses.astuple(size) if size is not None else None)


def _search(q: str, limit: int, size) -> list[AssetRecord]:
    found = _FOUND.get()
    if found is not None and (got := found.get(_search_key(q, limit, size))) is not None:
        return got
    return get_provider().search(q, limit=limit, size=size)


@contextlib.contextmanager
def _searching(objs: list[ObjectSpec], n: int):
    """Send the searches _find_candidates will make for these objects at once, and hand it the answers:
    30 objects with 3 queries each are 90 round trips to SimReady Central, 18 of the 61 s of a create_scene.
    The first search runs alone (a local folder builds its index on it). A search that fails here is simply made
    again where it is used, which raises or reports as before."""
    try:
        workers = int(os.environ.get("AGENTIC_SIM_WORLD_GEN_SEARCH_WORKERS", SEARCH_WORKERS))
    except ValueError:
        workers = SEARCH_WORKERS
    todo: dict[tuple, tuple] = {}
    for o in objs:
        size = size_filter_for(o.min_size, o.max_size)
        for q in [o.query, *o.alt_queries]:
            todo.setdefault(_search_key(q, n * 4, size), (q, n * 4, size))
    if workers < 2 or len(todo) < 2 or _FOUND.get() is not None:
        yield
        return
    from concurrent.futures import ThreadPoolExecutor

    provider = get_provider()

    def one(args):
        try:
            return provider.search(args[0], limit=args[1], size=args[2])
        except Exception:  # noqa: BLE001
            return None

    keys = list(todo)
    found = {keys[0]: one(todo[keys[0]])}
    with ThreadPoolExecutor(min(workers, len(keys) - 1)) as ex:
        found.update(zip(keys[1:], ex.map(one, [todo[k] for k in keys[1:]])))
    token = _FOUND.set({k: v for k, v in found.items() if v is not None})
    try:
        yield
    finally:
        _FOUND.reset(token)


def _find_candidates(o: ObjectSpec, n: int, place: str | None = None,
                     room_h: float | None = None) -> list[dict[str, Any]]:
    """Search query + alt_queries, merge (dedupe by id), size-filter, re-rank by metadata match.

    Each candidate dict gets a "match" entry: {score, matched, weak}. weak = no query's head noun
    names the asset in class/tags/caption, i.e. probably a substitute. Weak hits are dropped when at
    least one good match exists (a desk search should not list sofas); when none does, the weak ones
    are kept as substitutes (Placement.substitute). With Room.place, assets whose own picture is made
    for another kind of place (a bookstore's checkout sign in a drugstore) go after the others, and so do
    assets made for another line of work (a hospital bed in a home; match.off_place names it).
    Without min_size / max_size, assets far from the kind's typical size (assets.sizes: a pasta box for a
    "cardboard box") go after all of those (match.off_size names the dimensions). Assets with far more faces
    than their size needs go after lighter ones of the same standing (match.heavy = the face count).
    With room_h (the ceiling), assets whose top would be above it go after all the others (match.too_tall = the
    asset's height; a 2.7 m glass facade hung 0.4 m up for a shop window under a 2.7 m ceiling).
    """
    size = size_filter_for(o.min_size, o.max_size)
    seen: dict[str, tuple[int, int, AssetRecord]] = {}  # id -> (rank, query index, record)
    for qi, q in enumerate([o.query, *o.alt_queries]):
        for rank, r in enumerate(_search(q, n * 4, size)):
            if fits(r.size_m, o.min_size, o.max_size) and (r.id not in seen or (rank, qi) < seen[r.id][:2]):
                seen[r.id] = (rank, qi, r)
    pool = [r for _, _, r in sorted(seen.values(), key=lambda x: (x[0], x[1]))]
    ranked = rerank(pool, o.query, o.alt_queries)
    if any(not m.weak for _, m in ranked):
        ranked = [(r, m) for r, m in ranked if not m.weak]
    queries = [o.query, *o.alt_queries]
    kind = kind_of(o.query, o.alt_queries) if o.min_size is None and o.max_size is None else None
    ranked = [(r, m, off_place(r, place, queries), off_size(r, kind)) for r, m in ranked]
    tall = {r.id for r, *_ in ranked if room_h is not None and _bottom(o) + r.size_m[2] * _stretch(o, r) > room_h + 0.01}
    # stable: keeps the match order otherwise. The wrong size goes last: a lit screen can be turned off and a
    # hospital bed is still a bed, but a pasta box is no cardboard box at all. A picture for another place
    # only costs PICTURE_PENALTY of the match score: it can be blanked, so it loses to an about-as-good asset
    # but not to a much worse one (a convenience-store gondola in a liquor store was behind a kitchen rack)
    # A scan with millions of faces is still the thing asked for, so it only goes after the lighter ones that are
    # as right (same size and place standing)
    # An asset that failed its own drop test is still the thing: behind the ones that did not, like the heavy
    ranked.sort(key=lambda x: (x[0].id in tall, x[3] is not None, x[2] is not None,
                               cards.physics_issue(cards.of(x[0])) is not None, heavy_faces(x[0]) is not None,
                               -(x[1].score - PICTURE_PENALTY * _wrong_picture(x[0], o, place))))
    out = []
    for r, m, off, bad_size in ranked[:n]:
        d = record_to_dict(r)
        d["match"] = {"score": m.score, "matched": m.matched, "weak": m.weak}
        if (faces := heavy_faces(r)) is not None:
            d["match"]["heavy"] = faces
        if card := cards.brief(cards.of(r)):
            d["match"]["card"] = card
        if off:
            d["match"]["off_place"] = off
        if bad_size:
            d["match"]["off_size"] = {"kind": kind, **bad_size}
        if r.id in tall:
            d["match"]["too_tall"] = round(r.size_m[2], 2)
        out.append(d)
    return out


def _size_misses(o: ObjectSpec, cands: list[dict[str, Any]], n: int = 3) -> list[dict[str, Any]]:
    """When every candidate is weak, look for good matches the size limits dropped (so the caller can
    widen min/max_size instead of settling for a substitute)."""
    if (o.min_size is None and o.max_size is None) or any(not (c.get("match") or {}).get("weak", True) for c in cands):
        return []
    seen: dict[str, AssetRecord] = {}
    for q in [o.query, *o.alt_queries]:
        for r in get_provider().search(q, limit=n * 4):
            if not fits(r.size_m, o.min_size, o.max_size):
                seen.setdefault(r.id, r)
    out = []
    for r, m in rerank(list(seen.values()), o.query, o.alt_queries):
        if not m.weak and len(out) < n:
            out.append({"name": r.name, "caption": r.caption, "size_m": [round(v, 2) for v in r.size_m],
                        "off": size_off(r.size_m, o.min_size, o.max_size),
                        "match": {"score": m.score, "matched": m.matched, "weak": m.weak}})
    return out


def size_off(size: tuple[float, float, float], lo: tuple[float, float, float] | None,
             hi: tuple[float, float, float] | None) -> dict[str, str]:
    """Which limits an asset misses and by how much, e.g. {"height": "+16% over max 1.2 m"}
    (size and limits compared as footprint long, short side, height, like fits())."""
    dims = sorted(size[:2], reverse=True) + [size[2]]
    out = {}
    for name, v, mn, mx in zip(("long", "short", "height"), dims, lo or (None,) * 3, hi or (None,) * 3):
        if mx is not None and v > mx > 0:
            out[name] = f"+{(v / mx - 1) * 100:.0f}% over max {mx:g} m"
        elif mn is not None and v < mn and mn > 0:
            out[name] = f"-{(1 - v / mn) * 100:.0f}% under min {mn:g} m"
    return out


def _stretch(o: ObjectSpec, r: AssetRecord) -> float:
    """How much taller a window / curtain gets when it is stretched to its opening (1 for anything else):
    its min_size height over the asset's, never below 1 (the solver fits it in, FIT_RANGE)."""
    if not (o.opening or o.covers) or o.min_size is None or not r.size_m[2]:
        return 1.0
    return max(1.0, o.min_size[2] / r.size_m[2])


def _bottom(o: ObjectSpec) -> float:
    """Where the object's bottom is known to be before it is placed: hung at layout.wall_height, or at z."""
    if o.layout is not None and o.layout.wall_height is not None:
        return o.layout.wall_height
    return o.z if o.opening is None and o.covers is None else 0.0


def _placement_for(o: ObjectSpec, n: int, place: str | None = None, room_h: float | None = None) -> Placement:
    cands = _find_candidates(o, n, place, room_h)
    return Placement(object_id=o.id, candidates=cands, size_misses=_size_misses(o, cands))


SNAPSHOTS = 30  # saved versions kept per scene for restore
SNAP_DIR = ".history"  # <scene>/.history/<version>.json.gz, one per logged save


def versions(scene_dir: Path) -> list[int]:
    """The versions restore can go back to, oldest first (the last is the scene as it is now)."""
    out = []
    for f in (scene_dir / SNAP_DIR).glob("*.json.gz"):
        try:
            out.append(int(f.name.split(".")[0]))
        except ValueError:
            pass
    return sorted(out)


def _snap_path(scene_dir: Path, version: int) -> Path:
    return scene_dir / SNAP_DIR / f"{version:06d}.json.gz"


def _snapshot(scene_dir: Path, have: list[int]) -> int:
    """Keep the saved scene.resolved.json as the next version; the oldest beyond SNAPSHOTS go."""
    import gzip

    v = (have[-1] if have else 0) + 1
    path = _snap_path(scene_dir, v)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(gzip.compress((scene_dir / RESOLVED).read_bytes(), 6))
    tmp.replace(path)
    for old in (have + [v])[:-SNAPSHOTS]:
        _snap_path(scene_dir, old).unlink(missing_ok=True)
    return v


def version_rows(scene_dir: Path) -> list[dict[str, Any]]:
    """The restorable versions with the history line each was saved by, oldest first."""
    from agentic_sim_world_gen.preview.page import STATE

    try:
        hist = json.loads((scene_dir / STATE).read_text()).get("history") or []
    except (OSError, ValueError):
        hist = []
    text = {e["version"]: e.get("text") for e in hist if e.get("version")}
    vs = versions(scene_dir)
    return [{"version": v, "text": text.get(v) or ("これより前の状態" if v == vs[0] else ""),
             **({"current": True} if v == vs[-1] else {})} for v in vs]


def restore(scene: str | Path, version: int | None = None) -> dict[str, Any]:
    """Go back to a saved version of the scene: every logged save keeps one (the last SNAPSHOTS), listed
    in the change history of the preview and by version_rows. version None = the one before the latest (undo;
    again = redo, since the restore is itself a save). The notes the user wrote on the preview stay as they are
    now. The objects, the room and the assumptions come back exactly: no automatic fix or new decor runs."""
    import gzip

    d = _resolved_path(scene).parent
    rows = version_rows(d)
    vs = [r["version"] for r in rows]
    if version is None:
        if len(vs) < 2:
            raise ValueError("nothing to undo: no earlier version of this scene is kept")
        version = vs[-2]
    if version not in vs:
        have = "; ".join(f"{r['version']}: {r['text']}" for r in rows[-10:]) or "none"
        raise ValueError(f"version {version} is not kept (the last {SNAPSHOTS} are); kept: {have}")
    now = load(scene)
    s = ResolvedScene.model_validate(json.loads(gzip.decompress(_snap_path(d, int(version)).read_bytes())))
    s.spec.notes = now.spec.notes
    text = (next(r["text"] for r in rows if r["version"] == version) or "").removeprefix(_who_preview(""))
    st = _save(s, d, f"「{text}」の時点に戻す" if text else "前の状態に戻す", fix=False)
    st["restored"] = int(version)
    return st


def _save(scene: ResolvedScene, scene_dir: Path, event: str | None = None, focus: str | None = None,
          decor_for: set[str] | None = None, touched: set[str] | None = None,
          windows_missing: list[dict[str, Any]] | None = None, fix: bool = True) -> dict[str, Any]:
    """Solve, save and re-render the previews; `event` is the line shown on preview.html ("what happened").
    decor_for: objects added or given another asset; with Room.density set they get their decor now.
    touched: the objects this call placed, when more than the focus: the automatic fixes leave them.
    fix=False: save the scene as given (restore): no retried decor, no automatic fix.
    Every save with an event keeps a version for restore."""
    from agentic_sim_world_gen.preview import page
    from agentic_sim_world_gen.preview.candidates import write_html
    from agentic_sim_world_gen.preview.layout2d import write_svg

    _ensure_shelves(scene)
    solve(scene)  # objects with `layout` get position/yaw/z from their relations
    if fix and event is not None and scene.spec.decor_skipped and (more := _retry_decor(scene)):
        event += more  # the host of some left-out decor is fixed now
        solve(scene)
    if event is not None and decor_for and (more := _decor_for(scene, decor_for)):
        event += more
        solve(scene)
    fixed = (_auto_fix(scene, (touched or set()) | ({focus} if focus else set()))
             if fix and event is not None and auto_fix_on(scene) else [])
    if fixed:
        event += _record_fixes(scene, fixed)
    _auto_assumptions(scene)
    scene_dir.mkdir(parents=True, exist_ok=True)
    version = None
    if event is not None:
        have = versions(scene_dir)
        if not have and (scene_dir / RESOLVED).exists():  # a scene from an older version: keep where it was, for undo
            have = [_snapshot(scene_dir, have)]
    scene.save(scene_dir / RESOLVED)
    if event is not None:
        version = _snapshot(scene_dir, have)
    write_html(scene, scene_dir / "candidates.html")
    write_svg(scene, scene_dir / "layout.svg")
    _write_options(scene, scene_dir)
    st = status(scene_dir)
    page.write(scene, scene_dir, st["issues"],
               None if event is None else {"text": _who(event), "focus": focus, "running": _running.get(scene_dir.resolve()),
                                           "version": version, **_by()},
               usability=st["usability"], taste=st.get("taste"))
    if event is not None:
        missing.record(scene_dir, scene, st, windows_missing)  # what the sources did not have
    if fixed:
        st["auto_fixed"] = fixed
        st["auto_fixed_hint"] = ("the server made these fixes itself (each removed its issue and added none): tell "
                                 "the user in a line; move it back by hand if they want it as it was "
                                 "(move_object correction=true keeps it there)")
    return st


def _ensure_shelves(scene: ResolvedScene) -> None:
    """Find the shelf boards of every fixture something is stocked on, and of a shop's display fixtures (once
    per selected asset; kept in the candidate so the solver needs no USD)."""
    import agentic_sim_world_gen.assets.shelves as sh
    from agentic_sim_world_gen.layout.usability import shelf_fixtures

    targets = {o.fill.on for o in scene.spec.objects if o.fill is not None} | shelf_fixtures(scene)
    for p in scene.placements:
        if p.object_id in targets and p.candidates:
            c = p.candidates[p.selected]
            # scan once; again when an older detector did it (it found no hooks, maybe no boards)
            if c.get("shelves") is None or c.get("shelves_v", sh.VERSION) != sh.VERSION:
                c["shelves_v"] = sh.VERSION
                try:  # one read of the USD: both share the scan's cache
                    c["shelves"] = sh.find_shelves(c["uri"], _cache_dir())
                except Exception as e:  # unreadable asset: report as "no shelf boards"
                    c["shelves"], c["hooks"] = c.get("shelves") or [], []
                    c["shelves_error"] = str(e)
                    continue
                try:
                    c["hooks"] = sh.find_hooks(c["uri"], _cache_dir())
                except Exception:
                    c["hooks"] = []


def _hooks_view(hooks: list[dict]) -> dict[str, Any]:
    """A fixture's hooks for status: rows (lowest first) and how many are free (fill.hang)."""
    from agentic_sim_world_gen.layout.fill import hook_rows

    rows = hook_rows(hooks)
    return {"rows": [{"z": r[0]["z"], "hooks": len(r), "free": sum(1 for k in r if not k["busy"])} for r in rows]}


def _cache_dir() -> Path:
    return Path(os.environ.get("AGENTIC_SIM_WORLD_GEN_CACHE", "~/.cache/agentic-sim-world-gen")).expanduser()


# Who is changing the scene: None = the agent (MCP / CLI), "preview" = the user on the live preview page.
# Kept in the history so the agent sees the user's edits on its next turn (status `preview_edits`).
ACTOR: contextvars.ContextVar[str | None] = contextvars.ContextVar("agentic_sim_world_gen_actor", default=None)

_running: dict[Path, str] = {}  # scene dir -> long step in progress (a check), kept on the page across other events


def _by() -> dict[str, str]:
    return {"by": a} if (a := ACTOR.get()) else {}


def _who_preview(text: str) -> str:
    return f"プレビューで操作：{text}"


def _who(text: str | None) -> str | None:
    return _who_preview(text) if text and ACTOR.get() == "preview" else text


FEEDBACK_EVENT = "仮定 "  # start of the history line for feedback on an assumption
NOTE_EVENT = "書き込み "  # start of the history line for a note written on the preview


def preview_edits(scene_dir: Path) -> list[dict[str, Any]]:
    """What the user changed on the preview page since the agent's last step (newest last)."""
    from agentic_sim_world_gen.preview.page import STATE

    try:
        hist = json.loads((scene_dir / STATE).read_text()).get("history") or []
    except (OSError, ValueError):
        return []
    out = []
    for e in reversed(hist):
        if e.get("by") != "preview":
            break
        if (e.get("text") or "").startswith((_who_preview(FEEDBACK_EVENT), _who_preview(NOTE_EVENT))):
            continue  # feedback on an assumption / a note: listed in `assumption_feedback` / `notes` instead
        out.append({"text": e.get("text"), "object": e.get("focus"), "changes": e.get("changes") or []})
    return out[::-1]


def _event(scene: str | Path, text: str, focus: str | None = None, busy: bool = False, level: str | None = None) -> None:
    """Update preview.html for a step that does not change the scene (viewing candidates, compose, check)."""
    from agentic_sim_world_gen.preview import page

    try:
        s = load(scene)
        d = _resolved_path(scene).parent
        page.write(s, d, _issues(s), {"text": _who(text), "focus": focus, "busy": busy, "level": level,
                                      "running": _running.get(d.resolve()), **_by()}, usability=_usability(s),
                   taste=_taste(s, d))
    except Exception:  # the preview must never break the operation itself
        pass


def _issues(s: ResolvedScene) -> list[dict[str, Any]]:
    return [{"kind": i.kind, "objects": list(i.objects), "detail": i.detail} for i in solve(s) + check(s)]


def _decor_skipped_view(s: ResolvedScene) -> dict[str, Any]:
    """Decor Room.density left out, for the agent's report; blocked ones come back by themselves."""
    from agentic_sim_world_gen.scene import decor

    ids = {o.id for o in s.spec.objects}
    rows = [k.model_dump(exclude_defaults=True) for k in s.spec.decor_skipped if k.host is None or k.host in ids]
    if not rows:
        return {}
    out: dict[str, Any] = {"decor_skipped": rows}
    if any(r.get("blockers") for r in rows):
        out["decor_hint"] = ("decor_skipped with blockers: the host's own problems took every spot. Fix them "
                             "(move / set_layout); the item is added again by itself on that save. Tell the user "
                             "what was left out and why (no_asset = not in the catalog, crowded = too many bodies or faces, heavy = only scanned assets of ~1 M faces)")
    stocked = {o.query for o in s.spec.objects if o.decor and o.fill is not None}
    missing = [q for q in s.spec.room.goods if q not in stocked]
    if missing and any(r["reason"] == "no_asset" for r in rows):  # the shop's goods are not in the catalog
        out["decor_goods_hint"] = (f"goods not stocked (no asset naming {', '.join(sorted(decor.goods_words(s.spec.room)))}): "
                                   f"{', '.join(missing)}. Try other English words or a local: folder in set_room(goods=), "
                                   "tell the user what is missing; do not stock unrelated products by hand")
    if any(r["reason"] == "no_kind" for r in rows):
        from agentic_sim_world_gen.scene.spec import STORE_PLACES

        out["decor_kind_hint"] = ("no_kind: the shop has no kind, so nothing says what its fixtures hold. "
                                  f"set_room(place=<one of {', '.join(STORE_PLACES)}>) to stock them by kind; for "
                                  "another kind, set_room(goods=[<English product queries>], goods_words=[<kind>]) "
                                  "(sporting goods: soccer ball, tennis racket, sports shoes); or "
                                  "set_fill(on=<fixture>, query=<product>) for what the user asked")
    return out


def _draw_view(s: ResolvedScene) -> dict[str, Any]:
    """Faces the scene draws, when over decor.DRAW_BUDGET: Isaac Sim runs out of memory and is killed."""
    from agentic_sim_world_gen.scene import decor

    rows = _drawn(s)
    total = sum(r[0] for r in rows)
    if total <= decor.DRAW_BUDGET:
        return {}
    return {"draw_load": {"faces": total, "budget": decor.DRAW_BUDGET,
                          "heaviest": [{"object": oid, "copies": n, "faces_each": f} for _, oid, n, f in rows[:5]]},
            "draw_hint": (f"the scene draws {total / 1e6:.0f} M faces (budget {decor.DRAW_BUDGET / 1e6:.0f} M): "
                          "check_in_isaac would run out of memory. Swap the heaviest stock for a lighter candidate "
                          "(select_asset; faces_each over 100 k is a scan) or stock fewer rows (set_fill rows=1)")}


def _window_view(s: ResolvedScene) -> dict[str, Any]:
    """Windows (or a glass door) in a room with nothing outside: they show a gray void in the renders."""
    room = s.spec.room
    if room.outdoor or room.site is not None:
        return {}
    seen = [o.id for o in room.openings if o.kind == "window" or o.style == "auto_sliding"]
    if not seen:
        return {}
    home = room.place in (None, "home") or (room.place or "").endswith(("cafe", "restaurant", "bakery"))
    return {"window_view": {"openings": seen},
            "window_view_hint": ("window_view: nothing is outside the room, so its windows show a gray void. "
                                 "To show ground and sky, set_room(site=true) (asphalt and sun"
                                 + ("; a house or a log cabin: site={\"ground\": \"grass\"}" if home else "")
                                 + "); leave it if the windows are not seen")}


SHOP_HINTS = {
    "empty_shelves": "empty_shelves: stock them (set_room(density='normal' or 'lived_in') in a shop of a kind, or "
                     "set_fill with a product); boards no product fits stay empty, say so",
    "empty_floor": "empty_floor: add fixtures there (another row: set_array count / rows, or add_objects), or make "
                   "the room smaller with set_room; a patch by the door may stay free",
    "wide_aisle": "wide_aisle: set_array(aisle=<the place's aisle width, or the photo's when one was measured>) or "
                  "move the rows closer",
}


def _usability_hint(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """What to do next about a shop's empty shelves / floor."""
    kinds = {r["kind"] for r in rows}
    hints = [h for k, h in SHOP_HINTS.items() if k in kinds]
    return {"usability_hint": hints} if hints else {}


def _usability(s: ResolvedScene) -> list[dict[str, Any]]:
    """Usability warnings: not errors; the layout works but is awkward to use."""
    from agentic_sim_world_gen.layout.usability import usability

    try:
        return [{"kind": i.kind, "objects": list(i.objects), "detail": i.detail} for i in usability(s)]
    except Exception as e:  # a warning pass must never break status
        return [{"kind": "usability_error", "objects": [], "detail": f"{type(e).__name__}: {e}"}]


def _taste_pools(s: ResolvedScene, d: Path, search: bool = False) -> dict[str, list[dict[str, Any]]]:
    """The wider search per object that the taste check measures against, from d/taste_pool.json.
    Objects whose query / size limits changed since are left out (stale), unless search=True: then they are
    searched again (taste.POOL matches, as _find_candidates ranks them) and the file is rewritten."""
    from agentic_sim_world_gen import taste

    f = d / taste.POOL_FILE
    try:
        saved = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    out, changed = {}, False
    stale = [o for it in taste.items_of(s) if (o := s.obj(it.id)) and (saved.get(it.id) or {}).get("key") != taste.pool_key(o)]
    with _searching(stale if search else [], taste.POOL):
        for it in taste.items_of(s):
            o = s.obj(it.id)
            key = taste.pool_key(o)
            e = saved.get(it.id)
            if e and e.get("key") == key:
                out[it.id] = e["candidates"]
            elif search:
                out[it.id] = _find_candidates(o, taste.POOL)
                saved[it.id] = {"key": key, "query": o.query, "candidates": out[it.id]}
                changed = True
    if changed:
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
        tmp.replace(f)
    return out


def _taste(s: ResolvedScene, d: Path) -> dict[str, Any] | None:
    """Taste warnings from cached thumbnail embeddings and the saved pools only (the model is loaded
    and the pools are searched by check_taste)."""
    from agentic_sim_world_gen import taste

    try:
        r = taste.report(s, cache_only=True, pools=_taste_pools(s, d))
    except Exception as e:  # a warning pass must never break status
        return {"error": f"{type(e).__name__}: {e}"}
    return None if r.get("note") else r  # fewer than 3 objects: nothing to say


def check_taste(scene: str | Path, embedder: Any = None) -> dict[str, Any]:
    """Search a wider pool per object (query + alt_queries, taste.POOL matches; kept in taste_pool.json), embed
    its thumbnails (the image model is optional; skipped with the reason when it is missing) and report the
    selected assets that do not go with the rest, with assets of the same kind that fit the others
    better. Suggested assets from the pool are added to that object's candidates (at the end, so view_candidates
    and select_asset see them); the selection is never changed. status shows the same from the caches."""
    from agentic_sim_world_gen import taste

    s = load(scene)
    d = _resolved_path(scene).parent
    if embedder is None:
        embedder, why = taste.load_embedder()
        if embedder is None:
            _event(scene, f"テイストの確認：スキップ（{why}）")
            return {"taste": {"skipped": why}}
    _running[d.resolve()] = "テイストの確認中（候補を広く検索して画像埋め込み）"
    _event(scene, "テイストの確認中（候補を広く検索して画像埋め込み）", busy=True)
    try:
        pools = _taste_pools(s, d, search=True)
        r = taste.report(s, embedder, cache_only=False, pools=pools)
        added = _add_taste_alternatives(s, r, pools)
        if added:  # the candidates changed, the selection did not
            from agentic_sim_world_gen.preview.candidates import write_html

            s.save(_resolved_path(scene))
            write_html(s, d / "candidates.html")
    finally:
        _running.pop(d.resolve(), None)
    n = len(r.get("outliers") or [])
    _event(scene, f"テイストの確認：外れている物 {n} 件" if n else "テイストの確認：外れている物なし",
           (r["outliers"][0]["object"] if n else None), level=None if n else "ok")
    return {"taste": r}


def _add_taste_alternatives(s: ResolvedScene, r: dict[str, Any], pools: dict[str, list[dict[str, Any]]]) -> int:
    """Append the suggested pool assets to their object's candidates and give the suggestion its index."""
    n = 0
    for row in r.get("outliers") or []:
        p = _placement(s, row["object"])
        for a in row.get("alternatives") or []:
            if a.get("index") is None:
                a["index"] = _add_pool_candidate(p, pools.get(row["object"]) or [], a["uri"])
                a.pop("uri", None)
                n += 1
    return n


def _add_pool_candidate(p: Placement, pool: list[dict[str, Any]], uri: str) -> int:
    """Index of the candidate with this uri, appending it from the taste pool or an earlier search_assets result
 when it is not there yet."""
    uris = [c.get("uri") for c in p.candidates]
    if uri in uris:
        return uris.index(uri)
    c, origin = next((c for c in pool if c.get("uri") == uri), None), "taste"
    if c is None:
        c, origin = _SEARCHED.get(uri), "search"
    if c is None:
        raise KeyError(f"'{p.object_id}' has no candidate, taste suggestion or search_assets result with uri {uri}; "
                       "search for it with search_assets or research_object first")
    p.candidates.append({**c, "from": origin})
    return len(p.candidates) - 1


def _name(s: ResolvedScene, object_id: str) -> str:
    o = s.obj(object_id)
    return o.label or o.id


def _short(c: dict[str, Any], i: int) -> dict[str, Any]:
    r = record_from_dict(c)
    return {
        "index": i,
        "name": r.name,
        "caption": r.caption,
        "size_m": [round(v, 2) for v in r.size_m],
        "license": r.license,
        **({"screens": [x["material"] for x in r.screens]} if r.screens else {}),
        "match": c.get("match"),
    }


def _source_errors() -> dict[str, Any]:
    """Search sources that failed since the last report (e.g. SimReady Central unreachable)."""
    errs = getattr(_provider, "errors", None)
    if not errs:
        return {}
    out = {"source_errors": list(dict.fromkeys(errs))}
    errs.clear()
    return out


# --- read -------------------------------------------------------------------


def search_assets(
    query: str,
    limit: int = 8,
    min_size: tuple[float, float, float] | None = None,
    max_size: tuple[float, float, float] | None = None,
    alt_queries: list[str] | None = None,
) -> list[dict[str, Any]]:
    o = ObjectSpec(id="q", query=query, alt_queries=alt_queries or [], min_size=min_size, max_size=max_size)
    found = _find_candidates(o, limit)
    for c in found:  # select_asset(uri=) takes these too
        _SEARCHED.pop(c.get("uri"), None)
        _SEARCHED[c.get("uri")] = c
    while len(_SEARCHED) > SEARCHED_MAX:
        _SEARCHED.pop(next(iter(_SEARCHED)))
    return [{**record_from_dict(c).summary(), "match": c.get("match")} for c in found]


# The assets search_assets returned in this process, by uri (newest last), for select_asset(uri=).
_SEARCHED: dict[str, dict[str, Any]] = {}
SEARCHED_MAX = 500


def _room_view(room) -> dict[str, Any]:
    """Room for status(): the plan itself is replaced by a summary (wall ids, faces, zones)."""
    from agentic_sim_world_gen.layout.room import RoomGeometry

    d = room.model_dump(exclude={"plan", "openings"})
    if room.plan is not None or room.openings or room.site is not None:
        d["layout"] = RoomGeometry(room).summary()
    return d


def _facing_view(o, p) -> dict[str, Any]:
    """World facing of the selected asset's front, and where that front is in the asset's own frame
    (so a raw yaw_deg can be read: facing = yaw_deg + asset_front_deg)."""
    a = p.asset
    if a is None:
        return {}
    from agentic_sim_world_gen.layout.frame import facing_of, front_local_deg, norm_deg

    return {"facing_deg": round(norm_deg(facing_of(a, o)), 1), "asset_front_deg": round(front_local_deg(a, o), 1)}


NOT_IN_CATALOG_CHOICES = ["代用（近いものを使う）", "手持ちの USD（local: フォルダ）", "やめる（置かない）"]


def kind_availability(queries: list[str]) -> dict[str, Any] | None:
    """How many assets of the queries' kind each source has: the kind table for SimReady Central,
    the records themselves for the Isaac index and local: folders. None when no source can tell."""
    from agentic_sim_world_gen.assets import kinds

    try:
        return kinds.availability(queries, get_provider())
    except Exception as e:  # the table is a hint; never break a status over it
        log.warning("kind availability failed: %s", e)
        return None


def _substitute_view(o: ObjectSpec, p: Placement) -> dict[str, Any]:
    """Every candidate is weak. When the kind table also has none of this kind, say so on the first result so the
    caller asks the user once (substitute / own USD / drop it) instead of searching again with more synonyms."""
    out: dict[str, Any] = {"sources": getattr(get_provider(), "name", None)}
    if left := sources_view().get("left_out"):
        out["left_out_sources"] = left  # a default source not searched may have the real thing
    cat = kind_availability([o.query, *o.alt_queries])
    if cat and cat["absent"]:
        built = (cat.get("table") or {}).get("built")
        out["hint"] = (f"not in the catalog: no searched source has a '{cat['kind']}' at all (kind table"
                       f"{f' built {built}' if built else ''}; the Isaac index and local: folders checked in full). "
                       "Do not search again with other synonyms. Ask the user once which of `choices` they want: "
                       "the closest substitute (pick it with view_candidates and call it 代用), their own USD (add "
                       "local:<dir> to AGENTIC_SIM_WORLD_GEN_SOURCES, then research_object), or drop the object (remove_object).")
        out["not_in_catalog"] = True
        out["choices"] = NOT_IN_CATALOG_CHOICES
    elif cat and any(cat["counts"].values()):
        out["hint"] = ("no candidate names this kind, but the sources do have some ("
                       + ", ".join(f"{s} {n}" for s, n in cat["counts"].items() if n) + " assets match "
                       f"'{cat['kind']}'): check size_misses and widen min_size/max_size, or research_object once with "
                       "the plain kind word; if that still finds none, use the closest and call it 代用.")
    else:
        out["hint"] = ("no asset in the searched sources is this kind of object; every candidate is a substitute. "
                       "Try other synonyms with research_object, else pick the closest with view_candidates and tell "
                       "the user it is a substitute (代用). A local: folder (AGENTIC_SIM_WORLD_GEN_SOURCES) can add the real thing.")
    if cat:
        out["catalog"] = cat
    return out


def status(scene: str | Path) -> dict[str, Any]:
    path = _resolved_path(scene)
    s = ResolvedScene.load(path)
    objects = []
    for p in s.placements:
        o = s.obj(p.object_id)
        objects.append(
            {
                "id": o.id,
                "label": o.label,
                "query": o.query,
                "position": o.position,
                "yaw_deg": o.yaw_deg,
                **_facing_view(o, p),
                "z": o.z,
                "layout": o.layout.model_dump(exclude_defaults=True) if o.layout else None,
                "array": o.array.model_dump() if o.array else None,
                **({"fill": {**o.fill.model_dump(exclude={"slots"}), "count": len(o.fill.slots)}} if o.fill else {}),
                **({"hooks": _hooks_view(p.asset.hooks)} if p.asset is not None and p.asset.hooks else {}),
                "selected": p.selected if p.candidates else None,
                "candidates": [_short(c, i) for i, c in enumerate(p.candidates)],
            }
        )
        if o.decor:
            objects[-1]["decor"] = True
        if p.asset is not None and wants_box_collider(o.collider, p.asset):
            objects[-1]["collider"] = ("box: the asset has no physics, a static box collider is added" if o.collider == "auto"
                                       else "box (set_collider)")
        elif o.collider == "none":
            objects[-1]["collider"] = "none (set_collider)"
        if p.asset is not None and p.asset.screens:
            objects[-1]["screens"] = [x["material"] for x in p.asset.screens]
        if o.screen is not None:
            objects[-1]["screen"] = o.screen.model_dump(exclude_none=True)
        if o.tint:
            objects[-1]["tint"] = [t.model_dump(exclude_none=True) for t in o.tint]
        for k in ("opening", "covers", "fit_scale"):
            if getattr(o, k) is not None:
                objects[-1][k] = getattr(o, k)
        if p.substitute:
            objects[-1]["substitute"] = _substitute_view(o, p)
        if p.size_misses:
            objects[-1]["size_misses"] = {
                "hint": "all candidates are weak, but these good matches fall outside min_size/max_size "
                "(off: which limit and by how much); widen the limits with research_object if one would do",
                "assets": p.size_misses,
            }
    d = path.parent
    usd = d / "scene.usda"
    from agentic_sim_world_gen.inputs import missing_source

    inputs = []
    for i in s.spec.inputs:
        v = i.model_dump(mode="json", exclude_none=True)
        if miss := missing_source(i):
            v["missing"] = miss
        inputs.append(v)
    return {
        "scene_dir": str(d),
        "room": _room_view(s.spec.room),
        **({"inputs": inputs} if inputs else {}),
        **({"sdg": s.spec.sdg.model_dump(mode="json")} if s.spec.sdg else {}),
        "objects": objects,
        "issues": _issues(s),
        "usability": (u := _usability(s)),
        **_usability_hint(u),
        **_decor_skipped_view(s),
        **_draw_view(s),
        **_window_view(s),
        "sources": sources_view(),
        **({"taste": t} if (t := _taste(s, d)) else {}),
        **_assumption_view(s),
        **_request_view(s),
        "previews": {"page": str(d / "preview.html"), "candidates": str(d / "candidates.html"),
                     "layout": str(d / "layout.svg")},
        "usd": str(usd) if usd.exists() else None,
        **({"preview_edits": edits, "preview_edits_hint": "the user moved / turned / swapped these on the preview "
            "page since your last step; keep them (do not re-run a layout over them) unless asked"}
           if (edits := preview_edits(d)) else {}),
        **({"photo_placement": pp} if (pp := _photo_placement(s, d)) else {}),
        **_compare_state(s, d),
        **_picture_view(s, d),
        **_feedback_view(s),
        **_mood_view(s),
        **_purpose_view(s, d),
        **_photo_aisle_view(s),
        **_notes_view(s),
        **_options_view(d),
        **_nav_view(d),
        **_check_view(d),
        **_source_errors(),
        **_conflicts_view(s),
        **_lang_view(s),
    }


OPTIONS_DIR = "layout_options"  # <scene>/layout_options/{<name>.svg, summary.json} while options are open


def _option_scene(s: ResolvedScene, opt: LayoutOption) -> ResolvedScene:
    c = s.model_copy(deep=True)
    c.spec.layout_options = []
    _apply_option(c, opt)
    return c


def _with_decor(s: ResolvedScene, ids: set[str]) -> set[str]:
    """The ids plus the decor resting on / beside them."""
    gone = set(ids)
    while more := {o.id for o in s.spec.objects if o.decor and o.id not in gone and gone & _refs(o)}:
        gone |= more
    return gone


def _apply_option(s: ResolvedScene, opt: LayoutOption) -> None:
    """Make the scene the option: its room size (what is placed by position moves in proportion), without
    the objects it leaves out, with its placements."""
    room = s.spec.room
    if opt.room and room.width and room.depth:
        w, dp = opt.room.get("width", room.width), opt.room.get("depth", room.depth)
        fx, fy = w / room.width, dp / room.depth
        for o in s.spec.objects:
            if o.position is not None and o.layout is None:
                o.position = (round(o.position[0] * fx, 3), round(o.position[1] * fy, 3))
        openings = [op.model_copy(update={"offset": round(op.offset * (fx if op.wall.lower()[:1] in "sn" else fy), 3)})
                    for op in room.openings]
        s.spec.room = type(room).model_validate({**room.model_dump(), "width": w, "depth": dp,
                                                 "openings": [op.model_dump() for op in openings]})
    if opt.without:
        _drop(s, _with_decor(s, {i for i in opt.without if any(o.id == i for o in s.spec.objects)}))
    for oid, patch in opt.objects.items():
        if any(o.id == oid for o in s.spec.objects):
            patch.apply(s.obj(oid))


def _rate_option(c: ResolvedScene) -> dict[str, Any]:
    """What an option leaves to fix: layout issues, usability warnings, request items not met."""
    out: dict[str, Any] = {"issues": len(_issues(c)), "usability": len(_usability(c))}
    if c.spec.requirements:
        from agentic_sim_world_gen.layout.request import evaluate, summary

        try:
            out["requirements_unmet"] = summary(evaluate(c))["unmet"]
        except Exception:
            out["requirements_unmet"] = None
    return out


def _best(rated: list[dict[str, Any]]) -> str:
    return min(rated, key=lambda r: (r["issues"], r.get("requirements_unmet") or 0, r["usability"]))["name"]


def _write_options(s: ResolvedScene, d: Path) -> None:
    """Solve each layout option on a copy, rate it and draw its plan; nothing left when there are none."""
    from agentic_sim_world_gen.preview.layout2d import write_svg

    out = d / OPTIONS_DIR
    if not s.spec.layout_options:
        shutil.rmtree(out, ignore_errors=True)
        return
    out.mkdir(exist_ok=True)
    rated = []
    for opt in s.spec.layout_options:
        c = _option_scene(s, opt)
        r = {"name": opt.name, "label": opt.label, **({"note": opt.note} if opt.note else {}), **_rate_option(c)}
        if s.spec.options_axis:
            r |= {"axis": s.spec.options_axis, **_draft_words(c)}
        elif s.spec.contrast and s.spec.contrast.step:
            r |= {"contrast": s.spec.contrast.step.rstrip("+-"), **_draft_words(c)}
        write_svg(c, out / f"{opt.name}.svg")
        rated.append(r)
    # Drafts: on a tie the middle one, not the first (the smallest room)
    best = _best([rated[1], rated[0], *rated[2:]] if s.spec.options_axis else rated)
    for r in rated:
        r["best"] = r["name"] == best and "contrast" not in r  # a contrast is a question, not a recommendation
    keep = {f"{r['name']}.svg" for r in rated} | {"summary.json"}
    for f in out.iterdir():
        if f.name not in keep:
            f.unlink()
    (out / "summary.json").write_text(json.dumps(rated, ensure_ascii=False, indent=1), encoding="utf-8")


def _draft_words(c: ResolvedScene) -> dict[str, Any]:
    """What a draft option is, in numbers: the room and how much stands on its floor."""
    room = c.spec.room
    things = [o for o in c.spec.objects if not o.decor and o.fill is None and o.opening is None]
    out: dict[str, Any] = {"objects": sum(_units(o) for o in things)}
    if room.width and room.depth:
        out["room"] = f"{room.width:g} × {room.depth:g} m"
    return out


DRAFT_AXES = ("size", "amount", "composition")  # in the order of how much they change the result
AXIS_JA = {"size": "広さ", "amount": "物の量", "composition": "構成", None: "配置"}
DRAFT_BASIS = "叩き台 3 案から選択"
DRAFT_SCALES = (("small", "小さめ", 0.8), ("middle", "中くらい", 1.0), ("large", "大きめ", 1.25))
# words in a recorded assumption's item that say the axis is decided
_AXIS_WORDS = {"size": ("size", "広さ", "寸法", "畳", "面積"),
               "amount": ("amount", "density", "物の量", "密度", "people", "seats", "人数", "席数", "台数", "count", "racks"),
               "composition": ("composition", "構成", "room kind", "meeting", "eat-in", "line", "site", "desk layout")}


def draft_axes(s: ResolvedScene) -> list[dict[str, Any]]:
    """The draft axes with whether the user already decided each: a given assumption row for the room whose
    item names it, a floor plan (the size), or - for the size - the room's size question answered in the request."""
    given = [a.item.strip().lower() for a in s.spec.assumptions if a.given and a.object is None and not a.auto]
    out = []
    for axis in DRAFT_AXES:
        said = next((it for it in given if any(w in it for w in _AXIS_WORDS[axis])), None)
        why = f"given: {said}" if said else None
        if axis == "size" and why is None and (s.spec.room.plan is not None or s.spec.room.outdoor):
            why = "the room comes from a floor plan" if s.spec.room.plan is not None else "an outdoor site"
        out.append({"axis": axis, "label": AXIS_JA[axis], "decided": why})
    return out


def _size_options(s: ResolvedScene) -> list[LayoutOption]:
    """Three room sizes far enough apart to tell: the place's size choices (plan_questions) when three of them
    read as W × D, else the room as it is and 0.8x / 1.25x of each side (0.64x / 1.56x the floor)."""
    from agentic_sim_world_gen.scene.questions import options_for

    room = s.spec.room
    sizes = []
    for text in options_for(room.place, "size"):
        if m := re.search(r"(\d+(?:\.\d+)?)\s*[×x]\s*(\d+(?:\.\d+)?)\s*m", text):
            sizes.append((float(m.group(1)), float(m.group(2))))
    cur = (room.width, room.depth)
    area = lambda wd: wd[0] * wd[1]  # noqa: E731
    if len(sizes) >= 3 and any(abs(area(z) - area(cur)) < 0.05 * area(cur) for z in sizes):
        sizes = sorted(sizes, key=area)
        i = min(range(len(sizes)), key=lambda k: abs(area(sizes[k]) - area(cur)))
        i = min(max(i, 1), len(sizes) - 2)  # the current one in the middle when it can be
        picked = sizes[i - 1:i + 2]
        if (cur[0] >= cur[1]) != (picked[0][0] >= picked[0][1]):
            picked = [(b, a) for a, b in picked]
    else:
        picked = [(round(cur[0] * k, 1), round(cur[1] * k, 1)) for _, _, k in DRAFT_SCALES]
    return [LayoutOption(name=n, label=f"{ja}（{w:g} × {dp:g} m）", room={"width": w, "depth": dp})
            for (n, ja, _), (w, dp) in zip(DRAFT_SCALES, picked)]


def propose_drafts(scene: str | Path, axis: str | None = None,
                   options: list[LayoutOption | dict[str, Any]] | None = None) -> dict[str, Any]:
    """Three drafts that differ on purpose in ONE thing the user did not say, shown as plans only (no Isaac
    Sim) for the user to pick: axis = size (the room), amount (how much stands in it) or composition (which things).
    axis left out: the first one in that order the user has not decided (`draft_axes`).
    size needs no options: the service makes small / middle / large. amount / composition take 3 options, each
    with `without` (ids left out: the scene holds everything of the fullest option), `objects` (array counts,
    placements) and / or `room`. They are kept as layout options: the preview shows them side by side,
    choose_layout(name) keeps one and records the axis as an assumption (given, 「叩き台 3 案から選択」)."""
    s = load(scene)
    axes = draft_axes(s)
    if axis is None:
        axis = next((a["axis"] for a in axes if not a["decided"]), None)
        if axis is None:
            raise ValueError("the user decided the size, the amount and the composition: nothing to offer drafts on")
    if axis not in DRAFT_AXES:
        raise ValueError(f"unknown axis {axis!r}; one of {', '.join(DRAFT_AXES)}")
    if options:
        opts = [o if isinstance(o, LayoutOption) else LayoutOption.model_validate(o) for o in options]
    elif axis == "size":
        if s.spec.room.plan is not None or s.spec.room.outdoor or not (s.spec.room.width and s.spec.room.depth):
            raise ValueError("size drafts need a rectangular room (not a floor plan or an outdoor site): pass another axis")
        opts = _size_options(s)
    else:
        raise ValueError(
            f"axis {axis!r} needs 3 options from you: the scene as it is must hold everything of the fullest one; "
            "each option = {name, label, note, without: [ids it leaves out], objects: {id: {array / layout ...}}}")
    if len(opts) != 3:
        raise ValueError("propose 3 drafts")
    names = [o.name for o in opts]
    if len(set(names)) != len(names) or "best" in names:
        raise ValueError("option names must be unique and not 'best'")
    ids = {o.id for o in s.spec.objects}
    for o in opts:
        if o.room and (s.spec.room.plan is not None or s.spec.room.outdoor):
            raise ValueError("an option's room size needs a rectangular room")
        if bad := [i for i in [*o.without, *o.objects] if i not in ids]:
            raise ValueError(f"option '{o.name}': unknown object(s) {bad}")
    same = lambda a, b: (a.room, sorted(a.without), a.objects) == (b.room, sorted(b.without), b.objects)  # noqa: E731
    if any(same(a, b) for i, a in enumerate(opts) for b in opts[i + 1:]):
        raise ValueError("two drafts are the same: each must differ in the room, what it leaves out or the placement")
    s.spec.layout_options, s.spec.options_axis = opts, axis
    if s.spec.contrast:
        s.spec.contrast.step = s.spec.contrast.other = None
    st = _save(s, _resolved_path(scene).parent,
               f"叩き台を 3 案作成（{AXIS_JA[axis]}が違う）：" + "／".join(o.label for o in opts))
    st["draft_axes"] = axes
    return st


CONTRAST_AXES = ("size", "amount", "mood", "layout")  # the order they are offered in
CONTRAST_JA = {"size": "広さ", "amount": "物の量", "mood": "雰囲気", "layout": "配置の型"}
CONTRAST_BASIS = "見本の二択で選択"
CONTRAST_SCALE = {"+": 1.25, "-": 0.8}  # each side of the room, like the drafts


def _contrast_steps(s: ResolvedScene) -> list[dict[str, Any]]:
    """The comparisons in the order they are offered, with what stands in the way of each (`needs`): the size the
    way the room more likely fails first (bigger when the layout has issues or usability warnings, else smaller),
    fewer things, the look, the arrangement, then the size the other way."""
    from agentic_sim_world_gen.scene import moods as md

    room = s.spec.room
    first = "+" if (_issues(s) or _usability(s)) else "-"
    rect = None if (room.plan is None and not room.outdoor and room.width and room.depth) else \
        "a rectangular room (not a floor plan or an outdoor site)"
    cur = s.spec.mood.name if s.spec.mood else None
    others = [] if room.outdoor else [m for m in md.for_place(room.place) if m.name != cur]
    decor = any(o.decor for o in s.spec.objects)
    steps = [("size" + first, rect),
             ("amount-", None if decor else "option: {label, without: [ids left out]} (nothing was added for density)"),
             ("mood", None if others else "an indoor room of a place with sample looks"),
             ("layout", "option: {label, objects: {id: {layout / array / position / facing_deg / zone}}} - another "
                        "type of arrangement of the same things"),
             ("size" + ("-" if first == "+" else "+"), rect),
             ("amount+", "option: {label, objects: {id: {array: {count}}}} (more of what is there)")]
    tried = set(s.spec.contrast.tried) if s.spec.contrast else set()
    return [{"step": k, "axis": k.rstrip("+-"), "label": CONTRAST_JA[k.rstrip("+-")], "tried": k in tried,
             **({"needs": n} if n else {})} for k, n in steps]


def propose_contrast(scene: str | Path, axis: str | None = None, direction: str | None = None,
                     option: LayoutOption | dict[str, Any] | str | None = None) -> dict[str, Any]:
    """After a 「なんか違う」 with no reason: the scene as it is next to one that differs in ONE thing, for the
    user to say which is closer - two or three rounds find what was wrong without the user putting it into words.
    axis: size (a room 1.25x / 0.8x each side, made here), amount (fewer things: without what density added, made
    here; or your option), mood (another look's sample picture; option = its name), layout (your option: another
    type of arrangement). Left out: the next step not answered yet (`contrast.steps`). direction: "more" / "less"
    for size and amount. The pair is shown like layout options (preview 「どちらが近いですか」); choose_contrast
    takes the answer."""
    from agentic_sim_world_gen.scene import moods as md
    from agentic_sim_world_gen.scene.spec import Contrast

    s = load(scene)
    d = _resolved_path(scene).parent
    if s.spec.layout_options and not (s.spec.contrast and s.spec.contrast.step):
        raise ValueError("layout options are waiting for a pick: choose_layout first (or propose_layouts([]))")
    steps = _contrast_steps(s)
    if axis is not None and axis not in CONTRAST_AXES:
        raise ValueError(f"unknown axis {axis!r}; one of {', '.join(CONTRAST_AXES)}")
    if direction not in (None, "more", "less"):
        raise ValueError("direction is 'more' or 'less'")
    sign = {"more": "+", "less": "-", None: None}[direction]
    if axis is None:
        step = next((x for x in steps if not x["tried"] and ("needs" not in x or (option is not None and x["needs"].startswith("option")))), None)
        if step is None:
            left = [x for x in steps if not x["tried"]]
            raise ValueError("no comparison left that can be made here" + (
                ": " + "; ".join(f"{x['step']} needs {x['needs']}" for x in left) if left else
                " (every step was answered): ask the user in words what differs"))
    else:
        cands = [x for x in steps if x["axis"] == axis and (sign is None or x["step"].endswith(sign) or axis in ("mood", "layout"))]
        step = next((x for x in cands if not x["tried"]), cands[0])
    key = step["step"]
    if "needs" in step and not (option is not None and step["needs"].startswith("option")):
        raise ValueError(f"the {step['axis']} comparison needs {step['needs']}")
    ja = CONTRAST_JA[step["axis"]]
    con = s.spec.contrast or Contrast()
    con.step, con.other = key, None
    s.spec.contrast = con
    room = s.spec.room
    if step["axis"] == "mood":
        looks = [m for m in md.for_place(room.place) if m.name != (s.spec.mood.name if s.spec.mood else None)]
        other = next((m for m in looks if m.name == option), None) if isinstance(option, str) else looks[0]
        if other is None:
            raise ValueError(f"no mood {option!r}; one of {', '.join(m.name for m in looks)}")
        con.other = other.name
        s.spec.layout_options, s.spec.options_axis = [], None
        st = _save(s, d, f"二択の見本を作成（{ja}だけが違う）：今のまま／{other.label}")
    else:
        if isinstance(option, str):
            raise ValueError("option is a layout option ({label, without / objects / room}) for this axis")
        if option is not None:
            raw = option.model_dump() if isinstance(option, LayoutOption) else dict(option)
            opt = LayoutOption.model_validate({**raw, "name": "other"})
            ids = {o.id for o in s.spec.objects}
            if bad := [i for i in [*opt.without, *opt.objects] if i not in ids]:
                raise ValueError(f"option: unknown object(s) {bad}")
            if not (opt.room or opt.without or opt.objects):
                raise ValueError("the option changes nothing")
        elif step["axis"] == "size":
            k = CONTRAST_SCALE[key[-1]]
            w, dp = round(room.width * k, 1), round(room.depth * k, 1)
            opt = LayoutOption(name="other", label=f"{'広め' if k > 1 else '狭め'}（{w:g} × {dp:g} m）", room={"width": w, "depth": dp})
        else:  # amount-: without what density added
            opt = LayoutOption(name="other", label="物を少なく（小物なし）", without=[o.id for o in s.spec.objects if o.decor])
        now = LayoutOption(name="now", label="今の案" + (f"（{room.width:g} × {room.depth:g} m）" if step["axis"] == "size" else ""))
        s.spec.layout_options, s.spec.options_axis = [now, opt], None
        st = _save(s, d, f"二択の見本を作成（{ja}だけが違う）：{now.label}／{opt.label}")
    st["contrast"] = {"step": key, "axis": step["axis"], "label": ja, **_contrast_done(s)}
    if step["axis"] == "mood":
        cur = s.spec.mood.name if s.spec.mood else None
        st["contrast"] |= {
            "now": {"label": md.get(cur, room.place).label if cur else "今のまま",
                    "image": str(f) if cur and (f := md.image(room.place, cur)) else None},
            "other": {"name": other.name, "label": other.label, "note": other.note,
                      "image": str(f) if (f := md.image(room.place, other.name)) else None}}
        st["contrast_hint"] = ("show the other look's sample picture (and the current one's, or the scene as it is) "
                               "and ask which is closer - nothing else in that message; choose_contrast('now' | "
                               "'other' | 'neither')")
    return st


def _contrast_done(s: ResolvedScene) -> dict[str, Any]:
    left = [x["step"] for x in _contrast_steps(s) if not x["tried"]]
    return {"tried": list(s.spec.contrast.tried), "left": left}


def choose_contrast(scene: str | Path, pick: str) -> dict[str, Any]:
    """The user's answer to propose_contrast: "now" (the scene as it is is closer), "other" (the changed one: it is
    applied) or "neither". now / other are recorded as the assumption of that axis (given, 「見本の二択で選択」);
    the step is not offered again. `contrast.found` is the axis when the changed one was closer."""
    s = load(scene)
    d = _resolved_path(scene).parent
    con = s.spec.contrast
    if con is None or not con.step:
        raise ValueError("no comparison is open; propose_contrast first")
    if pick not in ("now", "other", "neither"):
        raise ValueError("pick is 'now', 'other' or 'neither'")
    key, axis = con.step, con.step.rstrip("+-")
    ja = CONTRAST_JA[axis]
    basis = CONTRAST_BASIS + ("（プレビュー）" if ACTOR.get() == "preview" else "")
    opts = {o.name: o for o in s.spec.layout_options}
    other_mood = con.other
    con.step, con.other = None, None
    con.tried = [*[k for k in con.tried if k != key], key]
    s.spec.layout_options, s.spec.options_axis = [], None
    value, extra = None, {}
    if axis == "mood":
        from agentic_sim_world_gen.scene import moods as md

        if pick == "other":
            _save(s, d)
            extra = {k: v for k, v in apply_mood(scene, other_mood).items() if k in ("mood_furniture", "overwrote")}
            s = load(scene)
            value = md.get(other_mood, s.spec.room.place).label
        elif pick == "now":
            value = "今のまま" + (f"（{md.get(s.spec.mood.name, s.spec.room.place).label}）" if s.spec.mood else "")
    elif pick == "other":
        _apply_option(s, opts["other"])
        value = opts["other"].label
    elif pick == "now":
        value = "今のまま" + (f"（{s.spec.room.width:g} × {s.spec.room.depth:g} m）" if axis == "size" and s.spec.room.width else "")
    if value is not None:
        row = Assumption(item=ja, value=value, given=True, basis=basis)
        words = _AXIS_WORDS.get(axis, ())
        s.spec.assumptions = [a for a in s.spec.assumptions if a.key() != row.key() and not (
            a.object is None and not a.given and not a.auto and any(w in a.item.lower() for w in words))] + [row]
    said = {"now": "今の案", "other": value or "変えた案", "neither": "どちらでもない"}[pick]
    st = _save(s, d, f"二択の見本（{ja}）：{said}")
    done = _contrast_done(s)
    st["contrast"] = {"axis": axis, "picked": pick, "found": axis if pick == "other" else None, **done, **extra}
    st["contrast_hint"] = (
        f"the {axis} was what differed: it is applied and recorded. Show the result (finish(draft=true) or the plan) "
        "and ask if it is closer now; still 「違う」 → propose_contrast again" if pick == "other" else
        (f"not the {axis}" + (" this way" if key[-1] in "+-" else "") + ": propose_contrast again for the next step "
         f"({done['left'][0]})" if done["left"] else
         "every comparison was answered: ask in words what differs (or show a reference picture)"))
    return st


def _options_view(d: Path) -> dict[str, Any]:
    try:
        rated = json.loads((d / OPTIONS_DIR / "summary.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if rated and rated[0].get("contrast"):
        return {"layout_options": rated, "layout_options_hint":
                f"two plans that differ only in {rated[0]['contrast']} (preview 「どちらが近いですか」): show both "
                "in one line each and ask which is closer - nothing else in that message. choose_contrast('now' | "
                "'other' | 'neither') with the answer (a pick on the page shows in preview_edits)"}
    if rated and rated[0].get("axis"):
        return {"layout_options": rated, "layout_options_hint":
                f"three drafts that differ in {rated[0]['axis']} (preview 「叩き台 3 案」), plans only: show the "
                "three in one short line each (label + room / objects) and ask which is closest - no other question "
                "in that message. choose_layout(name) with the pick (a pick on the page shows in preview_edits); "
                "「どれも違う」→ ask what differs and change the scene, then propose_layouts([]) to drop them. Left to "
                "you → choose_layout('best'). The 3D draft (finish(draft=true)) comes after the choice"}
    return {"layout_options": rated, "layout_options_hint":
            "layouts proposed for the user to pick (preview 「配置案」): show them in one short line each and ask which "
            "one; call choose_layout(name) with their pick (or the user picks on the page and status shows the "
            "choice in preview_edits). If the user leaves it to you (おまかせ), choose_layout('best'). Do not "
            "compose before one is chosen"}


def propose_layouts(scene: str | Path, options: list[LayoutOption | dict[str, Any]]) -> dict[str, Any]:
    """Offer 2-4 layouts to choose from before generating. Each option changes only how objects are
    placed (layout / array / position / facing / zone) and shares the assets; objects not named stay as they
    are. The preview shows the plans side by side with their issue counts. An empty list withdraws them."""
    s = load(scene)
    opts = [o if isinstance(o, LayoutOption) else LayoutOption.model_validate(o) for o in options]
    if opts and not 2 <= len(opts) <= 4:
        raise ValueError("propose 2-4 layouts")
    names = [o.name for o in opts]
    if len(set(names)) != len(names) or "best" in names:
        raise ValueError("option names must be unique and not 'best'")
    for o in opts:
        for oid in o.objects:
            _placement(s, oid)
    if any(o.room or o.without for o in opts):
        raise ValueError("room / without belong to draft options: propose_drafts")
    s.spec.layout_options, s.spec.options_axis = opts, None
    if s.spec.contrast:
        s.spec.contrast.step = s.spec.contrast.other = None
    text = f"配置案を {len(opts)} 件作成：" + "／".join(o.label for o in opts) if opts else "配置案を取り下げ"
    st = _save(s, _resolved_path(scene).parent, text)
    return st


def choose_layout(scene: str | Path, name: str) -> dict[str, Any]:
    """Keep one of the proposed layouts: its placement is applied to the scene and the other options
    are dropped. name='best' takes the one with the fewest issues / unmet request items / warnings (おまかせ).
    The choice is recorded as the assumption 'layout' (given unless it was 'best')."""
    s = load(scene)
    d = _resolved_path(scene).parent
    if s.spec.contrast and s.spec.contrast.step and s.spec.layout_options:  # a two-way comparison
        if name not in ("now", "other"):
            raise ValueError("a two-way comparison is open: choose_contrast('now' | 'other' | 'neither')")
        return choose_contrast(scene, name)
    if not s.spec.layout_options:
        raise ValueError("no layout options; propose_layouts first")
    if name == "best":
        opts = list(s.spec.layout_options)
        if s.spec.options_axis:  # drafts: on a tie the middle one, not the smallest
            opts = [opts[1], opts[0], *opts[2:]]
        name = _best([{"name": o.name, **_rate_option(_option_scene(s, o))} for o in opts])
        given, basis = False, "問題が少ない案（おまかせ）"
    else:
        given, basis = True, ("プレビューで選択" if ACTOR.get() == "preview" else None)
    opt = next((o for o in s.spec.layout_options if o.name == name), None)
    if opt is None:
        raise ValueError(f"no layout option '{name}'; one of {', '.join(o.name for o in s.spec.layout_options)}")
    axis = s.spec.options_axis
    _apply_option(s, opt)
    s.spec.layout_options, s.spec.options_axis = [], None
    if axis:  # a draft: the user picked it among three, so it is theirs - not a default of the draft
        if given:
            basis = DRAFT_BASIS + ("（プレビュー）" if basis else "")
        row = Assumption(item=AXIS_JA[axis], value=opt.label, given=given, basis=basis)
        words = _AXIS_WORDS[axis]
        s.spec.assumptions = [a for a in s.spec.assumptions if a.key() != row.key() and not (
            a.object is None and not a.given and not a.auto and any(w in a.item.lower() for w in words))] + [row]
        return _save(s, d, f"叩き台「{opt.label}」を選択（{AXIS_JA[axis]}）")
    row = Assumption(item="layout", value=opt.label, given=given, basis=basis)
    s.spec.assumptions = [a for a in s.spec.assumptions if a.key() != row.key()] + [row]
    return _save(s, d, f"配置案「{opt.label}」を選択")


def moods(scene: str | Path) -> dict[str, Any]:
    """The looks the scene's place offers, each with its sample picture (a standard room drawn once in Isaac
    Sim, not this scene), its floor / walls / light and the furniture colors."""
    from agentic_sim_world_gen.scene import moods as md

    s = load(scene)
    cur = s.spec.mood.name if s.spec.mood else None
    return {"place_group": md.group(s.spec.room.place), "current": cur, "moods": [
        {"name": m.name, "label": m.label, "note": m.note, "image": str(f) if (f := md.image(s.spec.room.place, m.name))
         else None, "taste": m.taste().model_dump(exclude_none=True, exclude_defaults=True)}
        for m in md.for_place(s.spec.room.place)] if not s.spec.room.outdoor else []}


# store fixtures a mood does not swap in a shop (set_tint repaints them): the color words find other trades' pieces
STORE_FIXTURES = ("counter", "shelf", "shelving", "rack", "gondola", "stand", "case", "showcase")


def _mood_furniture(s: ResolvedScene, m) -> list[dict[str, Any]]:
    """Objects whose kind has colors in the mood: the query to look for with research_object."""
    from agentic_sim_world_gen.assets.sizes import kind_of
    from agentic_sim_world_gen.scene import moods as md

    shop = md.group(s.spec.room.place) == "shop"
    out = []
    for o in s.spec.objects:
        if o.decor or not o.query:
            continue
        kind = kind_of(o.query, o.alt_queries) or o.query
        if shop and kind.lower().split()[-1] in STORE_FIXTURES:  # "white checkout counter" found kitchens
            continue
        words = md.furniture_words(m, kind)
        if words and words.lower() not in o.query.lower():
            out.append({"object": o.id, "query": o.query, "words": words, "try": f"{words} {o.query}"})
    return out


MOOD_BASIS = "雰囲気「"  # basis of the assumptions apply_mood records
MOOD_ROOM_WORDS = ("floor", "walls", "lighting")  # the room items a mood decides (_ITEM_WORDS keys)
# room fields a mood sets: changing one by hand drops the mood (set_room)
MOOD_FIELDS = ("floor", "walls", "ceiling", "baseboard", "wainscot", "lighting", "light_kelvin", "lighting_scale")


def _room_item(item: str) -> str | None:
    """The room item (floor / walls / lighting) an assumption's item names, if any."""
    return next((k for k in MOOD_ROOM_WORDS if any(w in item.lower() for w in _ITEM_WORDS[k])), None)


def _mood_conflicts(s: ResolvedScene) -> list[dict[str, Any]]:
    """What the user decided about the look that a mood would replace: room assumptions given as asked
    (not by an earlier mood) and the taste read off reference images."""
    out = [{"item": a.item, "value": a.value, "from": "指示どおり"} for a in s.spec.assumptions
           if a.object is None and a.given and not (a.basis or "").startswith(MOOD_BASIS) and _room_item(a.item)]
    for i in s.spec.inputs:
        t = i.taste
        if i.kind == "reference" and t is not None:
            got = [v for v in (t.floor, t.walls, t.lighting, f"{t.light_kelvin:g} K" if t.light_kelvin else None) if v]
            if got:
                out.append({"item": f"参考画像 {i.id}", "value": "、".join(got), "from": "参考画像"})
    return out


def apply_mood(scene: str | Path, name: str, confirm: bool = False) -> dict[str, Any]:
    """Apply a look picked from the sample pictures (`moods` lists them): the floor, walls, trim, ceiling,
    light color and brightness change at once (presets, no download), recorded as assumptions. The furniture is
    not swapped: `mood_furniture` lists each object with words to try ("light oak wood desk") — look with
    research_object(select=false) / view_candidates and offer the swaps. Picking on the preview page leaves
    `mood_hint` in status until the agent calls apply_mood with the same name.

    The room as it was before the first mood is kept: name "undo" puts it back. What the user decided
    (assumptions given as asked, reference images' taste) is listed in `overwrote`; with confirm=True nothing
    changes when there is any and the result is {"applied": False, "confirm": [...]} (the preview page asks)."""
    from agentic_sim_world_gen.scene import moods as md
    from agentic_sim_world_gen.scene.spec import MoodChoice, Room

    s = load(scene)
    d = _resolved_path(scene).parent
    if name == "undo":
        return _undo_mood(s, d)
    room = s.spec.room
    if room.outdoor:
        raise ValueError("moods are for indoor rooms")
    m = md.get(name, room.place)
    conflicts = _mood_conflicts(s) if s.spec.mood is None else []  # once overwritten, switching does not ask again
    if confirm and conflicts:
        return {"applied": False, "mood": name, "confirm": conflicts}
    changes = m.room(bool(room.ceiling))
    if room.plan is not None and isinstance(changes["walls"], dict):  # plan walls have their own ids
        changes["walls"] = changes["walls"]["*"]
    prev = s.spec.mood
    data = room.model_dump()
    before_room = prev.before_room if prev and prev.before_room is not None else \
        {k: v for k, v in room.model_dump(mode="json").items() if k in changes}
    data.update(changes)
    s.spec.room = Room.model_validate(data)
    preview = ACTOR.get() == "preview"
    same = prev is not None and prev.name == name
    basis = f"{MOOD_BASIS}{m.label}」" + ("（プレビューの見本から選択）" if preview else "")
    rows = [Assumption(item="雰囲気", value=f"{m.label}：{m.note}", given=True, basis=basis),
            Assumption(item="床", value=m.floor, given=True, basis=basis),
            Assumption(item="壁", value=m.taste().walls or "", given=True, basis=basis),
            Assumption(item="照明の色", value=f"{m.kelvin:g} K、明るさ {m.scale:g}", given=True, basis=basis)]
    words = {"雰囲気": ("雰囲気", "mood"), **{a.item: _ITEM_WORDS[k] for a, k in zip(rows[1:], MOOD_ROOM_WORDS)}}
    keep, dropped = [], []
    for a in s.spec.assumptions:
        hit = None if a.object is not None else next(
            (r for r in rows if any(w in a.item.lower() for w in words[r.item])), None)
        if hit is None:
            keep.append(a)
            continue
        dropped.append(a)
        if a.feedback is not None:  # the user's words are not answered yet: they stay on the row that replaces it
            if hit.feedback is None:
                hit.feedback = a.feedback
            else:
                keep.append(a)
    s.spec.assumptions = keep + rows
    s.spec.mood = MoodChoice(name=name, by="preview" if preview else "agent", furniture_pending=preview,
                             before_room=before_room,
                             before_assumptions=prev.before_assumptions if prev and prev.before_room is not None
                             else [a.model_dump(mode="json") for a in dropped])
    msg = f"雰囲気を「{m.label}」に" + ("（同じ）" if same and not preview else "")
    out = _save(s, d, msg)
    if conflicts:
        out["overwrote"] = conflicts
    if fur := _mood_furniture(s, m):
        out["mood_furniture"] = fur
    return out


def _undo_mood(s: ResolvedScene, d: Path) -> dict[str, Any]:
    """Put the room back as it was before the first mood."""
    from agentic_sim_world_gen.scene import moods as md
    from agentic_sim_world_gen.scene.spec import Room

    mc = s.spec.mood
    if mc is None:
        raise ValueError("no mood to undo")
    if mc.before_room is None:
        raise ValueError("this mood was applied before undo was kept: set the floor / walls / light with set_room")
    data = s.spec.room.model_dump(mode="json")
    data.update(mc.before_room)
    s.spec.room = Room.model_validate(data)
    back = [Assumption.model_validate(a) for a in mc.before_assumptions or []]
    # the user's words given while the mood was on are not lost: they go to the restored row of the same item
    pending = {_room_item(a.item): a.feedback for a in s.spec.assumptions
               if a.feedback and (a.basis or "").startswith(MOOD_BASIS)}
    for a in back:
        if a.feedback is None and (fb := pending.pop(_room_item(a.item), None)):
            a.feedback = fb
    keys = {a.key() for a in back}
    s.spec.assumptions = [a for a in s.spec.assumptions
                          if not (a.basis or "").startswith(MOOD_BASIS) and a.key() not in keys] + back
    label = md.MOODS[mc.name].label if mc.name in md.MOODS else mc.name
    s.spec.mood = None
    return _save(s, d, f"雰囲気「{label}」を取り消し（元の床・壁・照明に）")


def _mood_view(s: ResolvedScene) -> dict[str, Any]:
    from agentic_sim_world_gen.scene import moods as md

    mc = s.spec.mood
    if mc is None:
        return {}
    out: dict[str, Any] = {"mood": mc.model_dump(exclude={"before_room", "before_assumptions"})}
    if mc.furniture_pending and mc.name in md.MOODS:
        m = md.MOODS[mc.name]
        out["mood_hint"] = (f"the user picked the look '{mc.name}' ({m.label}) on the preview page; the room already "
                            "has it. Look for furniture in its colors (mood_furniture: research_object with select=false, "
                            "view_candidates), offer the swaps, then call apply_mood with the same name to clear this")
        if fur := _mood_furniture(s, m):
            out["mood_furniture"] = fur
    return out


PURPOSE_BASIS = "用途から"  # basis of the assumptions set_purpose records
DEFAULT_ROBOT_RADIUS = 0.3


def set_purpose(scene: str | Path, purpose: str, robot_radius: float | None = None, targets: list[str] | None = None,
                given: bool = True) -> dict[str, Any]:
    """What the scene is for: demo / amr / manipulation / sdg (or the answer to the plan_questions purpose
    question). Applies the purpose's defaults the user has not decided (Room.density, the robot's walking width for
    the usability check, SceneSpec.sdg) and records each as a guess with the basis 「用途から（…）」. given=false when
    the purpose itself is a guess (the user left it to you). targets: for sdg, what the detector should find.
    robot_radius: the robot's size when the user said it: recorded as given, and kept so by later calls."""
    from agentic_sim_world_gen.scene import decor
    from agentic_sim_world_gen.scene import purpose as pp
    from agentic_sim_world_gen.scene.questions import PURPOSE
    from agentic_sim_world_gen.scene.spec import Assumption, PurposeSpec

    p = pp.get(purpose)
    d = _resolved_path(scene).parent
    s = load(scene)
    old = s.spec.purpose
    decided = {a.item.strip().lower() for a in s.spec.assumptions
               if a.object is None and a.given and not (a.basis or "").startswith(PURPOSE_BASIS)}
    ours = {a.item.strip().lower() for a in s.spec.assumptions
            if a.object is None and (a.basis or "").startswith(PURPOSE_BASIS)}
    basis = f"{PURPOSE_BASIS}（{p.label}）"
    radius = (robot_radius or (old.robot_radius if old and old.robot_radius else DEFAULT_ROBOT_RADIUS)) if p.robot else None
    # the size is the user's when passed now, or passed before and still in use
    radius_given = bool(robot_radius) or (bool(old and old.robot_radius and radius == old.robot_radius) and any(
        a.object is None and a.item.strip().lower() == "robot" and a.given for a in s.spec.assumptions))
    if targets is None and old is not None:
        targets = old.targets
    s.spec.purpose = PurposeSpec(name=p.name, robot_radius=radius, aisle=p.aisle_for(radius) if radius else None,
                                 targets=[w.strip() for w in targets or [] if w.strip()])
    rows = [Assumption(item="purpose", value=p.label, given=given,
                       basis=None if given else "おまかせ（既定）", options=list(PURPOSE.options))]
    drop = set(ours) | {"robot"}
    msg = f"用途を「{p.label}」に"
    if "density" not in decided:
        rows.append(Assumption(item="density", value=decor.DENSITY_JA.get(p.density, p.density), basis=basis))
        if s.spec.room.density != p.density:
            s.spec.room.density = p.density  # type: ignore[assignment]
            msg += "。" + _apply_density(s)
    if radius:
        from agentic_sim_world_gen.scene.questions import ROBOT

        rows.append(Assumption(item="robot", value=f"半径 {radius:g} m、通路 {s.spec.purpose.aisle:g} m 以上",
                               given=radius_given, basis=None if radius_given else basis, options=list(ROBOT.options)))
    if p.sdg:
        if s.spec.sdg is None:
            s.spec.sdg = SdgSpec(frames=100, writers=["basic", "coco"])
            rows.append(Assumption(item="sdg", value="100 枚、COCO 形式、照明の明るさ・色温度をランダム", basis=basis))
        elif "sdg" in ours:
            rows.append(next(a for a in s.spec.assumptions if a.object is None and a.item.strip().lower() == "sdg"))
    elif "sdg" in ours and s.spec.sdg is not None:
        s.spec.sdg = None  # set by the previous purpose, not by the user
    if s.spec.purpose.targets:
        rows.append(Assumption(item="targets", value=", ".join(s.spec.purpose.targets), given=True))
        drop.add("targets")
    keys = {a.key() for a in rows} | {(None, i) for i in drop | {"purpose"}}
    s.spec.assumptions = [a for a in s.spec.assumptions if a.key() not in keys] + rows
    out = _save(s, d, msg)
    out["purpose_next"] = _purpose_next(s, d)
    return out


def _purpose_missing(s: ResolvedScene) -> list[str]:
    """sdg targets no object in the scene stands for (query, label, class, or the selected asset's class)."""
    import re

    p = s.spec.purpose
    if p is None or not p.targets:
        return []
    names = []
    for o in s.spec.objects:
        names += [o.query or "", o.label or "", o.semantic_class or ""]
    names += [pl.asset.semantic_class or "" for pl in s.placements if pl.asset is not None]
    text = " ".join(n.lower() for n in names)
    return [w for w in p.targets if not re.search(rf"\b{re.escape(w.lower())}", text)]


def _purpose_next(s: ResolvedScene, d: Path) -> list[str]:
    """What still has to be done for the purpose (English, for the agent)."""
    p = s.spec.purpose
    if p is None:
        return []
    out = []
    if p.name == "amr":
        nav = d / NAV_DIR / "nav.json"
        try:
            made = json.loads(nav.read_text()).get("made_from")
        except (OSError, ValueError):
            made = None
        if made is None:
            out.append(f"after compose_scene run nav_map(robot_radius={p.robot_radius or 0.3:g}) and fix what the robot cannot "
                       "reach (unreachable_objects / unreachable_zones)")
        else:
            from agentic_sim_world_gen.preview.page import scene_hash

            if made != scene_hash(d):
                # The layout changed after the last map, so its reach no longer holds
                out.append(f"the layout changed since the last nav map: run nav_map(robot_radius={p.robot_radius or 0.3:g}) "
                           "again before reporting what the robot can reach")
        out.append("keep the floor clear of small things the robot cannot see (no rugs or cables across aisles)")
    elif p.name == "manipulation":
        out.append("put graspable things (mugs, bottles, boxes, tools) on the work surfaces with layout.on / set_fill; "
                   f"keep {p.aisle:g} m free in front of them for the robot")
    elif p.name == "sdg":
        missing = _purpose_missing(s)
        if not p.targets:
            out.append("ask what the detector should find and call set_purpose(targets=[...]) (English words)")
        if missing:
            out.append(f"add objects for the targets not in the scene: {', '.join(missing)} (several, varied assets)")
        if p.targets:  # an "other" class for the rest outnumbered every target
            out.append('map the target objects in sdg.classes and set sdg.others="background": the rest stays '
                       'unlabelled (no "other" class)')
        out.append("generate_data(dry_run=true) to check the cameras before rendering")
    elif p.name == "demo":
        out.append("the look matters most: pick the room's look with the user and check_in_isaac with the orbit views")
    return out


def _purpose_view(s: ResolvedScene, d: Path) -> dict[str, Any]:
    p = s.spec.purpose
    if p is None:
        return {}
    out: dict[str, Any] = {"purpose": p.model_dump(mode="json", exclude_defaults=True)}
    if missing := _purpose_missing(s):
        out["purpose_missing"] = missing
    out["purpose_next"] = _purpose_next(s, d)
    return out


def _feedback_view(s: ResolvedScene) -> dict[str, Any]:
    """Assumption rows the user marked wrong on the preview page and the agent has not re-recorded yet."""
    rows = [{"item": a.item, **({"object": a.object} if a.object else {}), "value": a.value,
             "feedback": a.feedback.text, **({"options": a.options} if a.options else {})}
            for a in s.spec.assumptions if a.feedback]
    out: dict[str, Any] = {}
    if rows:
        out = {"assumption_feedback": rows, "assumption_feedback_hint":
               "the user says these assumptions are wrong (sent from the preview page): fix only these, as their "
               "words say, then re-record each row with set_assumptions (given=true), which clears the feedback; "
               "leave the other rows and any preview_edits as they are"}
    if review := [{"item": a.item, "object": a.object, "value": a.value, "why": a.review}
                  for a in s.spec.assumptions if a.review]:
        out["assumptions_review"] = review
        out["assumptions_review_hint"] = ("the asset of these objects was swapped after the rows were written: "
                                          "check each value (size, look, position) and re-record it with "
                                          "set_assumptions, which clears the mark")
    return out


BRIEF_CAPTION = 60  # characters of the selected asset's caption in the brief status
BRIEF_OTHERS = 3  # other candidates listed per object in the brief status


def _one_line(c: dict[str, Any]) -> str:
    m = c.get("match") or {}
    size = "×".join(f"{v:g}" for v in c.get("size_m") or [])
    score = f" score {m['score']:g}" if m.get("score") is not None else ""
    off = f" off-place:{m['off_place']}" if m.get("off_place") else ""
    if m.get("off_size"):
        off += f" off-size:{','.join(k for k in m['off_size'] if k != 'kind')}"
    if m.get("heavy"):
        off += f" heavy:{m['heavy'] / 1e6:.1f}M faces"
    if (m.get("card") or {}).get("issue"):
        off += f" card:{m['card']['issue']}"
    return f"[{c['index']}] {c['name']} {size} m{score}{' weak' if m.get('weak') else ''}{off}"


def brief(st: dict[str, Any], focus: str | list[str] | None = None) -> dict[str, Any]:
    """The status made small enough for an MCP result in a big scene (a konbini with 39 objects and 6 candidates
    each is ~56k characters in full). Each object keeps its selected asset (short caption) and the other
    candidates as one line each; the focus object keeps every candidate in full. Indices are unchanged, so
    select_asset works from either; get_scene(object_id=...) shows one object in full."""
    if "objects" not in st:
        return st
    focus = {focus} if isinstance(focus, str) else set(focus or ())
    objs, decor = [], {}
    for o in st["objects"]:
        if o["id"] in focus:
            objs.append(o)
            continue
        cands = o.get("candidates") or []
        if o.get("decor"):  # stock and table things in one line each
            decor[o["id"]] = _decor_line(o, cands[o["selected"]] if o.get("selected") is not None and cands else None)
            continue
        b = {k: v for k, v in o.items() if k != "candidates" and v is not None}
        if b.get("z") == 0:
            b.pop("z")
        if b.get("array"):
            b["array"] = {k: v for k, v in b["array"].items() if v not in (False, 0, 0.0) or k in ("count", "rows")}
        if b.get("fill"):  # the settings left at their defaults say nothing
            b["fill"] = {k: v for k, v in b["fill"].items() if v not in (None, False) or k in ("on", "count")}
        if o.get("selected") is not None and cands:
            c = cands[o["selected"]]  # one line like the others, with its license and caption
            cap = c.get("caption") or ""
            cap = cap[:BRIEF_CAPTION] + "…" if len(cap) > BRIEF_CAPTION else cap
            b["asset"] = _one_line(c) + (f" {c['license']}" if c.get("license") else "") + (f" — {cap}" if cap else "")
        others = [_one_line(c) for c in cands if c["index"] != o.get("selected")]
        if others:
            b["others"] = others[:BRIEF_OTHERS] + ([f"+{len(others) - BRIEF_OTHERS} more"] if len(others) > BRIEF_OTHERS else [])
        objs.append(b)
    more = {}
    if st.get("assumptions"):  # the options are the preview's choices for 「違う」, not for the agent
        more["assumptions"] = [{k: v for k, v in a.items() if k != "options" and v not in (None, [], "")}
                               if isinstance(a, dict) else a for a in st["assumptions"]]
    return {**st, **more, "objects": objs, **({"decor": decor} if decor else {}),
            "brief": "objects show the selected asset and other candidates in one line each, decor objects one line "
            "each; get_scene(scene_dir, object_id) or view_candidates for one object in full"}


def _decor_line(o: dict[str, Any], c: dict[str, Any] | None) -> str:
    """'チップス (Chips Bag) ×48 on shelf_e' / 'マグ (Mug) on desk' / '観葉植物 (Plant) beside desk'."""
    what = o.get("label") or o.get("query") or o["id"]
    if c is not None:
        what += f" ({c['name']})"
    else:
        what += " (no asset)"
    if f := o.get("fill"):
        return f"{what} ×{f.get('count', 0)} on {f.get('on')}"
    lay = o.get("layout") or {}
    for k, word in (("on", "on"), ("next_to", "beside"), ("in_front_of", "in front of"), ("against_wall", "against")):
        if lay.get(k):
            return f"{what} {word} {lay[k]}"
    return what


REPLY_STATE = ".last_reply.json"  # what the agent last saw of each object / key
REPLY_KEEP = ("scene_dir", "issues", "usd", "reply_language", "brief")


def _digest(v: Any) -> str:
    return hashlib.sha1(json.dumps(v, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:12]


def _lang_first(r: dict[str, Any]) -> dict[str, Any]:
    """The language keys before the rest of a result."""
    head = {k: r[k] for k in ("reply_language", "reply_language_hint") if k in r}
    return {**head, **{k: v for k, v in r.items() if k not in head}} if head else r


def reply(st: dict[str, Any], focus: str | list[str] | None = None, full: bool = False) -> dict[str, Any]:
    """The result of an editing tool: only what changed since the agent's last result for this scene
    (objects, decor lines and status sections, by content), the focus objects in full, the issues, every hint
    and a summary of counts. Full status costs 12-16k characters per call in a shop and kept compacting the
    conversation. full=True (create_scene, get_scene) returns everything; both remember what was shown."""
    b = _lang_first(brief(st, focus))
    if "objects" not in b or not b.get("scene_dir"):
        return b
    d = Path(b["scene_dir"])
    short = brief(st) if focus else b  # digests of the short form: a focus object is not "changed" next time
    now = {"objects": {o["id"]: _digest(o) for o in short["objects"]},
           "decor": {k: _digest(v) for k, v in (short.get("decor") or {}).items()},
           "keys": {k: _digest(v) for k, v in b.items() if k not in ("objects", "decor")}}
    try:
        last = json.loads((d / REPLY_STATE).read_text())
    except (OSError, ValueError):
        last = None
    try:
        (d / REPLY_STATE).write_text(json.dumps(now))
    except OSError:
        pass
    if full or not isinstance(last, dict):
        return b
    focus = {focus} if isinstance(focus, str) else set(focus or ())
    out: dict[str, Any] = {}
    same = []
    for k, v in b.items():
        if k in ("objects", "decor"):
            continue
        if (k in REPLY_KEEP or k.endswith(("hint", "next")) or not isinstance(v, (dict, list))
                or now["keys"][k] != last.get("keys", {}).get(k)):
            out[k] = v
        else:
            same.append(k)
    seen, seen_decor = last.get("objects", {}), last.get("decor", {})
    out["objects"] = [o for o in b["objects"]
                      if o["id"] in focus or seen.get(o["id"]) != now["objects"].get(o["id"])]
    decor = {k: v for k, v in (b.get("decor") or {}).items() if seen_decor.get(k) != now["decor"][k]}
    if decor:
        out["decor"] = decor
    gone = [i for i in [*seen, *seen_decor] if i not in now["objects"] and i not in now["decor"]]
    if gone:
        out["removed"] = gone
    n_obj, n_decor = len(now["objects"]), len(now["decor"])
    shown = {o["id"] for o in out["objects"]} | set(decor)
    out["summary"] = {"objects": n_obj, "decor": n_decor, "issues": len(b["issues"]),
                      "usability": len(b.get("usability") or []),
                      "unchanged": n_obj + n_decor - len(shown & (set(now["objects"]) | set(now["decor"]))),
                      "unchanged_sections": same}
    out["brief"] = ("only what changed since your last agentic-sim-world-gen result for this scene (objects, decor lines, "
                    "sections); the rest is as before. get_scene(scene_dir) for everything")
    return out


JA = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]")


def scene_language(s: ResolvedScene) -> str | None:
    """The user's language: SceneSpec.lang, else Japanese when the labels, requests, assumptions or name
    the agent wrote in the user's words have kana or kanji, else English once any of them is written."""
    if s.spec.lang:
        return s.spec.lang
    words = [s.spec.name, *(o.label or "" for o in s.spec.objects if not o.decor),
             *(r.text for r in s.spec.requirements), *(a.value for a in s.spec.assumptions if a.given)]
    if any(JA.search(w) for w in words):
        return "ja"
    if s.spec.requirements:
        return "en"
    return None


LANG_NAMES = {"ja": "Japanese (日本語)", "en": "English"}
# The reminders are written in the user's language: an English "write in Japanese" between English tool
# Results did not hold after a compaction; a line already in that language is what the next note follows.
LANG_HINTS = {
    "ja": ("ユーザーへの文章はすべて日本語で書く。途中報告、ツール呼び出しの間の 1 行、最終報告も。"
           "会話が要約（圧縮）された後も同じ（要約が英語でも日本語に戻す）。",
           "次にユーザーへ書く文は日本語で。ツール呼び出しの間の 1 行も（「Passed.」ではなく「通りました。」）。"
           "会話が要約（圧縮）された直後でも日本語。"),
}


def language_hint(lang: str, short: bool = False) -> str:
    """The reminder to write to the user in `lang`, in that language when there is a text for it (LANG_HINTS)."""
    if lang in LANG_HINTS:
        return LANG_HINTS[lang][1 if short else 0]
    name = LANG_NAMES.get(lang, lang)
    if short:
        return (f"write the next message to the user in {name}, even a one-line note like 'Passed.', also right "
                "after the conversation was compacted")
    return (f"the user writes {name}: write every message to the user in {name}, short progress notes and the final "
            "report too, even a one-line note between tool calls, also after the conversation was compacted (the "
            "summary may be in another language)")


def _conflicts_view(s: ResolvedScene) -> dict[str, Any]:
    """`conflicts` while assumptions contradict each other: a room too small for its fixtures, a robot wider
    than the aisles, fewer seats than people, a fixture taller than the ceiling."""
    from agentic_sim_world_gen.scene import conflicts

    r = conflicts.report(s)
    return {"conflicts": r} if r else {}


def _lang_view(s: ResolvedScene) -> dict[str, Any]:
    lang = scene_language(s)
    if lang is None:
        return {}
    return {"reply_language": lang, "reply_language_hint": language_hint(lang)}


def with_language(scene_dir: str | Path, r: Any) -> Any:
    """Add the user's language to a tool result that does not carry the scene status: check_in_isaac,
    view_fronts, compose_scene and the like answer in English JSON, and the note the agent wrote right after
    one ("Passed. Let me look at the screenshot.") came out in English. The two keys come first: at the
    end of a long result they were read last, after the English the note then copied."""
    if not isinstance(r, dict) or "reply_language" in r:
        return r
    try:
        lang = scene_language(load(scene_dir))
    except (OSError, ValueError, LookupError):
        return r
    if lang is None:
        return r
    return {"reply_language": lang, "reply_language_hint": language_hint(lang, short=True), **r}


def latest_language(out: str | Path | None = None) -> str | None:
    """The language of the scene saved last under `out` (default AGENTIC_SIM_WORLD_GEN_OUT): what a hook outside the
    conversation can know of the user's language (`agentic-sim-world-gen reply-language`)."""
    root = Path(out or os.environ.get("AGENTIC_SIM_WORLD_GEN_OUT") or "").expanduser()
    if not str(out or os.environ.get("AGENTIC_SIM_WORLD_GEN_OUT") or "") or not root.is_dir():
        return None
    files = sorted(root.glob("*/scene.resolved.json"), key=lambda f: f.stat().st_mtime, reverse=True)
    for f in files[:3]:
        try:
            if lang := scene_language(load(f.parent)):
                return lang
        except (OSError, ValueError, LookupError):
            continue
    return None


def shown_pictures(s: ResolvedScene) -> list[dict[str, Any]]:
    """The asset's own printed pictures and screen images the scene shows: shop headers, POPs, posters,
    signage whose wording the agent has not chosen. One entry per image, with the objects showing it."""
    out: dict[str, dict[str, Any]] = {}
    for p in s.placements:
        o, a = s.obj(p.object_id), p.asset
        if a is None or not a.screens or o.screen is not None:
            continue
        for x in a.screens:
            vset, on = x.get("variant_set"), x.get("on")
            if vset and on is not None and o.variants.get(vset, on) != on:
                continue  # blank / off
            e = out.setdefault(x["image"], {
                "objects": [], "asset": a.name, "kind": x.get("kind", "screen"), "material": x["material"],
                "image": x["image"], **{k: x[k] for k in ("text", "lang", "audience") if x.get(k)},
                "places": x.get("places") or [],
                "blank": {vset: x.get("off_variant") or "Off"} if vset else None})
            if o.id not in e["objects"]:
                e["objects"].append(o.id)
    return list(out.values())


PICTURES_SEEN = "pictures_seen.json"  # images view_pictures has shown


def _seen_pictures(d: Path) -> set[str]:
    try:
        return set(json.loads((d / PICTURES_SEEN).read_text()))
    except (OSError, ValueError):
        return set()


def _picture_view(s: ResolvedScene, d: Path) -> dict[str, Any]:
    pics = shown_pictures(s)
    if not pics:
        return {}
    seen = _seen_pictures(d)
    pics = [{**x, "viewed": x["image"] in seen} for x in pics]
    unseen = [x for x in pics if not x["viewed"]]
    if not unseen:
        return {"pictures": pics}
    said = [f"{', '.join(x['objects'])}: \"{' / '.join(x['text'])}\"" for x in unseen if x.get("text")]
    return {"pictures": pics, "pictures_hint": (
        "these objects show the asset's own printed wording (section headers, POPs, posters, screens). Look at them "
        "with view_pictures and blank the ones that do not fit the request or the photos (a men's / women's section "
        "the user did not ask for, a genre, a language or region unlike the input photos): set_variants with each "
        "entry's `blank`, or set_screen with an image that fits" + (f". Wording: {'; '.join(said)}" if said else ""))}


THUMB_PX = 256  # a candidate or material thumbnail shown to the agent (some are 1024 px = 16 times the tokens)
PICTURE_PX = 512  # a printed picture: still readable wording at a third of the tokens of 768 px


def _shrink(img: bytes | None, max_px: int) -> bytes | None:
    """The image no larger than max_px on its long side (JPEG stays JPEG, the rest PNG); unreadable ones as they are."""
    import io

    from PIL import Image as PILImage

    if not img:
        return img
    try:
        im = PILImage.open(io.BytesIO(img))
        if max(im.size) <= max_px:
            return img
        jpeg = im.format == "JPEG"
        im.thumbnail((max_px, max_px))
        buf = io.BytesIO()
        if jpeg:
            im.convert("RGB").save(buf, "JPEG", quality=88)
        else:
            im.save(buf, "PNG")
        return buf.getvalue()
    except (OSError, ValueError):
        return img


def picture_images(scene: str | Path, max_px: int = PICTURE_PX) -> list[tuple[dict[str, Any], bytes | None]]:
    """(summary, PNG bytes or None) for each picture the scene shows, scaled down to max_px."""
    import io

    from PIL import Image as PILImage

    s = load(scene)
    pics = shown_pictures(s)
    _event(scene, f"掲示物・画面の絵柄 {len(pics)} 件を確認中")
    d = _resolved_path(scene).parent
    (d / PICTURES_SEEN).write_text(json.dumps(sorted(_seen_pictures(d) | {x["image"] for x in pics}), indent=1))
    out = []
    for x in pics:
        img = None
        try:
            im = PILImage.open(x["image"])
            im.thumbnail((max_px, max_px))
            buf = io.BytesIO()
            im.convert("RGB").save(buf, "PNG")
            img = buf.getvalue()
        except OSError:
            pass
        out.append(({k: v for k, v in x.items() if k != "image"}, img))
    return out


def candidate_thumbnails(scene: str | Path, object_id: str, timeout_s: float = 20) -> list[tuple[dict[str, Any], bytes | None]]:
    """(short summary, PNG/JPEG bytes or None) for each candidate of one object."""
    from agentic_sim_world_gen.taste import fetch

    s = load(scene)
    p = _placement(s, object_id)
    _event(scene, f"{_name(s, object_id)} の候補 {len(p.candidates)} 件をサムネイルで確認中", object_id)
    return [(_short(c, i), _shrink(fetch(u, timeout_s), THUMB_PX) if (u := c.get("thumbnail_url")) else None)
            for i, c in enumerate(p.candidates)]


SHEET_TILE = 192  # px per candidate on a contact sheet
SHEET_OBJECTS = 8  # objects per view_candidates call when none are named


def _doubtful(s: ResolvedScene) -> list[str]:
    """Objects whose chosen asset deserves a look: a substitute, a weak match or one outside the size asked for."""
    out = []
    for p in s.placements:
        if not p.candidates:
            continue
        m = p.candidates[min(p.selected, len(p.candidates) - 1)].get("match") or {}
        if p.substitute or m.get("weak") or m.get("off_size") or m.get("off_place"):
            out.append(p.object_id)
    return out


def _sheet(images: list[bytes | None], selected: int | None, tile: int = SHEET_TILE) -> bytes | None:
    """One row of thumbnails, each with its candidate index in the corner; the selected one framed."""
    import io

    from PIL import Image as PILImage
    from PIL import ImageDraw

    if not any(images):
        return None
    sheet = PILImage.new("RGB", (tile * len(images), tile), (255, 255, 255))
    draw = ImageDraw.Draw(sheet)
    for i, img in enumerate(images):
        x = i * tile
        try:
            im = PILImage.open(io.BytesIO(img)).convert("RGBA") if img else None
        except (OSError, ValueError):
            im = None
        if im is None:
            draw.rectangle((x + 8, 8, x + tile - 8, tile - 8), outline=(190, 190, 190))
            draw.line((x + 8, 8, x + tile - 8, tile - 8), fill=(190, 190, 190))
        else:
            im.thumbnail((tile - 8, tile - 8))
            back = PILImage.new("RGBA", im.size, (255, 255, 255, 255))
            sheet.paste(PILImage.alpha_composite(back, im).convert("RGB"),
                        (x + (tile - im.width) // 2, (tile - im.height) // 2))
        if i == selected:
            draw.rectangle((x + 1, 1, x + tile - 2, tile - 2), outline=(0, 120, 255), width=4)
        draw.rectangle((x + 4, 4, x + 30, 26), fill=(0, 0, 0))
        draw.text((x + 12, 9), str(i), fill=(255, 255, 255))
    buf = io.BytesIO()
    sheet.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def candidate_sheets(scene: str | Path, object_ids: list[str] | None = None, timeout_s: float = 20,
                     limit: int = SHEET_OBJECTS) -> list[tuple[dict[str, Any], bytes | None]]:
    """Several objects' candidates in one call: per object (summary, one JPEG with all its candidates in a
    row, index in the corner, the selected one framed). object_ids None: the objects whose asset deserves a look
    (substitute, weak match, off size), at most `limit`; the summary of the last one says which were left out."""
    from agentic_sim_world_gen.taste import fetch

    s = load(scene)
    asked = object_ids is not None
    ids = list(dict.fromkeys(object_ids)) if asked else _doubtful(s)
    rest = [] if asked else ids[limit:]
    ids = ids if asked else ids[:limit]
    for i in ids:
        _placement(s, i)
    if ids:
        _event(scene, f"{len(ids)} 個の物の候補をサムネイルで確認中（{'、'.join(_name(s, i) for i in ids[:4])}"
                      f"{' ほか' if len(ids) > 4 else ''}）", ids[0])
    out: list[tuple[dict[str, Any], bytes | None]] = []
    for i in ids:
        p = _placement(s, i)
        info: dict[str, Any] = {"object": i, "selected": p.selected if p.candidates else None,
                                "candidates": [_short(c, k) for k, c in enumerate(p.candidates)]}
        if p.substitute:
            info["substitute"] = True
        out.append((info, _sheet([fetch(u, timeout_s) if (u := c.get("thumbnail_url")) else None for c in p.candidates],
                                 info["selected"])))
    if not ids:
        out.append(({"objects": [], "hint": "no object has a doubtful asset (substitute, weak match, off size): "
                     "name the ones to look at with object_id"}, None))
    elif rest:
        out[-1][0]["not_shown"] = rest
    return out


def open_preview(scene: str | Path, browser: bool = True) -> dict[str, Any]:
    """Serve preview.html over local HTTP (it reloads itself on every change) and open it in the
    browser, unless a page for this scene is already open (it polls the server every second)."""
    from agentic_sim_world_gen.preview import server

    d = _resolved_path(scene).parent
    if not (d / "preview.html").exists():
        _event(scene, "プレビューを開きました")
    url = server.url_for(d)
    out: dict[str, Any] = {"url": url, "already_open": server.page_is_open(d)}
    out["opened"] = bool(browser and not out["already_open"] and server.open_browser(url))
    if browser and not out["opened"] and not out["already_open"]:
        host, port = url.split("/")[2].split(":")
        out["hint"] = (f"no desktop browser here: open {url} yourself (over ssh: "
                       f"ssh -L {port}:{host}:{port} <this host>, then the same URL on your machine)")
    return out


# --- mutate -----------------------------------------------------------------


def refresh(scene: str | Path) -> dict[str, Any]:
    """Re-render previews after scene.resolved.json was edited by hand."""
    return _save(load(scene), _resolved_path(scene).parent)


def create_scene(spec: SceneSpec | dict[str, Any], out_dir: str | Path, candidates: int = DEFAULT_CANDIDATES,
                 preferences: bool = True) -> dict[str, Any]:
    """Preferences: apply the remembered preferences to what the spec leaves out."""
    from agentic_sim_world_gen import preferences as prefs
    from agentic_sim_world_gen.inputs import resolve

    spec = spec if isinstance(spec, SceneSpec) else SceneSpec.model_validate(spec)
    spec.inputs = [resolve(i, Path.cwd()) for i in spec.inputs]
    found = _room_words(spec, Path(out_dir).expanduser())
    with _searching(spec.objects, candidates):
        placements = [_placement_for(o, candidates, spec.room.place, spec.room.height) for o in spec.objects]
    applied = prefs.apply(spec, placements) if preferences else []
    s = ResolvedScene(spec=spec, placements=placements)
    msg = f"シーンを作成：{len(placements)} 物体の候補を検索して配置" + (f"（前回の好み {len(applied)} 件）" if applied else "")
    if spec.room.density not in (None, "sparse"):
        msg += "。" + _apply_density(s, candidates)
    st = _save(s, Path(out_dir).expanduser(), msg)
    if applied:
        st["preferences_applied"] = applied
    if found:
        st["surface_candidates"] = found
    return st


def _out_root(out: str | Path | None = None) -> Path:
    return Path(out or os.environ.get("AGENTIC_SIM_WORLD_GEN_OUT") or "~/agentic_sim_world_gen_scenes").expanduser()


def show_gallery(request: str = "", place: str | None = None, out: str | Path | None = None,
                 browser: bool = True) -> dict[str, Any]:
    """The examples and past scenes as a page to pick a start from: gallery.html under the output root,
    nearest first (the same place, the same family of places, words of the request). Returns the page's url, the
    nearest entries and `picked` (what the user clicked on the page since it was last shown)."""
    from agentic_sim_world_gen import gallery
    from agentic_sim_world_gen.preview import server
    from agentic_sim_world_gen.scene.questions import guess_place

    root = _out_root(out)
    root.mkdir(parents=True, exist_ok=True)
    place = place or guess_place(request)
    rows = gallery.rank(gallery.entries(root), place, request)
    gallery.write_page(root, rows, place)
    httpd = server.serve(root)
    host, port = httpd.server_address[:2]
    url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '') else host}:{port}/{gallery.PAGE}"
    keys = ("id", "title", "place", "size", "objects", "fixtures", "near")
    near = [r for r in rows if r.get("near")] or rows
    res: dict[str, Any] = {"url": url, "place": place, "entries": len(rows),
                           "near": [{k: r[k] for k in keys} for r in near[:gallery.NEAR]],
                           "picked": gallery.picked(root)}
    res["opened"] = bool(browser and server.open_browser(url))
    res["hint"] = ("show the url and name the near ones in a line each; the user picks on the page (`picked` on the "
                   "next show_gallery) or says one: start_from_example(<id>). None fits -> go on with create_scene")
    if not any(r.get("near") for r in rows):
        res["hint"] += "; nothing is near this place: say so, the list is everything there is"
    return res


def start_from_example(example: str | None = None, name: str | None = None, room: dict[str, Any] | None = None,
                       drop: list[str] | None = None, out: str | Path | None = None, **start: Any) -> dict[str, Any]:
    """Start a scene from a gallery entry: its SceneSpec under a new name, with `room` fields overridden
    (width, depth, height, place, name, floor, walls, lighting, ...) and the objects in `drop` left out, then
    start_scene. What came from the example (size, place, floor / walls / lighting, fixtures) is recorded as
    guesses with the basis 作例「…」から. example: an id from show_gallery; left out = what the user picked on
    the page."""
    from agentic_sim_world_gen import gallery

    root = _out_root(out)
    if not example:
        got = gallery.picked(root)
        if not got:
            raise ValueError("nothing picked on the gallery page yet: pass example=<id> (show_gallery lists them)")
        example = got["id"]
    spec = gallery.load_spec(example, root)
    title = spec.room.name if spec.room.name != "room" else spec.name
    data = spec.model_dump(mode="json", exclude_unset=True)  # unset stays unset: only what the example says is inherited
    data.setdefault("objects", [])
    data["assumptions"], data["inputs"] = [], []  # the source's own decisions and photos stay with it
    for k in ("notes", "requirements"):
        data.pop(k, None)
    changed = set(room or {})
    if changed & {"width", "depth"} and data["room"].get("plan"):
        raise ValueError("this example has a floor plan: its size comes from the plan (set_room / edit the plan after)")
    data["room"].update(room or {})
    ids = {o["id"] for o in data["objects"]}
    if missing := [i for i in drop or [] if i not in ids]:
        raise ValueError(f"drop: no such object(s) {missing} in {example}")
    gone = set(drop or [])
    kept = []
    for o in data["objects"]:  # what stood on / in front of a dropped object goes with it
        lay = o.get("layout") or {}
        refs = {v for v in lay.values() if isinstance(v, str)} | {(o.get("fill") or {}).get("on")}
        if o["id"] in gone or refs & gone:
            gone.add(o["id"])
            continue
        kept.append(o)
    data["objects"] = kept
    base = name or "near_" + example.split(":", 1)[-1]
    data["name"] = base
    n = 2
    while not name and (root / data["name"]).exists():
        data["name"] = f"{base}_{n}"
        n += 1
    new = SceneSpec.model_validate(data)
    st = start_scene(new, root / new.name, **start)
    d = Path(st["scene_dir"])
    rows = gallery.inherited(new, title, changed)
    set_assumptions(d, rows)
    gallery.clear_pick(root)
    started = st.get("started", {})
    out_st = status(d)
    out_st["started"] = started | {"from": {"example": example, "title": title, "inherited": [r["item"] for r in rows],
                                             "changed": sorted(changed), "dropped": sorted(gone)}}
    return out_st


START_STEPS = ("purpose", "windows", "preview", "draft")
NO_ISAAC = "Isaac Sim not found (ISAACSIM_PYTHON / ISAACSIM_PATH)"


def _has_isaac() -> bool:
    return isaac_python() is not None or live_available()


def _usd_fresh(d: Path) -> bool:
    from agentic_sim_world_gen.preview.page import STATE, scene_hash

    if not (d / "scene.usda").exists():
        return False
    try:
        made = (json.loads((d / STATE).read_text()).get("made_from") or {}).get("usd")
    except (OSError, ValueError):
        made = None
    return made == scene_hash(d)


def _check_brief(r: dict[str, Any]) -> dict[str, Any]:
    return {k: r[k] for k in ("verdict", "mode", "error", "screenshot", "gif", "report", "draw_load", "next") if r.get(k)}


def _draft_look(d: Path, draft: bool = True, **check: Any) -> dict[str, Any]:
    """Write the USD if it is not the scene as it is now, then look at it in Isaac Sim (draft: pictures only).
    Returns {usd, check} or {skipped: why}: open issues or layout options, a refused compose, no Isaac Sim."""
    st = status(d)
    if st["issues"]:
        return {"skipped": f"{len(st['issues'])} layout issue(s): fix them first", "stopped": "issues"}
    if st.get("layout_options"):
        return {"skipped": "layout options are open: choose_layout first", "stopped": "layout_options"}
    out: dict[str, Any] = {}
    if not _usd_fresh(d):
        try:
            out["composed"] = bool(compose_scene(d))
        except ValueError as e:
            return {"skipped": f"compose_scene: {e}", "stopped": "compose"}
    out["usd"] = str(d / "scene.usda")
    if not _has_isaac():
        out["check"] = {"skipped": NO_ISAAC}
        return out
    out["check"] = _check_brief(check_in_isaac(d, draft=draft, live=live_available(), **check))
    return out


def start_scene(spec: SceneSpec | dict[str, Any], out_dir: str | Path, candidates: int = DEFAULT_CANDIDATES,
                preferences: bool = True, purpose: str | None = None, purpose_given: bool = False,
                skip: list[str] | None = None, browser: bool = True, drafts: str | None = None,
                robot_radius: float | None = None) -> dict[str, Any]:
    """create_scene and what always followed it, in one call: the purpose (the one given, else demo as a
    guess, unless the spec has one), windows for the window openings, the preview page, and the first draft (USD +
    a look in Isaac Sim without physics, when the layout has no issue). skip: names of START_STEPS to leave out.
    Returns the status after all of it, with `started` saying what each step did."""
    from agentic_sim_world_gen.scene import purpose as pp

    skip = list(skip or [])
    if bad := [k for k in skip if k not in START_STEPS]:
        raise ValueError(f"skip: unknown step(s) {bad}; one of {', '.join(START_STEPS)}")
    if purpose:
        pp.get(purpose)  # an unknown one fails before anything is searched
    first = create_scene(spec, out_dir, candidates, preferences)
    d = Path(first["scene_dir"])
    started: dict[str, Any] = {}
    if "purpose" not in skip:
        s = load(d)
        if purpose or s.spec.purpose is None:
            set_purpose(d, purpose or "demo", robot_radius, given=bool(purpose) and purpose_given)
            started["purpose"] = (purpose or "demo") + ("" if purpose and purpose_given else " (a guess: say so)")
    if "windows" not in skip and any(o.kind == "window" for o in load(d).spec.room.openings):
        w = fill_windows(d)
        started["windows"] = {k: w[k] for k in ("added", "not_found", "not_found_hint") if w.get(k)} or "nothing to add"
    if drafts:  # three plans to pick from instead of one draft in 3D
        try:
            got = propose_drafts(d, None if drafts == "auto" else drafts)
            started["drafts"] = {"axis": got["layout_options"][0]["axis"], "options": [r["name"] for r in got["layout_options"]]}
        except ValueError as e:
            started["drafts"] = {"skipped": str(e)}
    if "preview" not in skip:
        started["preview"] = open_preview(d, browser)
    if "draft" not in skip:
        if status(d).get("conflicts"):  # ask which side wins before the pictures of a scene to be redone
            started["draft"] = {"skipped": "conflicting assumptions (status `conflicts`): ask its one question first",
                                "stopped": "conflicts"}
        else:
            started["draft"] = _draft_look(d)
    st = status(d)
    for k in ("preferences_applied", "surface_candidates"):
        if k in first:
            st[k] = first[k]
    st["started"] = started
    return st


def finish(scene: str | Path, draft: bool = False, steps: int = 240, gif: bool = False, orbit: int = 24,
           force: bool = False) -> dict[str, Any]:
    """The closing steps in one call: write the USD if it is behind the scene, run the Isaac Sim check,
    measure the recorded request items again and report the licenses. Stops at the first thing that needs a fix
    (`stopped`: issues / layout_options / compose / check) and says what in `next`. draft=True: only the USD and
    the pictures without physics (the first draft after fixing the layout).
    Without Isaac Sim the check is `skipped` and the rest still runs (`unverified`)."""
    d = _resolved_path(scene).parent
    out: dict[str, Any] = {"scene_dir": str(d), "done": False}
    last = status(d).get("last_check") or {}
    if not draft and last.get("verdict") == "passed" and not last.get("stale") and _usd_fresh(d):
        look: dict[str, Any] = {"usd": str(d / "scene.usda"), "check": {"verdict": "passed", "reused": "the scene has "
                                "not changed since the last check"}}
    else:
        look = _draft_look(d, draft=draft, steps=steps, gif=gif, orbit=orbit, force=force)
    out.update({k: v for k, v in look.items() if k != "skipped"})
    if why := look.get("skipped"):
        out["next"] = why
        if look["stopped"] == "issues":
            out["issues"] = status(d)["issues"]
        return out
    verdict = look["check"].get("verdict")
    if verdict in ("failed", "needs_fix"):
        out["stopped"] = "check"
        out["next"] = look["check"].get("next") or f"the check {verdict}: {look['check'].get('error')}"
        return out
    if draft:
        out["next"] = ("a first draft, physics not checked: show it to the user (check.png, the preview page); "
                       "after they agree, finish() runs the full check and the rest")
        return out
    s = load(d)
    if s.spec.requirements:
        st = check_request(d)
        out["requirements_summary"] = st["requirements_summary"]
        out["requirements_open"] = [r for r in st["requirements"] if r["status"] != "met"]
    else:
        out["requirements_hint"] = ("no request items recorded: check_request(requirements=[...]) with what the "
                                    "user asked for, once, to measure them")
    lic = license_brief(license_report(d))
    out["licenses"] = {k: lic[k] for k in ("objects", "attribution_lines", "warnings", "file") if lic.get(k)}
    if "skipped" in look["check"]:
        out["unverified"] = ["physics (no Isaac Sim on this machine): say so, do not call the scene checked"]
    unmet = [r for r in out.get("requirements_open", []) if r["status"] == "unmet"]
    out["done"] = not unmet
    out["next"] = (f"{len(unmet)} request item(s) not met (requirements_open): fix them and finish again, or tell "
                   "the user why not" if unmet else
                   "report to the user: what was made, the substitutes, the licenses (warnings first)"
                   + (", and that physics was not checked" if "unverified" in out else ""))
    return out


def _room_words(spec: SceneSpec, d: Path) -> dict[str, Any]:
    """Words given for the floor / walls / ceiling / zone floors in a new spec: searched like set_room does
    (the best material is applied, the candidates go to surfaces.json). Nothing found: the field's default (the
    ceiling stays on, by place), noted in the returned candidates."""
    from agentic_sim_world_gen.scene.looks import is_words
    from agentic_sim_world_gen.scene.spec import Room
    from agentic_sim_world_gen.scene.walls import is_per_wall

    data, found = spec.room.model_dump(), {}

    def search(target: str, words: str) -> Any:
        got, found[target] = _material_query(d, target, words, None)
        return got

    for target in ("floor", "walls", "ceiling"):
        if is_words(v := data[target]):
            got = search(target, v)
            data[target] = got if got is not None else True if target == "ceiling" else Room.model_fields[target].default
    if is_per_wall(data["walls"]):
        walls = {}
        for k, v in data["walls"].items():
            got = search(f"walls:{k}", v) if is_words(v) else v
            if got is not None:
                walls[k] = got
        data["walls"] = walls or Room.model_fields["walls"].default
    zones = {}
    for zid, v in (data.get("zone_floors") or {}).items():
        got = search(f"floor:{zid}", v) if v != "room" and is_words(v) else v
        if got is not None:
            zones[zid] = got
    data["zone_floors"] = zones
    if found:  # only the fields the spec gave: the rest stay open for the remembered preferences
        spec.room = Room.model_validate({k: v for k, v in data.items() if k in spec.room.model_fields_set})
    return found


def _problems(s: ResolvedScene) -> set[tuple[str, tuple[str, ...]]]:
    """Issues and usability warnings as comparable keys: added decor must not make either worse."""
    from agentic_sim_world_gen.layout.usability import usability

    _ensure_shelves(s)
    out = {(i.kind, tuple(sorted(i.objects))) for i in solve(s) + check(s)}
    try:
        out |= {("~" + i.kind, tuple(sorted(i.objects))) for i in usability(s)}
    except Exception:
        pass
    return out


def _drop(s: ResolvedScene, ids: set[str]) -> None:
    s.spec.objects = [o for o in s.spec.objects if o.id not in ids]
    s.placements = [p for p in s.placements if p.object_id not in ids]
    s.spec.assumptions = [a for a in s.spec.assumptions if a.object not in ids]
    s.spec.decor_skipped = [k for k in s.spec.decor_skipped if k.host not in ids]


def _bodies(s: ResolvedScene) -> int:
    """Objects Isaac Sim will simulate: one per array unit, one per shelf product (fill slots, after solve;
    static display stock, Fill.physics, does not count)."""
    n = 0
    for o in s.spec.objects:
        if o.fill is not None:
            n += sum(1 for slot in o.fill.slots if o.fill.simulated(slot[0]))
        elif o.array is not None:
            n += o.array.count * (o.array.rows or 1)
        else:
            n += 1
    return n


def _units(o: ObjectSpec) -> int:
    """Copies of the asset object `o` puts in the USD: fill slots (static stock too), array units, or one."""
    if o.fill is not None:
        return len(o.fill.slots)
    if o.array is not None:
        return o.array.count * (o.array.rows or 1)
    return 1


def _faces(a: AssetRecord | None) -> int:
    from agentic_sim_world_gen.scene import decor

    return decor.UNKNOWN_FACES if a is None or a.polygons is None else a.polygons


def _drawn(s: ResolvedScene) -> list[tuple[int, str, int, int]]:
    """(faces in all, object id, copies, faces of one) per object, heaviest first: what Isaac Sim has to hold
    (instancing does not save it: 1600 scanned toys of 0.86 M faces ran 31 GB out)."""
    assets = {p.object_id: p.asset for p in s.placements}
    rows = [(_units(o) * _faces(assets.get(o.id)), o.id, _units(o), _faces(assets.get(o.id))) for o in s.spec.objects]
    return sorted(rows, reverse=True)


def _light_stock(p: Placement) -> tuple[Placement, bool]:
    """The placement with its first good (not weak) candidate light enough to stock by the hundred selected;
    heavy = True when good ones exist but all are heavier (scanned assets)."""
    from agentic_sim_world_gen.scene import decor

    good = [i for i, c in enumerate(p.candidates) if not (c.get("match") or {}).get("weak")]
    light = [i for i in good if _faces(record_from_dict(p.candidates[i])) <= decor.STOCK_FACES_MAX]
    if not light:
        return p, bool(good)
    return (p if light[0] == p.selected else p.model_copy(update={"selected": light[0]})), False


def _free_levels(s: ResolvedScene, host: str, hang: bool = False) -> list[int]:
    """Shelf levels of fixture `host` that hold no product (fill slots are named <unit>_l<level>...); hang: its
    hook rows with a free hook that hold none (<unit>_h<row>...)."""
    import re

    from agentic_sim_world_gen.layout.fill import hook_rows, levels

    a = next((p.asset for p in s.placements if p.object_id == host), None)
    if a is None:
        return []
    used = {int(m.group(1)) for o in s.spec.objects if o.fill is not None and o.fill.on == host
            for n, *_ in o.fill.slots if (m := re.search(r"_h(\d+)" if hang else r"_l(\d+)", n))}
    if hang:
        return [i for i, r in enumerate(hook_rows(a.hooks or [])) if i not in used and any(not k["busy"] for k in r)]
    return [i for i in range(len(levels(a.shelves or []))) if i not in used]


def _apply_density(s: ResolvedScene, candidates: int = DEFAULT_CANDIDATES) -> str:
    """Re-make the decor objects for Room.density: drop the old ones, then add each planned item at the
    first spot that adds no problem; items with no proper asset (only substitutes) or no free spot are left out
    and kept in SceneSpec.decor_skipped with the reason.
    Each one added is recorded as a guessed assumption (「生活感のため追加」) so the user can take it out."""
    from agentic_sim_world_gen.scene import decor

    _drop(s, {o.id for o in s.spec.objects if o.decor})
    s.spec.decor_skipped = []
    density = s.spec.room.density
    _ensure_shelves(s)  # the plan needs the fixtures' boards
    items = decor.plan(s, density)
    added, skipped = _add_decor(s, items, candidates)
    if decor._rank(density):
        from agentic_sim_world_gen.scene.spec import DecorSkip

        kindless = [DecorSkip(label=f"陳列（{o.label or o.id}）", host=o.id, reason="no_kind")
                    for o in decor.unstocked(s, items)]
        s.spec.decor_skipped += kindless
        skipped += kindless
    msg = f"生活感「{decor.DENSITY_JA.get(density or 'sparse', density)}」：小物 {len(added)} 点を追加"
    return msg + _skipped_text(skipped)


SKIP_JA = {"no_asset": "素材なし", "no_spot": "置き場所なし", "crowded": "物が多すぎる", "no_kind": "業種が分からない",
           "heavy": "重い素材しかない"}


def _skipped_text(skipped: list[Any]) -> str:
    if not skipped:
        return ""
    words = [f"{k.label}（{SKIP_JA[k.reason]}" + (f"：{k.blockers[0]}" if k.blockers else "") + "）" for k in skipped]
    return f"、{len(words)} 点は見送り（" + "・".join(words[:6]) + ("…" if len(words) > 6 else "") + "）"


def _host_problems(problems: set[tuple[str, tuple[str, ...]]], host: str | None) -> list[str]:
    """The problems that touch this host (or one of its array units), as 'kind: a, b'."""
    if host is None:
        return []
    mine = [(k, objs) for k, objs in problems if k != "~empty_shelves"  # what the stock itself fixes
            and any(o == host or o.startswith(host + ".") for o in objs)]
    return [f"{k.lstrip('~')}: {', '.join(objs)}" for k, objs in sorted(mine)]


def _add_decor(s: ResolvedScene, items: list[Any], candidates: int = DEFAULT_CANDIDATES) -> tuple[list[ObjectSpec], list[Any]]:
    """Add each item at its first spot that adds no problem; the rest go to SceneSpec.decor_skipped."""
    from agentic_sim_world_gen.scene import decor
    from agentic_sim_world_gen.scene.spec import DecorSkip

    base = _problems(s)
    added, skipped = [], []
    found: dict[tuple, Any] = {}

    need = decor.stock_words(s)

    def placement(t: ObjectSpec):
        key = (t.query, tuple(t.alt_queries), t.min_size, t.max_size, t.fill is not None)
        if key not in found:
            p = _placement_for(t, candidates, s.spec.room.place)
            if t.fill is not None:  # stock the shop's own kind of goods only
                goods = t.query in s.spec.room.goods
                p.candidates = [c for c in p.candidates if decor.fits_words(need, r := record_from_dict(c))
                                and (not goods or decor.fits_goods(t.query, s.spec.room, r))]
            found[key] = _light_stock(p) if t.fill is not None else (p, False)
        return found[key][0]

    def heavy(t: ObjectSpec) -> bool:
        placement(t)
        return found[(t.query, tuple(t.alt_queries), t.min_size, t.max_size, t.fill is not None)][1]

    for item in items:
        if item.rest:  # the fixture's levels no product took
            free = _free_levels(s, item.host, item.tries[0].fill.hang)
            if not free:
                continue
            for t in item.tries:
                t.fill.levels = free
        p = placement(item.tries[0])
        if not item.rest and (p.asset is None or p.substitute):
            skipped.append(DecorSkip(label=item.label, host=item.host, reason="no_asset"))
            continue
        if not item.rest and heavy(item.tries[0]):
            skipped.append(DecorSkip(label=item.label, host=item.host, reason="heavy"))
            continue
        crowded = False
        for t in item.tries:
            if any(o.id == t.id for o in s.spec.objects):
                break  # same id already added (two hosts named alike): keep the first
            p = placement(t)
            if p.asset is None or p.substitute or heavy(t):
                continue
            a = p.asset
            if item.host is not None and t.layout is not None and t.layout.on:
                host = s.obj(item.host)
                ha = next(q.asset for q in s.placements if q.object_id == host.id)
                decor.match_array(t, a, host, ha)
            s.spec.objects.append(t)
            s.placements.append(p.model_copy(deep=True, update={"object_id": t.id}))
            if _problems(s) - base or (t.fill is not None and not t.fill.slots):
                _drop(s, {t.id})
                continue
            if _bodies(s) > decor.BODY_BUDGET or sum(r[0] for r in _drawn(s)) > decor.DRAW_BUDGET:
                _drop(s, {t.id})
                crowded = True
                continue
            added.append(t)
            break
        else:
            # the host's own problems (it overlaps a neighbour, stands outside the room) block the spots on it:
            # kept, so the item is tried again once they are fixed (_retry_decor). Leftover levels are not kept.
            if not item.rest:
                skipped.append(DecorSkip(label=item.label, host=item.host, reason="crowded" if crowded else "no_spot",
                                         blockers=[] if crowded else _host_problems(base, item.host)))
    for o in added:
        s.spec.assumptions.append(Assumption(item="追加", value=o.label or o.query, given=False, basis=decor.BASIS,
                                             object=o.id))
    s.spec.decor_skipped += skipped
    return added, skipped


def _retryable(s: ResolvedScene, problems: set[tuple[str, tuple[str, ...]]] | None = None) -> list[Any]:
    """Skipped decor whose host had problems then and has lost one of them now (the agent fixed the layout).
    A retry that fails again records the problems left, so it is tried once per fix, not on every save."""
    from agentic_sim_world_gen.scene import decor

    if decor._rank(s.spec.room.density) == 0:
        return []
    ids = {o.id for o in s.spec.objects}
    waiting = [k for k in s.spec.decor_skipped if k.reason == "no_spot" and k.blockers and k.host in ids]
    if not waiting:
        return []
    problems = _problems(s) if problems is None else problems
    return [k for k in waiting if set(k.blockers) - set(_host_problems(problems, k.host))]


def _retry_decor(s: ResolvedScene, candidates: int = DEFAULT_CANDIDATES) -> str:
    """Try again the decor left out only because its host had problems, now fixed. Other decor (and decor
    the user removed) is left as it is."""
    from agentic_sim_world_gen.scene import decor

    ready = _retryable(s)
    if not ready:
        return ""
    keys = {(k.host, k.label) for k in ready}
    s.spec.decor_skipped = [k for k in s.spec.decor_skipped if (k.host, k.label) not in keys]
    _ensure_shelves(s)
    items = [i for i in decor.plan(s, s.spec.room.density) if (i.host, i.label) in keys]
    added, skipped = _add_decor(s, items)
    if not added and not skipped:
        return ""
    return f"。小物を追加し直し：{len(added)} 点" + _skipped_text(skipped)


def _decor_for(s: ResolvedScene, hosts: set[str], candidates: int = DEFAULT_CANDIDATES) -> str:
    """Decor for objects added (or given another asset) after Room.density made the rest (a shelf removed
    and added again, or swapped for one that holds the products, stayed empty). Their old skip records go;
    decor already on them stays (the same id is not added twice)."""
    from agentic_sim_world_gen.scene import decor
    from agentic_sim_world_gen.scene.spec import DecorSkip

    if decor._rank(s.spec.room.density) == 0:
        return ""
    hosts = {o.id for o in s.spec.objects if o.id in hosts and not o.decor}
    if not hosts:
        return ""
    s.spec.decor_skipped = [k for k in s.spec.decor_skipped if k.host not in hosts]
    _ensure_shelves(s)
    plan = decor.plan(s, s.spec.room.density)
    items = [i for i in plan if i.host in hosts]
    added, skipped = _add_decor(s, items, candidates)
    kindless = [DecorSkip(label=f"陳列（{o.label or o.id}）", host=o.id, reason="no_kind")
                for o in decor.unstocked(s, plan) if o.id in hosts]
    s.spec.decor_skipped += kindless
    skipped += kindless
    if not added and not skipped:
        return ""
    return f"。小物を追加：{len(added)} 点" + _skipped_text(skipped)


def get_preferences(place: str | None = None) -> dict[str, Any]:
    """The remembered preferences: `defaults` (2+ scenes agree; create_scene applies them to what the
    spec leaves out) and `pending` (seen once). place: only the room items of that place."""
    from agentic_sim_world_gen import preferences as prefs

    return prefs.preferences(place)


def forget_preference(item: str | None = None, target: str | None = None) -> dict[str, Any]:
    """Forget remembered preferences: one item (lighting, lighting_scale, floor, walls, gap, aisle, spacing, asset),
    optionally only one target ("place:home", "chair", "office chair"); item None / "all" forgets everything."""
    from agentic_sim_world_gen import preferences as prefs

    return prefs.forget(item, target)


def _learn(correction: bool, item: str, target: str | None, scene_dir: Path, before: Any, after: Any,
           label: str | None = None) -> None:
    """Remember a correction the user asked for (correction=True) or made on the preview page."""
    from agentic_sim_world_gen import preferences as prefs

    if not target or not (correction or ACTOR.get() == "preview"):
        return
    try:
        prefs.observe(item, target, scene_dir, before, after, label)
    except Exception:  # remembering must never break the edit itself
        pass


def set_assumptions(scene: str | Path, assumptions: list[Assumption | dict[str, Any]], replace: bool = False,
                    remove: list[str] | None = None) -> dict[str, Any]:
    """Record the decisions behind the scene. Rows with the same (object, item) are replaced; replace=True
    drops the old list first. remove: items to drop ("<item>" for the room, "<object>:<item>" for an object)."""
    s = load(scene)
    rows = [a if isinstance(a, Assumption) else Assumption.model_validate(a) for a in assumptions]
    old = [] if replace else list(s.spec.assumptions)
    keys = {a.key() for a in rows}
    gone = set()
    for r in remove or []:
        obj, _, item = r.rpartition(":")
        gone.add((obj or None, item.strip().lower()))
    prev = {a.key(): a for a in old}
    for a in rows:
        a.feedback = None  # re-recording a row answers the user's feedback on it
        a.review = None  # and settles a row marked for review
        a.auto = False
        if not a.options and a.key() in prev:
            a.options = list(prev[a.key()].options)
    s.spec.assumptions = [a for a in old if a.key() not in keys and a.key() not in gone] + rows
    mine = [a for a in s.spec.assumptions if not a.auto]  # without the rows the service writes itself
    guessed = sum(not a.given for a in mine)
    out = _save(s, _resolved_path(scene).parent, f"仮定を記録：{len(mine)} 項目（推測 {guessed}）")
    # The preview shows the item as written; a Japanese user read 「pink wall」
    from agentic_sim_world_gen.preview.page import ITEM_JA

    english = [a.item for a in rows if not JA.search(a.item) and a.item.strip().lower() not in ITEM_JA]
    if english and scene_language(s) == "ja":
        out["hint"] = ((out["hint"] + " ") if out.get("hint") else "") + (
            f"items {english} are shown as written on the preview: write the item in the user's language "
            "(壁の色, not 'pink wall'), set_assumptions again with the same object and remove the old item")
    return out


def assumption_feedback(scene: str | Path, item: str, text: str, object: str | None = None) -> dict[str, Any]:
    """The user says an assumption row is wrong (preview page): keep their words on the row until the
    agent re-records it. Empty text withdraws the feedback. The scene itself is not changed here."""
    from datetime import datetime, timezone

    from agentic_sim_world_gen.preview.page import item_ja
    from agentic_sim_world_gen.scene.spec import AssumptionFeedback

    s = load(scene)
    key = (object or None, item.strip().lower())
    row = next((a for a in s.spec.assumptions if a.key() == key), None)
    if row is None:
        raise ValueError(f"no assumption '{item}'" + (f" for '{object}'" if object else ""))
    names = {o.id: o.label or o.id for o in s.spec.objects}
    label = (names.get(row.object, row.object) + "：" if row.object else "") + item_ja(row.item)
    text = " ".join(text.split())[:500]
    if text:
        row.feedback = AssumptionFeedback(text=text, at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        msg = f"{FEEDBACK_EVENT}{label}：「{text}」"
    else:
        row.feedback = None
        msg = f"{FEEDBACK_EVENT}{label}：指摘を取り消し"
    return _save(s, _resolved_path(scene).parent, msg, focus=row.object)


def add_note(scene: str | Path, text: str, position: tuple[float, float] | list[float] | None = None,
             object: str | None = None, view: str = "plan", frame: int | None = None, u: float | None = None,
             v: float | None = None, aspect: float | None = None) -> dict[str, Any]:
    """The user writes at a spot on the preview: on the top view (position, and the object clicked if any)
    or on a frame of the check's orbit viewer (frame, u / v = 0..1 from the image's top left, aspect = width /
    height), which is turned into the spot by the frame's camera. Kept for the agent until resolve_notes."""
    from datetime import datetime, timezone

    from agentic_sim_world_gen import notes as nt
    from agentic_sim_world_gen.layout.room import RoomGeometry
    from agentic_sim_world_gen.scene.spec import Note

    s = load(scene)
    d = _resolved_path(scene).parent
    text = " ".join(text.split())[:500]
    if not text:
        raise ValueError("empty note")
    if view == "3d":
        try:
            cams = (json.loads((d / "check.json").read_text()).get("orbit") or {}).get("cameras") or []
        except (OSError, ValueError):
            cams = []
        if frame is None or not 0 <= frame < len(cams) or cams[frame] is None or None in (u, v, aspect):
            raise ValueError("no camera for that view: write on the top view")
        spot = nt.hit(s, *nt.camera_ray(cams[frame], float(u), float(v), float(aspect)))
        if spot is None:
            raise ValueError("that spot is not on the floor or an object")
    elif position is not None:
        spot = nt.at_spot(s, float(position[0]), float(position[1]))
        if object:  # the object drawn there on the page (its footprint may be under another one)
            spot["object"] = object
    else:
        raise ValueError("no position")
    x0, y0, x1, y1 = RoomGeometry(s.spec.room).bounds
    if not (x0 - 0.05 <= spot["position"][0] <= x1 + 0.05 and y0 - 0.05 <= spot["position"][1] <= y1 + 0.05):
        raise ValueError("that spot is outside the room")
    n = 1 + max((int(x.id[1:]) for x in s.spec.notes if x.id[1:].isdigit()), default=0)
    note = Note(id=f"n{n}", text=text, view="3d" if view == "3d" else "plan", frame=frame if view == "3d" else None,
                at=datetime.now(timezone.utc).isoformat(timespec="seconds"), **spot)
    s.spec.notes.append(note)
    return _save(s, d, f"{NOTE_EVENT}{n}（{nt.where_ja(s, note)}）：「{text}」", focus=(note.object or "").split(".")[0] or None)


def resolve_notes(scene: str | Path, ids: list[str], reply: str | None = None) -> dict[str, Any]:
    """Drop notes the agent has handled (or the user withdraws on the page); `reply` goes in the history."""
    s = load(scene)
    want = {i.strip() for i in ids}
    gone = [x for x in s.spec.notes if x.id in want]
    if missing := want - {x.id for x in gone}:
        raise ValueError(f"no note {', '.join(sorted(missing))} (notes: {', '.join(x.id for x in s.spec.notes) or 'none'})")
    s.spec.notes = [x for x in s.spec.notes if x.id not in want]
    nums = "・".join(x.id[1:] for x in gone)
    if ACTOR.get() == "preview":
        msg = f"{NOTE_EVENT}{nums} を取り消し"
    else:
        msg = f"{NOTE_EVENT}{nums} に対応" + (f"：{' '.join(reply.split())[:200]}" if reply and reply.strip() else "")
    return _save(s, _resolved_path(scene).parent, msg)


def _notes_view(s: ResolvedScene) -> dict[str, Any]:
    """Notes the user wrote on the preview that the agent has not handled yet."""
    from agentic_sim_world_gen import notes as nt

    if not s.spec.notes:
        return {}
    rows = []
    for x in s.spec.notes:
        r = x.model_dump(exclude_none=True, exclude={"at"})
        r["where"] = nt.where_ja(s, x)
        if x.position is not None:
            if near := nt.near(s, *x.position, skip=x.object):
                r["near"] = near
        rows.append(r)
    return {"notes": rows, "notes_hint":
            "the user wrote these at spots on the preview (position in meters; object = what is at the spot; near = "
            "objects close by). Act on each as its words say (「ここに棚」= put one there, 「狭い」= make room there), "
            "then resolve_notes(ids, reply) with a short Japanese reply; ask in chat only if one is unclear"}


AUTO_BASIS = "自動（配置から）"  # basis of the rows _auto_assumptions writes
ROOM_ITEMS = ("size", "place", "floor", "walls", "lighting")  # what every scene decides about the room
_ITEM_WORDS = {"size": ("size", "dimension", "寸法", "広さ", "サイズ", "大きさ", "畳"), "place": ("place", "場所", "種類", "業種"),
               "floor": ("floor", "床"), "walls": ("wall", "壁"), "lighting": ("light", "照明", "明るさ")}


def _surface_words(v: Any) -> str:
    if isinstance(v, str):
        return v
    if isinstance(v, dict):
        return "、".join(f"{k}: {_surface_words(x)}" for k, x in v.items())
    return str(getattr(v, "name", None) or getattr(v, "material", None) or getattr(v, "color", None) or "指定の素材")


def _object_words(o: ObjectSpec, p: Placement) -> str:
    """One object as it stands, for its automatic assumption row: asset, size, count, position, facing."""
    a = p.asset
    if a is None:
        return "アセットなし"
    parts = [a.name, " × ".join(f"{v:.2f}" for v in a.size_m) + " m"]
    if (n := _units(o)) > 1:
        parts.append(f"{n} 個")
    if o.position is not None:
        parts.append(f"位置 ({o.position[0]:.2f}, {o.position[1]:.2f})")
    if (f := _facing_view(o, p).get("facing_deg")) is not None:
        parts.append(f"向き {f:g}°")
    return "、".join(parts)


# --- fixes that need no judgement ------------------------------------------------------------------------
AUTO_FIX_KINDS = ("too_close", "tips_over", "narrow_aisle")  # the order they are tried in
AUTO_FIX_TRIALS = 12  # layouts tried per save (each is a solve + check + usability)
AUTO_FIX_EXTRA = (0.0, 0.05, 0.1)  # a narrow aisle is measured on a grid: try a little more when the first is short
AUTO_FIX_ITEM = "自動で直した"  # item of the assumption row a fix leaves on its object
HANG_ABOVE = 0.05  # a TV hung behind its stand: this far above the stand's top [m]
PLACED_WORDS = ("position", "place", "layout", "wall", "facing", "aisle", "spacing", "位置", "場所", "配置", "壁", "向き",
                "通路", "間隔")  # a given assumption about one of these says the user put the object there
FIX_WHY = {"too_close": "関節のある椅子どうしが触れていた", "tips_over": "足が無く台の上で倒れる", "narrow_aisle": "通路が狭かった"}


def auto_fix_on(s: ResolvedScene) -> bool:
    return s.spec.auto_fix and os.environ.get("AGENTIC_SIM_WORLD_GEN_AUTO_FIX", "1") != "0" and ACTOR.get() != "preview"


def _user_placed(s: ResolvedScene) -> set[str]:
    """Objects the automatic fixes leave alone: moved on the preview or by a correction, or whose position the
    user gave (a given assumption about its position / wall / facing / aisle / spacing)."""
    out = {o.id for o in s.spec.objects if o.placed_by_user}
    return out | {a.object for a in s.spec.assumptions if a.object and a.given and not a.auto
                  and any(w in a.item.lower() for w in PLACED_WORDS)}


def _issue_list(s: ResolvedScene) -> list[tuple[tuple[str, tuple[str, ...]], Any]]:
    """Issues and usability warnings with the keys of _problems (solves the scene)."""
    from agentic_sim_world_gen.layout.usability import usability

    out = [((i.kind, tuple(sorted(i.objects))), i) for i in solve(s) + check(s)]
    try:
        out += [(("~" + i.kind, tuple(sorted(i.objects))), i) for i in usability(s)]
    except Exception:
        pass
    return out


def _apply_fix(s: ResolvedScene, fix: dict[str, Any], extra: float = 0.0) -> dict[str, Any] | None:
    """Make one Issue.fixes change on the scene; what was done, or None when it cannot be done here."""
    oid = fix.get("move") or fix.get("array") or fix.get("hang")
    o = next((x for x in s.spec.objects if x.id == oid), None)
    if o is None or o.fill is not None or o.decor or o.opening or o.covers:
        return None
    if "move" in fix:
        lay = o.layout
        # on a support it moves with it; against a wall, moving it leaves a dead strip behind (the agent's call)
        if o.position is None or (lay is not None and (lay.on or lay.against_wall or lay.ceiling)):
            return None
        dx, dy = fix["by"]
        n = math.hypot(dx, dy)
        if n < 1e-6:
            return None
        k = (n + extra) / n
        was = o.position
        _move(s, oid, (round(was[0] + dx * k, 3), round(was[1] + dy * k, 3)), None, None)
        return {"object": oid, "moved_m": [round(dx * k, 2), round(dy * k, 2)], "position": list(o.position),
                "words": f"{n + extra:.2f} m 動かした"}
    if "array" in fix:
        if o.array is None:
            return None
        item = "aisle" if "aisle" in fix else "spacing"
        was = getattr(o.array, item)
        now = round(was + fix[item] + extra, 2)
        setattr(o.array, item, now)
        return {"object": oid, item: [was, now], "words": f"{'通路' if item == 'aisle' else '間隔'}を {was:g} → {now:g} m に"}
    room = s.spec.room
    sup = next((x for x in s.spec.objects if o.layout is not None and x.id == o.layout.on), None)
    wall = sup.layout.against_wall if sup is not None and sup.layout is not None else None
    if wall not in ("north", "south", "east", "west") or room.plan is not None or sup.position is None \
            or not room.width or not room.depth:
        return None  # which wall is behind it is not known
    h = round(o.z + HANG_ABOVE, 2)
    o.facing_deg = None
    axis, ends = 0 if wall in ("north", "south") else 1, []
    for t in (0.0, 1.0):  # `along` runs over what the object's own width leaves free: measure it
        o.layout = Layout(against_wall=wall, along=t, wall_height=h)
        solve(s)
        ends.append(o.position[axis])
    if abs(ends[1] - ends[0]) < 1e-6:
        return None
    t = (sup.position[axis] - ends[0]) / (ends[1] - ends[0])
    o.layout = Layout(against_wall=wall, along=round(min(1.0, max(0.0, t)), 4), wall_height=h)
    return {"object": oid, "hung": {"wall": wall, "wall_height": h, "above": sup.id},
            "words": f"{sup.id} の後ろの壁（{wall}）に掛けた（下端 {h:g} m）"}


def _auto_fix(s: ResolvedScene, touched: set[str]) -> list[dict[str, Any]]:
    """Apply the fixes that need no judgement: a fix is kept only when its issue is gone and no issue or
    usability warning is new; else the scene stays as it was and the issue is reported as before. Objects the
    user placed and the ones this call changed (touched) are not moved, and a fix is made once per object and
    kind: when the issue is back (the agent undid the fix), it stays."""
    pinned = _user_placed(s) | touched
    # once per object and kind: an issue that is back on an object the server fixed was put back on purpose
    done = {(a.object, a.basis) for a in s.spec.assumptions if a.auto and a.item == AUTO_FIX_ITEM}
    fixed: list[dict[str, Any]] = []
    tried: set[str] = set()  # by what the fix does: every unit of an array carries the same one
    trials = 0
    now = _issue_list(s)
    while trials < AUTO_FIX_TRIALS:
        cand = next(((k, i, n, f) for kind in AUTO_FIX_KINDS for k, i in now if i.kind == kind
                     for n, f in enumerate(i.fixes)
                     if repr(f) not in tried and (t := f.get("move") or f.get("array") or f.get("hang")) not in pinned
                     and (t, FIX_WHY[kind]) not in done), None)
        if cand is None:
            break
        k, issue, n, fix = cand
        tried.add(repr(fix))
        before = {x for x, _ in now}
        for extra in AUTO_FIX_EXTRA if issue.kind == "narrow_aisle" else (0.0,):
            trials += 1
            trial = s.model_copy(deep=True)
            what = _apply_fix(trial, fix, extra)
            if what is None:
                break
            after = _issue_list(trial)
            if {x for x, _ in after} <= before - {k}:
                s.spec, s.placements, now = trial.spec, trial.placements, after
                fixed.append({"issue": issue.kind, "issue_objects": list(issue.objects), **what})
                break
    return fixed


def _record_fixes(s: ResolvedScene, fixed: list[dict[str, Any]]) -> str:
    """Leave an assumption row on each fixed object; the words for the history line."""
    words = []
    for f in fixed:
        text = f.pop("words")
        row = Assumption(item=AUTO_FIX_ITEM, object=f["object"], value=text, basis=FIX_WHY[f["issue"]], auto=True)
        s.spec.assumptions = [a for a in s.spec.assumptions if a.key() != row.key()] + [row]
        words.append(f"{_name(s, f['object'])} {text}")
    return "（自動で直した：" + "、".join(words) + "）"


def set_auto_fix(scene: str | Path, on: bool) -> dict[str, Any]:
    """Turn the automatic fixes on or off for this scene; on applies them now."""
    s = load(scene)
    s.spec.auto_fix = bool(on)
    return _save(s, _resolved_path(scene).parent, f"自動の手直しを{'有効' if on else '無効'}に")


def _auto_assumptions(s: ResolvedScene) -> None:
    """The service records what nobody recorded, as guesses from the scene as it stands — the room's size /
    place / floor / walls / lighting and one row per object (asset, size, count, position, facing) — and rewrites
    them at every save. The agent only records what the user said (given) and the guesses it has a basis for; a
    row it records for the same room item or object takes the automatic one's place. Feedback the user sent on an
    automatic row stays on it."""
    ids = {o.id for o in s.spec.objects}
    fixes = [a for a in s.spec.assumptions if a.auto and a.item == AUTO_FIX_ITEM and a.object in ids]  # they stay
    kept = [a for a in s.spec.assumptions if not a.auto]
    old = {a.key(): a for a in s.spec.assumptions if a.auto}
    room, rows = s.spec.room, []
    words = {
        "size": (f"{room.width:g} × {room.depth:g} m、" if room.width and room.depth else "間取りから、") + f"天井 {room.height:g} m",
        "place": room.place or "指定なし",
        "floor": _surface_words(room.floor),
        "walls": _surface_words(room.walls),
        "lighting": room.lighting + (f"（明るさ ×{room.lighting_scale:g}）" if room.lighting_scale != 1 else ""),
    }
    room_items = [a.item.lower() for a in kept if a.object is None]
    for k in ROOM_ITEMS:
        if not any(w in it for it in room_items for w in _ITEM_WORDS[k]):
            rows.append(Assumption(item=k, value=words[k], basis=AUTO_BASIS, auto=True))
    covered = {a.object for a in kept if a.object}
    by_id = {p.object_id: p for p in s.placements}
    for o in s.spec.objects:
        if o.id not in covered and o.id in by_id:
            rows.append(Assumption(item="asset", object=o.id, value=_object_words(o, by_id[o.id]), basis=AUTO_BASIS, auto=True))
    for r in rows:
        if prev := old.get(r.key()):
            r.feedback, r.options = prev.feedback, prev.options
    s.spec.assumptions = kept + fixes + rows


def _assumption_view(s: ResolvedScene) -> dict[str, Any]:
    """The recorded assumptions (guesses first). The rows the service wrote itself are only counted: they
    repeat the scene, and would come back in every reply as they follow each move."""
    rows = sorted((a for a in s.spec.assumptions if not a.auto or a.feedback or a.item == AUTO_FIX_ITEM), key=lambda a: a.given)
    out: dict[str, Any] = {"assumptions": [a.model_dump(exclude_none=True, exclude={"auto"}) for a in rows]}
    if n := sum(a.auto and a.item != AUTO_FIX_ITEM for a in s.spec.assumptions):
        out["assumptions_auto"] = {
            "count": n,
            "hint": "the room items and objects you recorded nothing for are on the preview as guesses taken from "
                    "the scene (自動). Record with set_assumptions only what the user said (given=true) and the "
                    "guesses you have a reason for (basis); no need to repeat assets, counts or positions",
        }
    return out


def check_request(scene: str | Path, requirements: list[Requirement | dict[str, Any]] | None = None,
                  replace: bool = True) -> dict[str, Any]:
    """Match the request against the result: store the items (replace=True drops the old list; False
    replaces items with the same text and adds the rest), measure their checks on the placed scene and save.
    Without requirements, re-measures the stored ones (after fixing). Returns status with `requirements`."""
    from agentic_sim_world_gen.layout.request import evaluate, summary

    s = load(scene)
    if requirements is not None:
        rows = [r if isinstance(r, Requirement) else Requirement.model_validate(r) for r in requirements]
        texts = {r.text for r in rows}
        old = [] if replace else [r for r in s.spec.requirements if r.text not in texts]
        s.spec.requirements = old + rows
    n = summary(evaluate(s))
    return _save(s, _resolved_path(scene).parent,
                 f"指示との突き合わせ：満たした {n['met']} / 満たしていない {n['unmet']} / 確かめられない {n['unknown']}")


def _request_view(s: ResolvedScene) -> dict[str, Any]:
    """The request items with their measured checks, if recorded."""
    if not s.spec.requirements:
        return {}
    from agentic_sim_world_gen.layout.request import evaluate, summary

    try:
        rows = evaluate(s)
    except Exception as e:  # never break status over a check
        return {"requirements_error": f"{type(e).__name__}: {e}"}
    out: dict[str, Any] = {"requirements": rows, "requirements_summary": summary(rows)}
    if any(r["status"] != "met" for r in rows):
        out["requirements_hint"] = ("fix the unmet items (layout / select_asset / set_array ...) and call check_request "
                                    "again without arguments; report what stays unmet or unknown to the user honestly")
    return out


def set_input(scene: str | Path, inp: InputRef | dict[str, Any], seen_in: list[str] | None = None) -> dict[str, Any]:
    """Add an input (photo, drawing) or replace the one with the same id, e.g. to adjust its camera.
    seen_in = the ids of the objects this photo shows: recorded on each object's seen_in (the others lose it)."""
    from agentic_sim_world_gen.inputs import resolve

    s = load(scene)
    inp = inp if isinstance(inp, InputRef) else InputRef.model_validate(inp)
    if seen_in is not None:
        ids = {o.id for o in s.spec.objects}
        if bad := sorted(set(seen_in) - ids):
            raise ValueError(f"no such objects: {', '.join(bad)}")
        for o in s.spec.objects:
            keep = [i for i in o.seen_in or [] if i != inp.id] + ([inp.id] if o.id in seen_in else [])
            o.seen_in = keep or None
    old = next((i for i in s.spec.inputs if i.id == inp.id), None)
    if old is not None:  # keep recorded fields the caller left out (validated again: a reference has no camera)
        inp = InputRef.model_validate({**old.model_dump(), **inp.model_dump(exclude_unset=True)})
    inp = resolve(inp, Path.cwd())
    s.spec.inputs = [i for i in s.spec.inputs if i.id != inp.id] + [inp]
    out = _save(s, _resolved_path(scene).parent, f"入力 {inp.id} を記録")
    if w := _camera_warnings(s, inp.id):
        out["warnings"] = out.get("warnings", []) + w
    if old is not None and old.camera is not None and inp.camera != old.camera:
        out["warnings"] = out.get("warnings", []) + [
            f"moved the camera of {inp.id}: it should stay where the photo was taken. Only change it to match the "
            "photo's view better (horizon, landmarks); to move things in the picture, move the objects. Then "
            "compare_photo again"]
    return out


def _camera_warnings(s: ResolvedScene, input_id: str | None = None) -> list[str]:
    """A photo camera inside a placed object renders that object's inside (black, or one flat face)."""
    from agentic_sim_world_gen.layout.validate import cameras_inside

    out = [f"camera of {cid} is inside {oid}: its render shows the inside of {oid} - the real room has nothing "
           f"there, so move {oid} (or check that camera's landmarks)"
           for cid, oid in cameras_inside(s) if input_id is None or cid == input_id]
    room = s.spec.room
    if room.plan is not None or room.outdoor:
        return out
    for i in s.spec.inputs:  # a fitted camera beyond a wall renders the wall's back
        if i.camera is None or (input_id is not None and i.id != input_id):
            continue
        x, y, z = i.camera.position
        if not (0 < x < room.width and 0 < y < room.depth and 0 < z < room.height):
            out.append(f"camera of {i.id} is outside the room at {[round(v, 2) for v in i.camera.position]} (room "
                       f"{room.width} x {room.depth} x {room.height} m): its render shows a wall from behind - fine "
                       "if it stood in a doorway, else check that photo's landmarks (fit_photos' `outside`)")
    return out


def _photo_placement(s: ResolvedScene, d: Path) -> dict[str, Any]:
    """Furniture placed away from where the photos put it (photo_fit.json's located objects)."""
    from agentic_sim_world_gen.layout.validate import located_misses
    from agentic_sim_world_gen.photo_fit import located_objects

    f = d / "photo_fit.json"
    if not f.exists():
        return {}
    try:
        objects, _ = located_objects(json.loads(f.read_text()).get("result", {}).get("located") or [])
    except (OSError, ValueError, AttributeError):
        return {}
    exempt = _photo_exempt(d)
    miss, unknown = located_misses(s, objects)
    miss = [m for m in miss if m["id"] not in exempt]
    ids = {o.id for o in s.spec.objects}
    unlocated = [o.id for o in s.spec.objects if o.id not in objects and o.id not in exempt and _floor_furniture(o)]
    one_point = [k for k, v in objects.items() if len(v["points"]) == 1 and k in ids and k not in exempt]
    if not miss and not unknown and not unlocated and not one_point:
        return {"not_in_photos": exempt} if exempt else {}
    out: dict[str, Any] = {}
    if miss:
        out["misplaced"] = miss
    if unknown:
        out["located_unknown"] = unknown
    if unlocated:
        out["unlocated"] = unlocated
    if one_point:
        out["one_point"] = one_point
    if exempt:
        out["not_in_photos"] = exempt
    if not miss and not unknown and not unlocated:
        out["hint"] = ("one_point: these rest on one located point, a foot or corner, not their middle - add 1-3 "
                       "more feet / corners (a diagonal one) in the frames, fit again and place_from_photos")
        return out
    if not miss and not unknown:
        out["hint"] = ("unlocated: these stand on the floor but no fit_photos locate has their object id - add 2-4 "
                       "feet / corners of each in the frames that show them (another object of the same kind seen "
                       "in the frames gets its own id), fit again and place_from_photos")
        return out
    out["hint"] = ("the photos (fit_photos locate with object) put these elsewhere: place_from_photos moves each "
                   "to its located feet (the asset's footprint fitted to them) instead of a relation guess"
                   + ("; `there` is what now stands at that spot - move it too" if any("there" in m for m in miss)
                      else "")
                   + ("; located_unknown: no object has that id - add it or fix the locate's object" if unknown
                      else "")
                   + ("; unlocated: floor furniture with no locate - locate it too" if unlocated else "")
                   + ("; one_point: add more feet of these" if one_point else ""))
    return out


PHOTO_EXEMPT = "not_in_photos.json"  # {object id: why the frames cannot locate it}, kept across fits


def _photo_exempt(d: Path) -> dict[str, str]:
    try:
        j = json.loads((d / PHOTO_EXEMPT).read_text())
    except (OSError, ValueError):
        return {}
    return {str(k): str(v) for k, v in j.items() if v} if isinstance(j, dict) else {}


def _set_photo_exempt(d: Path, given: dict[str, str]) -> None:
    ex = _photo_exempt(d)
    for k, v in given.items():
        if v:
            ex[k] = v
        else:
            ex.pop(k, None)
    (d / PHOTO_EXEMPT).write_text(json.dumps(ex, ensure_ascii=False, indent=1))


def _photo_gate(s: ResolvedScene, d: Path) -> str | None:
    """Why compose_scene stops: furniture the photos (photo_fit.json) do not place yet. A video run saw
    the warnings after every call and still placed by eye instead of fitting again."""
    pp = _photo_placement(s, d)
    why = [f"{k}: {', '.join(m['id'] if isinstance(m, dict) else m for m in pp[k])}"
           for k in ("misplaced", "located_unknown", "unlocated", "one_point") if pp.get(k)]
    if not why:
        return None
    return ("the photos do not place all the furniture yet (" + "; ".join(why) + "). Pick 2-4 feet / corners of "
            "each in the frames (locate with `object`, `edge` front / back), run fit_photos again with this scene folder "
            "and place_from_photos (misplaced: it moves them there). Only for one the frames do not show on the floor, "
            "compose_scene(not_in_photos={id: why}) - say so in the report")


def _floor_furniture(o: ObjectSpec) -> bool:
    """An object standing on the floor on its own: not decor, a door / window piece, on something, hung from the
    ceiling or on a wall (z above the floor without a layout)."""
    if o.decor or o.opening is not None:
        return False
    if o.layout is not None and (o.layout.on or o.layout.ceiling):
        return False
    return not (o.layout is None and (o.z or 0) > 0.05)


def _placement_warnings(s: ResolvedScene, d: Path) -> list[str]:
    pp = _photo_placement(s, d)
    w = [f"{m['id']} is placed at {m['placed']} but the photos show it at {m['located']} ({m['off_m']} m off"
         + (f"; {', '.join(m['there'])} stands there" if m.get("there") else "") + ")"
         for m in pp.get("misplaced", [])]
    return (w + [f"located object {k} is not in the scene" for k in pp.get("located_unknown", [])]
            + ([f"not located in the photos: {', '.join(pp['unlocated'])}"] if pp.get("unlocated") else [])
            + ([f"located by one point only: {', '.join(pp['one_point'])}"] if pp.get("one_point") else []))


COMPASS_JA = ("東", "北東", "北", "北西", "西", "南西", "南", "南東")


VARIANTS_DIR = "variants"  # <scene>/variants/{index.html, summary.json, NN/}


def make_variants(scene: str | Path, count: int = 4, seed: int = 0, vary: list[str] | None = None,
                  size_jitter: float = 0.1, compose: bool = True, tries: int = 4) -> dict[str, Any]:
    """Make `count` variations of the scene, each a scene of its own in <scene>/variants/NN/: other assets
    (among the first good candidates, never a substitute), objects against a wall moved along it, floor / wall
    presets usual for the place, and the room size within ±size_jitter (rectangular rooms). vary: which of
    assets / layout / materials / size (all by default). A draw with more layout issues than the source is drawn
    again (up to `tries`; the one with the fewest is kept and flagged). Each variant gets its scene.usda (compose)
    and LICENSES.md; <scene>/variants/index.html shows them side by side. The same seed gives the same variants;
    running it again replaces the folder."""
    import random

    from agentic_sim_world_gen import variants

    s = load(scene)
    d = _resolved_path(scene).parent
    if s.spec.layout_options:
        raise ValueError("layout options are still open: choose_layout first")
    if not 1 <= count <= 50:
        raise ValueError("count: 1-50")
    kinds = tuple(vary) if vary else variants.KINDS
    base_issues = len(_issues(s))
    data = variants.absolute_paths(s.model_dump(mode="json"), d.resolve())
    out = d / VARIANTS_DIR
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir()
    rows = []
    for n in range(count):
        best, drawn = None, 0
        for t in range(max(1, tries)):
            drawn = t + 1
            c = ResolvedScene.model_validate(data)
            changes = variants.vary(c, random.Random(f"{seed}:{n}:{t}"), kinds, size_jitter)
            k = len(_issues(c))
            if best is None or k < best[0]:
                best = (k, c, changes, t)
            if k <= base_issues:
                break
        k, c, changes, t = best
        vd = out / f"{n:02d}"
        c.spec.name = f"{s.spec.name}_v{n:02d}"
        c.spec.assumptions = [a for a in c.spec.assumptions if a.item != "variant"] + [Assumption(
            item="variant", value=f"{d.name} の {n:02d}（seed {seed}）", given=False,
            basis="make_variants：" + ("、".join(x["text"] for x in changes) or "変更なし"))]
        st = _save(c, vd, f"バリエーション {n:02d}（{d.name}、seed {seed}）：" + ("、".join(x["text"] for x in changes) or "変更なし"))
        row = {"name": f"{n:02d}", "dir": str(vd), "issues": len(st["issues"]), "tries": drawn, "changes": changes,
               **({"more_issues": True} if k > base_issues else {})}
        if compose:
            try:
                compose_scene(vd, log=False)
                license_report(vd)
                row["usd"] = str(vd / "scene.usda")
            except Exception as e:  # a variant that does not compose is reported, the others still are made
                row["compose_error"] = f"{type(e).__name__}: {e}"
        (vd / "variant.json").write_text(json.dumps({"source": str(d.resolve()), "seed": seed, "index": n, **row},
                                                    ensure_ascii=False, indent=1), encoding="utf-8")
        rows.append(row)
    variants.write_index(out, s, rows, base_issues, seed, kinds)
    warnings = [f"variant {r['name']}: {r['issues']} issues after {r['tries']} draws (the source has {base_issues})"
                for r in rows if r.get("more_issues")]
    warnings += [f"variant {r['name']}: compose failed ({r['compose_error']})" for r in rows if "compose_error" in r]
    if not any(r["changes"] for r in rows):
        warnings.append("nothing to vary: no object has 2+ good candidates or stands against a wall, the finishes "
                        "are not presets, and the room is not a rectangle")
    _event(scene, f"バリエーションを {count} 件作成（seed {seed}）：{VARIANTS_DIR}/index.html")
    return {"ok": True, "dir": str(out), "index": str(out / "index.html"), "source_issues": base_issues,
            "variants": [{k2: v for k2, v in r.items() if k2 != "changes"} | {"changes": [x["text"] for x in r["changes"]]}
                         for r in rows], "warnings": warnings}


def place_from_photos(scene: str | Path, objects: list[str] | None = None, apply: bool = True,
                      outlines: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Put furniture where the photos show it: each located object (photo_fit.json, fit_photos with
    `locate` + `object`) gets the pose of its selected asset's footprint fitted to its feet (photo_place.fit_pose),
    its layout is cleared and an assumption row 位置 (basis 写真から当てはめ) records it. objects: only these ids;
    apply=False only reports. Skipped: arrays, objects on something / hung / in an opening, one-point objects.

    An object with `outlines` is then refined in the photos themselves (photo_refine.refine): the asset's box
    seen by the fitted cameras against the feet's pixels and its outlines ([{object, photo, box: [u0, v0, u1, v1]}], a rectangle
    around the object in a photo; kept in photo_fit.json, so later calls use them again). Each row gets `px` (rms
    of what was compared) and `left` lists the objects still to look at (loose, skipped)."""
    from agentic_sim_world_gen import photo_refine
    from agentic_sim_world_gen.layout.array import placed_size
    from agentic_sim_world_gen.layout.frame import front_local_deg, norm_deg
    from agentic_sim_world_gen.photo_fit import located_objects
    from agentic_sim_world_gen.photo_place import MISFIT_M, fit_pose

    s = load(scene)
    d = _resolved_path(scene).parent
    f = d / "photo_fit.json"
    if not f.exists():
        return {"ok": False, "error": "no photo_fit.json in the scene folder: run fit_photos with out_dir = the "
                "scene folder and `locate` (2-4 feet / corners per object, each with `object`)"}
    fit_json = json.loads(f.read_text())
    res, req = fit_json.get("result", {}), fit_json.get("request") or {}
    located = res.get("located") or []
    cams = {k: tuple(v["position"][:2]) for k, v in (res.get("cameras") or {}).items() if v.get("position")}
    found, _ = located_objects(located)
    ids = {o.id for o in s.spec.objects}
    # The cameras as fitted, to draw the boxes into the photos
    views = {k: photo_refine.camera(v, tuple(ph["size"])) for k, v in (res.get("cameras") or {}).items()
             if "yaw_deg" in v and "pitch_deg" in v and (ph := (req.get("photos") or {}).get(k)) and ph.get("size")}
    drawn: dict[tuple[str, str], tuple] = {(o["object"], o["photo"]): tuple(o["box"])
                                           for o in fit_json.get("outlines") or []}
    bad_outlines = []
    for o in outlines or []:
        b = o.get("box") or ()
        if o.get("photo") not in views or len(b) != 4 or not (b[0] < b[2] and b[1] < b[3]):
            bad_outlines.append({"object": o.get("object"), "photo": o.get("photo"),
                                 "why": "not a fitted photo of photo_fit.json" if o.get("photo") not in views
                                 else "box is [u0, v0, u1, v1] in pixels, u0 < u1 and v0 < v1"})
            continue
        drawn[(o["object"], o["photo"])] = tuple(float(x) for x in b)
    if outlines and apply:
        fit_json["outlines"] = [{"object": k[0], "photo": k[1], "box": list(v)} for k, v in sorted(drawn.items())]
        f.write_text(json.dumps(fit_json, ensure_ascii=False, indent=1))
    outlined = {k[0] for k in drawn}
    want = list(objects) if objects else sorted(k for k in set(found) | outlined if k in ids)
    placed, skipped, warnings = [], [], []
    for oid in want:
        if oid not in ids:
            skipped.append({"id": oid, "why": "no such object in the scene"})
            continue
        if oid not in found and oid not in outlined:
            skipped.append({"id": oid, "why": "not located in photo_fit.json"})
            continue
        o, pl = s.obj(oid), _placement(s, oid)
        if o.array is not None:
            skipped.append({"id": oid, "why": "an array: place the block with set_array / set_layout"})
            continue
        if not _floor_furniture(o):
            skipped.append({"id": oid, "why": "not on the floor on its own (on something, hung or in an opening)"})
            continue
        if pl.asset is None:
            skipped.append({"id": oid, "why": "no asset selected"})
            continue
        v = found.get(oid) or {"points": {}, "photos": []}
        outs = [(k[1], b) for k, b in drawn.items() if k[0] == oid]
        if len(v["points"]) < 2 and not (outs and o.position is not None):
            skipped.append({"id": oid, "why": "one point only: add 1-3 more feet / corners and fit again"
                            if v["points"] else "an outline only and no position yet: locate a foot too"})
            continue
        edges = v.get("edges", {})
        pts = [(tuple(xy), edges.get(name)) for name, xy in v["points"].items()]
        w, dd, tall = placed_size(o, pl.asset)
        front = front_local_deg(pl.asset, o)
        on_x = abs(math.cos(math.radians(front))) >= abs(math.sin(math.radians(front)))
        size = (w, dd) if on_x else (dd, w)
        side = lambda e: [cams[lo["photo"]] for lo in located  # noqa: E731
                          if lo.get("object") == oid and lo.get("edge") == e and lo.get("photo") in cams]
        now = norm_deg(o.facing_deg if o.facing_deg is not None else o.yaw_deg + front)
        if len(pts) >= 2:
            r = fit_pose(pts, size, now, side("front"), side("back"))
        else:  # outlines (and at most one foot): start where it stands
            r = {"center": [o.position[0], o.position[1]], "facing_deg": round((now + 180) % 360 - 180, 1),
                 "rms_m": 0.0, "facing_from": "kept"}
        if "error" in r:
            skipped.append({"id": oid, "why": r["error"]})
            continue
        # In the photos: the feet's pixels (z = 0) and the outlines against the asset's box
        feet_px = [(lo["photo"], tuple(lo["px"]), lo.get("edge")) for lo in req.get("locate") or []
                   if lo.get("object") == oid and not lo.get("z") and lo.get("photo") in views]
        fine = None
        # only with an outline: feet alone fit no better in pixels than on the floor (0.28 against 0.30 m in
        # refine_eval.py), and the near feet weigh more there, which pulls an asset bigger than the real thing
        if views and outs:
            fine = photo_refine.refine((size[0], size[1], tall), (r["center"][0], r["center"][1], r["facing_deg"]),
                                       feet_px, outs, views,
                                       [tuple(o.position[:2])] if o.position is not None else None)
            if "error" in fine:
                fine = None
            else:
                r = {**r, "center": fine["center"],
                     **({"facing_deg": fine["facing_deg"], "facing_from": "photos"} if fine["turned"] else {})}
        before = [round(x, 2) for x in o.position] if o.position is not None else None
        row = {"id": oid, "position": [round(x, 2) for x in r["center"]], "facing_deg": r["facing_deg"],
               "rms_m": r["rms_m"], "facing_from": r["facing_from"], "points": len(pts),
               "photos": sorted(set(v["photos"]) | {k for k, _ in outs}),
               **({"px": fine["rms_px"], "px_was": fine["rms_px_was"], "outlines": fine["outlines"]} if fine else {}),
               "size_m": [round(size[0], 2), round(size[1], 2)],
               **({"moved_m": round(math.dist(before, r["center"]), 2)} if before else {}),
               **({"ties": r["ties"]} if "ties" in r else {})}
        if r["rms_m"] > MISFIT_M:
            span = v.get("extent_m") or ([v["span_m"]] if "span_m" in v else None)
            warnings.append(f"{oid}: the feet are {r['rms_m']} m off the asset's {size[0]:.2f} x {size[1]:.2f} m "
                            "footprint - the asset is not the size of the real one (min_size / max_size"
                            + (f"; the points span {span} m" if span else "") + ") or a point is on something else")
        if fine and fine["rms_sigma"] > photo_refine.LOOSE:
            row["loose"] = True
            warnings.append(f"{oid}: its box is {fine['rms_px']} px off its feet / outlines in the photos - the "
                            "asset is not the size of the real one, or a pick is on another object")
        if r["facing_from"] == "unknown":
            warnings.append(f"{oid}: the points do not tell its front - mark front / back feet (`edge`) or give "
                            "facing_deg")
        elif r["facing_from"] == "kept" and "ties" in r:
            warnings.append(f"{oid}: the points fit facing {r['ties']} deg alike, kept the one nearest its facing "
                            "now - mark front / back feet (`edge`) to tell")
        placed.append(row)
        if apply:
            o.layout = o.opening = None
            o.position = (r["center"][0], r["center"][1])
            o.facing_deg = r["facing_deg"]
            ja = COMPASS_JA[round(norm_deg(r["facing_deg"]) % 360 / 45) % 8]
            s.spec.assumptions = [a for a in s.spec.assumptions if a.key() != (oid, "位置")] + [Assumption(
                item="位置", object=oid, given=False, basis="写真から当てはめ",
                value=f"({row['position'][0]}, {row['position'][1]}) m、{ja}向き（写真 {len(row['photos'])} 枚・足元 "
                      f"{len(pts)} 点、ずれ {r['rms_m']:.2f} m"
                      + (f"、写真上 {fine['rms_px']:.0f} px" + (f"・輪郭 {len(outs)}" if outs else "") if fine else "")
                      + "）")]
    left = [p["id"] for p in placed if p.get("loose") or p["rms_m"] > MISFIT_M] + [k["id"] for k in skipped]
    out: dict[str, Any] = {"ok": True, "placed": placed, **({"skipped": skipped} if skipped else {}),
                           **({"outlines_skipped": bad_outlines} if bad_outlines else {}),
                           **({"left": left} if left else {}),
                           "warnings": warnings, "applied": bool(apply and placed)}
    if apply and placed:
        st = _save(s, d, f"写真から家具の位置を当てはめ：{len(placed)} 物体", placed[0]["id"])
        out["issues"] = st["issues"]
        if st.get("photo_placement"):
            out["photo_placement"] = st["photo_placement"]
    return out


def extract_frames(video: str | Path, out_dir: str | Path, max_frames: int = 8, min_frames: int = 4,
                   url: str | None = None, title: str | None = None, author: str | None = None,
                   license: str | None = None, start_s: float | None = None,
                   end_s: float | None = None, times: list[float] | None = None,
                   rotate: int | None = None) -> dict[str, Any]:
    """Pick 4-8 sharp frames of a walk-through video where the view changed (agentic_sim_world_gen.video), write them to
    <out_dir>/inputs/<video stem>_tNNN_NN.jpg with a <stem>.source.json (the video's source + video + time_s)
    and a contact sheet. start_s / end_s: only that part of the video (one room of a walk through several).
    times: write exactly the frames at these seconds instead of choosing (all of them: the scene's frames of this
    video are replaced by them; e.g. the earlier times with a frame video_poses calls weak moved to its better_s).
    rotate: turn the frames 90 / 180 / 270 degrees clockwise (a video that comes out lying on its side or upside
    down: no rotation metadata in the file); None keeps the turn of the frames of this video already in
    <out_dir>/inputs (0 if none). If out_dir already holds a scene, the frames are recorded as photo inputs
    (replacing earlier frames of the same video); otherwise `inputs` is ready for create_scene's spec.inputs."""
    from agentic_sim_world_gen import video as vid
    from agentic_sim_world_gen.inputs import missing_source, resolve

    d = Path(out_dir).expanduser()
    if rotate is None:
        rotate = _frames_rotate(d / "inputs", video)
    try:
        r = vid.extract(video, d / "inputs", max_frames, min_frames,
                        source={"url": url, "title": title, "author": author, "license": license},
                        start_s=start_s, end_s=end_s, times=times, rotate=rotate)
    except (vid.FfmpegMissing, FileNotFoundError, ValueError) as e:
        return {"ok": False, "error": str(e)}
    refs = [resolve(InputRef(id=f["id"], kind="photo", path=f["path"])) for f in r["frames"]]
    missing = sorted({k for i in refs for k in missing_source(i)})
    out: dict[str, Any] = {"ok": True, **r, "inputs": [i.model_dump(mode="json", exclude_none=True) for i in refs]}
    if missing:
        out["missing_source"] = missing
        out["hint_source"] = (f"the video's {', '.join(missing)} is not recorded: ask the user where it came from and "
                              f"put {Path(r['video']).stem}.source.json next to the video (or pass url / license), "
                              "then extract again")
    if _resolved_path(d).exists():
        s = load(d)
        cams = {i.id: i.camera for i in s.spec.inputs if i.camera is not None}
        for i in refs:  # the same frame extracted again keeps the camera set for it
            i.camera = cams.get(i.id)
        s.spec.inputs = [i for i in s.spec.inputs if i.video != r["video"] and i.id not in {x.id for x in refs}] + refs
        _save(s, d, f"動画 {Path(r['video']).name} から {len(refs)} コマを入力に記録")
        out["recorded"] = True
    else:
        out["recorded"] = False
    if r.get("people_frames"):
        out["hint_people"] = (f"the people detector ({r['people_check']}) found people in "
                              f"{', '.join(r['people_frames'])}: do not use these frames / this video")
    elif r.get("people_check") == "none":
        out["hint_people"] = "no people detector is installed (opencv): check the frames for people yourself"
    turned = f" (turned {r['rotate']} degrees clockwise)" if r["rotate"] else ""
    out["hint"] = ('PEOPLE: look at the sheet and every frame (Read) before using them. If anyone is visible in any frame, stop: do not use the video (or pick a start_s/end_s range without people and extract again) and tell the user'
                   f". UPRIGHT: check on the sheet that the frames{turned} stand upright (floor at the bottom, door "
                   "frames and wall corners vertical). A phone video without rotation metadata comes out on its side: "
                   "extract again with rotate added to the turn so far (+90 if the floor is on the right, +270 if it "
                   "is on the left, +180 if upside down); fit_photos and video_poses need upright frames. Frames with notes (sky / close-up / dark) are weak views of the room; if they are not the room, "
                   "narrow start_s/end_s and extract again. The first frame "
                   "sets the room's orientation; pick landmarks per frame and run fit_photos for the room and every "
                   "frame's camera, put each object seen in several frames into the room once, then set_input "
                   "(camera) per frame and compare_photo")
    return out


def _frames_rotate(inputs: Path, video: str | Path) -> int:
    """The turn (degrees clockwise) the frames of `video` in `inputs` were extracted with (0 if none)."""
    want = str(Path(video).expanduser().resolve())
    for side in sorted(inputs.glob("*.source.json")) if inputs.is_dir() else []:
        try:
            meta = json.loads(side.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if meta.get("video") == want and meta.get("time_s") is not None:
            return int(meta.get("rotate") or 0)
    return 0


def video_poses(scene_dir: str | Path, hfov_deg: float = 65.0, video: str | None = None,
                rate: float | None = None) -> dict[str, Any]:
    """How the camera turned (and which way it moved) between the frames extract_frames wrote into
    <scene_dir>/inputs, measured from the video between them (agentic_sim_world_gen.video_pose: ORB features; needs OpenCV).
    Returns `relative` for fit_photos' request (frames of one video only; with several videos, each on its own) and
    writes video_poses.json. hfov_deg: the video camera's field of view (fit_photos estimates it: run again
    with that value if it differs by more than 5 degrees)."""
    from agentic_sim_world_gen import video_pose as vp
    from agentic_sim_world_gen.inputs import sidecar_path

    d = Path(scene_dir).expanduser()
    if not vp.available():
        return {"ok": False, "error": "OpenCV is not installed: uv sync again with --extra video added "
                "(or pip install opencv-python-headless). Without it fit the frames from landmarks only"}
    groups: dict[str, list[dict[str, Any]]] = {}
    # a scene's recorded frames only: frames extracted again at other times leave the old files in inputs/
    recorded = {i.id for i in load(d).spec.inputs} if _resolved_path(d).exists() else None
    for jpg in sorted((d / "inputs").glob("*.jpg")):
        side = sidecar_path(jpg)
        if not side.exists() or recorded is not None and jpg.stem not in recorded:
            continue
        meta = json.loads(side.read_text(encoding="utf-8"))
        if meta.get("video") and meta.get("time_s") is not None:
            groups.setdefault(meta["video"], []).append({"id": jpg.stem, "time_s": float(meta["time_s"]),
                                                         "path": str(jpg), "rotate": int(meta.get("rotate") or 0)})
    if video is not None:
        want = str(Path(video).expanduser().resolve())
        groups = {k: v for k, v in groups.items() if k == want or Path(k).name == Path(video).name}
    if not groups:
        return {"ok": False, "error": f"no video frames in {d / 'inputs'} (run extract_frames first)"}
    out: dict[str, Any] = {"ok": True, "hfov_deg": hfov_deg, "relative": [], "videos": [], "warnings": [],
                           "matcher": {"name": "orb"}}
    for v, frames in groups.items():
        try:
            r = vp.relative_poses(v, frames, hfov_deg, rate or vp.RATE)
        except (vp.CvMissing, FileNotFoundError, ValueError) as e:
            out["warnings"].append(f"{Path(v).name}: {e}")
            continue
        info = {k: r[k] for k in ("video", "size", "rate", "samples", "breaks", "checks", "dropped")}
        out["relative"] += r["relative"]
        out["warnings"] += r["warnings"]
        out["videos"].append(info)
    if not out["relative"]:
        out["ok"] = False
        out["error"] = "no relative pose could be measured: " + "; ".join(out["warnings"] or ["too few matches"])
        return out
    f = d / "video_poses.json"
    f.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    out["file"] = str(f)
    out["hint"] = ("put `relative` into the fit_photos request as it is (same photo ids as the frames). Check "
                   "fit_photos' `relative` (turn_error_deg) and warnings: a frame whose landmarks disagree with the "
                   "video by more than 5 degrees has a mislabeled landmark (or the field of view is off)")
    return out


CHOICES = "choices.jsonl"  # every asset swap with its query and the candidate order, for the search evaluation
REASONS = ("match", "look", "variety", "fit", "other")


def _anon(uri: str | None) -> str | None:
    """A local path without the user's home ("~/..."): the choice log keeps queries and uris only."""
    home = str(Path.home())
    return "~" + uri[len(home):] if uri and uri.startswith(home + os.sep) else uri


def _record_choice(s: ResolvedScene, p: Placement, object_id: str, scene_dir: Path, before: int | None, index: int,
                   correction: bool, reason: str | None) -> None:
    """Append the swap to choices.jsonl (scripts/search_eval/choices.py collects them). Never breaks the edit."""
    try:
        o = s.obj(object_id)
        rec = {"query": o.query, "alt_queries": list(o.alt_queries), "place": s.spec.room.place,
               "min_size": o.min_size, "max_size": o.max_size,
               "candidates": [_anon(c.get("uri")) for c in p.candidates], "from": before, "to": index,
               "by": ACTOR.get() or "agent", "correction": correction, "reason": reason}
        with open(scene_dir / CHOICES, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        log.debug("could not record the choice", exc_info=True)


def select_asset(scene: str | Path, object_id: str, index: int | None = None, correction: bool = False,
                 uri: str | None = None, reason: str | None = None) -> dict[str, Any]:
    """Correction: the user asked for this asset instead of yours (remembered per query). uri instead of
    index: a candidate by uri, or an asset the taste check suggested from its wider pool (it is added to
    the candidates). reason (REASONS): why it beats the one before — "match" = it is the thing the words name
    (the search ranked it too low), "look" = style or color, "variety" = differ from a twin, "fit" = size or
    space. Every swap goes to the scene's choices.jsonl for the search evaluation."""
    if reason is not None and reason not in REASONS:
        raise ValueError(f"reason must be one of {REASONS}, not {reason!r}")
    from agentic_sim_world_gen.preferences import asset_label

    s = load(scene)
    p = _placement(s, object_id)
    if uri is not None:
        index = _add_pool_candidate(p, _taste_pools(s, _resolved_path(scene).parent).get(object_id) or [], uri)
    if index is None:
        raise ValueError("pass index or uri")
    if not 0 <= index < len(p.candidates):
        raise IndexError(f"'{object_id}' has {len(p.candidates)} candidates; index {index} is out of range")
    before = p.candidates[p.selected].get("uri") if p.candidates else None
    _record_choice(s, p, object_id, _resolved_path(scene).parent, p.selected if p.candidates else None, index,
                   correction, reason)
    changed = index != p.selected
    if changed:  # rows about this object (size, position, asset) may no longer hold
        name_new = record_from_dict(p.candidates[index]).name
        for a in s.spec.assumptions:
            if a.object == object_id:
                a.review = f"アセットを {name_new} に差し替えた"
    p.selected = index
    _learn(correction, "asset", (s.obj(object_id).query or "").strip().lower(), _resolved_path(scene).parent,
           before, p.candidates[index].get("uri"), asset_label(p.candidates[index]))
    name = record_from_dict(p.candidates[index]).name
    return _save(s, _resolved_path(scene).parent, f"{_name(s, object_id)}：候補 [{index}] {name} に差し替え", object_id,
                 decor_for={object_id} if changed else None)


def move_object(
    scene: str | Path,
    object_id: str,
    position: tuple[float, float] | None = None,
    yaw_deg: float | None = None,
    facing_deg: float | None = None,
    correction: bool = False,
) -> dict[str, Any]:
    """Manual placement. Clears the object's `layout` / `opening` so the solver stops moving it.
    correction: the user asked for this spot; the automatic fixes leave it there.
    facing_deg: the world direction the asset's front faces (0 = east +X, 90 = north +Y); the solver turns it
    into a yaw for the selected asset's front, and keeps it when the asset is swapped. yaw_deg is the raw
    rotation of the asset (fronts differ between assets) and clears facing_deg."""
    s = load(scene)
    _move(s, object_id, position, yaw_deg, facing_deg)
    _placed_by_user(s.obj(object_id), correction)
    return _save(s, _resolved_path(scene).parent, f"{_name(s, object_id)} を移動", object_id)


def _placed_by_user(o: ObjectSpec, correction: bool = False) -> None:
    if correction or ACTOR.get() == "preview":
        o.placed_by_user = True


def _move(s: ResolvedScene, object_id: str, position: Any, yaw_deg: float | None, facing_deg: float | None) -> None:
    if yaw_deg is not None and facing_deg is not None:
        raise ValueError(f"{object_id}: pass yaw_deg or facing_deg, not both")
    _placement(s, object_id)
    o = s.obj(object_id)
    if position is None and o.position is None:
        raise ValueError(f"'{object_id}' has no position yet; pass position")
    o.layout = o.opening = None
    if position is not None:
        o.position = (float(position[0]), float(position[1]))
    if yaw_deg is not None:
        o.yaw_deg, o.facing_deg = yaw_deg, None
    if facing_deg is not None:
        o.facing_deg = facing_deg


def move_objects(scene: str | Path, moves: list[dict[str, Any]]) -> dict[str, Any]:
    """Several manual placements in one save (a photo trial moved 23 objects one call each).
    moves: [{"id": ..., "position": [x, y], "facing_deg" | "yaw_deg": ...}]; nothing is saved if one is wrong."""
    if not moves:
        raise ValueError("no moves")
    s = load(scene)
    ids = []
    for m in moves:
        m = dict(m)
        i = m.pop("id", None) or m.pop("object_id", None)
        if not i:
            raise ValueError(f"a move needs an id: {m}")
        extra = set(m) - {"position", "yaw_deg", "facing_deg"}
        if extra:
            raise ValueError(f"{i}: unknown keys {sorted(extra)}; a move takes id, position, facing_deg or yaw_deg")
        _move(s, i, m.get("position"), m.get("yaw_deg"), m.get("facing_deg"))
        _placed_by_user(s.obj(i))
        ids.append(i)
    ids = list(dict.fromkeys(ids))
    what = _name(s, ids[0]) if len(ids) == 1 else f"{len(ids)} 点（{'、'.join(ids[:5])}{' ほか' if len(ids) > 5 else ''}）"
    st = _save(s, _resolved_path(scene).parent, f"{what} を移動", ids[0] if len(ids) == 1 else None, touched=set(ids))
    st["moved"] = ids
    return st


def try_move(scene: str | Path, object_id: str, position: tuple[float, float], yaw_deg: float) -> list[dict[str, Any]]:
    """Layout issues if `object_id` stood at position / yaw (nothing is saved): the preview colors a drag with it."""
    s = load(scene)
    _placement(s, object_id)
    o = s.obj(object_id)
    o.layout = o.opening = None
    o.position, o.yaw_deg = (float(position[0]), float(position[1])), float(yaw_deg)
    mine = lambda i: any(x == object_id or x.startswith(object_id + ".") for x in i.objects)  # noqa: E731
    return [{"kind": i.kind, "objects": list(i.objects), "detail": i.detail} for i in check(s) if mine(i)]


def set_layout(
    scene: str | Path,
    object_id: str,
    layout: Layout | dict[str, Any] | None,
    front_offset_deg: float | None = None,
    correction: bool = False,
) -> dict[str, Any]:
    """Set (or clear with None) an object's relational layout; the solver re-places it.
    front_offset_deg corrects the asset front (180 = it was facing backwards, +-90 = sideways).
    correction: the user asked for this change; the in_front_of gap is remembered per kind."""
    from agentic_sim_world_gen.layout.solver import NEAR_GAP
    from agentic_sim_world_gen.preferences import kind

    s = load(scene)
    _placement(s, object_id)
    o = s.obj(object_id)
    old = o.layout
    before = NEAR_GAP if old is None or not old.in_front_of or old.gap is None else old.gap
    o.layout = None if layout is None else (layout if isinstance(layout, Layout) else Layout.model_validate(layout))
    if o.layout is not None:
        o.opening = None  # the two placement rules exclude each other
        o.facing_deg = None  # the layout decides the facing
    if front_offset_deg is not None:
        o.front_offset_deg = front_offset_deg
    _placed_by_user(o, correction)
    if o.layout is not None and o.layout.in_front_of:
        _learn(correction, "gap", kind(o.query), _resolved_path(scene).parent, before,
               NEAR_GAP if o.layout.gap is None else o.layout.gap)
    return _save(s, _resolved_path(scene).parent, f"{_name(s, object_id)} の配置を変更", object_id)


def set_variants(scene: str | Path, object_id: str | list[str], variants: dict[str, str] | None) -> dict[str, Any]:
    """Choose USD variants for this object, or the same ones for several (a list, one save): variant set name ->
    value, or clear with None / {}. The asset's sets are listed in its candidate summary (`variants`); a missing
    one is a bad_variant issue."""
    s = load(scene)
    ids = [object_id] if isinstance(object_id, str) else list(dict.fromkeys(object_id))
    if not ids:
        raise ValueError("no objects")
    for i in ids:
        _placement(s, i)
        s.obj(i).variants = dict(variants or {})
    what = _name(s, ids[0]) if len(ids) == 1 else f"{len(ids)} 点"
    return _save(s, _resolved_path(scene).parent, f"{what} のバリアントを変更", ids[0] if len(ids) == 1 else None)


def set_screen(scene: str | Path, object_id: str, screen: Screen | dict[str, Any] | None) -> dict[str, Any]:
    """Show an image on this object's screens (signage), or go back to the asset's own with None.
    screen: {image: file or {material: file}, fit: contain|cover|stretch, background: "#rrggbb",
    url/title/author/license: the image's source (else read from <stem>.source.json next to it)}.
    The asset's screens are in its candidate summary (`screens`)."""
    s = load(scene)
    p = _placement(s, object_id)
    o = s.obj(object_id)
    if screen is None:
        o.screen = None
        return _save(s, _resolved_path(scene).parent, f"{_name(s, object_id)} の画面を元に戻す", object_id)
    sc = screen if isinstance(screen, Screen) else Screen.model_validate(screen)
    a = p.asset
    names = [x["material"] for x in a.screens] if a is not None else []
    if a is not None and not names:
        raise ValueError(f"'{object_id}' ({a.name}) has no screen or picture whose image can be replaced")
    base = Path.cwd()

    def absolute(f: str) -> str:
        q = Path(f).expanduser()
        q = q if q.is_absolute() else base / q
        if not q.exists():
            raise FileNotFoundError(f"screen image not found: {q}")
        return str(q.resolve())

    sc.image = absolute(sc.image) if isinstance(sc.image, str) else {m: absolute(f) for m, f in sc.image.items()}
    o.screen = sc
    return _save(s, _resolved_path(scene).parent, f"{_name(s, object_id)} の画面に画像を表示", object_id)


def set_tint(scene: str | Path, object_id: str, tint: list[dict[str, Any]] | dict[str, Any] | str | None) -> dict[str, Any]:
    """New colors for this object's materials: "#rrggbb" repaints the asset's main color (the largest painted
    area and every material of that color), {color, materials: [name or pattern]} repaints those materials;
    a list applies in order. None (or []) goes back to the asset's own colors. Material names are in
    describe_asset / the USD; the result (which materials changed) is the object's customData agentic_sim_world_gen:tint."""
    s = load(scene)
    _placement(s, object_id)
    o = s.obj(object_id)
    o.tint = ObjectSpec.model_validate({"id": o.id, "query": o.query, "tint": tint or []}).tint
    what = "、".join(t.color for t in o.tint)
    msg = f"{_name(s, object_id)} の色を {what} に" if o.tint else f"{_name(s, object_id)} の色を元に戻す"
    return _save(s, _resolved_path(scene).parent, msg, object_id)


def set_collider(scene: str | Path, object_id: str, collider: str) -> dict[str, Any]:
    """Static box collider for this object: auto (only for assets known to have no physics), box (always, e.g.
    after check reported no_collider for an Isaac / local asset), none."""
    s = load(scene)
    _placement(s, object_id)
    if collider not in ("auto", "box", "none"):
        raise ValueError(f"collider must be auto, box or none, not {collider!r}")
    s.obj(object_id).collider = collider
    return _save(s, _resolved_path(scene).parent, f"{_name(s, object_id)} の当たり判定を {collider} に", object_id)


def set_array(scene: str | Path, object_id: str, array: Array | dict[str, Any] | None,
              correction: bool = False) -> dict[str, Any]:
    """Repeat an object in rows (racks with aisles, rows of desks), or clear with None.
    The block is placed by the object's layout; units are checked individually.
    correction: the user asked for this change; aisle / spacing are remembered per kind."""
    from agentic_sim_world_gen.preferences import kind

    s = load(scene)
    _placement(s, object_id)
    o = s.obj(object_id)
    old = o.array or Array()
    o.array = None if array is None else (array if isinstance(array, Array) else Array.model_validate(array))
    _placed_by_user(o, correction)
    if o.array is not None:
        for item in ("aisle", "spacing"):
            _learn(correction, item, kind(o.query), _resolved_path(scene).parent, getattr(old, item), getattr(o.array, item))
    return _save(s, _resolved_path(scene).parent, f"{_name(s, object_id)} の繰り返し配置を変更", object_id)


def set_fill(scene: str | Path, object_id: str, fill: Fill | dict[str, Any] | None) -> dict[str, Any]:
    """Stock a fixture's shelves with this object (product), or clear with None. The shelf boards are
    found from the fixture's geometry; status shows the product count, issues a level that does not fit."""
    s = load(scene)
    _placement(s, object_id)
    o = s.obj(object_id)
    o.fill = None if fill is None else (fill if isinstance(fill, Fill) else Fill.model_validate(fill))
    if o.fill is not None:
        _placement(s, o.fill.on)  # the fixture must exist
        o.layout, o.array = None, None
    return _save(s, _resolved_path(scene).parent, f"{_name(s, object_id)} を陳列", object_id)


# BIM furniture: the footprint is where the piece must fit, so keep it close; the height is often a
# simplified box without the parts on top (a register counter: 1.0 m in BIM, 1.39 m with its partition),
# So allow more, mostly upward.
FURNITURE_TOL = 0.2
FURNITURE_HEIGHT_TOL = (0.3, 0.5)  # -30 % .. +50 %


def furniture_object(f: dict[str, Any], tol: float = FURNITURE_TOL,
                     height_tol: tuple[float, float] = FURNITURE_HEIGHT_TOL) -> dict[str, Any]:
    """Draft ObjectSpec (dict) for one imported furniture entry: fixed spot and facing, footprint
    within ±tol, height within -height_tol[0] .. +height_tol[1]."""
    (a, b), h = sorted(f["size"][:2], reverse=True), f["size"][2]
    o: dict[str, Any] = {
        "id": f["id"], "query": f["query"], "label": f.get("name") or f["id"],
        "position": f["position"], "facing_deg": f["facing_deg"], "ifc_guid": f["ifc_guid"],
        "min_size": [round(a * (1 - tol), 3), round(b * (1 - tol), 3), round(h * (1 - height_tol[0]), 3)],
        "max_size": [round(a * (1 + tol), 3), round(b * (1 + tol), 3), round(h * (1 + height_tol[1]), 3)],
    }
    if f.get("z", 0) > 0.005:
        o["z"] = f["z"]
    if f.get("zone"):
        o["zone"] = f["zone"]
    return o


def import_floor_plan(path: str | Path, out_dir: str | Path | None = None, **options: Any) -> dict[str, Any]:
    """Read a floor plan (.json / .dxf / .ifc) into Room fields. With out_dir, also writes plan.json
    (the fields) and plan.svg (preview, with the traced image underneath if any).
    Pass {"plan_file": path} (or the returned room) as SceneSpec.room."""
    from agentic_sim_world_gen.layout.room import RoomGeometry
    from agentic_sim_world_gen.plan import load_any
    from agentic_sim_world_gen.preview.layout2d import write_svg
    from agentic_sim_world_gen.scene.spec import Room

    fields = load_any(path, **options)
    warnings = fields.pop("warnings", [])
    furniture = fields.pop("furniture", [])
    room = Room.model_validate(fields)
    out: dict[str, Any] = {"room": fields, **RoomGeometry(room).summary(), "warnings": warnings}
    if furniture:
        out["furniture"] = furniture
        out["objects"] = [furniture_object(f) for f in furniture]
    if out_dir is not None:
        d = Path(out_dir).expanduser()
        d.mkdir(parents=True, exist_ok=True)
        # written in the plan JSON format (meters, already shifted) so it loads back as room.plan_file
        flat = {"units": "m", **{k: v for k, v in fields.items() if k != "plan"}, **fields["plan"]}
        if furniture:
            flat["furniture"] = furniture  # ignored when loaded back as a plan; kept for reference
        (d / "plan.json").write_text(json.dumps(flat, ensure_ascii=False, indent=2), encoding="utf-8")
        empty = ResolvedScene(spec=SceneSpec(name=Path(path).stem, room=room), placements=[])
        out["files"] = {"plan": str(d / "plan.json"), "preview": str(write_svg(empty, d / "plan.svg", furniture))}
    return out


def set_room(scene: str | Path, floor_alt_queries: list[str] | None = None, walls_alt_queries: list[str] | None = None,
             ceiling_alt_queries: list[str] | None = None, zone_floor_alt_queries: dict[str, list[str]] | None = None,
             correction: bool = False, **changes: Any) -> dict[str, Any]:
    """Change room fields (size, floor, walls, wainscot, baseboard, lighting, lighting_scale, ceiling, openings, plan_file, ...).
    None values are ignored. plan_file imports a floor plan (replacing plan and openings).
    The layout is re-solved, so a resized room re-places related objects.

    floor / walls / ceiling (ceiling also takes true / false): a preset name, a Surface dict, "mdl:<index id>", or words (an English query such as
    "light oak planks", with synonyms in <target>_alt_queries) searched in the bundled MDL materials
    (agentic_sim_world_gen.materials) and the CC0 PBR textures of ambientCG / Poly Haven (agentic_sim_world_gen.pbr; "pbr:<id>" directly,
    downloaded into AGENTIC_SIM_WORLD_GEN_CACHE once when applied). The best match is applied; all candidates are kept in surfaces.json and returned
    as surface_candidates (view_surface_candidates shows the thumbnails, select_surface picks another).
    When nothing matches the surface is left as it was.

    walls may be a map wall id -> surface ({"*": "paint_white", "N": "red brick"}; S/N/W/E or plan wall ids) for an
    accent wall; words there are searched per wall (target "walls:<wall>" for view_surface_candidates /
    select_surface). wainscot / baseboard: {height, surface, color, thickness, walls}, true for the defaults,
    false to remove.
    density: sparse / normal / lived_in re-makes the small things added for a lived-in look (decor=true
    objects, each recorded as a guessed assumption 「生活感のため追加」; remove_object takes one out).
    zone_floors {zone id: floor} merges into Room.zone_floors (plan zones with their own floor): a value like
    floor above (words search target "floor:<zone id>", synonyms in zone_floor_alt_queries[zone id]),
    "room" = the room floor, None = back to the plan / the guess from the zone name.
    columns / floor_areas replace their lists; a floor area's floor may be words too (target
    "floor:<area id>", synonyms in zone_floor_alt_queries[area id]).

    correction: the user asked for this change (not your own adjustment); lighting / lighting_scale / floor /
    walls are remembered per place and become defaults after 2+ scenes."""
    from agentic_sim_world_gen.scene.spec import Room
    from agentic_sim_world_gen.scene.walls import is_per_wall

    s = load(scene)
    d = _resolved_path(scene).parent
    found: dict[str, Any] = {}
    alts = {"floor": floor_alt_queries, "walls": walls_alt_queries, "ceiling": ceiling_alt_queries}
    for target in ("floor", "walls", "ceiling"):
        v = changes.get(target)
        if isinstance(v, str) and _is_material_query(v):
            changes[target], found[target] = _material_query(d, target, v, alts[target])  # None keeps the surface
    if is_per_wall(walls := changes.get("walls")):  # words for one wall: searched as target "walls:<wall>"
        old = s.spec.room.model_dump()["walls"]
        walls = dict(walls)
        for k, v in list(walls.items()):
            if isinstance(v, str) and _is_material_query(v):
                got, found[f"walls:{k}"] = _material_query(d, f"walls:{k}", v, walls_alt_queries)
                if got is None:  # nothing matches: keep that wall as it was
                    walls.pop(k)
                    if is_per_wall(old) and k in old:
                        walls[k] = old[k]
                else:
                    walls[k] = got
        changes["walls"] = walls
    data = s.spec.room.model_dump()
    if (zf := changes.get("zone_floors")) is not None:
        merged = dict(data.get("zone_floors") or {})
        for zid, v in zf.items():
            target = f"floor:{zid}"
            if isinstance(v, str) and v != "room" and _is_material_query(v):
                v, found[target] = _material_query(d, target, v, (zone_floor_alt_queries or {}).get(zid))
                if v is None:  # nothing matched: keep the zone's floor
                    continue
            if v is None:
                merged.pop(zid, None)
            else:
                merged[zid] = v
        changes["zone_floors"] = merged
    if (fa := changes.get("floor_areas")) is not None:  # floor areas: words search target "floor:<area id>"
        fa = [a.model_dump(exclude_none=True) if hasattr(a, "model_dump") else dict(a) for a in fa]
        old_fa = {a["id"]: a for a in data.get("floor_areas") or []}
        for a in fa:
            v = a.get("floor")
            if isinstance(v, str) and _is_material_query(v):
                a["floor"], found[f"floor:{a['id']}"] = _material_query(
                    d, f"floor:{a['id']}", v, (zone_floor_alt_queries or {}).get(a["id"]))
                if a["floor"] is None:  # nothing matched: keep its floor (or drop the words)
                    a["floor"] = (old_fa.get(a["id"]) or {}).get("floor")
                    if a["floor"] is None and a.get("color") is None:
                        a["floor"] = data.get("floor")
        changes["floor_areas"] = fa
    if changes.get("plan_file"):
        data.pop("plan", None)
        data["openings"] = []
    data.update({k: v for k, v in changes.items() if v is not None})
    if changes.get("plan_file") and data.get("zone_floors"):  # keep the overrides of zones the new plan still has
        new = Room.model_validate({**data, "zone_floors": {}})
        zones = {z.id for z in new.plan.zones} if new.plan else set()
        data["zone_floors"] = {k: v for k, v in data["zone_floors"].items() if k in zones}
    before = s.spec.room.model_dump(mode="json")
    s.spec.room = Room.model_validate(data)
    from agentic_sim_world_gen.preferences import ROOM_ITEMS as PREF_ITEMS, room_target
    from agentic_sim_world_gen.preview.changes import ROOM_FIELDS_JA

    after = s.spec.room.model_dump(mode="json")
    for item in PREF_ITEMS:
        if changes.get(item) is not None:
            _learn(correction, item, room_target(s.spec.room.place), d, before[item], after[item])

    what = "・".join(ROOM_FIELDS_JA.get(k, k) for k, v in changes.items() if v is not None)
    msg = f"部屋を変更（{what}）" if what else "部屋の素材を検索（該当なし）"
    mood_off = s.spec.mood is not None and any(before[k] != after[k] for k in MOOD_FIELDS)
    if mood_off:  # the look is no longer the mood's: the room keeps the new values, the mood is dropped
        s.spec.mood = None
        s.spec.assumptions = [a for a in s.spec.assumptions if not (a.item == "雰囲気" and (a.basis or "").startswith(MOOD_BASIS))]
        msg += "。雰囲気の選択を外した"
    # The decor depends on the place too: a shop's kind says what its shelves hold
    if changes.get("density") is not None or (s.spec.room.density and any(
            before[k] != after[k] for k in ("place", "goods", "goods_words"))):
        msg += "。" + _apply_density(s)
    out = _save(s, d, msg)
    if found:
        out["surface_candidates"] = found
    return out


SURFACES_FILE = "surfaces.json"  # the last floor / walls / ceiling material search per target (candidates, selected)


SURFACE_JA = {"floor": "床", "walls": "壁", "ceiling": "天井"}


def _surface_ja(target: str) -> str:
    """床 / 壁 / 天井; 「北の壁」for one wall "walls:N"; 床（<zone>）for a zone floor "floor:<zone id>"."""
    from agentic_sim_world_gen.scene.walls import RECT_ALIASES, WALL_JA

    base, _, sub = target.partition(":")
    if base == "walls" and sub:
        return f"{WALL_JA.get(RECT_ALIASES.get(sub.lower(), ''), sub)}の壁"
    return SURFACE_JA.get(base, base) + (f"（{sub}）" if sub else "")


def _is_material_query(v: str) -> bool:
    from agentic_sim_world_gen.scene.looks import SURFACES

    return v not in SURFACES


def _surfaces(d: Path) -> dict[str, Any]:
    try:
        return json.loads((d / SURFACES_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_surfaces(d: Path, allc: dict[str, Any]) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / SURFACES_FILE).write_text(json.dumps(allc, ensure_ascii=False, indent=1), encoding="utf-8")


def _material_query(d: Path, target: str, query: str, alt_queries: list[str] | None) -> tuple[dict | None, dict]:
    """Search the bundled MDL materials and the CC0 PBR ones (ambientCG / Poly Haven) for a floor / walls
    look; record the candidates in surfaces.json. The first candidate that can be applied is: a PBR material
    downloads its maps once (offline and not cached yet -> the next candidate, noted in `skipped`)."""
    from agentic_sim_world_gen import materials, pbr

    if query.startswith(("mdl:", "pbr:")):
        r = materials.by_id(query[4:])
        if r is None:
            raise KeyError(f"unknown material id '{query[4:]}' (ids come from surface_candidates)")
        cands = [r]
    else:
        cands = materials.search(query, alt_queries, target.partition(":")[0], recs=materials.all_records())
    info: dict[str, Any] = {"query": query, "alt_queries": alt_queries or [],
                            "candidates": [materials.short(r, i) for i, r in enumerate(cands)], "selected": None}
    surf, skipped = None, []
    for i, r in enumerate(cands):
        try:
            surf = materials.surface(r)
        except pbr.FetchError as e:
            skipped.append({"index": i, "id": r["id"], "error": str(e)})
            continue
        info["selected"] = i
        break
    if skipped:
        info["skipped"] = skipped
    if not cands:
        info["note"] = "no material matches; the surface was left as it was (try other words or a preset)"
    elif surf is None:
        info["note"] = "the PBR textures could not be downloaded (offline?); the surface was left as it was"
    elif all(c.get("match", {}).get("weak") for c in cands):
        info["substitute"] = True
        info["note"] = "every candidate is only a weak match (a substitute): check the thumbnails and tell the user"
    allc = _surfaces(d)
    allc[target] = info
    _write_surfaces(d, allc)
    return surf, info


def surface_thumbnails(scene: str | Path, target: str, timeout_s: float = 20) -> list[tuple[dict[str, Any], bytes | None]]:
    """(short summary, PNG bytes or None) for the MDL candidates of the last floor / walls search."""
    import requests

    from agentic_sim_world_gen import materials

    d = _resolved_path(scene).parent
    info = _surfaces(d).get(target)
    if not info:
        raise KeyError(f"no material search for '{target}' yet: set_room({target}='<words>') first")
    _event(scene, f"{_surface_ja(target)}の素材の候補 {len(info['candidates'])} 件をサムネイルで確認中")
    out = []
    for c in info["candidates"]:
        r = materials.by_id(c["id"])
        img = None
        if r is not None and (u := materials.thumb_url(r)):
            try:
                if u.startswith(("http://", "https://")):
                    resp = requests.get(u, headers=UA, timeout=timeout_s)
                    img = resp.content if resp.ok else None
                else:
                    img = Path(u.removeprefix("file://")).read_bytes()
            except (requests.RequestException, OSError):
                pass
        out.append((c, _shrink(img, THUMB_PX)))
    return out


def select_surface(scene: str | Path, target: str, index: int) -> dict[str, Any]:
    """Apply candidate `index` of the last floor / walls material search."""
    from agentic_sim_world_gen import materials
    from agentic_sim_world_gen.scene.spec import Room

    d = _resolved_path(scene).parent
    allc = _surfaces(d)
    info = allc.get(target)
    if not info:
        raise KeyError(f"no material search for '{target}' yet: set_room({target}='<words>') first")
    if not 0 <= index < len(info["candidates"]):
        raise IndexError(f"'{target}' has {len(info['candidates'])} material candidates; index {index} is out of range")
    c = info["candidates"][index]
    r = materials.by_id(c["id"])
    if r is None:
        raise KeyError(f"material '{c['id']}' is not in the index")
    surf = materials.surface(r)  # a CC0 PBR material downloads its maps here (once)
    s = load(scene)
    data = s.spec.room.model_dump()
    base, _, sub = target.partition(":")
    if base == "walls" and sub:  # one wall of a per-wall map
        from agentic_sim_world_gen.scene.walls import is_per_wall

        walls = data["walls"] if is_per_wall(data["walls"]) else {"*": data["walls"]}
        data["walls"] = {**walls, sub: surf}
    elif sub and any(a["id"] == sub for a in data.get("floor_areas") or []):  # a floor area
        data["floor_areas"] = [{**a, "floor": surf} if a["id"] == sub else a for a in data["floor_areas"]]
    elif sub:  # a zone floor
        data["zone_floors"] = {**(data.get("zone_floors") or {}), sub: surf}
    else:
        data[target] = surf
    s.spec.room = Room.model_validate(data)
    info["selected"] = index
    _write_surfaces(d, allc)
    label = _surface_ja(target)
    return _save(s, d, f"{label}の素材：候補 [{index}] {c.get('title') or c['name']} に変更")


def research_object(
    scene: str | Path,
    object_id: str,
    query: str | None = None,
    min_size: tuple[float, float, float] | None = None,
    max_size: tuple[float, float, float] | None = None,
    candidates: int = DEFAULT_CANDIDATES,
    alt_queries: list[str] | None = None,
    select: bool | None = None,
) -> dict[str, Any]:
    """Search again for one object with a new query, synonyms and/or size bounds.

    select: True picks the new top candidate, False keeps the current asset (it stays in the list),
    None (default) picks the top only when it matches; when every new candidate is a substitute the
    current asset is kept, so the scene does not change before the user chooses.
    """
    from agentic_sim_world_gen.assets.rerank import score

    s = load(scene)
    p = _placement(s, object_id)
    o = s.obj(object_id)
    current = p.candidates[p.selected] if p.candidates else None
    if query:
        o.query = query
    if alt_queries is not None:
        o.alt_queries = list(alt_queries)
    if min_size is not None:
        o.min_size = tuple(min_size)
    if max_size is not None:
        o.max_size = tuple(max_size)
    fresh = _placement_for(o, candidates, s.spec.room.place, s.spec.room.height)
    keep = current is not None and (select is False or (select is None and fresh.substitute))
    p.candidates, p.size_misses, p.selected = fresh.candidates, fresh.size_misses, 0
    if keep:
        uris = [c.get("uri") for c in p.candidates]
        if current.get("uri") in uris:
            p.selected = uris.index(current["uri"])
        else:  # not found by the new search: keep it at the end, scored against the new query
            m = score(record_from_dict(current), o.query, o.alt_queries)
            p.candidates.append({**current, "match": {"score": m.score, "matched": m.matched, "weak": m.weak}})
            p.selected = len(p.candidates) - 1
    event = f"{_name(s, object_id)} を再検索（“{o.query}”）" + ("、今のアセットのまま" if keep else "")
    swapped = (current or {}).get("uri") != (p.candidates[p.selected].get("uri") if p.candidates else None)
    st = _save(s, _resolved_path(scene).parent, event, object_id, decor_for={object_id} if swapped else None)
    st["research"] = {"object_id": object_id, "kept_current": keep,
                      "selected": p.candidates[p.selected].get("name") if p.candidates else None}
    sub = next((x.get("substitute") or {} for x in st["objects"] if x["id"] == object_id), {})
    if sub.get("not_in_catalog"):  # stop re-searching, ask the user
        st["research"]["not_in_catalog"] = sub["hint"]
    return st


def add_object(scene: str | Path, obj: ObjectSpec | dict[str, Any], candidates: int = DEFAULT_CANDIDATES) -> dict[str, Any]:
    return add_objects(scene, [obj], candidates)


def add_objects(scene: str | Path, objs: list[ObjectSpec | dict[str, Any]],
                candidates: int = DEFAULT_CANDIDATES) -> dict[str, Any]:
    """Add several objects in one save (one call instead of one per object). All or none: a bad or
    duplicate id adds nothing."""
    s = load(scene)
    new = [o if isinstance(o, ObjectSpec) else ObjectSpec.model_validate(o) for o in objs]
    if not new:
        raise ValueError("no objects to add")
    have = {p.object_id for p in s.placements}
    for o in new:
        if o.id in have:
            raise ValueError(f"object id '{o.id}' already exists")
        have.add(o.id)
    with _searching(new, candidates):
        for o in new:
            s.spec.objects.append(o)
            s.placements.append(_placement_for(o, candidates, s.spec.room.place, s.spec.room.height))
    event = f"{new[0].label or new[0].id} を追加" if len(new) == 1 else \
        f"{len(new)} 点を追加（{'、'.join(o.label or o.id for o in new[:5])}{' ほか' if len(new) > 5 else ''}）"
    st = _save(s, _resolved_path(scene).parent, event, new[0].id, decor_for={o.id for o in new})
    st["added"] = [o.id for o in new]
    return st


SHOP_WINDOW_W = 1.8  # a shop's window this wide is its storefront: a wide FIX window or a shop-front sash
FIXED_WINDOW_PLACES = {"office", "shop", "cafe", "restaurant", "factory", "plant_factory", "warehouse", "datacenter"}


def window_query(op, wall_h: float, place: str | None) -> tuple[str, list[str]]:
    """Search words for a window asset that fits this opening (its shape, then the kind of place)."""
    sill, top = op.resolved(wall_h)
    h = top - sill
    if (place == "shop" or place in STORE_PLACES) and op.width >= SHOP_WINDOW_W:  # a storefront
        return "shop window", ["storefront window", "fixed window", "picture window", "window"]
    if sill <= 0.05 and h >= 1.8:
        return "floor-length sliding window", ["patio window", "sliding window"]
    if op.width <= 0.8 and h <= 0.8:
        return "small frosted window", ["bathroom window", "small window"]
    if op.width <= 0.5:
        return "casement window", ["narrow window"]
    if place in FIXED_WINDOW_PLACES or place in STORE_PLACES:
        return "fixed window", ["picture window", "window"]
    return "sliding window", ["waist window", "window"]


def _window_size(op, wall_h: float) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """min_size / max_size of a window asset that stretches to this opening within FIT_RANGE."""
    from agentic_sim_world_gen.layout.solver import FIT_RANGE

    lo, hi = FIT_RANGE
    sill, top = op.resolved(wall_h)
    h = top - sill
    return (round(op.width / hi, 3), 0.0, round(h / hi, 3)), (round(op.width / lo, 3), 0.6, round(h / lo, 3))


def _cover_size(op, wall_h: float) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """min_size / max_size of a curtain / blind over this window: wide enough after stretching, as tall as
    the window at least and no taller than the wall."""
    from agentic_sim_world_gen.layout.solver import COVER_WIDER, FIT_RANGE

    lo, hi = FIT_RANGE
    sill, top = op.resolved(wall_h)
    w = op.width + COVER_WIDER
    return (round(w / hi, 3), 0.0, round((top - sill) * 0.9, 3)), (round(w / lo, 3), 0.6, round(wall_h, 3))


def cover_query(op, wall_h: float, kind: str) -> tuple[str, list[str]] | None:
    """Search words for a curtain / blind over this window (None for small windows: bathroom, toilet)."""
    sill, top = op.resolved(wall_h)
    if op.width <= 0.8 and top - sill <= 0.8:
        return None
    if kind == "blind":
        return "roller blind", ["roll screen", "blind"]
    if sill <= 0.05:
        return "floor-length curtain", ["curtain", "curtains"]
    return "waist window curtain", ["curtain", "curtains"]


def fill_windows(scene: str | Path, covers: str | None = "auto", candidates: int = DEFAULT_CANDIDATES) -> dict[str, Any]:
    """Put a window asset in every window opening that has none (win_<opening>, stretched to fit), and a
    curtain or blind over it (curtain_<opening>). covers: "auto" (curtains at home, none elsewhere),
    "curtain", "blind" or None. Small windows (<= 0.8 x 0.8 m: bathroom, toilet) get no cover."""
    from agentic_sim_world_gen.layout.room import RoomGeometry

    if covers not in ("auto", "curtain", "blind", None):
        raise ValueError(f"covers must be auto, curtain, blind or None, not {covers!r}")
    s = load(scene)
    place = s.spec.room.place
    kind = ("curtain" if place in (None, "home") else None) if covers == "auto" else covers
    have_win = {o.opening for o in s.spec.objects if o.opening}
    have_cover = {o.covers for o in s.spec.objects if o.covers}
    ids = {o.id for o in s.spec.objects}
    added: list[str] = []
    not_found: list[dict[str, Any]] = []
    for w in RoomGeometry(s.spec.room).walls:
        for op in w.openings:
            if op.kind != "window":
                continue
            new = []
            if op.id not in have_win:
                q, alt = window_query(op, w.height, place)
                lo, hi = _window_size(op, w.height)
                new.append(ObjectSpec(id=f"win_{op.id}", query=q, alt_queries=alt, opening=op.id, label=f"窓 {op.id}",
                                      min_size=lo, max_size=hi))
            cq = cover_query(op, w.height, kind) if kind else None
            if cq and op.id not in have_cover:
                lo, hi = _cover_size(op, w.height)
                new.append(ObjectSpec(id=f"{kind}_{op.id}", query=cq[0], alt_queries=cq[1], covers=op.id,
                                      min_size=lo, max_size=hi,
                                      label=f"{'カーテン' if kind == 'curtain' else 'ブラインド'} {op.id}"))
            for o in new:
                if o.id in ids:
                    continue
                pl = _placement_for(o, candidates, place, s.spec.room.height)
                if why := _unfit_window_asset(pl):  # a car door for a window, a fence for a curtain
                    nearest = pl.candidates[0]["name"] if pl.candidates else None
                    not_found.append({"id": o.id, "query": o.query, "nearest": nearest, "why": why, "opening": op.id})
                    continue
                s.spec.objects.append(o)
                s.placements.append(pl)
                added.append(o.id)
    event = f"窓 {len(added)} 点を追加" if added else "窓はすべて埋まっている"
    if not_found:
        event = (f"窓 {len(added)} 点を追加、" if added else "") + f"{len(not_found)} 点は合うアセットが無く空けたまま"
    st = _save(s, _resolved_path(scene).parent, event, added[0] if added else None, windows_missing=not_found)
    st["added"] = added
    if not_found:
        st["not_found"] = [{k: v for k, v in w.items() if k != "opening"} for w in not_found]
        st["not_found_hint"] = ("no window / curtain asset fits these openings in the asset sources (the nearest is "
                                "another kind of thing), so they stay empty: tell the user. A local:<dir> source "
                                "with window assets can fill them (fill_windows again)")
    return st


def _unfit_window_asset(p: Placement) -> str | None:
    """Why the first candidate of a window / curtain is no window / curtain at all (None if it is one): no
    candidate, only weak matches (a fence for a curtain) or an asset made for another line of work (a car door
    tagged 'window')."""
    if not p.candidates:
        return "no candidate"
    m = p.candidates[0].get("match") or {}
    if m.get("weak"):
        return "weak match: " + (p.candidates[0].get("caption") or p.candidates[0]["name"])
    if m.get("off_place"):
        return f"made for {m['off_place']}: " + (p.candidates[0].get("caption") or p.candidates[0]["name"])
    return None


def remove_object(scene: str | Path, object_id: str | list[str]) -> dict[str, Any]:
    """Remove one object or several (a list, one save). Decor resting on / beside a removed object goes too."""
    s = load(scene)
    ids = [object_id] if isinstance(object_id, str) else list(dict.fromkeys(object_id))
    if not ids:
        raise ValueError("no objects to remove")
    for i in ids:
        _placement(s, i)
    gone = set(ids)
    while True:  # decor resting on / beside a removed object goes with it
        more = {o.id for o in s.spec.objects if o.decor and o.id not in gone and gone & _refs(o)}
        if not more:
            break
        gone |= more
    _drop(s, gone)
    extra = f"（小物 {len(gone) - len(ids)} 点も）" if len(gone) > len(ids) else ""
    what = ids[0] if len(ids) == 1 else f"{len(ids)} 点（{'、'.join(ids[:5])}{' ほか' if len(ids) > 5 else ''}）"
    st = _save(s, _resolved_path(scene).parent, f"{what} を削除{extra}")
    st["removed"] = sorted(gone)
    return st


def duplicate_object(scene: str | Path, object_id: str) -> dict[str, Any]:
    """One more of an object, with the same asset, beside it (the preview's 「複製」). The copy stands free
    (no layout), a step of its own size away where it adds no layout issue when there is such a spot; move it
    from there. Not for arrays, shelf stock, door / window assets and curtains."""
    import copy

    s = load(scene)
    p = _placement(s, object_id)
    o = s.obj(object_id)
    if o.array is not None or o.fill is not None or o.opening is not None or o.covers is not None:
        raise ValueError(f"'{object_id}' is an array, shelf stock or a door / window asset: add_object instead")
    if o.position is None:
        raise ValueError(f"'{object_id}' has no position yet")
    have = {x.id for x in s.spec.objects}
    base = re.sub(r"_\d+$", "", o.id)
    n = 2
    while f"{base}_{n}" in have:
        n += 1
    new = o.model_copy(deep=True, update={"id": f"{base}_{n}", "layout": None, "placed_by_user": True})
    s.spec.objects.append(new)
    s.placements.append(Placement(object_id=new.id, selected=p.selected, candidates=copy.deepcopy(p.candidates),
                                  size_misses=copy.deepcopy(p.size_misses)))
    w, dd, _ = p.asset.size_m if p.asset else (0.5, 0.5, 0.0)
    step = max(w, dd) + 0.1
    x, y = o.position
    room = s.spec.room
    spots = [(x + step, y), (x - step, y), (x, y + step), (x, y - step)]
    inside = [q for q in spots if 0 <= q[0] <= room.width and 0 <= q[1] <= room.depth] or [(x + 0.3, y + 0.3)]
    mine = lambda i: new.id in i.objects  # noqa: E731
    new.position = inside[0]
    for q in inside:
        new.position = q
        solve(s)
        if not any(mine(i) for i in check(s)):
            break
    else:
        new.position = inside[0]
    st = _save(s, _resolved_path(scene).parent, f"{_name(s, object_id)} を複製", new.id, touched={new.id})
    st["added"] = [new.id]
    return st


def _refs(o: ObjectSpec) -> set[str]:
    lay = o.layout
    out = {o.fill.on} if o.fill is not None else set()
    if lay is not None:
        out |= {r for r in (lay.on, lay.next_to, lay.in_front_of) if r}
    return out


def compose_scene(scene: str | Path, out: str | Path | None = None, localize: bool = False,
                  log: bool = True, not_in_photos: dict[str, str] | None = None) -> dict[str, Any]:
    """log=False: no history entry of its own (the preview page rewrites the USD after each edit) and no photo
    gate. not_in_photos: {object id: why} for furniture the photos cannot locate (kept; "" removes one)."""
    from agentic_sim_world_gen.usd.compose import compose

    path = _resolved_path(scene)
    s = ResolvedScene.load(path)
    if s.spec.layout_options and log:
        raise ValueError("layout options are still open: choose_layout(name) with the user's pick, or 'best', first")
    if not_in_photos:
        _set_photo_exempt(path.parent, not_in_photos)
    if log and (why := _photo_gate(s, path.parent)):
        raise ValueError(why)
    from agentic_sim_world_gen.assets.providers.isaac_index import rebase_uri

    # Isaac bundled assets follow ISAAC_ASSETS_ROOT at compose time too (local/Nucleus copy)
    uri_map = {a.uri: u for p in s.placements if (a := p.asset) and (u := rebase_uri(a))}
    if localize:
        from agentic_sim_world_gen.assets.localize import localize as _localize

        uri_map.update(_localize(s))
    out_path = Path(out) if out else path.parent / "scene.usda"
    compose(s, out_path, uri_map=uri_map)
    if out_path == path.parent / "scene.usda":
        from agentic_sim_world_gen.preview.page import mark

        mark(path.parent, "usd")
    if log:
        _event(scene, f"USD を書き出し：{out_path.name}")
    else:
        refresh(scene)  # the page shows the USD as up to date, the last step stays the edit
    st = status(path)
    res = {"usd": str(out_path), "localized_assets": len(uri_map), "issues": st["issues"]}
    if log and (h := st.get("photo_compare_hint")):
        res["next"] = h
    return res


def license_report(scene: str | Path) -> dict[str, Any]:
    """Licenses of the selected assets, attribution lines and warnings. Also writes LICENSES.md."""
    from agentic_sim_world_gen.license import report, to_markdown

    s = load(scene)
    r = report(s)
    md = _resolved_path(scene).parent / "LICENSES.md"
    md.write_text(to_markdown(s, r), encoding="utf-8")
    _event(scene, "ライセンスを確認して LICENSES.md に記録" + (f"（注意 {len(r['warnings'])} 件）" if r["warnings"] else ""))
    return {**r, "file": str(md)}


def license_brief(r: dict[str, Any]) -> dict[str, Any]:
    """license_report for the agent: per license the objects using it, the attribution lines, the
    warnings, the sources without per-object versions and the credits without their users. The full report
    (19k characters in a shop, every asset's origin and sha256) stays in LICENSES.md and full=true."""
    out = {k: v for k, v in r.items() if k not in ("assets", "sources", "credits")}
    out["objects"] = {lic: sorted({a.get("label") or a.get("object_id") or a.get("asset") for a in items})
                      for lic, items in (r.get("assets") or {}).items()}
    out["attribution_lines"] = list(dict.fromkeys(a["attribution"] for items in (r.get("assets") or {}).values()
                                                  for a in items if a.get("attribution")))
    out["sources"] = [{"from": s.get("from"), "versions": list(s.get("versions") or {}), "count": s.get("count")}
                      for s in r.get("sources") or []]
    out["credits"] = [{k: v for k, v in c.items() if k != "used_by"} | {"used_by": len(c.get("used_by") or [])}
                      for c in r.get("credits") or []]
    out["brief"] = "per-asset origins, versions and hashes are in LICENSES.md (file); license_report(full=true) for them here"
    return out


# --- Isaac Sim --------------------------------------------------------------

CHECK_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "isaac" / "check_scene.py"
FRONT_VIEWS_SCRIPT = CHECK_SCRIPT.with_name("front_views.py")
RENDER_VIEW_SCRIPT = CHECK_SCRIPT.with_name("render_view.py")
SDG_SCRIPT = CHECK_SCRIPT.with_name("sdg.py")
NAV_MAP_SCRIPT = CHECK_SCRIPT.with_name("nav_map.py")


def isaac_python() -> Path | None:
    """ISAACSIM_PYTHON (python of a venv with the pip package `isaacsim`) wins over
    ISAACSIM_PATH/python.sh (standalone install, default ~/isaacsim)."""
    if env := os.environ.get("ISAACSIM_PYTHON"):
        py = Path(env).expanduser()
        return py if py.exists() else None
    root = Path(os.environ.get("ISAACSIM_PATH", "~/isaacsim")).expanduser()
    py = root / "python.sh"
    return py if py.exists() else None


def _run_isaac(script: Path, args: list[str], timeout_s: int) -> tuple[subprocess.CompletedProcess | None, dict[str, Any]]:
    """Run a script of scripts/isaac with Isaac Sim's Python. Returns (process, error dict or {})."""
    py = isaac_python()
    if py is None:
        return None, {
            "ok": False,
            "error": "Isaac Sim not found (set ISAACSIM_PATH to the folder with python.sh, "
            "or ISAACSIM_PYTHON to the python of a venv with the pip package isaacsim)",
        }
    if (done := isaac_worker.run(py, script, args, timeout_s)) is not None:  # the Kit that stays up
        return done, {}
    proc = subprocess.run(
        [str(py), str(script), *args],
        capture_output=True,
        text=True,
        timeout=timeout_s,
        stdin=subprocess.DEVNULL,  # never block on the EULA prompt
    )
    return proc, {}


def _isaac_mode() -> dict[str, Any]:
    """For a result: how the Isaac Sim call just made ran, when the worker is on."""
    return {"isaac": dict(isaac_worker.last)} if isaac_worker.last else {}


def _isaac_failure(proc: subprocess.CompletedProcess) -> dict[str, Any]:
    out = proc.stdout + proc.stderr
    err: dict[str, Any] = {"stderr_tail": out[-3000:]}
    if "EULA not accepted" in out:
        err["error"] = "Isaac Sim EULA not accepted: set OMNI_KIT_ACCEPT_EULA=YES (accepts the NVIDIA Omniverse EULA)"
    return err


LIVE_DEFAULT = "127.0.0.1:8226"


def _live_address() -> tuple[str, int]:
    """AGENTIC_SIM_WORLD_GEN_ISAAC_LIVE=host:port of a running Isaac Sim's python_server (default 127.0.0.1:8226)."""
    host, _, port = os.environ.get("AGENTIC_SIM_WORLD_GEN_ISAAC_LIVE", LIVE_DEFAULT).rpartition(":")
    return host or "127.0.0.1", int(port)


def live_available(timeout_s: float = 0.5) -> bool:
    """Is a python_server listening (Isaac Sim started with --enable isaacsim.code_editor.python_server)?"""
    import socket

    try:
        with socket.create_connection(_live_address(), timeout=timeout_s):
            return True
    except OSError:
        return False


def live_exec(code: str, timeout_s: float = 900) -> dict[str, Any]:
    """Run Python in the running Isaac Sim (python_server; top-level await is allowed) and return its
    reply {status, output, ename, evalue, traceback}. The server runs arbitrary code, so it must stay on
    loopback. AGENTIC_SIM_WORLD_GEN_ISAAC_TOKEN is sent when the server was started with require_auth."""
    import socket

    envelope: dict[str, Any] = {"code": code, "timeout": timeout_s}
    if token := os.environ.get("AGENTIC_SIM_WORLD_GEN_ISAAC_TOKEN"):
        envelope["auth_token"] = token
    payload = json.dumps(envelope)
    with socket.create_connection(_live_address(), timeout=10) as sock:
        sock.settimeout(timeout_s + 30)
        sock.sendall(payload.encode())
        sock.shutdown(socket.SHUT_WR)
        chunks = []
        while data := sock.recv(65536):
            chunks.append(data)
    return json.loads(b"".join(chunks).decode())


_LIVE_CHECK = """
import importlib, sys
import omni.kit.app
_dir = {scripts!r}
if _dir not in sys.path:
    sys.path.insert(0, _dir)
import view, check_core
importlib.reload(view)
importlib.reload(check_core)  # pick up edits without restarting Isaac Sim

async def _step():
    await omni.kit.app.get_app().next_update_async()

_r = await check_core.run({usd!r}, _step, steps={steps}, screenshot={shot!r}, gif={gif!r},
                          report_path={report!r}, live=True, orbit_dir={orbit_dir!r}, orbit_frames={orbit})
print("agentic-sim-world-gen-check:", "error" if "error" in _r else "ok")
"""


ORBIT_DIR = "check_orbit"  # the settled room shot from all around (NN.jpg), for the preview's viewer


def check_in_isaac(scene: str | Path, steps: int = 240, timeout_s: int = 900, gif: bool = False,
                   live: bool = False, orbit: int = 24, draft: bool = False, force: bool = False) -> dict[str, Any]:
    """Open scene.usda in Isaac Sim, run physics, take a screenshot (and record check.gif). orbit > 0: also
    shoot the settled room from that many directions around it into check_orbit/ (drag-to-rotate on the
    preview page; about 6 s for 24 on an RTX 2080 Ti).
    draft=True: no physics, only the screenshot and the orbit of the room as laid out, to show the user
    a first draft; the report has physics = False and lists nothing as moved (run the full check later).
    live=True: use the Isaac Sim that is already running (python_server on AGENTIC_SIM_WORLD_GEN_ISAAC_LIVE), so the
    scene shows up on its screen and there is no start-up wait; falls back to headless when none is running.
    A scene drawing more than decor.DRAW_BUDGET faces is not opened (status draw_load) unless force."""
    d = _resolved_path(scene).parent.resolve()
    if not force and (heavy := _draw_view(load(scene))):
        return {"ok": False, "verdict": "failed", "error": "too heavy to open: " + heavy["draw_hint"],
                "draw_load": heavy["draw_load"],
                "next": "lighten the scene as draw_hint says, or pass force=true to try anyway"}
    if draft:
        steps, gif = 0, False
    _running[d] = "Isaac Sim で叩き台の 3D を撮影中（物理なし）…" if draft else "Isaac Sim でチェック中（物理を回してスクショ）…"
    _event(scene, _running[d], busy=True)
    try:
        r = _check_in_isaac(scene, steps, timeout_s, gif, live, max(0, int(orbit)))
    finally:
        _running.pop(d, None)
    if (rep := r.get("report")) is not None:
        from agentic_sim_world_gen.preview.page import mark

        mark(_resolved_path(scene).parent, "check")
    rep = rep or {}
    from agentic_sim_world_gen.preview.page import prim_summary

    try:
        labels = {o.id: o.label or o.id for o in load(scene).spec.objects}
    except Exception:  # noqa: BLE001 - fall back to prim names
        labels = {}
    bad = [(label, rep[k]) for k, label in (("unstable", "物理で動いた"), ("sunk", "床を抜けた"),
                                            ("no_collider", "当たり判定なし")) if rep.get(k)]
    where = "開いている Isaac Sim" if r.get("mode") == "live" else "headless"
    n = sum(len(v) for _, v in bad)
    # Ok only says the run worked; verdict says whether the scene passed (24 bodies moved, result "ok")
    r["verdict"] = "failed" if not r.get("ok") else "draft" if draft else "needs_fix" if bad else "passed"
    if not r.get("ok"):
        text, level = f"チェック失敗（{where}）：{r.get('error')}", "bad"
    elif draft:
        text, level = f"叩き台の 3D を撮影（{where}、物理は未確認）", None
        if rep.get("no_collider"):
            text += "：当たり判定なし " + prim_summary(labels, rep["no_collider"])
        r["next"] = "physics not checked: after the user agrees, run check_in_isaac() without draft"
    elif bad:
        text = f"チェックで問題 {n} 件（{where}）：" + "／".join(
            f"{label} " + prim_summary(labels, v) for label, v in bad)
        level = "bad"
        r["next"] = (f"NOT passed: {n} bodies moved, sank or have no collider (report.problems). Fix them "
                     "(move apart, collider, another candidate, set_fill to fewer items) and check again before "
                     "telling the user the scene is done")
    else:
        text, level = f"チェック完了（{where}）：問題なし", "ok"
    _event(scene, text, level=level)
    if rep:
        r["report"] = summarize_report(rep, _resolved_path(scene).parent.resolve() / "check.json")
    return r


_REPORT_LISTS = ("unstable", "sunk", "no_collider", "static", "selected")
_LIST_MAX = 20  # longer lists are grouped by object id (fill / array members)
_GROUPS_MAX = 12  # and at most this many groups are listed (33 kB of sdf_to_hull paths)
_DETAIL_MAX = 20
_QUIET_M = 0.01  # groups that moved less are only counted
_DROP_MIN = 3  # a group whose moved bodies (at least this many) all went down by the same height
_DROP_SPREAD = 0.01  # within this [m]


def _group_names(names: list[str]) -> list[str]:
    if len(names) <= _LIST_MAX:
        return names
    counts: dict[str, int] = {}
    for n in names:
        counts[n.split("/")[0]] = counts.get(n.split("/")[0], 0) + 1
    out = [f"{k}/* ({c})" if c > 1 or k not in names else k for k, c in counts.items()]
    if len(out) > _GROUPS_MAX:
        out = out[:_GROUPS_MAX] + [f"... {len(out) - _GROUPS_MAX} more objects (full_report)"]
    return out


def summarize_report(rep: dict[str, Any], path: Path) -> dict[str, Any]:
    """Short form of check.json for the tool result: a scene with filled shelves has ~1000 bodies and the
    per-object report is 300k characters. Keeps the problem lists (grouped by object id when long), per-object
    details only for problem bodies, per-group counts / largest move of the groups that moved, the number of SDF
    colliders made hulls, and the orbit's folder and frame count. The full report stays in check.json."""
    objs: dict[str, dict[str, Any]] = rep.get("objects") or {}
    out: dict[str, Any] = {k: v for k, v in rep.items() if k != "objects"}
    for k in _REPORT_LISTS:
        if isinstance(out.get(k), list):
            out[k] = _group_names(out[k])
    if isinstance(h := out.get("sdf_to_hull"), dict) and isinstance(h.get("objects"), list):
        out["sdf_to_hull"] = {**h, "hulled": len(h["objects"]), "objects": _group_names(h["objects"])}
    if isinstance(o := out.get("orbit"), dict):  # the cameras are for preview notes, read from check.json
        out["orbit"] = {k: v for k, v in o.items() if k not in ("frames", "azimuth_deg", "cameras")}
        if isinstance(o.get("frames"), list):
            out["orbit"]["frames"] = len(o["frames"])
    groups: dict[str, dict[str, Any]] = {}
    drops: dict[str, list[float | None]] = {}  # per group: how far each unstable body went down
    for name, o in objs.items():
        g = groups.setdefault(name.split("/")[0], {"count": 0, "max_displacement_m": 0.0})
        g["count"] += 1
        d = o.get("max_displacement_m")
        if d is None and o.get("stable") is False:  # exploded
            g["max_displacement_m"] = None
        elif d is not None and g["max_displacement_m"] is not None:
            g["max_displacement_m"] = max(g["max_displacement_m"], d)
        if o.get("stable") is False:
            g["unstable"] = g.get("unstable", 0) + 1
            mb = o.get("moved_body") or {}
            if len(mb.get("from") or ()) == 3 and len(mb.get("to") or ()) == 3:
                drops.setdefault(name.split("/")[0], []).append(mb["from"][2] - mb["to"][2])
            else:
                drops.setdefault(name.split("/")[0], []).append(None)
    far = max(objs.items(), key=lambda kv: kv[1].get("max_displacement_m") or 0, default=None)
    bad = [n for n in dict.fromkeys(n for k in ("unstable", "sunk", "no_collider") for n in rep.get(k) or [])
           if n in objs]
    moved = {k: g for k, g in groups.items()
             if g.get("unstable") or g["max_displacement_m"] is None or g["max_displacement_m"] >= _QUIET_M}
    out.update(objects_total=len(objs), groups=dict(list(moved.items())[:_GROUPS_MAX]),
               problems={n: objs[n] for n in bad[:_DETAIL_MAX]}, full_report=str(path))
    if len(moved) > _GROUPS_MAX:
        out["groups_omitted"] = len(moved) - _GROUPS_MAX
    if quiet := len(groups) - len(moved):
        out["groups_still"] = quiet  # objects that moved less than _QUIET_M
    if len(bad) > _DETAIL_MAX:
        out["problems_omitted"] = len(bad) - _DETAIL_MAX
    if far and far[1].get("max_displacement_m"):
        out["max_displacement"] = {"object": far[0], "m": far[1]["max_displacement_m"]}
    if dropped := _dropped(drops):
        out["dropped"] = dropped
        out["dropped_hint"] = (
            "each of these groups went straight down by the same height and stopped: what they stand on (a shelf "
            "board) has its collider that much below where the board was found in the mesh. They rest on the "
            "collider now, so this is not a layout problem; nothing to move")
    return out


def _dropped(drops: dict[str, list[float | None]]) -> dict[str, dict[str, Any]]:
    """Groups whose unstable bodies all sank by the same height (100 boxes 4.1-4.3 cm onto a rack's deck)."""
    out = {}
    for g, dz in drops.items():
        if len(dz) < _DROP_MIN or None in dz:
            continue
        lo, hi = min(dz), max(dz)  # type: ignore[type-var]
        if lo >= _QUIET_M and hi - lo <= _DROP_SPREAD:
            out[g] = {"count": len(dz), "m": round(sorted(dz)[len(dz) // 2], 3)}  # type: ignore[type-var]
    return out


def _check_in_isaac(scene: str | Path, steps: int, timeout_s: int, gif: bool, live: bool,
                    orbit: int = 0) -> dict[str, Any]:
    d = _resolved_path(scene).parent.resolve()  # absolute: the live Isaac Sim has its own cwd
    usd = d / "scene.usda"
    if not usd.exists():
        return {"ok": False, "error": f"{usd} not found; run compose_scene first"}
    report, shot, anim = d / "check.json", d / "check.png", d / "check.gif"
    report.unlink(missing_ok=True)  # never return a stale report
    orbit_dir = d / ORBIT_DIR
    shutil.rmtree(orbit_dir, ignore_errors=True)  # nor stale orbit frames
    note: dict[str, Any] = {}
    if live:
        host, port = _live_address()
        if steps > 0 and (risky := _live_risk(scene)):
            note["live_fallback"] = (f"objects overlap ({risky}): they can blow up in PhysX, which breaks the GPU "
                                     "physics of the open Isaac Sim (CUDA error 700); ran headless instead")
        elif live_available():
            return _check_live(usd, report, shot, anim if gif else None, steps, timeout_s,
                               orbit_dir if orbit else None, orbit)
        else:
            note["live_fallback"] = (f"no Isaac Sim python_server on {host}:{port} (start Isaac Sim with "
                                     "--enable isaacsim.code_editor.python_server); ran headless instead")
    args = [str(usd), "--steps", str(steps), "--screenshot", str(shot), "--report", str(report)]
    if gif:
        args += ["--gif", str(anim)]
    if orbit:
        args += ["--orbit-dir", str(orbit_dir), "--orbit-frames", str(orbit)]
    proc, err = _run_isaac(CHECK_SCRIPT, args, timeout_s)
    if proc is None:
        return {**err, **note}
    result: dict[str, Any] = {"ok": proc.returncode == 0 and report.exists(), "returncode": proc.returncode, **note,
                              **_isaac_mode()}
    if report.exists():
        result["report"] = json.loads(report.read_text())
        if "error" in result["report"]:
            result.update(ok=False, error=result["report"]["error"])
        result["screenshot"] = str(shot) if shot.exists() else None
        if gif:
            result["gif"] = str(anim) if anim.exists() else None
    else:
        result.update(_isaac_failure(proc))
    return result


def _live_risk(scene: str | Path) -> str:
    """Overlapping footprints, counting chairs tucked under desks: in a windowed Isaac Sim a blow-up
    (e.g. a chair whose back is inside the desk top) stops the GPU physics for the session and can
    crash the app, so such scenes go to the headless check (a crash there is harmless)."""
    from agentic_sim_world_gen.layout.validate import check

    hits = [i for i in check(load(scene), allow_tuck=False) if i.kind == "overlap"]
    return ", ".join(f"{a} / {b}" for a, b in (i.objects for i in hits))


def _check_live(usd: Path, report: Path, shot: Path, anim: Path | None, steps: int, timeout_s: int,
                orbit_dir: Path | None = None, orbit: int = 0) -> dict[str, Any]:
    code = _LIVE_CHECK.format(scripts=str(CHECK_SCRIPT.parent), usd=str(usd.resolve()), steps=int(steps),
                              shot=str(shot), gif=str(anim) if anim else None, report=str(report),
                              orbit_dir=str(orbit_dir) if orbit_dir else None, orbit=int(orbit))
    try:
        reply = live_exec(code, timeout_s)
    except (OSError, ValueError) as e:  # connection dropped / not JSON
        return {"ok": False, "mode": "live", "error": f"python_server: {e}"}
    result: dict[str, Any] = {"ok": reply.get("status") == "ok" and report.exists(), "mode": "live"}
    if report.exists():
        result["report"] = json.loads(report.read_text())
        if "error" in result["report"]:
            result.update(ok=False, error=result["report"]["error"])
        result["screenshot"] = str(shot) if shot.exists() else None
        if anim:
            result["gif"] = str(anim) if anim.exists() else None
    else:
        result["error"] = f"{reply.get('ename', '')}: {reply.get('evalue', '')}".strip(": ") or "no report"
        result["output_tail"] = (reply.get("output") or "")[-3000:]
        if tb := reply.get("traceback"):
            result["traceback"] = tb[-20:] if isinstance(tb, list) else str(tb)[-3000:]
    return result


def front_targets(usd: str | Path, ids: list[str] | None = None, fill: bool = False) -> dict[str, Any]:
    """Pick the object prims of scene.usda that front_views shoots (those with agentic_sim_world_gen:facing_deg).
    Fill items (products on shelves) are left out unless fill=True or their fill object is named in ids.
    ids: object ids (a whole array / fill group) or "<group>/<item>" for one item.
    Reads scene.usda only (no composition), so it is fast and does not fetch the assets."""
    from pxr import Sdf

    layer = Sdf.Layer.FindOrOpen(str(usd))
    root = layer.GetPrimAtPath("/World/Objects") if layer else None
    if root is None:
        return {"ok": False, "error": f"no /World/Objects in {usd}"}
    found: list[tuple[str, bool]] = []  # (id relative to /World/Objects, is a fill item)

    def walk(spec: Any, rel: str, in_fill: bool) -> None:
        wg = dict(spec.customData).get("agentic_sim_world_gen", {})
        if "facing_deg" in wg:
            found.append((rel, in_fill))
        for c in spec.nameChildren:
            walk(c, f"{rel}/{c.name}", in_fill or "fill" in wg)

    for c in root.nameChildren:
        walk(c, c.name, False)
    if ids:
        top = {r.split("/", 1)[0] for r, _ in found}
        unknown = [i for i in ids if i not in top and i not in {r for r, _ in found}]
        if unknown:
            return {"ok": False, "error": f"unknown object ids {unknown}; objects: {sorted(top)}"}
        chosen = [r for r, _ in found if r in ids or r.split("/", 1)[0] in ids]
    else:
        chosen = [r for r, f in found if fill or not f]
    return {"ok": True, "ids": chosen, "skipped_fill": sum(1 for r, f in found if f and r not in chosen)}


def front_stage(usd: Path, ids: list[str], path: Path) -> int:
    """Write a layer over scene.usda with every object that is not shot (nor holds one) deactivated, so
    Isaac Sim never loads their assets: opening a whole store took 28 s of 44 s for one shelf.
    Returns how many prims were deactivated."""
    from pxr import Sdf

    keep = {"/World/Objects/" + i for i in ids}
    src = Sdf.Layer.FindOrOpen(str(usd))
    layer = Sdf.Layer.CreateAnonymous(".usda")  # CreateNew fails while a layer of that name is still open
    layer.subLayerPaths.append(usd.name if usd.parent.resolve() == path.parent.resolve() else str(usd.resolve()))
    off = 0

    def walk(spec: Any) -> None:
        nonlocal off
        for c in spec.nameChildren:
            p = str(c.path)
            if p in keep:
                continue
            if any(k.startswith(p + "/") for k in keep):
                walk(c)
            else:
                Sdf.CreatePrimInLayer(layer, c.path).active = False
                off += 1

    if root := src.GetPrimAtPath("/World/Objects"):
        walk(root)
    layer.Export(str(path))
    return off


def front_views(scene: str | Path, around: bool = False, ids: list[str] | None = None, fill: bool = False,
                timeout_s: int = 900) -> dict[str, Any]:
    """Render objects from the side their front is assumed to face (around=True: 4 sides) in
    Isaac Sim headless, as one contact sheet front_views.png. Fill items are skipped by default
    (see front_targets). Only the objects shot are loaded: about 11 s to start Isaac Sim, 1-3 s to open
    the scene (6-7 s more when SimReady assets are referenced by URL, not localized) and 0.8 s per tile;
    `seconds` in the result says where the time went."""
    d = _resolved_path(scene).parent
    usd = d / "scene.usda"
    if not usd.exists():
        return {"ok": False, "error": f"{usd} not found; run compose_scene first"}
    if isaac_python() is None:
        return _run_isaac(FRONT_VIEWS_SCRIPT, [], timeout_s)[1]  # the "Isaac Sim not found" error
    sel = front_targets(usd, ids, fill)
    if not sel["ok"]:
        return sel
    if not sel["ids"]:
        return {"ok": False, "error": "no objects to shoot", "skipped_fill": sel["skipped_fill"]}
    targets = d / "front_views_targets.json"
    targets.write_text(json.dumps(sel["ids"]))
    sheet = d / "front_views.png"
    if sheet.exists():
        sheet.unlink()
    stage = d / "front_views_stage.usda"
    front_stage(usd, sel["ids"], stage)
    args = [str(stage), "--out", str(sheet), "--targets", str(targets)] + (["--around"] if around else [])
    proc, err = _run_isaac(FRONT_VIEWS_SCRIPT, args, timeout_s)
    stage.unlink(missing_ok=True)
    if proc is None:
        return err
    result: dict[str, Any] = {"ok": proc.returncode == 0 and sheet.exists(), "returncode": proc.returncode,
                              "objects": len(sel["ids"]), "skipped_fill": sel["skipped_fill"], **_isaac_mode()}
    if sheet.exists():
        result["image"] = str(sheet)
        for line in reversed(proc.stdout.splitlines()):
            if line.startswith("{") and '"seconds"' in line:
                result["seconds"] = json.loads(line)["seconds"]
                break
    else:
        result.update(_isaac_failure(proc))
    return result


def compare_photo(scene: str | Path, input_id: str | None = None, timeout_s: int = 900) -> dict[str, Any]:
    """Render scene.usda from the photos' cameras (/World/Cameras/<id>) in Isaac Sim (one run) and put
    each photo and its render side by side: compare_<id>.png for one input_id, else every photo with a
    camera, one row each, in compare.png."""
    d = _resolved_path(scene).parent
    s = load(scene)
    photos = [i for i in s.spec.inputs if i.camera is not None and (input_id is None or i.id == input_id)]
    if not photos:
        return {"ok": False, "error": f"no input {input_id or ''} with a camera; set one with set_input"}
    missing = [i.id for i in photos if not i.path or not Path(i.path).exists()]
    if missing:
        return {"ok": False, "error": f"photo file of input {missing} not found"}
    usd = d / "scene.usda"
    if not usd.exists():
        return {"ok": False, "error": f"{usd} not found; run compose_scene first"}
    sheet = d / (f"compare_{photos[0].id}.png" if input_id else "compare.png")
    jobs = []
    for i in photos:
        render = d / f"render_{i.id}.png"
        if render.exists():
            render.unlink()
        jobs.append({"camera": f"/World/Cameras/{i.id}", "out": str(render), "photo": i.path,
                     "size": list(i.size_px) if i.size_px else None})
    if sheet.exists():
        sheet.unlink()
    jf = d / "compare_jobs.json"
    jf.write_text(json.dumps(jobs))
    proc, err = _run_isaac(RENDER_VIEW_SCRIPT, [str(usd), "--jobs", str(jf), "--compare", str(sheet)],
                           timeout_s + 60 * (len(jobs) - 1))
    if proc is None:
        return err
    result: dict[str, Any] = {"ok": proc.returncode == 0 and sheet.exists(), "returncode": proc.returncode,
                              **_isaac_mode(), "inputs": [i.id for i in photos],
                              "cameras": {i.id: i.camera.model_dump() for i in photos}}
    if len(photos) == 1:
        result.update(input=photos[0].id, camera=photos[0].camera.model_dump())
    if w := [x for i in photos for x in _camera_warnings(s, i.id)] + _placement_warnings(s, d):
        result["warnings"] = w
    if sheet.exists():
        n = _record_compare(d, photos)
        result.update(round=n, hint=_round_hint(n))
        result.update(renders={i.id: j["out"] for i, j in zip(photos, jobs)}, image=str(sheet))
        if len(photos) == 1:
            result["render"] = jobs[0]["out"]
        result["measure"] = {}
        for i in photos:  # sizes to compare, not only colors
            m = measure_photo(d, i.id)
            if m.get("ok"):
                result["measure"][i.id] = {k: m[k] for k in ("camera", "across", "arrays_in_view", "notes")}
    else:
        result.update(_isaac_failure(proc))
    return result


COMPARE_JSON = "compare.json"
COMPARE_ROUNDS = 3  # compare_photo rounds to aim for (a photo trial took 80 turns of small fixes)


def _round_hint(n: int) -> str:
    """What to do after compare round n."""
    if n < COMPARE_ROUNDS:
        return (f"compare round {n} of about {COMPARE_ROUNDS}: list every difference you see in this comparison (framing, "
                "widths - use measure / floor_pairs -, rows and their unit counts, big objects, wall / floor colors, light) "
                "and fix them all before the next compare_photo, in as few calls as you can (add_objects, one set_room); "
                "do not compare after each small fix")
    return (f"compare round {n}: the layout and proportions are what count now. Fix only what is clearly wrong (a wrong "
            "wall or floor color, a big object missing or in the wrong place, a width off by 20 %+), compare once more if "
            "you did, then report and list the small differences that remain instead of fixing them one by one")


def _photo_inputs(s: ResolvedScene) -> list[InputRef]:
    return [i for i in s.spec.inputs if i.kind != "reference" and i.camera is not None]


def _record_compare(d: Path, photos: list[InputRef]) -> int:
    """What compare_photo last compared: the scene it rendered and each photo's camera then, and how many
    rounds of comparing there were. Returns that count."""
    from agentic_sim_world_gen.preview.page import scene_hash

    f = d / COMPARE_JSON
    try:
        old = json.loads(f.read_text())
    except (OSError, ValueError):
        old = {}
    cams = {**old.get("cameras", {}), **{i.id: i.camera.model_dump(mode="json") for i in photos}}
    made = scene_hash(d)
    # the same scene and cameras again (a retry) is not a new round
    n = int(old.get("rounds", 0)) + (0 if old.get("made_from") == made and old.get("cameras") == cams else 1)
    f.write_text(json.dumps({"made_from": made, "cameras": cams, "rounds": max(n, 1)}, indent=1))
    return max(n, 1)


def _compare_state(s: ResolvedScene, d: Path) -> dict[str, Any]:
    """Whether the scene was compared with its photos since the last change (a trial never compared, another
    moved the photo's camera instead of the register and did not compare again)."""
    photos = _photo_inputs(s)
    if not photos:
        return {}
    try:
        rec = json.loads((d / COMPARE_JSON).read_text())
    except (OSError, ValueError):
        return {"photo_compare": {"compared": False},
                "photo_compare_hint": "the scene has never been compared with the photo(s): compose_scene, then "
                                      "compare_photo and fix what differs before reporting"}
    from agentic_sim_world_gen.preview.page import scene_hash

    out: dict[str, Any] = {"compared": True, "rounds": int(rec.get("rounds", 1))}
    cams = rec.get("cameras", {})
    moved = [i.id for i in photos if i.id in cams and cams[i.id] != i.camera.model_dump(mode="json")]
    never = [i.id for i in photos if i.id not in cams]
    if moved:
        out["camera_moved"] = moved
    if never:
        out["not_compared"] = never
    if rec.get("made_from") != scene_hash(d):
        out["stale"] = True
    hint = ""
    if moved:
        hint = (f"the camera of {', '.join(moved)} moved after the last comparison. A photo's camera is where the photo "
                "was taken: to change where things are, move the objects, not the camera (set it back unless you were "
                "fixing its pose to the photo). ")
    if moved or never or out.get("stale"):
        hint += "The scene changed since the last compare_photo: compose_scene and compare_photo again before reporting"
        if out["rounds"] >= COMPARE_ROUNDS:
            hint += (f" (round {out['rounds'] + 1}: the last one - then report what still differs instead of fixing "
                     "small offsets)")
    return {"photo_compare": out, **({"photo_compare_hint": hint} if hint else {})}


NAV_DIR = "nav"


def nav_map(scene: str | Path, robot_radius: float = 0.3, robot_height: float = 1.2, min_height: float = 0.05,
            resolution: float = 0.05, start: tuple[float, float] | None = None, isaac: bool = False,
            step: float = 0.02, timeout_s: int = 900) -> dict[str, Any]:
    """A navigation map for a robot: <scene>/nav/map.{pgm,yaml,png} + nav.json from the objects' bounding
    boxes, and with isaac=true also map_isaac.* + nav_isaac.json from Isaac Sim's Occupancy Map (rays against the
    colliders; needs scene.usda). Reports where a robot of that size reaches from the doors (or start)."""
    from agentic_sim_world_gen import nav
    from agentic_sim_world_gen.preview.page import scene_hash

    d = _resolved_path(scene).parent
    s = load(scene)
    robot = nav.Robot(radius=robot_radius, height=robot_height, min_height=min_height, step=step)
    if robot.min_height >= robot.height:
        return {"ok": False, "error": f"min_height {min_height} must be below robot_height {robot_height}"}
    g, warnings = nav.build(s, robot, resolution)
    out = d / NAV_DIR
    made = scene_hash(d)
    rep = nav.reach(g, s, robot, start)
    rep["made_from"] = made
    files = nav.write(g, rep, out)
    result: dict[str, Any] = {"ok": True, "summary": nav.summary(rep), "files": files,
                              **{k: v for k, v in rep.items() if not k.startswith("_") and k != "made_from"}}
    if start is not None and not any(x.get("at") for x in rep["starts"]):
        warnings.append(f"start {list(start)} is not a spot where the robot fits (within 1 m); nothing is reachable")
    if isaac:
        usd = d / "scene.usda"
        if not usd.exists():
            return {**result, "ok": False, "error": f"{usd} not found; run compose_scene first (the bbox map is written)"}
        origin = next((x["at"] for x in rep["starts"] if x.get("at")), None)
        if origin is None:
            return {**result, "ok": False, "error": "no free spot to start the Isaac Sim map from"}
        x0, y0, x1, y1 = g.bounds()
        oz = (robot.min_height + robot.height) / 2
        # the Occupancy Map takes its bounds relative to the origin
        job = {"origin": [origin[0], origin[1], oz], "res": g.res,
               "min": [x0 - origin[0], y0 - origin[1], robot.min_height - oz],
               "max": [x1 - origin[0], y1 - origin[1], robot.height - oz]}
        jf, raw = out / "isaac_job.json", out / "isaac_raw.json"
        jf.write_text(json.dumps(job))
        if raw.exists():
            raw.unlink()
        proc, err = _run_isaac(NAV_MAP_SCRIPT, [str(usd), "--job", str(jf), "--out", str(raw)], timeout_s)
        if proc is None:
            return {**result, **err, "ok": False}
        if proc.returncode != 0 or not raw.exists():
            return {**result, "ok": False, "error": "Isaac Sim occupancy map failed", **_isaac_failure(proc)}
        gi = nav.with_occupancy(g, json.loads(raw.read_text()))
        ri = nav.reach(gi, s, robot, start)
        ri["made_from"] = made
        result["isaac"] = {"summary": nav.summary(ri), "files": nav.write(gi, ri, out, "map_isaac"),
                           **{k: v for k, v in ri.items() if k in ("free_m2", "reachable_m2", "reachable",
                                                                     "unreachable_zones", "unreachable_objects", "pockets",
                                                                     "need_width", "routes")}}
        result["compare"] = str(_nav_compare(out))
    if warnings:
        result["warnings"] = warnings
    _event(d, "ナビ用の地図：" + nav_ja(rep) + ("（Isaac Sim でも作成）" if isaac else ""))
    return result


def nav_ja(rep: dict[str, Any]) -> str:
    s = f"半径 {rep['robot']['radius']} m のロボットが床の {rep['reachable']:.0%} に届く"
    if z := rep.get("unreachable_zones"):
        s += f"・行けない部屋 {len(z)}"
    if o := rep.get("unreachable_objects"):
        s += f"・近づけない物 {len(o)}"
    if p := rep.get("pockets"):
        s += f"・入れない空き {len(p)}"
    return s


def _nav_compare(out: Path) -> Path:
    """map.png | map_isaac.png side by side with labels."""
    from PIL import Image, ImageDraw

    a, b = Image.open(out / "map.png"), Image.open(out / "map_isaac.png")
    pad = 28
    im = Image.new("RGB", (a.width + b.width + 12, max(a.height, b.height) + pad), "white")
    im.paste(a, (0, pad))
    im.paste(b, (a.width + 12, pad))
    dr = ImageDraw.Draw(im)
    dr.text((6, 8), "bbox (objects' boxes)", fill="black")
    dr.text((a.width + 18, 8), "Isaac Sim (colliders)", fill="black")
    path = out / "compare.png"
    im.save(path)
    return path


def _check_view(d: Path) -> dict[str, Any]:
    """Status: the latest Isaac Sim check, so the agent does not have to open check.json."""
    from agentic_sim_world_gen.preview.page import STATE, scene_hash

    try:
        rep = json.loads((d / "check.json").read_text())
    except (OSError, ValueError):
        return {}
    counts = {k: len(rep.get(k) or []) for k in ("unstable", "sunk", "no_collider")}
    draft = rep.get("physics") is False
    out: dict[str, Any] = {"verdict": "draft" if draft else "needs_fix" if any(counts.values()) else "passed",
                           **{k: v for k, v in counts.items() if v}}
    try:
        made = (json.loads((d / STATE).read_text()).get("made_from") or {}).get("check")
    except (OSError, ValueError):
        made = None
    if made and made != scene_hash(d):
        out["stale"] = "the scene changed since; run check_in_isaac again"
    return {"last_check": out}


def _nav_view(d: Path) -> dict[str, Any]:
    """status: the latest navigation maps and whether the scene changed since."""
    from agentic_sim_world_gen.preview.page import scene_hash

    out = {}
    now = scene_hash(d)
    for key, name in (("bbox", "nav.json"), ("isaac", "nav_isaac.json")):
        f = d / NAV_DIR / name
        try:
            r = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        out[key] = {k: r[k] for k in ("robot", "reachable", "free_m2", "reachable_m2", "unreachable_zones",
                                      "unreachable_objects", "pockets", "need_width", "routes") if k in r}
        out[key]["map"] = str(d / NAV_DIR / r["files"]["yaml"])
        if r.get("made_from") != now:
            out[key]["stale"] = "the scene changed since; run nav_map again"
    return {"nav": out} if out else {}


PHOTO_AISLE_BASIS = "写真の実測"  # basis of the aisle assumption measure_photo records
AISLE_STEP = 0.05  # an array aisle this close to the measured width [m] is left as it is


def measure_photo(scene: str | Path, input_id: str | None = None,
                  floor_pairs: list[list[float]] | None = None, known: list[dict[str, Any]] | None = None,
                  aisle_pair: int | None = None) -> dict[str, Any]:
    """Widths as the photo's camera sees them: free width across the view at the camera and ahead, what the
    camera sees (pixel boxes, array units in view), and for pixel pairs read off the photo the metric floor distance
    next to the scene's free width on the same line. known = [{pair: index, m, from}]: widths counted off the photo,
    set against the other estimates; {pair, kind, count} and {kind, line | box}: things of known size counted
    across a pair / seen in the photo, the width worked out here. Pure Python (no Isaac Sim).
    aisle_pair: the index of the floor pair that spans an aisle. Its width (best_m, with range_m) becomes
    what the layout aims at: SceneSpec.photo_aisle, the assumption 「aisle」 (a guess, basis 「写真の実測」), the
    aisle of the array whose rows stand on both sides of the pair, and the usability widths
    (layout.usability.photo_aisle). The
    result's `aisle` says what changed and the scene's width against the photo's on that line (`ratio`)."""
    from agentic_sim_world_gen.photo_measure import measure

    out = measure(load(scene), input_id, floor_pairs, known, scene_dir=_resolved_path(scene).parent)
    if aisle_pair is None or not out.get("ok"):
        return out
    rows = out.get("floor_pairs") or []
    if not 0 <= aisle_pair < len(rows) or "error" in rows[aisle_pair]:
        out["aisle"] = {"error": f"aisle_pair {aisle_pair}: no measured floor pair with that index"}
        return out
    out["aisle"] = _use_photo_aisle(scene, out["input"], rows[aisle_pair])
    return out


def _use_photo_aisle(scene: str | Path, input_id: str, row: dict[str, Any]) -> dict[str, Any]:
    from agentic_sim_world_gen.layout import usability
    from agentic_sim_world_gen.photo_measure import line_width
    from agentic_sim_world_gen.scene.conflicts import AISLE_ITEMS, _stated
    from agentic_sim_world_gen.scene.spec import Assumption, PhotoAisle

    d = _resolved_path(scene).parent
    s = load(scene)
    m = row.get("best_m") or row["photo_m"]
    lo, hi = row.get("range_m") or (m, m)
    lo, hi = min(lo, m), max(hi, m)  # best_m may be the median of several estimates, outside the geometry's range
    got: dict[str, Any] = {"m": m, "range_m": [lo, hi], "scene_m_was": row["scene_m"],
                           "ratio_was": round(row["scene_m"] / m, 2)}
    said = _stated(s, AISLE_ITEMS)
    if said is not None and said[1].given:
        got["kept"] = (f"the user said the aisle is {said[0]:g} m (assumption {said[1].item!r}): nothing was changed; "
                       f"the photo measures {m:g} m - tell them when the two differ")
        return got
    s.spec.photo_aisle = PhotoAisle(m=m, range_m=(lo, hi), input=input_id, points=[tuple(p[:2]) for p in row["points"]])
    _, t_lo, t_hi = usability.photo_aisle(s)
    # the rows on both sides of the measured line belong to one array: its aisle is the measured one
    sides = [str(n).split(".")[0] for n in row.get("scene_by") or [] if n and "." in str(n)]
    arrays = []
    for o in s.spec.objects:
        if len(sides) == 2 and sides[0] == sides[1] == o.id and o.array is not None and o.array.rows > 1 \
                and abs(o.array.aisle - m) > AISLE_STEP:
            arrays.append({"object": o.id, "aisle_was": o.array.aisle, "aisle": m})
            o.array = o.array.model_copy(update={"aisle": m})
    row_ = Assumption(item="aisle", value=f"{m:g} m（{lo:g}〜{hi:g} m）", basis=f"{PHOTO_AISLE_BASIS}（{input_id}）")
    s.spec.assumptions = [a for a in s.spec.assumptions if a.key() != row_.key()] + [row_]
    _save(s, d, f"通路幅を写真の実測 {m:g} m に")
    w, by = line_width(load(scene), *s.spec.photo_aisle.points)
    got.update(scene_m=w, scene_by=by, ratio=round(w / m, 2), ok=t_lo <= w <= t_hi)
    if arrays:
        got["arrays"] = arrays
    if row.get("single") or row.get("disagree"):
        got["warning"] = ("one estimate only" if row.get("single") else "the estimates disagree") + \
            ": count shelf units / floor tiles across the aisle and pass `known` before trusting this width"
    if not got["ok"]:
        got["next"] = (f"the scene is {w:g} m there against {m:g} m in the photo: move the rows "
                       f"{'closer' if w > m else 'apart'} (set_array aisle / move_object / set_room); issue `photo_aisle` "
                       "stays until the widths agree")
    return got


def _photo_aisle_view(s: ResolvedScene) -> dict[str, Any]:
    """The aisle measured in a photo next to the scene's width on the same line, as it is now."""
    from agentic_sim_world_gen.layout import usability
    from agentic_sim_world_gen.photo_measure import line_width

    t = usability.photo_aisle(s)
    if t is None or s.spec.room.outdoor or len(s.spec.photo_aisle.points) != 2:
        return {}
    w, by = line_width(s, *s.spec.photo_aisle.points)
    return {"photo_aisle": {"photo_m": t[0], "range_m": [t[1], t[2]], "scene_m": w, "ratio": round(w / t[0], 2),
                            "scene_by": by, "ok": t[1] <= w <= t[2]}}


def generate_data(scene: str | Path, sdg: SdgSpec | dict[str, Any] | None = None, dry_run: bool = False,
                  timeout_s: int | None = None) -> dict[str, Any]:
    """Synthetic data with Replicator: recompose scene.usda (semantic labels), plan the frames
    (agentic_sim_world_gen.sdg) into sdg/plan.json and run scripts/isaac/sdg.py. `sdg` replaces the saved settings.
    dry_run only writes and returns the plan (no Isaac Sim needed)."""
    import shutil

    from agentic_sim_world_gen.sdg import plan

    d = _resolved_path(scene).parent.resolve()
    s = load(scene)
    if sdg is not None:
        s.spec.sdg = sdg if isinstance(sdg, SdgSpec) else SdgSpec.model_validate(sdg)
        _save(s, d)
    cfg = s.spec.sdg or SdgSpec()
    pl = plan(s, cfg)
    out = d / "sdg"
    out.mkdir(parents=True, exist_ok=True)
    (out / "plan.json").write_text(json.dumps(pl, indent=1, ensure_ascii=False), encoding="utf-8")
    head = {"plan": str(out / "plan.json"), "frames": len(pl["frames"]), "classes": sorted(set(pl["classes"].values()) - {""}),
            "plan_warnings": pl["warnings"]}
    if pl.get("materials"):
        head["materials"] = {t: [c["name"] for c in cands] for t, cands in pl["materials"].items()}
    if dry_run:
        return {"ok": True, **head, "sample_frames": pl["frames"][:3]}
    compose_scene(d)
    for sub in ("basic", "coco", "kitti"):
        shutil.rmtree(out / sub, ignore_errors=True)
    for f in ("summary.json", "sheet.jpg", "frames.json"):
        (out / f).unlink(missing_ok=True)
    sheet = out / "sheet.jpg"
    proc, err = _run_isaac(SDG_SCRIPT, [str(d / "scene.usda"), "--plan", str(out / "plan.json"), "--out", str(out),
                                        "--sheet", str(sheet)],
                           timeout_s or 600 + 10 * len(pl["frames"]))  # startup + asset loading, then ~1 s/frame
    if proc is None:
        return err
    summ = out / "summary.json"
    if not summ.exists():
        return {"ok": False, "returncode": proc.returncode, **head, **_isaac_failure(proc)}
    r = json.loads(summ.read_text(encoding="utf-8"))
    bad = [f for f in r.pop("per_frame") if not f.get("ok")]
    n_ok = r.pop("ok")
    return {
        **head,
        **r,  # "classes" becomes the per-class box counts
        "ok": proc.returncode == 0 and n_ok == r["frames"],
        "frames_ok": n_ok,
        "bad_frames": bad[:10],
        "output": {w: str(out / w) for w in pl["writers"]},
        "summary": str(summ),
        **({"image": str(sheet)} if sheet.exists() else {}),
    }


def fit_photos(request: dict[str, Any] | Any, out_dir: str | Path | None = None, depth: bool = False,
               apply: bool | None = None) -> dict[str, Any]:
    """Room size, openings and the photo cameras from landmarks picked in several photos of one room
    (agentic_sim_world_gen.photo_fit). With out_dir, the request and the result go to photo_fit.json.
    `room` holds Room fields (width, depth, height, openings) and each camera PhotoCamera fields
    (position, look_at, hfov_deg) for create_scene / set_room / set_input.
    apply (default: when out_dir is a scene folder): write the room (size, the openings fitted, the height when
    fitted) and each photo input's camera into that scene in one save, recorded in the history and as guessed
    assumptions (basis 「写真から当てはめ」); the result's `applied` says what, and warnings name cameras that end
    up inside an object or outside the room.
    depth=true: the photos with `image` get a depth map (agentic_sim_world_gen.depth, MoGe-2 in AGENTIC_SIM_WORLD_GEN_DEPTH_PYTHON; kept as
    <stem>.depth.npz) and their points / lines / surfaces the depth at their pixel; it sets the scale without a
    ceiling landmark."""
    from agentic_sim_world_gen.photo_fit import FitRequest, fit

    info = None
    if depth:
        from agentic_sim_world_gen import depth as dp

        raw = request.model_dump(mode="json") if isinstance(request, FitRequest) else json.loads(json.dumps(request))
        images = [c["image"] for c in (raw.get("photos") or {}).values() if isinstance(c, dict) and c.get("image")]
        if not images:
            return {"ok": False, "error": "depth=true needs `image` (the photo's path) on the photos"}
        made = dp.run(images)
        if not made["ok"]:
            return made
        info = dp.fill(raw)
        request = raw
    try:
        req = request if isinstance(request, FitRequest) else FitRequest.model_validate(request)
    except Exception as e:  # pydantic ValidationError: say what is wrong, do not raise
        return {"ok": False, "error": str(e)}
    r = fit(req)
    if info is not None and r.get("ok"):
        from agentic_sim_world_gen import depth as dp

        r["depth"] = info
        if hint := dp.fov_hint(info, req.hfov_deg if not any(c.hfov_deg for c in req.photos.values()) else
                               next(c.hfov_deg for c in req.photos.values() if c.hfov_deg)):
            r["warnings"].append(hint + f"; the fit used {next(iter(r['cameras'].values()))['hfov_deg']}")
        if not any(o.depth for c in req.photos.values() for o in [*c.points, *c.lines, *c.surfaces]):
            r["warnings"].append("depth=true but no pixel got a depth (no maps / no points)")
    if r.get("ok") and not any(lo.object for lo in req.locate):
        r["warnings"].append("no furniture located: add `locate` with `object` (the scene id) for each piece of "
                             "furniture the photos show - 2-4 pixels where it meets the floor, z=0 (`edge` front / back) - and "
                             "place_from_photos with the scene folder instead of guessing by relations")
    if out_dir is not None and r.get("ok"):
        p = Path(out_dir)
        p.mkdir(parents=True, exist_ok=True)
        f = p / "photo_fit.json"
        f.write_text(json.dumps({"request": req.model_dump(mode="json"), "result": r}, ensure_ascii=False, indent=1))
        r["file"] = str(f)
    if apply is None:
        apply = out_dir is not None and _resolved_path(out_dir).exists()
    if apply and r.get("ok"):
        if out_dir is None or not _resolved_path(out_dir).exists():
            r["warnings"].append("apply: out_dir is not a scene folder (no scene.resolved.json) - nothing applied")
        else:
            r["applied"] = _apply_fit(out_dir, req, r)
            r["warnings"] += r["applied"].pop("warnings")
    return r


FIT_BASIS = "写真から当てはめ"  # the assumptions fit_photos(apply) records


def _apply_fit(scene: str | Path, req: Any, r: dict[str, Any]) -> dict[str, Any]:
    """fit_photos' room and cameras into the scene: Room width / depth (and height when fitted), the fitted
    openings merged by id (style / clearance kept, the scene's other openings left alone), each photo input's
    camera; one save with one history line, and the room size, openings and cameras as guessed assumptions."""
    from agentic_sim_world_gen.scene.plan import Opening
    from agentic_sim_world_gen.scene.spec import PhotoCamera, Room
    from agentic_sim_world_gen.scene.walls import WALL_JA

    s = load(scene)
    d = _resolved_path(scene).parent
    room, warnings, done = r["room"], [], {}
    parts = []
    if s.spec.room.plan is not None or s.spec.room.outdoor:
        warnings.append("apply: the scene's room is a floor plan / a site - fit_photos fits a rectangular room, "
                        "so the room was left as it is (only the cameras were applied)")
    else:
        data = s.spec.room.model_dump()
        data.update(width=room["width"], depth=room["depth"])
        if room.get("height_fitted"):
            data["height"] = room["height"]
        elif abs(room["height"] - s.spec.room.height) > 0.01:
            warnings.append(f"apply: the fit used the height {room['height']} m but the scene's room is "
                            f"{s.spec.room.height} m (kept) - give the scene's height in the request, or fit it")
        ops = {o["id"] or f"{o['kind']}_{i + 1}": o for i, o in enumerate(data.get("openings") or [])}
        for o in room["openings"]:
            old = ops.get(o["id"], {})
            ops[o["id"]] = Opening.model_validate({**old, **{k: o[k] for k in ("id", "kind", "wall", "offset", "width")},
                                                   "height": o["height"], "sill": o.get("sill")}).model_dump()
        data["openings"] = list(ops.values())
        s.spec.room = Room.model_validate(data)
        h = s.spec.room.height
        done["room"] = {"width": room["width"], "depth": room["depth"], "height": h,
                        "openings": [o["id"] for o in room["openings"]]}
        parts.append(f"部屋 {room['width']:.2f} × {room['depth']:.2f} m・天井 {h:.2f} m"
                     + ("（高さも推定）" if room.get("height_fitted") else ""))
        if room["openings"]:
            parts.append(f"開口 {len(room['openings'])}")
    inputs = {i.id: i for i in s.spec.inputs}
    cams, missing = [], []
    for cid, c in r["cameras"].items():
        inp = inputs.get(cid)
        if inp is None or inp.kind == "reference":
            missing.append(cid)
            continue
        inp.camera = PhotoCamera(position=c["position"], look_at=c["look_at"], hfov_deg=c["hfov_deg"])
        cams.append(cid)
    if missing:
        warnings.append(f"apply: no photo input with the id of {', '.join(missing)} - their cameras were not written "
                        "(the photo ids in the request must be the scene's input ids; add them with set_input)")
    done["cameras"] = cams
    if cams:
        parts.append(f"カメラ {len(cams)} 台")
    rows = []
    if "room" in done:
        rows.append(Assumption(item="size", value=parts[0].removeprefix("部屋 ") + f"（rms {r['rms_px']} px）",
                               basis=FIT_BASIS))
        if room["openings"]:
            rows.append(Assumption(item="openings", basis=FIT_BASIS, value="、".join(
                f"{o['id']}：{WALL_JA.get(o['wall'], o['wall'])}の壁 {o['offset']:.2f} m・幅 {o['width']:.2f} m"
                for o in room["openings"])))
    if cams:
        rows.append(Assumption(item="cameras", basis=FIT_BASIS, value=f"{len(cams)} 台（{', '.join(cams)}）"))
    keys = {a.key() for a in rows}
    prev = {a.key(): a for a in s.spec.assumptions}
    for a in rows:
        if a.key() in prev:
            a.options = list(prev[a.key()].options)
    s.spec.assumptions = [a for a in s.spec.assumptions if a.key() not in keys] + rows
    out = _save(s, d, "写真から当てはめ：" + ("・".join(parts) or "変更なし"))
    warnings += _camera_warnings(s)
    done["issues"] = len(out.get("issues", []))
    done["warnings"] = warnings
    return done
