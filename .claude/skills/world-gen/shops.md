# Shops, restaurants, plant factories

Part of the world-gen skill ([SKILL.md](SKILL.md)); read it when the request needs it.

## Shops by kind

Sizes are typical Japanese stores; ask if the user cares, otherwise say what you assumed. Fixtures and products
from a shop asset folder (`local:`) if the user has one; otherwise stand-ins from SimReady Central, reported as
substitutes. Appliances from SimReady Central.

| place | 例 | room (w × d × h m) | look | layout |
|---|---|---|---|---|
| convenience_store | コンビニ | 10 × 8 × 2.7 | tile_white + paint_white + office | register near the door, reach-in coolers on the back wall, gondola rows in the middle |
| supermarket | スーパー | 30 × 20 × 3.5 (small 20 × 15) | tile_white + paint_white + office | produce at the entrance, meat / fish / dairy cases on the back wall, gondola aisles (1.8–2.4 m) in the middle, checkout lanes by the exit |
| drugstore | ドラッグストア | 20 × 15 × 3.0 | tile_white + paint_white + office | gondola aisles 1.5–1.8 m, pharmacy counter at the back, cosmetics near the entrance |
| home_center | ホームセンター | 40 × 30 × 6.0 | epoxy_gray / concrete + metal_panel + warehouse | tall racks (2.4–4.5 m) with 2.5–3 m aisles for carts and forklifts, garden goods outside or by the door |
| hundred_yen_shop | 100円ショップ | 15 × 10 × 2.7 | tile_white + paint_white + office | low gondolas (1.5–1.8 m) with hooks, narrow aisles 1.2 m |
| bookstore | 書店 | 15 × 10 × 2.7 | wood_light + paint_white + office | wall shelves, flat tables (平台) for new books in front, magazine racks by the window |
| electronics_store | 家電量販店 | 30 × 20 × 3.5 | tile_white + paint_white + office | appliances on low stands by category (TV wall, fridges / washers in rows), 2 m aisles |
| apparel_store | 衣料品店 | 15 × 10 × 3.0 | wood_light + paint_white + office, `light_kelvin` 4000 (neutral white so the clothes keep their colors; station-building tenants are bright) | wall racks and shelves, round / straight hanger racks on the floor, fitting rooms at the back |
| bakery | パン屋 | 8 × 6 × 2.7 | wood_light + paint_white + home | bakery shelves on the walls, 1–2 flat tables (平台) with bread in the middle so the centre is not empty, tray & tongs stand by the entrance, register near the door, eat-in counter by the window |
| variety_store | 雑貨店 | 8 × 6 × 2.7 | wood_light + paint_white + home | wooden wall shelves and open shelving, 1–2 display tables in the middle, a small register by the door; shelves are stocked by their words (towel → towels, stationery → notebooks, plant / pot → flower pots, candle, kitchen / tableware → mugs), anything else gets mugs |
| florist | 花屋 | 6 × 5 × 2.7 | tile_white + paint_white + home | buckets and tiered flower stands at the front (open to the street), a flower cooler (reach-in, glass doors) on the back wall, a work table and register at the back; cut-flower bunches are not in the catalog yet (potted flowers are) |
| greengrocer | 八百屋 | 8 × 6 × 2.7 | concrete + paint_white + office | sloped produce tables and crates from the entrance, a cooler for cut fruit / leafy greens on the back wall; often open-fronted (wide `opening`) |
| toy_store | おもちゃ屋 | 15 × 10 × 2.7 | tile_white + paint_white + office | gondolas 1.5 m with hooks, low tables for display in the front, register by the entrance |
| pet_shop | ペットショップ | 12 × 10 × 2.7 | tile_white + paint_white + office | food on gondolas, toys / treats on hooks; no live-animal cases (none in the catalog) |
| garden_center | 園芸店 | 30 × 20 × 4.5 | concrete + metal_panel + warehouse | plant tables in rows (aisles 1.2 m for carts), soil bags on pallets, a site (`room.site`) for the outdoor yard |
| shoe_store | 靴屋 | 12 × 8 × 2.7 | wood_light + paint_white + office, `light_kelvin` 4000 | wall shoe shelves, low benches with a floor mirror in the middle, shoe-box racks in the back |
| optician | 眼鏡店 | 8 × 6 × 2.7 | wood_light + paint_white + office | wall cases of frames, a counter with chairs, an eye-test corner at the back (no phoropter in the catalog: say so) |
| liquor_store | 酒屋 | 10 × 8 × 2.7 | tile_white + paint_white + office | sake / wine / whisky on wall shelves and gondolas, beer / chuhai in reach-in coolers on the back wall (cold cases get beer, not soft drinks) |
| bicycle_shop | 自転車店 | 10 × 8 × 2.7 | concrete + paint_white + office | bicycles stand on the floor as objects (an `array` of bicycles in or by a bicycle stand rack), helmets / lights / locks on wall shelves, a repair bench at the back |
| wagashi_shop | 和菓子屋 | 6 × 5 × 2.7 | wood_light + paint_white + home, ceiling board | a glass showcase of fresh sweets (dango, manju, monaka), gift boxes and yokan on wall shelves, a noren at the door (`against_wall` with a wall height, not hung from the ceiling) |
| record_store | レコード店 | 8 × 6 × 2.7 | wood_light + paint_white + office, ceiling board | record bins (エサ箱) in rows get LPs, CDs on wall shelves, a listening corner with a turntable |
| sporting_goods_store | スポーツ用品店 | 20 × 15 × 3.0 | tile_white + paint_white + office | gondolas of balls, rackets and shoes (aisles 1.2 m); a ball cart has no shelf levels, so it stands as a fixture and is not stocked |
| cosmetics_store | 化粧品店 | 10 × 8 × 2.7 | tile_white + paint_white + office | wall shelves of lotion, lipstick and sunscreen, tester islands and tables in the middle (they get stock too) |
| furniture_store | 家具店 | 30 × 20 × 3.5 | wood_light + paint_white + office | beds, sofas, tables and wardrobes are floor objects grouped as showroom corners (aisles 1.2 m); shelves get cushions, lamps, frames and vases |
| asian_grocery | アジア食材店 | 8 × 6 × 2.7 | tile_white + paint_white + office, ceiling board | gondolas of noodles, rice, chili oil and sauces, a reach-in cooler (asked as cold cases) |

