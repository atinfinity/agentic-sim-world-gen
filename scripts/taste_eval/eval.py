"""Evaluate the taste check on constructed study / living-room sets, and the re-rank term.

    # embed every thumbnail once (needs torch + transformers and the SigLIP2 model in the HF cache)
    HF_HUB_OFFLINE=1 CUDA_VISIBLE_DEVICES= PYTHONPATH=src python scripts/taste_eval/eval.py --embed
    PYTHONPATH=src python scripts/taste_eval/eval.py            # numbers from the cached embeddings

1. Outliers: from each template in data/sets.json, sample coherent sets (one "ok" pick per role) and
   planted sets (the same with one role swapped for an "off" pick) and run agentic_sim_world_gen.taste.analyze on
   them as on a scene (each role's pool = its candidates). Detection = the planted object is the first
   flag; false flags = a coherent set gets any flag. Also: is the first suggested alternative "ok"?
   Raw embeddings vs residuals (minus the mean of the role's candidates), margin, and the gain a
   candidate must offer before anything is flagged.
2. Re-rank term: score + w * closeness to the other chosen objects. (a) On the coherent templates: how
   often the top pick of a role is "ok" for the others. (b) On the search-eval queries grouped into rooms: P@1
   (grades from scripts/search_eval) when the context is the current top picks of the other queries.
"""
import argparse
import json
import random
from pathlib import Path

from agentic_sim_world_gen import taste

D = Path(__file__).parent / "data"
SE = Path(__file__).parents[1] / "search_eval" / "data"
ROOMS = {  # search-eval queries that share a room (context for the re-rank term)
    "study": ["desk", "chair", "bookshelf", "lamp", "filing", "monitor", "trash", "laptop", "mug"],
    "living": ["sofa", "tv", "plant", "dining_table", "lamp", "bed", "fridge", "microwave", "mug"],
    "office": ["conf_table", "chair", "whiteboard", "locker", "filing", "monitor", "trash", "plant"],
    "warehouse": ["pallet", "cardboard", "rack", "hand_truck", "forklift", "cone", "bin", "barrel", "fire_ext"],
}


def vectors(urls, embed):
    cache = taste.Cache(taste.MODEL)
    if embed:
        e = taste.SiglipEmbedder(taste.MODEL)
        vecs = taste.embed_urls(urls, e, cache)
        taste.embed_texts(taste.prompts(), e, cache)
        return vecs
    return {u: v for u in urls if (v := cache.get(u)) is not None}


def sample_sets(tpl, n, rng):
    roles = list(tpl)
    out = []
    for _ in range(n):
        pick = {r: rng.choice(tpl[r]["ok"]) for r in roles}
        cand = [r for r in roles if tpl[r]["off"]]
        bad = rng.choice(cand)
        planted = dict(pick, **{bad: rng.choice(tpl[bad]["off"])})
        out.append((pick, planted, bad))
    return out


def items(pools, tpl, pick):
    """The set as agentic_sim_world_gen.taste sees a scene: each role's pick among its candidate pool."""
    return [taste.Item(r, r, pick[r], [(i, c["name"], c["thumb"], c["weak"])
                                       for i, c in enumerate(pools[t["pool"][0]][t["pool"][1]])])
            for r, t in tpl.items()]


def outlier_eval(pools, sets, vecs, n, configs, title):
    """taste.analyze on coherent and planted sets. Detected = the planted object is the first flag."""
    print(f"\n## outliers: {title} ({n} sets per template x {len(sets['templates'])} templates)")
    print("| feature | margin | gain | detected | other objects flagged | false flags (coherent) | 1st alternative ok |")
    print("|---|---|---|---|---|---|---|")
    out = {}
    for residual, margin, gain in configs:
        rng = random.Random(0)
        det = other = fp = alt_ok = alt_n = k = 0
        per = {}
        for name, tpl in sets["templates"].items():
            pt = per.setdefault(name, [0, 0, 0])
            for pick, planted, bad in sample_sets(tpl, n, rng):
                k += 1
                pt[2] += 1
                r0 = taste.analyze(items(pools, tpl, pick), vecs, margin=margin, gain=gain, residual=residual)
                fp += bool(r0["outliers"])
                pt[1] += bool(r0["outliers"])
                r1 = taste.analyze(items(pools, tpl, planted), vecs, margin=margin, gain=gain, residual=residual)
                flagged = [o["object"] for o in r1["outliers"]]
                hit = bool(flagged) and flagged[0] == bad
                det += hit
                pt[0] += hit
                other += any(x != bad for x in flagged)
                if hit and r1["outliers"][0]["alternatives"]:
                    alt_n += 1
                    alt_ok += r1["outliers"][0]["alternatives"][0]["index"] in tpl[bad]["ok"]
        out[(residual, margin, gain)] = per
        print(f"| {'residual' if residual else 'raw'} | {margin:.2f} | {gain:.2f} | {det / k:.2f} | {other / k:.2f} | "
              f"{fp / k:.2f} | {alt_ok / max(1, alt_n):.2f} |")
    return out


