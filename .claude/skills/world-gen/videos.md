# Videos

Part of the world-gen skill ([SKILL.md](SKILL.md)); read it when the request needs it.

## Videos (「この動画の部屋を作って」、部屋を歩きながら撮った動画)

A video becomes a few photos of one room; then follow [photos.md](photos.md) for several photos.

1. **People and source first.** Do not use a video that shows people (identifiable persons: faces, bodies
   in the room): tell the user so and ask for one without people (or for photos). The source is required as
   for a photo: a sidecar `<video stem>.source.json` next to the video (url/title/author/license), or pass
   url / license to extract_frames (the user's own video: `license: "own video"`). No people detector is
   guaranteed (`people_check: "none"` unless OpenCV is installed): **you** check the frames in step 3, and if
   anyone is visible in any frame, stop there (or take a start_s/end_s range without people).
2. **`extract_frames(video, out_dir)`** (CLI `agentic-sim-world-gen frames <video> -o <dir>`): picks the best frame of
   each view where the picture changed a lot, not evenly spaced; blur-only stretches are dropped. Sharpness
   is normalized by contrast (a dark corner is not "blurred"), and views that are not the room are
   down-weighted: sky / outdoors, close-ups (a wall, a door, an appliance) and dark frames get a low `room`
   score and `notes`. **A walk through several rooms: pass `start_s` / `end_s`** (CLI `--start/--end`) for
   the room you build; first extract without them, find the room's stretch on the sheet (times are on
   each frame), then extract again with the range. Default
   at most 8, at least 4 (`max_frames` up to 12). **Keep the count low**: every frame is read and rendered,
   so time and cost grow about in proportion; 4-6 is usually enough for one room. Frames go to
   `<out_dir>/inputs/<stem>_tNNN_NN.jpg`, each with a sidecar carrying the video's source + `video` + `time_s`.
   Called before create_scene, it returns `inputs` to put into spec.inputs; on an existing scene it records
   them (frames of the same video replace the earlier ones, a camera set on a frame is kept).
