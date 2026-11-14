---
name: world-gen
description: Build an Isaac Sim world (USD) from a Japanese or English description of a room or space, using the agentic-sim-world-gen MCP tools (SimReady Central and Isaac Sim bundled assets). Use when the user asks to make, generate, or lay out a scene, room, world, or environment for Isaac Sim, e.g. 「6畳の書斎を作って」「会議室のワールドを生成して」.
---

# World generation (agentic-sim-world-gen MCP)

Turn the user's request into a SceneSpec and build a draft right away. Let the user react to it in the preview
(say what is different), then check it in Isaac Sim and report the licenses. Most users cannot describe the scene
they want in detail up front, but they can say what is wrong with one they see.

Talk to the user in their language (usually Japanese) in every message: progress notes, one-line notes between
tool calls (「通りました。スクショを見ます。」, not "Passed. Let me look at the screenshot."), and after a context
compaction (the summary is in English; your replies are not). Search queries are always English.

## Fewer turns

Each tool call is a turn; the shop trials took 39–49 turns where 20–25 would do.

- **Tool details.** The tool list carries only each tool's summary and one-line argument types. A tool whose
  description ends with `(More: tool_help("<tool>").)` has its full notes and argument fields behind
  `tool_help(name)` (mostly 1–5k characters per tool, set_room 11k; `model="Room"` for a nested type's fields).
  Call it when you need an option the summary does not show. **Do not Read tools.md** (110 KB, the same text for
  all tools at once; it filled the context in the photo trials). Check a tool's argument names before the
  first call: a wrong name costs a turn each time. A validation error already carries the bad argument's
  type: fix the call from the error.
- **Load the agentic-sim-world-gen tools once.** In Claude Code they are deferred: one `ToolSearch` with every tool the job
  may need, not one search per tool, e.g. `select:mcp__agentic-sim-world-gen__plan_questions,mcp__agentic-sim-world-gen__create_scene,`
  `mcp__agentic-sim-world-gen__get_scene,mcp__agentic-sim-world-gen__view_candidates,mcp__agentic-sim-world-gen__select_asset,mcp__agentic-sim-world-gen__research_object,`
  `mcp__agentic-sim-world-gen__move_object,mcp__agentic-sim-world-gen__move_objects,mcp__agentic-sim-world-gen__set_layout,mcp__agentic-sim-world-gen__set_array,mcp__agentic-sim-world-gen__set_fill,`
  `mcp__agentic-sim-world-gen__set_room,mcp__agentic-sim-world-gen__add_objects,mcp__agentic-sim-world-gen__remove_object,`
  `mcp__agentic-sim-world-gen__set_variants,mcp__agentic-sim-world-gen__set_assumptions,mcp__agentic-sim-world-gen__check_request,mcp__agentic-sim-world-gen__finish`.
  Add photo / video / SDG / floor-plan tools only when the request has them. Add `set_purpose`, `fill_windows`,
  `open_preview`, `compose_scene`, `check_in_isaac` or `license_report` only to redo that one step.
- **`create_scene` opens, `finish` closes.** `create_scene` also records the purpose, fills the windows,
  opens the preview and, when the layout has no issue, writes the USD and shoots the first draft in Isaac Sim
  (`started` says what each step did). `finish` writes the USD if it is behind, runs the full check, re-measures
  the request items and reports the licenses. Do not call those steps one by one.
- **Batch.** Put every object you already know (fixtures *and* their `fill`) in `create_scene`. Add the rest with
  one `add_objects([...])`, drop several with `remove_object(["a", "b"])`, move several with
  `move_objects([{id, position, facing_deg}, ...])` and give several the same variants with
  `set_variants(["a", "b"], {...})`, not one call each. Put mugs, books and goods on a surface with `layout.on`
  (+ `offset`) inside the `add_objects` list, not one `set_layout` per item; for many identical things use `fill`.
- **Editing tools answer with what changed**: the objects, decor lines and sections that changed since
  your last agentic-sim-world-gen result, plus the issues, every hint and the `summary` counts; the rest is as before. Call
  `get_scene` only when you need everything again. `reply_language` / `reply_language_hint` repeat the user's
  language: follow it. The other scene tools (compose_scene, check_in_isaac, view_fronts, …) carry them too.
- **Don't explore with the shell.** `search_assets` / `kind_availability` answer "is there an X"; the examples
  and the asset folders don't need `ls` / `cat`. If you must run Python, it is `python3` (there is no `python`).
- **Fix automatic stock in place.** `density` stocks empty fixtures (`decor_*`). Wrong goods: `research_object`
  on that decor object; too many: `set_fill` on it with fewer `levels` / `rows`. Don't remove the decor and add
  the same thing back by hand.

## Reference files

Read only what the request needs (each is a few thousand tokens):

| the request has | read |
|---|---|
| a video of the room | [videos.md](videos.md), then [photos.md](photos.md) |
| photos of the room to rebuild | [photos.md](photos.md) |
| a reference image / mood board for the look, 「雰囲気はおまかせ」, a taste warning | [look.md](look.md) |
| a shop, restaurant or plant factory (sizes, layouts, stocking shelves, hooks, kinds not in the list) | [shops.md](shops.md) |
| signage screens, posters, POPs, an image on a screen | [pictures.md](pictures.md) |
| a floor plan (JSON / DXF / IFC / image / PDF) | [plans.md](plans.md) |
| an outdoor site, or the street / parking / garden outside a room | [outdoor.md](outdoor.md) |
| partitions, raised floors, ceiling fixtures, beams, posters on walls, steps / stairs, columns, part of the floor | [interior.md](interior.md) |
| training data (SDG), variations, a mobile robot's map | [data.md](data.md) |
| an option a tool's summary does not show | `tool_help("<tool>")` (not tools.md) |

## At the start of every turn

When `status` / `get_scene` carries one of these (the user acted on the preview page), handle it first:

| field | what it is | what to do |
|---|---|---|
| `preview_edits` | the user's own edits on the page since your last step (step 3). Moves are saved like `move_object`, so the object's `layout` is cleared. | Mention them (「プレビューでオフィスチェアを移動されたのを確認しました」) and keep them: don't `set_layout` those objects again unless the user asks. A「…の時点に戻す」line = the user went back: read the scene again, do not re-apply what they undid. |
| `assumption_feedback` | corrections the user typed on rows of the preview's「仮定」table (step 5) | Say so (「プレビューで窓と机の位置に指摘をいただいたので直します」) and fix exactly what each `feedback` asks. Re-record each of those rows with `given: true` (that clears the feedback). Do not touch other rows or objects the user moved (`preview_edits`). If a feedback is unclear, ask about that row only. |
| `notes` (in `status`) | short notes the user wrote on the preview (step 5) | See "Notes on the preview" in step 5. |

## Flow

1. **Draft first, ask later.** Call `plan_questions(request=<the user's words>)` first. For each kind of place
   it knows the items that change the result most (home: room / size / taste / window; office: people / desk
   layout / meeting space; convenience store: size / entrance / register / cold-case wall; warehouse: rack
   rows / dock / aisle width …) and drops what the request already says. **Do not ask them now.** Build with
   each question's default (`defaults`, the first option). Right after `create_scene`, record each with
   `set_assumptions` as a guess (basis「叩き台の既定」, `options` = the question's options). The user answers
   them in step 5 by looking at the draft, where a wrong guess is easy to spot and fixed with one word.
   - **Activity** (home / office / school / lab): instead of the size and the number of people, the
     question is「誰が・何人で・何をする場所ですか？」(most users can answer that, but not a size). When the
     request already says it (`activity` in the result: 「4人で会議、画面共有あり」), build with its `room` and
     `objects` (what the user named wins) and record its `assumptions` as they are (basis「活動から」).
     Otherwise it is the first question: call `derive_activity(<the answer, or the default for the draft>, place)`
     and do the same. What a robot does there is the purpose question, not the activity.
   - **Order.** The questions come ordered by impact × uncertainty (what rebuilds the whole room comes
     first). `later` (the look, small things) is never asked: record its default as a guess the same way; the
     user fixes it on the preview. `preferred` is answered by the user's earlier scenes: do not ask, and
     leave those fields out of the spec so that `create_scene` applies the remembered value (「前回の好み」).
   - **Gallery.** When `plan_questions` returns `gallery` (the place has no question table of its own:
     hospital, school, station, lab, ...) or the request is too vague to draft from, call `show_gallery(request)`
     first. Show its `url` and name the `near` entries in a line each (title, size, main fixtures). The user picks
     on the page (`picked` on the next `show_gallery`) or names one → call `start_from_example(<id>)` instead of
     `create_scene` (`room` overrides the size / place / name the user gave, `drop` leaves fixtures out). It
     records what came from the example (size, place, floor, walls, lighting, fixtures) as guesses with the basis
     作例「…」から: say them in the draft's report, and record what the user said as given. If nothing is near,
     or the user wants none of them, write the SceneSpec yourself as usual.
   - **Conflicts.** When `create_scene` returns `conflicts` (the room too small for what goes in it, a
     robot wider than the aisles, fewer seats than people, a fixture taller than the ceiling), no draft was made.
     Before anything else, ask its **one** question (`conflicts.ask`: which side wins; AskUserQuestion in Claude
     Code). If the user saidおまかせ / 確認は不要, do not ask: apply each `left_to_us` (the roomier side:
     `set_room` width / depth / height, `set_array` aisle, more seats) and report what you changed. Record the
     decision (`set_assumptions`, given=true when the user chose), then call `finish(draft=true)`. `conflicts`
     leaves the status once the scene holds.

   **Ask before the draft only when no sensible draft can be made:**
   - `ask_photo` (a vague request: most of the place's items are open and no picture or reference is
     named): add its `say` (「イメージに近い写真があれば貼ってください…」) to your first reply **once**, next to
     `ask_now`'s question or the draft, without waiting for an answer. From then on pass `known=['reference']`.
     A photo comes → [look.md](look.md) (a reference for the look only, not a room to copy); none → go on.
   - `ask_now`: ask its question(s) before building (AskUserQuestion in Claude Code). It holds one of these:

     | `ask_now` asks | when | after「おまかせ」 | with the answer |
     |---|---|---|---|
     | the kind of place | the request names none (「ワールドを作って」) | ask | call `plan_questions` again with it |
     | the purpose (`purpose_unclear`) | the request names a robot but not its job (「ロボット用の店」) | still ask; never assume amr | pass it to `create_scene` as `purpose` (see **Purpose** below) |
     | the **robot's size** | the purpose is amr / manipulation and the request names no machine or size. When the purpose itself is still asked (`after_purpose`), ask the size together with an amr / manipulation answer. | not asked | pass the answer's `radius` (or `robot.radius` when the request said it) as `robot_radius` to `create_scene` / `set_purpose`; わからない → leave it out (0.3 m, a guess) |
     | **a plan or photo** (`existing_place`) | 「うちの」「実際の」「この店」with no file. A made-up room (「6畳の書斎」) is never asked. | not asked | with one, start from it ([plans.md](plans.md) / [photos.md](photos.md) / [videos.md](videos.md), `set_input`) instead of drafting from words; 「ない」 → the draft as usual |
   - The user asked to be asked first (「先に質問して」「確認しながら」): call `plan_questions(..., ask_first=true)`
     and ask its questions in one message (at most 3, options with the default first, marked「（おすすめ）」),
     then record the answers with `given: true`.
   - A floor plan / photo / video the request mentions is missing.

   `no_questions` (「おまかせ」「確認は不要」…): use the defaults with basis「おまかせ（既定）」, and do not stop
   in step 5. On later turns pass `known=[...]` (keys or items) so that answered items are not asked again.

   **Guides for the defaults:**
   - Room type and size. Japanese sizes: 4.5畳2.73×2.73, 6畳3.64×2.73, 8畳3.64×3.64, 10畳4.55×3.64,
     12畳5.46×3.64 (m). Ceiling 2.4 m for homes, 2.7 m for offices.
   - Required objects and rough arrangement ("机は窓側" → ask which wall is the window if it matters).
   - **Purpose.** Unless the request says it (`purpose` in the result), `plan_questions` adds
     「何に使うシーンですか？」. Pass the answer to `create_scene` (`purpose=<name>`, plus `purpose_given=true`
     when the user said it; left out = demo, as a guess). Use `set_purpose` only to change it later. The purpose
     fills in what the user has not decided, as guesses「用途から」: amr → sparse and the robot's aisle width;
     manipulation → lived_in; sdg → lived_in and `sdg` (ask what the detector should find → `targets`, and add
     several of each `purpose_missing`); demo → lived_in. A density the user stated stays. Follow
     `purpose_next` (amr: `nav_map` after compose).
   - A floor plan → [plans.md](plans.md), then `room.plan_file`.
   - Doors and windows matter for placement (nothing may stand in front of a door). Ask where they are if the
     user cares, and add `room.openings` (`{"kind": "door", "wall": "south", "offset": 1.0, "width": 0.8}`;
     `offset` = the center, measured inside the room from the west / south end of that wall). Shop entrances
     (doors only): `"style": "auto_sliding"` = glass sliding doors that open in Isaac Sim when anything with a
     collider comes within `sensor` m (default 1.0).
   - A door asset (leaf / frame) that fills an opening: give it the opening's id (`"opening": "toilet"`, no
     `position` / `layout`). It is centered on the wall and faces into the room; it does not count as out of
     the room, in the wall, or blocking its own door. Give openings an `id` when a door asset uses them.
   - Windows are only holes in the wall until something fills them. `create_scene` fills them
     (`started.windows`); after you add openings, call `fill_windows` again. Each window opening without
     an asset gets `win_<id>`, stretched to the opening (`fit_scale`): an aluminum sash, a floor-length one, a
     small frosted one for a bathroom / toilet, a casement for narrow ones, a fixed one in offices and shops, or
     a shop window for a shop's opening 1.8 m+ wide. At home it also gets a curtain `curtain_<id>` over
     it (`covers`: hung on the room side, 0.1 m above the head and 0.2 m wider; `covers="blind"` for a roller
     blind, `None` for none). Furniture under a window does not clash with the curtain. `opening_fit` = the
     asset had to stretch past 0.75–1.33x, or a curtain is taller than the room (a long one raises its rail up
     to the ceiling): pick another candidate. Windows and curtains come from an asset folder
     (`local:`) if there is one. Without them, an opening whose best asset is no window / curtain (only weak matches, or one
     made for another line of work, such as a car door tagged window) is left empty and listed in `not_found`
    : tell the user the windows stay open holes (or use `set_room(site=true)` so they show the outside).
2. **Write the SceneSpec** and call `create_scene`.
   - **Preferences first**: call `get_preferences(place=<the place>)`. Its `defaults` are the user's own
     corrections from 2+ earlier scenes: leave them out of the spec unless the user asked otherwise this time.
     `create_scene` fills them in as assumptions「前回の好み」 (`preferences_applied`); mention them in step 5.
     Ignore `pending`. 「照明の好みは忘れて」 → `forget_preference(item, target)`.
   - `id`: short ASCII (`desk`, `chair_1`). `label`: Japanese name for previews.
   - `query`: short English noun phrase ("office desk"); `alt_queries`: 1–3 synonyms ("writing desk", "computer desk").
     The last word must be the object itself. Keep colors / materials out ("bookshelf", not "wooden bookshelf"):
     a hit whose class has a different modifier ("coffee table" for "dining table") counts as weak.
   - `min_size` / `max_size`: (footprint long side, short side, height) m. Use them for anything with a typical
     size (see the table below); they stop tiny props (a paper ream) or giant items from winning. Without them
     the server still puts assets far from the kind's typical size last (×0.6 .. ×1.5 of `assets/sizes.py`, plus
     shape: a floor lamp is tall and thin, a rug flat) and flags them `off_size` (`off-size:height` in the brief
     list); passing your own limits turns that off. Read an `off_size` top pick as a substitute.
   - Prefer `layout` relations over coordinates. Walls: south y=0, north y=depth, west x=0, east x=width.

     | `layout` key | use |
     |---|---|
     | `against_wall` | north / south / east / west / any; `along` 0..1 |
     | `in_front_of` | a chair at a desk goes just in front. Tuck it under with a negative `gap` only when the thumbnail shows open knee space (overlaps explode in PhysX). A chair at a table's corner hits its leg: put chairs along a side, clear of the corners. |
     | `next_to` | with `side` |
     | `on` | a lamp on a desk. `on_height` = surface height above the support's bottom when something sticks up above it (a counter top under a POS terminal, the inside of an open stand). `offset` = (right, front) from its center, right as seen facing its front. `facing` turns it, e.g. `"facing": "east"` toward the clerk side. A flat TV with no foot on a stand falls over (`tips_over`): hang it with `wall_height`. |
     | `facing` | an object id or east / west / north / south |
     | `center` | the room's center |
     | `ceiling: true` | ceiling-hung things (hanging signage, displays): puts the top at the ceiling |
   - **Small things.** Set `room.density` to `"normal"` unless the user says otherwise (「すっきり」「最低限」
     「家具だけ」 → `"sparse"`; 「生活感」「使っている感じ」「散らかった」 → `"lived_in"`). List only the furniture
     yourself. `create_scene` adds the small things as objects `decor_*` (`decor: true`): books / pen holder /
     mug on a desk, a plant beside it, magazine / fruit bowl on a coffee table, photo frame / vase / candle on a
     TV stand, monitor / tray on office desks, books on bookshelves, products on every empty shop shelf. Do not
     write those in `objects` too. Each is kept only where it adds no issue or usability warning, is recorded as
     an assumption「生活感のため追加」, and is left out when the catalog has no matching asset (cushions, rugs and
     remotes are not in SimReady / Isaac today; the message names them). Change the level with
     `set_room(density=...)` (rebuilds all `decor_*`). Remove one with `remove_object` (a list removes several;
     removing a desk also removes what was on or beside it).
     What was left out is in status `decor_skipped` (reason `no_asset` / `no_spot` / `crowded` / `no_kind`;
     `blockers` = the host's own problems, e.g. a shelf overlapping the cake case). Fix those problems and the
     item is added by itself on that save (「小物を追加し直し」). Tell the user in the report what was left out
     and why. `no_kind`: a plain `place: "shop"` whose name / request does not say the kind, so nothing
     says what its fixtures hold: `set_room(place=<store kind>)` (rebuilds the decor) or `set_fill` what the
     user asked for. Hooks hold only the kind's own goods (none in a florist / greengrocer / liquor store / plain
     shop). Shops: status `usability_hint` says the next step for `empty_shelves` / `empty_floor` / `wide_aisle`.
   - **Rows of the same thing** (warehouse racks, classroom desks, pallets along a wall): one object with
     `array` {count, rows, spacing, aisle, alternate, back_to_back}, placed as a block by `layout` (see
     examples/warehouse_racks.json). Racks: `alternate: true`, `aisle` ~3.0 for forklifts, ~1.2 for people.
     Store gondolas / double-sided shelves: `back_to_back: true` (rows 2 = one island with shelves on both
     sides). Never fake it with `front_offset_deg: 180`, which makes the declared front the back panel.
     Desk islands with chairs: two arrays at the same position (desks `back_to_back`, chairs `alternate` with
     `aisle` = island depth + chair room) given the **same direction**: the same `facing` in `layout`, or the
     same `facing_deg` in `move_object`. Never the same `yaw_deg`: fronts differ between assets (office chairs
     face -Y), so equal yaws leave the chair rows 90° off the desks.
   - Stock shelves (store gondolas, coolers, showcases, hooks): one object per product with `fill` {on: <shelf id>, levels, units, span}. Read [shops.md](shops.md) "Stocking shelves" first.
   - **Room kind**: always set `room.place` (home, office, shop, convenience_store, supermarket, drugstore, home_center,
     hundred_yen_shop, bookstore, electronics_store, apparel_store, bakery, variety_store, florist, greengrocer,
     toy_store, pet_shop, garden_center, shoe_store, optician, liquor_store, bicycle_shop, wagashi_shop, record_store,
     sporting_goods_store, cosmetics_store, furniture_store, asian_grocery, cafe, restaurant, factory, warehouse,
     datacenter, lab, hospital, school, station, public, outdoor, other). Use the specific kind of shop when there
     is one ("shop" = any other shop). The check uses it: e.g. a signage screen or a poster showing its own shop ad
     is a `screen_content` issue outside shops and cafés, and a picture made for "shop" fits every kind of shop.
   - **Room look**: `room.floor`, `room.walls`, `room.lighting` presets that fit the place:

     | place | floor | walls | lighting |
     |---|---|---|---|
     | home | `wood_light` (和室`tatami`) | `paint_white` | `home` |
     | office | `carpet_gray` | `paint_white` | `office` |
     | warehouse / factory | `epoxy_gray` or `concrete` | `metal_panel` | `warehouse` |
     | data center | `raised_floor` | | `office` |

     Indoor presets are textured (planks with joints, tatami borders, tile grout, carpet tiles, brick, panel
     ribs, with normal maps). A preset with your own `color` (e.g. `{"preset": "wood_light", "color": [...]}`)
     is a plain color without the texture; `roughness` / friction overrides keep the texture.
     Custom: `{"color": [r, g, b]}` (linear 0..1), `{"color": "#8cbfad"}` (sRGB hex, as picked from an image or
     a color chip; converted to linear — never write picked 0..255/255 numbers as the linear list),
     `{"texture": "path", "normal": "path", "tile_m": 0.6}`, or an MDL material.
     **By words** (「明るい木目の床」, a look no preset has): `set_room(floor="light wood planks", floor_alt_queries=[...])`
     searches the Isaac Sim MDL materials and CC0 PBR textures and applies the top match. For ceilings
     (`ceiling: true`), an accent wall (`walls={"*": "paint_white", "N": "brick"}`), 腰壁`wainscot` and
     巾木`baseboard`, read [interior.md](interior.md) "Floor, wall and ceiling finishes".
     Change the look later with `set_room` (it also resizes the room). For a darker / brighter scene use
     `lighting_scale`. `light_kelvin` (e.g. 4500) replaces the presets' color temperature of the fixtures and
     the sun (home 3000 K, office 5000 K, night 2700 K). `studio` / `daylight` have no ceiling lights, so with
     `ceiling: true` the inside of a closed room is dark: use a fixture preset (home / office / warehouse /
     night) there.
   - Plans with several rooms: give each object `zone` (zone id from `import_floor_plan`); `against_wall`
     then uses that room's walls (`north` etc. or a plan wall id), and `center` the room's center.
