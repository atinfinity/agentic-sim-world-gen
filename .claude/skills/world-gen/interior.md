# Interior finish pieces, lights, steps, columns

Part of the world-gen skill ([SKILL.md](SKILL.md)); read it when the request needs it.

## Floor, wall and ceiling finishes

**By words** (「明るい木目の床」「白いタイルの壁」, a look no preset has): `set_room(floor="light wood planks",
floor_alt_queries=["pale oak flooring"], walls="white glazed tile", walls_alt_queries=[...])`. Words in create_scene's
room work the same, without alt queries. The words search two pools (`source` in each candidate says which):

- ~2600 MDL materials bundled with Isaac Sim (Base + vMaterials 2);
- ~2500 CC0 PBR textures of ambientCG / Poly Haven (photo-scanned tatami, wallpaper, plaster, wood floors, paving).

Write color + material + pattern in English (herringbone parquet, polished concrete, red brick, white plaster,
gray carpet, marble). The top match is applied (RTX MDL, its thumbnail color for the previews, friction by
category); `surface_candidates` lists the rest. Look with `view_surface_candidates(target)` and switch with
`select_surface(target, index)`, or pass `"mdl:<id>"` / `"pbr:<id>"`.

- A PBR candidate downloads its 1K maps (~1–10 MB) into the cache once when applied, then works offline.
  Offline without the cache it is skipped (`skipped`) for the next candidate.
- Its real size sets `tile_m`. ambientCG often has none: then 2 m; override `tile_m` if the pattern looks too big or small.
- `substitute` / empty = no such material: say so and use a preset or color.
- Licenses (NVIDIA vMaterials / Omniverse materials, CC0 with the download URL and version) go into LICENSES.md
  "Materials" and "Sources".

