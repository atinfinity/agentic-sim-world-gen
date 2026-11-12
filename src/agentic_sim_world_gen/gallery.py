"""Examples and past scenes as a gallery to start from: a vague request has no picture to begin with, and a
place without its own question table (questions.GENERIC) gets thin questions and defaults. The page shows the
top views by place; the one the user picks becomes the first draft, and what it brought along (size, fixtures,
finishes) is recorded as guesses with the basis 「作例「…」から」 so each can be corrected on the preview.

Entries: `example:<file stem>` (examples/*.json of the repository; top views drawn ahead by
scripts/make_gallery.py into data/gallery/) and `scene:<folder>` (scenes under AGENTIC_SIM_WORLD_GEN_OUT, their layout.svg).
"""

from __future__ import annotations

import json
import os
import shutil
import time
import unicodedata
from html import escape
from pathlib import Path
from typing import Any

from agentic_sim_world_gen.scene.spec import STORE_PLACES, SceneSpec

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"  # the repository (an editable install)
THUMBS = Path(__file__).parent / "data" / "gallery"
PAGE = "gallery.html"
PICK = "gallery_pick.json"
ASSETS = "_gallery"  # under the served root: the examples' top views
MAX_SCENES = 40  # the newest past scenes shown
NEAR = 6  # entries returned to the agent
FIXTURES = 8  # fixture names per entry

PLACE_JA = {"home": "住まい", "office": "オフィス", "shop": "店", "convenience_store": "コンビニ", "supermarket": "スーパー",
            "drugstore": "ドラッグストア", "home_center": "ホームセンター", "hundred_yen_shop": "100 円ショップ",
            "bookstore": "書店", "electronics_store": "家電量販店", "apparel_store": "衣料品店", "bakery": "パン屋",
            "cafe": "カフェ", "restaurant": "飲食店", "factory": "工場", "plant_factory": "植物工場", "warehouse": "倉庫",
            "datacenter": "データセンター", "lab": "実験室", "hospital": "病院", "school": "学校", "station": "駅",
            "public": "公共施設", "outdoor": "屋外", "other": "その他"}
# places that share fixtures: an example of the same family is the next best start
FAMILIES = (("shop", *STORE_PLACES), ("cafe", "restaurant"), ("office", "lab", "school", "hospital", "public", "station"),
            ("warehouse", "factory", "plant_factory", "datacenter"), ("home",), ("outdoor",))


def examples_dir() -> Path:
    return Path(os.environ.get("AGENTIC_SIM_WORLD_GEN_EXAMPLES") or EXAMPLES).expanduser()


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").lower()


def _family(place: str | None) -> tuple[str, ...]:
    return next((f for f in FAMILIES if place in f), ())


def _fixtures(spec: SceneSpec) -> list[str]:
    """Names of what stands on the floor (not the goods on shelves or things on tables), in the spec's order."""
    out: list[str] = []
    for o in spec.objects:
        if o.fill is not None or (o.layout is not None and getattr(o.layout, "on", None)):
            continue
        name = o.label or o.query
        if o.array is not None and o.array.count * o.array.rows > 1:
            name += f" ×{o.array.count * o.array.rows}"
        if name not in out:
            out.append(name)
    return out


def _size(spec: SceneSpec) -> tuple[float, float, float]:
    room = spec.room
    if room.width and room.depth:
        return round(room.width, 2), round(room.depth, 2), room.height
    from agentic_sim_world_gen.layout.room import RoomGeometry

    x0, y0, x1, y1 = RoomGeometry(room).bounds
    return round(x1 - x0, 2), round(y1 - y0, 2), room.height


def _words(v: Any) -> str | None:
    return v if isinstance(v, str) else None


