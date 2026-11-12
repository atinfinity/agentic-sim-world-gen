"""Build the asset cards (src/agentic_sim_world_gen/data/asset_cards.jsonl.gz; read by agentic_sim_world_gen.assets.cards).

    PYTHONPATH=src python scripts/build_asset_cards.py todo  --out W [--source isaac] [--match rack] [--limit 50]
    PYTHONPATH=src python scripts/build_asset_cards.py drop  --out W [--batch 12]     # Isaac Sim (Ubuntu)
    PYTHONPATH=src python scripts/build_asset_cards.py sheet --out W [--per 12]       # thumbnails to look at
    PYTHONPATH=src python scripts/build_asset_cards.py merge --out W [--cards FILE]   # W/drop.jsonl + W/look.jsonl

todo   lists the assets without a card into W/todo.jsonl (the Isaac index, or with --scene the candidates of a
       scene.resolved.json: whatever source they came from).
drop   puts the assets of todo.jsonl on the floor of an empty room, --batch at a time, 3 m apart, and runs the
       same check as check_in_isaac (scripts/isaac/check_scene.py, 240 steps). W/drop.jsonl gets, per asset,
       {key, physics: {stable, moved_m, sink_m, bodies, colliders, exploded?, resolved}}. An asset alone that moves,
       sinks or has nothing to collide with will do so in every scene. Continues where it stopped.
sheet  writes W/sheet_NN.jpg (numbered thumbnails, fetched from the catalog) and W/look_todo.md: what to
       answer per asset, as lines of W/look.jsonl. Whoever looks (a VLM, the agent reading the sheets, a
       person) writes look.jsonl; this script calls no model.
merge  checks the lines and writes them into the card file (a card keeps what a new line does not give).
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import math
import sys
from pathlib import Path

from agentic_sim_world_gen.assets import cards

LOOK_KEYS = {"kind": str, "also": list, "front_deg": (int, float), "colors": list, "materials": list,
             "features": dict, "note": str, "mount": str}
SPACING = 3.0  # [m] between the assets of a drop batch
STEPS = 240


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()] if path.exists() else []


def todo(a) -> None:
    have = {} if a.all else cards.all_cards()
    if a.scene:
        from agentic_sim_world_gen.scene.spec import ResolvedScene

        scene = ResolvedScene.model_validate_json(Path(a.scene).read_text())
        recs = [c for p in scene.placements for c in p.candidates]
    else:
        from agentic_sim_world_gen.assets.providers.isaac_index import index_path

        with gzip.open(index_path(), "rt", encoding="utf-8") as f:
            recs = [json.loads(x) for x in f]
    rows, seen = [], set()
    for r in recs:
        k = cards.key(r["uri"])
        text = " ".join(str(r.get(x) or "") for x in ("name", "semantic_class", "caption", "uri")).lower()
        if k in seen or k in have or (a.match and not all(w in text for w in a.match.lower().split())):
            continue
        seen.add(k)
        rows.append({"key": k, "record": {x: v for x, v in r.items() if x not in ("match", "index")}})
    rows = rows[: a.limit] if a.limit else rows
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "todo.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    print(f"{len(rows)} asset(s) without a card -> {a.out / 'todo.jsonl'}")


def physics_of(o: dict) -> dict:
    """The card's physics from one object of check.json."""
    ph = {"resolved": bool(o.get("resolved")), "bodies": int(o.get("rigid_bodies") or 0),
          "colliders": int(o.get("colliders") or 0)}
    if o.get("exploded"):
        ph["exploded"] = True
    if o.get("stable") is not None:
        ph["stable"] = bool(o["stable"])
    if o.get("max_displacement_m") is not None:
        ph["moved_m"] = o["max_displacement_m"]
    if o.get("settled_min_z") is not None:
        ph["sink_m"] = round(max(0.0, -o["settled_min_z"]), 3)
    return ph


