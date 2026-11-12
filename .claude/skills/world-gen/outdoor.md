# Outdoor sites

Part of the world-gen skill ([SKILL.md](SKILL.md)); read it when the request needs it.

## Outdoor sites (「倉庫の前の構内」「歩道のある通り」)

1. Sketch the site in meters: x east, y north, origin at the south-west corner. width × depth is the site
   (objects must stay inside); the ground runs on 30 m past it (`ground_margin`), so the horizon is not a cliff.
2. `room`: `{"outdoor": true, "width": 40, "depth": 30, "floor": "grass", "lighting": "sun", "areas": [...],
   "buildings": [...]}`. No walls, ceiling, openings or plan_file outdoors.
   - areas: `{"id": "walk", "kind": "sidewalk", "name": "歩道", "rect": [x0, y0, x1, y1]}` (or `polygon`).
     kind sets the defaults — road/parking asphalt, sidewalk paving with a 0.15 m curb, crosswalk, plaza,
     path/gravel, grass, soil, water. road, crosswalk and water are keep-clear: other objects get `in_keep_clear`.
     Later areas draw on top of earlier ones; keep them from overlapping where you can.
   - buildings: `{"id": "warehouse", "rect": [...], "height": 9, "surface": "metal_panel"}` — closed blocks
     (inside not modeled). Facades: concrete, brick, metal_panel, glass, paint_*.
3. Placing objects:
   - On an area: `zone: "<area id>"` + `against_wall: "north"` (that area's edge) or `center`. Objects on a
     sidewalk are lifted to its curb height. A car or cone on the road: zone = the road (then it is allowed).
   - Against a building: `against_wall: "warehouse:south"` (or `"warehouse"` for any side); add the area the
     object stands on as zone (`"apron"`), else it may land on a neighboring area.
   - Site edges (fences): `against_wall: "east"` without a zone, `along` 0..1 to slide it; use `array` for a run.
   - Issues: `hits_building`, `on_curb` (straddles a curb — give it the sidewalk's zone), `in_keep_clear`, `out_of_room`.
4. Lighting: sun (default), overcast, dusk, street_night (lamps on 6 m poles every 12 m). `sky` overrides the
   sky image (clear, partly_cloudy, overcast, evening, night, none, or an .hdr path/URL); the sky is listed in
   LICENSES.md. Outdoor queries: "bench", "trash can"/"recycle waste container", "barricade"/"traffic barrier",
   "fence", "forklift", "plywood crate", "vending machine". Street props are scarce: no traffic cones, cars,
   trees or street lamps (checked 2026-10) — substitute (barricade for cones, a car frame for a car) and report it.

## The outside of an indoor room (「店の前の歩道も」「駐車場つきのコンビニ」「庭のある家」)

For an indoor room whose surroundings matter (a shop seen from the street, a robot that drives in through the
door), keep the room as usual and add `room.site` instead of switching to `outdoor`:

1. `"site": {"rect": [x0, y0, x1, y1], "ground": "asphalt", "lighting": "sun", "areas": [...], "buildings": [...]}`
   in room coordinates (the room is x∈[0,width], y∈[0,depth]; the site may go negative, south of the room is y < 0).
   No rect = the room grown by `around` (8 m). areas / buildings as in Outdoor sites, but they must not reach into
   the room (`site_in_room`). `set_room(site=...)` adds it to a scene (`site=true` = the default: asphalt, sun), `site=false` removes it.
   Windows or a glass door with no site show a gray void outside: status `window_view` says so (a house or a log
   cabin: `site={"ground": "grass"}`).
2. Lighting: `site.lighting` (sun / overcast / dusk / street_night) is the sky and sun; the room's `lighting` is
   only its fixtures inside — use home / office, not studio (studio has no fixtures, so the room would be dark).
3. Placing outside: `zone: "outside"` (the ground around the room) or an area id. `against_wall: "outside:south"`
   puts the back against the room's south outer wall (vending machines, a trash station by the entrance —
   keep `along` away from the door), `"<building>:<side>"` by a neighbor, `against_wall: "north"` with an area's
   zone that area's edge. Objects without a zone are inside the room as usual. Doors in the room's walls open
   to the outside. `out_of_room` for an outside object means it is in the room or off the site.
4. check / look_views frame the room and the things outside, plus an `exterior` view from
   the street. The preview's top view starts on the room alone (「敷地まで表示」 shows the whole site).
5. Without a site the outside of every window and open front is a plain grey dome. When the photos or the request
   show the outside (a shop open to the street, a window onto a garden), add a site — even a small one
   (`{"ground": "grass", "lighting": "sun"}`) — so the openings show ground and sky.