3. **Show candidates.** `create_scene` opens the preview (`started.preview`): a page next to the terminal that
   follows every later tool call (progress, candidates with the selection framed, the object you are viewing
   highlighted, layout, issues, the Isaac Sim screenshot, licenses). If it has a `hint` instead of `opened`
   (ssh, no desktop), give the user the URL and the hint. Do not call `open_preview` for the same scene.
   - **The user can edit on that page** (all of it shows in `preview_edits`): drag an object on the layout, turn
     it with the blue handle, click a candidate card to swap, and use「複製」「削除」per object, the room's
     size / small things / brightness and color of the lights (saved like `duplicate_object` / `remove_object` /
     `set_room(correction=true)`), and「取り消す」「ここに戻す」on the rows of the change history.
   - When the user says「さっきの方がよかった」「元に戻して」, use `restore(scene_dir)` (undo the last change), or
     `restore(scene_dir, list_only=true)` then `restore(scene_dir, version)`, instead of rebuilding by hand.
     The last 30 saved versions are kept, and the user's notes stay. Call `compose_scene` again after it.
   - **List the chosen asset per object**, one line each. Tool results show each object's selected `asset`
     and the other candidates as one line each in `others` (`[index] name size score`, `weak`, then license and
     caption for `asset`); the object a tool acted on, or `get_scene(scene_dir, object_id)`, shows its
     candidates in full.
   - **Look at the doubtful ones.** For the objects whose selected asset is `weak` (or off-place / off-size / a
     substitute), call `view_candidates(scene_dir)` once: it shows those objects together, one contact sheet
     each (index in the corner, the selected one framed). Name others with `object_id=["a", "b"]`; a single id
     (`object_id="a"`) shows that object's candidates one image each, larger. Judge by the images. Swap with
     `select_asset`, or `research_object` with a better query / synonyms / size bounds.
   - Give every `select_asset` a `reason`: `"match"` when the new one is the thing the query names and the
     selected one was not (the search ranked it too low), `"look"` for style or color, `"variety"` to differ
     from a twin, `"fit"` for size or space, `"other"`. Swaps are logged for the search evaluation, and
     only `"match"` (and the user's own picks) counts as a ranking error, so do not say `"match"` for taste.
   - Assets with USD variant sets list them in the candidate summary (`variants`, e.g. `{"Lighting": ["Off", "On"]}`;
     local folders only). Pick one per object with `"variants": {"Lighting": "Off"}` or `set_variants`; a set or
     value the asset lacks is a `bad_variant` issue.
   - Screens and printed pictures (digital signage, posters, POPs): a lit screen, or a shop ad outside the
     places it suits, is a `screen_content` issue, and status `pictures_hint` names pictures you have not looked
     at. Read [pictures.md](pictures.md).
   - If an object shows `size_misses`, the right kind of asset exists but your `min_size` / `max_size` cut it
     (e.g. a 1.8 m desk with max 1.6). Keep size bounds loose (the table below is a range, not a target) and
     widen them before falling back to a substitute. Weak hits are dropped when a good match exists, so an
     object may have only one or two candidates.
   - If an object shows `substitute`, no candidate is that kind of object. Read its `substitute.hint` first:
     - `not_in_catalog: true` (the kind table says no source has this kind at all, e.g. a wheelchair or a
       whiteboard; `catalog.table.built` is the date of SimReady Central's listing): **do not search again**.
       Ask the user once with `choices`: 代用 (the closest by `view_candidates`, called代用in the summary),
       手持ちのUSD (they set AGENTIC_SIM_WORLD_GEN_SOURCES=+local:<dir>, then `research_object`), orやめる
       (`remove_object`). With「おまかせ」take the substitute and say so. `catalog.other_kinds` lists kinds with
       the same noun that do exist (desk lamp for a floor lamp): good substitutes to offer.
     - The sources do have some (`catalog.counts` > 0): check `size_misses` and widen the bounds, or
       `research_object` once with the plain kind word; if that is still a substitute, stop and say代用.
     - No `catalog` (a source the table cannot answer): try other synonyms once, then keep the closest.
   - For an unusual object, `kind_availability(["floor lamp", "standing lamp"])` answers before `create_scene`
     (absent → tell the user up front and ask the same three choices). Mention that a `local:` folder can add
     the real asset.
4. **Fix the layout** until `issues == []`: change `layout` with `set_layout`, or use `move_object` for a
   manual position (this drops the relation). Give a manual direction as `facing_deg` (where the front points
   in the world: 0 east, 90 north, 180 west, 270 south), not `yaw_deg` (the asset's raw rotation; status shows
   `facing_deg = yaw_deg + asset_front_deg`). layout.svg is for the user (its arrows show each object's front):
   you judge the layout from `position` / `facing_deg` / `issues` in the tool result, and from `check.png`
   after `check_in_isaac` (no need to parse the SVG).
   - **Fronts.** Fronts are +X of the asset (most SimReady / Isaac / store assets; known exceptions are built
     in). Check chairs and anything facing something in the thumbnails: they are shot from the asset's +X+Y
     corner, so the **left** face in the image is the +X face. Front on the left face → OK; front on the right
     face → `front_offset_deg=90`; back on the left face → `180`; back on the right face → `-90`
     (`set_layout(..., front_offset_deg=...)`). When a thumbnail does not show the front (cabinets, shelves),
     call `view_fronts` after `compose_scene` (Isaac Sim, ~30 s): one image per object from its assumed front
     (products placed by `fill` are skipped; `ids=[...]` to reshoot a few); `around=true` shows 4 sides to pick
     the offset. For a whole third-party folder, put `"front": <deg>` (or `{"<glob>": <deg>}`) in its agentic-sim-world-gen.json.
   - **The server fixes what needs no judgement.** At every save, a `narrow_aisle` is widened (the
     fixture across is moved, or the array's `aisle`), jointed chairs that touch (`too_close`) are parted, and a
     TV with no foot on a stand (`tips_over`) is hung on the wall behind it. The result's `auto_fixed` lists what
     moved (also an assumption row「自動で直した」and a line in the preview's history): tell the user in a line,
     do not repeat the fix. A fix is made only when its issue goes away and nothing new appears, so what is
     still in `issues` / `usability` is yours to fix. A fix is made once: if it is undone (e.g. the user wants
     the TV on the stand), it is not made again and the issue is then yours. It never moves what the user placed:
     pass `correction=true` to move_object / set_layout / set_array when the user asked for that spot. Call
     `set_auto_fix(scene_dir, false)` when the user wants nothing moved for them.
   - **Usability warnings.** Then look at `usability` in the status (warnings, not errors; the preview shows
     them as「使い勝手の注意」):

     | warning | meaning |
     |---|---|
     | `narrow_aisle` | the way to a shelf / case / register is narrower than the place needs: home 0.75 m, shops 1.0 m, supermarket / home center / warehouse / factory 1.2 m, otherwise 0.9 m (set `place`) |
     | `unreachable` | no way from the doors, not even 0.6 m wide |
     | `no_front_space` | no room to stand in front |
     | `chair_facing` / `chair_far` | a chair turned away from, or > 0.5 m from, its desk / table |
     | `tv_facing` / `tv_distance` | a TV and the sofa not facing each other, or too near / far for the screen |
     | `register_hidden` | a shop's register is not visible from the entrance at eye height |
     | `blocks_window` | a tall object in front of a window |
     | `in_photo_view` | an object not in an input photo covers 3 %+ of its view (see photos.md) |
     | `empty_floor` (shops) | a patch of sales floor 3 m or more across with no fixture, with its centre (3.5 m in a home center); ones "by a door" may be kept free on purpose |
     | `wide_aisle` (shops) | two shelf rows face each other across more than twice the place's aisle width (supermarket / electronics 2.5 m; home center not flagged) |

     Fix them unless the user asked for that layout; mention the ones you keep. A warning is not "only a
     warning": when the user left it to you (「おまかせ」, no stop in step 5), fix every one before the report,
     and keep one only when it cannot be fixed (say why, e.g. the room is too small for the aisle and the
     fixtures asked for). `narrow_aisle` says where the way pinches (`about 0.81 m at (x, y)`, the ring on
     layout.svg) and how to fix it: `move <id> 0.20 m north (+y) or <id> 0.20 m south (-y)` (move one of
     them, or `set_layout` gap / along), `widen the array aisle ... (set_array aisle=...)`, or, when the front is
     open, that the narrow part is the end of the aisle or a gap between fixtures. Chairs and vehicles do not
     count as obstacles for aisles (they move).
   - **Robot navigation**: for a mobile robot, call `nav_map` once the layout is issue-free and report the
     reachable share ([data.md](data.md) "Robot navigation").
   - **Taste**: once the assets are chosen (3+ objects), call `check_taste` once; how to read and report
     it (never swap on your own): [look.md](look.md) "Taste".
   - **Layout options**: when the arrangement is a real choice the user has not made (gondolas lengthwise
     or crosswise, desks as islands or facing the walls), call `propose_layouts` with 2–3 issue-free options
     (`name`, Japanese `label`, a one-line `note`, `objects`: id → `layout` / `array` / `position` /
     `facing_deg` / `zone`). Skip it when the user said how to arrange things or only one layout makes sense.
     The preview shows them side by side (「配置案」, 「この案にする」). In step 5 list them in one line each
     (mark theおすすめ) and ask. On the answer call `choose_layout(name)` (a pick on the page shows in
     `preview_edits`); left to you → `choose_layout("best")` and say why. Do not `compose_scene` while
     `layout_options` is in the status.
   - **Three drafts**: when `plan_questions` returned `drafts` (the user did not say how big, and did not
     sayおまかせ), pass `drafts="size"` to `create_scene`. Instead of one 3D draft, the server draws three plans
     far apart (small / middle / large; preview「叩き台3案」). End the turn with the three in one line each
     (label, room, how many things) and the one question「どれが一番近いですか」. On the answer call
     `choose_layout(name)` (recorded as the user's choice「叩き台3案から選択」, not as a default), then
     `finish(draft=true)` for the 3D. 「どれも違う」: ask what differs, change the scene, `propose_layouts([])`.
     When the size is decided but not how full the room is or which things stand in it, build the fullest scene
     and call `propose_drafts(axis="amount" | "composition", options=[3 options with `without` / `objects`])`
     the same way. One round of drafts per scene.
   - **The draft** (unless the user said not to stop, see step 5; then pass `skip=["draft"]` to `create_scene`):
     `create_scene` already made it when the layout had no issue (`started.draft`: `usd`, `check.verdict` draft).
     If it was skipped for layout issues, or you changed much since, call `finish(draft=true)` once
     `issues == []` (it writes the USD and shoots the draft). A draft runs no physics: it only shoots
     `check.png` and the 24-direction orbit of the room as laid out (about 40 s headless, seconds live), so the
     preview shows the top view *and* a 3D view the user can drag around (marked「叩き台（物理は未確認）」).
     Without Isaac Sim the top view is the draft. When the user asked to compare layouts (`propose_layouts`),
     the plans are the draft: skip the 3D until one is chosen.
5. **Show the draft and ask what is different.** Unless the user said not to ask (「確認は不要」「おまかせ」
   「最後まで進めて」, or the like), end your turn here, after the draft (step 4). In this turn the only Isaac
   Sim call is the draft (`create_scene`'s, `finish(draft=true)` or `check_in_isaac(draft=true)`): no full
   check, no `license_report`.
   - **Record the assumptions.** The server records the room (size, place, floor, walls, lighting) and each
     object (asset, size, count, position, facing) as guesses from the scene and keeps them current (status
     `assumptions_auto`). So with one `set_assumptions` record only: what the user actually said
     (`given: true`), the step-1 defaults (with the plan_questions options as `options`), and guesses that have a
     reason worth showing (a few-word `basis`: 「6畳の標準」「窓側と仮定」). A row you record replaces the
     automatic one.
   - **The message** (under ~15 lines; the detailed report comes after the check). Start with the draft, not the
     list: 「叩き台を作りました。プレビューの上面図と3D（ドラッグで回せます）を見て、違う所を教えてください」.
     Then show the **guesses first** as a short list (item: value — basis). The step-1 questions you defaulted
     go first among them, each with its other options in a few words (「広さ：6畳（ほかに4.5畳・8畳）」), so one
     word answers it. Then the given items in one line, and each object with its chosen asset in one short line
     (mark substitutes代用and say why; the `decor_*` small things in one line together, e.g.
     「生活感のため追加：本・ペン立て・観葉植物」). End with「違う所（物・位置・広さ・雰囲気）を言ってください。
     プレビューの「仮定」の「違う」でも直せます。このままでよければ「これでいい」と言ってください」.
   - **Corrections.** When the user corrects a row, change only that (set_room / set_layout / move_object /
     research_object …), re-record that row with `given: true`, and leave the other rows alone. Pass
     `correction=true` to `set_room` / `set_layout` / `set_array` / `select_asset` when the change is what the
     user asked for (not your own fix of an issue): it is remembered, and the same correction in 2 scenes becomes
     their default. Picks on the preview page are remembered without it. The user can also press「違う」
     on a row of the preview's「仮定」table and type how it should be (or pick one of the row's `options`):
     that comes back as `assumption_feedback`.
   - **Notes on the preview.** The user can double-click a spot of the top view or of the check's 3D
     orbit viewer and write a short note there (「ここにライトを」「この辺が狭い」). If `status` has `notes`,
     handle each one: `where` / `object` / `position` / `z` / `near` say where it was written (on an object's
     top, or a floor spot and the closest objects). Do what the note asks at that spot (add an object with
     `position`, move or swap the one there, widen a gap), then call `resolve_notes(ids, reply)` with a one-line
     Japanese reply of what you did; that clears the pins. If a note is unclear, ask about it in one line and
     leave it open. Notes are not edits (`preview_edits`).
   - **「なんか違う」with no reason** (「なんか違う」「イメージと違う」「しっくりこない」, and nothing about
     what): do not ask「どこが違いますか」; most users cannot say. Call `propose_contrast()`: it puts the scene
     as it is next to one that differs in ONE thing (size first, then the amount of things, then the look;
     preview「どちらが近いですか」), and you ask only「どちらが近いですか（どちらも違う、でも構いません）」.
     Pass the answer to `choose_contrast("now" | "other" | "neither")`. `other` is applied and recorded as the
     user's (「見本の二択で選択」): show the result and ask if it is closer. Otherwise call `propose_contrast()`
     again for the next step (`contrast.left`). The mood step returns two sample pictures instead of plans (show
     both). `axis="layout"` (another type of arrangement) and further steps need your `option`. When the user
     says what differs at any point, drop the comparison (`propose_layouts([])`) and fix that. When
     `contrast.left` is empty, ask in words or for a reference picture.
   - **Swaps in words** (「本棚は木目のやつに」「椅子は青いのがいい」「もっと低い棚」): first look at the
     current candidates (`view_candidates`); the wanted one is often there already → `select_asset`.
     Otherwise call `research_object` with the attribute in English in `query` and `alt_queries` ("wooden
     bookshelf", "wood bookcase", "oak shelving unit"), keeping the size bounds and passing `select=false`
     (the current asset stays selected and in the list), then `view_candidates` and judge by the images
     (captions and tags miss colors and materials). If one clearly has the attribute, `select_asset` it and say
     so. If the result has `research.not_in_catalog`, the kind itself is missing: stop searching (no third
     query) and ask代用 / 手持ちのUSD / やめる. If nothing has the attribute, say so plainly, show the closest
     options (e.g. right shape but wrong color, or right material but a different kind of furniture) and let the
     user pick. The scene keeps the current asset until they answer; do not swap silently to something that only
     matches one word. Asset colors and materials cannot be changed here (only the room's floor / walls can).
   - After the fixes, show a new draft (`finish(draft=true)`) only when the change is large (room size, layout,
     several assets); for small ones say what changed and ask once more.
   - On「いいね」「これでいい」「生成して」or similar, go on to step 6 (call `compose_scene(localize=true)` first
     only if Isaac Sim cannot open https references).
6. **Close with `finish`**. First record the request items once with `check_request(requirements=[...])`
   (step 7), then call `finish(scene_dir)`. In one result it writes the USD if it is behind the scene, runs the
   Isaac Sim check (physics), measures the request items again and reports the licenses. The check runs live
   when the user's Isaac Sim is open with `--enable isaacsim.code_editor.python_server` (failed objects get
   selected and a re-check takes seconds), else headless. It also shoots the room from 24 directions
   (`check_orbit/`, about 6 s more) so that the user can drag it around on the preview page.
   `finish` stops at the first thing to fix (`stopped`: issues / compose / check, with `next`): fix that and call
   `finish` again (a check that passed on an unchanged scene is not run twice). Without Isaac Sim the check is
   `skipped`, and `unverified` says what to tell the user. Use `check_in_isaac` alone for a quick re-check of
   physics only (`orbit=0`) or a GIF (`gif=true`). Look at `check.png` (and `check.gif`) and the report lists:
   - `no_collider`: the asset has no collision (things fall through it) → `set_collider(object_id, "box")` adds a static
     box collider the size of the asset (invisible, never moves), or pick another candidate. Assets known to have no
     physics (SimReady with `physics: false` in the candidate, e.g. Vertiv racks) get one automatically (`collider` in status).
   - `static`: colliders but no rigid body → it never moves; fine for furniture and fixtures.
   - `unstable`: it moved / tipped → it overlaps another object (check `issues`), has a thin or top-heavy shape
     (brackets, signs), or stands on something too small; move it, add `gap`, or pick another asset.
   - `sunk`: its bottom ended below the floor → usually the collision shape differs from the mesh, or it
     started inside the floor; pick another asset if it matters.
   - `dropped`: every body of a group went straight down by the same height (e.g. stock on a rack's board):
     the collider is lower than the board found in the mesh. Not a layout problem; do not move things for it.
   - `error` "physics did not run" with `blew_up`: those objects started touching or inside another and blew up,
     which stopped the GPU physics. Re-running the same scene fails the same way: move them a few cm apart first
     (e.g. office chairs in an array need `spacing` with a gap; `too_close` in `issues` says so before the check).

   The result is a summary: `groups` (per object id: count, largest move) and `problems` (details of the failed
   bodies only); long lists are grouped as `id/* (n)`. Read `full_report` (check.json) only if you need
   another body's numbers. `ok` only says the run worked: go by `verdict` (`passed` / `needs_fix` / `draft` /
   `failed`). With `needs_fix` the scene is **not done**, even if the rest looks fine; do not report it as
   finished. status carries the same as `last_check` (with `stale` once the scene changed), so there is no need
   to grep check.json. After `select_asset`, rows of the swapped object show up in `assumptions_review` (size,
   colour, … may no longer hold): set them again with `set_assumptions`. Fix and repeat steps 5–6.
7. **Match the request** with `check_request`, before `finish` (which re-measures; `requirements_open` lists
   what is not met). Split what the user asked for (in all turns) into items in their words, one each:
   「本棚は東の壁」「椅子2脚」「机は窓に向けて」「木目の本棚」. Attach the checks the service can measure:
   `against_wall` (object, target = compass / wall id), `next_to` / `near` (target = object or opening,
   value = max gap m), `count` (objects, value), `facing` (target = object, opening or compass), `in_zone`, `on`.
   Items that cannot be measured (style, color, material, the asset itself) get your own `verdict` from the
   thumbnails (`view_candidates`) and a short `note`; leave the verdict out if you cannot tell (unknown).
   Fix every `unmet` item you can, then call `check_request()` with no arguments to re-measure.
   Example: `{"text": "本棚は東の壁", "checks": [{"kind": "against_wall", "object": "shelf", "target": "east"}]}`.
   From a photo, add the room size and the aisle widths as items too, measured with measure_photo ([photos.md](photos.md) step 7).
8. **Report** in the user's language: first the request table (満たした / 満たしていない / 確かめられない, with the
   reason for each item not met: substitute asset, does not fit, ...), then the USD path, the objects with their
   chosen assets (substitutes marked), what was verified (and what was not: `unverified`), and the license
   summary from `finish` (`licenses`; attribution needed for CC-BY etc.).

## Typical sizes (long, short, height in m) for min/max_size

The same values (and more kinds) are in `src/agentic_sim_world_gen/assets/sizes.py` `TYPICAL`, which the server uses when you
pass no limits; keep the two in step (tests/test_sizes.py checks every row below).

| object | min | max |
|---|---|---|
| office desk | 1.0, 0.5, 0.65 | 1.9, 1.0, 0.85 |
| office chair | 0.45, 0.45, 0.8 | 0.8, 0.8, 1.3 |
| dining table (4p) | 1.1, 0.7, 0.65 | 1.8, 1.0, 0.8 |
| dining chair | 0.35, 0.35, 0.75 | 0.6, 0.6, 1.1 |
| sofa (2–3 seat) | 1.4, 0.7, 0.6 | 2.4, 1.1, 1.0 |
| bookshelf | 0.6, 0.25, 1.2 | 1.2, 0.5, 2.2 |
| bed (single–double) | 1.9, 0.9, 0.3 | 2.2, 1.6, 1.2 |
| TV (on stand) | 0.8, 0.05, 0.5 | 1.8, 0.4, 1.1 |
| refrigerator | 0.55, 0.55, 1.2 | 0.9, 0.8, 2.0 |
| warehouse rack | 1.5, 0.8, 1.8 | 3.0, 1.2, 6.0 |
| pallet | 1.0, 0.8, 0.1 | 1.3, 1.2, 0.2 |

## Query tips

- Use the object category, not the room: "office chair", not "study room chair".
- Material / colour words help only a little ("wooden bookshelf"); put the noun last. In `alt_queries` they widen
  the pool ("wooden chair" brings in a wood dining chair that "chair" alone misses) without spoiling the main
  query's class matches. Choose the color from the captions ("White …", "Black …") and thumbnails.
- Japanese → English examples: 本棚bookshelf/bookcase, 収納棚storage cabinet, 観葉植物potted plant,
  ゴミ箱trash can, 段ボールcardboard box, 台車hand truck, パレットpallet, 会議机conference table,
  ホワイトボードwhiteboard, ロッカーlocker.
- SimReady Central is strong on office, warehouse, retail and hospital props, and weaker on home decor. Say so if
  the user wants something unusual.
- Results also include Isaac Sim bundled assets (`source: isaac-6.1`; Props such as KLT bins, pallets, racks,
  YCB objects). Isaac Environments (whole rooms / warehouses) have no size, so they appear only without size bounds.
- License `NVIDIA-Isaac-Sim` = usable with Isaac Sim; flag it in the report if the world will be redistributed.
- Poly Haven's CC0 models (`source: polyhaven`, uri `polyhaven:<id>`) fill the home-decor gap: dining tables, sofas,
  beds, armchairs, side / coffee tables, vases, clocks, lamps, potted plants, books, fruit, picture frames. They are
  fetched (1k USD, a few MB each) only when the scene is composed. They have no physics of their own: they get a
  static box collider, and nothing is stocked on their shelves. Poly Haven has no rugs, floor lamps, remotes, mugs
  or fridges either.
- The user's own USD folders appear as `source: local:<folder>` when added to AGENTIC_SIM_WORLD_GEN_SOURCES (`+local:<dir>` = the
  defaults plus that folder; a plain list replaces the defaults). status `sources` shows what is searched and the
  `left_out` defaults. Never search a left-out source from Python outside the agentic-sim-world-gen tools: tell the user.
  If they mention their own assets, suggest `agentic-sim-world-gen index <dir> --list` and a `agentic-sim-world-gen.json` with the license.
  A license `see <path>` means the LICENSE file was not recognized: tell the user to check it.

## Don'ts

- Don't place by raw coordinates when a relation describes it.
- Don't hide substitutes or skipped checks.
- Don't undo what the user did on the preview page (`preview_edits`) without asking.
- Don't swap an asset because `check_taste` flagged it; offer the alternatives and let the user choose.
- Don't pip install into Isaac Sim's python; the MCP server runs from the repo's `.venv`.
- Don't edit the agentic-sim-world-gen code (this repo, a worktree of it) while building the user's scene, and don't ask them to
  restart the MCP server for it. When a tool cannot do what is needed, use what it can, and report the gap
  (what you tried, what is missing) so that it becomes an issue.
