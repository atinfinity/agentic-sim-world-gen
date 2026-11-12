"""Horizontal grouped bars: P@1 strict / lenient per system (PIL, static PNG for the PR)."""
import json, sys
from PIL import Image, ImageDraw, ImageFont
sys.path.insert(0, __import__("os").path.dirname(__file__))
from score import ORDER, metrics
sc = {s: {"p1s": (m := metrics(s))["P@1"], "p1l": m["P@1~"]} for s in ORDER}
LABELS = [("A", "A 現行（英訳＋類義語＋再ランク）"), ("B", "B SimReady Central そのまま"),
          ("C_alt", "C SigLIP2 画像埋め込み"), ("D_alt", "D キャプション埋め込み (e5)"),
          ("E1", "E1 融合 RRF(A, C, D)"), ("E2", "E2 融合＋再ランク"),
          ("F", "F 公開 USD Search（Isaac 6.2）"),
          ("A_ja", "A 日本語のまま"), ("C_ja", "C 日本語のまま"), ("D_ja", "D 日本語のまま"),
          ("F_ja", "F 日本語のまま")]
LABELS = [l for l in LABELS if l[0] in sc]
F = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"; B = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
f, fb, fs = ImageFont.truetype(F, 22), ImageFont.truetype(B, 28), ImageFont.truetype(F, 18)
SURF, INK, INK2, GRID, C1, C2 = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df", "#2a78d6", "#eb6834"
W, LX, PW, ROW, BH = 1400, 430, 860, 64, 22
H = 150 + ROW * len(LABELS) + 100
im = Image.new("RGB", (W, H), SURF); d = ImageDraw.Draw(im)
d.text((40, 24), "検索精度：35 クエリの top-1 が使えるか（P@1）", font=fb, fill=INK)
x = 40
for col, name in ((C1, "厳密（その物）"), (C2, "代用も可")):
    d.rounded_rectangle([x, 80, x + 22, 102], 4, fill=col); d.text((x + 30, 76), name, font=f, fill=INK2); x += 230
top = 130
for v in range(0, 11, 2):
    gx = LX + PW * v / 10
    d.line([gx, top - 6, gx, top + ROW * len(LABELS)], fill=GRID, width=1)
    d.text((gx - 14, top + ROW * len(LABELS) + 8), f"{v/10:.1f}", font=fs, fill=INK2)
for i, (k, lab) in enumerate(LABELS):
    y = top + i * ROW + 8
    if k == "A_ja":
        d.line([40, y - 12, W - 40, y - 12], fill="#b9b8b2", width=1)
    d.text((40, y + 6), lab, font=f, fill=INK)
    for j, (key, col) in enumerate((("p1s", C1), ("p1l", C2))):
        val = sc[k][key]; yy = y + j * (BH + 2)
        d.rounded_rectangle([LX, yy, LX + max(PW * val, 6), yy + BH], 4, fill=col)
        d.text((LX + PW * val + 8, yy - 2), f"{val:.2f}", font=fs, fill=INK2)
d.text((40, H - 66), "A–E：コーパス 3961 件（SimReady Central 3599＋Isaac 6.1 同梱 362）。F：NVIDIA 公開インスタンス（Isaac 6.2 の全 USD 2972 件、部品・シーン込み）。",
       font=fs, fill=INK2)
d.text((40, H - 38), "判定は全システムの上位 5 件をまとめ、どのシステムの結果かを伏せて 0/1/2 で評価。", font=fs, fill=INK2)
im.save(sys.argv[1]); print(sys.argv[1])
