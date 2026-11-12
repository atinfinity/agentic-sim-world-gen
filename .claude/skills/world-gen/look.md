# Looks: reference images, moods, taste

Part of the world-gen skill ([SKILL.md](SKILL.md)); read it when the request needs it.

## Reference images (「こんな感じの部屋」「この写真の雰囲気で」)

A moodboard or web photo that shows the **look**, not the room to rebuild: take its taste only — never its
size, layout or camera. (To rebuild the pictured room itself, use [photos.md](photos.md).)

1. **Source first**, as for photos (sidecar `<stem>.source.json` or url/author/license). Web images need it too.
2. **Look** with Read and fill the taste (English words for anything searched; `note` one Japanese line):
   ```json
   {"floor": "light oak herringbone parquet", "walls": "white paint", "ceiling": null,
    "lighting": "home", "light_kelvin": 4500, "lighting_scale": 1.2,
    "furniture": {"desk": "white", "chair": "white plastic, metal legs", "shelf": "white lacquer"},
    "style": ["scandinavian", "minimal"], "density": "sparse", "note": "明るい北欧風。白い家具とオークの床"}
   ```
   Light: warm bulbs / evening 2700–3000 K, neutral 4000–4500 K, office daylight 5000–6500 K; `lighting_scale`
   1.2–1.5 for a bright, airy picture, 0.6–0.8 for a dim one. `furniture` keys are object kinds or ids.
   `density`: sparse / normal / lived_in → `room.density` (or `set_room(density=...)`), which adds the small
   things itself.
3. **Record** it: `inputs: [{"id": "ref", "kind": "reference", "path": "<file>", "taste": {...}}]` in the spec
   or `set_input`. No `camera` (refused for a reference): it makes no `/World/Cameras/<id>`, is not drawn in
   layout.svg and is skipped by compare_photo / fit_photos. It shows in the preview (参考画像, with the taste
   table), in `/World` customData `agentic_sim_world_gen:inputs` and in LICENSES.md "Inputs" (reference image / 参考画像).
