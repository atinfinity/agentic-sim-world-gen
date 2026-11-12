"""Depth maps of photos with MoGe-2 (https://github.com/microsoft/MoGe, MIT) for agentic_sim_world_gen.depth.

Runs in its own venv (MoGe + torch; not in agentic-sim-world-gen's environment or Isaac Sim's Python):

  python -m venv ~/.venvs/moge && ~/.venvs/moge/bin/pip install torch torchvision pillow numpy scipy \
      opencv-python-headless huggingface-hub click
  ~/.venvs/moge/bin/pip install --no-deps "git+https://github.com/microsoft/MoGe.git@74fbce054ebed49800de42d0ad0e83495065719a" \
      "git+https://github.com/EasternJournalist/utils3d-moge.git@62f09d58509485564e24d5d9f6aac9ee9ebc0c37" \
      "git+https://github.com/EasternJournalist/pipeline.git@1c511390d90226c00c101f34b84df26a0f8789b4"

Usage: python -I moge_depth.py [--model Ruicheng/moge-2-vits-normal] [--max-side 640] photo.jpg ...
Writes <stem>.depth.npz next to each photo: depth (float16, at most --max-side px; z along the view axis [m],
0 where the model has no answer) and meta (JSON: model, hfov_deg the model estimates, size of the photo).
"""
import argparse
import json
import math
from pathlib import Path

import numpy as np
import torch
from PIL import Image


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("images", nargs="+")
    ap.add_argument("--model", default="Ruicheng/moge-2-vits-normal")
    ap.add_argument("--max-side", type=int, default=640)
    a = ap.parse_args()
    from moge.model.v2 import MoGeModel

    dev = "cuda" if torch.cuda.is_available() else "cpu"
    model = MoGeModel.from_pretrained(a.model).to(dev).eval()
    for path in map(Path, a.images):
        img = Image.open(path).convert("RGB")
        x = torch.tensor(np.asarray(img), dtype=torch.float32, device=dev).permute(2, 0, 1) / 255
        with torch.no_grad():
            r = model.infer(x, use_fp16=dev == "cuda")
        d = r["depth"].float().cpu().numpy()
        d[~np.isfinite(d)] = 0
        k = r["intrinsics"].float().cpu().numpy()
        hfov = math.degrees(2 * math.atan(0.5 / float(k[0, 0])))  # normalised intrinsics
        s = a.max_side / max(d.shape)
        if s < 1:
            d = np.asarray(Image.fromarray(d).resize((round(d.shape[1] * s), round(d.shape[0] * s)), Image.NEAREST))
        meta = {"model": a.model, "hfov_deg": round(hfov, 2), "size": list(img.size)}
        out = path.with_name(path.stem + ".depth.npz")
        np.savez_compressed(out, depth=d.astype(np.float16), meta=np.array(json.dumps(meta)))
        print(out, meta, flush=True)


if __name__ == "__main__":
    main()
