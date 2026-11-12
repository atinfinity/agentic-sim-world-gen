"""An evaluation set grown from real asset choices, and the re-rank weights tried against it.

Every select_asset (MCP, CLI or a click on the preview page) appends one line to the scene's choices.jsonl: the
query, alt_queries, place, size bounds, the candidate uris in their order, the index before and after, who chose
(agent / preview), correction and reason. Only some swaps say the search ranked the wanted asset too low:
picks on the preview page, correction=true, and reason "match". Swaps for looks, variety or space (and the
deliberate swaps of demos) are counted but left out.

    python scripts/search_eval/choices.py collect ~/agentic_sim_world_gen_scenes /tmp [--prefs ~/.config/agentic-sim-world-gen/preferences.json]
    PYTHONPATH=src python scripts/search_eval/choices.py score            # network (searches are cached)
    PYTHONPATH=src python scripts/search_eval/choices.py score --grid     # each weight up / down, with the 35 search-eval queries

`collect` merges into data/choices.json (deduplicated by query, alt_queries, place and asset file name; queries
and uris only, the home folder as "~"). `score` re-runs the current pipeline (service._find_candidates) for each
choice and reports where the chosen asset lands now: top-1, top-5 and MRR. `--grid` scales one rerank.WEIGHTS
entry at a time and also reports P@1 on the graded 35 queries, so a change that helps the choices but hurts
the graded set shows up. Searches are memoized for the run (the API answers differently between calls; one
answer per query keeps the comparison fair). Local assets need the same AGENTIC_SIM_WORLD_GEN_SOURCES as when chosen.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

D = Path(__file__).parent / "data"
OUT = D / "choices.json"
SIGNAL_REASONS = {"match"}


def _name(uri: str | None) -> str:
    return Path(uri or "").name


def _anon(uri: str | None) -> str | None:
    home = str(Path.home())
    return "~" + uri[len(home):] if uri and uri.startswith(home + os.sep) else uri


def is_signal(rec: dict) -> bool:
    """The search ranked the wanted asset too low: the user's own pick, a correction, or reason "match"."""
    return rec.get("by") == "preview" or bool(rec.get("correction")) or rec.get("reason") in SIGNAL_REASONS


def _key(e: dict) -> tuple:
    return ((e["query"] or "").strip().lower(), tuple(e.get("alt_queries") or ()), e.get("place"), e["name"])


def entries_from_log(path: Path) -> tuple[list[dict], int]:
    """(ranking signals, swaps left out) from one choices.jsonl."""
    out, skipped = [], 0
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
            uri = r["candidates"][r["to"]]
        except (ValueError, KeyError, IndexError, TypeError):
            continue
        if not is_signal(r):
            skipped += 1
            continue
        out.append({"query": r["query"], "alt_queries": r.get("alt_queries") or [], "place": r.get("place"),
                    "min_size": r.get("min_size"), "max_size": r.get("max_size"), "uri": _anon(uri), "name": _name(uri),
                    "rank_then": r["to"], "source": r.get("by") if r.get("by") == "preview" else
                    ("correction" if r.get("correction") else f"reason:{r.get('reason')}")})
    return out, skipped


def entries_from_prefs(path: Path) -> list[dict]:
    """Asset preferences: a query and the asset the user wanted for it (no alt_queries, place or order)."""
    d = json.loads(path.read_text(encoding="utf-8"))
    items = d.get("items", []) if isinstance(d, dict) else d
    return [{"query": it["target"], "alt_queries": [], "place": None, "min_size": None, "max_size": None,
             "uri": _anon(it["value"]), "name": _name(it["value"]), "rank_then": None, "source": "preference"}
            for it in items if it.get("item") == "asset" and it.get("target") and it.get("value")]


def merge(old: list[dict], new: list[dict]) -> list[dict]:
    by = {_key(e): e for e in old}
    for e in new:
        k = _key(e)
        if k in by:
            by[k]["count"] = by[k].get("count", 1) + 1
            by[k]["sources"] = sorted(set(by[k].get("sources", [])) | {e["source"]})
        else:
            by[k] = {**{x: v for x, v in e.items() if x != "source"}, "count": 1, "sources": [e["source"]]}
    return sorted(by.values(), key=lambda e: (e["query"].lower(), e["name"]))


