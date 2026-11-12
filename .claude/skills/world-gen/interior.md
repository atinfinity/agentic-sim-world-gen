# Interior finish pieces, lights, steps, columns

Part of the world-gen skill ([SKILL.md](SKILL.md)); read it when the request needs it.

## Floor, wall and ceiling finishes

**By words** (「明るい木目の床」「白いタイルの壁」, a look no preset has): `set_room(floor="light wood planks",
floor_alt_queries=["pale oak flooring"], walls="white glazed tile", walls_alt_queries=[...])` (words in create_scene's room work the same, without alt queries) searches the
~2600 MDL materials bundled with Isaac Sim (Base + vMaterials 2) and ~2500 CC0 PBR textures of ambientCG /
Poly Haven (photo-scanned tatami, wallpaper, plaster, wood floors, paving; `source` in each candidate). Write color + material + pattern in English
(herringbone parquet, polished concrete, red brick, white plaster, gray carpet, marble). The top match is
applied (RTX MDL, its thumbnail color for the previews, friction by category); `surface_candidates` lists the
rest. Look with `view_surface_candidates(target)` and switch with `select_surface(target, index)`, or pass
`"mdl:<id>"` / `"pbr:<id>"`. A PBR candidate downloads its 1K maps (~1–10 MB) into the cache once when applied
and then works offline; its real size sets `tile_m` (ambientCG often has none: 2 m, override `tile_m` if the
pattern looks too big or small). Offline without the cache it is skipped (`skipped`) for the next candidate.
`substitute` / empty = no such material: say so and use a preset or color.
Licenses (NVIDIA vMaterials / Omniverse materials, CC0 with the download URL and version) go into LICENSES.md
"Materials" and "Sources".
Ceiling: `ceiling: true` gives it its own surface, not the walls', chosen by `place` (home / cafe / restaurant
→ `ceiling_board` = white gypsum board; warehouse / factory / home_center → `metal_deck` = exposed folded steel
deck; other indoor places → `ceiling_tile` = 600 mm office grid), or without a place by lighting. Give a preset, a surface or words
(`set_room(ceiling="white plaster")`, target `"ceiling"` for view_surface_candidates / select_surface) to change it.
Accent wall (「北の壁だけレンガ」「一面だけ紺色」): `walls={"*": "paint_white", "N": "brick"}` (S/N/W/E; plans use
the plan wall ids; `"*"` = the other walls). Values are presets, surfaces or words (searched per wall:
target `"walls:N"`). 腰壁: `wainscot: {height: 0.9, surface: "wood_light", walls: ["S", "N"]}`; 巾木:
`baseboard: true` (6 cm white, `trim_white`) or `{height, thickness, surface: "trim_wood", color}`. Both are thin
boxes on the inner faces, cut at doors, stopping at window sills; against_wall keeps clear of them. Typical:
home 巾木 `trim_wood` or white 6 cm, 腰壁 0.9 m in a hallway / café / 和室 (`wood_light`, `wood_dark`).

## Interior finish pieces (「ガラス張りの会議室」「小上がりのある和食店」「OA フロア」「天井の設備も」)

Partitions, floor pieces, wall panels and ceiling equipment come from an interior asset folder (`local:<dir>`) if
there is one; otherwise SimReady stand-ins, reported as substitutes. Example: `examples/office_interior.json` (office with a glass meeting room, raised floor,
ceiling equipment).

- Queries: ガラス間仕切り "glass partition" / その扉 "glass partition door"; デスクの間仕切り "office partition panel";
  衝立 "japanese folding screen"; 小上がり "raised tatami platform"; 上がり框 "entrance step"; 置き畳 "tatami mat";
  タイルカーペット "carpet tile"; OA フロア "raised floor panel"; 床下点検口 "floor access hatch"; 床のタイル "floor tile panel";
  木のルーバー壁 "wood slat wall panel"; 有孔ボード "pegboard wall panel"; キッチンの壁タイル "tile backsplash";
  天井カセットエアコン "ceiling cassette air conditioner"; 吹出し口 "ceiling air diffuser"; スプリンクラー "sprinkler head";
  煙感知器 "smoke detector"; 非常用照明 "emergency light"; 天井点検口 "ceiling access hatch"; 避難口誘導灯 "emergency exit sign".
