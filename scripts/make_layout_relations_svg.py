"""Draw the layout relations as small top views for the READMEs: docs/images/layout_relations{,_ja}.svg.

    python scripts/make_layout_relations_svg.py   # writes both (English, Japanese)
"""
import sys
from pathlib import Path

W, H = 300, 230  # one panel
COLS, ROWS = 3, 2
PAD = 16
TEXT = {
    "en": [("against_wall", "back to a wall"), ("in_front_of", "a chair in front of the desk, facing it"),
           ("next_to", "beside, same direction"), ("on", "on top (side view)"),
           ("facing", "turned towards another"), ("array", "rows of racks with aisles")],
    "ja": [("against_wall", "壁に背を付ける"), ("in_front_of", "机の前に椅子（机を向く）"),
           ("next_to", "横に同じ向きで"), ("on", "上に載せる（横から見た図）"),
           ("facing", "相手の方を向く"), ("array", "ラックを通路を挟んで並べる")],
}
LEGEND = {"en": "Top views. The triangle marks the front of each object.",
          "ja": "上から見た図。三角が物の正面。"}


def obj(x, y, w, h, label, front=None, cls="obj"):
    """A box centred at x,y with a front marker on side front ('n','s','e','w')."""
    s = f'<rect class="{cls}" x="{x - w/2}" y="{y - h/2}" width="{w}" height="{h}" rx="3"/>'
    if label:
        s += f'<text class="lbl" x="{x}" y="{y + 4}">{label}</text>'
    if front:
        dx, dy = {"n": (0, -1), "s": (0, 1), "e": (1, 0), "w": (-1, 0)}[front]
        ex, ey = x + dx * w / 2, y + dy * h / 2
        px, py = -dy, dx
        tip = (ex + dx * 9, ey + dy * 9)
        a = (ex + px * 6, ey + py * 6)
        b = (ex - px * 6, ey - py * 6)
        s += f'<polygon class="front" points="{a[0]},{a[1]} {b[0]},{b[1]} {tip[0]},{tip[1]}"/>'
    return s


def panels(lang):
    names = {"en": dict(shelf="shelf", desk="desk", chair="chair", side="side table", table="table",
                         lamp="lamp", sofa="sofa", rack="rack", aisle="aisle", wall="wall", floor="floor"),
             "ja": dict(shelf="本棚", desk="机", chair="椅子", side="サイド台", table="テーブル",
                         lamp="照明", sofa="ソファ", rack="ラック", aisle="通路", wall="壁", floor="床")}[lang]
    n = names
    cx, cy = W / 2, 105
    out = []
    # against_wall
    out.append(f'<line class="wall" x1="40" y1="45" x2="{W-40}" y2="45"/><text class="dim" x="{W-40}" y="38" text-anchor="end">{n["wall"]}</text>'
               + obj(cx, 45 + 6 + 22, 150, 44, n["shelf"], "s"))
    # in_front_of
    out.append(obj(cx, 75, 150, 56, n["desk"], "s") + obj(cx, 150, 52, 46, n["chair"], "n", "obj2"))
    # next_to
    out.append(obj(cx - 40, 100, 130, 60, n["desk"], "s") + obj(cx + 75, 100, 80, 60, n["side"], "s", "obj2"))
    # on (side view)
    out.append(f'<line class="wall" x1="40" y1="165" x2="{W-40}" y2="165"/><text class="dim" x="{W-40}" y="160" text-anchor="end">{n["floor"]}</text>'
               f'<rect class="obj" x="{cx-75}" y="110" width="150" height="12" rx="2"/>'
               f'<rect class="obj" x="{cx-68}" y="122" width="10" height="43"/><rect class="obj" x="{cx+58}" y="122" width="10" height="43"/>'
               f'<text class="lbl" x="{cx}" y="145">{n["table"]}</text>'
               f'<rect class="obj2" x="{cx-14}" y="66" width="28" height="44" rx="3"/><text class="lbl" x="{cx}" y="58">{n["lamp"]}</text>')
    # facing
    out.append(obj(cx - 80, 105, 50, 120, n["sofa"], "e") + obj(cx + 80, 105, 50, 120, n["sofa"], "w", "obj2")
               + f'<path class="hint" d="M{cx-40} 105 h80" marker-end="url(#ah)" marker-start="url(#ah)"/>')
    # array: two back-to-back pairs of rack rows, an aisle between them
    s = ""
    for i, x in enumerate((62, 96, 204, 238)):
        for y in (55, 95, 135):
            s += f'<rect class="{"obj" if i < 2 else "obj2"}" x="{x - 15}" y="{y - 18}" width="30" height="36" rx="2"/>'
    s += f'<path class="hint" d="M150 40 v115"/><text class="dim" x="150" y="172">{n["aisle"]}</text>'
    s += f'<text class="dim" x="79" y="172">{n["rack"]}</text>'
    out.append(s)
    return out


def svg(lang):
    width, height = COLS * W + PAD * 2, ROWS * H + PAD * 2 + 30
    font = "'Noto Sans JP','Hiragino Sans','Yu Gothic',sans-serif" if lang == "ja" else "system-ui,-apple-system,'Segoe UI',sans-serif"
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" font-family="{font}">',
             """<style>
  .bg{fill:#ffffff}.card{fill:#f6f8fa;stroke:#d0d7de}.obj{fill:#cfe3ff;stroke:#2f6fd0;stroke-width:1.5}
  .obj2{fill:#ffe1c2;stroke:#c8661b;stroke-width:1.5}.front{fill:#1f2328}.wall{stroke:#57606a;stroke-width:6;stroke-linecap:round}
  .hint{stroke:#8c959f;stroke-width:1.5;stroke-dasharray:4 4;fill:none}.lbl{font-size:13px;text-anchor:middle;fill:#1f2328}
  .dim{font-size:12px;fill:#57606a;text-anchor:middle}.name{font-size:15px;font-weight:600;fill:#1f2328;font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
  .desc{font-size:13px;fill:#57606a}.legend{font-size:13px;fill:#57606a}#ah path{fill:#8c959f}
  @media (prefers-color-scheme: dark){
    .bg{fill:#0d1117}.card{fill:#161b22;stroke:#30363d}.obj{fill:#1d3b66;stroke:#79b0ff}.obj2{fill:#5a3414;stroke:#f0a060}
    .front{fill:#e6edf3}.wall{stroke:#8b949e}.lbl,.name{fill:#e6edf3}.dim,.desc,.legend{fill:#8b949e}
  }
</style>""",
             '<defs><marker id="ah" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z"/></marker></defs>',
             f'<rect class="bg" width="{width}" height="{height}" rx="8"/>']
    for k, (body, (name, desc)) in enumerate(zip(panels(lang), TEXT[lang])):
        x, y = PAD + (k % COLS) * W, PAD + (k // COLS) * H
        parts.append(f'<g transform="translate({x},{y})"><rect class="card" x="6" y="6" width="{W-12}" height="{H-12}" rx="8"/>'
                     f'<text class="name" x="20" y="30">{name}</text><text class="desc" x="20" y="{H-20}">{desc}</text>'
                     f'<g transform="translate(0,12)">{body}</g></g>')
    parts.append(f'<text class="legend" x="{PAD+6}" y="{height-16}">{LEGEND[lang]}</text></svg>')
    return "\n".join(parts) + "\n"


out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "docs" / "images"
(out / "layout_relations.svg").write_text(svg("en"), encoding="utf-8")
(out / "layout_relations_ja.svg").write_text(svg("ja"), encoding="utf-8")
