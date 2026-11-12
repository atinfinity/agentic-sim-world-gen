"""The user's corrections remembered as preferences, used as defaults in the next scene.

Only structured corrections are learned (no parsing of the user's words):

- room (per Room.place, target "place:<place>" or "room" without one): lighting, lighting_scale, floor, walls,
  from set_room(..., correction=True);
- objects (per kind = the head noun of the query: "chair", "rack"): gap (layout.gap of an in_front_of relation;
  negative = tucked under the desk) from set_layout(..., correction=True), aisle / spacing (Array) from
  set_array(..., correction=True);
- asset (per query, "office chair"): the candidate the user picked, from select_asset on the preview page
  (by = preview) or select_asset(..., correction=True).

Each scene contributes one observation per item (its latest value and the value it replaced first). An item
becomes a default once 2+ scenes agree: the same value (lighting, floor, walls, asset) or the same direction
(numbers: up or down from what was there). create_scene applies the defaults to fields the spec leaves out
and records each as an assumption with basis 「前回の好み」.

Stored in AGENTIC_SIM_WORLD_GEN_PREFERENCES, else ./.agentic-sim-world-gen/preferences.json when that exists (per project), else
~/.config/agentic-sim-world-gen/preferences.json.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentic_sim_world_gen import atomic

MIN_COUNT = 2  # scenes that agree before an item becomes a default
MAX_OBSERVATIONS = 20  # per item (oldest dropped)
BASIS = "前回の好み"

ROOM_ITEMS = ("lighting", "lighting_scale", "floor", "walls")
OBJECT_ITEMS = ("gap", "aisle", "spacing")
NUMERIC = {"lighting_scale", "gap", "aisle", "spacing"}
ITEMS = ROOM_ITEMS + OBJECT_ITEMS + ("asset",)


def path() -> Path:
    if p := os.environ.get("AGENTIC_SIM_WORLD_GEN_PREFERENCES"):
        return Path(p).expanduser()
    local = Path.cwd() / ".agentic-sim-world-gen" / "preferences.json"
    if local.exists():
        return local
    return Path("~/.config/agentic-sim-world-gen/preferences.json").expanduser()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load() -> dict[str, Any]:
    try:
        data = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": 1, "items": []}
    data.setdefault("items", [])
    return data


def _write(data: dict[str, Any]) -> None:
    atomic.write(path(), json.dumps(data, ensure_ascii=False, indent=2))


def room_target(place: str | None) -> str:
    return f"place:{place}" if place else "room"


def kind(query: str | None) -> str | None:
    """Head noun of a query ("office chairs" -> "chair"), as agentic_sim_world_gen.layout.usability reads kinds."""
    from agentic_sim_world_gen.layout.usability import _words

    w = _words(query)
    return w[-1] if w else None


def _same(a: Any, b: Any) -> bool:
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def _sign(x: float) -> int:
    return (x > 1e-9) - (x < -1e-9)


def _derive(e: dict[str, Any]) -> None:
    """value / count from the observations: the latest one, and how many scenes agree with it."""
    obs = e["observations"]
    if not obs:
        e["value"], e["count"] = None, 0
        return
    last = obs[-1]
    if e["item"] in NUMERIC:
        e["direction"] = last["direction"]
        e["count"] = sum(o["direction"] == last["direction"] for o in obs)
    else:
        e["count"] = sum(_same(o["value"], last["value"]) for o in obs)
    e["value"] = last["value"]
    if last.get("label"):
        e["label"] = last["label"]


def observe(item: str, target: str, scene: str | Path, before: Any, after: Any, label: str | None = None) -> dict[str, Any] | None:
    """One correction in one scene: `before` was there, the user wants `after`. Returns the updated entry."""
    if item not in ITEMS or _same(before, after):
        return None
    if item in NUMERIC and not all(isinstance(v, (int, float)) for v in (before, after)):
        return None
    data = _load()
    sid = str(Path(scene).expanduser().resolve())
    e = next((x for x in data["items"] if x["item"] == item and x["target"] == target), None)
    if e is None:
        e = {"item": item, "target": target, "observations": []}
        data["items"].append(e)
    old = next((o for o in e["observations"] if o["scene"] == sid), None)
    first = old["from"] if old else before  # what the scene had before its first correction
    e["observations"] = [o for o in e["observations"] if o["scene"] != sid]
    if not _same(first, after):  # back to where it started: the scene no longer counts
        o = {"scene": sid, "from": first, "value": after, "at": _now()}
        if item in NUMERIC:
            o["direction"] = "up" if _sign(after - first) > 0 else "down"
        if label:
            o["label"] = label
        e["observations"] = (e["observations"] + [o])[-MAX_OBSERVATIONS:]
    _derive(e)
    e["last_used"] = _now()
    if not e["observations"]:
        data["items"].remove(e)
    _write(data)
    return _view(e)


def _view(e: dict[str, Any]) -> dict[str, Any]:
    out = {k: e.get(k) for k in ("item", "target", "value", "count", "last_used")}
    for k in ("direction", "label"):
        if e.get(k) is not None:
            out[k] = e[k]
    out["default"] = e.get("count", 0) >= MIN_COUNT
    return out


def preferences(place: str | None = None) -> dict[str, Any]:
    """The remembered preferences: `defaults` (2+ scenes agree; create_scene applies them) and `pending`
    (seen once). place: only room items for that place (and object / asset items)."""
    rows = [_view(e) for e in _load()["items"]]
    if place is not None:
        rows = [r for r in rows if not r["target"].startswith(("place:", "room")) or r["target"] == room_target(place)]
    return {"file": str(path()), "defaults": [r for r in rows if r["default"]],
            "pending": [r for r in rows if not r["default"]]}


def forget(item: str | None = None, target: str | None = None) -> dict[str, Any]:
    """Drop remembered preferences: one item (all its targets, or one target); item None / "all" drops everything."""
    data = _load()
    keep, gone = [], []
    for e in data["items"]:
        hit = (item in (None, "all", e["item"])) and (target is None or e["target"] == target)
        (gone if hit else keep).append(e)
    data["items"] = keep
    _write(data)
    return {"forgotten": [{"item": e["item"], "target": e["target"]} for e in gone], **preferences()}


def _defaults() -> dict[tuple[str, str], dict[str, Any]]:
    return {(e["item"], e["target"]): e for e in _load()["items"] if e.get("count", 0) >= MIN_COUNT}


def _text(item: str, value: Any, label: str | None = None) -> str:
    from agentic_sim_world_gen.preview.changes import _surface, ja

    if item == "lighting":
        return ja(value)
    if item in ("floor", "walls"):
        return _surface(value) if not isinstance(value, dict) or "preset" in value or "mdl" in value or "color" in value \
            else "壁ごとに指定"
    if item == "lighting_scale":
        return f"×{value:g}"
    if item == "gap":
        return f"{value:g} m（机の下に入れる）" if value < 0 else f"{value:g} m"
    if item in ("aisle", "spacing"):
        return f"{value:g} m"
    return label or str(value)


def apply(spec, placements) -> list[dict[str, Any]]:
    """Apply the defaults to what the spec leaves out (create_scene). Records each as an assumption
    (basis 「前回の好み」, given=false) and returns what was applied."""
    from agentic_sim_world_gen.scene.spec import Assumption, Room

    prefs = _defaults()
    if not prefs:
        return []
    applied: list[dict[str, Any]] = []
    used: list[tuple[str, str]] = []
    rows: list[Assumption] = []

    def note(item: str, target: str, value: Any, obj: str | None = None, label: str | None = None) -> None:
        used.append((item, target))
        applied.append({"item": item, "target": target, "value": value, **({"object": obj} if obj else {})})
        rows.append(Assumption(item=item, value=_text(item, value, label), given=False, basis=BASIS, object=obj))

    room = spec.room
    t = room_target(room.place)
    changes = {}
    for item in ROOM_ITEMS:
        if item not in room.model_fields_set and (e := prefs.get((item, t))) is not None:
            changes[item] = e["value"]
    if changes:
        try:
            spec.room = Room.model_validate({**room.model_dump(), **changes})
        except ValueError:  # a stored value no longer valid (renamed preset): leave the room as it was
            changes = {}
        for item, v in changes.items():
            note(item, t, v)
    for o in spec.objects:
        k = kind(o.query)
        if k is None:
            continue
        lay = o.layout
        if lay is not None and lay.in_front_of and lay.gap is None and (e := prefs.get(("gap", k))) is not None:
            lay.gap = float(e["value"])
            note("gap", k, lay.gap, o.id)
        if o.array is not None:
            for item in ("aisle", "spacing"):
                if item not in o.array.model_fields_set and (e := prefs.get((item, k))) is not None:
                    setattr(o.array, item, float(e["value"]))
                    note(item, k, float(e["value"]), o.id)
    for p in placements:
        o = next(x for x in spec.objects if x.id == p.object_id)
        e = prefs.get(("asset", (o.query or "").strip().lower()))
        if e is None:
            continue
        i = next((i for i, c in enumerate(p.candidates) if c.get("uri") == e["value"]), None)
        if i is not None:
            p.selected = i
            note("asset", e["target"], e["value"], o.id, e.get("label"))
    keys = {a.key() for a in rows}
    spec.assumptions = [a for a in spec.assumptions if a.key() not in keys] + rows
    if used:  # last_used: when a default was last applied
        data = _load()
        now = _now()
        for e in data["items"]:
            if (e["item"], e["target"]) in used:
                e["last_used"] = now
        _write(data)
    return applied


def asset_label(c: dict[str, Any]) -> str:
    return c.get("name") or re.sub(r".*/", "", c.get("uri") or "")
