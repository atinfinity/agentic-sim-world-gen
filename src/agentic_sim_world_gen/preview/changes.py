"""What changed in the scene between two preview writes, in Japanese for the change history.

`snapshot()` keeps the few things a person cares about (room, each object's asset, place, facing, units,
variants, the number of layout issues) small enough to live in preview.json; `diff()` turns two snapshots
into lines like 「椅子：位置 (1.20, 0.50) → (1.40, 0.50)」. Objects moved by the solver as a side effect
(things placed relative to the one that was edited) show up too, which is the point.
"""

from __future__ import annotations

import re
from typing import Any

from agentic_sim_world_gen.scene.spec import ResolvedScene

MOVE_M = 0.01  # smaller moves are solver noise
TURN_DEG = 0.5
MAX_LINES = 12  # per history entry; the rest becomes 「ほか N 件」

# preset names as a person reads them (unknown names are shown as they are)
PRESETS_JA = {
    "wood_light": "明るい木目", "wood_dark": "暗い木目", "tatami": "畳", "carpet_gray": "灰色のカーペット",
    "tile_white": "白いタイル", "concrete": "コンクリート", "epoxy_gray": "灰色の塗り床", "raised_floor": "フリーアクセス床",
    "paint_white": "白い塗装", "paint_beige": "ベージュの塗装", "drywall_gray": "灰色の壁", "brick": "レンガ",
    "metal_panel": "金属パネル", "grass": "芝生", "asphalt": "アスファルト", "paving": "舗装", "crosswalk": "横断歩道",
    "gravel": "砂利", "soil": "土", "water": "水面", "glass": "ガラス",
    "ceiling_board": "白い石膏ボード", "ceiling_tile": "システム天井", "metal_deck": "折板（鉄骨むき出し）",
    "trim_white": "白い巾木", "trim_wood": "木の巾木",
    "studio": "スタジオ", "daylight": "昼光", "home": "家庭の照明", "office": "オフィス", "warehouse": "倉庫",
    "night": "夜", "sun": "晴れ", "overcast": "曇り", "dusk": "夕方", "street_night": "夜の街灯",
}
# set_room's fields in the step line
ROOM_FIELDS_JA = {
    "width": "幅", "depth": "奥行き", "height": "天井高", "wall_thickness": "壁の厚さ", "floor": "床", "walls": "壁",
    "lighting": "照明", "lighting_scale": "明るさ", "ceiling": "天井", "openings": "開口", "plan": "間取り",
    "plan_file": "間取り図", "sky": "空", "outdoor": "屋外", "ground_margin": "地面の広さ", "areas": "エリア",
    "buildings": "建物", "place": "場所の種類", "wainscot": "腰壁", "baseboard": "巾木", "zone_floors": "ゾーンの床",
    "density": "生活感", "site": "敷地", "goods": "商品", "goods_words": "商品の語",
    "light_kelvin": "色温度", "fixtures": "照明器具", "ceiling_features": "梁・下がり壁",
    "levels": "段差", "stairs": "階段", "columns": "柱", "floor_areas": "床の一部",
}


def ja(name: str | None) -> str:
    return PRESETS_JA.get(name or "", name or "なし")


def _surface(v: Any) -> str:
    if isinstance(v, str):
        return ja(v)
    d = v.model_dump(exclude_none=True) if hasattr(v, "model_dump") else dict(v or {})
    base = ja(d.pop("preset", None)) if d.get("preset") else "カスタム"
    d.pop("tile_m", None)
    if d.get("mdl"):  # a bundled MDL material (agentic_sim_world_gen.materials)
        return f"MDL {d.get('mdl_name') or d['mdl'].rsplit('/', 1)[-1]}"
    return base + ("（色・質感を指定）" if d else "")


def _walls(r: Any) -> str:
    """The default wall surface, then each accent wall: 「白い塗装（北 レンガ）」."""
    from agentic_sim_world_gen.scene.walls import WALL_JA, default_value, overrides

    rect = getattr(r, "plan", None) is None
    own = overrides(r.walls, rect)
    base = _surface(default_value(r.walls) or "paint_white")
    if not own:
        return base
    return base + "（" + "、".join(f"{WALL_JA.get(k, k) if rect else k} {_surface(v)}" for k, v in own.items()) + "）"


def _trim(t: Any) -> str | None:
    if t is None:
        return None
    out = f"{_surface(t.surface)} {t.height:g} m"
    if getattr(t, "color", None) is not None:
        out += "（色を指定）"
    return out + (f"（{'・'.join(t.walls)}）" if t.walls else "")


