"""Draw the concept figures of docs/architecture.md: docs/images/arch_*{,_ja}.svg (English, Japanese).

    python scripts/make_architecture_figures.py [out_dir]   # writes all four in both languages

- arch_object_transform: the xformOps that place an asset (usd/compose.py `_add_asset`)
- arch_room_geometry:    RoomGeometry of a rectangle and of a floor plan (layout/room.py)
- arch_validate_issues:  the static checks' issues (layout/validate.py) and narrow_aisle (layout/usability.py)
- arch_size_fits:        min_size / max_size compared orientation-free (assets/providers/base.py `fits`)
Same look as scripts/make_layout_relations_svg.py (light and dark through prefers-color-scheme).
"""
import math
import sys
from pathlib import Path

PAD = 16
FONTS = {"ja": "'Noto Sans JP','Hiragino Sans','Yu Gothic',sans-serif",
         "en": "system-ui,-apple-system,'Segoe UI',sans-serif"}
LANG = "en"


def tr(ja, en):
    """The label in the language being drawn."""
    return ja if LANG == "ja" else en

STYLE = """<style>
  .bg{fill:#ffffff}.card{fill:#f6f8fa;stroke:#d0d7de}.box{fill:#ffffff;stroke:#d0d7de}
  .obj{fill:#cfe3ff;stroke:#2f6fd0;stroke-width:1.5}.obj2{fill:#ffe1c2;stroke:#c8661b;stroke-width:1.5}
  .bad{fill:#ffd7d5;stroke:#cf222e;stroke-width:1.8}.ok{fill:none;stroke:#1a7f37;stroke-width:2;stroke-dasharray:4 3}
  .front{fill:#1f2328}.wall{stroke:#57606a;stroke-width:5;stroke-linecap:round}
  .wbody{fill:#afb8c1}.col{fill:#6e7781}.cl{stroke:#1f2328;stroke-width:1.2;stroke-dasharray:6 3;fill:none}
  .face{stroke:#1a7f37;stroke-width:3;stroke-linecap:butt;fill:none}.glass{stroke:#2f6fd0;stroke-width:2}
  .keep{fill:#fff1c2;stroke:#bf8700;stroke-width:1;stroke-dasharray:3 2}
  .zoneA{fill:#e7f0ff}.zoneB{fill:#f3ecff}.floor{fill:#ffffff}
  .hint{stroke:#8c959f;stroke-width:1.5;stroke-dasharray:4 4;fill:none}.axis{stroke:#57606a;stroke-width:1.5;fill:none}
  .dimln{stroke:#57606a;stroke-width:1;fill:none}.badln{stroke:#cf222e;stroke-width:1.5;fill:none}
  .pivot{fill:#cf222e;stroke:#ffffff;stroke-width:1.5}
  .lbl{font-size:13px;text-anchor:middle;fill:#1f2328}.sm{font-size:11px;fill:#57606a}
  .dim{font-size:12px;fill:#57606a;text-anchor:middle}.name{font-size:15px;font-weight:600;fill:#1f2328}
  .code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}.mono{font-size:12px;fill:#1f2328;font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
  .desc{font-size:13px;fill:#57606a}.legend{font-size:13px;fill:#57606a}.badt{fill:#cf222e}.okt{fill:#1a7f37}.warnt{fill:#9a6700}
  .tag{font-size:11px;font-weight:600}.verdict{font-size:13px;font-weight:600}#ah path{fill:#57606a}#ahr path{fill:#cf222e}
  @media (prefers-color-scheme: dark){
    .bg{fill:#0d1117}.card{fill:#161b22;stroke:#30363d}.box{fill:#0d1117;stroke:#30363d}
    .obj{fill:#1d3b66;stroke:#79b0ff}.obj2{fill:#5a3414;stroke:#f0a060}.bad{fill:#5c1a1a;stroke:#ff7b72}.ok{stroke:#3fb950}
    .front{fill:#e6edf3}.wall{stroke:#8b949e}.wbody{fill:#6e7681}.col{fill:#adbac7}.cl{stroke:#e6edf3}
    .face{stroke:#3fb950}.glass{stroke:#79b0ff}.keep{fill:#4a3a0a;stroke:#d29922}
    .zoneA{fill:#132a4a}.zoneB{fill:#2d1f47}.floor{fill:#0d1117}.axis,.dimln{stroke:#8b949e}.badln{stroke:#ff7b72}
    .pivot{fill:#ff7b72;stroke:#0d1117}
    .lbl,.name,.mono{fill:#e6edf3}.dim,.desc,.legend,.sm{fill:#8b949e}.badt{fill:#ff7b72}.okt{fill:#3fb950}.warnt{fill:#d29922}
    #ah path{fill:#8b949e}#ahr path{fill:#ff7b72}
  }
</style>"""
DEFS = ('<defs><marker id="ah" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
        '<path d="M0 0L10 5L0 10z"/></marker><marker id="ahr" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" '
        'markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z"/></marker></defs>')


