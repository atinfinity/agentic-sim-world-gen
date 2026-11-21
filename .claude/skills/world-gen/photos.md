# Photos

Part of the world-gen skill ([SKILL.md](SKILL.md)); read it when the request needs it.

## Photos (「この写真の部屋を作って」)

Looking at the photo with Read is not enough. Record it with `set_input` (with its camera), and after every
compose compare it with `compare_photo` until the scene matches. Status `photo_compare_hint` says when that
comparison is missing or out of date, and `compose_scene` returns it as `next`. `finish` stops on it
(`stopped: compare`) while compare rounds are left and Isaac Sim's Python is at hand - but not when the
only change since the comparison is small moves (up to 15 cm / 10°, e.g. the physics fixes): `photo_compare.small_moves`
lists them, and they are not compared again. When compare_photo
cannot run (it failed twice, or the photo files are not on this machine) finish goes on; to say so yourself:
`set_assumptions([{item: "photo_compare", value: <why>}])`.
With several photos, compare_photo shows only the rows that changed since you last saw them (`unchanged`
lists the others); compare.png has every row - Read it to look at an unchanged row again, and once after
a compaction (the rows you saw before it are gone).
**The photo's camera is where the photo was taken.** When the user says「レジを手前に」, move the register, not
the camera. Change the camera only to match the photo's view (horizon, landmarks). Objects the photo shows near
the camera (e.g. a counter it was taken from behind) stay there.

**Two passes, about 3 comparisons.** A trial that fixed one thing per comparison took 80 turns. Instead:
- **Pass 1 - the big layout, built once.** From the photo decide the room size, the shelf / gondola rows (how
  many, which way they run, units per row), the aisle widths, and where the register and the entrance are.
  Get the camera first (fit_photos, or step 6), and make the scene with the room, the photo input and no
  objects. Then read the widths off the photo with **measure_photo `floor_pairs`** (step 7): pixel pairs at the
  bottoms of the shelf fronts, the aisle edges and the walls. Each pair returns `photo_m` and the floor `points`
  [x, y], which give the rows' positions and the aisle widths directly. Then add every object in **one
  add_objects** (rows as `array` with the unit count you counted) and compose. Do not compare before this pass
  is complete.
- **Pass 2 - details, in batches.** compare_photo returns `round` and a `hint`. After each comparison, list
  **every** difference you see (framing, widths with `measure`, unit counts, big objects, wall / floor colors,
  light) and fix them all before the next compare_photo, in as few calls as you can: every move in one
  **move_objects** (a trial moved 23 objects with one call each), the same variants on several ids in one
  set_variants. Aim for 3 rounds. From round 3 on, fix only what is clearly wrong, then report and name the
  small differences instead of chasing them.

**Keeping the photo work out of the main conversation.** Matching a photo is the longest part of a session
(the photo, the camera fit, one render per comparison), and the main conversation needs only its outcome. When
you can start a subagent (the Agent / Task tool) and the scene has several photos or the session is already
long, hand it one photo's pass 2. Give it the scene_dir, the input id, what the user asked for and what pass 1
placed. Tell it to follow this file (compare_photo -> list every difference -> fix in batches, 3 rounds at most)
and to answer with only: what it changed (ids, from -> to), the differences that remain, and the number of
rounds. Do pass 1 and the user's questions yourself, and look at the last compare.png once before you report.
Without a subagent tool, do it all yourself (not yet measured in the benchmark, whose sessions have no Agent
tool).