def per_template(per, n):
    print(f"\n## per template (the defaults: residual, margin {taste.MARGIN}, gain {taste.FLAG_GAIN})")
    print("| template | detected | false flags |")
    print("|---|---|---|")
    for name, (det, fp, k) in per.items():
        print(f"| {name} | {det / k:.2f} | {fp / k:.2f} |")


def rerank_sets(pools, sets, vecs, weights, n):
    """(a): top pick of a role re-ranked with closeness to the other (coherent) picks: is it 'ok'?"""
    rng = random.Random(1)
    hits = {w: 0 for w in weights}
    total = 0
    for tpl in sets["templates"].values():
        roles = list(tpl)
        for pick, _, _ in sample_sets(tpl, n, rng):
            for r in roles:
                room, obj = tpl[r]["pool"]
                cands = [c for c in pools[room][obj] if c["thumb"] in vecs]
                idx = [i for i, c in enumerate(pools[room][obj]) if c["thumb"] in vecs]
                others = [vecs[pools[tpl[o]["pool"][0]][tpl[o]["pool"][1]][pick[o]]["thumb"]] for o in roles if o != r]
                cl = taste.closeness([vecs[c["thumb"]] for c in cands], others)
                total += 1
                for w in weights:
                    best = max(range(len(cands)), key=lambda k: (cands[k]["score"] + w * cl[k], -k))
                    hits[w] += idx[best] in tpl[r]["ok"]
    print(f"\n## re-rank (a): top pick fits the coherent set ({total} role picks)")
    print("| w | top pick is an 'ok' pick |")
    print("|---|---|")
    for w in weights:
        print(f"| {w:g} | {hits[w] / total:.2f} |")


def rerank_queries(runs, vecs, weights):
    """(b): P@1 on the search-eval queries with the other queries of the room as context."""
    grades = json.load(open(SE / "grades.json"))
    for k, v in json.load(open(SE / "grades_a2.json")).items():
        grades.setdefault(k, {}).update(v)
    print("\n## re-rank (b): search-eval queries, context = top picks of the other queries in the room")
    print("| w | P@1 | P@1 (substitute ok) | top pick changed | ungraded new picks |")
    print("|---|---|---|---|---|")
    for w in weights:
        p1 = p1s = changed = ungraded = n = 0
        for room, qs in ROOMS.items():
            for q in qs:
                cands = [c for c in runs.get(q, []) if c["thumb"] in vecs]
                if not cands:
                    continue
                others = [vecs[runs[o][0]["thumb"]] for o in qs if o != q and runs.get(o) and runs[o][0]["thumb"] in vecs]
                cl = taste.closeness([vecs[c["thumb"]] for c in cands], others)
                best = max(range(len(cands)), key=lambda k: (cands[k]["score"] + w * cl[k], -k))
                g = grades.get(q, {}).get(cands[best]["name"])
                n += 1
                changed += best != 0
                if g is None:
                    ungraded += 1
                    continue
                p1 += g == 2
                p1s += g >= 1
        print(f"| {w:g} | {p1 / n:.2f} | {p1s / n:.2f} | {changed} / {n} | {ungraded} |")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--embed", action="store_true", help="embed missing thumbnails (loads the model)")
    ap.add_argument("-n", type=int, default=40, help="sets per template")
    a = ap.parse_args()
    pools = json.load(open(D / "pools.json"))
    sets = json.load(open(D / "sets.json"))
    runs = json.load(open(D / "search_runs.json"))
    urls = [c["thumb"] for room in pools.values() for cs in room.values() for c in cs if c["thumb"]]
    urls += [c["thumb"] for cs in runs.values() for c in cs if c["thumb"]]
    vecs = vectors(urls, a.embed)
    print(f"{len(vecs)} / {len(set(urls))} thumbnails embedded")
    margins = [0.0, 0.05, 0.10, 0.15, 0.20, 0.25]
    outlier_eval(pools, sets, vecs, a.n, [(False, m, 0.0) for m in (0.02, 0.04, 0.06, 0.08, 0.10)],
                 "raw embeddings, any better candidate")
    outlier_eval(pools, sets, vecs, a.n, [(True, m, 0.0) for m in margins], "residuals, any better candidate")
    outlier_eval(pools, sets, vecs, a.n, [(True, m, g) for g in (0.2, 0.3, 0.4) for m in (0.05, 0.10, 0.15)],
                 "residuals, a candidate at least `gain` better")
    per = outlier_eval(pools, sets, vecs, a.n, [(True, taste.MARGIN, taste.FLAG_GAIN)], "the defaults")
    per_template(per[(True, taste.MARGIN, taste.FLAG_GAIN)], a.n)
    weights = [0, 0.25, 0.5, 1, 2, 4]
    rerank_sets(pools, sets, vecs, weights, a.n)
    rerank_queries(runs, vecs, weights)


if __name__ == "__main__":
    main()
