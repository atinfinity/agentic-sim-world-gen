"""Preview (2): top-down 2D layout as SVG, built only from search metadata (no downloads)."""

from __future__ import annotations

import math
from html import escape
from pathlib import Path

from agentic_sim_world_gen.layout.array import instances, placed_size
from agentic_sim_world_gen.layout.frame import facing_of, half_extent
from agentic_sim_world_gen.layout.validate import check, footprint
from agentic_sim_world_gen.layout.room import RoomGeometry, centroid
from agentic_sim_world_gen.scene.fixtures import KINDS, beams
from agentic_sim_world_gen.scene.fixtures import plan as fixture_plan
from agentic_sim_world_gen.scene.looks import LIGHTING, ceiling_surface, hex_color, resolve_surface
from agentic_sim_world_gen.scene.spec import ResolvedScene
from agentic_sim_world_gen.scene.walls import WALL_JA, applies, default_value, overrides
from agentic_sim_world_gen.scene.zone_floors import zone_floors

def image_size(path: str | Path) -> tuple[int, int] | None:
    """(width, height) in pixels of a PNG or JPEG, read from the header."""
    import struct

    try:
        data = Path(path).expanduser().read_bytes()
    except OSError:
        return None
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return struct.unpack(">II", data[16:24])
    if data[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker, size = data[i + 1], struct.unpack(">H", data[i + 2 : i + 4])[0]
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                h, w = struct.unpack(">HH", data[i + 5 : i + 9])
                return w, h
            i += 2 + size
    return None


PALETTE = ["#4e79a7", "#f28e2b", "#59a14f", "#e15759", "#76b7b2", "#edc948", "#b07aa1", "#9c755f"]


LAYOUT_ATTR = ' data-layout="1"'  # placed by the solver: a drag clears its layout
FURNITURE_FRONT_LOCAL_DEG = -90.0  # same as agentic_sim_world_gen.plan.ifc.FRONT_LOCAL_DEG


HIT_PX = 12  # radius of the grip of small objects in the live preview


def render_svg(scene: ResolvedScene, px_per_m: float | None = None, margin_px: float = 40.0,
               furniture: list[dict] | None = None) -> str:
    """furniture: entries from an imported IFC (position, size, facing_deg) drawn as dashed boxes."""
    room = scene.spec.room
    geom = RoomGeometry(room)
    x0, y0, x1, y1 = geom.slab
    if room.outdoor or room.site is not None:  # the site plus a little ground around it
        bx0, by0, bx1, by1 = geom.bounds if room.outdoor else geom.site_bounds
        pad = max(1.0, 0.03 * max(bx1 - bx0, by1 - by0))
        x0, y0, x1, y1 = bx0 - pad, by0 - pad, bx1 + pad, by1 + pad
    if px_per_m is None:  # 120 px/m for rooms, smaller for warehouses (max ~1600 px)
        px_per_m = min(120.0, 1600.0 / max(x1 - x0, y1 - y0))
    W = (x1 - x0) * px_per_m + 2 * margin_px
    H = (y1 - y0) * px_per_m + 2 * margin_px + 30

    def X(x: float) -> float:
        return margin_px + (x - x0) * px_per_m

    def Y(y: float) -> float:  # SVG y grows downward; flip so +Y points up
        return margin_px + (y1 - y) * px_per_m

    def P(poly) -> str:
        return " ".join(f"{X(x):.1f},{Y(y):.1f}" for x, y in poly)

    floor_s, wall_s = resolve_surface(room.floor, "wood_light"), resolve_surface(default_value(room.walls), "paint_white")
    floor_fill = hex_color(tuple(0.5 * c + 0.5 for c in floor_s["color"]))  # lightened so objects stay readable
    zfloors = {z["id"]: z for z in zone_floors(room)}
    parts = [f'{escape(z["name"])} {escape(z["surface"]["name"])}' for z in zfloors.values()]
    if not room.outdoor:  # floor areas
        parts += [f'{escape(a.name or a.id)} {escape(a.surface()["name"])}' for a in room.floor_areas]
    zone_note = "（" + "・".join(parts) + "）" if parts else ""
    wall_fill = hex_color(tuple(0.5 * c for c in wall_s["color"]))
    rect = room.plan is None
    own = {} if room.outdoor else {k: resolve_surface(v, "paint_white") for k, v in overrides(room.walls, rect).items()}
    fills = {k: hex_color(tuple(0.5 * c for c in v["color"])) for k, v in own.items()}  # accent walls
    accents = "".join(f'、{WALL_JA.get(k, k) if rect else k} {v["name"]}' for k, v in own.items())
    trim = "".join(f" / {ja} {t.height:g} m" for ja, t in (("腰壁", room.wainscot), ("巾木", room.baseboard)) if t)
    walls_txt = wall_s["name"] + (f"（{accents[1:]}）" if accents else "") + trim
    room_box = ""
    if room.site is not None:  # the room alone, for the page's 「部屋だけ」 view: the site makes it small
        sx0, sy0, sx1, sy1 = geom.slab
        m = 1.0 * px_per_m
        room_box = (f' data-room="{X(sx0) - m:.0f} {Y(sy1) - m:.0f} {X(sx1) - X(sx0) + 2 * m:.0f} '
                    f'{Y(sy0) - Y(sy1) + 2 * m:.0f}"')
    issues = check(scene)
    bad = {oid for i in issues for oid in i.objects}  # object ids and array unit ids (<id>.r0c1)

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W:.0f}" height="{H:.0f}" '
        f'viewBox="0 0 {W:.0f} {H:.0f}" font-family="sans-serif" font-size="12" class="wg-plan" '
        f'data-x0="{x0:.4f}" data-y1="{y1:.4f}" data-ppm="{px_per_m:.4f}" data-margin="{margin_px:g}"{room_box}>',
        f'<rect width="{W:.0f}" height="{H:.0f}" fill="#fafafa"/>',
        '<defs><marker id="front" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" '
        'orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#333"/></marker>'
        '<marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" '
        'orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#3b82b6"/></marker></defs>',
        f'<text x="{margin_px}" y="{margin_px - 14}" fill="#444">'
        + (f'屋外 / 地面 {escape(floor_s["name"])} / ' if room.outdoor else f'床 {escape(floor_s["name"])}{zone_note} / 壁 {escape(walls_txt)} / ')
        + f'照明 {escape(room.lighting)}{" ×" + format(room.lighting_scale, "g") if room.lighting_scale != 1 else ""}'
        f'{" " + format(room.light_kelvin, "g") + " K" if room.light_kelvin else ""}'
        f'{" / 空 " + escape(sky) if not room.site and (sky := room.sky or LIGHTING[room.lighting].get("sky")) and sky != "none" else ""}'
        + (f' / 外 {escape(resolve_surface(room.site.ground, "asphalt")["name"])}・{escape(room.site.lighting)}' if room.site else "")
        + f'{" / 天井 " + escape(ceil_s["name"]) if (ceil_s := ceiling_surface(room.ceiling, room.lighting, room.place)) else ""}</text>',
    ]
    u = room.plan.underlay if room.plan else None
    if u and (size := image_size(u.image)):  # traced source image, half transparent
        k = u.m_per_px
        left, top = -u.origin_px[0] * k + u.shift[0], u.origin_px[1] * k + u.shift[1]
        out.append(f'<image href="{escape(Path(u.image).expanduser().resolve().as_uri())}" x="{X(left):.1f}" y="{Y(top):.1f}" '
                   f'width="{size[0] * k * px_per_m:.1f}" height="{size[1] * k * px_per_m:.1f}" opacity="0.45" '
                   'preserveAspectRatio="none"/>')
    # floor: plan zones, else the interior rectangle; then a 0.5 m grid over it
    if room.outdoor:
        floors = [([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], None)]
    elif geom.zones:
        floors = [(p, zfloors[z]["surface"] if z in zfloors else None) for z, p in geom.zones.items()]
    else:
        bx0, by0, bx1, by1 = geom.bounds
        floors = [([(bx0, by0), (bx1, by0), (bx1, by1), (bx0, by1)], None)]
    see_through = ' fill-opacity="0.5"' if u else ""
    if room.site is not None:  # the outside around the room under it
        gs = resolve_surface(room.site.ground, "asphalt")
        out.append(f'<rect x="{X(x0):.1f}" y="{Y(y1):.1f}" width="{X(x1) - X(x0):.1f}" height="{Y(y0) - Y(y1):.1f}" '
                   f'fill="{hex_color(tuple(0.7 * c + 0.3 for c in gs["color"]))}"/>')
        out += _site_svg(room.site.areas, room.site.buildings, geom.site_bounds, P, X, Y)
    for poly, zs in floors:  # a zone with its own floor (agentic_sim_world_gen.scene.zone_floors) in that color
        fill = hex_color(tuple(0.5 * c + 0.5 for c in zs["color"])) if zs else floor_fill
        out.append(f'<polygon points="{P(poly)}" fill="{fill}"{see_through}/>')
    for a in [] if room.outdoor else room.floor_areas:  # parts of the floor with their own finish
        fill = hex_color(tuple(0.5 * c + 0.5 for c in a.surface()["color"]))
        out.append(f'<polygon points="{P(a.poly)}" fill="{fill}"{see_through} stroke="#999" stroke-dasharray="3 2">'
                   f'<title>{escape(a.name or a.id)}: {escape(a.surface()["name"])}</title></polygon>')
    g = 0.5 if max(x1 - x0, y1 - y0) <= 25 else 1.0 if max(x1 - x0, y1 - y0) <= 80 else 5.0
    k = 1
    while x0 + k * g < x1:
        out.append(f'<line x1="{X(x0 + k * g):.1f}" y1="{Y(y0):.1f}" x2="{X(x0 + k * g):.1f}" y2="{Y(y1):.1f}" stroke="#000" stroke-opacity="0.06"/>')
        k += 1
    k = 1
    while y0 + k * g < y1:
        out.append(f'<line x1="{X(x0):.1f}" y1="{Y(y0 + k * g):.1f}" x2="{X(x1):.1f}" y2="{Y(y0 + k * g):.1f}" stroke="#000" stroke-opacity="0.06"/>')
        k += 1
    if room.outdoor:
        out += _site_svg(room.areas, room.buildings, geom.bounds, P, X, Y)
    for zid, poly in geom.zones.items():
        cx, cy = centroid(poly)
        out.append(f'<text x="{X(cx):.1f}" y="{Y(cy):.1f}" text-anchor="middle" fill="#000" fill-opacity="0.35" '
                   f'font-size="16">{escape(geom.zone_names[zid])}</text>')

    # walls: solid parts; doors as a gap with a swing arc, windows as a light blue band
    for w in geom.walls:
        half = w.thickness / 2
        for u0, u1, z0, z1, kind in w.pieces():
            if kind == "wall":
                out.append(f'<polygon points="{P(w.rect(u0, u1, -half, half))}" fill="{fills.get(w.id, wall_fill)}"/>')
        for o in w.openings:
            u0, u1 = w.interval(o)
            if o.kind == "window":
                out.append(f'<polygon points="{P(w.rect(u0, u1, -half, half))}" fill="#9ad0f5" stroke="#3b82b6" stroke-width="1"/>')
                continue
            out.append(f'<polygon points="{P(w.rect(u0, u1, -half, half))}" fill="none" stroke="{wall_fill}" stroke-width="0.8"/>')
            if o.style == "auto_sliding":  # two panels on the outside face, arrows to where they slide
                out_n = -geom.inner_side(w, (u0 + u1) / 2) * (half + 0.035)
                mid = (u0 + u1) / 2
                for a, b in ((u0 - 0.05, mid), (mid, u1 + 0.05)):
                    p, q = w.at(a, out_n), w.at(b, out_n)
                    out.append(f'<line x1="{X(p[0]):.1f}" y1="{Y(p[1]):.1f}" x2="{X(q[0]):.1f}" y2="{Y(q[1]):.1f}" '
                               'stroke="#3b82b6" stroke-width="3"/>')
                for a, b in ((mid - 0.1, u0 - 0.2), (mid + 0.1, u1 + 0.2)):
                    p, q = w.at(a, out_n * 2.2), w.at(b, out_n * 2.2)
                    out.append(f'<line x1="{X(p[0]):.1f}" y1="{Y(p[1]):.1f}" x2="{X(q[0]):.1f}" y2="{Y(q[1]):.1f}" '
                               'stroke="#3b82b6" stroke-width="1" marker-end="url(#arrow)"/>')
                c = w.at(mid, -out_n * 3)
                out.append(f'<text x="{X(c[0]):.1f}" y="{Y(c[1]) + 3:.1f}" text-anchor="middle" fill="#3b82b6" font-size="9">自動ドア</text>')
            elif o.kind == "door":
                side = geom.inner_side(w, (u0 + u1) / 2)
                hinge, close = w.at(u0, side * half), w.at(u1, side * half)
                leaf = w.at(u0, side * (half + (u1 - u0)))
                r = (u1 - u0) * px_per_m
                # sweep direction: from the open leaf to the closed position, around the hinge
                cross = (X(leaf[0]) - X(hinge[0])) * (Y(close[1]) - Y(hinge[1])) - (Y(leaf[1]) - Y(hinge[1])) * (X(close[0]) - X(hinge[0]))
                sweep = 1 if cross > 0 else 0
                out.append(f'<line x1="{X(hinge[0]):.1f}" y1="{Y(hinge[1]):.1f}" x2="{X(leaf[0]):.1f}" y2="{Y(leaf[1]):.1f}" stroke="#555" stroke-width="1.5"/>')
                out.append(f'<path d="M{X(leaf[0]):.1f},{Y(leaf[1]):.1f} A{r:.1f},{r:.1f} 0 0 {sweep} {X(close[0]):.1f},{Y(close[1]):.1f}" '
                           'fill="none" stroke="#555" stroke-dasharray="3 2"/>')
    # wainscot / baseboards: lines along the inner faces in their colors (cut at doors)
    for t in (room.wainscot, room.baseboard) if not room.outdoor else ():
        if t is None:
            continue
        col = hex_color(t.resolved()["color"])
        for f in geom.faces:
            if not applies(t, f.wall, rect):
                continue
            for p0, p1, _ in geom.face_runs(f, t.height):
                out.append(f'<line x1="{X(p0[0]):.1f}" y1="{Y(p0[1]):.1f}" x2="{X(p1[0]):.1f}" y2="{Y(p1[1]):.1f}" '
                           f'stroke="{col}" stroke-width="{3 if t is room.wainscot else 1.5}"/>')
    if geom.room.plan is not None:
        for w in geom.walls:  # small wall ids, so against_wall can name them
            cx, cy = w.at(w.length / 2)
            out.append(f'<text x="{X(cx):.1f}" y="{Y(cy) + 3:.1f}" text-anchor="middle" fill="#fff" font-size="8">{escape(w.id)}</text>')

    if not room.outdoor and (room.levels or room.stairs):
        out += _levels_svg(room, P, X, Y)
    for c in [] if room.outdoor else room.columns:  # columns: solid, like the walls
        out.append(f'<polygon points="{P(c.poly)}" fill="{wall_fill}" stroke="#333" stroke-width="1">'
                   f'<title>柱 {escape(c.id)}: {c.dims[0]:g}×{c.dims[1]:g} m</title></polygon>')
    for b in beams(room):  # ceiling beams / soffits: dashed, see-through
        x0, y0, x1, y1 = b["rect"]
        out.append(f'<rect x="{X(x0):.1f}" y="{Y(y1):.1f}" width="{(x1 - x0) * px_per_m:.1f}" height="{(y1 - y0) * px_per_m:.1f}" '
                   f'fill="#8d6e63" fill-opacity="0.12" stroke="#8d6e63" stroke-dasharray="4 3">'
                   f'<title>{escape(b["id"])}: 天井から {b["feature"].drop:g} m</title></rect>')
    lp = LIGHTING[room.lighting]
    if room.fixtures is not None and not room.outdoor:  # fixtures of a kind: their shape from above
        for f in fixture_plan(room, geom.fixture_points(room.fixtures.spacing or KINDS[room.fixtures.kind]["spacing"])):
            L = f["light"]
            if L["shape"] == "rect":
                lw, lh = (L["width"], L["height"]) if L["yaw"] == 0 else (L["height"], L["width"])
                out.append(f'<rect x="{X(f["x"]) - lw * px_per_m / 2:.1f}" y="{Y(f["y"]) - lh * px_per_m / 2:.1f}" '
                           f'width="{lw * px_per_m:.1f}" height="{max(lh * px_per_m, 2):.1f}" fill="#ffd54f" stroke="#b08900"/>')
            else:
                r = max(max(p.get("radius", 0) for p in f["parts"]) * px_per_m, 3)
                out.append(f'<circle cx="{X(f["x"]):.1f}" cy="{Y(f["y"]):.1f}" r="{r:.1f}" fill="#ffd54f" stroke="#b08900"/>')
    elif lp.get("fixture"):  # ceiling fixtures (outdoor: lamps on poles) as small yellow marks
        for x, y in geom.fixture_points(lp["spacing"]):
            out.append(f'<circle cx="{X(x):.1f}" cy="{Y(y):.1f}" r="4" fill="#ffd54f" stroke="#b08900"/>')

    # higher objects last, so a cup on a desk is drawn (and grabbed) above the desk
    for idx, p in sorted(enumerate(scene.placements), key=lambda t: scene.obj(t[1].object_id).z or 0.0):
        o = scene.obj(p.object_id)
        a = p.asset
        if a is None or o.position is None:
            continue
        w, d, h = placed_size(o, a)
        color = PALETTE[idx % len(PALETTE)]
        stroke = "#333"
        # one group per object, so the live preview can drag / turn it; stocked products and door
        # assets in their opening are not draggable (they follow their fixture / opening)
        edit = o.fill is None and o.opening is None
        out.append(f'<g class="wg-obj{" edit" if edit else ""}" data-id="{escape(o.id)}" data-label="{escape(o.label or o.id)}" '
                   f'data-x="{o.position[0]:.4f}" data-y="{o.position[1]:.4f}" data-yaw="{o.yaw_deg:.3f}" '
                   f'data-front="{facing_of(a, o) - o.yaw_deg:.3f}" data-r="{math.hypot(w, d) / 2:.3f}"'
                   f'{LAYOUT_ATTR if o.layout is not None else ""}>')
        for inst in instances(o, a):
            poly = footprint(inst.position, (w, d), inst.yaw_deg)
            pts = " ".join(f"{X(x):.1f},{Y(y):.1f}" for x, y in poly)
            ibad = inst.id in bad or o.id in bad
            stroke = "#d00" if ibad else "#333"
            dash = ' stroke-dasharray="4 3"' if ibad else ""
            out.append(
                f'<polygon points="{pts}" fill="{color}" fill-opacity="0.35" stroke="{stroke}" stroke-width="1.5"{dash}/>'
            )
            if o.fill is not None:
                continue  # products on shelves: small boxes only
            # assumed front (solver convention): short tick from the center toward the front edge
            f = facing_of(a, o) + (inst.yaw_deg - o.yaw_deg)
            r = half_extent(a, inst.yaw_deg, f)
            fr = math.radians(f)
            ix, iy = inst.position
            fx, fy = ix + math.cos(fr) * r, iy + math.sin(fr) * r
            out.append(f'<line x1="{X(ix):.1f}" y1="{Y(iy):.1f}" x2="{X(fx):.1f}" y2="{Y(fy):.1f}" stroke="{stroke}" stroke-width="1" marker-end="url(#front)"/>')
        if o.fill is not None:
            out.append("</g>")
            continue  # the fixture's label is there
        cx, cy = X(o.position[0]), Y(o.position[1])
        if edit and not o.array and min(w, d) * px_per_m < 2 * HIT_PX:  # small things: a bigger invisible grip
            out.append(f'<circle class="wg-hit" cx="{cx:.1f}" cy="{cy:.1f}" r="{HIT_PX}" fill="transparent"/>')
        if o.decor:  # small added things; their labels covered the fixture's (the name is in data-label)
            out.append("</g>")
            continue
        if o.z > 0.005:
            out.append(f'<text x="{cx:.1f}" y="{cy + 24:.1f}" text-anchor="middle" fill="#444" font-size="10">z={o.z:.2f}m</text>')
        label = escape(o.label or o.id) + (f" ×{o.array.count * o.array.rows}" if o.array else "")
        out.append(f'<text x="{cx:.1f}" y="{cy - 2:.1f}" text-anchor="middle" font-weight="bold">{label}</text>')
        out.append(
            f'<text x="{cx:.1f}" y="{cy + 12:.1f}" text-anchor="middle" fill="#444" font-size="10">'
            f"{w:.2f}×{d:.2f}×{h:.2f}m</text>"
        )
        out.append("</g>")

    for f in furniture or []:  # imported BIM furniture (not yet assets): dashed outline + front tick
        fx, fy = f["position"]
        sx, sy = f["size"][0], f["size"][1]
        yaw = f["facing_deg"] - FURNITURE_FRONT_LOCAL_DEG  # local X direction
        poly = footprint((fx, fy), (sx, sy), yaw)
        out.append(f'<polygon points="{P(poly)}" fill="#8d6e63" fill-opacity="0.18" stroke="#5d4037" '
                   'stroke-width="1.2" stroke-dasharray="5 3"/>')
        fr = math.radians(f["facing_deg"])
        r = sy / 2
        out.append(f'<line x1="{X(fx):.1f}" y1="{Y(fy):.1f}" x2="{X(fx + math.cos(fr) * r):.1f}" '
                   f'y2="{Y(fy + math.sin(fr) * r):.1f}" stroke="#5d4037" stroke-width="1" marker-end="url(#front)"/>')
        out.append(f'<text x="{X(fx):.1f}" y="{Y(fy) - 2:.1f}" text-anchor="middle" fill="#3e2723" font-size="10">'
                   f'{escape(f.get("name") or f["id"])}</text>')
        out.append(f'<text x="{X(fx):.1f}" y="{Y(fy) + 10:.1f}" text-anchor="middle" fill="#5d4037" font-size="9">'
                   f'{escape(f["query"])}</text>')

    # photo cameras: where each input photo was taken from and what it sees (horizontal field of view)
    for i in scene.spec.inputs:
        if i.camera is None:
            continue
        (cx, cy, _), (ax, ay, _) = i.camera.position, i.camera.look_at
        head = math.atan2(ay - cy, ax - cx)
        reach = max(1.0, math.hypot(ax - cx, ay - cy))
        half = math.radians(i.camera.hfov_deg) / 2
        wedge = [(cx, cy)] + [(cx + reach * math.cos(head + a), cy + reach * math.sin(head + a))
                              for a in (-half + k * half / 4 for k in range(9))]
        out.append(f'<polygon points="{P(wedge)}" fill="#1e88e5" fill-opacity="0.10" stroke="#1e88e5" '
                   'stroke-width="1" stroke-dasharray="3 3"/>')
        out.append(f'<line x1="{X(cx):.1f}" y1="{Y(cy):.1f}" x2="{X(ax):.1f}" y2="{Y(ay):.1f}" stroke="#1e88e5" '
                   'stroke-width="1"/>')
        out.append(f'<circle cx="{X(cx):.1f}" cy="{Y(cy):.1f}" r="4" fill="#1e88e5"/>')
        out.append(f'<text x="{X(cx) + 6:.1f}" y="{Y(cy) - 6:.1f}" fill="#0d47a1" font-size="11">📷 {escape(i.id)}</text>')

    out += _widths_svg(scene, X, Y, px_per_m)

    # the user's notes: a numbered pin at each spot
    for n in scene.spec.notes:
        if n.position is None:
            continue
        px, py = X(n.position[0]), Y(n.position[1])
        out.append(f'<g class="wg-note" data-id="{escape(n.id)}"><title>{escape(n.id[1:])}：{escape(n.text)}</title>'
                   f'<path d="M{px:.1f},{py:.1f} l-7,-12 a8,8 0 1 1 14,0 z" fill="#d81b60" stroke="#fff" stroke-width="1.5"/>'
                   f'<text x="{px:.1f}" y="{py - 13:.1f}" text-anchor="middle" fill="#fff" font-size="10" '
                   f'font-weight="bold">{escape(n.id[1:])}</text></g>')

    # scale bar + title
    bar = 1 if px_per_m >= 40 else 5 if px_per_m >= 8 else 20
    out.append(f'<line x1="{margin_px}" y1="{H - 18}" x2="{margin_px + bar * px_per_m}" y2="{H - 18}" stroke="#000" stroke-width="2"/>')
    out.append(f'<text x="{margin_px + bar * px_per_m + 6}" y="{H - 14}">{bar} m</text>')
    out.append(
        f'<text x="{W - margin_px}" y="{H - 14}" text-anchor="end">{escape(scene.spec.name)} — '
        f"{room.width:.2f}×{room.depth:.2f} m{' (敷地)' if room.outdoor else ''}"
        f'{" — 問題 " + str(len(issues)) + " 件" if issues else ""}</text>'
    )
    out.append("</svg>")
    return "\n".join(out)


