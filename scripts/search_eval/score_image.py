"""Score the image-similarity weak rules from the committed data (data/runs_image.json, written by run_image.py
on the Ubuntu machine), no network or model needed.

- P@1 / P@1~ and wrong(top5) as in score_a2.py (grades.json + grades_a2/size/image; grades_image.json grades the 47
  top-5 hits nobody had judged, from name + caption, not blind)
- lost: grade >= 1 assets that were in the top 5 without the rule and are not with it (false exclusions)
- flagged: grades of the pool assets the rule marks weak among those the metadata keeps (g1 / g2 = false flags)
- AUC: how well the similarity alone separates grade 0 from grade 2 (judged pool assets)

    python scripts/search_eval/score_image.py [--per-query RULE]
"""
import json
import sys
from pathlib import Path

D = Path(__file__).parent / "data"
grades = json.loads((D / "grades.json").read_text())
for f in ("grades_a2.json", "grades_size.json", "grades_image.json"):
    for q, extra in json.loads((D / f).read_text()).items():
        grades[q].update(extra)
run = json.loads((D / "runs_image.json").read_text())
pools, base = run["pools"], run["base"]
sim = {(q, r["name"]): r["sim"] for q, rows in pools.items() for r in rows}


def g(q, name):
    return grades[q].get(name)


def score(tops):
    n = len(tops)
    p1 = sum(bool(v) and g(q, v[0]) == 2 for q, v in tops.items()) / n
    p1s = sum(bool(v) and (g(q, v[0]) or 0) >= 1 for q, v in tops.items()) / n
    top = [g(q, x) for q, v in tops.items() for x in v]
    lost = [f"{q}:{x}(g={g(q, x)},s={sim.get((q, x))})" for q, v in tops.items() for x in base[q]
            if x not in v and (g(q, x) or 0) >= 1]
    return p1, p1s, sum(x == 0 for x in top), len(top), sum(x is None for x in top), lost


def flagged(rule):
    kind, t = rule.split(":")
    t = float(t)
    out = {0: 0, 1: 0, 2: 0, None: 0}
    for q, rows in pools.items():
        best = max(r["sim"] for r in rows if r["sim"] is not None)
        for r in rows:
            s = r["sim"]
            if r["meta_weak"] or s is None:
                continue
            if (kind == "abs" and s < t) or (kind == "rel" and s < best - t) or \
                    (kind == "prob" and 1 / (1 + 2.718281828 ** -(run["scale"] * s + run["bias"])) < t):
                out[g(q, r["name"])] += 1
    return out


g0 = [r["sim"] for q, rows in pools.items() for r in rows if r["sim"] is not None and not r["meta_weak"] and g(q, r["name"]) == 0]
g2 = [r["sim"] for q, rows in pools.items() for r in rows if r["sim"] is not None and not r["meta_weak"] and g(q, r["name"]) == 2]
auc = sum((a < b) + 0.5 * (a == b) for a in g0 for b in g2) / len(g0) / len(g2)
print(f"similarity of metadata-kept judged assets: {len(g0)} grade 0, {len(g2)} grade 2, AUC {auc:.2f};"
      f" lowest grade 2 {min(g2):.4f}")
print("rule        P@1  P@1~ wrong(top5)    unjudged lost | flagged g0 g1 g2 unjudged")
p1, p1s, w, n5, un, lost = score(base)
print(f"{'none':10s} {p1:5.2f} {p1s:5.2f} {w / n5:5.3f} ({w:2d}/{n5}) {un:4d} {len(lost):4d} |")
for rule, tops in sorted(run["rules"].items(), key=lambda kv: (kv[0].split(":")[0], float(kv[0].split(":")[1]))):
    p1, p1s, w, n5, un, lost = score(tops)
    f = flagged(rule)
    print(f"{rule:10s} {p1:5.2f} {p1s:5.2f} {w / n5:5.3f} ({w:2d}/{n5}) {un:4d} {len(lost):4d} |"
          f" {f[0]:10d} {f[1]:2d} {f[2]:2d} {f[None]:5d}  {' '.join(lost[:3])}")
if "--per-query" in sys.argv:
    rule = sys.argv[sys.argv.index("--per-query") + 1]
    for q, v in run["rules"][rule].items():
        if v != base[q]:
            print(f"{q:13s} out: {[(x, g(q, x)) for x in base[q] if x not in v]}  in: {[(x, g(q, x)) for x in v if x not in base[q]]}")
