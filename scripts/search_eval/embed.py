"""Embed corpus thumbnails (SigLIP2) and corpus text (multilingual-e5), plus all queries (en + ja)."""
import json, re, sys
import numpy as np, torch
from PIL import Image
from transformers import AutoModel, AutoProcessor, AutoTokenizer

E = sys.argv[1]
corpus = json.load(open(f"{E}/corpus.json"))
queries = json.load(open(f"{E}/queries.json"))
dev = "cuda"

def norm(x): return x / np.linalg.norm(x, axis=1, keepdims=True)

# --- C: SigLIP2 image/text
mid = "google/siglip2-so400m-patch14-384"
model = AutoModel.from_pretrained(mid, torch_dtype=torch.float16).to(dev).eval()
proc = AutoProcessor.from_pretrained(mid)
feats = []
with torch.no_grad():
    for i in range(0, len(corpus), 32):
        ims = []
        for c in corpus[i:i + 32]:
            try:
                im = Image.open(f"{E}/thumbs/{c['img']}").convert("RGBA")
                bg = Image.new("RGBA", im.size, (255, 255, 255, 255)); bg.alpha_composite(im); ims.append(bg.convert("RGB"))
            except Exception:
                ims.append(Image.new("RGB", (256, 256), "white"))
        x = proc(images=ims, return_tensors="pt").to(dev)
        f = model.get_image_features(pixel_values=x["pixel_values"].half())
        f = getattr(f, "pooler_output", f)
        feats.append(f.float().cpu().numpy())
        print("img", i, flush=True)
np.save(f"{E}/emb/siglip_img.npy", norm(np.concatenate(feats)))
def stext(texts):
    with torch.no_grad():
        x = proc(text=texts, padding="max_length", max_length=64, truncation=True, return_tensors="pt").to(dev)
        f = model.get_text_features(input_ids=x["input_ids"])
        f = getattr(f, "pooler_output", f)
        return norm(f.float().cpu().numpy())
np.save(f"{E}/emb/siglip_q_en.npy", stext([f"a 3d model of a {q['q']}" for q in queries]))
np.save(f"{E}/emb/siglip_q_en_alt.npy", np.stack([stext([f"a 3d model of a {t}" for t in [q['q'], *q['alt']]]).mean(0) for q in queries]))
np.save(f"{E}/emb/siglip_q_ja.npy", stext([q['ja'] + "の3Dモデル" for q in queries]))
del model; torch.cuda.empty_cache()

# --- D: multilingual-e5 over class + caption + tags + name
mid = "intfloat/multilingual-e5-large"
tok = AutoTokenizer.from_pretrained(mid)
m = AutoModel.from_pretrained(mid, torch_dtype=torch.float16).to(dev).eval()
def e5(texts):
    out = []
    with torch.no_grad():
        for i in range(0, len(texts), 64):
            x = tok(texts[i:i + 64], padding=True, truncation=True, max_length=256, return_tensors="pt").to(dev)
            h = m(**x).last_hidden_state
            msk = x["attention_mask"].unsqueeze(-1)
            out.append(((h * msk).sum(1) / msk.sum(1)).float().cpu().numpy())
    return norm(np.concatenate(out))
def doc(c):
    name = " ".join(re.findall(r"[a-z]+", re.sub(r"^sm_|\.usd.?$", "", c["name"].lower())))
    return f"passage: {c['cls']}. {c['caption']}. {', '.join(c['tags'])}. {name}"
np.save(f"{E}/emb/e5_doc.npy", e5([doc(c) for c in corpus]))
np.save(f"{E}/emb/e5_q_en.npy", e5([f"query: {q['q']}" for q in queries]))
np.save(f"{E}/emb/e5_q_en_alt.npy", e5([f"query: {', '.join([q['q'], *q['alt']])}" for q in queries]))
np.save(f"{E}/emb/e5_q_ja.npy", e5([f"query: {q['ja']}" for q in queries]))
print("done")