def _ceiling(r: Any) -> str | None:
    from agentic_sim_world_gen.scene.looks import ceiling_surface

    s = ceiling_surface(r.ceiling, r.lighting, r.place) if not r.outdoor else None
    if s is None:
        return None
    return _surface(r.ceiling) if r.ceiling is not True else ja(s["name"])


def _zone_floors(r: Any) -> dict[str, str]:
    """zone name -> its own floor (only zones that differ from the room floor)."""
    from agentic_sim_world_gen.scene.zone_floors import zone_floors

    return {z["name"]: _surface(z["value"]) for z in zone_floors(r)}


OPENING_JA = {"door": "ドア", "window": "窓", "opening": "開口"}
STYLE_JA = {"open": "開き戸", "auto_sliding": "自動ドア"}


def _openings(r: Any) -> list[dict[str, Any]]:
    """Each door / window / opening with its resolved sill and height (defaults filled in)."""
    out = []
    for i, o in enumerate(getattr(r, "openings", None) or []):
        sill, top = o.resolved(r.height)
        out.append({"id": o.id or f"{o.kind}_{i}", "kind": o.kind, "wall": o.wall, "offset": round(o.offset, 3),
                    "width": round(o.width, 3), "height": round(top - sill, 3), "sill": round(sill, 3),
                    "style": o.style})
    return out


FIXTURE_JA = {"downlight": "ダウンライト", "pendant": "ペンダント", "line": "ライン照明", "panel": "パネル照明"}
FEATURE_JA = {"beam": "梁", "soffit": "下がり壁"}


def _fixtures(r: Any) -> str | None:
    f = getattr(r, "fixtures", None)
    if f is None:
        return None
    from agentic_sim_world_gen.scene.fixtures import KINDS

    spacing = f.spacing or KINDS[f.kind]["spacing"]
    return (f"{FIXTURE_JA.get(f.kind, f.kind)} {spacing:g} m 間隔" + (f"・{f.color}" if f.color else "")
            + (f"・上向き {f.uplight:.0%}" if getattr(f, "uplight", 0) else ""))


def _ceiling_features(r: Any) -> str | None:
    fs = getattr(r, "ceiling_features", None) or []
    if not fs:
        return None
    n: dict[str, int] = {}
    for f in fs:
        n[f.kind] = n.get(f.kind, 0) + 1
    return "・".join(f"{FEATURE_JA.get(k, k)} {v}" for k, v in n.items())


def _levels(r: Any) -> str | None:
    from agentic_sim_world_gen.scene.levels import describe

    return describe(r) if getattr(r, "levels", None) or getattr(r, "stairs", None) else None


def _columns(r: Any) -> dict[str, str | None]:
    from agentic_sim_world_gen.scene.columns import describe

    return describe(r) if not r.outdoor else {"columns": None, "floor_areas": None}


def snapshot(scene: ResolvedScene, issues: list[dict[str, Any]]) -> dict[str, Any]:
    r = scene.spec.room
    sel = {p.object_id: p for p in scene.placements}
    objs = {}
    for o in scene.spec.objects:
        p = sel.get(o.id)
        c = p.candidates[p.selected] if p and p.candidates else None
        a = o.array
        objs[o.id] = {
            "label": o.label or o.id,
            "asset": (c.get("name") or c.get("id")) if c else None,
            "pos": [round(v, 3) for v in o.position] if o.position else None,
            "z": round(o.z, 3),
            "yaw": round(o.yaw_deg, 1),
            "units": a.count * a.rows if a else 1,
            "variants": dict(o.variants or {}),
            "fill": o.fill.on if o.fill else None,
            "screen": _screen(o.screen),
            "tint": "、".join(t.color + (f"（{'・'.join(t.materials)}）" if t.materials else "") for t in o.tint),
        }
    return {
        "room": {"size": [r.width, r.depth, r.height], "floor": _surface(r.floor), "walls": _walls(r),
                 "wainscot": _trim(getattr(r, "wainscot", None)), "baseboard": _trim(getattr(r, "baseboard", None)),
                 "ceiling": _ceiling(r), "lighting": ja(r.lighting) + (f" {r.light_kelvin:g} K" if getattr(r, "light_kelvin", None) else ""), "openings": _openings(r),
                 "place": getattr(r, "place", None), "zone_floors": _zone_floors(r),
                 "fixtures": _fixtures(r), "ceiling_features": _ceiling_features(r), "levels": _levels(r),
                 **_columns(r)},
        "objects": objs,
        "issues": len(issues),
        "assumptions": {f"{a.object or ''}:{a.item}": [a.value, a.given] for a in scene.spec.assumptions},
        "requests": _requests(scene),
    }


