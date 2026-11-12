"""Which kinds of object the asset sources have at all.

A search with no strong match (every candidate weak = a substitute) does not say whether the catalog lacks
the thing or the words missed it, so a floor lamp was searched three times before giving up. The kind table
answers that up front: kind (the query's head noun, or a compound such as "floor lamp", normalized like
rerank does) -> number of assets per source that match it strongly (rerank.score not weak).

SimReady Central has no offline listing, so `scripts/build_kind_table.py` fetches its whole catalog once
(search_hybrid with a limit above the corpus size) and the table (`data/kinds.json.gz`, with the build date)
is shipped. Sources that hold their records locally (the Isaac index, `local:` folders) are scored exactly at
run time, so a replaced index or a new folder is always current.
"""

from __future__ import annotations

import gzip
import json
import os
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path
from typing import Any

from agentic_sim_world_gen.assets.record import AssetRecord
from agentic_sim_world_gen.assets.rerank import _AFTER_NOUN, _lead, _measure, noun_last, score, words

VERSION = 1
DATA = Path(__file__).resolve().parents[1] / "data"
UNSEEN = "qqzx"  # a modifier no asset has: "<UNSEEN> lamp" matches what any "<something> lamp" query would
OTHER_KINDS = 5  # compounds of the same head noun listed as substitutes


def table_path() -> Path:
    return Path(os.environ.get("AGENTIC_SIM_WORLD_GEN_KIND_TABLE") or DATA / "kinds.json.gz")


def kind_words(query: str) -> list[str]:
    """The query's words as rerank reads them (singular, trailing quantities moved to the front, then dropped)."""
    tw = noun_last(words(query), join=True)
    while len(tw) > 1 and _measure(tw[0]):  # "6pack water bottle" is a water bottle
        tw = tw[1:]
    return tw


def kind(query: str) -> str:
    return " ".join(kind_words(query))


def strong(rec: AssetRecord, queries: list[str]) -> bool:
    return bool(queries) and not score(rec, queries[0], queries[1:]).weak


# --- building (scripts/build_kind_table.py) ----------------------------------------------------------


def _phrases(rec: AssetRecord) -> list[list[str]]:
    cls = noun_last(words(rec.semantic_class) or words(rec.raw.get("kind")), join=True)
    return [p for p in [cls, *(noun_last(words(t), join=True) for t in rec.tags)] if p]


def candidate_kinds(rec: AssetRecord) -> tuple[set[str], set[str]]:
    """(single nouns, two-word compounds) this asset might be named by: every word of its class / tags / caption
    lead, and "<modifier> <noun>" where the noun ends a phrase of its class or tags. Checked with score() later."""
    heads, compounds = set(), set()
    for seq in [*_phrases(rec), _lead(words(rec.caption))]:
        heads.update(w for w in seq if len(w) > 1 and not _measure(w))
    for seq in _phrases(rec):
        for i, w in enumerate(seq):
            if i > 0 and (i + 1 == len(seq) or seq[i + 1] in _AFTER_NOUN) and seq[i - 1] not in _AFTER_NOUN \
                    and not _measure(seq[i - 1]) and not _measure(w):
                compounds.add(f"{seq[i - 1]} {w}")
    return heads, compounds


def build(sources: dict[str, list[AssetRecord]], kinds: Iterable[str] = (), built: str = "",
          notes: dict[str, str] | None = None) -> dict[str, Any]:
    """The table: {version, built, sources {name: {records, note}}, kinds {kind: {source: n}}, plain {noun: {source: n}}}.
    `kinds` holds every noun and class/tag compound some asset matches strongly, plus the listed kinds (also when
    nothing matches: {} = known to be absent). `plain` = assets that match "<any modifier> <noun>" (a bare
    "lamp" class does, a "desk lamp" does not), used for compounds the table does not list."""
    table: dict[str, dict[str, int]] = {}
    plain: dict[str, dict[str, int]] = {}
    listed = {k for k in (kind(q) for q in kinds) if k}
    for src, recs in sources.items():
        by_head: dict[str, list[AssetRecord]] = {}
        comps: set[str] = set()
        for r in recs:
            heads, cs = candidate_kinds(r)
            comps |= cs
            for h in heads:
                if strong(r, [h]):
                    by_head.setdefault(h, []).append(r)
        for h, rs in by_head.items():
            table.setdefault(h, {})[src] = len(rs)
            if n := sum(strong(r, [f"{UNSEEN} {h}"]) for r in rs):
                plain.setdefault(h, {})[src] = n
        for c in comps:
            if n := sum(strong(r, [c]) for r in by_head.get(c.rsplit(" ", 1)[-1], [])):
                table.setdefault(c, {})[src] = n
        for k in listed:
            if " " in k and (n := sum(strong(r, [k]) for r in recs)):  # longer phrases: every asset (mods add up)
                table.setdefault(k, {})[src] = n
    for k in listed:
        table.setdefault(k, {})
    return {"version": VERSION, "built": built,
            "sources": {s: {"records": len(rs), **({"note": notes[s]} if notes and s in notes else {})}
                        for s, rs in sources.items()},
            "listed": sorted(listed), "kinds": dict(sorted(table.items())), "plain": dict(sorted(plain.items()))}


