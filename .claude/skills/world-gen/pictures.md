# Screens and printed pictures

Part of the world-gen skill ([SKILL.md](SKILL.md)); read it when the request needs it.

A `Screen` set (On / Off) switches a display's built-in picture: digital signage from a
`local:` asset folder (e.g. `digital_signage_stand_43`) shows café / convenience-store ads when On (default).
Outside shops and cafés (factory, warehouse, office, lab, hospital...) set `{"Screen": "Off"}` (a dark, unlit
screen) and say so in the summary. The check enforces this: a lit screen showing the asset's own picture where
`room.place` is not one it suits (asset.json `screen.places`, default shops / cafés / restaurants), or with no
`room.place`, is a `screen_content` issue. Fix it with Off or `set_screen`, never by changing `place`.
Printed pictures work the same way with a `Picture` set (Default / Blank): a `local:` folder's `wall_poster`,
`window_poster`, `aisle_sign_hanging`, `a_frame_sign` and `entrance_mat` carry shop ads, menus, job ads or
greetings (asset.json `picture.places`). Elsewhere set `{"Picture": "Blank"}` (plain paper / board / mat) or
`set_screen` with an image that fits; the issue text names the value to set.
**Read what the pictures say**: a place that fits is not enough - shop fixtures carry section headers
and POPs with Japanese wording (「メンズ トップス」, 「話題の本・今週の新刊」). Status `pictures` lists every
asset picture the scene shows (objects, `text` / `lang` / `audience` when asset.json has them, `blank` = the
variant that hides it) and `pictures_hint` names those you have not looked at: call `view_pictures` once the
assets are chosen and blank (`set_variants` with `blank`) any whose wording the request or the photos do not
support - a men's or women's section the user did not ask for, a genre or section the store does not have,
Japanese text in a store whose photos show another country or language. Say which ones you blanked.
To show something that fits the place instead (a safety poster, KPI board, floor map, the user's own image),
use `set_screen(object_id, {"image": "<png/jpg>", "fit": "contain"})` on an asset whose summary lists
`screens` (screens and printed pictures). agentic-sim-world-gen paints the image onto the screen's front face in a copy of the texture
(`screens/<id>_<material>.png`, aspect kept; `cover` crops, `stretch` fills, `background` pads) and keeps
`Screen` On (`Picture` Default). Several screens on one asset: `"image": {"<material>": "<file>", ...}`. Record where the
image came from (`url`, `title`, `author`, `license`, or a `<stem>.source.json` next to it); it goes into
LICENSES.md. `set_screen(object_id, null)` restores the asset's own picture. Don't invent images: use one the
user gave, or ask; otherwise prefer `{"Screen": "Off"}` / `{"Picture": "Blank"}`.
