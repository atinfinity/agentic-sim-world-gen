# Floor plans

Part of the world-gen skill ([SKILL.md](SKILL.md)); read it when the request needs it.

## Floor plans

1. **JSON / DXF / IFC**: call `import_floor_plan(path)`. Read back size, walls, zones (id, name, area) and
   warnings; show the user `plan.svg` and ask them to confirm room names and doors. DXF: walls are found by
   layer name (WALL, 壁 …) and door/window blocks by name (DOOR, 窓 …); if no walls are found the error lists
   the layers — ask which are walls, then pass `wall_layers` (names or patterns, e.g. `["E-B-*", "0"]`).
   Read the warnings: "walls cover only part of the drawing" (more wall layers needed) and "units look wrong"
   (pass `units="mm"` etc.; drawings often have a wrong $INSUNITS). Rooms drawn as closed rectangles on the
   wall layer are fine (shared edges are merged; a closed outline holding one room label becomes a zone).
   IFC: pick `storey` if there are several floors.
2. **Image (PNG / JPEG) or PDF**: there is no automatic reader — trace it yourself:
   - PDF: convert the page first (`pdftoppm -png -r 150 plan.pdf page`), then look at the image.
   - Find the scale: a dimension label (e.g. 3,640 between two wall lines) ÷ its pixel length = `m_per_px`.
     Check it on a second dimension. Japanese plans: 1 畳 ≈ 1.82 × 0.91 m, the 910 mm grid.
   - Write a plan JSON with `"units": "px"`, `"underlay": {"image": "page-1.png", "m_per_px": ...}` and the
     wall centerlines, opening offsets / widths and zone polygons in image pixels (y down is fine; heights and
     sills stay in meters). Walls meet at centerline corners; give a thickness in px (typ. 0.12–0.18 m).
   - `import_floor_plan` it and open `plan.svg`: the image is drawn under the trace. Fix until walls sit on
     the drawn walls, then confirm the result with the user (traces are approximate).
3. Create the scene with `room: {"plan_file": "<.../plan.json>"}` (plus floor/walls/lighting). `get_scene`
   shows faces per zone. Issues `hits_wall`, `blocks_door`, `out_of_room` mean the object needs another spot.
   Floors per zone: a zone named 和室 / 畳 / tatami gets `tatami`, 洗面 / 脱衣 / 浴室 / トイレ / 玄関 / bath / toilet /
   entrance get `tile_white`, the rest `room.floor` (the zones' `floor` / `floor_from` in get_scene). Change with
   `set_room(zone_floors={"<zone id>": "tatami" | surface | words | "room"})` (`"room"` = the room floor, `null` =
   back to the guess; words search target `"floor:<zone id>"` for view_surface_candidates / select_surface).
   A plan JSON zone may carry `"floor"` itself. Walls stay one surface for the whole plan.
4. **BIM furniture (IFC)**: `import_floor_plan` also returns `furniture` (drawn as dashed brown boxes with a
   front tick in plan.svg) and draft `objects`. Then:
   - Show the user the list (name / type, zone, size, guessed query) with plan.svg; ask which to include.
   - Refine each `query` into a good English search from the name, type_name, predefined_type and
     classification (e.g. 「レジカウンター」→ "checkout counter", 「ゴンドラ什器」→ "gondola shelf");
     `query_from: "class"` means nothing was recognized — read the names yourself. Keep `position`,
     `facing_deg`, `z`, `zone`, `ifc_guid` and `min_size` / `max_size` (footprint ±20 %; height -30 % .. +50 %, since BIM heights are
     often simplified boxes). If nothing fits, `size_misses` lists good matches with `off` (which limit, by how much):
     widen that limit.
   - `create_scene` with `room: {"plan_file": ...}` and these objects (no `layout`: they stay where the BIM
     put them; yaw is set so the asset's front faces `facing_deg`). Add fill / extra objects as usual.
   - Front = the element's local −Y (usual for BIM furniture). If a piece faces the wrong way in layout.svg
     or the render, set `front_offset_deg` (180 = turned around) on that object; tell the user.
   - compose → check as usual; each prim keeps `agentic_sim_world_gen:ifc_guid` (customData) for tracing back to the BIM.
