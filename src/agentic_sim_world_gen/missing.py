"""What the asset sources did not have, over every scene made on this machine.

Each save writes the scene's own list to AGENTIC_SIM_WORLD_GEN_CACHE/missing/<hash of the scene folder>.json: the objects whose
every candidate is a weak match (`substitute`; `not_in_catalog` when the kind table has none of the kind either),
the objects with no candidate at all (`none`), the decor Room.density left out for want of an asset (`decor`), and
the window openings left empty (`window`). One file per scene, replaced whole (atomic): a scene counts once however
often it is saved, an object that later got a real asset drops out, and two servers never write the same file.
Two kinds of row outlive the objects: `removed` (remove_object(no_asset=True): taken out for want of an
asset) and `noted` (note_missing: an asset the agent only reported, e.g. the right chair in the wrong color).
`summary` adds them up by query; `agentic-sim-world-gen missing` prints it - the order to add assets in.
Assets get added to the sources over time: `recheck` searches every query again with the sources of now and keeps
what it found in AGENTIC_SIM_WORLD_GEN_CACHE/missing-found.json; `summary` moves those queries to `found`.
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
WHYS = ("not_in_catalog", "substitute", "none", "decor", "window", "removed", "noted")  # the report's columns
KEPT = ("removed", "noted")  # rows the scene's objects do not give back: carried over every save
FOUND = "missing-found.json"
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
        row = {"why": why, "query": o.query, "id": o.id, **({"alt": list(o.alt_queries)} if o.alt_queries else {})}
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
            # filled since: no longer missing. By role: a window found does not fill the missing curtain
            filled = {("win", o.opening) for o in scene.spec.objects if o.opening} | \
                     {("cover", o.covers) for o in scene.spec.objects if o.covers}
            win = [r for r in win if (_role(r), r.get("opening")) not in filled]
        else:
            win = [{"why": "window", "query": w["query"], "id": w["id"],
                    **({"opening": w["opening"]} if w.get("opening") else {}),
                    **({"used": w["nearest"]} if w.get("nearest") else {})} for w in windows]
        ids = {o.id for o in scene.spec.objects}
        # an object put back under the same id is no longer removed; a note stays until recheck finds the asset
        kept = [r for r in old.get("rows") or [] if r.get("why") in KEPT
                and not (r["why"] == "removed" and r.get("id") in ids)]
        rows = rows_of(scene, status) + win + kept
        if not rows and not path.exists():
            return
        _write(path, d, scene, rows)
    except Exception as e:
        log.warning("missing assets not recorded: %s", e)


def _write(path: Path, d: Path, scene: Any, rows: list[dict[str, Any]]) -> None:
    data = {"scene": str(d.resolve()), "name": scene.spec.name, "place": scene.spec.room.place,
            "updated": time.strftime("%Y-%m-%dT%H:%M:%S"), "rows": rows}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = atomic.temp_for(path)
    tmp.write_text(json.dumps(data, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def add(scene_dir: str | Path, scene: Any, rows: list[dict[str, Any]]) -> bool:
    """Add `removed` / `noted` rows to the scene's list (one per why, query and id: a second note replaces the
    first). False when the recording is off or failed. Never raises."""
    if not enabled():
        return False
    try:
        d = Path(scene_dir)
        path = _file(d)
        new = {(r["why"], _key(r["query"]), r.get("id")) for r in rows}
        old = [r for r in (_read(path) or {}).get("rows") or [] if (r.get("why"), _key(r.get("query", "")), r.get("id")) not in new]
        _write(path, d, scene, old + rows)
        return True
    except Exception as e:
        log.warning("missing assets not recorded: %s", e)
        return False


def _role(row: dict[str, Any]) -> str:
    """win_<opening> is the window, curtain_ / blind_<opening> its cover (fill_windows' ids)."""
    return "win" if str(row.get("id", "")).startswith("win_") else "cover"


def _read(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def windows(scene_dir: str | Path) -> list[dict[str, Any]]:
    """The scene's window / curtain openings fill_windows found no asset for (its recorded `window` rows).
    Empty when nothing is recorded or the recording is off."""
    if not enabled():
        return []
    data = _read(_file(Path(scene_dir))) or {}
    return [r for r in data.get("rows") or [] if r.get("why") == "window"]


def _key(query: str) -> str:
    return " ".join((query or "").lower().split())


def _found() -> dict[str, Any]:
    return _read(folder().parent / FOUND) or {}


def summary(top: int | None = None, why: str | None = None) -> dict[str, Any]:
    """Queries by the number of scenes that missed them (then by the number of objects), most first.
    why: only rows of this kind. Each row: query, scenes, objects, by (count per why), places, labels, used
    (the assets that stood in, most frequent first), notes, last (the newest scene's time), checked (the last
    recheck). A query the last recheck found an asset for goes to `found` (with `found`: asset, source,
    checked), unless a scene missed it again after that recheck."""
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
                                  "used": {}, "notes": {}, "last": ""})
            g["scenes"].add(data.get("scene"))
            g["objects"] += 1
            g["by"][r["why"]] = g["by"].get(r["why"], 0) + 1
            for field, value in (("places", data.get("place")), ("labels", r.get("label")), ("used", r.get("used")),
                                 ("notes", r.get("note"))):
                if value:
                    g[field][value] = g[field].get(value, 0) + 1
            g["last"] = max(g["last"], data.get("updated") or "")
    out, found = [], []
    checks = _found()
    for g in by.values():
        top_of = lambda d, n: [k for k, _ in sorted(d.items(), key=lambda kv: (-kv[1], kv[0]))[:n]]
        row = {"query": g["query"], "scenes": len(g["scenes"]), "objects": g["objects"],
               "by": {w: g["by"][w] for w in WHYS if w in g["by"]}, "places": top_of(g["places"], 3),
               "labels": top_of(g["labels"], 3), "used": top_of(g["used"], 3), "notes": top_of(g["notes"], 3),
               "last": g["last"]}
        c = checks.get(g["query"]) or {}
        if c.get("checked"):
            row["checked"] = c["checked"]
        if c.get("asset"):
            row["found"] = {k: c[k] for k in ("asset", "source", "checked") if c.get(k)}
        (found if row.get("found") and g["last"] <= c["checked"] else out).append(row)
    for rows in (out, found):
        rows.sort(key=lambda r: (-r["scenes"], -r["objects"], r["query"]))
    return {"folder": str(folder()), "scenes": scenes, "queries": len(out), "rows": out[:top] if top else out,
            "found": found}


