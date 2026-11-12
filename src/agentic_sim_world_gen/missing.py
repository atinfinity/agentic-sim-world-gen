"""What the asset sources did not have, over every scene made on this machine.

Each save writes the scene's own list to AGENTIC_SIM_WORLD_GEN_CACHE/missing/<hash of the scene folder>.json: the objects whose
every candidate is a weak match (`substitute`; `not_in_catalog` when the kind table has none of the kind either),
the objects with no candidate at all (`none`), the decor Room.density left out for want of an asset (`decor`), and
the window openings left empty (`window`). One file per scene, replaced whole (atomic): a scene counts once however
often it is saved, an object that later got a real asset drops out, and two servers never write the same file.
`summary` adds them up by query; `agentic-sim-world-gen missing` prints it - the order to add assets in.
Nothing leaves the machine. AGENTIC_SIM_WORLD_GEN_MISSING=0 turns the recording off.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

from agentic_sim_world_gen import atomic

log = logging.getLogger(__name__)

DIR = "missing"
WHYS = ("not_in_catalog", "substitute", "none", "decor", "window")  # the order of the report's columns
_HOST = re.compile(r"[（(][^（()）]*[)）]\s*$")  # 'パン（shelf_e）' -> 'パン'


def enabled() -> bool:
    return os.environ.get("AGENTIC_SIM_WORLD_GEN_MISSING", "1") != "0"


def folder() -> Path:
    return Path(os.environ.get("AGENTIC_SIM_WORLD_GEN_CACHE", "~/.cache/agentic-sim-world-gen")).expanduser() / DIR


def _file(scene_dir: Path) -> Path:
    return folder() / (hashlib.sha1(str(scene_dir.resolve()).encode()).hexdigest()[:16] + ".json")


def rows_of(scene: Any, status: dict[str, Any]) -> list[dict[str, Any]]:
    """The scene's missing things now, from its status (objects) and its spec (decor left out)."""
    rows: list[dict[str, Any]] = []
    queries = {o.id: o for o in scene.spec.objects}
    for x in status.get("objects") or []:
        o = queries.get(x.get("id"))
        if o is None:
            continue
        sub = x.get("substitute")
        p = next((p for p in scene.placements if p.object_id == o.id), None)
        if sub is not None:
            why = "not_in_catalog" if isinstance(sub, dict) and sub.get("not_in_catalog") else "substitute"
        elif p is not None and not p.candidates:
            why = "none"
        else:
            continue
        row = {"why": why, "query": o.query, "id": o.id}
        if o.label:
            row["label"] = o.label
        if p is not None and p.asset is not None:
            row["used"] = p.asset.name  # what stood in for it
        rows.append(row)
    for k in scene.spec.decor_skipped:
        if k.reason == "no_asset":
            rows.append({"why": "decor", "query": _HOST.sub("", k.label).strip(), **({"id": k.host} if k.host else {})})
    return rows


def record(scene_dir: str | Path, scene: Any, status: dict[str, Any],
           windows: list[dict[str, Any]] | None = None) -> None:
    """Replace the scene's list. windows: fill_windows' not_found of this call (None keeps the ones recorded).
    Never raises: a full disk or a read-only cache must not break a save."""
    if not enabled():
        return
    try:
        d = Path(scene_dir)
        path = _file(d)
        old = _read(path) or {}
        if windows is None:
            win = [r for r in old.get("rows") or [] if r.get("why") == "window"]
            open_ids = {o.opening or o.covers for o in scene.spec.objects}  # filled since: no longer missing
            win = [r for r in win if r.get("opening") not in open_ids]
        else:
            win = [{"why": "window", "query": w["query"], "id": w["id"],
                    **({"opening": w["opening"]} if w.get("opening") else {}),
                    **({"used": w["nearest"]} if w.get("nearest") else {})} for w in windows]
        rows = rows_of(scene, status) + win
        if not rows and not path.exists():
            return
        data = {"scene": str(d.resolve()), "name": scene.spec.name, "place": scene.spec.room.place,
                "updated": time.strftime("%Y-%m-%dT%H:%M:%S"), "rows": rows}
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = atomic.temp_for(path)
        tmp.write_text(json.dumps(data, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except Exception as e:  # noqa: BLE001
        log.warning("missing assets not recorded: %s", e)


def _read(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _key(query: str) -> str:
    return " ".join((query or "").lower().split())


def summary(top: int | None = None, why: str | None = None) -> dict[str, Any]:
    """Queries by the number of scenes that missed them (then by the number of objects), most first.
    why: only rows of this kind. Each row: query, scenes, objects, by (count per why), places, labels, used
    (the assets that stood in, most frequent first), last (the newest scene's time)."""
    by: dict[str, dict[str, Any]] = {}
    scenes = 0
    for path in sorted(folder().glob("*.json")) if folder().is_dir() else []:
        data = _read(path)
        if not data:
            continue
        rows = [r for r in data.get("rows") or [] if why in (None, r.get("why"))]
        scenes += bool(rows)
        for r in rows:
            k = _key(r.get("query", ""))
            if not k:
                continue
            g = by.setdefault(k, {"query": k, "scenes": set(), "objects": 0, "by": {}, "places": {}, "labels": {},
                                  "used": {}, "last": ""})
            g["scenes"].add(data.get("scene"))
            g["objects"] += 1
            g["by"][r["why"]] = g["by"].get(r["why"], 0) + 1
            for field, value in (("places", data.get("place")), ("labels", r.get("label")), ("used", r.get("used"))):
                if value:
                    g[field][value] = g[field].get(value, 0) + 1
            g["last"] = max(g["last"], data.get("updated") or "")
    out = []
    for g in by.values():
        top_of = lambda d, n: [k for k, _ in sorted(d.items(), key=lambda kv: (-kv[1], kv[0]))[:n]]  # noqa: E731
        out.append({"query": g["query"], "scenes": len(g["scenes"]), "objects": g["objects"],
                    "by": {w: g["by"][w] for w in WHYS if w in g["by"]}, "places": top_of(g["places"], 3),
                    "labels": top_of(g["labels"], 3), "used": top_of(g["used"], 3), "last": g["last"]})
    out.sort(key=lambda r: (-r["scenes"], -r["objects"], r["query"]))
    return {"folder": str(folder()), "scenes": scenes, "queries": len(out), "rows": out[:top] if top else out}


JA = {"not_in_catalog": "カタログに無い", "substitute": "代用", "none": "候補なし", "decor": "小物を見送り", "window": "窓が空き"}


def text(s: dict[str, Any]) -> str:
    """The summary as lines for the terminal."""
    if not s["rows"]:
        return f"不足の記録はありません（{s['folder']}）"
    lines = [f"不足しているアセット：{s['queries']} 種（{s['scenes']} シーン、{s['folder']}）",
             "シーン数  物の数  検索語  [内訳]  → 代わりに使った物"]
    for r in s["rows"]:
        why = "、".join(f"{JA[w]} {n}" for w, n in r["by"].items())
        more = (f"  （{'・'.join(r['labels'])}）" if r["labels"] else "") + (f"  場所：{'・'.join(r['places'])}" if r["places"] else "")
        used = f"  → {', '.join(r['used'])}" if r["used"] else ""
        lines.append(f"{r['scenes']:>4}  {r['objects']:>4}  {r['query']}  [{why}]{more}{used}")
    return "\n".join(lines)
