"""Draw the built-in floor / wall textures (src/agentic_sim_world_gen/data/textures/).

Every texture tiles seamlessly over its preset's tile_m (scene/looks.py TEXTURES): planks with grain and
staggered joints, tatami with borders and weave, tiles with grout, carpet tiles, concrete, brick, metal panels.
Each preset gets `<name>.jpg` (base color, sRGB) and `<name>_normal.png` (tangent space, OpenGL: green = +t, half size),
drawn with numpy only so they are ours to ship (same license as this repository).

    python scripts/make_textures.py            # all
    python scripts/make_textures.py brick      # some
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from agentic_sim_world_gen.scene.looks import SURFACES, TEXTURES, TEXTURE_DIR  # noqa: E402

# ---------------------------------------------------------------- helpers (all periodic on the image)


def noise(n: int, rng, beta: float = 2.0, stretch: tuple[float, float] = (1.0, 1.0), lo: int = 1) -> np.ndarray:
    """Periodic 1/f^beta noise, mean 0, std 1. stretch (x, y): a smaller value makes features longer across that axis (0.1, 1) = long along y."""
    k = np.fft.fftfreq(n) * n
    kx, ky = np.meshgrid(k * stretch[0], k * stretch[1])
    f = np.hypot(kx, ky)
    amp = np.where(f >= lo, 1.0 / np.maximum(f, 1e-6) ** (beta / 2), 0.0)
    w = np.fft.ifft2(np.fft.fft2(rng.standard_normal((n, n))) * amp).real
    return (w - w.mean()) / (w.std() + 1e-12)


def lines(n: int, positions_px, width_px: float, axis: int) -> np.ndarray:
    """1 on lines (soft edge) at the given positions along an axis (0 = x / columns, 1 = y / rows), periodic."""
    c = np.arange(n) + 0.5
    d = np.full(n, np.inf)
    for p in positions_px:
        e = np.abs((c - p + n / 2) % n - n / 2)
        d = np.minimum(d, e)
    v = np.clip(1.0 - (d - width_px / 2), 0.0, 1.0)  # 1 inside, 1 px ramp
    return v[None, :].repeat(n, 0) if axis == 0 else v[:, None].repeat(n, 1)


def to_srgb8(lin: np.ndarray) -> np.ndarray:
    c = np.clip(lin, 0.0, 1.0)
    s = np.where(c <= 0.0031308, 12.92 * c, 1.055 * c ** (1 / 2.4) - 0.055)
    return np.round(s * 255).astype(np.uint8)


def normal_map(height_m: np.ndarray, px_m: float) -> np.ndarray:
    """Height (meters, row 0 = top of the image = high t) -> tangent-space normal map (OpenGL)."""
    dx = (np.roll(height_m, -1, 1) - np.roll(height_m, 1, 1)) / (2 * px_m)
    dy = -(np.roll(height_m, -1, 0) - np.roll(height_m, 1, 0)) / (2 * px_m)  # +t is up the image
    nrm = np.stack([-dx, -dy, np.ones_like(dx)], -1)
    nrm /= np.linalg.norm(nrm, axis=-1, keepdims=True)
    return np.round((nrm * 0.5 + 0.5) * 255).astype(np.uint8)


def tint(color, field: np.ndarray) -> np.ndarray:
    """Linear color times a multiplier field (mean ~1)."""
    return np.asarray(color, float)[None, None, :] * field[..., None]


def mix(a: np.ndarray, b, m: np.ndarray) -> np.ndarray:
    b = np.asarray(b, float)
    if b.ndim == 1:
        b = b[None, None, :]
    return a * (1 - m[..., None]) + b * m[..., None]


# ---------------------------------------------------------------- patterns: (linear rgb, height in meters)


def wood(color, n, tile, rng):
    """Planks 0.15 m wide along x (= the s direction); each strip has two boards per tile with staggered end joints."""
    strips = round(tile / 0.15)
    sw = n / strips
    grain = noise(n, rng, 2.2, stretch=(1.0, 0.06)) * 0.10 + noise(n, rng, 1.2, stretch=(1.0, 0.1)) * 0.05
    field = 1.0 + grain
    seam = lines(n, [i * sw for i in range(strips)], 1.4, 1)
    cols = np.arange(n)
    for i in range(strips):
        y0, y1 = int(round(i * sw)), int(round((i + 1) * sw))
        j0 = rng.uniform(0, n)
        joints = [j0, (j0 + n / 2) % n]
        board = (((cols - j0) % n) >= n / 2).astype(int)
        tone = rng.normal(0, 0.08, 2)
        field[y0:y1, :] *= 1.0 + tone[board][None, :]
        seam[y0:y1] = np.maximum(seam[y0:y1], lines(n, joints, 1.4, 0)[y0:y1])
    lin = mix(tint(color, field), np.asarray(color) * 0.35, seam * 0.85)
    height = -seam * 0.0006 + grain * 0.00004
    return lin, height


def tatami(color, n, tile, rng):
    """Two mats 0.91 x 1.82 m side by side (long side along y), dark borders (heri) on the long sides, weave."""
    px = tile / n
    weave_p = 0.012 / px
    y = np.arange(n)
    weave = 0.5 + 0.5 * np.cos(2 * np.pi * y / weave_p)
    field = 1.0 + (weave[:, None] - 0.5) * 0.16 + noise(n, rng, 1.0) * 0.04 + noise(n, rng, 2.5) * 0.05
    lin = tint(color, field)
    heri_w = 0.03 / px
    heri = lines(n, [heri_w / 2, n / 2 - heri_w / 2, n / 2 + heri_w / 2, n - heri_w / 2], heri_w, 0)
    heri_c = (0.035, 0.045, 0.040)
    lin = mix(lin, heri_c, heri)
    gap = lines(n, [0, n / 2], 1.2, 0) + lines(n, [0], 1.2, 1)
    lin = mix(lin, np.asarray(color) * 0.3, np.clip(gap, 0, 1) * 0.7)
    height = (weave[:, None] - 0.5) * 0.0004 * (1 - heri) - np.clip(gap, 0, 1) * 0.001
    return lin, height


def grid_tiles(color, n, tile, rng, size, grout_m, grout_c, tone, speck=0.02, groove=0.0008):
    k = round(tile / size)
    p = n / k
    pos = [i * p for i in range(k)]
    grout = np.clip(lines(n, pos, grout_m / (tile / n), 0) + lines(n, pos, grout_m / (tile / n), 1), 0, 1)
    field = 1.0 + noise(n, rng, 1.0) * speck + noise(n, rng, 2.5) * speck
    idx = (np.arange(n) // p).astype(int)
    tones = rng.normal(0, tone, (k, k))
    field *= 1.0 + tones[idx[:, None], idx[None, :]]
    lin = mix(tint(color, field), grout_c, grout)
    return lin, -grout * groove, idx


def tiles_white(color, n, tile, rng):
    lin, h, _ = grid_tiles(color, n, tile, rng, 0.30, 0.004, (0.35, 0.35, 0.33), 0.03)
    return lin, h


def carpet(color, n, tile, rng):
    """50 cm carpet tiles laid in quarter turns (alternate tiles a little lighter), fine pile."""
    lin, h, idx = grid_tiles(color, n, tile, rng, 0.5, 0.0015, np.asarray(color) * 0.6, 0.0, speck=0.0, groove=0.0004)
    checker = ((idx[:, None] + idx[None, :]) % 2) * 0.08 - 0.04
    pile = noise(n, rng, 0.2) * 0.10 + noise(n, rng, 1.5) * 0.05
    lin = lin * (1.0 + checker + pile)[..., None]
    return lin, h + pile * 0.0002


def concrete(color, n, tile, rng):
    blot = noise(n, rng, 3.0) * 0.08 + noise(n, rng, 1.6) * 0.05
    pores = (noise(n, rng, 0.0) > 2.6).astype(float)
    lin = mix(tint(color, 1.0 + blot), np.asarray(color) * 0.45, pores * 0.8)
    return lin, blot * 0.0003 - pores * 0.0005


def epoxy(color, n, tile, rng):
    field = 1.0 + noise(n, rng, 3.0) * 0.03
    speck = (noise(n, rng, 0.0) > 2.8).astype(float)
    lin = mix(tint(color, field), np.asarray(color) * 1.6, speck * 0.5)
    return lin, field * 0.00002


def raised(color, n, tile, rng):
    lin, h, _ = grid_tiles(color, n, tile, rng, 0.6, 0.003, (0.08, 0.08, 0.09), 0.025, speck=0.03, groove=0.0015)
    return lin, h


def paint(color, n, tile, rng):
    """Embossed wallpaper / paint: tiny bumps, almost no color change."""
    bump = noise(n, rng, 0.8)
    lin = tint(color, 1.0 + bump * 0.015 + noise(n, rng, 3.0) * 0.01)
    return lin, bump * 0.00015


def brick(color, n, tile, rng):
    """Running bond 0.215 x 0.065 m bricks with 10 mm mortar (course 0.075 m, length 0.225 m)."""
    cols, rows = round(tile / 0.225), round(tile / 0.075)
    bw, bh = n / cols, n / rows
    px = tile / n
    mortar_w = 0.010 / px
    field = 1.0 + noise(n, rng, 1.0) * 0.08 + noise(n, rng, 2.5) * 0.06
    tones = rng.normal(0, 0.14, (rows, cols + 1))
    mortar = lines(n, [r * bh for r in range(rows)], mortar_w, 1)
    lin = np.zeros((n, n, 3))
    for r in range(rows):
        y0, y1 = int(round(r * bh)), int(round((r + 1) * bh))
        off = (bw / 2) * (r % 2)
        xs = [(c * bw + off) % n for c in range(cols)]
        m = lines(n, xs, mortar_w, 0)[y0:y1]
        mortar[y0:y1] = np.maximum(mortar[y0:y1], m)
        which = (((np.arange(n) - off) % n) // bw).astype(int)
        f = field[y0:y1] * (1.0 + tones[r, which][None, :])
        lin[y0:y1] = tint(color, f)
    lin = mix(lin, (0.45, 0.43, 0.40), mortar)
    return lin, -mortar * 0.004 + field * 0.0003


def metal(color, n, tile, rng):
    """Sandwich panels 1 m wide (joints along t = vertical on walls) with shallow ribs every 0.2 m."""
    px = tile / n
    x = np.arange(n) * px
    ribs = 0.5 + 0.5 * np.cos(2 * np.pi * x / 0.2)
    joint = lines(n, [0], 0.006 / px, 0)
    field = 1.0 + (ribs[None, :] - 0.5) * 0.06 + noise(n, rng, 2.0, stretch=(0.1, 1.0)) * 0.02
    lin = mix(tint(color, field), np.asarray(color) * 0.4, joint)
    return lin, ribs[None, :].repeat(n, 0) * 0.004 - joint * 0.003


def ceiling_board(color, n, tile, rng):
    """Gypsum boards 0.91 x 1.82 m (long side along t) with faint joints and fissured travertine pits."""
    px = tile / n
    joints = np.clip(lines(n, [0, n / 2], 0.002 / px, 0) + lines(n, [0], 0.002 / px, 1), 0, 1)
    pits = noise(n, rng, 0.3, stretch=(1.0, 0.35))
    pit = np.clip((pits - 1.6) * 1.5, 0, 1)
    field = 1.0 + noise(n, rng, 2.5) * 0.01
    lin = mix(tint(color, field), np.asarray(color) * 0.75, np.clip(pit * 0.6 + joints * 0.5, 0, 1))
    return lin, -pit * 0.0006 - joints * 0.0008


def ceiling_tile(color, n, tile, rng):
    """Office system ceiling: 600 mm rock-wool tiles in a white T-bar grid, fine perforation."""
    lin, h, _ = grid_tiles(color, n, tile, rng, 0.6, 0.012, (0.88, 0.88, 0.87), 0.01, speck=0.015, groove=0.004)
    holes = (noise(n, rng, 0.0) > 2.3).astype(float)
    lin = mix(lin, np.asarray(color) * 0.55, holes * 0.6)
    return lin, h - holes * 0.0004


def metal_deck(color, n, tile, rng):
    """Folded steel roof deck (折板): trapezoid ribs every 0.25 m along s, galvanized mottling, purlin shadow."""
    px = tile / n
    x = (np.arange(n) * px) % 0.25 / 0.25
    prof = np.clip(np.minimum(x, 1 - x) * 6 - 0.6, 0, 1)  # 0 = valley, 1 = crest, sloped webs between
    web = (np.abs(np.gradient(prof)) > 1e-6).astype(float)
    field = 1.0 + noise(n, rng, 1.5) * 0.05 + noise(n, rng, 2.5, stretch=(1.0, 0.1)) * 0.03
    lin = tint(color, field * (1.0 - 0.25 * web[None, :]) * (0.92 + 0.08 * prof[None, :]))
    return lin, prof[None, :].repeat(n, 0) * 0.02


DRAW = {"wood_light": wood, "wood_dark": wood, "tatami": tatami, "tile_white": tiles_white, "carpet_gray": carpet,
        "concrete": concrete, "epoxy_gray": epoxy, "raised_floor": raised, "paint_white": paint,
        "paint_beige": paint, "drywall_gray": paint, "brick": brick, "metal_panel": metal,
        "ceiling_board": ceiling_board, "ceiling_tile": ceiling_tile, "metal_deck": metal_deck}


def make(name: str, out: Path) -> None:
    t = TEXTURES[name]
    n, tile = t["px"], SURFACES[name]["tile_m"]
    rng = np.random.default_rng(sum(map(ord, name)))
    lin, height = DRAW[name](SURFACES[name]["color"], n, tile, rng)
    Image.fromarray(to_srgb8(lin)).save(out / f"{name}.jpg", quality=90)
    nrm = Image.fromarray(normal_map(height, tile / n)).resize((n // 2, n // 2), Image.LANCZOS)
    nrm.save(out / f"{name}_normal.png", optimize=True)


def main(argv: list[str]) -> None:
    names = argv or sorted(TEXTURES)
    TEXTURE_DIR.mkdir(parents=True, exist_ok=True)
    for name in names:
        make(name, TEXTURE_DIR)
        sizes = [(TEXTURE_DIR / f).stat().st_size // 1024 for f in (f"{name}.jpg", f"{name}_normal.png")]
        print(f"{name}: {sizes[0]} KB + normal {sizes[1]} KB")


if __name__ == "__main__":
    main(sys.argv[1:])