4. **Apply** with the usual tools:
   - `set_room(floor=..., walls=..., ceiling=..., lighting=..., light_kelvin=..., lighting_scale=...)`. For the
     material search, **name the material and species** ("oak floor planks", "cherry wood planks",
     "porcelain tile", "lime green paint"); color adjectives only reorder ("honey colored wood" alone found
     mulch and oak leaves). Then `view_surface_candidates` and `select_surface` the one whose thumbnail color
     matches the picture (e.g. the lighter ash herringbone over a dark oak one). An accent wall in the
     picture → `walls={"*": ..., "N": ...}`. A material much paler than the picture → a preset with `color`.
   - **Colors from the picture**: give them as sRGB hex (`"color": "#8cbfad"`). A pixel in a photo is
     paint × that room's light, and the render lights the wall again, so a raw pick comes out too dark and too
     tinted. Pick from the **brightest evenly lit part** of the wall (not the shaded corner), and if the photo
     has a warm or cool cast, judge the paint against something white in it (ceiling, trim) and take the cast
     out of the color: put the cast into `light_kelvin` instead. Measured in Isaac Sim 6.1 (`office`, ceiling
     on): a wall of `#8cbfad` renders about 25 % darker (≈ `#6a8c6c`) and a little yellower; under `home`
     (2700 K-ish bulbs) the same mint turns olive. So for a pale tint use the paint color, not the photo pixel,
     and check `check_in_isaac` / look views before reporting the match.
   - Furniture: keep `query` a plain noun ("chair", "bookshelf") and put the taste into `alt_queries`
     ("white chair", "wooden chair", "black metal shelving"). A color in the main query makes the real
     matches weak. Widen `min_size` / `max_size` a little (a dining chair is 0.45 m wide, an open shelf
     1.5 m long). Then `view_candidates` and `select_asset` by caption / thumbnail color. Nothing in that
     color: `research_object` with the attribute, else keep the closest and say so.
   - **Wrong color, right shape** (a red rotating rack in the picture, the asset is white): keep the asset and
     `set_tint(object_id, "#rrggbb")`. It repaints the asset's main color (the largest painted area and every
     material of that color; glass, bare metal and screens stay). Parts by name:
     `{"color": "#2b2b2b", "materials": ["Pole*"]}` (names from the USD; customData `agentic_sim_world_gen:tint` after
     compose lists what changed). A textured material (wood grain, print) is multiplied, so tint wood with a
     light warm color, not the target color. To make a printed part plain (a sign's print made white),
     add `"texture": false`: the color and normal textures are dropped and the color is flat. Pick the color from the picture, not a name ("red" → `#c0392b`).
     Record it in the assumptions (色：赤 `#c0392b`、参考画像 <id> から).
5. **Assumptions**: `set_assumptions` for floor, walls, lighting and each object whose look came from the
   picture, `basis: "参考画像 <id> から"`, given=false (the user showed a picture, not the words).
6. **Report** what matched and what did not (e.g. 「白いシェルチェアは素材に無いのでグレーの事務椅子」), with the
   reference's source. The catalog is office/warehouse heavy: wooden home furniture and white chairs are rare.

## Moods from sample pictures (「雰囲気はおまかせ」「どんな感じがいいか分からない」)

When the user cannot say what look they want (no reference image, 「テイスト」 left open), do not ask them to
describe it: the preview page has a 「雰囲気」 section with 3–4 sample pictures for the place (home, office,
shops, cafes — 木目ナチュラル・白モダン・工業系・落ち着いた濃い木). Say in one line that they can click one there
(or name it in chat). `moods(scene_dir)` lists them (the `image` is a standard room drawn once, not this scene;
Read it to look). `apply_mood(scene_dir, name)` changes floor, walls, trim, ceiling, light color and brightness at
once and records them as assumptions; it does not swap furniture: for each `mood_furniture` row run
`research_object(object, query=row.try, select=false)`, look with `view_candidates`, and offer the swaps that match
(do not swap a good piece for a substitute). **At the start of every turn, if status has `mood_hint`**, the user
picked a mood on the preview page: say so, do the furniture as above, then call `apply_mood` with the same name
to clear it. The room part is already applied; do not redo floor / walls with set_room unless asked. A reference
image wins over a mood when the user gave one (the page folds the 「雰囲気」 cards then). In a shop,
leave the store fixtures (gondolas, checkout counter, cases) out of the furniture swaps: 「white checkout counter」
finds kitchen counters; use `set_tint` on the fixture instead.

## Taste

**Taste**: once the assets are chosen (3 or more objects; decor and shelf stock are left out), call
`check_taste` once, and again after you change several assets. It searches a wider pool per object (40
results, saved in the scene; the first run takes a minute or two), embeds the thumbnails (SigLIP2 on
CPU, ~1 s per new thumbnail, cached) and flags an object whose look is far from the others (a white study with
one dark-steel bookshelf); the status keeps the result as `taste` and the preview shows 「テイストの注意」. It is
a warning, not an issue, and it only knows the thumbnails: when the user asked for that thing (「黒い棚」, an
accent piece) or the room is meant to be mixed, keep it and say nothing. Otherwise, in step 5, add one line
per outlier: what stands out and how (`looks` vs `others_look`), and the `alternatives` (index, name; ones
from the pool are appended to the object's candidates, so they have an index too; look at them with
`view_candidates` first and drop the ones that are the wrong kind of thing), e.g.
「本棚（青いスチール棚）がほかの白い家具から浮いています。候補 [10] 白い本棚 にしますか？」. Swap with
`select_asset` only after the user picks one (or clicks a card in 「テイストの注意」); never swap on your own,
and say so when there is no better candidate. `taste.skipped` (no torch / transformers or no model in the
Hugging Face cache, e.g. on the Mac) is fine: mention it once in the report, do not install anything.
