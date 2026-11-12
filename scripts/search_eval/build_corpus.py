import json, gzip, sys, hashlib, os, requests
from concurrent.futures import ThreadPoolExecutor
from agentic_sim_world_gen.assets.providers import simready_central as sc
E=sys.argv[1]
recs=sc.parse_response(json.load(gzip.open(f"{E}/simready_all.json.gz","rt")))
corpus=[]
for r in recs:
    corpus.append(dict(key=r.name, source="simready-central", uri=r.uri, name=r.name, cls=r.semantic_class or "", caption=r.caption or "", tags=list(r.tags or []), thumb=r.thumbnail_url, bbox=[list(r.bbox_min),list(r.bbox_max)] if r.bbox_min else None))
names={c["name"] for c in corpus}
for row in (json.loads(l) for l in gzip.open('src/agentic_sim_world_gen/data/isaac_6.1.jsonl.gz')):
    if row['name'] in names: continue
    corpus.append(dict(key=row['name'], source="isaac", uri=row['uri'], name=row['name'], cls=row.get('semantic_class') or "", caption=row.get('caption') or "", tags=row.get('tags') or [], thumb=row.get('thumbnail_url'), bbox=[row['bbox_min'],row['bbox_max']] if row.get('bbox_min') else None))
os.makedirs(f"{E}/thumbs",exist_ok=True)
s=requests.Session()
def get(c):
    c["img"]=hashlib.md5(c["uri"].encode()).hexdigest()+".png"
    p=f"{E}/thumbs/{c['img']}"
    if os.path.exists(p): return 1
    try:
        r=s.get(c["thumb"],timeout=60)
        if r.ok: open(p,"wb").write(r.content); return 1
    except Exception: pass
    return 0
with ThreadPoolExecutor(16) as ex: ok=sum(ex.map(get,corpus))
json.dump(corpus,open(f"{E}/corpus.json","w"))
print(len(corpus),"thumbs ok",ok)
