"""Compare A (before) and A2 (with the query rules) on what the agent is shown, from the committed data:

- P@1 / P@1~: top pick is the object / at least a substitute (as in score.py)
- shown: candidates listed (at most 10); wrong: share of the listed top 5 graded 0
- flagged: queries marked `substitute` (every candidate weak); gap: queries with no grade-2 asset in any system

    python scripts/search_eval/score_a2.py [--per-query]
"""
import json
import sys
from pathlib import Path

D = Path(__file__).parent / "data"
queries = [q["id"] for q in json.loads((D / "queries.json").read_text())]
grades = json.loads((D / "grades.json").read_text())
# A2 top-5 hits that no system had when judging; graded afterwards from name + caption (not blind)
for q, extra in json.loads((D / "grades_a2.json").read_text()).items():
    grades[q].update(extra)
runs = {"A": json.loads((D / "runs.json").read_text())["A"]}
a2 = json.loads((D / "runs_a2.json").read_text())
runs["A2"] = {q: v["names"] for q, v in a2.items()}
gap = {q for q in queries if 2 not in grades[q].values()}


def g(q, name):
    return grades[q].get(name)  # None = not judged (did not reach any system's top 5 when judging)


def row(system, q):
    names = runs[system][q]
    top = [g(q, n) for n in names[:5]]
    return {
        "p1": bool(names) and top[0] == 2, "p1~": bool(names) and (top[0] or 0) >= 1,
        "shown": len(names), "wrong": sum(x == 0 for x in top), "unjudged": sum(x is None for x in top), "n5": len(top),
    }


def table(qs):
    for s in runs:
        rows = [row(s, q) for q in qs]
        n = len(rows)
        print(f"{s:6s} {sum(r['p1'] for r in rows) / n:5.2f} {sum(r['p1~'] for r in rows) / n:5.2f}"
              f" {sum(r['shown'] for r in rows) / n:6.1f}  {sum(r['wrong'] for r in rows) / sum(r['n5'] for r in rows):10.2f}"
              f"  {sum(r['unjudged'] for r in rows):8d}")


print(f"{len(queries)} queries, {len(gap)} with no such asset in the catalog: {sorted(gap)}")
for title, qs in [("all", queries), ("in catalog", [q for q in queries if q not in gap]), ("not in catalog", sorted(gap))]:
    print(f"\n{title} ({len(qs)})\nsystem   P@1  P@1~  shown  wrong(top5)  unjudged")
    table(qs)
flag = {q for q, v in a2.items() if v["substitute"]}
print(f"A2 flagged substitute: {sorted(flag)} ({len(flag & gap)}/{len(gap)} gaps, {len(flag - gap)} false alarms)")
if "--per-query" in sys.argv:
    print("\nquery         A:top1 shown wrong | A2:top1 shown wrong flag")
    for q in queries:
        a, b = row("A", q), row("A2", q)
        print(f"{q:13s} {str(g(q, runs['A'][q][0]) if runs['A'][q] else '-'):>6} {a['shown']:5d} {a['wrong']:5d} |"
              f" {str(g(q, runs['A2'][q][0]) if runs['A2'][q] else '-'):>7} {b['shown']:5d} {b['wrong']:5d}"
              f" {'*' if q in flag else ''}{' gap' if q in gap else ''}")