def collect(roots: list[str], prefs: list[str]) -> None:
    new, skipped, logs = [], 0, 0
    for root in roots:
        for p in Path(root).expanduser().rglob("choices.jsonl"):
            got, sk = entries_from_log(p)
            new += got
            skipped += sk
            logs += 1
    for p in prefs:
        if Path(p).expanduser().exists():
            new += entries_from_prefs(Path(p).expanduser())
    old = json.loads(OUT.read_text()) if OUT.exists() else []
    merged = merge(old, new)
    OUT.write_text(json.dumps(merged, ensure_ascii=False, indent=1) + "\n")
    print(f"{logs} logs: {len(new)} ranking signals, {skipped} swaps left out (looks / variety / fit / no reason); "
          f"{len(merged)} entries in {OUT}")


class Memo:
    """provider.search memoized for the run, so every weight sees the same hits (the API answers differently)."""

    def __init__(self, p):
        self.p, self.name, self.seen = p, p.name, {}

    def search(self, query, *, limit=10, size=None):
        k = (query, limit, repr(size))
        if k not in self.seen:
            self.seen[k] = self.p.search(query, limit=limit, size=size)
        return self.seen[k]


def _rank(cands: list[dict], name: str) -> int | None:
    return next((i for i, c in enumerate(cands) if c.get("name") == name or _name(c.get("uri")) == name), None)


def run_choices(entries: list[dict]) -> list[int | None]:
    from agentic_sim_world_gen import service
    from agentic_sim_world_gen.scene.spec import ObjectSpec

    ranks = []
    for e in entries:
        o = ObjectSpec(id="x", query=e["query"], alt_queries=e.get("alt_queries") or [],
                       min_size=e.get("min_size"), max_size=e.get("max_size"))
        ranks.append(_rank(service._find_candidates(o, 10, e.get("place")), e["name"]))
    return ranks


def summary(ranks: list[int | None]) -> dict:
    n = max(len(ranks), 1)
    return {"n": len(ranks), "top1": sum(r == 0 for r in ranks) / n, "top5": sum(r is not None and r < 5 for r in ranks) / n,
            "mrr": sum(1 / (r + 1) for r in ranks if r is not None) / n}


def graded_p1() -> float:
    """P@1 (grade 2) of the current pipeline on the 35 search-eval queries (grades.json + the later grade files)."""
    from agentic_sim_world_gen import service
    from agentic_sim_world_gen.scene.spec import ObjectSpec

    grades = json.loads((D / "grades.json").read_text())
    for extra in ("grades_a2.json", "grades_size.json"):
        if (D / extra).exists():
            for q, g in json.loads((D / extra).read_text()).items():
                grades[q].update(g)
    qs = json.loads((D / "queries.json").read_text())
    hits = 0
    for q in qs:
        c = service._find_candidates(ObjectSpec(id="x", query=q["q"], alt_queries=q["alt"]), 10)
        hits += bool(c) and grades[q["id"]].get(c[0]["name"]) == 2
    return hits / len(qs)


def score(grid: bool) -> None:
    from agentic_sim_world_gen import service
    from agentic_sim_world_gen.assets import rerank

    entries = json.loads(OUT.read_text()) if OUT.exists() else []
    if not entries:
        sys.exit(f"no choices in {OUT}; run collect first")
    service.set_provider(Memo(service.get_provider()))
    ranks = run_choices(entries)
    for e, r in zip(entries, ranks):
        mark = "o" if r == 0 else ("~" if r is not None and r < 5 else "x")
        print(f"{mark} {e['query']!r:32} {e['name'][:44]:44} now {r if r is not None else '-'} "
              f"(then {e.get('rank_then')}, {','.join(e.get('sources', []))})")
    base = summary(ranks)
    print("current:", base, "| 35 queries P@1", round(graded_p1(), 3))
    if not grid:
        return
    keep = dict(rerank.WEIGHTS)
    print(f"{'weight':12} {'x':>5} {'top1':>6} {'top5':>6} {'mrr':>6} {'P@1(35)':>8}")
    for k in keep:
        for f in (0.5, 0.8, 1.25, 1.5):
            rerank.WEIGHTS.update(keep)
            rerank.WEIGHTS[k] = keep[k] * f
            s = summary(run_choices(entries))
            print(f"{k:12} {f:5.2f} {s['top1']:6.2f} {s['top5']:6.2f} {s['mrr']:6.3f} {graded_p1():8.3f}")
    rerank.WEIGHTS.update(keep)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect")
    c.add_argument("roots", nargs="+")
    c.add_argument("--prefs", action="append", default=[])
    s = sub.add_parser("score")
    s.add_argument("--grid", action="store_true")
    a = ap.parse_args()
    collect(a.roots, a.prefs) if a.cmd == "collect" else score(a.grid)