3. **Look at them**: the result's contact sheet, then each frame you use (Read). People visible → stop
   (step 1). **Upright?** The floor must be at the bottom and door frames / wall corners vertical. A phone
   video whose file has no rotation metadata comes out lying on its side: extract again with `rotate`
   (degrees clockwise, CLI `--rotate`): 90 if the floor is on the right, 270 if on the left, 180 if upside
   down (added to the result's `rotate` if the frames were already turned). It is kept in each frame's sidecar,
   so video_poses and later extracts with `times` turn the video the same way. Nothing measures this for
   you: check it on the sheet every time. Frames of another room or with notes (sky / close-up / dark) usually add nothing. Drop frames that add
   nothing (leave them out of fit_photos and give them no camera; compare_photo renders only inputs with a
   camera), or extract again with a smaller `max_frames`. Say which frames you used.
4. **The first frame sets the room's orientation** (camera near the south wall looking north, as for one
   photo). The other frames' cameras are placed **in that room's coordinates**: pick landmarks per frame
   (corners, door/window corners, shared points) and run **fit_photos** for the room size, openings and every
   frame's camera. With `out_dir` = the scene folder it writes them into the scene (`apply`: the room,
   the fitted openings, each frame's input camera - the photo ids must be the input ids; recorded as assumptions
   「写真から当てはめ」), so no set_room / set_input per frame. Read `applied` and the warnings: a camera "inside"
   an object or "outside the room" means a misplaced object or wrong landmarks. Consecutive frames share many landmarks, which
   makes the fit stable. **Give each wall landmarks in at least two frames**: a wall only one frame sees
   carries the room's width or depth alone (fit_photos warns "rests on one photo for the east wall"; on a
   handheld recording one frame's east wall moved the width 3.0 → 3.85 m). Look through the other frames for
   that wall's corner, its floor / ceiling line or plain wall pixels (`surfaces`), and report the warning's
   ± with the size if none shows it.
   Run **video_poses** first (hfov_deg = the video's field of view, or fit_photos'
   estimate): it measures how the camera turned between the frames, and its `relative` goes into the
   fit_photos request. Pairs marked `direct` are reliable; `chain` pairs are weaker on edited videos
   (dissolves, fast pans). A warning that the landmarks turn a camera far from the video means a landmark of
   that frame is probably wrong: check those first; a pair that disagrees by more than 5° is left out of the
   fit (`used: false`), usually a frame of blank wall. It uses ORB features (OpenCV, the `video` extra); if it
   says OpenCV is missing, skip this step and fit from landmarks only. It measures turns and move directions
   between frames, not the cameras' positions over the whole video: the frames are fitted as photos from their
   landmarks, so the landmarks decide the room's size (on a handheld video the room came out about 0.8 m off with
   few landmarks; mark every corner, edge and door top you can see). A frame that video_poses could not tie
   (warning "no relative pose for ...", a `breaks` entry) or that is blurred: move it with extract_frames
   `times` = all the frames' times with that one moved a little (the other frames keep their ids and cameras),
   pick the new frame's landmarks, run video_poses again.
   **Scale**: if no landmark at the ceiling is seen (ceiling corner / ceiling line / a shared point with a known
   height above the floor), the room's size comes from the camera height: give `eye_height` (a hand-held phone
   is about 1.4–1.6 m; 1.4 is the default) and say it is assumed. A 6 % wrong height makes the room 6 % wrong.
   **What set the scale** (the result's `scale`): each source (depth, door top, eye height, the
   typical 2.4–2.7 m wall height) as the factor it alone would grow the fitted room by, 1.0 = it agrees. Read the
   "scale: ..." warnings: one names a source that stands apart while the others agree (`outlier`) and what to do -
   `depth`: fit without depth=true; `door top` / `eye height`: give the door's `top` / the `eye_height` if the
   user knows them. When the fit moved to a wrong source, the size is off by about as much as the others' scales
   say (others at 1.3 = the room is about 1.3 times too small). Tell the user the `median` ± `pm` when it is more
   than 5 % off 1.0.
   **Depth, if AGENTIC_SIM_WORLD_GEN_DEPTH_PYTHON is set**: give each photo its `image` (the frame's path) and call
   fit_photos with `depth=true`. It fills the depth of every landmark pixel from a monocular depth model
   (MoGe-2), and also of `surfaces`: a few pixels on plain floor / ceiling / each wall (`on`: floor, ceiling,
   wall:north ...; not on furniture or rugs), about 10 per surface. It replaces the eye_height guess, but a
   photo that sees only one or two walls absorbs its depth's scale: it helps most when the frames share
   landmarks and video_poses' pairs tie them. Each camera's `depth_scale` far from 1 (a warning)
   means that photo's depth is off: say so. If it says the model is missing, ask before downloading it
   (141 MB, AGENTIC_SIM_WORLD_GEN_DEPTH_DOWNLOAD=1).
   With depth, the wall height is fitted too (`room.height_fitted`; `request.height` is only the start): do not
   record the height as an assumption then. Pick ceiling corners / lines **and** the floor in the same frames
   (or frames joined by video_poses' pairs; a handheld frame seldom sees both). If a warning says the height is not
   fixed by the photos, the cameras and the ceiling move together: move a frame to one that sees a ceiling
   corner and the floor below it, or keep the height as an assumption and say so. If it says the room's
   **scale** is loose (the floor and the ceiling are tied), the shape is right and only the overall size comes
   from the depth: say the size is ± that much, and give the height (fit_height=false) if the user knows it.
   **Depth without a known length** (warning "nothing of known length is seen": no ceiling seen with the height
   given, no shared point at a known height) - the size comes from the depth and the camera height together, and a
   depth model can be off by 10–15 % in every frame (a rendered room came out 13 % big from the depth alone). Ask
   the user how high the camera was held (or the room's height) before fitting when you can; else keep
   `eye_height` 1.4 and record it as an assumption with the size as ±5 %.
   **Typical sizes hold the scale**: mark the **top corners of every door** you see (`<door>:start:top`,
   `<door>:end:top`, or the line `<door>:top`) - each seen door top is held near `door_top` (2.0 m, ±0.1; give
   2.03 for a US room, `null` to turn it off) and replaces the camera-height guess. A door that is not a standard
   one (a sliding door to the ceiling, a low closet) takes its own `openings.<id>.top` / `top_sigma`. A flat top of
   standard height seen in two or more frames can be a shared point with `z` and `z_sigma` (a kitchen counter 0.9
   ±0.03, a desk 0.72 ±0.03); a dining table's top seen in one frame did not help. The result lists `size_priors`
   (typical vs fitted) and warns when one is more than 2 σ off: check that landmark, or the door is not typical.
   **A camera outside the room** (the result's `outside`): each such frame gets a `verdict`. `doorway` = it
   sees the room only through an opening (`through`, pixels per opening) - it stood in that door or in the hallway,
   fine if that is where the frame was taken, but never name the hallway's own corners / floor as this room's.
   `wall` = it sees pixels through the wall itself (`solid`) or floor / ceiling pixels beyond it (`floor_beyond`):
   the wall is likely placed too far in. The fit is then redone with that wall moved out past the camera
   (`wall_moved_out`: rms / depth rms before and after, the room). "about as good" -> the wall is the error: find
   the landmarks that hold that wall (its corners, `floor:<wall>` lines, `wall:<wall>` surfaces in every frame),
   fix them and fit again (until then trust the moved-out size more). "much worse" -> the other frames do see the
   wall there: check this frame's own landmarks, or it stood beyond the wall. When you know a camera stood in the
   room, pass its photo id in `inside` (held 0.1 m inside every wall).
   **Windows and doors are fitted only from their own landmarks**: an opening with no marked corner stays at the
   guess (warning "not seen in any photo"), and compare_photo then shows it in the wrong place and size (on a
   handheld recording the window came out narrower than the real one, which reaches further east). For every
   window give all four corners (`win_n:start:top`, `win_n:end:top`, `win_n:start:bottom`, `win_n:end:bottom`)
   in the frames that show them, at least two frames each, and an edge (`win_n:top`, `win_n:start` ...) where a
   corner is cut off by the frame or hidden by furniture; the same for doors (`top`, and `bottom` is the floor).
   Their offset, width, sill and top are their own unknowns, and the corners also lie on the wall, so they
   help place a wall that few frames see. If the fit gets worse with them, check start / end (start = west end of a north/south wall, south end of an east/west
   wall) and top / bottom before leaving them out, and say in the report when a window was left at the guess.
   **Both edges, or the width is a guess**: an opening with only one edge marked keeps its width near the
   guess (door 0.9, window 1.2; warning "only its end edge is marked") - mark the other edge, or ask / record the
   width as an assumption. A door or window under 0.4 m wide is warned: the fit is tried with its start / end
   swapped (the result's opening `swapped`); "likely swapped" -> fix the labels; "labelled right" -> check that
   both marks are this opening's edges (not the frame of a hallway door beyond it) and the scale. Never copy a
   width under 0.4 m into the scene without checking.
   **Landmarks on something else** (the result's `suspects`): when the rms is over 10 px the fit is redone
   without each of the worst objects of one photo (an opening, a corner, a shared point, a wall's line). A suspect
   = without that photo's marks on it the rest agree (rms and the room before / after). On a handheld run a
   wardrobe door 0.8 m in front of the wall marked as the room's door made the depth 0.39 m short. Look at that
   photo: if the marks are on furniture, a frame or another wall, relabel or remove them (all of them - the
   lines too) and fit again; if they are right, keep them and say so. Never drop them without looking.
5. **One object per real thing, placed where the frames show it.** A door, window, sofa or shelf seen in
   several frames is placed once. **Before adding objects, list the furniture of every frame** and count each
   kind across the frames (two chairs are `chair1`, `chair2`: a handheld run left the second chair out).
   **Locate each one in the fit_photos request** (`locate`, as for photos): 2-4 pixels where it meets the floor
   (feet, base corners - at least two diagonal ones; z = 0) in the frames that show them best, each with
   `object` = its scene id and the same `name` (`desk:sw_leg`) for the same corner in each frame. One point is a
   foot, not the middle (fit_photos warns); `objects[id]` gives the `center`, and with 3+ points `axis_deg` and
   `extent_m` (its length and width on the floor). Mark each point's side when the frame shows it: `edge`
   "front" for a foot / corner on the side it faces (a chair's seat edge, a sofa's seat, a cabinet's doors),
   "back" for the far side. Then add the objects with those ids, select their assets, and run
   **place_from_photos** (scene dir): it fits each selected asset's footprint to its points (center and facing;
   two front feet give the center, not the middle of the front edge) and records the position as an assumption.
   Pass `outlines` in the same call - [{object, photo, box: [u0, v0, u1, v1]}], a tight rectangle around the
   whole object in 1-3 frames that show it (a side at the image border = cut off there) - and it refines each
   pose in the frames (the asset's box drawn by the fitted cameras against the feet and the rectangles); `left`
   lists the objects still to look at (`loose`: the asset's size is wrong or a pick is on something else), the
   rest need no compare-and-move round.
   It keeps the facing you gave unless the points fit another one clearly better, and always for two unmarked
   points - so set facing_deg first from what the frames show. Its warnings name an asset whose size does not
   fit the points (fix min_size / max_size or the asset, then run it again) and a facing it could not tell (mark
   `edge`). Use against_wall / next_to only for what no frame shows on the floor. On a handheld video, relations put the desk 0.85 m off, where the chair stood. The scene
   folder's photo_fit.json then lets status / compare_photo flag what is still off (`photo_placement`:
   `misplaced`, `located_unknown` = located but not added, `unlocated` = floor furniture not located,
   `one_point`). **compose_scene stops while any of these is left**: pick the missing feet in the frames and run
   fit_photos again with the scene folder (a run that only moved things by eye put the desk on the chair).
   Only an object no frame shows on the floor goes in `compose_scene(not_in_photos={id: why})`; report those.
6. compose_scene, then **compare_photo** renders every frame's camera in one run, one row per frame.
   A render that is all black or one flat face is a camera inside an object (set_input / compare_photo warn
   "camera of … is inside …"): move that object where the frames show it, not onto a camera. White furniture
   under warm light looks like the walls in a render; look for its edges before calling it missing.
7. Report: the frames used (time in the video), what each showed, and the video's source.
   license_report lists each frame under "Inputs" as `frame at <t> s of <video>`.
