"""Widths of the free floor on a grid: the distance transform (how far each free cell is from anything in
the way), the widest way from the entrances to every cell and where it is narrowest, and the centre lines of the
aisles. One set of functions for the usability check (layout.usability.AisleMap), nav_map (agentic_sim_world_gen.nav) and the
colours on layout.svg."""

from __future__ import annotations

import heapq
import math

INF = 1e18
Cell = tuple[int, int]  # (i, j) = (column, row)


def edt1d(f: list[float]) -> list[float]:
    """Squared distance transform of a 1D sampled function (lower envelope of parabolas, Felzenszwalb)."""
    n = len(f)
    v: list[int] = []
    z: list[float] = []
    for q in range(n):
        if f[q] >= INF:
            continue
        while v:
            p = v[-1]
            s = ((f[q] + q * q) - (f[p] + p * p)) / (2 * q - 2 * p)
            if s <= z[-1]:
                v.pop()
                z.pop()
                continue
            v.append(q)
            z.append(s)
            break
        else:
            v.append(q)
            z.append(-INF)
    if not v:
        return [INF] * n
    out, k = [], 0
    for q in range(n):
        while k + 1 < len(v) and z[k + 1] < q:
            k += 1
        out.append((q - v[k]) ** 2 + f[v[k]])
    return out


def edt(seed: list[list[bool]]) -> list[list[float]]:
    """Distance (in cells) from every cell to the nearest seed cell."""
    ny, nx = len(seed), len(seed[0])
    cols = []
    for i in range(nx):
        cols.append(edt1d([0.0 if seed[j][i] else INF for j in range(ny)]))
    out = []
    for j in range(ny):
        row = edt1d([cols[i][j] for i in range(nx)])
        out.append([math.sqrt(x) if x < INF / 2 else INF for x in row])
    return out


def widest(clear: list[list[float]], seeds: list[Cell], floor: float = 0.0
           ) -> tuple[list[list[float]], list[list[Cell | None]]]:
    """The widest way from the seeds to every cell: best[j][i] = the largest clearance c such that a path of cells
    with clearance >= c leads there from a seed (-1 where none does), and prev = the cell before it on that
    path. Cells with clearance <= floor are not walked. Thresholding `best` at t gives exactly the cells
    connected to a seed through cells of clearance >= t."""
    ny, nx = len(clear), len(clear[0])
    best = [[-1.0] * nx for _ in range(ny)]
    prev: list[list[Cell | None]] = [[None] * nx for _ in range(ny)]
    heap = []
    for i, j in seeds:
        if clear[j][i] > floor and clear[j][i] > best[j][i]:
            best[j][i] = clear[j][i]
            heap.append((-clear[j][i], i, j))
    heapq.heapify(heap)
    while heap:
        b, i, j = heapq.heappop(heap)
        b = -b
        if b < best[j][i]:
            continue
        for x, y in ((i + 1, j), (i - 1, j), (i, j + 1), (i, j - 1)):
            if 0 <= x < nx and 0 <= y < ny and clear[y][x] > floor:
                nb = min(b, clear[y][x])
                if nb > best[y][x]:
                    best[y][x], prev[y][x] = nb, (i, j)
                    heapq.heappush(heap, (-nb, x, y))
    return best, prev


CLIMB_LOOK = 3  # cells looked ahead while walking away from the target for the open floor still getting wider


def narrowest(clear: list[list[float]], prev: list[list[Cell | None]], cell: Cell) -> tuple[Cell, float]:
    """(the cell with the least clearance, that clearance) on the widest way to `cell` (follow prev back). The
    last steps, from the middle of the aisle up to the target (a spot at a shelf front has little clearance by
    itself), do not count: the way is followed back from the target while the floor keeps getting wider."""
    path: list[Cell] = []
    c: Cell | None = cell
    while c is not None:
        path.append(c)
        c = prev[c[1]][c[0]]
    v = [clear[j][i] for i, j in path]
    k = 0
    while k + 1 < len(v) and max(v[k + 1:k + 1 + CLIMB_LOOK]) > v[k] + 1e-9:
        k += 1 + max(range(len(v[k + 1:k + 1 + CLIMB_LOOK])), key=lambda n: v[k + 1 + n])
    low = min(v[k:])
    ties = [n for n in range(k, len(v)) if v[n] <= low + 1e-9]
    # the widest way may run along a wall of an open room at the doorway's clearance: the spot is where the gap
    # itself is that narrow (a centre-line cell; the middle one of them along an aisle of one width)
    ties = [n for n in ties if on_ridge(clear, *path[n])] or ties
    n = ties[len(ties) // 2]
    return path[n], v[n]


def on_ridge(clear: list[list[float]], i: int, j: int) -> bool:
    """The clearance falls off on both sides of the cell along x or along y (a plateau of two equal cells counts
    once): the cell is on the centre line between two things."""
    ny, nx = len(clear), len(clear[0])
    eps = 1e-9

    def at(x: int, y: int) -> float:
        return clear[y][x] if 0 <= x < nx and 0 <= y < ny else 0.0

    v = clear[j][i]
    for dx, dy in ((1, 0), (0, 1)):
        if v > at(i - dx, j - dy) + eps and (
                v > (n := at(i + dx, j + dy)) + eps or (abs(v - n) <= eps and n > at(i + 2 * dx, j + 2 * dy) + eps)):
            return True
    return False


def ridge(clear: list[list[float]], least: float = 0.0) -> list[Cell]:
    """Centre-line cells of the aisles (on_ridge). The diagonals running into corners are not two-sided, so they
    are left out."""
    return [(i, j) for j in range(len(clear)) for i in range(len(clear[0]))
            if least < clear[j][i] < INF / 2 and on_ridge(clear, i, j)]