def drop_scene(rows: list[dict]):
    """An empty room with the assets on its floor, SPACING apart; ids a0, a1, ..."""
    from agentic_sim_world_gen.scene.spec import ObjectSpec, Placement, ResolvedScene, Room, SceneSpec, record_from_dict

    n = math.ceil(math.sqrt(len(rows)))
    big = max(max(record_from_dict(r["record"]).size_m[:2]) for r in rows)
    step = SPACING + big
    side = round(n * step + 2, 2)
    objs = [ObjectSpec(id=f"a{i}", query=r["record"].get("name") or r["key"],
                       position=(1 + step / 2 + (i % n) * step, 1 + step / 2 + (i // n) * step))
            for i, r in enumerate(rows)]
    spec = SceneSpec(name="drop", room=Room(width=side, depth=side, height=max(3.0, big + 1)), objects=objs)
    return ResolvedScene(spec=spec, placements=[Placement(object_id=f"a{i}", candidates=[r["record"]])
                                                for i, r in enumerate(rows)])


def drop(a) -> None:
    from agentic_sim_world_gen import service
    from agentic_sim_world_gen.usd.compose import compose

    rows = _jsonl(a.out / "todo.jsonl")
    done = {r["key"] for r in _jsonl(a.out / "drop.jsonl")}
    rows = [r for r in rows if r["key"] not in done]
    stamp = f"isaac-sim check_scene {STEPS} steps, {dt.date.today().isoformat()}"
    for b in range(0, len(rows), a.batch):
        batch = rows[b: b + a.batch]
        folder = a.out / f"drop_{len(done) + b:05d}"
        folder.mkdir(parents=True, exist_ok=True)
        usd, report = folder / "scene.usda", folder / "check.json"
        try:
            compose(drop_scene(batch), usd)
            proc, err = service._run_isaac(service.CHECK_SCRIPT, [str(usd), "--steps", str(STEPS), "--report",
                                                                        str(report), "--screenshot",
                                                                        str(folder / "shot.png")], 1800)
        except Exception as e:  # noqa: BLE001  (an asset that cannot be fetched: say so, go on with the next batch)
            print(f"batch {folder.name}: {e}", file=sys.stderr)
            continue
        if proc is None or not report.exists():
            print(f"batch {folder.name}: {err or proc.stderr[-400:]}", file=sys.stderr)
            if a.batch > 1:
                print("  (run it again with --batch 1 to find the asset)", file=sys.stderr)
            continue
        objects = json.loads(report.read_text()).get("objects") or {}
        with open(a.out / "drop.jsonl", "a") as f:
            for i, r in enumerate(batch):
                if (o := objects.get(f"a{i}")) is not None:
                    f.write(json.dumps({"key": r["key"], "physics": physics_of(o), "by": {"drop": stamp}}) + "\n")
        print(f"{folder.name}: {len(batch)} asset(s)")


def sheet(a) -> None:
    import io

    import requests
    from PIL import Image, ImageDraw

    rows = _jsonl(a.out / "todo.jsonl")
    tile, cols = 256, 4
    lines = ["# What to answer per asset (one JSON line each in look.jsonl)", "",
             '`{"key": "<key>", "kind": "<what it is, English, noun last: \\"floor lamp\\">", "also": ["<other names>"],',
             ' "colors": ["white"], "materials": ["metal"], "features": {"legs": true, "knee_space": false, "shelves": 0},',
             ' "front_deg": <only when the picture shows it; 0 = +X of the asset, 90 = +Y>,',
             ' "mount": "<floor | on (a table, a shelf) | wall | ceiling: say it for what hangs>", "note": "<doubts>"}`', "",
             "Leave out what the picture does not show. `kind` is what the thing is, not what its label says.", ""]
    for s in range(0, len(rows), a.per):
        part = rows[s: s + a.per]
        img = Image.new("RGB", (cols * tile, math.ceil(len(part) / cols) * tile), "white")
        for i, r in enumerate(part):
            rec = r["record"]
            try:
                t = Image.open(io.BytesIO(requests.get(rec["thumbnail_url"], timeout=30).content)).convert("RGB")
                t.thumbnail((tile, tile))
                img.paste(t, ((i % cols) * tile, (i // cols) * tile))
            except Exception as e:  # noqa: BLE001
                print(f"{r['key']}: no thumbnail ({e})", file=sys.stderr)
            ImageDraw.Draw(img).text(((i % cols) * tile + 6, (i // cols) * tile + 4), str(s + i), fill="red")
            size = "×".join(f"{v:.2f}" for v in _size(rec))
            lines.append(f"- {s + i} (sheet_{s // a.per:02d}.jpg) key `{r['key']}`: labelled "
                         f"\"{rec.get('semantic_class') or '-'}\", {size} m, \"{(rec.get('caption') or '')[:100]}\"")
        img.save(a.out / f"sheet_{s // a.per:02d}.jpg", quality=88)
    (a.out / "look_todo.md").write_text("\n".join(lines) + "\n")
    print(f"{math.ceil(len(rows) / a.per)} sheet(s), {a.out / 'look_todo.md'}")


def _size(rec: dict) -> tuple[float, float, float]:
    from agentic_sim_world_gen.scene.spec import record_from_dict

    return record_from_dict(rec).size_m


def check_look(d: dict) -> list[str]:
    bad = [f"{k}: not {t}" for k, t in LOOK_KEYS.items() if k in d and not isinstance(d[k], t)]
    bad += [f"unknown field {k}" for k in d if k not in LOOK_KEYS and k not in ("key", "by", "physics")]
    if "front_deg" in d and isinstance(d["front_deg"], (int, float)) and d["front_deg"] % 90:
        bad.append("front_deg: a multiple of 90")
    if isinstance(d.get("mount"), str) and d["mount"] not in cards.MOUNTS:
        bad.append(f"mount: one of {', '.join(cards.MOUNTS)}")
    return bad


def merge(a) -> None:
    file = Path(a.cards) if a.cards else cards.DATA / cards.FILE
    out = cards.read(file) if file.exists() else {}
    n = 0
    for name in ("drop.jsonl", "look.jsonl"):
        for d in _jsonl(a.out / name):
            if bad := check_look(d):
                sys.exit(f"{name}: {d.get('key')}: {'; '.join(bad)}")
            card = out.setdefault(d["key"], {"key": d["key"]})
            by = {**card.get("by", {}), **d.get("by", {})}
            if name == "look.jsonl" and "look" not in d.get("by", {}):
                by["look"] = f"{a.by}, {dt.date.today().isoformat()}"
            card.update({k: v for k, v in d.items() if k != "by"})
            card["by"] = by
            n += 1
    opener = gzip.open if file.suffix == ".gz" else open
    with opener(file, "wt", encoding="utf-8") as f:
        for k in sorted(out):
            f.write(json.dumps(out[k], ensure_ascii=False, sort_keys=True) + "\n")
    print(f"{n} line(s) merged, {len(out)} card(s) in {file}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("todo", "drop", "sheet", "merge"):
        p = sub.add_parser(name)
        p.add_argument("--out", type=Path, required=True, help="work folder")
        if name == "todo":
            p.add_argument("--scene", help="take the candidates of this scene.resolved.json instead of the Isaac index")
            p.add_argument("--match", help="only assets with all these words in name / class / caption / uri")
            p.add_argument("--limit", type=int)
            p.add_argument("--all", action="store_true", help="also the assets that have a card")
        if name == "drop":
            p.add_argument("--batch", type=int, default=12)
        if name == "sheet":
            p.add_argument("--per", type=int, default=12)
        if name == "merge":
            p.add_argument("--cards", help="card file to write (default: the one in src/agentic_sim_world_gen/data)")
            p.add_argument("--by", default="look", help="who looked (model or person), recorded in by.look")
    a = ap.parse_args()
    {"todo": todo, "drop": drop, "sheet": sheet, "merge": merge}[a.cmd](a)


if __name__ == "__main__":
    main()