WIDTH_COLORS = {"squeeze": "#d32f2f", "narrow": "#f57c00", "ok": "#43a047", "wide": "#1e88e5"}
WIDTH_MARKS = 8  # rings on layout.svg at most (the narrowest spots)
WIDTH_JA = {"squeeze": "通れない", "narrow": "狭い", "ok": "通路", "wide": "広い"}


def _widths_svg(scene: ResolvedScene, X, Y, px_per_m: float) -> list[str]:
    """The aisle widths: the centre lines of the free floor coloured by how wide it is there (the same map
    as the usability check's narrow_aisle), and a ring at the narrowest spot of the ways from the entrances."""
    from agentic_sim_world_gen.layout import usability

    if scene.spec.room.outdoor:
        return []
    try:
        amap = usability.aisle_map(scene)
        if amap is None:
            return []
        g, need, over = amap.grid, amap.need, usability.wide_over(scene.spec.room.place)
        rows: dict[tuple[str, int], list[int]] = {}
        for (i, j), w in amap.ridge():
            k = ("squeeze" if w < usability.SQUEEZE - 2 * g.c else "narrow" if w < need - 2 * g.c else
                 "wide" if over is not None and w > over else "ok")
            rows.setdefault((k, j), []).append(i)
        spots = amap.spots()
    except Exception:  # the plan is still drawn
        return []
    s = g.c * px_per_m
    out = [f'<g class="wg-widths" opacity="0.6" data-need="{need:g}">']
    for (k, j), cols in sorted(rows.items()):
        cols.sort()
        a = b = cols[0]
        for i in [*cols[1:], None]:  # runs of cells in a row are one rect
            if i is not None and i == b + 1:
                b = i
                continue
            x, y = g.x0 + a * g.c, g.y0 + (j + 1) * g.c
            out.append(f'<rect x="{X(x):.1f}" y="{Y(y):.1f}" width="{(b - a + 1) * s:.1f}" height="{s:.1f}" '
                       f'fill="{WIDTH_COLORS[k]}"/>')
            if i is not None:
                a = b = i
    out.append("</g>")
    marks = [r for r in spots if r["narrow"]][:WIDTH_MARKS] or spots[:1]  # the narrow spots, or the narrowest one
    for r in marks:
        px, py = X(r["at"][0]), Y(r["at"][1])
        col = WIDTH_COLORS["squeeze" if r["width"] < usability.SQUEEZE else "narrow"] if r["narrow"] else "#2e7d32"
        to = "・".join(r["to"][:3]) + (" ほか" if len(r["to"]) > 3 else "")
        out.append(f'<g class="wg-narrowest" data-width="{r["width"]:g}"><title>{escape(to)} への道で一番狭い所：約 '
                   f'{r["width"]:.2f} m（必要 {need:g} m）</title>'
                   f'<circle cx="{px:.1f}" cy="{py:.1f}" r="9" fill="none" stroke="{col}" stroke-width="2.5"/>'
                   f'<text x="{px + 12:.1f}" y="{py + 4:.1f}" fill="{col}" font-size="11" font-weight="bold" '
                   f'stroke="#fff" stroke-width="3" paint-order="stroke">{r["width"]:.2f} m</text></g>')
    used = [k for k in WIDTH_COLORS if any(kk == k for kk, _ in rows)]
    x = X(g.x0)
    y = Y(g.y0) + 26
    out.append('<g class="wg-widths-legend" font-size="10" fill="#444">')
    for k in used:
        out.append(f'<rect x="{x:.1f}" y="{y - 8:.1f}" width="14" height="5" fill="{WIDTH_COLORS[k]}" opacity="0.6"/>'
                   f'<text x="{x + 18:.1f}" y="{y:.1f}">{WIDTH_JA[k]}</text>')
        x += 24 + 11 * len(WIDTH_JA[k])
    out.append("</g>")
    return out


