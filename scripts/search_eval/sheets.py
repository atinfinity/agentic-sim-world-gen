import json, sys, random
from PIL import Image, ImageDraw, ImageFont
E = sys.argv[1]
r = {**json.load(open(f"{E}/runs_ab.json")), **json.load(open(f"{E}/runs_cde.json"))}
qs = json.load(open(f"{E}/queries.json"))
corpus = {c["key"]: c for c in json.load(open(f"{E}/corpus.json"))}
f = ImageFont.truetype("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 15, index=0)
T, COLS = 220, 6
for q in qs:
    pool = sorted({k for s in r for k in r[s][q["id"]][:5]})
    random.Random(q["id"]).shuffle(pool)  # hide which system found what
    rows = (len(pool) + COLS - 1) // COLS
    sheet = Image.new("RGB", (COLS * T, rows * (T + 24)), "white")
    d = ImageDraw.Draw(sheet)
    items = []
    for i, k in enumerate(pool):
        c = corpus[k]
        try:
            im = Image.open(f"{E}/thumbs/{c['img']}").convert("RGBA")
            bg = Image.new("RGBA", im.size, (235, 235, 235, 255)); bg.alpha_composite(im); im = bg.convert("RGB")
            im.thumbnail((T - 8, T - 8))
        except Exception:
            im = Image.new("RGB", (T - 8, T - 8), "gray")
        x, y = (i % COLS) * T, (i // COLS) * (T + 24)
        sheet.paste(im, (x + 4, y + 4))
        d.rectangle([x + 4, y + 4, x + 34, y + 26], fill="black")
        d.text((x + 8, y + 5), str(i + 1), font=f, fill="white")
        d.text((x + 4, y + T), k[:30], font=f, fill="black")
        items.append({"n": i + 1, "name": k, "class": c["cls"], "caption": c["caption"][:160],
                      "size_m": [round(b - a, 2) for a, b in zip(*c["bbox"])] if c.get("bbox") else None})
    sheet.save(f"{E}/judge/{q['id']}.jpg", quality=88)
    json.dump({"query": q, "items": items}, open(f"{E}/judge/{q['id']}.json", "w"), ensure_ascii=False, indent=1)
print("ok")
