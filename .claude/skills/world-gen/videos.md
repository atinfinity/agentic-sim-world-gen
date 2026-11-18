# Videos

Part of the world-gen skill ([SKILL.md](SKILL.md)); read it when the request needs it.

## Videos (「この動画の部屋を作って」、部屋を歩きながら撮った動画)

A video becomes a few photos of one room; from then on follow [photos.md](photos.md) for several photos
(you need its steps 2, 3, 5 and 7; read it with Read, not cat). This file adds what is specific to video frames.

**The order** (do not skip the measuring steps 4a-4c): source and people → extract_frames → look at the
frames → **video_poses → fit_photos** (writes the room and the cameras) → place the furniture located by
fit_photos → compose_scene → **compare_photo** → report.

1. **People and source first.** Do not use a video that shows people (identifiable persons: faces, bodies
   in the room): tell the user so and ask for one without people (or for photos). The source is required as
   for a photo: a sidecar `<video stem>.source.json` next to the video (url/title/author/license), or pass
   url / license to extract_frames (the user's own video: `license: "own video"`). No people detector is
   guaranteed (`people_check: "none"` unless OpenCV is installed): **you** check the frames in step 3. If
   anyone is visible in any frame, stop there (or take a start_s/end_s range without people).
2. **`extract_frames(video, out_dir)`** (CLI `agentic-sim-world-gen frames <video> -o <dir>`) picks the best frame of each
   view where the picture changed a lot, not evenly spaced frames. Blur-only stretches are dropped. Sharpness
   is normalized by contrast (a dark corner is not "blurred"), and views that are not the room are
   down-weighted: sky / outdoors, close-ups (a wall, a door, an appliance) and dark frames get a low `room`
   score and `notes`.
   - **A walk through several rooms**: pass `start_s` / `end_s` (CLI `--start/--end`) for the room you build.
     First extract without them, find the room's stretch on the contact sheet (each frame shows its time),
     then extract again with the range.
   - Frame count: default at most 8, at least 4 (`max_frames` up to 12). **Keep the count low**: every frame
     is read and rendered, so time and cost grow about in proportion; 4-6 is usually enough for one room.
   - Frames go to `<out_dir>/inputs/<stem>_tNNN_NN.jpg`, each with a sidecar carrying the video's source +
     `video` + `time_s`. Called before create_scene, it returns `inputs` to put into spec.inputs. On an
     existing scene it records them (frames of the same video replace the earlier ones; a camera set on a
     frame is kept).
3. **Look at them**: the result's contact sheet, then each frame you use (Read). People visible → stop
   (step 1).
   - **Upright?** The floor must be at the bottom and door frames / wall corners vertical. A phone video whose
     file has no rotation metadata comes out lying on its side: extract again with `rotate` (degrees
     clockwise, CLI `--rotate`): 90 if the floor is on the right, 270 if on the left, 180 if upside down
     (added to the result's `rotate` if the frames were already turned). It is kept in each frame's sidecar,
     so video_poses and later extracts with `times` turn the video the same way. Nothing measures this
     for you: check it on the sheet every time.
   - Frames of another room or with notes (sky / close-up / dark) usually add nothing. Drop frames that add
     nothing (leave them out of fit_photos and give them no camera; compare_photo renders only inputs with a
     camera), or extract again with a smaller `max_frames`. Say which frames you used.
4. **Cameras and room: measure them, never guess them.** The first frame sets the room's orientation
   (camera near the south wall looking north, as for one photo); the other frames' cameras are placed in that
   room's coordinates. Do these in order, every time, before placing any furniture:
   (a) **video_poses**(scene_dir, hfov_deg): how the camera turned between the frames (`relative`).
   (b) frames it could not tie (`breaks`) or blurred ones: move them and run video_poses again.
   (c) **fit_photos**(out_dir=<scene>) with that `relative`, the landmarks of every frame
       ([photos.md](photos.md) step 3) and `locate` for the furniture (step 5 below): it writes the room size,
       the openings and every frame's camera into the scene.
       **Images fill the context**: Read each frame once (one grid overlay per frame at most), and write its
       landmarks to `<scene>/landmarks.json` (Write) as soon as you have them. Do not look at the contact sheet
       or the frames again while a tool runs. After a compaction, read `landmarks.json` (or the request
       fit_photos kept in `<scene>/photo_fit.json`), not the frames.
   (d) read fit_photos' warnings and fix them.
   **When video_poses returns, read [videos-fit.md](videos-fit.md)** for its result keys, moving frames, scale,
   depth and each fit_photos warning.
   Skip a step only when its tool says it cannot run (no OpenCV for video_poses: say so in the report). Room
   size, cameras or furniture positions set by eye from the frames are not a substitute: a run that skipped
   (a)-(c) got a room that did not match the video.
5. **One object per real thing, placed where the frames show it.** A door, window, sofa or shelf seen in
   several frames is placed once. **Before adding objects, list the furniture of every frame** and count each
   kind across the frames (two chairs are `chair1`, `chair2`: a handheld run left the second chair out).
   The list also has the wall, floor, window and ceiling things ([photos.md](photos.md) step 2: pictures, a
   clock, rugs, curtains, light fixtures, plants on a sill): a living room video kept only its 9 pieces of
   furniture, with no rug, curtains or pictures. They are not located in fit_photos unless they stand
   on the floor; hang them by wall and `along`.
   Locate each one in the fit_photos request (`locate`, [photos.md](photos.md) step 3) in the frames that show
   it best, then place them with place_from_photos and clear `photo_placement` before compose
   ([photos.md](photos.md) step 5). Fill the windows with what covers them in the frames:
   `fill_windows(covers="blind" | "curtain" | None)`.
   Wall and ceiling equipment the frames show (an air conditioner, radiators under the windows, wall speakers,
   ceiling vents: [interior.md](interior.md) "Wall and ceiling equipment") is hung by wall and `along`, not
   located; the ones with no asset are named in the report.
6. compose_scene, then **compare_photo** renders every frame's camera in one run, one row per frame.
   A render that is all black or one flat face is a camera inside an object (set_input / compare_photo warn
   "camera of … is inside …"): move that object where the frames show it, not onto a camera. White furniture
   under warm light looks like the walls in a render; look for its edges before calling it missing.
7. Report: the frames used (time in the video), what each showed, and the video's source.
   license_report lists each frame under "Inputs" as `frame at <t> s of <video>`.