def _screen(sc) -> str | None:
    if sc is None:
        return None
    files = [sc.image] if isinstance(sc.image, str) else list(sc.image.values())
    return "、".join(dict.fromkeys(f.rsplit("/", 1)[-1] for f in files))


def _m(v: float | None) -> str:
    return "?" if v is None else f"{v:g}"


def _xy(p: list[float] | None) -> str:
    return "未設定" if not p else f"({p[0]:.2f}, {p[1]:.2f})"


def _wall(w: str) -> str:
    from agentic_sim_world_gen.scene.walls import WALL_JA

    return f"{WALL_JA[w]}の壁" if w in WALL_JA else f"壁 {w}"


def _style(o: dict[str, Any]) -> str:
    return "開口のみ" if o["kind"] == "opening" and o["style"] == "open" else STYLE_JA.get(o["style"], o["style"])


def _default_id(o: dict[str, Any]) -> bool:
    return re.fullmatch(rf"{o['kind']}_\d+", o["id"]) is not None


def _opening(a: dict[str, Any], b: dict[str, Any]) -> list[str]:
    parts = []
    if a["kind"] != b["kind"]:
        parts.append(f"種類 {OPENING_JA[a['kind']]} → {OPENING_JA[b['kind']]}")
    if a["wall"] != b["wall"] or abs(a["offset"] - b["offset"]) >= MOVE_M:
        parts.append(f"{_wall(a['wall'])} {a['offset']:g} m → {_wall(b['wall'])} {b['offset']:g} m")
    for key, name in (("width", "幅"), ("height", "高さ"), ("sill", "腰高")):
        if abs(a[key] - b[key]) >= MOVE_M:
            parts.append(f"{name} {a[key]:g} → {b[key]:g} m")
    if a["style"] != b["style"]:
        parts.append(f"{_style(a)} → {_style(b)}")
    return parts


def _opening_lines(old: Any, new: list[dict[str, Any]]) -> list[str]:
    """Added / removed / moved / resized openings, matched by id. Unmatched ones with default ids
    (``window_1``: set_room replaced the list and the index moved) are paired by kind, in order."""
    if not isinstance(old, list):  # a snapshot from from an older version kept only the count
        n = len(new)
        return [] if old is None or old == n else [f"開口の数：{old} → {n}"]
    oa = {o["id"]: o for o in old}
    ob = {o["id"]: o for o in new}
    pairs = [(oa[i], ob[i]) for i in ob if i in oa]
    gone = [o for o in old if o["id"] not in ob]
    added = [o for o in new if o["id"] not in oa]
    for b in list(added):
        a = next((a for a in gone if a["kind"] == b["kind"] and _default_id(a) and _default_id(b)), None)
        if a is not None:
            gone.remove(a)
            added.remove(b)
            pairs.append((a, b))

    def label(o: dict[str, Any]) -> str:  # 「窓」, or 「窓 w1」 when the id is given or there are several windows
        name = OPENING_JA.get(o["kind"], o["kind"])
        alone = all(sum(x["kind"] == o["kind"] for x in xs) <= 1 for xs in (old, new))
        return name if _default_id(o) and alone else f"{name} {o['id']}"

    def sp(o: dict[str, Any]) -> str:  # 「窓を追加」 but 「窓 w1 を追加」
        return label(o) + (" " if " " in label(o) else "")

    out = [f"{sp(b)}を追加（{_wall(b['wall'])}、幅 {b['width']:g} m）" for b in added]
    out += [f"{sp(a)}を削除（{_wall(a['wall'])}）" for a in gone]
    for a, b in pairs:
        if parts := _opening(a, b):
            out.append(f"{label(b)}：" + "、".join(parts))
    return out


