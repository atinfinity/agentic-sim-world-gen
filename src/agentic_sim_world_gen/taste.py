"""Taste check: find the chosen asset that does not go with the rest.

Objects are picked one by one, so each can match its query while the set clashes (a white desk and
white cabinets next to a blue steel rack). Each selected asset's thumbnail is embedded with an image
model (SigLIP2 by default), each object is compared with the mean of the others (leave one out), and
an object well below the rest is reported as an outlier together with candidates of that object that
fit the others better. It is a warning for the user to decide on: nothing is swapped.

The model is optional (extra `taste`: torch + transformers, and the model already in the Hugging Face
cache; nothing is downloaded unless AGENTIC_SIM_WORLD_GEN_TASTE_DOWNLOAD=1). Without it the check is skipped and
says why. Embeddings are cached under AGENTIC_SIM_WORLD_GEN_CACHE/taste/<model>/ by thumbnail URL, so status (which
never loads the model) can report from the cache once check_taste has embedded the thumbnails.

Tests plug in a fake embedder (anything with `name` and `images(list[bytes]) -> list[vector]`).
"""

from __future__ import annotations

import hashlib
import importlib.util
import math
import os
import re
from array import array
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol, Sequence

from agentic_sim_world_gen import atomic

MODEL = "google/siglip2-so400m-patch14-384"
MIN_OBJECTS = 3  # fewer objects than this have no "rest" to compare with
# Features are residuals: each selected embedding minus the mean of that object's candidates, so what is
# compared is how the pick differs from the usual look of its kind (raw embeddings mostly say "chair" vs
# "desk"). An object is an outlier when its fit (cosine to the mean of the others) is below the median
# fit of the set by more than MARGIN and one of its candidates fits the others at least FLAG_GAIN better
# (so every flag comes with a way out). Tuned on constructed study / living-room sets
# (scripts/taste_eval): a high bar, since a false flag costs the user a question.
MARGIN = 0.10
FLAG_GAIN = 0.30
MAX_OUTLIERS_SHARE = 1 / 3  # never call more than a third of the set "off"
ALTERNATIVES = 3
BETTER_BY = 0.10  # an alternative listed must fit the others at least this much better than the current pick
MIN_POOL = 3  # candidates needed for an object's own mean; fewer use the mean of every candidate
# The baseline of a residual is a wider search than the scene keeps (DEFAULT_CANDIDATES = 6): with only the
# scene's few candidates every pick sits near their mean and every residual is ~0, so nothing is ever
# flagged in a real scene. check_taste searches each object's query + alt_queries for up to POOL matches
# (same size limits, weak hits dropped as in the scene's search) and keeps them in the scene directory
# (POOL_FILE); the alternatives offered may come from that pool too.
POOL = 40
# An object with fewer assets of its kind than this (pick + right-kind candidates + pool) is never flagged:
# the mean of 3 sofas says little about what a sofa usually looks like, and its residual is mostly shape
# (the long sofa among them was flagged in half the coherent living rooms). It still counts as context.
MIN_KIND = 6
POOL_FILE = "taste_pool.json"

# words for "what it looks like" (SigLIP text prompts); the first match per object is shown in Japanese
STYLE_WORDS: list[tuple[str, str]] = [
    ("wooden", "木目"), ("white", "白"), ("black", "黒"), ("gray", "グレー"), ("silver metal", "金属（銀）"),
    ("blue", "青"), ("yellow", "黄"), ("red", "赤"), ("green", "緑"), ("orange", "オレンジ"), ("beige", "ベージュ"),
    ("glass", "ガラス"), ("fabric upholstered", "布張り"), ("leather", "革"), ("plastic", "プラスチック"),
    ("industrial steel", "工業用のスチール"), ("medical", "医療用"), ("modern minimalist", "モダン"),
    ("rustic", "素朴"), ("classic", "クラシック"),
]


class Embedder(Protocol):
    name: str

    def images(self, blobs: Sequence[bytes]) -> list[list[float]]: ...


# --- vectors ------------------------------------------------------------------


