"""Preview (1): candidate assets per object as a static HTML page.

Thumbnails are loaded straight from Hugging Face, so the page needs internet
access when opened. The selected candidate is highlighted; change `selected`
in scene.resolved.json to swap.
"""

from __future__ import annotations

from html import escape
from pathlib import Path

from agentic_sim_world_gen.layout.validate import check
from agentic_sim_world_gen.scene.spec import ResolvedScene, record_from_dict

_CSS = """
:root { --bg:#fff; --fg:#1d1d1f; --muted:#6e6e73; --card:#f5f5f7; --accent:#76b900; --bad:#d70015; }
@media (prefers-color-scheme: dark) { :root { --bg:#161617; --fg:#f5f5f7; --muted:#a1a1a6; --card:#232325; } }
body { background:var(--bg); color:var(--fg); font:14px/1.5 -apple-system,"Hiragino Sans","Noto Sans JP",sans-serif; margin:0; padding:16px; }
h1 { font-size:20px; margin:0 0 4px; } h2 { font-size:16px; margin:24px 0 8px; }
.muted { color:var(--muted); font-size:12px; }
.row { display:flex; gap:12px; overflow-x:auto; padding-bottom:6px; }
.card { flex:0 0 180px; background:var(--card); border-radius:10px; padding:8px; border:2px solid transparent; }
.card.sel { border-color:var(--accent); }
.card img { width:164px; height:164px; object-fit:contain; background:#fff; border-radius:6px; }
.name { font-weight:600; font-size:12px; word-break:break-all; }
.issues { color:var(--bad); }
"""


def render_html(scene: ResolvedScene) -> str:
    issues = check(scene)
    parts = [
        "<!doctype html><html lang='ja'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        f"<title>候補アセット — {escape(scene.spec.name)}</title><style>{_CSS}</style></head><body>",
        f"<h1>候補アセット：{escape(scene.spec.name)}</h1>",
        "<div class='muted'>緑枠が現在の選択。差し替えは scene.resolved.json の <code>selected</code> を変更。</div>",
    ]
    if issues:
        parts.append("<ul class='issues'>" + "".join(f"<li>{escape(str(i))}</li>" for i in issues) + "</ul>")
    for p in scene.placements:
        o = scene.obj(p.object_id)
        parts.append(f"<h2>{escape(o.label or o.id)} <span class='muted'>query: “{escape(o.query)}”</span></h2>")
        if not p.candidates:
            parts.append("<div class='issues'>候補なし（代用またはクエリ変更が必要）</div>")
            continue
        if p.substitute:
            parts.append("<div class='issues'>該当なし：どの候補も別物（代用）。近いものを選ぶか、"
                         "<code>local:</code> フォルダでアセットを追加</div>")
        parts.append("<div class='row'>")
        for i, c in enumerate(p.candidates):
            r = record_from_dict(c)
            w, d, h = r.size_m
            img = f"<img loading='lazy' src='{escape(r.thumbnail_url)}' alt=''>" if r.thumbnail_url else ""
            parts.append(
                f"<div class='card{' sel' if i == p.selected else ''}'>{img}"
                f"<div class='name'>[{i}] {escape(r.name)}</div>"
                f"<div class='muted'>{escape(r.caption or r.semantic_class or '')}</div>"
                f"<div class='muted'>{w:.2f}×{d:.2f}×{h:.2f} m"
                f"{f' · {r.mass_kg:g} kg' if r.mass_kg else ''}</div>"
                f"<div class='muted'>{escape(r.license or 'license ?')} · {escape(r.author or r.source)}</div>"
                + "".join(f"<div class='muted'>バリアント {escape(k)}: {escape(' / '.join(v))}</div>"
                          for k, v in r.variant_sets.items())
                + "</div>"
            )
        parts.append("</div>")
    parts.append("</body></html>")
    return "".join(parts)


def write_html(scene: ResolvedScene, path: str | Path) -> Path:
    path = Path(path)
    path.write_text(render_html(scene), encoding="utf-8")
    return path