def doc(width, height, body):
    return "\n".join([f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" '
                      f'height="{height}" font-family="{FONTS[LANG]}">', STYLE, DEFS,
                      f'<rect class="bg" width="{width}" height="{height}" rx="8"/>', *body, "</svg>"]) + "\n"


def text_w(s, size=13):
    """Rough width of a label: full width for Japanese, about 0.6 em for ASCII."""
    return sum(size if ord(ch) > 0x2000 else size * (0.6 if LANG == "ja" else 0.55) for ch in s)


def t(x, y, s, cls="lbl", anchor=None, rot=None):
    a = f' style="text-anchor:{anchor}"' if anchor else ""  # the classes set text-anchor too
    r = f' transform="rotate({rot},{x},{y})"' if rot is not None else ""
    return f'<text class="{cls}" x="{x:g}" y="{y:g}"{a}{r}>{s}</text>'


def rect(x, y, w, h, cls, rx=0):
    return f'<rect class="{cls}" x="{x:g}" y="{y:g}" width="{w:g}" height="{h:g}" rx="{rx}"/>'


def line(x1, y1, x2, y2, cls="axis", arrow=None):
    m = f' marker-end="url(#{arrow})"' if arrow else ""
    return f'<line class="{cls}" x1="{x1:g}" y1="{y1:g}" x2="{x2:g}" y2="{y2:g}"{m}/>'


def poly(pts, cls):
    return f'<polygon class="{cls}" points="{" ".join(f"{x:g},{y:g}" for x, y in pts)}"/>'


def rbox(cx, cy, w, h, yaw, cls, front=False):
    """A w x h box centred at cx,cy turned by yaw (CCW on screen, y down), a front triangle on its local +x side."""
    c, s = math.cos(math.radians(yaw)), -math.sin(math.radians(yaw))
    tr = lambda lx, ly: (cx + lx * c - ly * s, cy + lx * s + ly * c)  # noqa: E731
    out = poly([tr(-w / 2, -h / 2), tr(w / 2, -h / 2), tr(w / 2, h / 2), tr(-w / 2, h / 2)], cls)
    if front:
        out += poly([tr(w / 2, -6), tr(w / 2, 6), tr(w / 2 + 9, 0)], "front")
    return out


def card(x, y, w, h, title, desc, body, tag=None, title_cls="name code"):
    s = f'<g transform="translate({x},{y})"><rect class="card" x="6" y="6" width="{w - 12}" height="{h - 12}" rx="8"/>'
    s += t(20, 30, title, title_cls, "start")
    if tag:
        s += t(w - 18, 30, tag[0], f"tag {tag[1]}", "end")
    for i, d in enumerate(desc):
        s += t(20, h - 20 - 18 * (len(desc) - 1 - i), d, "desc", "start")
    return s + body + "</g>"


# --- 1. how an asset is placed -------------------------------------------------------------------------