def write(table: dict[str, Any], path: Path) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(table, f, ensure_ascii=False, separators=(",", ":"))


# --- looking up ---------------------------------------------------------------------------------------


@lru_cache(maxsize=4)
def _load(path: str) -> dict[str, Any] | None:
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            t = json.load(f)
    except (OSError, ValueError):
        return None
    return t if t.get("version") == VERSION else None


def load(path: str | Path | None = None) -> dict[str, Any] | None:
    return _load(str(path or table_path()))


def lookup(query: str, table: dict[str, Any]) -> tuple[str, dict[str, int]] | None:
    """(kind, {source: n}) from the table: the longest listed tail of the query ("black floor lamp" -> "floor lamp"),
    else the head noun (for a compound the table does not list, only assets that would match any modifier)."""
    tw = kind_words(query)
    if not tw:
        return None
    kinds = table["kinds"]
    for k in range(len(tw), 1, -1):
        key = " ".join(tw[-k:])
        if key in kinds:
            return key, kinds[key]
    head = tw[-1]
    if len(tw) == 1:
        return head, kinds.get(head, {})
    return " ".join(tw[-2:]), table["plain"].get(head, {})


def other_kinds(query: str, table: dict[str, Any], n: int = OTHER_KINDS) -> list[dict[str, Any]]:
    """Other kinds with the same head noun that the sources do have ("whiteboard" has none; "dining table" ->
    packing table, bedside table ...), ranked by the assets named that way beyond the plain head noun ones
    (a bare "lamp" class matches every "<x> lamp"). Counts are the largest source's (sources share assets)."""
    tw = kind_words(query)
    if not tw:
        return []
    head, me = tw[-1], " ".join(tw[-2:])
    base = table["plain"].get(head, {})
    rows = []
    for k, v in table["kinds"].items():
        if k != me and k.count(" ") == 1 and k.endswith(" " + head) and v:
            own = max(c - base.get(s, 0) for s, c in v.items())
            if own > 0:
                rows.append((k, own, max(v.values())))
    rows.sort(key=lambda x: (-x[1], x[0]))
    return [{"kind": k, "assets": c} for k, _, c in rows[:n]]


_exact: dict[tuple, tuple[Any, int]] = {}  # (source, id(records), size, queries) -> (records, count); status runs on every save


def _leaves(provider: Any) -> list[Any]:
    subs = getattr(provider, "providers", None)
    return [x for p in subs for x in _leaves(p)] if subs else [provider]


def availability(queries: list[str], provider: Any = None, table: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """How many assets of this kind each source has. Sources with local records (Isaac index, local: folders) are
    scored exactly; SimReady Central comes from the table. absent = every source is known and none has one.
    None when no source can be answered (e.g. a test provider)."""
    queries = [q for q in queries if q and kind_words(q)]
    if not queries:
        return None
    if provider is None:
        from agentic_sim_world_gen.service import get_provider

        provider = get_provider()
    table = table if table is not None else load()
    counts: dict[str, int] = {}
    unknown: list[str] = []
    key = kind(queries[0])
    for p in _leaves(provider):
        name = getattr(p, "name", "?")
        if callable(getattr(p, "records", None)):
            recs = p.records()
            ck = (name, id(recs), len(recs), tuple(queries))
            if ck not in _exact or _exact[ck][0] is not recs:
                if len(_exact) > 4096:
                    _exact.clear()
                _exact[ck] = (recs, sum(strong(r, queries) for r in recs))  # keeps recs alive: the id stays unique
            counts[name] = _exact[ck][1]
        elif table and name in table["sources"]:
            found = [lookup(q, table) for q in queries]
            counts[name] = max((c.get(name, 0) for _, c in filter(None, found)), default=0)
            if found[0]:
                key = found[0][0]
        else:
            unknown.append(name)
    if not counts:
        return None
    out: dict[str, Any] = {"kind": key, "counts": counts,
                           "absent": not unknown and not any(counts.values())}
    if unknown:
        out["unknown_sources"] = unknown
    if table:
        out["table"] = {"built": table.get("built"), "version": table.get("version")}
        if others := other_kinds(queries[0], table):
            out["other_kinds"] = others
    return out