**Ceiling**: `ceiling: true` gives the ceiling its own surface (not the walls'), chosen by `place`:

| place | ceiling |
|---|---|
| home, cafe, restaurant, bakery, variety_store, florist, greengrocer, wagashi_shop, record_store, asian_grocery | `ceiling_board` (white gypsum board) |
| warehouse, factory, home_center, garden_center | `metal_deck` (exposed folded steel deck) |
| plant_factory | `metal_panel` |
| other indoor places | `ceiling_tile` (600 mm office grid) |
| no place (or `other`) | by lighting: office → `ceiling_tile`, warehouse → `metal_deck`, else `ceiling_board` |

To change it, give a preset, a surface or words (`set_room(ceiling="white plaster")`; target `"ceiling"` for
view_surface_candidates / select_surface).

**Accent wall** (「北の壁だけレンガ」「一面だけ紺色」): `walls={"*": "paint_white", "N": "brick"}` (S/N/W/E; plans use
the plan wall ids; `"*"` = the other walls). Values are presets, surfaces or words (searched per wall: target `"walls:N"`).

**腰壁・巾木**: `wainscot: {height: 0.9, surface: "wood_light", walls: ["S", "N"]}`; `baseboard: true` (6 cm white,
`trim_white`) or `{height, thickness, surface: "trim_wood", color}`. Both are thin boxes on the inner faces, cut at
doors and stopping at window sills; against_wall keeps clear of them. Typical: a home's巾木is `trim_wood` or white
6 cm; 腰壁0.9 m in a hallway / café / 和室 (`wood_light`, `wood_dark`).

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
- Floor pieces (tatami, carpet tiles, raised floor, platforms) are objects on the floor. What stands on them uses
  `layout.on` with `offset` (right, front in the support's frame). Keep the center strictly inside the support
  (0.40 on a 0.82 m tatami, not 0.41).
- Wall panels sit flush: `against_wall` with `gap: 0`. Things hung on the wall at a height (backsplash, exit sign,
  poster) use `layout: {against_wall: "<wall>", wall_height: <m from the floor to its bottom>}`: backsplash 0.9 over
  the counter; exit sign 2.2 above the door, with `along` to center it on the door. See "Ceiling lights, beams and posters".
- Ceiling equipment uses `layout.ceiling: true` and goes between the light fixtures. The fixtures stand on
  `fixture_grid` points (cell centers; spacing home 2.5 m / office 2.4 m): keep an air conditioner ≥ 0.6 m and
  smaller pieces ≥ 0.15–0.3 m away from those points.
- The exit sign's picture fits every place (`screen.places ["any"]`), so it is not pushed back as a wrong picture.
- Many office chairs (about 10 or more): query "black office chair" (sm_chair_office_amf_black). The first hit for
  "office chair" (sm_chair_office_fabric_manager) has jointed casters; 12 of them stopped the Isaac Sim 6.1
  GPU PhysX with CUDA error 700. One or two are fine.

## Ceiling lights, beams and posters (「ダウンライト」「ペンダント照明」「梁が出ている」「壁にポスター」)

- Fixtures with a body: `set_room(fixtures="downlight" | "pendant" | "line" | "panel")` or `{kind, spacing, size,
  drop, axis, color}` replaces the preset's bare ceiling lights. `fixtures: false` goes back to the preset (and
  keeps the place's default off).
- By default a room with a ceiling gets its place's fixtures on the preset's grid: office / hospital
  `panel`, school / lab / station / most shops `line`, café / restaurant / boutiques `downlight` (home, factory,
  warehouse, home center: none). It is recorded as a guess (`照明器具`); say it, and `set_room(fixtures=...)` when
  the user wants another kind or none. Light fixtures placed as objects (`layout.ceiling`, e.g. from a photo)
  replace the default, so do not add both.
  The preset (`lighting`, `lighting_scale`) still sets how bright the floor is, so changing the kind keeps the
  brightness. `light_kelvin` overrides the kind's color temperature:

  | kind | 例 | color | default spacing | typical use |
  |---|---|---|---|---|
  | `downlight` | ダウンライト | 3500 K | 1.5 m | home / café |
  | `pendant` | ペンダント照明 | 2700 K | 1.8 m (black shade) | home / café, over a table |
  | `line` | ライン照明 | 5000 K | 2.0 m (along the longer side) | office / supermarket / bookstore |
  | `panel` | 600角のパネル | 5000 K | 2.4 m | office / supermarket / bookstore |

- A ceiling lit only by downward lights shows the color of the floor and shelves bounced up (a dark wood floor
  gives a brown ceiling). When the photo or request has a bright ceiling, hang the lights (`drop: 0.3`) with
  `uplight: 0.3`. A pendant also takes uplight; recessed lights cannot.
- Pendants keep 2.1 m under the shade and are obstacles: a tall shelf under one is `hits_fixture` (move it, or
  set `spacing` so the pendants miss it).
- Beams and soffits (梁・下がり壁): `ceiling_features: [{kind: "beam", axis: "x" | "y" (the way it runs), at: <m> or
  spacing: <m>, width 0.3, drop 0.4, color / surface}, {kind: "soffit", wall: "N", width: 0.6, drop: 0.3}]`. They
  default to the ceiling's surface, and fixtures under them hang from their underside. Anything taller than the
  space under them is `hits_beam`. Exposed beams in a café: `color: "#6b4a2b"`; a concrete office: `surface: "concrete"`.
- Posters / POP on a wall: the retail `wall_poster` (front −Y, 0.55 × 0.76 m) with `layout: {against_wall: "N",
  along: 0..1, wall_height: 1.2}` (the poster's bottom 1.2 m above the floor puts its center at eye level, 1.5 m).
  It hangs flat on the wall above the腰壁 / 巾木. The picture comes from `set_screen` (or `{"Picture": "Blank"}`),
  as for signage. A row of posters: `array {count, spacing}` with the same layout.

## Wall and ceiling equipment (「エアコン」「窓の下の暖房器」「壁のスピーカー」「天井の換気口」「コンセント」)

Equipment fixed to the walls or the ceiling is neither furniture nor clutter, so it is easy to leave out (a
classroom video lost its radiators under the windows, the wall speakers and the ceiling vents).
**Place what is big enough to see in the render** (about 0.3 m or more): air conditioners, radiators / heaters,
speakers, ceiling vents and diffusers, a wall clock, exit signs, a fire extinguisher. Skip outlets, switches,
thermostats and smoke detectors unless the user asks or the world is for close-up work (a robot pressing a switch,
synthetic data of these things).

| 設備 | query (alt_queries) | usual size of the wall-mounted kind (long, short, height) | how to place | in the catalog (2026-11-16) |
|---|---|---|---|---|
| 壁掛けエアコン | "air conditioner" ("wall mounted air conditioner", "split air conditioner") | 0.8-1.0, 0.2-0.3, 0.25-0.35 | against_wall, wall_height 1.9-2.1 (0.1-0.3 m under the ceiling) | **none**: SimReady's are 2.5-4.5 m rooftop / precision units (`off_size` for "air conditioner"). A ceiling cassette and diffuser can come from an interior asset folder (`local:<dir>`, see "Interior finish pieces") |
| ラジエーター・暖房器（窓の下） | "radiator" ("panel radiator", "wall heater") | 0.6-1.6, 0.06-0.15, 0.4-0.7 | against_wall, wall_height 0.1-0.15, along = under the window | **none**: car radiators (off_place in a home / office / school) and Poly Haven's stoves (off_size) |
| 壁のスピーカー | "wall speaker" ("speaker", "loudspeaker") | 0.2-0.35, 0.15-0.25, 0.3-0.5 | against_wall, wall_height 2.0-2.2 | **none**: Poly Haven's megaphone and boombox only |
| 天井の換気口・吹出し口 | "ceiling vent" ("air vent", "ceiling air diffuser") | 0.3-1.0 square, 0.05-0.1 | `layout.ceiling: true` | SimReady `extractor_ceiling_a01` (0.95 × 0.95 × 0.08): query "ceiling vent", which lists it first. "air vent" alone lists car parts (an armrest, a hood) and a smoke detector before it, with or without a place |
| 壁の時計 | "wall clock" | 0.25-0.35 round | against_wall, wall_height 1.9-2.1 | SimReady, Poly Haven `wall_clock` |
| 誘導灯 | "emergency exit sign" ("exit sign") | | wall_height 2.2 above the door | SimReady (hospital signs) |
| 消火器 | "fire extinguisher" | | on the floor against_wall, or wall_height 0.6 | SimReady, Isaac |
| 煙感知器・火災報知器 | "smoke detector" / "fire alarm" | ~0.12 | ceiling / wall_height 1.5 | SimReady, Poly Haven `fire_alarm` |
| コンセント・スイッチ | "electrical outlet" / "light switch" | ~0.07 × 0.11 | wall_height 0.25 / 1.2 | SimReady (`sm_socket_*`) |

- The sizes in the table are guides for the wall-mounted kind, not the server's defaults. Without min/max_size the
  server uses wider typical ranges (`assets/sizes.py`: "air conditioner" up to 1.2 m long and 0.8 m high, also
  covering a 0.95 m ceiling cassette; "radiator"; "vent"), so a rooftop unit or a Poly Haven stove is `off_size`
  and goes last. Passing the table's values as min/max_size drops a ceiling cassette (0.95 m square): give limits
  only for the kind you saw. For a data center's or a roof's big units, query "precision air conditioner" /
  "rooftop air conditioner" (their own typical sizes).
- Give the room's `place` (home, office, school ...): it sends car parts and other trades' assets after the room's
  own (`match.off_place`).
- Wall equipment hangs: `layout: {against_wall: "<wall>", along: 0..1, wall_height: <m to its bottom>}` (no rigid
  body, nothing holds it up in physics). It is not floor furniture: do not locate it in fit_photos, and
  `photo_placement` / place_from_photos leave it out. Read `along` off the frames (a radiator under a window:
  the window's center along that wall).
- **No asset of that shape** (a radiator, a wall air conditioner, a wall speaker): do not stand in something of
  another shape or trade (a car radiator, a rooftop unit, a megaphone). Leave it out and name it in the report
  (「窓の下のラジエーターは該当アセットが無いので置いていない」); the user's own USD can add it (`local:<dir>`).
  Do not build boxes for it either.

## Steps, stairs and railings (「奥が一段高い」「小上がり」「階段がある」「ステージ」)

- `set_room(levels=[{id, rect [x0,y0,x1,y1] | polygon, height, floor / color, rail ["south", ...]}])`: a solid raised
  floor (0 < height ≤ 2 m). Objects whose footprint is inside stand on it (z = height, also after move_object);
  one across its edge is `on_step_edge` (move it fully on or off). An `array` stands on one floor (the one under
  its center), so a row of shelves along a wall that runs onto the level is two objects: one block below and one on it.
  `rail` lists the edges with a railing (rect: south / north / west / east; polygon: edge numbers, 0 = first point to second).
- `stairs=[{id, level, edge, offset, width 1.0, rail}]` go up onto a level's edge. `offset` = m along the edge from
  its west / south end to the stairs' center. Risers ≤ 0.18 m, treads 0.28 m, the last riser is the level's edge;
  the level's railing opens there.
- A stair to an upper floor that is not modelled: `{id, at [x, y] (foot center), up: "north" | degrees, rise: 3.0}`
  (17 steps, 4.8 m long: keep it inside the room).
- `rail: left | right | both` adds a handrail 0.9 m above the nosings (left = left going up). Steps are solid
  colliders: things on them are `hits_stairs`, on railings `hits_rail`. `color` / `surface` per level and stairs
  (default the room floor).
- A小上がりwith tatami that covers a whole part of the room: `levels: [{id: "agari", rect: [...], height: 0.4,
  floor: "tatami"}]` instead of the raised tatami platform asset. Keep the asset for a single platform.

## Columns and part of the floor (「柱がある」「入口だけタイル」「一角にカーペット」)

- `set_room(columns=[{id, at [x, y] (center), size 0.5 (side, or [x, y] with yaw_deg) | diameter (round),
  height (default the ceiling), surface / color (default the walls)}])`: solid posts (static colliders). Things in
  them are `hits_column`; the nav map and the walking checks go around them. Place shelves and tables around
  columns (a gap or a row split in two), never through them. From a photo, a column standing in the room is a
  column, not a wall corner: give it here instead of bending the walls.
- `floor_areas=[{id, name, rect | polygon, floor (preset / words, searched as target "floor:<id>") | color}]`: part
  of the floor with its own finish in any room (a tile entrance mat, a carpeted corner, a darker aisle). Later
  areas lie over earlier ones. For a whole plan zone use `zone_floors` instead; area ids must not be zone ids.
- Both lists (`columns`, `floor_areas`) are replaced as a whole; `[]` removes them.
- Robots (`nav_map`): level edges higher than `step` (0.02 m) and stairs / railings are blocked. The level's top is
  drivable, but a wheeled robot cannot get onto it.