def _levels_svg(room, P, X, Y) -> list[str]:
    """Raised floors: shaded with their height; stairs as tread lines with an arrow up; railings dark."""
    from agentic_sim_world_gen.scene import levels as lv

    out = []
    for L in room.levels:
        own = L.color or (hex_color(resolve_surface(L.floor, "wood_light")["color"]) if L.floor is not None else None)
        out.append(f'<polygon points="{P(L.poly)}" fill="{own or "#000"}" fill-opacity="{0.45 if own else 0.08}" '
                   f'stroke="#555" stroke-width="1.5"><title>{escape(L.name or L.id)}: +{L.height:g} m</title></polygon>')
        x, y = min(L.poly, key=lambda p: (p[0] - p[1], p[0]))  # its top-left corner (upper right of the label)
        out.append(f'<text x="{X(x) + 4:.1f}" y="{Y(y) + 13:.1f}" fill="#555" font-size="10">'
                   f'{escape(L.name or L.id)} +{L.height:g} m</text>')
    for s in room.stairs:
        f = lv.flight(room, s)
        out.append(f'<polygon points="{P(lv.footprint(f, s.width))}" fill="#ddd" stroke="#555" stroke-width="1">'
                   f'<title>{escape(s.id)}: {f["count"]} 段・{f["rise"]:g} m</title></polygon>')
        (fx, fy), (nx, ny) = f["foot"], f["up"]
        ax, ay = -ny * s.width / 2, nx * s.width / 2
        for k in range(1, f["treads"]):
            px, py = fx + nx * k * s.going, fy + ny * k * s.going
            out.append(f'<line x1="{X(px - ax):.1f}" y1="{Y(py - ay):.1f}" x2="{X(px + ax):.1f}" y2="{Y(py + ay):.1f}" stroke="#555"/>')
        tx, ty = fx + nx * f["run"] * 0.9, fy + ny * f["run"] * 0.9
        out.append(f'<line x1="{X(fx + nx * 0.1):.1f}" y1="{Y(fy + ny * 0.1):.1f}" x2="{X(tx):.1f}" y2="{Y(ty):.1f}" '
                   'stroke="#333" stroke-width="1.2" marker-end="url(#arrow)"/>')
        out.append(f'<text x="{X(fx - nx * 0.15):.1f}" y="{Y(fy - ny * 0.15) + 3:.1f}" text-anchor="middle" fill="#333" font-size="9">UP</text>')
    for r in lv.rails(room):
        out.append(f'<polygon points="{P(lv.rail_rect(r))}" fill="#333"/>')
    return out