def _norm(v: Sequence[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _mean(vs: Sequence[Sequence[float]]) -> list[float]:
    n = len(vs)
    return [sum(c) / n for c in zip(*vs)]


def cos(a: Sequence[float], b: Sequence[float]) -> float:
    return _dot(_norm(a), _norm(b))


# --- cache --------------------------------------------------------------------


def _cache_root() -> Path:
    return Path(os.environ.get("AGENTIC_SIM_WORLD_GEN_CACHE", "~/.cache/agentic-sim-world-gen")).expanduser() / "taste"


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name)


class Cache:
    """float32 vectors on disk, one file per key (thumbnail URL or text prompt)."""

    def __init__(self, model: str, root: Path | None = None):
        self.dir = (root or _cache_root()) / _slug(model)

    def _path(self, key: str) -> Path:
        return self.dir / (hashlib.sha1(key.encode()).hexdigest() + ".f32")

    def get(self, key: str) -> list[float] | None:
        try:
            a = array("f")
            a.frombytes(self._path(key).read_bytes())
            return list(a)
        except (OSError, ValueError):
            return None

    def put(self, key: str, vec: Sequence[float]) -> None:
        atomic.write(self._path(key), array("f", vec).tobytes())


def fetch(url: str, timeout_s: float = 20) -> bytes | None:
    """Thumbnail bytes from file:// or http(s); None when it cannot be read."""
    if url.startswith("file://"):
        from urllib.parse import unquote, urlparse

        try:
            return Path(unquote(urlparse(url).path)).read_bytes()
        except OSError:
            return None
    import requests

    try:
        from agentic_sim_world_gen import UA

        r = requests.get(url, headers=UA, timeout=timeout_s)
        if r.ok and r.headers.get("content-type", "").startswith("image/"):
            return r.content
    except requests.RequestException:
        pass
    return None


def embed_urls(urls: Sequence[str], embedder: Embedder, cache: Cache | None = None,
               get: Callable[[str], bytes | None] = fetch, batch: int = 8) -> dict[str, list[float]]:
    """Embeddings of the thumbnails (cached ones are not fetched again); unreadable URLs are left out."""
    cache = cache or Cache(embedder.name)
    out: dict[str, list[float]] = {}
    missing = []
    for u in dict.fromkeys(urls):
        if (v := cache.get(u)) is not None:
            out[u] = v
        else:
            missing.append(u)
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(8) as ex:  # thumbnails are small; the round trips dominate
        todo = [(u, b) for u, b in zip(missing, ex.map(get, missing)) if b is not None]
    for i in range(0, len(todo), batch):
        part = todo[i : i + batch]
        for (u, _), v in zip(part, embedder.images([b for _, b in part])):
            cache.put(u, v)
            out[u] = list(v)
    return out


def embed_texts(texts: Sequence[str], embedder: Any, cache: Cache | None = None) -> dict[str, list[float]]:
    """Text embeddings (only for embedders with `texts`; else empty)."""
    if not hasattr(embedder, "texts"):
        return {}
    cache = cache or Cache(embedder.name)
    out = {t: v for t in texts if (v := cache.get("text:" + t)) is not None}
    todo = [t for t in texts if t not in out]
    if todo:
        for t, v in zip(todo, embedder.texts(todo)):
            cache.put("text:" + t, v)
            out[t] = list(v)
    return out


# --- the model (optional) -----------------------------------------------------


def model_name() -> str | None:
    """The configured model, None when the check is turned off (AGENTIC_SIM_WORLD_GEN_TASTE=0)."""
    v = os.environ.get("AGENTIC_SIM_WORLD_GEN_TASTE", "").strip()
    if v.lower() in ("0", "off", "false", "no"):
        return None
    return v if v and v.lower() not in ("1", "on", "true", "yes") else MODEL


def _hf_cached(model: str) -> bool:
    hub = os.environ.get("HF_HUB_CACHE") or os.path.join(
        os.environ.get("HF_HOME", os.path.expanduser("~/.cache/huggingface")), "hub")
    d = Path(hub) / ("models--" + model.replace("/", "--")) / "snapshots"
    return d.is_dir() and any(d.iterdir())


def unavailable() -> str | None:
    """Why the model cannot be used here (None = it can). Cheap: nothing is imported or loaded."""
    m = model_name()
    if m is None:
        return "turned off (AGENTIC_SIM_WORLD_GEN_TASTE=0)"
    missing = [p for p in ("torch", "transformers") if importlib.util.find_spec(p) is None]
    if missing:
        return f"{' and '.join(missing)} not installed (optional: uv sync again with --extra taste added)"
    if not Path(m).is_dir() and not _hf_cached(m) and os.environ.get("AGENTIC_SIM_WORLD_GEN_TASTE_DOWNLOAD") != "1":
        return (f"model {m} is not in the Hugging Face cache (download it once, or set AGENTIC_SIM_WORLD_GEN_TASTE_DOWNLOAD=1 "
                "to let check_taste fetch it)")
    return None


class SiglipEmbedder:
    """SigLIP / SigLIP2 image and text features (transformers). CPU unless AGENTIC_SIM_WORLD_GEN_TASTE_DEVICE is set."""

    def __init__(self, model: str = MODEL, device: str | None = None):
        self.name = model
        self.device = device or os.environ.get("AGENTIC_SIM_WORLD_GEN_TASTE_DEVICE", "cpu")
        self._m = None

    def _load(self):
        if self._m is None:
            from transformers import AutoModel, AutoProcessor

            local = os.environ.get("AGENTIC_SIM_WORLD_GEN_TASTE_DOWNLOAD") != "1"
            self._m = AutoModel.from_pretrained(self.name, local_files_only=local).to(self.device).eval()
            self._p = AutoProcessor.from_pretrained(self.name, local_files_only=local)
        return self._m, self._p

    def images(self, blobs: Sequence[bytes]) -> list[list[float]]:
        import io

        import torch
        from PIL import Image

        m, p = self._load()
        ims = []
        for b in blobs:
            try:
                im = Image.open(io.BytesIO(b)).convert("RGBA")
                bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
                bg.alpha_composite(im)
                ims.append(bg.convert("RGB"))
            except Exception:
                ims.append(Image.new("RGB", (256, 256), "white"))
        with torch.no_grad():
            x = p(images=ims, return_tensors="pt").to(self.device)
            f = m.get_image_features(**{k: v for k, v in x.items() if k != "input_ids"})
            f = getattr(f, "pooler_output", f)
        return [_norm(r) for r in f.float().cpu().tolist()]

    def texts(self, texts: Sequence[str]) -> list[list[float]]:
        import torch

        m, p = self._load()
        with torch.no_grad():
            x = p(text=list(texts), padding="max_length", max_length=64, truncation=True, return_tensors="pt").to(self.device)
            f = m.get_text_features(input_ids=x["input_ids"])
            f = getattr(f, "pooler_output", f)
        return [_norm(r) for r in f.float().cpu().tolist()]


def load_embedder() -> tuple[Embedder | None, str | None]:
    """(embedder, None) or (None, why it is skipped)."""
    if why := unavailable():
        return None, why
    return SiglipEmbedder(model_name() or MODEL), None


# --- the check ----------------------------------------------------------------


@dataclass
class Item:
    """One object of the set: its selected thumbnail and its candidates (index, name, url, weak). pool: the
    wider search for the same object (uri, name, url, weak), the baseline of its residual and a source of
    alternatives; empty = the candidates alone."""

    id: str
    label: str
    selected: int
    candidates: list[tuple[int, str, str | None, bool]] = field(default_factory=list)
    pool: list[tuple[str, str, str | None, bool]] = field(default_factory=list)

    @property
    def url(self) -> str | None:
        return next((u for i, _, u, _ in self.candidates if i == self.selected), None)


def items_of(scene: Any) -> list[Item]:
    """Objects that make up the look: placed with an asset; not decor, not stocked products (fill), not
    door assets in openings. Array blocks count once."""
    out = []
    by_id = {o.id: o for o in scene.spec.objects}
    for p in scene.placements:
        o = by_id.get(p.object_id)
        if o is None or not p.candidates or o.decor or o.fill is not None or o.opening:
            continue
        out.append(Item(o.id, o.label or o.id, p.selected,
                        [(i, c.get("name") or "", c.get("thumbnail_url"), bool((c.get("match") or {}).get("weak")))
                         for i, c in enumerate(p.candidates)]))
    return out


def fits(vecs: Sequence[Sequence[float]]) -> list[float]:
    """Each vector's cosine to the mean of the others (leave one out)."""
    vs = [_norm(v) for v in vecs]
    total = [sum(c) for c in zip(*vs)]
    n = len(vs)
    return [_dot(v, _norm([(t - x) / (n - 1) for t, x in zip(total, v)])) for v in vs]


def _median(xs: Sequence[float]) -> float:
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def outliers(vecs: Sequence[Sequence[float]], margin: float = MARGIN) -> tuple[list[float], list[int]]:
    """(fit per vector, indices of the outliers, worst first)."""
    f = fits(vecs)
    med = _median(f)
    low = sorted((i for i, x in enumerate(f) if x < med - margin), key=lambda i: f[i])
    return f, low[: max(1, int(len(f) * MAX_OUTLIERS_SHARE))]


def _looks(vec: Sequence[float], others: Sequence[Sequence[float]], words: dict[str, list[float]]) -> tuple[list[str], list[str]]:
    """Style words this image has more than the others, and the others more than it (Japanese)."""
    if not words:
        return [], []
    ja = dict(STYLE_WORDS)
    diff = []
    for w, t in words.items():
        mine = _dot(_norm(vec), t)
        theirs = sum(_dot(_norm(o), t) for o in others) / len(others)
        diff.append((mine - theirs, ja.get(w.removeprefix(_PROMPT_HEAD).removesuffix(_PROMPT_TAIL), w)))
    diff.sort()
    return [w for d, w in diff[::-1][:2] if d > 0], [w for d, w in diff[:2] if d < 0]


_PROMPT_HEAD, _PROMPT_TAIL = "a photo of ", " furniture"


def prompts() -> list[str]:
    return [f"{_PROMPT_HEAD}{w}{_PROMPT_TAIL}" for w, _ in STYLE_WORDS]


def _features(items: Sequence[Item], vecs: dict[str, list[float]], residual: bool = True):
    """feature(item, url): the normalised embedding, minus the mean of the item's candidates (residual)."""
    def mean_of(urls):
        vs = [_norm(vecs[u]) for u in urls if u in vecs]
        return _mean(vs) if vs else None

    every = mean_of([u for it in items for _, _, u, _ in it.candidates if u])
    means = {}
    for it in items:
        urls = list(dict.fromkeys([u for _, _, u, _ in it.candidates if u and u in vecs]
                                  + [u for _, _, u, _ in it.pool if u and u in vecs]))
        means[it.id] = mean_of(urls) if len(urls) >= MIN_POOL else every

    def feat(it: Item, url: str) -> list[float]:
        v = _norm(vecs[url])
        m = means.get(it.id)
        return [a - b for a, b in zip(v, m)] if residual and m else v

    return feat


def analyze(items: Sequence[Item], vecs: dict[str, list[float]], words: dict[str, list[float]] | None = None,
            margin: float = MARGIN, gain: float = FLAG_GAIN, residual: bool = True,
            min_kind: int | None = None) -> dict[str, Any]:
    """The taste report for a set of objects whose thumbnails are embedded in `vecs` (URL -> vector).
    min_kind: an object is flagged only when its candidates + pool hold at least this many assets of its kind."""
    used = [it for it in items if it.url and it.url in vecs]
    missing = [it.id for it in items if not (it.url and it.url in vecs)]
    out: dict[str, Any] = {"objects": len(used)}
    if missing:
        out["not_embedded"] = missing
    if len(used) < MIN_OBJECTS:
        out["note"] = f"fewer than {MIN_OBJECTS} objects with a thumbnail: nothing to compare"
        out["outliers"] = []
        return out
    feat = _features(used, vecs, residual)
    sel = [feat(it, it.url) for it in used]  # type: ignore[arg-type]
    f = fits(sel)
    med = _median(f)
    out["median_fit"] = round(med, 3)
    out["fit"] = {it.id: round(x, 3) for it, x in zip(used, f)}
    rows = []
    for k in sorted((k for k, x in enumerate(f) if x < med - margin), key=lambda k: f[k]):
        it = used[k]
        rest = [v for j, v in enumerate(sel) if j != k]
        centre = _norm(_mean([_norm(v) for v in rest]))
        alts = []
        seen = {it.url}
        for i, name, url, weak in it.candidates:
            if i == it.selected or not url or url not in vecs or url in seen:
                continue
            seen.add(url)
            g = _dot(_norm(feat(it, url)), centre)
            alts.append({"index": i, "name": name, "fit": round(g, 3), **({"weak": True} if weak else {})})
        for uri, name, url, weak in it.pool:  # not among the scene's candidates: select_asset(uri=) adds it
            if not url or url not in vecs or url in seen:
                continue
            seen.add(url)
            g = _dot(_norm(feat(it, url)), centre)
            alts.append({"index": None, "uri": uri, "name": name, "fit": round(g, 3), **({"weak": True} if weak else {})})
        weak_now = next((w for i, _, _, w in it.candidates if i == it.selected), False)
        kind = [a["fit"] for a in alts if weak_now or not a.get("weak")]
        best = max(kind, default=None)
        # too few of its kind to say what is usual for it: its residual is noise
        if len(kind) + 1 < (MIN_KIND if min_kind is None else min_kind):
            continue
        if best is None or best < f[k] + gain:  # no candidate of the right kind fits clearly better: say nothing
            continue
        alts = sorted((a for a in alts if a["fit"] >= f[k] + BETTER_BY), key=lambda a: (a.get("weak", False), -a["fit"]))
        looks, others_look = _looks(_norm(vecs[it.url]), [_norm(vecs[u.url]) for j, u in enumerate(used) if j != k],  # type: ignore[index]
                                    words or {})
        rows.append({"object": it.id, "label": it.label, "asset": next(n for i, n, _, _ in it.candidates if i == it.selected),
                     "fit": round(f[k], 3), "median_fit": out["median_fit"],
                     **({"looks": looks} if looks else {}), **({"others_look": others_look} if others_look else {}),
                     "alternatives": alts[:ALTERNATIVES]})
    out["outliers"] = rows[: max(1, int(len(f) * MAX_OUTLIERS_SHARE))]
    return out


def closeness(cands: Sequence[Sequence[float]], chosen: Sequence[Sequence[float]]) -> list[float]:
    """For re-ranking: each candidate's cosine to the mean of the already chosen objects, centred on the
    candidates' mean (so the term only reorders, it does not shift every score)."""
    if not chosen or not cands:
        return [0.0] * len(cands)
    c = _norm(_mean([_norm(v) for v in chosen]))
    g = [_dot(_norm(v), c) for v in cands]
    m = sum(g) / len(g)
    return [x - m for x in g]


def pool_key(o: Any) -> str:
    """What an object's wider search depends on (a changed query or size limit makes its pool stale)."""
    import json

    return hashlib.sha1(json.dumps([o.query, list(o.alt_queries), o.min_size, o.max_size, POOL,
                                    os.environ.get("AGENTIC_SIM_WORLD_GEN_SOURCES", "")], default=list).encode()).hexdigest()[:16]


def report(scene: Any, embedder: Embedder | None = None, cache_only: bool = True,
           get: Callable[[str], bytes | None] = fetch,
           pools: dict[str, list[dict[str, Any]]] | None = None) -> dict[str, Any]:
    """The taste report of a scene. cache_only=True (status) uses only cached embeddings and never loads
    the model; otherwise the embedder fills the cache first (check_taste). pools: object id -> the candidate
    dicts of its wider search (the residual baseline); None = each object's own candidates (old behaviour);
    with cache_only, an object missing from `pools` means check_taste has not seen it yet."""
    items = items_of(scene)
    if len(items) < MIN_OBJECTS:
        return {"objects": len(items), "outliers": [], "note": f"fewer than {MIN_OBJECTS} objects to compare"}
    if embedder is None:
        name = model_name()
        if name is None or (why := unavailable()):
            return {"skipped": why if name else "turned off (AGENTIC_SIM_WORLD_GEN_TASTE=0)"}
    else:
        name = embedder.name
    if pools is not None:
        if cache_only and any(it.id not in pools for it in items):
            return {"checked": False, "hint": "call check_taste to search and embed the thumbnails (first run loads the model)"}
        for it in items:
            it.pool = [(c.get("uri") or c.get("id") or "", c.get("name") or "", c.get("thumbnail_url"),
                        bool((c.get("match") or {}).get("weak"))) for c in pools.get(it.id) or []]
    cache = Cache(name)
    sel_urls = [it.url for it in items if it.url]
    all_urls = list(dict.fromkeys([u for it in items for _, _, u, _ in it.candidates if u]
                                  + [u for it in items for _, _, u, _ in it.pool if u]))
    if cache_only:
        vecs = {u: v for u in all_urls if (v := cache.get(u)) is not None}
        if any(u not in vecs for u in sel_urls):
            return {"checked": False, "hint": "call check_taste to search and embed the thumbnails (first run loads the model)"}
        words = {t: v for t in prompts() if (v := cache.get("text:" + t)) is not None}
    else:
        assert embedder is not None
        vecs = embed_urls(all_urls, embedder, cache, get)
        try:
            words = embed_texts(prompts(), embedder, cache)
        except Exception:  # style words are only for the explanation
            words = {}
    out = {"model": name, **analyze(items, vecs, words)}
    if out["outliers"]:
        out["hint"] = ("a warning, not an error: tell the user which object looks out of place next to the others "
                       "(looks / others_look) and offer the alternatives (view_candidates shows them); swap only "
                       "when the user picks one (select_asset with the index, or uri when index is null). "
                       "Leaving it as is is fine.")
    return out
