"""Mark candidates whose thumbnail looks unlike the query (SigLIP2 image-text similarity) as weak, and see
whether the list gets cleaner. The 35 queries are searched once (memoized) and the image check only re-marks weak,
the rest of the pipeline (`service._find_candidates`: drop weak when a good match exists, size / place order) is
unchanged. Graded with the committed grades (+ grades_a2 / grades_size). Needs network and the `taste` extra with
the SigLIP2 model in the Hugging Face cache (run on the Ubuntu machine, CPU):

    PYTHONPATH=src python scripts/search_eval/run_image.py [out.json]

Similarity of an asset = max over the query and its alt_queries of the cosine between the thumbnail embedding and
the text "this is a photo of a <query>." (SigLIP2 prompt). Rules tried (an asset is image-weak when):
- abs:t   similarity < t
- prob:p  sigmoid(logit_scale * sim + logit_bias) < p (the model's own "this caption matches" probability)
- rel:d   similarity < best similarity among that query's pool - d
Writes every pool asset's similarity and each rule's top 5 to out.json (default data/runs_image.json);
score_image.py scores that file offline with the grades (the table printed here uses only the grades known before).
"""
import json
import math
import sys
from pathlib import Path

from agentic_sim_world_gen import service, taste

D = Path(__file__).parent / "data"
grades = json.loads((D / "grades.json").read_text())
for extra_file in ("grades_a2.json", "grades_size.json", "grades_image.json"):
    if (D / extra_file).exists():
        for q, extra in json.loads((D / extra_file).read_text()).items():
            grades[q].update(extra)
queries = json.loads((D / "queries.json").read_text())
N = 10


class Memo:
    def __init__(self, p):
        self.p, self.name, self.seen = p, p.name, {}

    def search(self, query, *, limit=10, size=None):
        k = (query, limit, repr(size))
        if k not in self.seen:
            self.seen[k] = self.p.search(query, limit=limit, size=size)
        return self.seen[k]


class ImageWeak:
    """A rerank Match that is weak either way (metadata or image)."""

    def __init__(self, m):
        self.score, self.matched, self.weak = m.score, m.matched, True


service.set_provider(Memo(service.get_provider()))
rerank = service.rerank
pools: dict[str, list] = {}
flag = None  # callable (query id, record) -> bool, or None


def patched(pool, query, alt):
    ranked = rerank(pool, query, alt)
    pools[cur] = ranked
    if flag is None:
        return ranked
    return [(r, ImageWeak(m) if (not m.weak and flag(cur, r)) else m) for r, m in ranked]


service.rerank = patched
from agentic_sim_world_gen.scene.spec import ObjectSpec  # noqa: E402

objs = {q["id"]: ObjectSpec(id="x", query=q["q"], alt_queries=q["alt"]) for q in queries}
base = {}
for q in queries:
    cur = q["id"]
    base[cur] = service._find_candidates(objs[cur], N)

emb, why = taste.load_embedder()
if emb is None:
    sys.exit(f"skipped: {why}")
urls = [r.thumbnail_url for v in pools.values() for r, _ in v if r.thumbnail_url]
vecs = taste.embed_urls(urls, emb)
prompt = "this is a photo of a {}."
texts = {q["id"]: [prompt.format(t.lower()) for t in [q["q"], *q["alt"]]] for q in queries}
tv = taste.embed_texts([t for ts in texts.values() for t in ts], emb)
m, _ = emb._load()
scale, bias = float(m.logit_scale.exp().detach()), float(m.logit_bias.detach())

sim: dict[tuple[str, str], float] = {}
dump = {}
for q in queries:
    qid = q["id"]
    rows = []
    for r, mt in pools[qid]:
        v = vecs.get(r.thumbnail_url or "")
        s = max(taste._dot(v, tv[t]) for t in texts[qid]) if v else None
        if s is not None:
            sim[(qid, r.id)] = s
        rows.append({"name": r.name, "meta_weak": mt.weak, "sim": None if s is None else round(s, 4)})
    dump[qid] = rows
best = {qid: max([s for (q2, _), s in sim.items() if q2 == qid] or [0]) for qid in dump}


def rule(kind, t):
    def f(qid, r):
        s = sim.get((qid, r.id))
        if s is None:
            return False  # no thumbnail: cannot judge, keep
        if kind == "abs":
            return s < t
        if kind == "prob":
            return 1 / (1 + math.exp(-(scale * s + bias))) < t
        return s < best[qid] - t
    return f


gap = {q["id"] for q in queries if 2 not in grades[q["id"]].values()}


def stats(cands_by_q):
    n = len(queries)
    st = {"p1": 0, "p1~": 0, "wrong": 0, "n5": 0, "unjudged": 0, "shown": 0, "subst": set()}
    for q in queries:
        g = grades[q["id"]]
        c = cands_by_q[q["id"]]
        top = [g.get(x["name"]) for x in c[:5]]
        st["p1"] += bool(top) and top[0] == 2
        st["p1~"] += bool(top) and (top[0] or 0) >= 1
        st["wrong"] += sum(x == 0 for x in top)
        st["unjudged"] += sum(x is None for x in top)
        st["n5"] += len(top)
        st["shown"] += len(c)
        if c and all(x["match"]["weak"] for x in c):
            st["subst"].add(q["id"])
    return (f"{st['p1'] / n:5.2f} {st['p1~'] / n:5.2f} {st['wrong'] / st['n5']:6.2f} {st['unjudged']:5d}"
            f" {st['shown'] / n:5.1f} {len(st['subst']):3d}({len(st['subst'] - gap)} not gap)")


# grade of the image-flagged assets among those the metadata alone keeps (what the filter would hide)
def flagged(f):
    out = {0: 0, 1: 0, 2: 0, None: 0}
    examples = []
    for q in queries:
        qid = q["id"]
        for r, mt in pools[qid]:
            if not mt.weak and f(qid, r):
                gr = grades[qid].get(r.name)
                out[gr] += 1
                if gr in (1, 2):
                    examples.append(f"{qid}:{r.name}(g={gr},s={sim[(qid, r.id)]:.3f})")
    return out, examples


print(f"logit_scale {scale:.2f} bias {bias:.2f}; {len(sim)} pool assets with a thumbnail")
print("rule        P@1  P@1~ wrong(top5) unjudged shown subst | image-weak among metadata-kept: g0 g1 g2 unjudged")
print(f"{'none':10s} {stats(base)}")
rules = [("abs", t) for t in (0.0, 0.04, 0.05, 0.06, 0.07, 0.08, 0.09, 0.10, 0.11, 0.12)] + \
        [("prob", p) for p in (0.001, 0.01, 0.05)] + [("rel", d) for d in (0.04, 0.06, 0.08, 0.10)]
details = {}
for kind, t in rules:
    flag = rule(kind, t)
    res = {}
    for q in queries:
        cur = q["id"]
        res[cur] = service._find_candidates(objs[cur], N)
    fl, ex = flagged(flag)
    print(f"{kind + ':' + str(t):10s} {stats(res)} | {fl[0]:3d} {fl[1]:3d} {fl[2]:3d} {fl[None]:3d}")
    details[f"{kind}:{t}"] = {qid: [c["name"] for c in res[qid][:5]] for qid in res}
flag = None
out = Path(sys.argv[1]) if len(sys.argv) > 1 else D / "runs_image.json"
out.write_text(json.dumps({"scale": round(scale, 3), "bias": round(bias, 3), "pools": dump,
                           "base": {qid: [c["name"] for c in base[qid][:5]] for qid in base}, "rules": details},
                          ensure_ascii=False, separators=(",", ":")))
print("wrote", out)