def _entry(id_: str, spec: SceneSpec, thumb: str | None, when: float | None = None) -> dict[str, Any]:
    room = spec.room
    fixtures = _fixtures(spec)
    return {"id": id_, "kind": id_.split(":", 1)[0], "title": room.name if room.name != "room" else spec.name,
            "place": room.place or ("outdoor" if getattr(room, "outdoor", False) else "other"), "size": list(_size(spec)),
            "objects": len(spec.objects), "fixtures": fixtures[:FIXTURES], "more_fixtures": max(0, len(fixtures) - FIXTURES),
            "look": {k: v for k in ("floor", "walls", "lighting") if (v := _words(getattr(room, k)))},
            "thumb": thumb, "when": when}


def load_spec(id_: str, out: str | Path | None = None, examples: str | Path | None = None) -> SceneSpec:
    """The SceneSpec behind an entry id (`example:<stem>` / `scene:<folder>`; a bare name is tried as both)."""
    kind, _, name = id_.partition(":")
    if not name:
        kind, name = "", id_
    if not name or Path(name).name != name:
        raise ValueError(f"not a gallery id: {id_!r}")
    if kind in ("", "example"):
        f = Path(examples or examples_dir()) / f"{name}.json"
        if f.is_file():
            return SceneSpec.model_validate(json.loads(f.read_text(encoding="utf-8")))
    if kind in ("", "scene") and out:
        f = Path(out).expanduser() / name / "scene.resolved.json"
        if f.is_file():
            return SceneSpec.model_validate(json.loads(f.read_text(encoding="utf-8"))["spec"])
    raise ValueError(f"no such example or scene: {id_!r} (show_gallery lists the ids)")


def entries(out: str | Path | None = None, examples: str | Path | None = None) -> list[dict[str, Any]]:
    """Every example and the newest past scenes. A file that does not load is left out (never an error here)."""
    rows: list[dict[str, Any]] = []
    ex = Path(examples or examples_dir())
    for f in sorted(ex.glob("*.json")) if ex.is_dir() else []:
        try:
            spec = SceneSpec.model_validate(json.loads(f.read_text(encoding="utf-8")))
        except (ValueError, OSError):
            continue
        thumb = f"{ASSETS}/{f.stem}.svg" if (THUMBS / f"{f.stem}.svg").is_file() else None
        rows.append(_entry(f"example:{f.stem}", spec, thumb))
    root = Path(out).expanduser() if out else None
    if root and root.is_dir():
        found = sorted((p for p in root.glob("*/scene.resolved.json") if not p.parent.name.startswith(("_", "."))),
                       key=lambda p: -p.stat().st_mtime)[:MAX_SCENES]
        for f in found:
            try:
                spec = SceneSpec.model_validate(json.loads(f.read_text(encoding="utf-8"))["spec"])
            except (ValueError, OSError, KeyError):
                continue
            thumb = f"{f.parent.name}/layout.svg" if (f.parent / "layout.svg").is_file() else None
            rows.append(_entry(f"scene:{f.parent.name}", spec, thumb, f.stat().st_mtime))
    return rows


def rank(rows: list[dict[str, Any]], place: str | None = None, request: str = "") -> list[dict[str, Any]]:
    """Nearest first: the same place, then the same family of places, then words of the request found in the
    title or the fixtures; a past scene before an example on a tie. Each row gets `near` (why, or None)."""
    text = _norm(request)
    family = _family(place)

    def score(r: dict[str, Any]) -> tuple[int, str | None]:
        n, why = 0, None
        if place and r["place"] == place:
            n, why = 4, "同じ種類の場所"
        elif place and r["place"] in family:
            n, why = 2, "近い種類の場所"
        names = [_norm(x).split(" ×")[0] for x in [r["title"], *r["fixtures"]]]
        hit = [w for w in names if len(w) >= 2 and w in text]
        if hit:
            n, why = n + 1, (why + "、" if why else "") + f"依頼の「{hit[0]}」"
        return n, why

    scored = [(score(r), r) for r in rows]
    scored.sort(key=lambda x: (-x[0][0], x[1]["kind"] != "scene", -(x[1]["when"] or 0), x[1]["id"]))
    return [{**r, "near": why} for (n, why), r in scored]


