"""Repeated placement (ObjectSpec.array): rack rows with aisles, rows of desks, ...

An array is laid out in the asset's "front frame": units side by side along the lateral axis
(across the asset's front), rows stacked along the front axis. The whole block is centered
on the object's position and faces the object's facing, so `layout` places the block like one
big object (against_wall puts the block's back on the wall).

    alternate=False: every row faces front, an `aisle` in front of each row (classroom desks)
    alternate=True:  rows face each other across an aisle, then back to back (warehouse racks):
                     row0 ->front | aisle | <- row1 row2 -> | aisle | <- row3 ...
    back_to_back=True: rows start back to back (store gondola islands, double-sided shelves):
                     <- row0 row1 -> | aisle | <- row2 row3 -> ...
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from agentic_sim_world_gen.assets.record import AssetRecord
from agentic_sim_world_gen.layout.frame import facing_of, front_local_deg, norm_deg, unit
from agentic_sim_world_gen.scene.spec import ObjectSpec


@dataclass
class Instance:
    id: str  # object id, or "<id>.r<row>c<col>" for array members
    parent: str
    position: tuple[float, float]
    yaw_deg: float
    z: float


def _rows(o: ObjectSpec, depth: float) -> tuple[list[tuple[float, bool]], float]:
    """[(center offset along the front axis from the block's back edge, flipped)], total depth."""
    arr = o.array
    out, v = [], 0.0
    for k in range(arr.rows):
        if arr.back_to_back:  # even rows face back, odd rows front: pairs stand back to back
            flipped, back_next = k % 2 == 0, k % 2 == 0
        else:  # alternate: even row faces +front toward the next row -> aisle; odd -> back to back
            flipped, back_next = arr.alternate and k % 2 == 1, arr.alternate and k % 2 == 1
        out.append((v + depth / 2, flipped))
        v += depth
        if k < arr.rows - 1:
            v += arr.back_gap if back_next else arr.aisle
    return out, v


def _front_on_x(a: AssetRecord, o: ObjectSpec) -> bool:
    """True if the asset's front points along its local X axis (nearest axis), False for Y."""
    f = abs(norm_deg(front_local_deg(a, o)))
    return f <= 45.0 or f >= 135.0


def unit_size(o: ObjectSpec, a: AssetRecord) -> tuple[float, float]:
    """(lateral, front-axis) size of one unit: width seen from the front, and depth."""
    w, d, _ = a.size_m
    if o.fit_scale is not None:
        w, d = w * o.fit_scale[0], d * o.fit_scale[1]
    return (d, w) if _front_on_x(a, o) else (w, d)


def placed_size(o: ObjectSpec, a: AssetRecord) -> tuple[float, float, float]:
    """a.size_m as placed: a product laid flat (fill.lay_flat) swaps its front-axis size and its height."""
    w, d, h = a.size_m
    if o.fit_scale is not None:  # stretched to its opening (windows, curtains)
        w, d, h = w * o.fit_scale[0], d * o.fit_scale[1], h * o.fit_scale[2]
    if o.fill is None or not o.fill.lay_flat:
        return w, d, h
    return (h, d, w) if _front_on_x(a, o) else (w, h, d)


def block_size(o: ObjectSpec, a: AssetRecord) -> tuple[float, float]:
    """(lateral, front-axis) size of the whole array block."""
    lat, dep = unit_size(o, a)
    if o.array is None:
        return lat, dep
    n = o.array.count
    _, total = _rows(o, dep)
    return n * lat + (n - 1) * o.array.spacing, total


def block_record(o: ObjectSpec, a: AssetRecord) -> AssetRecord:
    """A stand-in record with the block's footprint (in the asset's local axes) for the solver."""
    if o.array is None:
        return a
    h = a.size_m[2]
    U, V = block_size(o, a)
    bw, bd = (V, U) if _front_on_x(a, o) else (U, V)
    s = a.meters_per_unit
    # bbox in asset units so that size_m == (bw, bd, h); centered pivot
    return replace(
        a, bbox_min=(-bw / 2 / s, -bd / 2 / s, 0.0), bbox_max=(bw / 2 / s, bd / 2 / s, h / s), up_axis="Z"
    )


def instances(o: ObjectSpec, a: AssetRecord) -> list[Instance]:
    if o.fill is not None:  # products on a fixture's shelves (agentic_sim_world_gen.layout.fill writes the slots)
        return [Instance(f"{o.id}.{n}", o.id, (x, y), yaw, z) for n, x, y, yaw, z in o.fill.slots]
    if o.position is None:
        return []
    if o.array is None:
        return [Instance(o.id, o.id, tuple(o.position), o.yaw_deg, o.z)]
    lat, dep = unit_size(o, a)
    rows, V = _rows(o, dep)
    n, sp = o.array.count, o.array.spacing
    U = n * lat + (n - 1) * sp
    f = facing_of(a, o)
    fx, fy = unit(f)  # front axis (+ = the block's facing)
    lx, ly = unit(f + 90.0)  # lateral axis
    out = []
    for r, (v, flipped) in enumerate(rows):
        dv = v - V / 2
        for c in range(n):
            du = -U / 2 + lat / 2 + c * (lat + sp)
            x = o.position[0] + fx * dv + lx * du
            y = o.position[1] + fy * dv + ly * du
            yaw = norm_deg(o.yaw_deg + (180.0 if flipped else 0.0))
            out.append(Instance(f"{o.id}.r{r}c{c}", o.id, (round(x, 4), round(y, 4)), round(yaw, 3), o.z))
    return out
