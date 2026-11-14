# Synthetic data, variations, robot navigation

Part of the world-gen skill ([SKILL.md](SKILL.md)); read it when the request needs it.

## Synthetic data (「このシーンで学習データを作って」)

`generate_data(scene_dir, sdg, dry_run)` runs Replicator in Isaac Sim (Ubuntu). It randomizes camera poses
(in free floor space, aimed at objects) and the light intensity / color temperature (optionally floor / wall
colors). It writes RGB, tight 2D boxes, semantic + instance segmentation and depth (BasicWriter; add
`"writers": ["coco"]` or `"kitti"`). It returns a contact sheet (RGB with boxes | segmentation): look at it.

1. Settle the scene first (compose + check_in_isaac OK). Classes come from the asset's class
   (SimReady `wikidata_class`), else the query. Fix odd ones per object with `sdg.classes`
   (`{"tv": "tv"}`) or `objects[].semantic_class`. Floor / walls / ceiling are labeled too.
   For a detector of a few classes, map those objects in `sdg.classes` and set `"others": "background"`:
   everything else gets no label (no box, not in COCO / KITTI). Never invent an `"other"` class for them
   (`""` in `classes` leaves one object unlabelled). `"structure": false` leaves floor / walls / ceiling unlabelled.
2. `dry_run: true` first: it only plans (no Isaac Sim) and shows classes, sample poses and warnings
   (unknown targets, cramped rooms with no clear line of sight).
3. Typical settings:

   | field | use |
   |---|---|
   | `frames` | 20-200 (about 1 s per frame at 1280x720) |
   | `camera.height` | [0.6, 1.8]; robot eye level e.g. [0.3, 0.5] |
   | `camera.targets` | focus on some objects |
   | `camera.zone` | one room of a floor plan |
   | `randomize.room_colors` | more variety |
   | `seed` | a different set |

   For texture variety swap the materials themselves: `randomize.materials` with `floor` / `walls` / `ceiling`.
   Each is a list of presets / `"mdl:<id>"` (`["wood_dark", "tatami", "carpet_gray"]`), an English query string
   (`"parquet wood"`), or `{"surfaces": [...], "query": "...", "category": "Wood", "tags": [...], "count": 6,
   "keep_current": true}`. Each frame picks one per surface (all used evenly); labels stay floor / wall / ceiling.
   The dry run lists the candidates under `materials` — check they fit the place before running.
4. Report: frames written / ok, class counts, materials used (summary `materials`), the output folders, and
   anything odd on the sheet (objects missing boxes, black frames).

## Variations (「バリエーションを 10 個」「強化学習用に部屋違いを何通りか」)

`make_variants(scene_dir, count, seed, vary, size_jitter)` makes `count` separate scenes from a settled one in
`<scene>/variants/NN/` (scene.usda, layout.svg, preview.html, LICENSES.md, variant.json) plus `variants/index.html`
(each top view with what changed). Unlike `generate_data` (lights / materials / cameras inside one scene), each
variant is a scene of its own: other assets, objects moved along their wall, other floor / wall presets, another
room size.

1. Settle the scene first (issues none, check OK, layout options chosen). Variants keep its relations
   (in_front_of, on, arrays) and never draw a substitute.
2. `vary` picks what changes (default all):

   | vary | what changes |
   |---|---|
   | `assets` | among the first 5 candidates that match no worse than the chosen one, with no other line of work / size / picture |
   | `layout` | objects with only `against_wall`: `along` ±0.25 |
   | `materials` | floor / walls when they are preset names, from the presets usual for the place |
   | `size` | rectangular rooms, ±`size_jitter` (default 0.1) |

   A plan, an outdoor site or a finish of its own (a texture, an MDL found by words) is kept.
3. A draw with more layout issues than the source is drawn again (up to 4 tries). If every try is still worse,
   the one with the fewest issues is kept and flagged (`more_issues`, red on the index). Say which, and offer
   another `seed` or fewer `vary` kinds.
4. Report the index (`/<scene>/variants/index.html` on the preview server, or the file), the count and the
   changes in a line each. Do not check every variant in Isaac Sim unless asked; check one if the user will use
   them for physics.

## Robot navigation

**Robot navigation**: when the scene is for a mobile robot (navigation, delivery, cleaning, a digital
twin a robot drives in), call `nav_map` once the layout is issue-free. It writes a ROS / Nav2 map
(`nav/map.pgm` + `map.yaml`, map frame = the stage's XY) and reports where a robot of that size reaches
from the doors:

| field | meaning |
|---|---|
| `reachable` | share of the floor |
| `unreachable_zones` | each with `widest_m` / `narrowest_at`: the widest way there and where it pinches |
| `routes` | plans with zones: the narrowest width on the way from the doors to each zone and where; `tight` when under `need_width` |
| `unreachable_objects` | floor objects it cannot get within 0.25 m of |
| `pockets` | open floor it fits in but cannot get to (behind furniture) |

Defaults: radius 0.3 m, height 1.2 m (objects lower than 0.05 m are ignored); pass the user's robot size when
they name one. The default map uses the objects' boxes, so under a desk is blocked. `isaac=true` (after
`compose_scene`, ~1 min) also maps the colliders in Isaac Sim — legs instead of boxes, so a robot lower than
the table top drives under it — and returns both side by side. Tell the user the reachable share and what is
out of reach that a robot should get to, widen the way (`set_layout` / `move_object`) if they want, and run it
again. The status keeps the result (`nav`, marked stale after edits) and the preview shows「ロボットの走行」.