def inherited(spec: SceneSpec, title: str, changed: set[str] | None = None) -> list[dict[str, Any]]:
    """What a scene started from an example took over from it, as assumption rows (guesses, basis 作例「…」から):
    the size, the kind of place, floor / walls / lighting and the fixtures. `changed`: room fields the caller
    overrode (their rows are left to the caller / the automatic ones)."""
    changed = changed or set()
    basis = f"作例「{title}」から"
    w, d, h = _size(spec)
    room = spec.room
    rows = []
    if not changed & {"width", "depth", "height", "plan"}:
        rows.append({"item": "size", "value": f"{w:g} × {d:g} m、天井 {h:g} m"})
    if "place" not in changed and room.place:
        rows.append({"item": "place", "value": PLACE_JA.get(room.place, room.place)})
    for k in ("floor", "walls", "lighting"):
        # only what the example itself says (a field it leaves out is the default, not its choice)
        if k not in changed and k in room.model_fields_set and (v := _words(getattr(room, k))):
            rows.append({"item": k, "value": v})
    fixtures = _fixtures(spec)
    if fixtures:
        more = f" ほか {len(fixtures) - FIXTURES} 種" if len(fixtures) > FIXTURES else ""
        rows.append({"item": "什器", "value": "、".join(fixtures[:FIXTURES]) + more})
    return [{**r, "given": False, "basis": basis} for r in rows]


