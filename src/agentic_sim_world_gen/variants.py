"""Variations of one scene: the same request with other assets, positions along the walls, floor / wall
finishes and room sizes, one per seed (environments for RL, synthetic data). SDG varies lights, materials and
cameras inside one scene; this makes separate scenes.

`vary(scene, rng, ...)` changes a copy in place and returns what it changed (shown on the index page and kept in
each variant's variant.json). The service keeps a draw only when it has no more layout issues than the source and
draws again otherwise (service.make_variants).
"""
from __future__ import annotations

import random
from pathlib import Path
from typing import Any

from agentic_sim_world_gen.scene.spec import STORE_PLACES, ResolvedScene, record_from_dict

KINDS = ("assets", "layout", "materials", "size")
TOP = 5  # draw assets among the first few candidates (the search's order is a quality order)
ALONG_STEP = 0.25  # a wall object moves this far along its wall at most (0..1 of the wall)
ALONG_RANGE = (0.1, 0.9)

# finishes usual for each kind of place (presets of scene.looks); a place not listed uses "other"
FLOORS = {
    "home": ("wood_light", "wood_dark", "tatami", "carpet_gray", "tile_white"),
    "office": ("carpet_gray", "tile_white", "wood_light"),
    "shop": ("tile_white", "wood_light", "concrete"),
    "cafe": ("wood_light", "wood_dark", "tile_white", "concrete"),
    "restaurant": ("wood_light", "wood_dark", "tile_white"),
    "warehouse": ("concrete", "epoxy_gray"),
    "factory": ("concrete", "epoxy_gray"),
    "plant_factory": ("epoxy_gray", "concrete", "tile_white"),
    "datacenter": ("raised_floor", "epoxy_gray"),
    "other": ("wood_light", "tile_white", "carpet_gray"),
}
WALLS = {
    "home": ("paint_white", "paint_beige", "drywall_gray"),
    "office": ("paint_white", "drywall_gray", "paint_beige"),
    "cafe": ("paint_white", "paint_beige", "brick"),
    "restaurant": ("paint_white", "paint_beige", "brick"),
    "warehouse": ("metal_panel", "concrete", "drywall_gray"),
    "factory": ("metal_panel", "concrete", "drywall_gray"),
    "plant_factory": ("metal_panel", "paint_white"),
    "datacenter": ("drywall_gray", "paint_white", "metal_panel"),
    "other": ("paint_white", "paint_beige", "drywall_gray"),
}
SHOPS = {"shop", *STORE_PLACES}
WALL_JA = {"north": "北の壁", "south": "南の壁", "east": "東の壁", "west": "西の壁", "any": "壁"}
PATH_KEYS = {"path", "texture", "roughness_texture", "normal_texture", "image", "underlay", "plan_file", "video"}


def _pool(table: dict[str, tuple[str, ...]], place: str | None) -> tuple[str, ...]:
    key = "shop" if place in SHOPS and place not in table else place
    return table.get(key or "other", table["other"])


LIGHT_PLACE = {"home": "home", "office": "office", "warehouse": "warehouse"}  # lighting presets named for a place


def place_of(s: ResolvedScene) -> str | None:
    """Room.place, else guessed from the scene's name and request items, else from the lighting preset (scenes
    made before Room.place have only these)."""
    from agentic_sim_world_gen.scene.questions import guess_place

    room = s.spec.room
    if room.place:
        return room.place
    text = " ".join([s.spec.name, *(r.text for r in s.spec.requirements)])
    return guess_place(text) or (LIGHT_PLACE.get(room.lighting) if isinstance(room.lighting, str) else None)


def absolute_paths(data: Any, base: Path) -> Any:
    """The scene's relative file paths (textures, photos, screen images, plan underlays) made absolute, so the
    copy in variants/<n>/ still finds them."""
    if isinstance(data, dict):
        return {k: (str(base / v) if k in PATH_KEYS and isinstance(v, str) and _relative_file(v, base)
                    else absolute_paths(v, base)) for k, v in data.items()}
    if isinstance(data, list):
        return [absolute_paths(v, base) for v in data]
    return data