def _site_svg(areas, buildings, bounds, P, X, Y) -> list[str]:
    """Outdoor (or Room.site): areas in their surface colors (keep-clear dashed, curbs outlined), buildings,
    the site edge."""
    out = []
    for a in areas:
        fill = hex_color(tuple(0.7 * c + 0.3 for c in resolve_surface(a.surface_or_default, "concrete")["color"]))
        stroke = ' stroke="#555" stroke-width="1.5"' if a.top > 0 else ""
        out.append(f'<polygon points="{P(a.poly)}" fill="{fill}"{stroke}/>')
        if a.clear:
            out.append(f'<polygon points="{P(a.poly)}" fill="none" stroke="#c62828" stroke-opacity="0.5" stroke-dasharray="8 6"/>')
    for b in buildings:
        fill = hex_color(tuple(0.6 * c for c in resolve_surface(b.surface, "concrete")["color"]))
        out.append(f'<polygon points="{P(b.poly)}" fill="{fill}" stroke="#222"/>')
        cx, cy = centroid(b.poly)
        out.append(f'<text x="{X(cx):.1f}" y="{Y(cy):.1f}" text-anchor="middle" fill="#fff" font-size="14">'
                   f'{escape(b.name or b.id)} ({escape(b.id)}) h{b.height:g}m</text>')
    bx0, by0, bx1, by1 = bounds
    out.append(f'<polygon points="{P([(bx0, by0), (bx1, by0), (bx1, by1), (bx0, by1)])}" fill="none" stroke="#333" '
               'stroke-width="1.5" stroke-dasharray="10 4 2 4"/>')
    return out


def write_svg(scene: ResolvedScene, path: str | Path, furniture: list[dict] | None = None) -> Path:
    path = Path(path)
    path.write_text(render_svg(scene, furniture=furniture), encoding="utf-8")
    return path
