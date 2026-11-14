# Screens and printed pictures

Part of the world-gen skill ([SKILL.md](SKILL.md)); read it when the request needs it.

Some shop assets in a `local:` asset folder carry their own picture: shop ads, menus, job ads or greetings.
A variant set switches it:

| Kind | Assets | Variant set | Shows the picture | Hides it | Suits (asset.json) |
|---|---|---|---|---|---|
| Screen | `digital_signage_stand_43`, `digital_signage_ceiling_3x` (café / convenience-store ads) | `Screen` | `On` (default) | `Off` (a dark, unlit screen) | `screen.places` |
| Printed picture | `wall_poster`, `window_poster`, `aisle_sign_hanging`, `a_frame_sign`, `entrance_mat` | `Picture` | `Default` | `Blank` (plain paper / board / mat) | `picture.places` |

The places default to shops / cafés / restaurants. Elsewhere (factory, warehouse, office, lab, hospital ...)
set `{"Screen": "Off"}` / `{"Picture": "Blank"}`, or `set_screen` with an image that fits (below), and say so in
the summary. The check enforces this: an asset showing its own picture where `room.place` is not one it suits,
or with no `room.place`, is a `screen_content` issue. The issue text names the value to set. Fix it with the
variant or `set_screen`, never by changing `place`.

**Read what the pictures say**: a place that suits is not enough. Shop fixtures carry section headers
and POPs with Japanese wording (「メンズ トップス」, 「話題の本・今週の新刊」). Status `pictures` lists every
asset picture the scene shows (objects, `text` / `lang` / `audience` when asset.json has them, and `blank` = the
variant that hides it); `pictures_hint` names those you have not looked at. Once the assets are chosen, call
`view_pictures`, then blank (`set_variants` with `blank`) any whose wording the request or the photos do not
support: a men's or women's section the user did not ask for, a genre or section the store does not have,
Japanese text in a store whose photos show another country or language. Say which ones you blanked.

**Your own image**: to show something that fits the place instead (a safety poster, KPI board, floor map, the
user's own image), use `set_screen(object_id, {"image": "<png/jpg>", "fit": "contain"})` on an asset whose
summary lists `screens` (this covers screens and printed pictures). agentic-sim-world-gen paints the image onto the screen's
front face in a copy of the texture (`screens/<id>_<material>.png`, aspect kept; `contain` pads with `background`,
`cover` crops, `stretch` fills) and keeps `Screen` On (`Picture` Default). For several screens on one
asset: `"image": {"<material>": "<file>", ...}`. Record where the image came from (`url`, `title`, `author`,
`license`, or a `<stem>.source.json` next to it); it goes into LICENSES.md. `set_screen(object_id, null)`
restores the asset's own picture. Don't invent images: use one the user gave, or ask; otherwise prefer
`{"Screen": "Off"}` / `{"Picture": "Blank"}`.
