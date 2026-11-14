"""Draw the overview figures for README.md and docs/getting-started.md.

    .venv/bin/python scripts/make_overview_figures.py [out_dir]   # default docs/images; writes English and _ja

- overview_storyboard{,_ja}.jpg: "from the instruction to the world" as five frames, built from the Getting
  Started example (a 10 m² study): gs_candidates.jpg, gs_layout.png and gs_check.jpg (needs Pillow).
- check_results{,_ja}.svg: one side-view card per result key of scripts/isaac/check_core.py (unstable, sunk,
  no_collider, static, and exploded / blew_up), before and after the physics settle. Light / dark via CSS.

The storyboard needs a Japanese font (Hiragino on macOS, Noto Sans CJK on Linux) for the _ja version and for
the Japanese request in both; without one the labels come out as boxes, so make it on a machine that has one.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMAGES = ROOT / "docs" / "images"

# ------------------------------------------------------------------ storyboard (JPEG)

SCENE = "p0_study_room"
CROPS = [None, (0, 0, 640, 519), (0, 0, 546, 443), (140, 0, 1028, 720), None]  # same aspect ratio
SOURCES = [None, "gs_candidates.jpg", "gs_layout.png", "gs_check.jpg", None]
TEXT = {
    "ja": dict(
        instruction=["6畳の書斎を作って。", "机と椅子、壁際に本棚"],  # docs/getting-started.md 4
        title="指示からワールドまで",
        subtitle=f"Getting Startedの例：6畳の書斎（{SCENE}）。②〜④は実際の画面",
        you="あなた → Claude Code", defaults=["広さ・床・照明など指示に無い", "ことは既定で決める"],
        captions=[("日本語で頼む", "Claude Codeに一文で"),
                  ("候補を見て選ぶ", "create_scene → candidates.html"),
                  ("関係で配置・検査", "layout.svg（矢印が正面）"),
                  ("Isaac Simで物理チェック", "check_in_isaac → check.png"),
                  ("ワールドとライセンス", "compose_scene・license_report")],
        files=["Isaac Sim用ワールド", "ライセンスと帰属表示", "物理チェックの結果", "シーンの本体"]),
    "en": dict(
        instruction=["6畳の書斎を作って。", "机と椅子、壁際に本棚"],
        title="From request to world",
        subtitle=f"The Getting Started example: a 10 m² study ({SCENE}). Frames 2–4 are real screens",
        you="You → Claude Code", defaults=["Size, floor, lighting and the rest", "the request leaves out: defaults"],
        captions=[("Ask in words", "one sentence to Claude Code"),
                  ("Pick from candidates", "create_scene → candidates.html"),
                  ("Lay out and check", "layout.svg (arrows = front)"),
                  ("Physics in Isaac Sim", "check_in_isaac → check.png"),
                  ("World and licenses", "compose_scene, license_report")],
        files=["the Isaac Sim world", "licenses and attribution", "physics check results", "the scene itself"]),
}
OUTPUT_FILES = [("scene.usda", True), ("LICENSES.md", True), ("check.json", False),
                ("scene.resolved.json", False)]  # docs/getting-started.md 7
FONT_FILES = {
    "regular": ["/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
                "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
                "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc"],
    "bold": ["/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
             "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
             "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc"],
    "mono": ["/System/Library/Fonts/Menlo.ttc", "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"],
}


def font(kind: str, size: int):
    from PIL import ImageFont

    for f in FONT_FILES[kind] + FONT_FILES["regular"]:
        if Path(f).exists():
            return ImageFont.truetype(f, size)
    return ImageFont.load_default(size)


def cover(img, box, size):
    """Crop `box`, then scale and centre-crop to `size` keeping the aspect ratio (no distortion)."""
    from PIL import Image

    img = img.crop(box)
    tw, th = size
    s = max(tw / img.width, th / img.height)
    img = img.resize((round(img.width * s), round(img.height * s)), Image.LANCZOS)
    x, y = (img.width - tw) // 2, (img.height - th) // 2
    return img.crop((x, y, x + tw, y + th))


def storyboard(out: Path, lang: str) -> None:
    from PIL import Image, ImageDraw

    t_ = TEXT[lang]

    W, M, GAP = 1600, 24, 36
    TW = (W - 2 * M - 4 * GAP) // 5
    TH = round(TW * 503 / 620)
    TOP = 66
    H = TOP + TH + 96
    ink, sub, line, accent = (31, 35, 40), (87, 96, 106), (208, 215, 222), (47, 111, 208)
    im = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(im)
    d.text((M, 18), t_["title"], font=font("bold", 24), fill=ink)
    d.text((M + d.textlength(t_["title"], font=font("bold", 24)) + 20, 25), t_["subtitle"], font=font("regular", 16), fill=sub)
    for k, (src, box, (cap, subcap)) in enumerate(zip(SOURCES, CROPS, t_["captions"])):
        x = M + k * (TW + GAP)
        if src:
            im.paste(cover(Image.open(IMAGES / src).convert("RGB"), box, (TW, TH)), (x, TOP))
        elif k == 0:
            d.rounded_rectangle((x, TOP, x + TW, TOP + TH), 10, fill=(246, 248, 250))
            d.text((x + 16, TOP + 18), t_["you"], font=font("regular", 14), fill=sub)
            f = font("bold", 21)
            bw = max(d.textlength(t, font=f) for t in t_["instruction"]) + 32
            bx0, by0 = x + TW - 14 - bw, TOP + 52
            by1 = by0 + 24 + 34 * len(t_["instruction"])
            d.rounded_rectangle((bx0, by0, x + TW - 14, by1), 16, fill=accent)
            d.polygon([(x + TW - 40, by1), (x + TW - 22, by1), (x + TW - 18, by1 + 16)], fill=accent)
            for i, t in enumerate(t_["instruction"]):
                d.text((bx0 + 16, by0 + 12 + 34 * i), t, font=f, fill="white")
            for i, t in enumerate(t_["defaults"]):
                d.text((x + 16, by1 + 34 + 22 * i), t, font=font("regular", 14), fill=sub)
        else:
            d.rounded_rectangle((x, TOP, x + TW, TOP + TH), 10, fill=(22, 27, 34))
            d.text((x + 16, TOP + 16), f"scenes/{SCENE}/", font=font("mono", 16), fill=(139, 148, 158))
            for i, ((name, main), what) in enumerate(zip(OUTPUT_FILES, t_["files"])):
                y = TOP + 50 + i * 44
                d.text((x + 24, y), name, font=font("mono", 17 if main else 14),
                       fill=(121, 192, 255) if main else (139, 148, 158))
                d.text((x + 24, y + (21 if main else 18)), what, font=font("regular", 13), fill=(201, 209, 217))
        d.rectangle((x, TOP, x + TW - 1, TOP + TH - 1), outline=line)
        # caption: number in a circle, title, sub
        cy = TOP + TH + 30
        d.ellipse((x, cy - 15, x + 30, cy + 15), fill=accent)
        num = str(k + 1)
        fn = font("bold", 18)
        d.text((x + 15 - d.textlength(num, font=fn) / 2, cy - 12), num, font=fn, fill="white")
        d.text((x + 40, cy - 13), cap, font=font("bold", 19), fill=ink)
        d.text((x + 40, cy + 15), subcap, font=font("regular", 15), fill=sub)
        if k < 4:  # arrow to the next frame
            ax, ay = x + TW + 8, TOP + TH // 2
            d.polygon([(ax, ay - 14), (ax + GAP - 16, ay), (ax, ay + 14)], fill=(140, 149, 159))
    im.save(out, quality=85, optimize=True, progressive=True)


# ------------------------------------------------------------------ check results (SVG)

CW, CH = 300, 250  # one card
COLS, ROWS, PAD = 3, 2, 16
FLOOR = 150  # y of the floor line inside a card
CARDS = {  # (key, two description lines): scripts/isaac/check_core.py judge() / run()
    "ja": [
        ("unstable", ["物理を流す間に2cm以上動いた・倒れた", "（物ごとのmax_displacement_mで判定）"]),
        ("sunk", ["落ち着いた後、物の底が床より", "1cmを超えて下にある"]),
        ("no_collider", ["当たり判定が無い", "（ロボットがすり抜ける）"]),
        ("static", ["当たり判定はあるが剛体でない", "（動かない固定物。家具なら問題ない）"]),
        ("exploded", ["位置がNaNになった（重なって吹き飛んだ）", "物ごとの項目で、unstableにも入る"]),
        ("blew_up", ["GPU物理が止まった時、吹き飛んだ物", "（errorは「physics did not run」）"]),
    ],
    "en": [
        ("unstable", ["moved 2 cm or more, or fell over", "(per object: max_displacement_m)"]),
        ("sunk", ["after the settle, its bottom is more", "than 1 cm below the floor"]),
        ("no_collider", ["no collision shape", "(a robot drives through it)"]),
        ("static", ["collides but is no rigid body", "(fixed; fine for furniture)"]),
        ("exploded", ["its pose became NaN (overlaps blew it", "away); per object, also in unstable"]),
        ("blew_up", ["what blew away when GPU physics", "stopped (error: physics did not run)"]),
    ],
}
LEGEND = {
    "ja": ("横から見た図。左が物理を流す前（書いたとおりの位置）、右が流した後"
           "（既定240ステップ＝60 Hzで4秒）。判定はscripts/isaac/check_core.py"),
    "en": ("Side views. Left: before the physics run (as written); right: after it (240 steps by default,"
           " 4 s at 60 Hz). Judged in scripts/isaac/check_core.py"),
}
WORDS = {"ja": dict(before="前", after="後", below="床下", robot="ロボット", fixed="動かない", nopose="位置が取れない"),
         "en": dict(before="before", after="after", below="below", robot="robot", fixed="stays", nopose="no poses")}


def _box(x, y_bottom, w, h, cls="obj", rot=0, cx=None, cy=None):
    """A box standing on y_bottom (SVG y grows down), optionally rotated about (cx, cy)."""
    t = f' transform="rotate({rot} {cx} {cy})"' if rot else ""
    return f'<rect class="{cls}" x="{x}" y="{y_bottom - h}" width="{w}" height="{h}" rx="2"{t}/>'


def _floor():
    return f'<line class="floor" x1="20" y1="{FLOOR}" x2="{CW - 20}" y2="{FLOOR}"/>'


def _arrow(x1, x2, y=95):
    return f'<path class="hint" d="M{x1} {y} H{x2}" marker-end="url(#ah)"/>'


def card_body(key: str, lang: str) -> str:
    w = WORDS[lang]
    L, R = 80, 220  # centres of the before / after halves
    s = _floor() + _arrow(L + 42, R - 42)
    s += f'<text class="dim" x="{L}" y="40">{w["before"]}</text><text class="dim" x="{R}" y="40">{w["after"]}</text>'
    if key == "unstable":  # a tall thin thing falls over
        s += _box(L - 12, FLOOR, 24, 80)
        s += _box(R - 40, FLOOR, 80, 24, "bad")
    elif key == "sunk":
        s += _box(L - 30, FLOOR, 60, 40)
        s += _box(R - 30, FLOOR + 18, 60, 40, "bad")
        s += f'<line class="floor" x1="{R - 40}" y1="{FLOOR}" x2="{R + 40}" y2="{FLOOR}" opacity="0.6"/>'
        s += f'<text class="note" x="{R + 36}" y="{FLOOR + 14}" style="text-anchor:start">{w["below"]}</text>'
    elif key == "no_collider":  # a robot drives through the box
        for c, rx in ((L, L - 46), (R, R - 17)):
            s += _box(c - 22, FLOOR, 44, 50, "ghost")
            s += f'<rect class="robot" x="{rx}" y="{FLOOR - 26}" width="34" height="22" rx="4"/>'
            s += f'<circle class="robot" cx="{rx + 8}" cy="{FLOOR - 4}" r="4"/><circle class="robot" cx="{rx + 26}" cy="{FLOOR - 4}" r="4"/>'
        s += f'<text class="note" x="{L - 29}" y="{FLOOR - 62}">{w["robot"]}</text>'
    elif key == "static":  # a shelf bolted in place: same before and after
        for c in (L, R):
            s += _box(c - 22, FLOOR, 44, 72, "fixed")
            s += f'<path class="pin" d="M{c - 26} {FLOOR + 6} h52"/>'
        s += f'<text class="note" x="{R}" y="{FLOOR - 80}">{w["fixed"]}</text>'
    elif key == "exploded":  # two boxes start inside each other and fly apart
        s += _box(L - 32, FLOOR, 40, 40) + _box(L - 8, FLOOR, 40, 40, "obj2")
        s += f'<rect class="bad" x="{R - 58}" y="38" width="30" height="30" rx="2" transform="rotate(-30 {R - 43} 53)"/>'
        s += f'<rect class="bad" x="{R + 26}" y="30" width="30" height="30" rx="2" transform="rotate(25 {R + 41} 45)"/>'
        s += f'<path class="hint" d="M{R - 8} {FLOOR - 20} L{R - 36} 76 M{R + 8} {FLOOR - 20} L{R + 34} 68"/>'
        s += f'<text class="note" x="{R}" y="{FLOOR - 4}">NaN</text>'
    elif key == "blew_up":  # jointed office chairs side by side with no gap: physics stops, no poses
        for i in range(2):
            x = L - 40 + i * 40
            s += (f'<rect class="obj" x="{x + 4}" y="{FLOOR - 70}" width="8" height="38" rx="2"/>'
                  f'<rect class="obj" x="{x + 4}" y="{FLOOR - 36}" width="32" height="8" rx="2"/>'
                  f'<path class="leg" d="M{x + 20} {FLOOR - 28} V{FLOOR - 6} M{x + 8} {FLOOR - 4} H{x + 32}"/>')
        s += f'<text class="err" x="{R}" y="{FLOOR - 36}">CUDA error</text>'
        s += f'<text class="err" x="{R}" y="{FLOOR - 16}">{w["nopose"]}</text>'
    return s


def check_svg(lang: str) -> str:
    width, height = COLS * CW + PAD * 2, ROWS * CH + PAD * 2 + 30
    font_ = ("'Noto Sans JP','Hiragino Sans','Yu Gothic',sans-serif" if lang == "ja"
             else "system-ui,-apple-system,'Segoe UI',sans-serif")
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" font-family="{font_}">',
             """<style>
  .bg{fill:#ffffff}.card{fill:#f6f8fa;stroke:#d0d7de}.obj{fill:#cfe3ff;stroke:#2f6fd0;stroke-width:1.5}
  .obj2{fill:#ffe1c2;stroke:#c8661b;stroke-width:1.5;fill-opacity:0.85}.bad{fill:#ffd7d5;stroke:#cf222e;stroke-width:1.5}
  .fixed{fill:#d8dee4;stroke:#57606a;stroke-width:1.5}.ghost{fill:none;stroke:#2f6fd0;stroke-width:1.5;stroke-dasharray:4 3}
  .robot{fill:#ffe1c2;stroke:#c8661b;stroke-width:1.5}.pin{stroke:#57606a;stroke-width:4;stroke-linecap:round}
  .leg{stroke:#2f6fd0;stroke-width:3;fill:none;stroke-linecap:round}
  .floor{stroke:#57606a;stroke-width:4;stroke-linecap:round}
  .hint{stroke:#8c959f;stroke-width:1.5;stroke-dasharray:4 4;fill:none}
  .dim{font-size:13px;fill:#57606a;text-anchor:middle}.note{font-size:12px;fill:#57606a;text-anchor:middle}
  .err{font-size:13px;fill:#cf222e;text-anchor:middle;font-weight:600}
  .name{font-size:15px;font-weight:600;fill:#1f2328;font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
  .desc{font-size:13px;fill:#57606a}.legend{font-size:13px;fill:#57606a}#ah path{fill:#8c959f}
  @media (prefers-color-scheme: dark){
    .bg{fill:#0d1117}.card{fill:#161b22;stroke:#30363d}.obj{fill:#1d3b66;stroke:#79b0ff}.obj2{fill:#5a3414;stroke:#f0a060}
    .bad{fill:#5d1a1d;stroke:#ff7b72}.fixed{fill:#30363d;stroke:#8b949e}.ghost{stroke:#79b0ff}
    .robot{fill:#5a3414;stroke:#f0a060}.pin,.floor{stroke:#8b949e}.leg{stroke:#79b0ff}
    .name{fill:#e6edf3}.dim,.desc,.legend,.note{fill:#8b949e}.err{fill:#ff7b72}
  }
</style>""",
             '<defs><marker id="ah" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z"/></marker></defs>',
             f'<rect class="bg" width="{width}" height="{height}" rx="8"/>']
    for k, (key, desc) in enumerate(CARDS[lang]):
        x, y = PAD + (k % COLS) * CW, PAD + (k // COLS) * CH
        lines = "".join(f'<text class="desc" x="20" y="{CH - 42 + 18 * i}">{t}</text>' for i, t in enumerate(desc))
        parts.append(f'<g transform="translate({x},{y})"><rect class="card" x="6" y="6" width="{CW-12}" height="{CH-12}" rx="8"/>'
                     f'<text class="name" x="20" y="30">{key}</text>{lines}'
                     f'<g transform="translate(0,12)">{card_body(key, lang)}</g></g>')
    parts.append(f'<text class="legend" x="{PAD+6}" y="{height-16}">{LEGEND[lang]}</text></svg>')
    return "\n".join(parts) + "\n"


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else IMAGES
    for lang, suffix in (("en", ""), ("ja", "_ja")):
        (out / f"check_results{suffix}.svg").write_text(check_svg(lang), encoding="utf-8")
        storyboard(out / f"overview_storyboard{suffix}.jpg", lang)
