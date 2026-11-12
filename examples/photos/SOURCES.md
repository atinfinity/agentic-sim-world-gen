# Photo sources

Test photos for photo input. All are released under **CC0 1.0** (public domain dedication) on
Wikimedia Commons; attribution is not required, but we record it anyway. Retrieved 2026-10-25.
Each photo has a `<stem>.source.json` next to it (page URL, author, license, original size, what was changed);
agentic-sim-world-gen reads it when the photo is used as a scene input, so the source ends up in the scene
(`scene.resolved.json`, customData `agentic_sim_world_gen:inputs` on `/World`, `LICENSES.md`).

| File | Title (Commons) | Author | License | Original size | Changes |
|---|---|---|---|---|---|
| `living_room.jpg` | [Living room (Unsplash).jpg](https://commons.wikimedia.org/wiki/File:Living_room_(Unsplash).jpg) | Jarosław Ceborski (Unsplash: jarson, [source](https://unsplash.com/photos/jn7uVeCdf6U)) | CC0 1.0 | 5346×3568 | downscaled to 1920×1281 |
| `meeting_room.jpg` | [HUB Ashdod accelerator meeting room at Kivunim Youth Centre.jpg](https://commons.wikimedia.org/wiki/File:HUB_Ashdod_accelerator_meeting_room_at_Kivunim_Youth_Centre.jpg) | מקף־עברי (own work) | CC0 1.0 | 4592×3064 | downscaled to 1920×1281 |
| `warehouse_aisle.jpg` | [Magazyn fabryki rajstop Gabriella w Łodzi (Polska).jpg](https://commons.wikimedia.org/wiki/File:Magazyn_fabryki_rajstop_Gabriella_w_%C5%81odzi_(Polska).jpg) | MalwiWiadomo (own work) | CC0 1.0 | 3024×4032 | downscaled to 1920×2560 |
| `dining_kitchen.jpg` | [176 Nile Street dining area kitchen.jpg](https://commons.wikimedia.org/wiki/File:176_Nile_Street_dining_area_kitchen.jpg) | Siobhan Leachman (own work) | CC0 1.0 | 4032×3024 | downscaled to 1920×1440 (spare) |

License: <https://creativecommons.org/publicdomain/zero/1.0/>. Downloaded through the Wikimedia Commons API
(`prop=imageinfo`, `iiurlwidth`); the license and author fields come from its `extmetadata`.
To add photos, keep the same layout: the image plus `<stem>.source.json` with at least `url` and `license`.