def object_transform():
    W, H, COLS = 300, 262, 3
    K = 0.8  # px per asset unit (cm) in the side views
    L, D = 180 * K, 40 * K  # the asset: 180 cm tall (along its +Y), 40 cm deep

    def yz_axes(ox, oy, x0=None, x1=None, y1=None):
        x0, x1, y1 = x0 if x0 is not None else ox - 20, x1 if x1 is not None else W - 26, y1 if y1 is not None else 60
        return (line(x0, oy, x1, oy, "axis", "ah") + t(x1 + 2, oy + 16, "+Y", "dim")
                + line(ox, oy + 22, ox, y1, "axis", "ah") + t(ox - 12, y1 + 4, "+Z", "dim"))

    def up_arrow(x1, y1, x2, y2, lx, ly, anchor="middle"):
        return line(x1, y1, x2, y2, "dimln", "ah") + t(lx, ly, tr("アセットの上", "asset up"), "sm", anchor)

    panels = []
    # 1 the asset as it comes: Y-up in centimetres, lying on its back in our Z-up stage
    ox, oy = 40, 172
    y0, z0 = 24, 26
    b = rect(ox + y0, oy - z0 - D, L, D, "obj") + yz_axes(ox, oy)
    b += up_arrow(ox + y0 + 30, oy - z0 - D / 2, ox + y0 + L - 22, oy - z0 - D / 2, ox + y0 + L / 2 + 4, oy - z0 - D - 8)
    b += f'<circle class="pivot" cx="{ox + y0}" cy="{oy - z0 - D / 2}" r="5"/>'
    b += t(ox + y0 - 6, oy - z0 - D - 8, "pivot", "sm code", "middle")
    b += t(ox + 4, oy + 16, tr("原点", "origin"), "sm", "start")
    panels.append((tr("アセット（例：Y-up・cm）", "The asset (e.g. Y-up, cm)"),
                   [tr("Z-upのステージでは寝ている。", "It lies on its back in a Z-up stage."),
                    tr("●がpivot（bbox底面のfootprint中心）", "● = pivot (footprint center, bbox bottom)")], b, "name"))
    # 2 translate:pivot
    ox, oy = 40, 140
    b = rect(ox, oy - D / 2, L, D, "obj") + yz_axes(ox, oy)
    b += f'<circle class="pivot" cx="{ox}" cy="{oy}" r="5"/>'
    b += up_arrow(ox + 30, oy, ox + L - 22, oy, ox + L / 2 + 4, oy - D / 2 - 8)
    panels.append(("translate:pivot", [tr("−pivotだけ動かし、pivotを原点に", "move by −pivot: the pivot to the origin")], b, "name code"))
    # 3 rotateX:upAxis
    ox, oy = 150, 200
    b = yz_axes(ox, oy, ox - 90, W - 40, 44) + rect(ox - D / 2, oy - L, D, L, "obj")
    b += f'<circle class="pivot" cx="{ox}" cy="{oy}" r="5"/>'
    b += up_arrow(ox, oy - 34, ox, oy - L + 18, ox + D / 2 + 8, oy - L / 2, "start")
    b += f'<path class="hint" d="M{ox + 60} {oy - 14} A60 60 0 0 0 {ox + 14} {oy - 60}" marker-end="url(#ah)"/>'
    b += t(ox + 62, oy - 52, "90°", "dim")
    panels.append(("rotateX:upAxis", [tr("Y-upのアセットだけ90°回して立てる", "stand only Y-up assets up by 90°")], b, "name code"))
    # 4 scale = metersPerUnit
    ox, oy = 150, 200
    s = 0.33
    b = yz_axes(ox, oy, ox - 90, W - 40, 44) + rect(ox - D / 2, oy - L, D, L, "hint")
    b += rect(ox - D * s / 2, oy - L * s, D * s, L * s, "obj") + f'<circle class="pivot" cx="{ox}" cy="{oy}" r="4"/>'
    b += t(ox + D / 2 + 8, oy - L + 12, tr("180（cm）", "180 (cm)"), "sm", "start")
    b += t(ox + 14, oy - L * s / 2 + 4, tr("1.8（m）", "1.8 (m)"), "sm", "start")
    b += line(ox + D / 2 + 30, oy - L + 22, ox + 30, oy - L * s - 4, "dimln", "ah")
    panels.append(("scale", [tr("metersPerUnitを掛ける（cmなら0.01）", "times metersPerUnit (0.01 for cm)")], b, "name code"))
    # 5 rotateZ (top view)
    cx, cy = 150, 118
    fw, fd = 64, 32  # footprint (80 x 40 cm), drawn larger than the side views
    b = (line(cx - 100, cy, cx + 112, cy, "axis", "ah") + t(cx + 112, cy + 16, tr("+X（東）", "+X (east)"), "dim")
         + line(cx, cy + 80, cx, cy - 84, "axis", "ah") + t(cx + 10, cy - 80, tr("+Y（北）", "+Y (north)"), "dim", "start"))
    b += rbox(cx, cy, fw, fd, 0, "hint") + rbox(cx, cy, fw, fd, 90, "obj", front=True)
    b += f'<circle class="pivot" cx="{cx}" cy="{cy}" r="4"/>'
    b += f'<path class="hint" d="M{cx + 70} {cy} A70 70 0 0 0 {cx} {cy - 70}" marker-end="url(#ah)"/>'
    b += t(cx + 58, cy - 54, "yaw_deg", "sm code", "start")
    panels.append(("rotateZ", [tr("上から見て正面（+X、三角）を", "turn the front (+X, triangle)"), tr("yaw_degの向きへ回す", "to yaw_deg, seen from above")], b, "name code"))
    # 6 translate (top view of the room)
    rx, ry, rw, rd = 52, 44, 196, 128
    b = rect(rx, ry, rw, rd, "floor") + f'<rect class="hint" x="{rx}" y="{ry}" width="{rw}" height="{rd}"/>'
    b += line(rx, ry + rd, rx + rw + 22, ry + rd, "axis", "ah") + t(rx + rw + 18, ry + rd + 16, "x", "dim")
    b += line(rx, ry + rd, rx, ry - 18, "axis", "ah") + t(rx - 10, ry - 10, "y", "dim")
    b += t(rx - 4, ry + rd + 16, "(0, 0)", "sm", "middle")
    px, py = rx + 140, ry + 48
    b += line(rx + 4, ry + rd - 4, px - 12, py + 12, "hint", "ah")
    b += rbox(px, py, 64 * 0.5, 32 * 0.5, 90, "obj", front=True) + f'<circle class="pivot" cx="{px}" cy="{py}" r="3.5"/>'
    b += t(px + 18, py + 30, "(x, y)", "sm", "start")
    panels.append(("translate", [tr("部屋の中の位置（x, y）と高さzへ", "to (x, y) in the room and height z"), tr("（zは床0・段差・載せた物の上面）", "(z: floor 0, a step, a support's top)")], b, "name code"))

    width = COLS * W + PAD * 2
    strip_y = PAD + 2 * H
    strip_h = 168
    height = strip_y + strip_h + 44
    body = []
    for k, (title, desc, b, cls) in enumerate(panels):
        x, y = PAD + (k % COLS) * W, PAD + (k // COLS) * H
        num = f'<text class="dim" x="{W - 22}" y="30" text-anchor="end">{k + 1}</text>'
        body.append(card(x, y, W, H, title, desc, f'<g transform="translate(0,12)">{b}</g>' + num, title_cls=cls))
    # the prim layout
    x0, y0 = PAD + 6, strip_y + 6
    bx, bw = x0 + 14, 560
    s = f'<g><rect class="card" x="{x0}" y="{y0}" width="{width - 2 * PAD - 12}" height="{strip_h - 12}" rx="8"/>'
    s += t(x0 + 14, y0 + 24, tr("primの形（scene.usda）", "The prims (scene.usda)"), "name", "start")
    s += rect(bx, y0 + 38, bw, 108, "box", 6)
    s += t(bx + 12, y0 + 60, "/World/Objects/&lt;id&gt;　Xform", "mono", "start")
    s += t(bx + 12, y0 + 82, "xformOpOrder = [translate, rotateZ, scale,", "mono", "start")
    s += t(bx + 12 + 7.2 * 16, y0 + 100, "rotateX:upAxis, translate:pivot]", "mono", "start")
    s += t(bx + 12, y0 + 122, "customData: agentic_sim_world_gen:{source, uri, license, label}", "mono", "start")
    s += t(bx + 12, y0 + 140, tr("点に効くのは右から（上の図の1→6の順）", "applied right to left (1→6 above)"), "sm", "start")
    cx0 = bx + bw + 46
    s += line(bx + bw, y0 + 92, cx0 - 4, y0 + 92, "axis", "ah") + t((bx + bw + cx0) / 2, y0 + 84, tr("子", "child"), "dim")
    cw = width - PAD - 6 - 14 - cx0
    s += rect(cx0, y0 + 38, cw, 108, "box", 6)
    s += t(cx0 + 12, y0 + 60, "Asset", "mono", "start")
    s += t(cx0 + 12, y0 + 82, tr("references: アセットのURI", "references: the asset URI"), "mono", "start")
    s += t(cx0 + 12, y0 + 100, "variant: PhysX=Enabled", "mono", "start")
    s += t(cx0 + 12, y0 + 122, tr("アセット自身のルートの", "the asset's own root"), "sm", "start")
    s += t(cx0 + 12, y0 + 140, tr("変換はここに残る", "transform stays here"), "sm", "start")
    body.append(s + "</g>")
    body.append(t(PAD + 6, height - 16, tr("横から見た図（1〜4）と上から見た図（5・6）。●がpivot、三角が正面。"
                                          "4の縮み方は模式（実際は1/100）。",
                                          "Side views (1–4) and top views (5, 6). ● = pivot, triangle = front. "
                                          "The shrink in 4 is schematic (really 1/100)."), "legend", "start"))
    return doc(width, height, body)


# --- 2. RoomGeometry ---------------------------------------------------------------------------------

def room_geometry():
    W, H = 440, 414
    width, height = 2 * W + PAD * 2, H + PAD * 2 + 52
    T = 10  # drawn wall thickness (wall_thickness, default 0.12 m; thicker here to be seen)
    body = []

    # left: a rectangular room (Room.width / depth)
    S = 58  # px per m
    w, d = 4.6, 3.5
    ox, oy = 72, 294  # interior (0, 0)
    X = lambda u: ox + u * S  # noqa: E731
    Y = lambda v: oy - v * S  # noqa: E731
    g = rect(X(0), Y(d), w * S, d * S, "floor")
    door = (0.75, 1.65)  # Opening(kind=door, wall=south, offset=1.2, width=0.9): keep_clear = min(0.9, 0.9)
    win = (1.15, 2.35)  # Opening(kind=window, wall=east, offset=1.75, width=1.2): keep_clear = 0
    c = 0.9 * S
    g += rect(X(door[0]), Y(0) - c, (door[1] - door[0]) * S, c, "keep")
    g += rect(X(door[0]), Y(0) + T, (door[1] - door[0]) * S, c, "keep")
    # wall bodies: outside the interior (they are not obstacles of a rectangle)
    g += rect(X(0) - T, Y(d) - T, w * S + 2 * T, T, "wbody")
    g += rect(X(0) - T, Y(0), (door[0] * S) + T, T, "wbody") + rect(X(door[1]), Y(0), (w - door[1]) * S + T, T, "wbody")
    g += rect(X(0) - T, Y(d), T, d * S, "wbody")
    g += rect(X(w), Y(d), T, (d - win[1]) * S, "wbody") + rect(X(w), Y(win[0]), T, win[0] * S, "wbody")
    g += line(X(w) + T / 2, Y(win[0]), X(w) + T / 2, Y(win[1]), "glass")
    # centerlines
    g += line(X(0) - T, Y(d) - T / 2, X(w) + T, Y(d) - T / 2, "cl") + line(X(0) - T, Y(0) + T / 2, X(w) + T, Y(0) + T / 2, "cl")
    g += line(X(0) - T / 2, Y(0), X(0) - T / 2, Y(d), "cl") + line(X(w) + T / 2, Y(0), X(w) + T / 2, Y(d), "cl")
    # faces: the interior edges, the whole length (doors are kept clear by their obstacle)
    for a, b in (((0, 0), (w, 0)), ((0, d), (w, d)), ((0, 0), (0, d)), ((w, 0), (w, d))):
        g += line(X(a[0]), Y(a[1]), X(b[0]), Y(b[1]), "face")
    # labels
    g += t(X(w / 2), Y(d) - T - 8, "north → Wall_N", "mono")
    g += t(X(3.4), Y(0) + T + 18, "south → Wall_S", "mono")
    g += t(X(0) - T - 8, Y(d / 2), "west → Wall_W", "mono", rot=-90)
    g += t(X(w) + T + 14, Y(0.55), "east → Wall_E", "mono", rot=-90)
    g += t(X(door[0] + 0.45), Y(0) - c / 2 + 4, "door", "sm code", "middle")
    g += t(X(door[0] + 0.45), Y(0) + T + c / 2 + 4, "door", "sm code", "middle")
    g += t(X(w) - 8, Y((win[0] + win[1]) / 2) + 4, "window", "sm code", "end")
    g += t(X(w / 2), Y(d / 2) - 10, tr("x∈[0, width]、y∈[0, depth]", "x∈[0, width], y∈[0, depth]"), "lbl")
    g += t(X(w / 2), Y(d / 2) + 12, tr("床の上面z=0", "floor top z=0"), "dim")
    g += t(X(0) + 4, Y(0) - 8, "(0, 0)", "sm", "start")
    g += t(X(w) - 4, Y(d) + 16, "(width, depth)", "sm", "end")
    g += t(X(1.4), Y(d) + 18, "face", "sm code", "middle")
    # compass
    g += line(400, Y(d) + 56, 400, Y(d) + 16, "axis", "ah") + t(400, Y(d) + 8, tr("北（+Y）", "north (+Y)"), "dim")
    g += t(400, Y(d) + 74, tr("東は+X", "east is +X"), "dim")
    body.append(card(PAD, PAD, W, H, tr("矩形の部屋（Room.width / depth）", "A rectangular room (Room.width / depth)"),
                     [tr("壁は部屋の外側にあり障害物ではない（出ればout_of_room）。", "Walls lie outside: not obstacles (out_of_room)."), tr("USDの壁の名前はWall_S / N / W / E", "The USD walls: Wall_S / N / W / E")],
                     g, title_cls="name"))

    # right: a floor plan (Room.plan): walls on their centerlines, zones, a column
    S = 54
    w, d, part = 6.0, 4.4, 2.6  # outer walls on the centerline box, a partition wall at x = part
    ox, oy = 50, 312
    X = lambda u: ox + u * S  # noqa: E731
    Y = lambda v: oy - v * S  # noqa: E731
    h = T / 2
    g = poly([(X(0) + h, Y(0) - h), (X(part) - h, Y(0) - h), (X(part) - h, Y(d) + h), (X(0) + h, Y(d) + h)], "zoneA")
    g += poly([(X(part) + h, Y(0) - h), (X(w) - h, Y(0) - h), (X(w) - h, Y(d) + h), (X(part) + h, Y(d) + h)], "zoneB")
    dr = (2.2, 3.0)  # door in the partition (along y), keep_clear = min(0.8, 0.9) = 0.8 on both sides
    c = 0.8 * S
    g += rect(X(part) - h - c, Y(dr[1]), c, (dr[1] - dr[0]) * S, "keep")
    g += rect(X(part) + h, Y(dr[1]), c, (dr[1] - dr[0]) * S, "keep")
    wn = (3.4, 4.8)  # window in the south wall of the LDK
    # wall bodies (obstacles wall:<id> of a plan); corners closed by _close_corners
    g += rect(X(0) - h, Y(d) - h, w * S + T, T, "wbody")
    g += rect(X(0) - h, Y(0) - h, (wn[0]) * S + h, T, "wbody") + rect(X(wn[1]), Y(0) - h, (w - wn[1]) * S + h, T, "wbody")
    g += line(X(wn[0]), Y(0), X(wn[1]), Y(0), "glass")
    g += rect(X(0) - h, Y(d), T, d * S, "wbody") + rect(X(w) - h, Y(d), T, d * S, "wbody")
    g += rect(X(part) - h, Y(d) + h, T, (d - dr[1]) * S - h, "wbody") + rect(X(part) - h, Y(dr[0]), T, dr[0] * S - h, "wbody")
    # centerlines (PlanWall.start -> end)
    for a, b in (((0, 0), (w, 0)), ((0, d), (w, d)), ((0, 0), (0, d)), ((w, 0), (w, d)), ((part, 0), (part, d))):
        g += line(X(a[0]), Y(a[1]), X(b[0]), Y(b[1]), "cl")
    # faces: both sides of each wall where a zone is in front, cut where other walls touch
    for a, b in (((0, 0), (part, 0)), ((part, 0), (w, 0)), ((0, d), (part, d)), ((part, d), (w, d))):
        g += line(X(a[0]) + h, Y(a[1]) + (-h if a[1] == 0 else h), X(b[0]) - h, Y(b[1]) + (-h if b[1] == 0 else h), "face")
    for x, sgn in ((0, 1), (w, -1), (part, -1), (part, 1)):
        g += line(X(x) + sgn * h, Y(0) - h, X(x) + sgn * h, Y(d) + h, "face")
    # a column in the LDK (column:<id>)
    g += rect(X(4.9) - 0.2 * S, Y(1.2) - 0.2 * S, 0.4 * S, 0.4 * S, "col")
    g += t(X(4.9), Y(1.2) + 0.2 * S + 16, "column:c1", "sm code", "middle")
    # labels
    g += t(X(part / 2), Y(d / 2) - 4, tr("寝室", "bedroom"), "lbl") + t(X(part / 2), Y(d / 2) + 14, "zone bed", "sm code", "middle")
    g += t(X((part + w) / 2), Y(3.5), "LDK", "lbl")
    g += t(X((part + w) / 2), Y(3.5) + 18, "zone ldk", "sm code", "middle")
    g += t(X(part) + h + c + 4, Y((dr[0] + dr[1]) / 2) + 4, "door:d1", "sm code", "start")
    g += t(X((wn[0] + wn[1]) / 2), Y(0) + 20, "window", "sm code", "middle")
    g += t(X(1.3), Y(0) + 20, "wall:s", "sm code", "middle")
    g += t(X(part) + 4, Y(d) + 30, "wall:p", "sm code", "start")
    g += t(X(part / 2), Y(d) - 14, tr("中心線（PlanWall.start→end）", "centerline (PlanWall.start→end)"), "sm", "middle")
    body.append(card(PAD + W, PAD, W, H, tr("間取り（Room.plan）", "A floor plan (Room.plan)"),
                     [tr("壁の本体（中心線±厚さの半分）が障害物wall:&lt;id&gt;。", "Wall bodies (centerline ± half thickness): wall:&lt;id&gt;."), tr("faceは壁の両側の、前がゾーンの部分", "faces: both sides of a wall, where a zone is in front")],
                     g, title_cls="name"))
    # legend
    lx, ly = PAD + 6, height - 30
    items = [("wbody", tr("壁の本体（thickness）", "wall body (thickness)")), ("cl", tr("中心線", "centerline")), ("face", tr("壁面face", "wall face")),
             ("keep", tr("ドアの前の空き（障害物door:&lt;id&gt;）", "door clearance (obstacle door:&lt;id&gt;)")), ("col", tr("柱", "column")), ("zoneA", tr("ゾーン", "zone"))]
    s, x = "", lx
    for cls, label in items:
        if cls in ("cl", "face"):
            s += line(x, ly - 4, x + 22, ly - 4, cls)
        else:
            s += rect(x, ly - 12, 22, 14, cls)
        s += t(x + 28, ly, label, "legend", "start")
        x += 28 + text_w(label.replace("&lt;", "<").replace("&gt;", ">")) + 12
    body.append(s)
    body.append(t(PAD + 6, height - 8, tr("ドアの前の空きの深さはclearance（既定は幅、最大0.9 m）、両側に取る。窓には取らない。",
                                         "A door's clear space is clearance deep (default its width, at most 0.9 m), "
                                         "on both sides. Windows get none."), "legend", "start"))
    return doc(width, height, body)


# --- 3. the layout checks ------------------------------------------------------------------------------

def clip(subject, clipper):
    """Sutherland-Hodgman: the part of polygon `subject` inside the convex polygon `clipper` (both CCW on screen)."""
    out = subject
    for i in range(len(clipper)):
        a, b = clipper[i], clipper[(i + 1) % len(clipper)]
        side = lambda p: (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])  # noqa: E731
        inp, out = out, []
        for j in range(len(inp)):
            p, q = inp[j], inp[(j + 1) % len(inp)]
            sp, sq = side(p), side(q)
            if sp >= 0:
                out.append(p)
            if (sp >= 0) != (sq >= 0):
                r = sp / (sp - sq)
                out.append((p[0] + (q[0] - p[0]) * r, p[1] + (q[1] - p[1]) * r))
    return out