1. **Source first.** Record where the photo came from: page URL, author, license (CC0 / public domain /
   the user's own photo ...). If the user cannot say, ask; do not use photos of unknown origin for scenes
   that will be shared. A sidecar `<stem>.source.json` next to the file fills url/title/author/license
   (see `examples/photos/`); then `inputs: [{"id": "photo", "path": "<file>"}]` is enough.
2. **Look at it** with Read. List what is visible, left to right, near to far: walls, windows/doors,
   furniture, large props, and the **wall, floor, window and ceiling things** (see the last point of this
   step). Skip small clutter (cushions, candles, cables, magazines) unless asked.
   - **First the rooms and floor levels.** Is it one room, or a front room opening onto another (an arch, a
     wide opening)? Is part of the floor higher (a step up into the back, a小上がり, a stage), or are there
     stairs? Count the risers you see (one riser ≈ 0.15-0.18 m) and give them as `levels` / `stairs` (see
     [interior.md](interior.md) "Steps, stairs and railings"). A post standing in the room (not a wall corner)
     is a column: `set_room(columns=[...])` ([interior.md](interior.md) "Columns and part of the floor"), not a
     cabinet or a bent wall.
   - **Tell walls from things in front of them** before choosing wall colors: a red block may be a shelf or a
     display, a white zigzag a staircase. Name what each colored area is (「奥の赤は棚、白いのは階段」) and
     color only what really is wall.
   - Note each wall's color and finish too. One wall that stands out (red paint, brick, a dark accent, wood
     panels, a wainscot) is part of the room's look, not clutter. Give the walls separately in `create_scene`
     (`walls={"*": "paint_white", "N": {"preset": "paint_white", "color": [r, g, b]}}`, or words searched per
     wall) instead of making every wall the same.
   - **The wall, floor, window and ceiling things** are easy to miss because they are neither furniture nor
     clutter (a living room trial kept only its 9 pieces of furniture). List them too:
     - on the walls: pictures, a clock, a mirror, wall shelves, a TV on the wall, a whiteboard / chalkboard;
     - on the floor: rugs and mats (a large patch of another color in front of a sofa or under a table);
     - at the windows: curtains or blinds (they also hide the gray void behind a window);
     - on the ceiling: light fixtures, a fan; and plants on a windowsill or a shelf.

     How to place them:
     - a picture / clock / mirror: `layout: {against_wall: "<wall>", along: 0..1, wall_height: <m to its bottom>}`
       ([interior.md](interior.md) "Ceiling lights, beams and posters"); a chalkboard / whiteboard the same way,
       with query "chalkboard" / "whiteboard";
     - a rug: an object with query "rug" on the floor (`position` or `in_front_of` the sofa, its size as
       `min_size`, its color as `tint`). If no rug asset fits (the default sources have few floor rugs), leave it
       out and name it in the report; an asset folder of your own (`local:<dir>`) can add them;
     - curtains / blinds: `fill_windows(covers="curtain" | "blind")` puts one over every window; a window with no
       fitting curtain asset is listed in `not_found`: name it in the report;
     - ceiling lights: an office, classroom or shop with a ceiling gets its place's fixtures by default (interior.md
       "Ceiling lights, beams and posters"); change the kind with `set_room(fixtures=...)` to match the photo.
       Place lights as objects (`layout: {ceiling: true}`) only for ones the fixtures do not cover (a pendant over
       a dining table, a fan).