**Other kinds**: a shop not in the table (鮮魚店・文房具店・楽器店 …) is `place="shop"` plus
`set_room(goods=[English product queries], goods_words=[the kind])`:

- 文房具店 → `goods=["ballpoint pen", "notebook", "eraser"], goods_words=["stationery"]`
- 楽器店 → `goods=["guitar", "ukulele", "music stand"], goods_words=["music", "instrument"]`

With a density, the shelves get only these goods. A stocked asset must name one of their words (so "racket box"
does not bring a beer box). For a query of two words or more it must also name the head noun plus the qualifier or
a goods_words word ("ballpoint pen" needs "pen", and "ballpoint" or "stationery": a pig pen is not stocked).
Goods with no asset come back in `decor_goods_hint`: try other English words or a `local:` folder, or tell the
user it is missing — do not stock unrelated products by hand. Without goods such a shop gets `no_kind` skips
(`decor_kind_hint`).

**Storefront windows**: make the shop window a window opening in the room (`openings`, 1.8 m+ wide).
`create_scene` fills it (`fill_windows` for one added later) by searching "shop window" sized to the opening (a
wide FIX window or a shop-front sash, from a shop asset folder if there is one). Do not add a window as a free object against a
wall: with no size, "shop window" finds a 3.1 m glass facade (taller than the ceiling; candidates taller than the
room go last, `match.too_tall`) or a microwave's window.

**Fill the floor**: the example sizes come with their fixture counts — a bigger room needs more. Size
the layout to the room, not the example:

- rows = (room depth − wall fixtures − front zone) ÷ (fixture depth + the aisle in the table); the front zone is
  ≈ 3 m by the entrance and registers.
- Each row (an `array`) is as long as the room minus the side aisles.

Floor fixtures cover about 15–35 % of the floor in the examples (konbini 33 %, the bigger stores 15–23 % with
their wider aisles); a trial drugstore of 20 × 15 m with three short islands left 4–5 m wide empty patches.
Leftover space goes to more rows or longer ones, or the room gets smaller — not left as an open hall.
`empty_floor` / `wide_aisle` in the status point at what is left. From photos, do not make the room bigger than
the pictures support: the part no photo shows stays small.

Queries (English, with synonyms): 青果の平台 "produce display table" / "produce stand"; 対面ケース "serve-over counter" /
"deli case"; 平型冷凍ケース "island freezer" / "chest freezer"; レジ台 "checkout lane" / "checkout counter";
サッカー台 "bagging table"; 薬の棚 "pharmacy shelf"; 長尺ラック "pallet rack" / "warehouse shelving"; 平台（書店）
"book display table"; パンの平台 "bakery display table" / "produce display table"; パン棚 "bakery shelf";
トレーとトング "tray and tongs stand"; 雑貨の棚 "wooden display shelf" / "open shelving"; 雑貨の平台 "display table"; ハンガーラック "clothing rack" / "garment rack"; テレビ "television"; 冷蔵庫 "refrigerator".

## Restaurants (「ファミレス」「定食屋」「居酒屋」)

`place: "restaurant"` (cafes keep `cafe`). Seating, tableware, dishes and kitchen equipment come from
a shop asset folder (`local:`) if there is one; without it say so and use SimReady tables and chairs.

- Size and look: a family restaurant is 15 × 10 × 2.8 m for about 60 seats (dining ~1.6 m² a seat, the kitchen
  ~30 % of the floor; a small定食屋10 × 8). wood_light floor, paint_white walls, `ceiling: true`, `home` lighting.
- Dining room: fill it — booth units along the window wall and the other dining walls, a grid of 4-seat tables
  in the middle — not a few tables in a big hall. Booth seats (static, back to the wall or back to back in the
  middle) face a 4-seat table each. Aisles 0.9–1.1 m for staff with a serving wagon. The drink bar stands near
  the kitchen; the waiting board and benches by the entrance.