- Floor pieces (tatami, carpet tiles, raised floor, platforms) are objects on the floor; what stands on them uses
  `layout.on` with `offset` (right, front in the support's frame). Keep the center strictly inside the support
  (0.40 on a 0.82 m tatami, not 0.41).
- Wall panels sit flush: `against_wall` with `gap: 0`. Things hung on the wall at a height (backsplash, exit sign,
  poster) use `layout: {against_wall: "<wall>", wall_height: <m from the floor to its bottom>}` (backsplash 0.9 over
  the counter, exit sign 2.2 above the door with `along` to center it on the door); see "Ceiling lights, beams and posters".
- Ceiling equipment uses `layout.ceiling: true` and goes between the light fixtures: the fixtures stand on
  `fixture_grid` (cell centers, spacing home 2.5 m / office 2.4 m), so put an air conditioner ≥ 0.6 m and smaller
  pieces ≥ 0.15–0.3 m away from those points.
- The exit sign's picture fits every place (`screen.places ["any"]`), so it is not pushed back as a wrong picture.
- Many office chairs (about 10 or more): query "black office chair" (sm_chair_office_amf_black). The first hit for
  "office chair" (sm_chair_office_fabric_manager) has jointed casters, and 12 of them stopped the Isaac Sim 6.1
  GPU PhysX with CUDA error 700; one or two are fine.

## Ceiling lights, beams and posters (「ダウンライト」「ペンダント照明」「梁が出ている」「壁にポスター」)

- Fixtures with a body: `set_room(fixtures="downlight" | "pendant" | "line" | "panel")` or `{kind, spacing, size,
  drop, axis, color}` replaces the preset's bare ceiling lights. The preset (`lighting`, `lighting_scale`) still sets
  how bright the floor is, so changing the kind keeps the brightness; `light_kelvin` overrides the kind's color
  (downlight 3500 K, pendant 2700 K, line / panel 5000 K). Typical: home / café ダウンライト 1.5 m or pendant over a
  table (1.8 m, black shade), office / supermarket / bookstore ライン照明 (`line`, 2 m, along the longer side) or
  `panel` (600 角). `fixtures: false` goes back to the preset. A ceiling lit only by downward lights shows the color
  of the floor and shelves bounced up (a dark wood floor gives a brown ceiling): when the photo or request has a
  bright ceiling, hang the lights (`drop: 0.3`) with `uplight: 0.3` (a pendant also takes uplight; recessed lights cannot). Pendants keep 2.1 m under the shade and are
  obstacles: a tall shelf under one is `hits_fixture` (move it, or set `spacing` so the pendants miss it).
- Beams and soffits (梁・下がり壁): `ceiling_features: [{kind: "beam", axis: "x" | "y" (the way it runs), at: <m> or
  spacing: <m>, width 0.3, drop 0.4, color / surface}, {kind: "soffit", wall: "N", width: 0.6, drop: 0.3}]`. They
  default to the ceiling's surface, fixtures under them hang from their underside, and anything taller than the
  space under them is `hits_beam`. Exposed beams in a café: `color: "#6b4a2b"`; a concrete office: `surface: "concrete"`.
- Posters / POP on a wall: a `local:` folder's `wall_poster` (front −Y, 0.55 × 0.76 m) with `layout: {against_wall: "N",
  along: 0..1, wall_height: 1.2}` (bottom of the poster 1.2 m above the floor; eye level 1.5 m is its center). It
  hangs flat on the wall above the 腰壁 / 巾木. The picture comes from `set_screen` (or `{"Picture": "Blank"}`),
  as for the signage above. A row of posters: `array {count, spacing}` with the same layout.

## Steps, stairs and railings (「奥が一段高い」「小上がり」「階段がある」「ステージ」)

- `set_room(levels=[{id, rect [x0,y0,x1,y1] | polygon, height, floor / color, rail ["south", ...]}])`: a solid raised
  floor (0 < height ≤ 2 m). Objects whose footprint is inside stand on it (z = height, also after move_object);
  one across its edge is `on_step_edge` (move it fully on or off). An `array` stands on one floor (the one under
  its center): a row of shelves along a wall that runs onto the level is two objects, one block below and one on it. `rail` lists the edges with a railing (rect:
  south / north / west / east; polygon: edge numbers, 0 = first point to second).
- `stairs=[{id, level, edge, offset, width 1.0, rail}]` go up onto a level's edge (offset = m along the edge from
  its west / south end to the stairs' center; risers ≤ 0.18 m, treads 0.28 m, the last riser is the level's edge;
  the level's railing opens there). A stair to an upper floor that is not modelled: `{id, at [x, y] (foot center),
  up: "north" | degrees, rise: 3.0}` (17 steps, 4.8 m long: keep it inside the room). `rail: left | right | both`
  adds a handrail 0.9 m above the nosings (left = left going up). Steps are solid colliders: things on them are
  `hits_stairs`, on railings `hits_rail`. `color` / `surface` per level and stairs (default the room floor).
- A 小上がり with tatami: `levels: [{id: "agari", rect: [...], height: 0.4, floor: "tatami"}]` instead of the
  raised tatami platform asset when it covers a whole part of the room; keep the asset for a single platform.

## Columns and part of the floor (「柱がある」「入口だけタイル」「一角にカーペット」)

- `set_room(columns=[{id, at [x, y] (center), size 0.5 (side, or [x, y] with yaw_deg) | diameter (round),
  height (default the ceiling), surface / color (default the walls)}])`: solid posts (static colliders). Things in
  them are `hits_column`; the nav map and the walking checks go around them. Place shelves and tables around
  columns (a gap or a row split in two), never through them. From a photo: a column standing in the room is a
  column, not a wall corner: give it here instead of bending the walls.
- `floor_areas=[{id, name, rect | polygon, floor (preset / words, searched as target "floor:<id>") | color}]`: part
  of the floor with its own finish in any room (a tile entrance mat, a carpeted corner, a darker aisle). Later
  areas lie over earlier ones. For a whole plan zone use `zone_floors` instead; area ids must not be zone ids.
  Both lists are replaced as a whole; `[]` removes them.
- Robots (`nav_map`): level edges higher than `step` (0.02 m) and stairs / railings are blocked; the level's top is
  drivable but a wheeled robot cannot get onto it.