3. **Orient the room.** The camera stands near the **south** wall looking **north**: the far wall is
   north (y = depth), the left wall west (x = 0), the right wall east (x = width). Things behind the camera
   are unknown: leave that part of the room empty or make a sensible guess, and say so.
   **Several photos of one room** (from different sides): the first photo
   (or the one showing the most) sets the room as above. For each other photo:
   - Find landmarks it shares with photos already placed: doors, windows, large furniture, corners,
     a distinctive floor or ceiling pattern. From them, say which wall it faces (e.g. 「写真2は窓が右、
     ソファが奥 → 東の壁を背に西向き」). If a photo shares nothing, ask the user where it was taken.
   - One object per real thing: something seen in several photos is one object (place it from the
     photo that shows it best). Walls/openings seen in several photos fix the room size: use them to
     check the estimates from step 4.
   - **Fit the room and cameras with `fit_photos`** instead of guessing them. Per photo, give a rough
     `facing` and the pixels of the landmarks it shows:
     - room corners (`corner:ne:floor`);
     - window / door corners (`win_n:start:top`; start = the west end of a north/south wall, the south end of
       an east/west wall);
     - lines where a corner is hidden (`floor:east`, `ceiling:north`, `corner:sw`, `win_e:top`);
     - points shared between photos (e.g. a cabinet's top corner), declared in `points` with what you know
       about them (e.g. `z`).

     Mark every window / door you see by its corners (all four when visible, in each photo that shows it):
     an opening without them stays at its guessed place and size (details in **Fit details** below).
     Read pixels off the image carefully (overlay a grid when needed). Aim for 6+ measurements per photo.
     Check `rms_px` (a few px is good), `worst` and `warnings`. A large error is usually a mislabeled landmark
     (start/end swapped, wrong corner); a camera with ± > 0.3 m needs more landmarks at different depths.
     With `out_dir` = the scene dir, the room and each input's `camera` are written into the scene
     (`applied`). With `apply=false` it only fits (e.g. before the scene exists): then pass `room` to
     create_scene and the cameras as each input's `camera`. The wall height sets the scale: use a known one
     (2.4 m is the default) and say it is assumed (with depth it is fitted, see **Fit details** below).
   - **Locate every piece of furniture you see** in the same fit_photos request (`locate`). Per object, give
     2-4 pixels where it meets the floor (feet, base corners - at least two diagonal ones; z = 0) or a top
     corner at its height. Give each pixel `object` = the object id you will use in the scene (`desk`, `bed`;
     `chair1`, `chair2` for two of a kind) and the same `name` for the same corner in each photo that shows it
     (`desk:sw_leg`). Add `edge` when you can tell the side: "front" for a foot / corner on the side the object
     faces (a chair's seat edge, a sofa's seat, a cabinet's doors), "back" for the far side. One point is a foot,
     not the middle (fit_photos warns). The result's `objects[id]` gives the floor `center` and points, and with
     3+ points `axis_deg` and `extent_m` (its length and width on the floor); a warning says when two photos
     disagree (a pixel on the wrong object). Write the result into the scene folder (`out_dir` = the scene dir)
     so status / compare_photo can check the placement later.
   - Give each photo its own input (`id`: photo1, photo2 … or by the direction) with its own source
     (sidecar or fields) and its own `camera` in the same room coordinates.
   - Before compose, check the cameras in layout.svg (📷 marks with the field of view) against what each
     photo shows: each wedge should cover the walls and objects visible in that photo.
   - **Record what each photo shows**: `set_input(scene_dir, {"id": "<photo>"}, seen_in=[object ids
     visible in it])`. Objects you add that no photo shows (the user asked for them, or they are behind the
     cameras) must stay out of the photos' views: put them behind or beside the camera, not between it and
     what it shows. Status `in_photo_view` names any that cover 3 %+ of a photo, and the compare render would
     show them hiding the photo's furniture.
4. **Estimate sizes** from things of known size: door 2.0 x 0.8 m, window sill 0.8-0.9 m, ceiling 2.4 m
   (Japan, apartments) / 2.5-2.7 m, sofa seat 0.42 m high, table 0.72 m, office chair 0.9-1.1 m,
   kitchen counter 0.85-0.9 m, pallet rack beam pitch ~1.5 m, pallet 1.2 x 0.8 (EU) / 1.1 x 1.1 m (JIS).
   Count floor tiles, boards or ceiling panels when visible. Round to 0.1 m; state the uncertainty.
   **Widths across the view** (room width, aisle width) are where a photo is easiest to misread (a
   bookstore aisle came out twice as wide as the photo's). Count things of known width along the aisle and
   across it: shelf / gondola units ~0.9 m each, doors 0.8-0.9 m, ceiling lights at a regular pitch, floor
   tiles. The aisle is the gap between the shelf fronts at the same depth, not the room width. When room
   corners and shelf fronts are visible, fit them with fit_photos (`locate` the shelf fronts' feet) rather
   than guessing.
5. **Place each located object from its points.** Add it with its `facing_deg` from the side it faces in the
   photos (set this first: see below), select the asset, then run **place_from_photos** (scene dir). It fits
   each selected asset's footprint to its points (center and facing; two front feet give the center, not the
   middle of the front edge) and records the position as an assumption; use against_wall only when the
   object's points touch a wall. Pass `outlines` in the same call, for all objects at once:
   `[{object, photo, box: [u0, v0, u1, v1]}]`, a tight rectangle around the whole object in 1-3 photos that
   show it (a side at the image border = cut off there). It then refines each pose in the photos (the asset's
   box drawn by the fitted cameras against the feet and the rectangles). Only the objects in `left` still need
   compare_photo + move_object (`loose`: the asset's size is wrong or a pick is on something else).
   It keeps the facing you gave unless the points fit another one clearly better, and always keeps it for two
   points without `edge`. Its warnings name an asset whose size does not fit the points (fix min_size /
   max_size or the asset, then run it again) and a facing it could not tell (mark `edge`).
   Place by relations (against_wall + along 0..1, next_to, in_front_of) only what no photo shows on the floor
   (relations put the desk where the wardrobe stood; in another room, 0.85 m off where the chair
   stood). The scene folder's photo_fit.json lets status / compare_photo flag what is still off
   (`photo_placement`), and **compose_scene stops while any of these is left** (a run that only moved things by
   eye put the desk on the chair):

   | `photo_placement` key | Means | Fix |
   |---|---|---|
   | `misplaced` | placed more than 0.3 m from its located feet (`there` = what stands there now) | place_from_photos moves it; move `there` too |
   | `located_unknown` | located, but no object has that id | add the object, fix the locate's `object`, or (removed on purpose, e.g. merged into an array) `compose_scene(not_in_photos={id: why})` |
   | `unlocated` | floor furniture with no locate | pick 2-4 feet / corners in the photos, fit_photos again with the scene folder, place_from_photos |
   | `one_point` | rests on one located point | add 1-3 more feet / corners (a diagonal one), fit again, place_from_photos |

   Only an object no photo shows on the floor, or a located id removed on purpose, goes in
   `compose_scene(not_in_photos={id: why})`; report those.
   Any still `unlocated` at the end are in finish's `report_must_include`: name them in the report.
   Size ranges come from what you estimated (wide ranges: ±30%). Use the windows/doors in room.openings when
   visible, then `fill_windows` with what covers them in the photos: `covers="blind"` (slats, a roller),
   `"curtain"`, or `None` (bare glass); the default follows the place. Match the floor/wall looks and the lighting (e.g. herringbone parquet -> wood_light, white paint
   -> paint_white, home lighting; warehouse -> concrete/epoxy + warehouse).
   **Wall and ceiling equipment** you see (an air conditioner, radiators under the windows, wall speakers, ceiling
   vents, a clock, exit signs) is listed and placed too when it is about 0.3 m or bigger: hang it by wall,
   `along` and `wall_height` (or `ceiling: true`), not from located feet. See [interior.md](interior.md) "Wall and
   ceiling equipment" for the queries, heights and which have no asset (name those in the report).
6. **Camera** (`inputs[].camera`):
   - position = where the photographer stood (x, y inside the room, z = 1.0-1.6 m; lower if the floor fills
     much of the frame and the horizon line sits high);
   - look_at = the point at the image center (usually on the far wall);
   - hfov_deg = 60-70 for a phone main camera, 75-95 for real-estate style wide-angle shots (straight walls
     bending away fast at the edges, a lot of floor and ceiling visible). Check: a far wall of width W at
     distance d that fills a fraction f of the image width means hfov ≈ 2·atan(W / (2·f·d)).

   **Tilt**: extend two floor or shelf-foot lines that run away from the camera (an aisle's left and right
   shelf feet) until they meet: that row is the horizon. measure_photo's `horizon_v_px` must match it. If it
   is lower in the photo, the photo looks up (raise look_at's z); if higher, down. A wrong tilt makes every
   floor distance wrong (a 6° error read a 3.3 m aisle as 1.7 m). measure_photo reads the tilt off the
   photo's vertical edges itself when it can (`photo_lines`): when its note says the camera is off, set
   look_at's z to `look_at_z`. Its `hfov_deg` is only a hint.
7. compose_scene, then **compare_photo**: it renders the camera view next to the photo (all photos in one run,
   one row each). Compare in this order: the framing (fix the camera with set_input until the far wall and
   corners line up), then the sizes and positions of the objects (set_layout, set_room), then the choice of
   assets. Its `warnings` (and status `photo_placement`, step 5) name misplaced objects: move them before the
   next round.
   **Check widths with numbers, not by eye**, with **measure_photo** (compare_photo's `measure[<photo>]` holds
   the same view without pairs):

   | Pass | Returns | Use |
   |---|---|---|
   | (nothing) | `across`: the free width across the view 1-10 m ahead and what bounds it on each side (a wall, a shelf unit); `camera.stands`: where the camera stands in the aisle (left/right distance and blocker); how many units of each array are in view | compare with the photo: the shelf units you count on each side, where the shelf fronts disappear at the image edge, which side the camera is closer to |
   | `floor_pairs` = pixel pairs read off the photo (e.g. the bottoms of the left and right shelf fronts at the same depth) | per pair: `photo_m` (the metric distance on the floor), the floor `points`, the scene's free width there (`scene_m`, `ratio`) and `range_m` (tilt ±2°, camera height ±10 %) | a ratio off by more than 20 % is a wrong width; a scene width inside range_m is not a difference |
   | `known` | per pair: `estimates`, `best_m`, `outliers`, `typical_off` | see below |
   | `aisle_pair` | `aisle` | see below |
   | (a depth map exists: estimate_layout `depth=true`) | `depth_floor` | see below |

   - **Wrong width** (ratio off by more than 20 %): fix the room or the rows (set_room, move_object, set_array)
     and measure again before reporting. When range_m is called too loose, count shelf units or floor tiles
     across the aisle instead.
   - **Never resize from one estimate**. Pass what you counted as `known=[{"pair": 0, "m": 2.7, "from":
     "3 shelf units"}]`. Each pair then lists its `estimates` (geometry, depth map, your count), their median
     `best_m`, the `outliers` to fix (e.g. "geometry (camera as set)": the camera's tilt) and whether best_m is
     outside the store kind's usual aisle (`typical_off`). Resize toward `best_m`.
   - **Let the server do the arithmetic**. Instead of multiplying yourself, pass what you counted across
     the pair as `{"pair": 0, "kind": "shelf_unit", "count": 2.5}` (kinds: shelf_unit 0.9 m, door 0.85, basket
     0.48, cart 0.5, pallet 1.1, floor_tile with `"size_m": 0.3 / 0.45 / 0.6`; without size_m the tile is picked
     by the geometry and not counted). Or point at a thing of known size in the photo:
     `{"kind": "door", "line": [u1, v1, u2, v2]}` = its two ends on the floor (`"box"` = its pixel box when it
     faces the camera). Each pair then gets a "known size" estimate in which the camera's height cancels, and
     you get `scale.camera_height_m` (set position's z to it when the note says so). Choose a thing seen across
     the view near the pair's depth; one running away from the camera or far off in depth is reported `loose`
     and not counted.
   - **depth_floor** is the floor plane fitted to the depth map and the free width 1-10 m ahead, without
     picking pixels. Use it (its notes) to find where the scene is off, then confirm that place with
     floor_pairs / `known`. Its scale is the map's (-15..+24 %); `width_at_set_height` is the same width at the
     camera height you set.
   - **Make the measured aisle the target.** Once a pair that spans an aisle has estimates that agree
     (not `single` / `disagree`; count units or tiles and pass `known` first), call measure_photo again with
     `aisle_pair=<its index>`. The width (best_m, with range_m) is then recorded as the guess「aisle」
     (basis写真の実測) and set as the aisle of the array whose rows stand on both sides. The usability check
     uses it instead of the store kind's usual aisle (a tighter real aisle is not narrow_aisle, a wider one is
     not wide_aisle). `aisle.ratio` and status `photo_aisle` give the scene's width against the photo's on that
     line. Issue `photo_aisle` stays until it is within the range; rows of separate objects you move yourself.
     It is not applied when the user stated the aisle (`aisle.kept`): tell them the two differ.

   About 3 rounds (see "Two passes" above), each fixing every difference you found in it. Exact matches are not
   the goal; the layout and proportions are. Still, a difference you can see and a tool can fix (a wall or
   floor of the wrong color, a missing accent wall, the light far too warm or dark, a big object in the wrong
   place) is fixed (`set_room`, `move_object` ...) and compared again before you report; "close enough" is for
   small offsets. Pillars and partitions block the floor and set the aisles, so do not drop them: stand in a
   tall boxy asset of about the same footprint (a locker, tall cabinet, shelf unit; a partition = a shelf row,
   set_array) and record it as a substitute (set_assumptions) for the report. Leave out only what no tool or
   stand-in can make (things no asset matches) and name it in the report.
8. Report: what was reproduced, what was substituted or left out (and why), the size estimates (room width and
   depth, aisle widths, each with how you got it: counted units, fit_photos, floor_pairs), and the photo's
   source. license_report lists the photo under "Inputs".

## Fit details

More on fit_photos (step 3).

**Images fill the context**: Read each photo once (one grid overlay per photo at most), and write its
landmarks to `<scene>/landmarks.json` (Write) as soon as you have them. Do not look at the photos again while
a tool runs. After a compaction, read `landmarks.json` (or the request fit_photos kept in
`<scene>/photo_fit.json`), not the photos. Photos that share many landmarks make the fit stable. With
`out_dir` = the scene folder fit_photos writes the fit into the scene (`apply`: the room, the fitted openings
and each photo's input camera; the photo ids must be the input ids; recorded as assumptions「写真から当てはめ」), so no set_room / set_input per photo is needed. Read
`applied` and the warnings: a camera "inside" an object or "outside the room" means a misplaced object or
wrong landmarks.
**Give each wall landmarks in at least two photos**: a wall only one photo sees carries the room's width or
depth alone (fit_photos warns "rests on one photo for the east wall"; in one room a single photo's
east wall moved the width 3.0 → 3.85 m). Look through the other photos for that wall's corner, its floor /
ceiling line or plain wall pixels (`surfaces`), and if none shows it, report the warning's ± with the size.
**Scale**: if no landmark at the ceiling is seen (ceiling corner / ceiling line / a shared point with a known
height above the floor), the room's size comes from the camera height. Give `eye_height` (a hand-held phone
is about 1.4–1.6 m; 1.4 is the default) and say it is assumed. A 6 % wrong height makes the room 6 % wrong.
**Typical sizes pull the scale** toward them, but without a ceiling in view each photo carries its own scale: a
door's top fixes the scale only of the photos it is in. Mark it in a photo that also sees the far walls / corners
(both walls of the width or the depth); a door seen only next to the near corner leaves the room at the
`eye_height` scale, fitted at 2.0 m all the same. Heed the warning "the room's <width|depth> rests on the camera
height": add such a photo, or give eye_height; and read `scale` (the eye height's `sets`), report those sizes as
assumed. Mark the **top corners of every
door** you see (`<door>:start:top`, `<door>:end:top`, or the line `<door>:top`): each seen door top is held
near `door_top` (2.0 m, ±0.1; give 2.03 for a US room, `null` to turn it off). A door that is not a standard
one (a sliding door to the ceiling, a low closet) takes its own `openings.<id>.top` / `top_sigma`. A flat top
of standard height seen in two or more photos can be a shared point with `z` and `z_sigma` (a kitchen counter
0.9 ±0.03, a desk 0.72 ±0.03); a dining table's top seen in one photo did not help. The result lists
`size_priors` (typical vs fitted) and warns when one is more than 2 σ off: check that landmark, or the door is
not typical.
**Depth, if AGENTIC_SIM_WORLD_GEN_DEPTH_PYTHON is set**: give each photo its `image` (the photo's path) and call
fit_photos with `depth=true`. It fills the depth of every landmark pixel from a monocular depth model
(MoGe-2), and also of `surfaces`: a few pixels on plain floor / ceiling / each wall (`on`: floor, ceiling,
wall:north ...; not on furniture or rugs), about 10 per surface. It replaces the eye_height guess, but a
photo that sees only one or two walls absorbs its depth's scale: it helps most when the photos share
landmarks. Each camera's `depth_scale` far from 1 (a warning)
means that photo's depth is off: say so. If it says the model is missing, ask before downloading it
(141 MB, AGENTIC_SIM_WORLD_GEN_DEPTH_DOWNLOAD=1).
- With depth, the wall height is fitted too (`room.height_fitted`; `request.height` is only the start): do
  not record the height as an assumption then. Pick ceiling corners / lines **and** the floor in the same
  photos (a handheld photo seldom sees both).
- Warning that the height is not fixed by the photos: the cameras and the ceiling move together. Add a
  photo that sees a ceiling corner and the floor below it, or keep the height as an assumption and
  say so.
- Warning that the room's **scale** is loose (the floor and the ceiling are tied): the shape is right and
  only the overall size comes from the depth. Say the size is ± that much, and give the height
  (fit_height=false) if the user knows it.
- Warning "height: the fitted wall height ... is suspect" (`scale.height_suspect`): the room's size holds
  without it, so a ceiling corner or line is on a soffit or a beam. Do not use that height: relabel the
  ceiling in the listed photos, or fit again with fit_height=false and the real or a typical height.
- `height_refit`: a fitted height outside the room's range (by `place`: home, office, school, lab,
  hospital, cafe, restaurant, or `height_range`; the scene's with out_dir; none for no place or a shop / hall)
  or suspect was already fitted again held at the given or a typical height in the same call - do not fit
  again for that. `adopted: held` = that fit is the result (the scale was off; apply keeps the scene's height
  unless you gave `height`); `adopted: fitted` = the landmarks did not fit the held height (misread ceiling
  landmarks: relabel them), or `metric_worse` lists the depth / door top that fit it worse (the ceiling may
  really be that high: ask, or give `height`). Give `height` in the request when the user knows it (it is the
  one held).
- Warning "nothing of known length is seen" (no ceiling seen with the height given, no shared point at a
  known height): the size comes from the depth and the camera height together, and a depth model can be off
  by 10–15 % in every photo (a rendered room came out 13 % big from the depth alone). Before fitting, ask the
  user how high the camera was held (or the room's height) when you can; else keep `eye_height` 1.4 and
  record it as an assumption with the size as ±5 %.

**What set the scale** (the result's `scale`): each source (depth, door top, eye height, the
typical 2.4–2.7 m wall height) as the factor it alone would grow the fitted room by (1.0 = it agrees). Read
the "scale: ..." warnings: one names a source that stands apart while the others agree (`outlier`) and what
to do - `depth`: fit without depth=true; `door top` / `eye height`: give the door's `top` / the
`eye_height` if the user knows them. When the fit moved to a wrong source, the size is off by about as much
as the others' scales say (others at 1.3 = the room is about 1.3 times too small). Tell the user the
`median` ± `pm` when it is more than 5 % off 1.0.
**A camera outside the room** (the result's `outside`): each such photo gets a `verdict`. When you
know a camera stood in the room, pass its photo id in `inside` (held 0.1 m inside every wall).

| verdict | Means | What to do |
|---|---|---|
| `doorway` | it sees the room only through an opening (`through`, pixels per opening): it stood in that door or in the hallway | fine if that is where the photo was taken, but never name the hallway's own corners / floor as this room's |
| `wall` | it sees pixels through the wall itself (`solid`) or floor / ceiling pixels beyond it (`floor_beyond`): the wall is likely placed too far in. The fit is redone with that wall moved out past the camera (`wall_moved_out`: rms / depth rms before and after, the room) | "about as good" → the wall is the error: find the landmarks that hold that wall (its corners, `floor:<wall>` lines, `wall:<wall>` surfaces in every photo), fix them and fit again (until then trust the moved-out size more). "much worse" → the other photos do see the wall there: check this photo's own landmarks, or it stood beyond the wall |
| `loose` | the photo has fewer `measurements` than a camera's 6 unknowns: nothing holds the camera, so its place says nothing about the walls (no refit) | add points / lines to that photo, or leave it out; do not move a wall for it |

**Windows and doors are fitted only from their own landmarks**: an opening with no marked corner stays at
the guess (warning "not seen in any photo"), and compare_photo then shows it in the wrong place and size (in
one room the window came out narrower than the real one, which reaches further east). For every
window give all four corners (`win_n:start:top`, `win_n:end:top`, `win_n:start:bottom`, `win_n:end:bottom`)
in the photos that show them, at least two photos each, and an edge (`win_n:top`, `win_n:start` ...) where a
corner is cut off by the photo's edge or hidden by furniture; the same for doors (`top`; `bottom` is the floor).
Their offset, width, sill and top are their own unknowns, and the corners also lie on the wall, so they help
place a wall that few photos see. If the fit gets worse with them, check start / end (start = the west end of
a north/south wall, the south end of an east/west wall) and top / bottom before leaving them out, and say in
the report when a window was left at the guess.
**Both edges, or the width is a guess**: an opening with only one edge marked keeps its width near the
guess (door 0.9, window 1.2; warning "only its end edge is marked"): mark the other edge, or ask / record the
width as an assumption. A door or window under 0.4 m wide is warned, and the fit is tried with its start / end
swapped (the result's opening `swapped`). "likely swapped" → fix the labels; "labelled right" → check that
both marks are this opening's edges (not the frame of a hallway door beyond it) and the scale. Never copy a
width under 0.4 m into the scene without checking.
**Landmarks on something else** (the result's `suspects`): when the rms is over 10 px, the fit is redone
without each of the worst objects of one photo (an opening, a corner, a shared point, a wall's line). A
suspect = without that photo's marks on it, the rest agree (rms and the room before / after). In one
room, a wardrobe door 0.8 m in front of the wall marked as the room's door made the depth 0.39 m short. Look
at that photo: if the marks are on furniture, a door frame or another wall, relabel or remove them (all of them,
the lines too) and fit again; if they are right, keep them and say so. Never drop them without looking.