- Kitchen wall: use `room.plan` with the outer walls plus a partition wall, and zones `dining` and `kitchen`
  (kitchen floor `tile_white`). The partition has a pass window (window, sill 0.95, height 0.85, 2 m wide) with
  the deshap counter (デシャップ台, the pass) behind it on the kitchen side (its blocks_window note is expected),
  and a 0.9 m door for staff. Plan coordinates start at the outer wall face, so the inside starts at the wall
  thickness.
- Kitchen: a 2-door fridge and cold table on one wall, gas range + fryer under the exhaust hood, a 2-bowl sink
  and the dishwasher, steel work tables in the middle, a rack for dishes. Kitchen aisles 1.0–1.2 m.
- Izakaya: zataku tables on zabuton (座敷) plus a high counter.
- Small things: with `density` normal the tables get a condiment set, call bell and napkin holder (lived_in adds
  the menu stand and water glasses); kitchen racks get plates. Dishes on a table: `layout.on` the table with
  `offset`, one per seat.

Queries: ボックス席 "booth seat"; 4人掛けテーブル "restaurant table" / "dining table"; 椅子 "restaurant chair";
子ども用椅子 "high chair"; 座卓 "zataku table" / "low table"; 座布団 "zabuton" / "floor cushion"; 呼び出しベル
"call bell"; 調味料 "condiment set"; ドリンクバー "drink bar" / "drink dispenser"; 配膳ワゴン "serving wagon";
デシャップ台 "pass-through counter" / "deshap counter"; 業務用冷蔵庫 "commercial refrigerator"; コールドテーブル
"cold table" / "refrigerated work table"; ガスレンジ "gas range"; フライヤー "deep fryer"; シンク "kitchen sink";
作業台 "work table" (cold tables come up too: pick with view_candidates); 食洗機 "commercial dishwasher"
("dishwasher" alone finds home ones); 排気フード "exhaust hood"; dishes by their English names ("hamburg steak",
"omurice", "curry rice", "ramen bowl").

## Plant factories (「イチゴの植物工場」「水耕栽培の栽培室」)

`place: "plant_factory"`. Racks, plants and floor tape come from a farm asset folder (`local:<dir>`) if there is one; without
it say so and use shelving as a stand-in. A growing room is about 10 × 8 × 3.0 m (a test room 6 × 5): epoxy_gray floor
(green with `color`), white `metal_panel` walls (`{"preset": "metal_panel", "color": [0.9, 0.9, 0.88]}`), `ceiling: true`
(white panels), `office` lighting at `lighting_scale` 0.5 (the racks carry their own LED bars). Racks stand in rows with
`array` (rows, `back_to_back` for double-sided rows), fronts (−Y, the aisle side, where the fruit hangs) facing the
aisle; aisles 0.9 m for people, 1.2 m when a harvest robot drives through. Magnetic guide tape (1 m pieces, `array`
along the aisle) marks a robot's path; no robot is placed. Queries: 栽培棚 "strawberry growing rack" / "growing rack";
空の栽培棚 "empty growing rack"; イチゴの苗 "potted strawberry plant"; 誘導テープ "magnetic guide tape".

## Stocking shelves

Stock shelves (store gondolas, coolers, showcases): one object per product with
`fill` {on: <shelf object id>, levels: [0, 1], rows, units: ["r0c0", ...], span: [0, 0.5]} instead of
`layout`. Shelf boards come from the fixture's geometry; products face like their unit, rows start at the
front edge; `shelf_fit` issues mean the product is too tall for that level. Change with `set_fill`.
Give each product its own levels / units / span so they do not overlap.
Tips from the examples:

- Products must fit the shelf gap and depth (`shelf_fit` otherwise), so big goods (storage cases, soil bags) go
  on pallets or the floor, not on tall racks.
- The endcap promo table's top deck is shallow (0.28 m): use level 1 with small goods.
- Hook racks have no shelf boards: only hung packs go there (below).
- Keep the scene under ~1200 rigid bodies: tiny goods (batteries, eye drops) multiply fast, so use `rows: 1` and
  `units`, or `physics: "front"` (below).

Blister packs (`*_blister`: batteries, bulbs, earphones, tools) belong on hooks: on a pegboard or hook rack
(status `hooks` lists its hook rows and how many are free) use `fill` {on, hang: true, levels: [hook rows],
span, rows (packs per hook)}; hung packs are fixed (no physics). Products taller than the row pitch skip the
row below (stacks hung earlier in the spec keep their hooks). On a shelf they stand only on the card's thin
edge and fall over: there, stock them with `lay_flat: true` (on their backs, front up).
Fill a shelf as deep as it goes: leave `rows` out and set `physics: "front"` — only the front row is a rigid
body, the rows behind are static display stock (same look, no simulation, not counted as bodies; `density` stops
adding at 1500 bodies). `fit_only: true` with `levels` skips the levels the product is too tall for instead of
reporting them. A shop fixture with half its levels or more empty is the usability warning `empty_shelves`
: stock it (or leave it to `density`, which puts up to three products on each empty fixture, one per
level, and fills the levels they do not fit with the others).
