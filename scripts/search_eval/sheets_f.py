import json, sys, random, hashlib, urllib.request
from PIL import Image, ImageDraw, ImageFont
E = sys.argv[1]
r = json.load(open(f"{E}/runs_f.json")); qs = json.load(open(f"{E}/queries.json"))
f = ImageFont.truetype("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 15, index=0)
T, COLS = 220, 5
for q in qs:
    rows_ = {h["url"]: h for s in r for h in r[s][q["id"]]}
    pool = sorted(rows_); random.Random(q["id"]).shuffle(pool)
    sheet = Image.new("RGB", (COLS * T, ((len(pool)+COLS-1)//COLS) * (T + 24)), "white"); d = ImageDraw.Draw(sheet)
    items = []
    for i, u in enumerate(pool):
        h = rows_[u]; p = f"{E}/f_thumbs/{hashlib.md5(u.encode()).hexdigest()}.png"
        try:
            try: im = Image.open(p)
            except Exception:
                open(p, "wb").write(urllib.request.urlopen(h["thumb"], timeout=30).read()); im = Image.open(p)
            im = im.convert("RGBA"); bg = Image.new("RGBA", im.size, (235,235,235,255)); bg.alpha_composite(im); im = bg.convert("RGB"); im.thumbnail((T-8, T-8))
        except Exception:
            im = Image.new("RGB", (T-8, T-8), "gray")
        x, y = (i % COLS) * T, (i // COLS) * (T + 24)
        sheet.paste(im, (x+4, y+4)); d.rectangle([x+4, y+4, x+34, y+26], fill="black"); d.text((x+8, y+5), str(i+1), font=f, fill="white")
        d.text((x+4, y+T), h["name"][:30], font=f, fill="black")
        items.append({"n": i+1, "url": u, "name": h["name"], "path": u.split("/Isaac/6.2/")[-1], "caption": h["caption"][:160]})
    sheet.save(f"{E}/judge_f/{q['id']}.jpg", quality=88)
    json.dump({"query": q, "items": items}, open(f"{E}/judge_f/{q['id']}.json", "w"), ensure_ascii=False, indent=1)
print("ok")