def _relative_file(v: str, base: Path) -> bool:
    return bool(v) and ":" not in v.split("/")[0] and not Path(v).is_absolute() and (base / v).exists()


def _domains(r: Any) -> set[str]:
    from agentic_sim_world_gen.assets.domain import domains_of

    return set(domains_of(" | ".join([r.semantic_class or "", r.caption or "", *r.tags])))


def _good(c: dict[str, Any], o: Any, place: str | None, chosen: Any = None) -> bool:
    """A candidate fit to draw: not a substitute, not made for another line of work, about the kind's size and
    with no picture for another place, and of no line of work (medical, automotive, ...) the chosen one is not of. Checked again here (not only the flags saved with the candidates):
    an older scene has no off_place / off_size on its candidates."""
    from agentic_sim_world_gen import service
    from agentic_sim_world_gen.assets.domain import off_place
    from agentic_sim_world_gen.assets.sizes import kind_of, off_size

    m = c.get("match") or {}
    if m.get("weak") or m.get("off_place") or m.get("off_size"):
        return False
    r = record_from_dict(c)
    if chosen is not None and not _domains(r) <= _domains(chosen):
        return False  # made for a line of work the chosen one is not (a hospital chair for an office chair)
    kind = kind_of(o.query, o.alt_queries) if o.min_size is None and o.max_size is None else None
    return (off_place(r, place, [o.query, *o.alt_queries]) is None and off_size(r, kind) is None
            and not service._wrong_picture(r, o, place))


def _score(c: dict[str, Any]) -> float:
    return float((c.get("match") or {}).get("score") or 0.0)


def _name(c: dict[str, Any]) -> str:
    return record_from_dict(c).name


def _label(s: ResolvedScene, oid: str) -> str:
    o = s.obj(oid)
    return o.label or oid


def vary(s: ResolvedScene, rng: random.Random, kinds: tuple[str, ...] | list[str] = KINDS,
         size_jitter: float = 0.1) -> list[dict[str, Any]]:
    """Change the scene in place; returns [{kind, object?, from, to, text}]."""
    bad = set(kinds) - set(KINDS)
    if bad:
        raise ValueError(f"vary: unknown {sorted(bad)}; one of {KINDS}")
    out: list[dict[str, Any]] = []
    room = s.spec.room
    place = place_of(s)
    if "size" in kinds and size_jitter > 0 and room.plan is None and not room.outdoor and room.width and room.depth:
        w = round(room.width * rng.uniform(1 - size_jitter, 1 + size_jitter) / 0.05) * 0.05
        d = round(room.depth * rng.uniform(1 - size_jitter, 1 + size_jitter) / 0.05) * 0.05
        if (round(w, 2), round(d, 2)) != (room.width, room.depth):
            out.append({"kind": "size", "from": [room.width, room.depth], "to": [round(w, 2), round(d, 2)],
                        "text": f"部屋 {room.width:g}×{room.depth:g} → {w:.2f}×{d:.2f} m"})
            room.width, room.depth = round(w, 2), round(d, 2)
    if "materials" in kinds and not room.outdoor:
        for target, table, ja in (("floor", FLOORS, "床"), ("walls", WALLS, "壁")):
            cur = getattr(room, target)
            if not isinstance(cur, str):
                continue  # a surface of its own (a texture, an MDL found by words, an accent wall): kept
            pool = [x for x in _pool(table, place) if x != cur]
            if pool:
                new = rng.choice(pool)
                out.append({"kind": "materials", "target": target, "from": cur, "to": new, "text": f"{ja} {cur} → {new}"})
                setattr(room, target, new)
    if "assets" in kinds:
        for p in s.placements:
            if p.substitute or not p.candidates:
                continue
            o = s.obj(p.object_id)
            # no lower in the search's match than the one chosen: the agent chose it looking at the pictures, and
            # a lower match is often another thing that only shares a tag ("stand": a TV stand, an axle stand)
            floor = _score(p.candidates[p.selected]) if p.selected < len(p.candidates) else 0.0
            good = [i for i, c in enumerate(p.candidates[:TOP])
                    if i == p.selected or (_score(c) >= floor - 1e-9 and _good(c, o, place, p.asset))]
            if len(good) < 2:
                continue
            i = rng.choice(good)
            if i != p.selected:
                a, b = _name(p.candidates[p.selected]), _name(p.candidates[i])
                out.append({"kind": "assets", "object": p.object_id, "from": a, "to": b,
                            "text": f"{_label(s, p.object_id)}：{a} → {b}"})
                p.selected = i
    if "layout" in kinds:
        for o in s.spec.objects:
            lo = o.layout
            if (lo is None or not lo.against_wall or o.opening or o.decor
                    or lo.in_front_of or lo.next_to or lo.on):
                continue
            was = lo.along
            new = rng.uniform(*ALONG_RANGE) if was is None else min(ALONG_RANGE[1], max(
                ALONG_RANGE[0], was + rng.uniform(-ALONG_STEP, ALONG_STEP)))
            lo.along = round(new, 2)
            wall = WALL_JA.get(str(lo.against_wall), str(lo.against_wall))
            out.append({"kind": "layout", "object": o.id, "from": was, "to": lo.along,
                        "text": f"{_label(s, o.id)}：{wall}の {'自動' if was is None else f'{was:.2f}'} → {lo.along:.2f}"})
    return out