def picked(out: str | Path) -> dict[str, Any] | None:
    """What the user clicked on the page (None: nothing yet)."""
    try:
        return json.loads((Path(out).expanduser() / PICK).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def pick(out: str | Path, id_: str) -> dict[str, Any]:
    """Record the user's click (the preview server calls this). The id must be one of the entries."""
    root = Path(out).expanduser()
    if id_ not in {r["id"] for r in entries(root)}:
        raise ValueError(f"no such entry: {id_!r}")
    got = {"id": id_, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    (root / PICK).write_text(json.dumps(got, ensure_ascii=False), encoding="utf-8")
    return got


def clear_pick(out: str | Path) -> None:
    (Path(out).expanduser() / PICK).unlink(missing_ok=True)


CSS = """
:root{--bg:#f6f6f4;--fg:#1d1d1b;--mut:#6b6b66;--card:#fff;--line:#deded8;--acc:#1f6feb;--on:#e7f0ff}
@media (prefers-color-scheme: dark){:root{--bg:#18181a;--fg:#ececea;--mut:#a0a09a;--card:#232326;--line:#3a3a3e;--acc:#6ea8ff;--on:#1d2c45}}
*{box-sizing:border-box}body{margin:0;padding:16px;background:var(--bg);color:var(--fg);font:15px/1.6 system-ui,"Hiragino Sans","Noto Sans JP",sans-serif}
h1{font-size:20px;margin:0 0 4px}h2{font-size:15px;margin:22px 0 8px;color:var(--mut);font-weight:600}
p.lead{margin:0 0 4px;color:var(--mut)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px;display:flex;flex-direction:column;gap:6px}
.card.on{border-color:var(--acc);background:var(--on)}
.thumb{background:#fff;border-radius:6px;aspect-ratio:4/3;display:flex;align-items:center;justify-content:center;overflow:hidden;color:#888;font-size:13px}
.thumb img{width:100%;height:100%;object-fit:contain}
.t{font-weight:600}.m{color:var(--mut);font-size:13px}.near{font-size:12px;color:var(--acc)}
button{font:inherit;padding:6px 10px;border-radius:8px;border:1px solid var(--acc);background:var(--acc);color:#fff;cursor:pointer;margin-top:auto}
.card.on button{background:transparent;color:var(--acc)}
#msg{position:sticky;top:0;z-index:2;margin:8px 0;padding:8px 12px;border-radius:8px;background:var(--on);border:1px solid var(--acc);display:none}
code{background:var(--line);padding:1px 5px;border-radius:4px}
"""

JS = """
const token = document.querySelector("meta[name='wg-token']")?.content;
const msg = document.getElementById("msg");
function mark(id){document.querySelectorAll(".card").forEach(c=>{const on=c.dataset.id===id;c.classList.toggle("on",on);
  c.querySelector("button").textContent=on?"選択中":"これに近い";});}
function say(id,title,sent){msg.style.display="block";
  msg.innerHTML=(sent?"「"+title+"」を選びました。チャットに戻って「これで」と伝えてください。":"チャットで <code>"+id+"</code> に近い、と伝えてください。");}
document.querySelectorAll(".card button").forEach(b=>b.addEventListener("click",async()=>{
  const c=b.closest(".card"),id=c.dataset.id,title=c.dataset.title;let sent=false;
  if(token){try{const r=await fetch("api/gallery/pick",{method:"POST",headers:{"Content-Type":"application/json","X-Agentic-Sim-World-Gen-Token":token},
    body:JSON.stringify({id})});sent=r.ok;}catch(e){}}
  mark(id);say(id,title,sent);}));
const first=document.body.dataset.picked;if(first)mark(first);
"""


def _card(r: dict[str, Any]) -> str:
    w, d, h = r["size"]
    thumb = (f"<img src='{escape(r['thumb'], quote=True)}' alt='' loading='lazy'>" if r["thumb"] else "上面図なし")
    fixtures = "、".join(escape(x) for x in r["fixtures"]) + (f" ほか {r['more_fixtures']} 種" if r["more_fixtures"] else "")
    kind = "作例" if r["kind"] == "example" else "過去のシーン"
    near = f"<div class='near'>{escape(r['near'])}</div>" if r.get("near") else ""
    return (f"<div class='card' data-id='{escape(r['id'], quote=True)}' data-title='{escape(r['title'], quote=True)}'>"
            f"<div class='thumb'>{thumb}</div><div class='t'>{escape(r['title'])}</div>{near}"
            f"<div class='m'>{kind}・{escape(PLACE_JA.get(r['place'], r['place']))}・{w:g} × {d:g} m・{r['objects']} 物体</div>"
            f"<div class='m'>{fixtures}</div><button type='button'>これに近い</button></div>")


def write_page(out: str | Path, rows: list[dict[str, Any]], place: str | None = None) -> Path:
    """gallery.html under `out` (the preview server's root): the near entries first, then the rest by place."""
    root = Path(out).expanduser()
    (root / ASSETS).mkdir(parents=True, exist_ok=True)
    for r in rows:
        if r["kind"] == "example" and r["thumb"]:
            src, dst = THUMBS / Path(r["thumb"]).name, root / r["thumb"]
            if not dst.is_file() or dst.stat().st_mtime < src.stat().st_mtime:
                shutil.copyfile(src, dst)
    near = [r for r in rows if r.get("near")]
    rest = [r for r in rows if not r.get("near")]
    parts = []
    if near:
        parts.append("<h2>近い作例</h2><div class='grid'>" + "".join(_card(r) for r in near) + "</div>")
    by_place: dict[str, list[dict[str, Any]]] = {}
    for r in rest:
        by_place.setdefault(r["place"], []).append(r)
    for p, group in by_place.items():
        parts.append(f"<h2>{escape(PLACE_JA.get(p, p))}</h2><div class='grid'>" + "".join(_card(r) for r in group) + "</div>")
    got = picked(root)
    html = ("<!doctype html><html lang='ja'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>作例ギャラリー</title><style>{CSS}</style></head><body data-picked='{escape((got or {}).get('id', ''), quote=True)}'>"
            "<h1>作例ギャラリー</h1><p class='lead'>近い物を選ぶと、それを叩き台にして始めます。寸法・什器・仕上げは引き継いだ値として"
            "プレビューの「仮定」に出るので、あとから直せます。</p><div id='msg'></div>"
            + ("".join(parts) or "<p>作例がありません。</p>") + f"<script>{JS}</script></body></html>")
    f = root / PAGE
    f.write_text(html, encoding="utf-8")
    return f
