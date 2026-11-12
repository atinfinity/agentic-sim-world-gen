"""C = SigLIP2 text->thumbnail, D = e5 text->(class+caption+tags+name), *_ja = Japanese query,
E1 = RRF(A, C, D), E2 = metadata rerank (rerank.py) over the C+D pool, ties by RRF(C, D)."""
import json, sys
import numpy as np
from agentic_sim_world_gen.assets.record import AssetRecord
from agentic_sim_world_gen.assets.rerank import rerank

E = sys.argv[1]
corpus = json.load(open(f"{E}/corpus.json"))
queries = json.load(open(f"{E}/queries.json"))
ab = json.load(open(f"{E}/runs_ab.json"))
L = lambda n: np.load(f"{E}/emb/{n}.npy")
img, doc = L("siglip_img"), L("e5_doc")
keys = [c["key"] for c in corpus]
def top(sim, n=40): return [keys[i] for i in np.argsort(-sim)[:n]]
def rrf(*lists, k=60):
    s = {}
    for l in lists:
        for r, x in enumerate(l): s[x] = s.get(x, 0) + 1 / (k + r + 1)
    return sorted(s, key=lambda x: -s[x])
rec = {c["key"]: AssetRecord(id=c["key"], source=c["source"], uri=c["uri"], name=c["name"], semantic_class=c["cls"] or None,
                             caption=c["caption"] or None, tags=c["tags"], bbox_min=None, bbox_max=None) for c in corpus}
out = {k: {} for k in ("C", "C_alt", "C_ja", "D", "D_alt", "D_ja", "E1", "E2", "E2_ja")}
for i, q in enumerate(queries):
    qid = q["id"]
    C = top(img @ L("siglip_q_en")[i]); Ca = top(img @ L("siglip_q_en_alt")[i]); Cj = top(img @ L("siglip_q_ja")[i])
    D = top(doc @ L("e5_q_en")[i]); Da = top(doc @ L("e5_q_en_alt")[i]); Dj = top(doc @ L("e5_q_ja")[i])
    out["C"][qid], out["C_alt"][qid], out["C_ja"][qid] = C, Ca, Cj
    out["D"][qid], out["D_alt"][qid], out["D_ja"][qid] = D, Da, Dj
    out["E1"][qid] = rrf(ab["A"][qid], Ca, Da)[:40]
    for name, (c, d, qq, alt) in {"E2": (Ca, Da, q["q"], q["alt"]), "E2_ja": (Cj, Dj, q["ja"], [])}.items():
        pool = rrf(c[:30], d[:30])
        ranked = rerank([rec[x] for x in pool], qq, alt)  # stable: keeps the RRF order on ties
        out[name][qid] = [r.id for r, _ in ranked]
json.dump(out, open(f"{E}/runs_cde.json", "w"), indent=1)
print({k: v["desk"][:3] for k, v in out.items()})