def recheck(find: Any) -> dict[str, Any]:
    """Search every recorded query again. find(query, alt_queries) -> {name, source, uri} of the first good
    (not weak) match with the sources of now, or None. Writes AGENTIC_SIM_WORLD_GEN_CACHE/missing-found.json (replaced whole)."""
    queries: dict[str, list[str]] = {}
    for path in sorted(folder().glob("*.json")) if folder().is_dir() else []:
        for r in (_read(path) or {}).get("rows") or []:
            if k := _key(r.get("query", "")):
                alts = queries.setdefault(k, [])
                alts += [a for a in r.get("alt") or [] if a not in alts]
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    checks: dict[str, Any] = {}
    for q, alts in queries.items():
        try:
            hit = find(q, alts)
        except Exception as e:  # one failed search must not lose the others
            log.warning("recheck of %r failed: %s", q, e)
            continue
        checks[q] = {"checked": now, "asset": None, **({"asset": hit["name"], "source": hit.get("source"),
                                                         "uri": hit.get("uri")} if hit else {})}
    path = folder().parent / FOUND
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = atomic.temp_for(path)
    tmp.write_text(json.dumps(checks, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    hits = sorted(q for q, c in checks.items() if c["asset"])
    return {"checked": len(checks), "found": [{"query": q, "asset": checks[q]["asset"], "source": checks[q]["source"]}
                                              for q in hits], "still_missing": len(checks) - len(hits)}


JA = {"not_in_catalog": "カタログに無い", "substitute": "代用", "none": "候補なし", "decor": "小物を見送り", "window": "窓が空き",
      "removed": "無いので外した", "noted": "報告"}


def text(s: dict[str, Any]) -> str:
    """The summary as lines for the terminal."""
    if not s["rows"] and not s.get("found"):
        return f"不足の記録はありません（{s['folder']}）"
    lines = [f"不足しているアセット：{s['queries']} 種（{s['scenes']} シーン、{s['folder']}）",
             "シーン数  物の数  検索語  [内訳]  → 代わりに使った物  （最後に足りなかった日）"]
    for r in s["rows"]:
        why = "、".join(f"{JA[w]} {n}" for w, n in r["by"].items())
        more = (f"  （{'・'.join(r['labels'])}）" if r["labels"] else "") + (f"  場所：{'・'.join(r['places'])}" if r["places"] else "")
        more += f"  メモ：{' / '.join(r['notes'])}" if r.get("notes") else ""
        used = f"  → {', '.join(r['used'])}" if r["used"] else ""
        again = f"  ※{r['found']['asset']}があるが、その後も足りなかった" if r.get("found") else ""
        lines.append(f"{r['scenes']:>4}  {r['objects']:>4}  {r['query']}  [{why}]{more}{used}  （{r['last'][:10]}）{again}")
    if s.get("found"):
        lines += ["", f"探し直して見つかった：{len(s['found'])} 種（必要なら作り直したシーンで差し替える）"]
        lines += [f"  {r['query']} → {r['found']['asset']}（{r['found'].get('source') or ''}、{r['found']['checked'][:10]}）"
                  for r in s["found"]]
    checked = [r["checked"] for r in s["rows"] + s.get("found", []) if r.get("checked")]
    if checked:
        lines.append(f"最後に探し直した日：{max(checked)[:10]}（agentic-sim-world-gen missing --recheck）")
    elif s["rows"]:
        lines.append("アセットを足したら agentic-sim-world-gen missing --recheck で探し直す")
    return "\n".join(lines)