KIND_JA = {"assets": "アセット", "layout": "配置", "materials": "床・壁", "size": "寸法"}


def write_index(out: Path, source: ResolvedScene, rows: list[dict[str, Any]], base_issues: int, seed: int,
                kinds: tuple[str, ...]) -> None:
    """variants/index.html (each variant's layout.svg with what changed) and summary.json."""
    import html
    import json

    (out / "summary.json").write_text(json.dumps({"source": source.spec.name, "seed": seed, "vary": list(kinds),
                                                  "source_issues": base_issues, "variants": rows},
                                                 ensure_ascii=False, indent=1), encoding="utf-8")
    cards = []
    for r in rows:
        n = r["name"]
        flag = (f'<span class="bad">問題 {r["issues"]}（元 {base_issues}）</span>' if r.get("more_issues")
                else f'<span class="ok">問題 {r["issues"]}</span>')
        items = "".join(f"<li><b>{KIND_JA[x['kind']]}</b> {html.escape(x['text'])}</li>" for x in r["changes"]) \
            or "<li>変更なし</li>"
        err = f'<p class="bad">USD の書き出しに失敗：{html.escape(r["compose_error"])}</p>' if "compose_error" in r else ""
        cards.append(f'<section><h2>{n} {flag}</h2><a href="{n}/preview.html"><img src="{n}/layout.svg" '
                     f'alt="バリエーション {n} の上面図"></a><ul>{items}</ul>{err}</section>')
    page = f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>バリエーション一覧</title>
<style>
:root {{ --bg: #fafaf8; --fg: #222; --muted: #666; --card: #fff; --line: #ddd; --ok: #1a7f37; --bad: #c62828; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg: #161616; --fg: #eee; --muted: #aaa; --card: #222; --line: #333;
  --ok: #5cc17a; --bad: #ef6b6b; }} }}
body {{ margin: 0; padding: 16px; background: var(--bg); color: var(--fg); font: 14px/1.5 system-ui, sans-serif; }}
p.meta {{ color: var(--muted); }}
main {{ display: grid; gap: 16px; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); }}
section {{ background: var(--card); border: 1px solid var(--line); border-radius: 8px; padding: 12px; }}
h2 {{ font-size: 16px; margin: 0 0 8px; display: flex; justify-content: space-between; }}
img {{ width: 100%; background: #fff; border-radius: 4px; }}
ul {{ padding-left: 18px; margin: 8px 0 0; }}
.ok {{ color: var(--ok); font-weight: normal; }} .bad {{ color: var(--bad); font-weight: normal; }}
</style></head><body>
<h1>{html.escape(source.spec.name)} のバリエーション</h1>
<p class="meta">{len(rows)} 件・seed {seed}・変えたもの：{"、".join(KIND_JA[k] for k in kinds)}・元のシーンの問題 {base_issues} 件。
図をクリックするとそのシーンのプレビュー。</p>
<main>{"".join(cards)}</main>
</body></html>
"""
    (out / "index.html").write_text(page, encoding="utf-8")