def corners(cx, cy, w, h, yaw):
    c, s = math.cos(math.radians(yaw)), -math.sin(math.radians(yaw))
    return [(cx + lx * c - ly * s, cy + lx * s + ly * c) for lx, ly in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2))]


def validate_issues():
    W, H, COLS = 222, 236, 4
    cx = W / 2
    cards = []
    # out_of_room
    b = line(30, 56, 30, 150, "wall") + line(30, 150, 160, 150, "wall") + line(30, 56, 160, 56, "wall")
    b += line(160, 56, 160, 150, "wall")
    b += rect(56, 84, 50, 40, "obj") + rect(136, 90, 52, 34, "bad")
    cards.append(("out_of_room", [tr("足元が部屋の外に出る", "the footprint leaves the room"), tr("（間取りではゾーンの外）", "(on a plan: its zone)")], b, ("issue", "badt")))
    # overlap
    a_, b_ = corners(cx - 22, 104, 96, 50, 0), corners(cx + 36, 112, 56, 56, 20)
    b = poly(a_, "obj") + poly(b_, "obj2") + poly(clip(b_, a_), "bad")
    cards.append(("overlap", [tr("足元の多角形が重なり（SAT）、", "the footprints overlap (SAT)"), tr("高さの範囲も重なる", "and so do the height ranges")], b, ("issue", "badt")))
    # too_tall (side view)
    b = line(30, 156, 192, 156, "wall") + line(30, 74, 192, 74, "badln") + t(30, 68, "room.height", "sm code", "start")
    b += rect(56, 100, 60, 56, "obj") + rect(132, 54, 40, 102, "bad")
    cards.append(("too_tall", [tr("上端（z＋高さ）が天井より上", "the top (z + height) is above"), tr("（横から見た図）", "the ceiling (side view)")], b, ("issue", "badt")))
    # blocks_door
    b = line(24, 64, 80, 64, "wall") + line(140, 64, 198, 64, "wall") + rect(80, 64, 60, 52, "keep")
    b += t(110, 58, "door", "sm code", "middle") + rect(118, 96, 62, 44, "bad")
    cards.append(("blocks_door", [tr("ドアの前の空き（両側）に入る", "in a door's clear space (both"), tr("深さは既定で幅（最大0.9 m）", "sides; its width, at most 0.9 m)")], b, ("issue", "badt")))
    # hits_wall
    b = rect(140, 50, 12, 106, "wbody") + line(146, 50, 146, 156, "cl")
    b += rect(60, 76, 50, 40, "obj") + rect(112, 112, 44, 34, "bad")
    cards.append(("hits_wall", [tr("間取りの壁の本体に食い込む", "cuts into a plan's wall body"), tr("（矩形の部屋はout_of_room）", "(a rectangle: out_of_room)")], b, ("issue", "badt")))
    # narrow_aisle (usability)
    b = ""
    for y in (48, 76, 104):
        b += rect(34, y, 54, 24, "obj2") + poly([(88, y + 6), (88, y + 18), (96, y + 12)], "front")
        b += rect(132, y, 54, 24, "obj2") + poly([(132, y + 6), (132, y + 18), (124, y + 12)], "front")
    b += line(98, 140, 122, 140, "badln", "ahr") + line(122, 140, 98, 140, "badln", "ahr")
    b += t(110, 158, tr("通路", "aisle"), "sm", "middle")
    cards.append(("narrow_aisle", [tr("棚などの前への道が", "the way to a shelf front is"), tr("場所の通路幅より狭い", "narrower than the place's aisle"), tr("（家0.75・店1.0・既定0.9 m）", "(home 0.75, shop 1.0, else 0.9 m)")], b,
                  (tr("警告", "warning"), "warnt")))
    # tuck under a desk: allowed
    b = rect(40, 58, 140, 56, "obj") + t(110, 82, tr("机", "desk"), "lbl")
    b += rect(88, 96, 44, 44, "obj2") + rect(88, 96, 44, 18, "ok")
    b += line(138, 96, 138, 114, "dimln") + t(142, 130, tr("≤0.6×短辺", "≤0.6×short side"), "sm", "start")
    b += t(110, 158, tr("椅子", "chair"), "lbl")
    cards.append(("tuck_pair", [tr("椅子は机の下に、足元の", "a chair may go under a desk"), tr("短辺の60%（MAX_TUCK）", "by 60% of its short side"), tr("まで入れてよい", "(MAX_TUCK)")], b, (tr("許される", "allowed"), "okt")))
    # a tuck at the table's corner is an overlap (the leg stands there)
    b = rect(40, 58, 120, 56, "obj") + t(96, 90, tr("机", "desk"), "lbl")
    b += rect(140, 94, 44, 44, "obj2") + rect(140, 94, 20, 20, "bad") + t(162, 158, tr("椅子", "chair"), "lbl")
    cards.append(("overlap", [tr("机の角から差し込むと", "tucked in at the corner, it"), tr("脚に当たる。辺から入れる", "hits the leg: go in from a side"), tr("（through_side）", "(through_side)")], b, ("issue", "badt")))

    width = COLS * W + PAD * 2
    rows = (len(cards) + COLS - 1) // COLS
    height = rows * H + PAD * 2 + 30
    body = []
    for k, (name, desc, b, tag) in enumerate(cards):
        x, y = PAD + (k % COLS) * W, PAD + (k // COLS) * H
        body.append(card(x, y, W, H, name, desc, b, tag))
    body.append(t(PAD + 6, height - 16, tr("上から見た図（too_tallは横から）。赤が問題の物、黄がドアの前の空き、"
                                          "緑の破線が許される重なり。issueは0件まで直す。",
                                          "Top views (too_tall from the side). Red: the problem; yellow: a door's clear "
                                          "space; green dashes: an allowed overlap. Fix issues down to 0."),
                  "legend", "start"))
    return doc(width, height, body)


# --- 4. min_size / max_size ----------------------------------------------------------------------------

def size_fits():
    W, H, COLS = 300, 268, 3
    M = 70  # px per m
    MAX = (1.6, 0.8, 0.8)
    cases = [((1.4, 0.7, 0.75), True), ((0.7, 1.4, 0.75), True), ((1.4, 0.7, 1.1), False)]
    body = []
    for k, ((w, d, h), ok) in enumerate(cases):
        cx, cy = 92, 112
        long_x = w >= d
        ew, ed = (MAX[0], MAX[1]) if long_x else (MAX[1], MAX[0])
        g = rect(cx - ew * M / 2, cy - ed * M / 2, ew * M, ed * M, "hint")
        g += rect(cx - w * M / 2, cy - d * M / 2, w * M, d * M, "obj")
        g += t(cx, cy - ed * M / 2 - 8, f"w={w:g}", "sm", "middle")
        g += t(cx + ew * M / 2 + 6, cy + 4, f"d={d:g}", "sm", "start")
        g += t(cx, 186, tr("上から", "top"), "dim")
        # side view: height against max height
        fx, fy = 196, 160
        g += line(fx - 12, fy, fx + 72, fy, "wall")
        g += line(fx - 12, fy - MAX[2] * M, fx + 72, fy - MAX[2] * M, "hint")
        g += rect(fx, fy - h * M, 40, h * M, "obj" if ok else "bad")
        g += t(fx + 20, fy - h * M - 6, f"h={h:g}", "sm", "middle")
        g += t(fx + 30, 186, tr("横から", "side"), "dim")
        dims = (max(w, d), min(w, d), h)
        title = f"size_m = ({w:g}, {d:g}, {h:g})"
        desc = [tr("(長辺, 短辺, 高さ)", "(long, short, height)") + f" = ({dims[0]:g}, {dims[1]:g}, {dims[2]:g})", ""]
        g += t(20, H - 26, tr("→ 合う", "→ fits") if ok else tr(f"→ 合わない（高さ{h:g} &gt; {MAX[2]:g}）", f"→ does not fit (height {h:g} &gt; {MAX[2]:g})"),
               "verdict " + ("okt" if ok else "badt"), "start")
        x, y = PAD + k * W, PAD + 30
        body.append(card(x, y, W, H, title, desc, f'<g transform="translate(0,6)">{g}</g>'))
    width = COLS * W + PAD * 2
    height = H + PAD * 2 + 30 + 46
    body.insert(0, t(PAD + 6, PAD + 18, tr("max_size = (1.6, 0.8, 0.8)　…（長辺, 短辺, 高さ）の上限 [m]", "max_size = (1.6, 0.8, 0.8): upper limits of (long side, short side, height) [m]"), "name code", "start"))
    body.append(t(PAD + 6, height - 34, tr("破線がmax_size。fitsはアセットの(幅, 奥行き, 高さ)を(長辺, 短辺, 高さ)に並べ替えて比べるので、",
                                          "Dashes: max_size. fits sorts the asset's (width, depth, height) into (long side, "
                                          "short side, height) before comparing,"), "legend", "start"))
    body.append(t(PAD + 6, height - 14, tr("アセットがどちら向きに作られていても同じ結果になる。min_sizeも同じ並びで下限を比べる。",
                                          "so the result is the same whichever way the asset was modeled. min_size "
                                          "compares lower limits the same way."), "legend", "start"))
    return doc(width, height, body)


out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "docs" / "images"
for LANG, suffix in (("en", ""), ("ja", "_ja")):
    for name, fn in (("arch_object_transform", object_transform), ("arch_room_geometry", room_geometry),
                     ("arch_validate_issues", validate_issues), ("arch_size_fits", size_fits)):
        (out / f"{name}{suffix}.svg").write_text(fn(), encoding="utf-8")