def _object(a: dict[str, Any], b: dict[str, Any]) -> list[str]:
    parts = []
    if a["asset"] != b["asset"]:
        parts.append(f"アセット {a['asset'] or 'なし'} → {b['asset'] or 'なし'}")
    pa, pb = a["pos"], b["pos"]
    if (pa is None) != (pb is None) or (pa and pb and max(abs(pa[0] - pb[0]), abs(pa[1] - pb[1])) >= MOVE_M):
        parts.append(f"位置 {_xy(pa)} → {_xy(pb)}")
    if abs(a["z"] - b["z"]) >= MOVE_M:
        parts.append(f"高さ {a['z']:.2f} → {b['z']:.2f} m")
    turn = (b["yaw"] - a["yaw"] + 180) % 360 - 180
    if abs(turn) >= TURN_DEG:
        parts.append(f"向き {a['yaw']:g}° → {b['yaw']:g}°")
    if a["units"] != b["units"]:
        parts.append(f"台数 {a['units']} → {b['units']}")
    if a["variants"] != b["variants"]:
        v = "、".join(f"{k}={x}" for k, x in b["variants"].items()) or "なし"
        parts.append(f"バリアント → {v}")
    if a.get("screen") != b.get("screen"):
        parts.append(f"画面 → {b.get('screen') or 'アセットの画像'}")
    if a.get("tint", "") != b.get("tint", ""):
        parts.append(f"色 → {b.get('tint') or 'アセットの色'}")
    if a["fill"] != b["fill"]:
        parts.append(f"陳列先 {a['fill'] or 'なし'} → {b['fill'] or 'なし'}")
    return parts


def diff(old: dict[str, Any] | None, new: dict[str, Any]) -> list[str]:
    """Japanese lines, most important first; [] when nothing a person would notice changed."""
    if not old:
        return []
    out: list[str] = []
    ra, rb = old.get("room") or {}, new["room"]
    if ra.get("size") != rb["size"]:
        sa, sb = ra.get("size") or [None] * 3, rb["size"]
        out.append(f"部屋：{' × '.join(map(_m, sa))} → {' × '.join(map(_m, sb))} m")
    for key, ja in (("place", "場所の種類"), ("floor", "床"), ("walls", "壁"), ("wainscot", "腰壁"), ("baseboard", "巾木"),
                    ("ceiling", "天井"), ("lighting", "照明"), ("fixtures", "照明器具"),
                    ("ceiling_features", "梁・下がり壁"), ("levels", "段差・階段"), ("columns", "柱"),
                    ("floor_areas", "床の一部")):
        if key in ("ceiling", "wainscot", "baseboard", "fixtures", "ceiling_features", "levels", "columns",
                   "floor_areas") and key not in ra:  # a snapshot from before these existed
            continue
        if ra.get(key) != rb[key]:
            out.append(f"{ja}：{ra.get(key) or 'なし'} → {rb[key] or 'なし'}")
    out += _opening_lines(ra.get("openings"), rb.get("openings") or [])
    if "zone_floors" in ra:  # older snapshots have no zone floors
        za, zb = ra["zone_floors"], rb.get("zone_floors") or {}
        for name in dict.fromkeys([*za, *zb]):
            if za.get(name) != zb.get(name):
                out.append(f"床（{name}）：{za.get(name) or '部屋の床'} → {zb.get(name) or '部屋の床'}")
    oa, ob = old.get("objects") or {}, new["objects"]
    for i, b in ob.items():
        if i not in oa:
            out.append(f"{b['label']} を追加（{b['asset'] or 'アセットなし'}、位置 {_xy(b['pos'])}）")
    for i, a in oa.items():
        if i not in ob:
            out.append(f"{a['label']} を削除")
    for i, b in ob.items():
        if i in oa:
            parts = _object(oa[i], b)
            if parts:
                out.append(f"{b['label']}：" + "、".join(parts))
    aa, ab = old.get("assumptions") or {}, new.get("assumptions") or {}
    for k, (v, given) in ab.items():
        from agentic_sim_world_gen.preview.page import item_ja

        obj, item = k.split(":", 1)
        name = f"{ob[obj]['label']}：{item_ja(item)}" if obj in ob else item_ja(item)
        if k not in aa:
            continue  # first recorded: the whole list is in 「仮定」
        if aa[k] != [v, given]:
            what = "（指示どおりに）" if given and not aa[k][1] else ""
            out.append(f"仮定 {name}：{aa[k][0]} → {v}{what}")
    ra, rb = old.get("requests") or {}, new.get("requests") or {}
    for t, st in rb.items():
        if t in ra and ra[t] != st:
            from agentic_sim_world_gen.preview.page import REQ_BADGE

            out.append(f"指示「{t}」：{REQ_BADGE[ra[t]][1]} → {REQ_BADGE[st][1]}")
    if old.get("issues") != new["issues"]:
        out.append(f"配置の問題 {old.get('issues', 0)} → {new['issues']} 件")
    if len(out) > MAX_LINES:
        out = out[:MAX_LINES - 1] + [f"ほか {len(out) - MAX_LINES + 1} 件"]
    return out


def _requests(scene) -> dict:
    if not scene.spec.requirements:
        return {}
    from agentic_sim_world_gen.layout.request import evaluate

    try:
        return {r["text"]: r["status"] for r in evaluate(scene)}
    except Exception:
        return {}
