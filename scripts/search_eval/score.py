"""Score every system from the committed data: P@1, P@5 and hit@5, strict (grade 2) and lenient (grade >= 1).

    python scripts/search_eval/score.py            # table
    python scripts/search_eval/score.py --per-query
"""
import json
import sys
from pathlib import Path

D = Path(__file__).parent / "data"
queries = [q["id"] for q in json.loads((D / "queries.json").read_text())]
runs = json.loads((D / "runs.json").read_text())
grades = json.loads((D / "grades.json").read_text())
ORDER = ["A", "B", "C", "C_alt", "D", "D_alt", "E1", "E2", "F", "A_ja", "B_ja", "C_ja", "D_ja", "E2_ja", "F_ja"]


def top5(system, q):
    g = [grades[q].get(name, 0) for name in runs[system][q][:5]]
    return g + [0] * (5 - len(g))


def metrics(system):
    rows = [top5(system, q) for q in queries]
    n = len(rows)
    return {
        "P@1": sum(g[0] == 2 for g in rows) / n, "P@1~": sum(g[0] >= 1 for g in rows) / n,
        "P@5": sum(sum(x == 2 for x in g) / 5 for g in rows) / n, "P@5~": sum(sum(x >= 1 for x in g) / 5 for g in rows) / n,
        "hit@5": sum(2 in g for g in rows) / n, "hit@5~": sum(any(x >= 1 for x in g) for g in rows) / n,
    }


if __name__ == "__main__":
    print("(~ = substitutes count)")
    print("system  " + " ".join(f"{k:>6}" for k in metrics("A")))
    for s in ORDER:
        print(f"{s:7s} " + " ".join(f"{v:6.2f}" for v in metrics(s).values()))
    if "--per-query" in sys.argv:
        print("\ntop-1 grade\n" + "query".ljust(13) + " ".join(s[:5].rjust(5) for s in ORDER))
        for q in queries:
            print(q.ljust(13) + " ".join(str(top5(s, q)[0]).rjust(5) for s in ORDER))
