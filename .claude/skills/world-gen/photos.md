# Photos

Part of the world-gen skill ([SKILL.md](SKILL.md)); read it when the request needs it.

## Photos (「この写真の部屋を作って」)

Looking at the photo with Read is not enough. Record it with `set_input` (with its camera), and after every
compose compare it with `compare_photo` until the scene matches. Status `photo_compare_hint` says when that
comparison is missing or out of date, and `compose_scene` returns it as `next`.
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
   furniture, large props. Skip small clutter (cushions, candles, cables) unless asked.
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
3. **Orient the room.** The camera stands near the **south** wall looking **north**: the far wall is
   north (y = depth), the left wall west (x = 0), the right wall east (x = width). Things behind the camera
   are unknown: leave that part of the room empty or make a sensible guess, and say so.
   **Several photos of one room** (from different sides, or frames of a walk-through): the first photo
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
     an opening without them stays at its guessed place and size (details in [videos.md](videos.md) step 4).
     Read pixels off the image carefully (overlay a grid when needed). Aim for 6+ measurements per photo.
     Check `rms_px` (a few px is good), `worst` and `warnings`. A large error is usually a mislabeled landmark
     (start/end swapped, wrong corner); a camera with ± > 0.3 m needs more landmarks at different depths.
     With `out_dir` = the scene dir, the room and each input's `camera` are written into the scene
     (`applied`). With `apply=false` it only fits (e.g. before the scene exists): then pass `room` to
     create_scene and the cameras as each input's `camera`. The wall height sets the scale: use a known one
     (2.4 m is the default) and say it is assumed (with depth it is fitted, see [videos.md](videos.md)).
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
   (relations put the desk where the wardrobe stood; on a handheld video, 0.85 m off where the chair
   stood). The scene folder's photo_fit.json lets status / compare_photo flag what is still off
   (`photo_placement`), and **compose_scene stops while any of these is left** (a run that only moved things by
   eye put the desk on the chair):

   | `photo_placement` key | Means | Fix |
   |---|---|---|
   | `misplaced` | placed more than 0.3 m from its located feet (`there` = what stands there now) | place_from_photos moves it; move `there` too |
   | `located_unknown` | located, but no object has that id | add the object, or fix the locate's `object` |
   | `unlocated` | floor furniture with no locate | pick 2-4 feet / corners in the photos, fit_photos again with the scene folder, place_from_photos |
   | `one_point` | rests on one located point | add 1-3 more feet / corners (a diagonal one), fit again, place_from_photos |

   Only an object no photo shows on the floor goes in `compose_scene(not_in_photos={id: why})`; report those.
   Size ranges come from what you estimated (wide ranges: ±30%). Use the windows/doors in room.openings when
   visible. Match the floor/wall looks and the lighting (e.g. herringbone parquet -> wood_light, white paint
   -> paint_white, home lighting; warehouse -> concrete/epoxy + warehouse).
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
