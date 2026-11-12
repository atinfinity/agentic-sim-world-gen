"""Room density: small things added so a room does not look empty (Room.density).

sparse   nothing is added
normal   a few things per host: books and a pen holder on a study desk, a plant beside it, cushions on the
         sofa, a magazine on the table, a photo frame and a vase on the TV stand, a rug in front of the sofa,
         a monitor and a tray on office desks, products on every unstocked shop shelf (blister packs on the
         free hooks of pegboards and hook racks)
lived_in more of them (a mug on the desk, a plant by the wall, a fruit bowl, a candle, ...)

`plan()` is pure: it finds hosts by their head nouns (agentic_sim_world_gen.layout.usability.heads) and returns, per item,
the placements to try in order (ObjectSpec with layout / fill, decor=True). The service searches assets for
them, keeps the first try that adds no problem (overlap, out of the room, unsupported, ...) and drops the
item otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agentic_sim_world_gen.assets.record import AssetRecord
from agentic_sim_world_gen.layout.array import unit_size
from agentic_sim_world_gen.layout.usability import _mods, _words, heads
from agentic_sim_world_gen.scene.spec import STORE_PLACES, Array, ObjectSpec, ResolvedScene

DENSITIES = ("sparse", "normal", "lived_in")
DENSITY_JA = {"sparse": "少なめ", "normal": "ふつう", "lived_in": "生活感あり"}
BASIS = "生活感のため追加"

HOME = ("home", None)
OFFICE = ("office",)
RESTAURANT = ("restaurant",)
SHOPS = ("shop", *STORE_PLACES)

DESKS = frozenset({"desk", "workstation"})
TABLES = frozenset({"table"})
SOFAS = frozenset({"sofa", "couch", "loveseat"})
TV_STANDS = frozenset({"stand", "console", "sideboard", "credenza"})  # "tv stand", "media console"
KITCHEN_TABLES = frozenset({"work", "cold", "prep", "steel", "stainless", "side", "end", "coffee", "serving"})
NOT_TV = frozenset({"coat", "hat", "plant", "music", "umbrella", "night", "bedside"})
BOOKSHELVES = frozenset({"bookshelf", "bookcase", "shelf", "shelve", "shelving"})
FIXTURES = frozenset({"shelf", "shelve", "shelving", "gondola", "rack", "bookcase", "bookshelf", "cooler",
                      "refrigerator", "fridge", "freezer", "showcase", "multideck", "display", "pegboard"})
# shelf stock, tried in order: (rows from the front edge (None: as deep as fits), span of the board width);
# the narrower span keeps the end products off a fixture standing next to it (wide base boards of gondolas in
# a row). Only the front row is a rigid body (Fill.physics "front"): the rows behind are static display stock,
# so a full shelf costs no more simulation than one row (a full-depth fill of every fixture as rigid bodies is
# thousands of them and PhysX stops: 3548 in a konbini).
STOCK_TRIES = ((None, (0.0, 1.0)), (None, (0.08, 0.92)), (2, (0.08, 0.92)), (1, (0.08, 0.92)))
STOCK_PHYSICS = "front"
BODY_BUDGET = 1500  # decor stops adding once the scene simulates this many objects (array units + products)
# faces the scene draws, static display stock included: 1600-9100 scanned toy houses (0.86 M faces each)
# ran Isaac Sim 6.1 out of memory (31 GB), 7945 shop products (median 2500 faces, ~24 M) are fine
DRAW_BUDGET = 50_000_000
STOCK_FACES_MAX = 100_000  # a product stocked by the hundred has at most this many faces; heavier ones are skipped
UNKNOWN_FACES = 5_000  # assumed when the catalog does not say (Isaac and Poly Haven records)
COLD = frozenset({"cooler", "refrigerator", "fridge", "freezer", "multideck"})
MIX = 3  # products per fixture at most: level L holds product L mod k

Product = tuple[str, tuple[str, ...]]  # (query, alt_queries)
# what a shop's shelves hold, by place (cold cases: drinks); several products, one per shelf level in turn
PRODUCTS: dict[str, tuple[Product, ...]] = {
    "convenience_store": (("potato chips bag", ("snack bag", "chips bag")), ("cup noodle", ("instant noodle cup",)),
                          ("candy bag", ("gummy candy", "chocolate")), ("cracker box", ("cookie box", "biscuit box"))),
    "supermarket": (("canned food", ("food can", "tuna can")), ("curry roux box", ("retort curry box", "cereal box")),
                    ("instant ramen pack", ("instant noodles",)), ("spaghetti pack", ("pasta", "packaged food"))),
    "drugstore": (("shampoo bottle", ("shampoo pump", "conditioner bottle")), ("lotion bottle", ("cosmetic bottle",)),
                  ("toothpaste box", ("toothpaste",)), ("body soap refill", ("hand soap", "detergent bottle"))),
    "home_center": (("paint can", ("paint bucket",)), ("spray can", ("aerosol can",)),
                    ("trash bags", ("garbage bags",)), ("laundry detergent", ("detergent bottle",))),
    "hundred_yen_shop": (("plastic container", ("storage case", "storage box")), ("kitchen sponge", ("sponge",)),
                         ("food wrap box", ("plastic wrap",)), ("rubber gloves", ("kitchen gloves",))),
    "bookstore": (("book", ("books", "hardcover book")), ("paperback book", ("bunko paperback", "novel")),
                  ("comic book", ("manga", "comic paperback"))),
    # a plain "product box" was a case of soft drinks
    "electronics_store": (("headphones box", ("headphones",)), ("usb charger box", ("charger box",)),
                          ("led bulb box", ("light bulb box",))),
    "apparel_store": (("folded t-shirt", ("folded clothes", "folded shirt")), ("folded jeans", ("jeans stack",)),
                      ("socks pack", ("socks",))),
    "bakery": (("bread", ("bread roll",)), ("croissant", ("pastry",)), ("melon pan", ("sweet bun",)),
               ("curry bread", ("anpan",))),
    "variety_store": (("coffee mug", ("mug",)), ("ceramic bowl", ("bowl",)), ("small vase", ("vase",))),
    "florist": (("potted flower", ("flower pot", "potted plant")), ("flower bouquet", ("bouquet", "cut flowers")),
                ("small potted plant", ("succulent pot", "house plant"))),
    "greengrocer": (("apple", ("red apple",)), ("cabbage", ("lettuce",)), ("tomato", ("vegetable",)),
                    ("orange", ("citrus fruit", "fruit"))),
    "toy_store": (("toy car", ("toy vehicle",)), ("building blocks", ("toy blocks",)), ("stuffed animal", ("plush toy", "teddy bear")),
                  ("toy box", ("boxed toy",))),
    "pet_shop": (("pet food bag", ("dog food bag", "cat food bag")), ("cat food pouch", ("cat food",)),
                 ("pet food can", ("cat food can",)), ("pet toy", ("dog toy",))),
    "garden_center": (("seedling tray", ("plant tray",)), ("potted plant", ("plant pot",)), ("soil bag", ("potting soil bag",)), ("watering can", ()),
                      ("flower pot", ("planter",))),
    "shoe_store": (("sneaker", ("sneakers", "shoe")), ("shoe box", ("shoebox",)), ("leather shoe", ("dress shoe", "shoe"))),
    "optician": (("eyeglasses", ("glasses", "spectacles")), ("glasses case", ("eyeglass case",))),
    "liquor_store": (("sake bottle", ("japanese sake bottle",)), ("wine bottle", ("red wine bottle",)),
                     ("whiskey bottle", ("whisky bottle",)), ("beer can", ("beer",)), ("beer bottle", ("bottled beer",))),
    # bicycles, beds and wardrobes stand on the floor as objects
    "bicycle_shop": (("bicycle helmet", ("bike helmet", "cycling helmet")), ("bicycle light", ("bike light",)),
                     ("bicycle lock", ("bike lock",))),
    "wagashi_shop": (("wagashi gift box", ("confectionery gift box", "sweets gift box")), ("yokan", ("yokan bar",)),
                     ("monaka", ("monaka pack",))),
    "record_store": (("lp record", ("vinyl record", "record album")), ("cd case", ("compact disc",))),
    "sporting_goods_store": (("soccer ball", ("football",)), ("basketball", ("sports ball",)),
                             ("tennis racket", ("racket",)), ("sneaker", ("running shoe",))),
    "cosmetics_store": (("lotion bottle", ("cosmetic bottle",)), ("lipstick", ("lip gloss",)),
                        ("sunscreen tube", ("cream tube",)), ("nail polish", ("nail polish bottle",))),
    "furniture_store": (("throw cushion", ("cushion",)), ("table lamp", ("desk lamp",)), ("picture frame", ("photo frame",)),
                        ("flower vase", ("vase",))),
    "asian_grocery": (("asian noodle bag", ("instant noodles", "noodle pack")), ("jasmine rice bag", ("rice bag",)),
                      ("chili oil jar", ("chili crisp",)), ("oyster sauce bottle", ("sauce bottle",))),
}
# what a cold case holds in these shops instead of soft drinks (DRINK)
COLD_PRODUCTS: dict[str, tuple[Product, ...]] = {
    "liquor_store": (("beer can", ("beer",)), ("chuhai can", ("drink can",)), ("sake bottle 300ml", ("small sake bottle",))),
    "greengrocer": (("cut fruit cup", ("fruit cup",)), ("lettuce", ("vegetable",)), ("strawberry pack", ("strawberry",))),
    "florist": (("flower bouquet", ("bouquet", "cut flowers")),),
}
# what hangs on a shop's hooks (blister packs), by place
HANG: dict[str, tuple[Product, ...]] = {
    "home_center": (("screwdriver blister pack", ("screwdriver blister",)), ("pliers blister pack", ("pliers blister",)),
                    ("tape measure blister pack", ("tape measure blister",)), ("screw blister pack", ("wood screw blister",))),
    "hundred_yen_shop": (("usb cable blister pack", ("usb cable blister",)), ("earphones blister pack", ("earphones blister",)),
                         ("aa battery blister pack", ("battery blister",)), ("phone case blister pack", ("phone case blister",))),
    "electronics_store": (("usb cable blister pack", ("usb cable blister",)), ("earphones blister pack", ("earphones blister",)),
                          ("mobile battery blister pack", ("power bank blister",)), ("phone case blister pack", ("phone case blister",)),
                          ("led bulb blister pack", ("led bulb blister",))),
    "drugstore": (("toothbrush blister pack", ("toothbrush blister",)), ("aa battery blister pack", ("battery blister",))),
    # what the shop sells on hooks, not batteries; none for shops that hang nothing
    "toy_store": (("toy blister pack", ("toy car blister",)), ("aa battery blister pack", ("battery blister",))),
    "pet_shop": (("pet toy blister pack", ("dog toy blister",)), ("pet treat bag", ("dog treat bag",))),
    "garden_center": (("seed packet", ("flower seed packet",)), ("garden gloves blister pack", ("garden gloves",))),
    "optician": (("glasses cleaner blister pack", ("lens cloth",)),),
    "shoe_store": (("shoe insole pack", ("insole",)), ("shoelace blister pack", ("shoelaces",))),
    "florist": (), "greengrocer": (), "liquor_store": (),
}
DEFAULT_HANG: tuple[Product, ...] = (("aa battery blister pack", ("battery blister",)),
                                     ("usb cable blister pack", ("usb cable blister",)),
                                     ("toothbrush blister pack", ("toothbrush blister",)))
# the everyday packs of a konbini / supermarket; a shop of another or no kind hangs only its own goods (# USB cables and toothbrushes on a pet shop's pegboard)
HANG["convenience_store"] = HANG["supermarket"] = DEFAULT_HANG


def _kw(*ws: str) -> frozenset[str]:
    return frozenset(w for x in ws for w in _words(x))


# A shop's stock must name one of these words: a plain "toy" match is a marble and "blister pack" an AA battery,
# not a pet toy; "food can" is canned beans; "toy box" a six-pack of beer tagged "box". Checked on
# the asset's class / tags / caption / name. Shops that sell all sorts (konbini, variety store) have none.
KIND_WORDS: dict[str, frozenset[str]] = {
    "pet_shop": _kw("pet", "dog", "cat", "puppy", "kitten", "bird", "fish", "hamster", "rabbit"),
    "toy_store": _kw("toy", "toys", "plush", "stuffed", "teddy", "doll", "puzzle", "battery"),
    "shoe_store": _kw("shoe", "shoes", "sneaker", "shoebox", "boot", "sandal", "slipper", "insole", "shoelace"),
    "optician": _kw("eyeglasses", "glasses", "eyeglass", "spectacles", "eyewear", "sunglasses", "lens"),
    "florist": _kw("flower", "flowers", "bouquet", "plant", "succulent", "rose", "tulip", "carnation", "lily"),
    "garden_center": _kw("plant", "planter", "soil", "seed", "garden", "gardening", "watering", "flower", "succulent"),
    "liquor_store": _kw("sake", "wine", "whiskey", "whisky", "beer", "chuhai", "shochu", "liquor", "alcohol",
                        "champagne", "vodka", "bourbon"),
    "greengrocer": _kw("fruit", "vegetable", "produce", "apple", "cabbage", "lettuce", "tomato", "orange", "strawberry",
                       "banana", "citrus", "carrot", "potato", "onion", "cucumber", "grape", "lemon", "melon", "peach",
                       "pear"),
    "bookstore": _kw("book", "books", "manga", "comic", "novel", "paperback", "hardcover", "magazine", "bunko"),
    # no bare "ball" (pie weights, marbles) or "oil" (motor oil)
    "bicycle_shop": _kw("bicycle", "bike", "cycle", "cycling", "helmet", "tire", "tyre", "pump", "saddle"),
    "wagashi_shop": _kw("wagashi", "dango", "manju", "monaka", "yokan", "daifuku", "mochi", "dorayaki", "senbei",
                        "confectionery"),
    "record_store": _kw("record", "records", "vinyl", "lp", "album", "cd", "disc", "turntable"),
    "sporting_goods_store": _kw("sports", "sport", "soccer", "football", "basketball", "volleyball", "baseball", "tennis",
                                "racket", "racquet", "badminton", "sneaker", "sneakers", "athletic", "jersey"),
    "cosmetics_store": _kw("cosmetic", "cosmetics", "makeup", "lotion", "lipstick", "cream", "skincare", "perfume",
                           "nail", "foundation", "sunscreen", "beauty", "tester", "serum"),
    "furniture_store": _kw("furniture", "cushion", "pillow", "lamp", "frame", "vase", "clock", "rug", "bed",
                           "nightstand", "wardrobe", "chair", "table", "sofa", "interior"),
    "asian_grocery": _kw("asian", "noodle", "noodles", "ramen", "rice", "sauce", "chili", "oyster", "soy", "curry",
                         "spice", "seasoning", "condiment", "kimchi", "tea", "sesame"),
    "electronics_store": _kw("electronics", "headphones", "headphone", "earphones", "charger", "usb", "bulb", "led",
                             "battery", "cable", "laptop", "phone", "smartphone", "tablet", "camera", "speaker", "audio"),
}


# words of Room.goods that say nothing of the kind ("gift box" is a wagashi box, not any box)
GENERIC = _kw("box", "bag", "pack", "case", "bottle", "can", "jar", "set", "pair", "packet", "item", "product",
              "goods", "store", "shop", "small", "large", "big", "mini")


def goods_words(room: Any) -> frozenset[str]:
    """The words a product stocked from Room.goods must name."""
    return frozenset(w for q in room.goods for w in _words(q) if w not in GENERIC) | _kw(*room.goods_words)


def stock_words(scene: ResolvedScene) -> frozenset[str]:
    """The words this shop's stock must name: Room.goods', else its kind's KIND_WORDS (empty: anything)."""
    room = scene.spec.room
    if room.goods:
        return goods_words(room)
    return KIND_WORDS.get(shop_kind(scene) or "", frozenset())


def fits_words(need: frozenset[str], a: AssetRecord) -> bool:
    """The asset names one of these words in its class / tags / caption / name (always, for none)."""
    if not need:
        return True
    text = " ".join([a.semantic_class or "", *a.tags, a.caption or "", a.name.replace("_", " ")])
    return bool(need & set(_words(text)))


def fits_goods(query: str, room: Any, a: AssetRecord) -> bool:
    """A product stocked from this Room.goods query names its own words: the head noun and the qualifier
    ("ball" and "soccer" of "soccer ball") or a goods_words word (the kind). One word of another query or the head
    noun alone matches pie weights and marbles tagged "ball", the qualifier alone exit signs for "running shoes"."""
    ws = [w for w in _words(query) if w not in GENERIC]
    if not ws:
        return True
    text = " ".join([a.semantic_class or "", *a.tags, a.caption or "", a.name.replace("_", " ")])
    have, kind = set(_words(text)), _kw(*room.goods_words)
    if len(ws) == 1:
        return bool(({ws[0]} | kind) & have)
    return ws[-1] in have and bool((set(ws[:-1]) | kind) & have)


def fits_kind(kind: str | None, a: AssetRecord) -> bool:
    """The asset can be this shop's stock (always, for a shop with no KIND_WORDS)."""
    need = KIND_WORDS.get(kind or "")
    if not need:
        return True
    text = " ".join([a.semantic_class or "", *a.tags, a.caption or "", a.name.replace("_", " ")])
    return bool(need & set(_words(text)))


HANG_TRIES = (None, 1)  # packs per hook: as many as fit, one
# a shop of no particular kind ("shop" or no place) gets products only where the fixture says what it holds:
# a generic "product box" can be a case of soft drinks in a variety store
# Short, stable drinks: tall SimReady bottles (cap as its own body) tip over and fall off cooler shelves.
DRINK: tuple[Product, ...] = (("pet bottle drink 600ml", ("green tea pet bottle",)),
                              ("black tea pet bottle 500ml", ("tea pet bottle",)),
                              ("sports drink pet bottle 500ml", ("soda pet bottle",)), ("drink can", ("coffee can",)))


@dataclass(frozen=True)
class Decor:
    key: str  # id stem
    label: str  # Japanese
    query: str
    alt: tuple[str, ...]
    hosts: frozenset[str]  # head nouns of the host (or of what makes the room need it, for "wall")
    how: str  # on | fill | next_to | in_front_of | wall
    level: str = "normal"
    places: tuple[str | None, ...] = HOME
    # on: (right, front) as fractions of the host's (width, depth) seen from its front, tried in order
    spots: tuple[tuple[float, float], ...] = ((0.0, 0.0),)
    on_height: float | None = None  # on: fraction of the host's height (sofa seat), default its top
    count: int = 1  # this many of it (two cushions), each at the next free spot
    max_size: tuple[float, float, float] | None = None
    min_size: tuple[float, float, float] | None = None  # keeps out look-alikes (an "air cushion" mask)
    no_mods: frozenset[str] = frozenset()  # host modifiers that rule it out ("coffee" for a work table)

    def nouns(self) -> set[str]:
        return {_words(q)[-1] for q in (self.query, *self.alt) if _words(q)}


TABLE: tuple[Decor, ...] = (
    # study
    Decor("books", "本", "stack of books", ("books", "book"), DESKS, "on",
          spots=((0.35, -0.25), (-0.35, -0.25), (0.3, 0.1)), max_size=(0.45, 0.35, 0.35)),
    Decor("pen_holder", "ペン立て", "pen holder", ("pencil cup", "pen cup", "desk organizer"), DESKS, "on",
          spots=((-0.35, -0.25), (0.35, -0.25), (-0.3, 0.1)), max_size=(0.2, 0.2, 0.25)),
    Decor("plant", "観葉植物", "house plant", ("indoor plant", "potted plant"), DESKS, "next_to",
          max_size=(0.7, 0.7, 1.4), min_size=(0.15, 0.15, 0.35)),
    Decor("shelf_books", "本（棚）", "book", ("books", "hardcover book"), BOOKSHELVES, "fill",
          places=(*HOME, *OFFICE)),
    # living room
    Decor("cushion", "クッション", "throw pillow", ("cushion", "sofa pillow"), SOFAS, "on",
          spots=((-0.3, 0.05), (0.3, 0.05), (0.0, 0.05)), on_height=0.55, count=2, max_size=(0.6, 0.6, 0.5),
          min_size=(0.3, 0.08, 0.2)),
    Decor("magazine", "雑誌", "magazine", ("magazines", "book"), TABLES, "on",
          spots=((0.2, 0.0), (-0.2, 0.0), (0.0, 0.2)), max_size=(0.45, 0.35, 0.1)),
    Decor("frame", "写真立て", "picture frame", ("photo frame",), TV_STANDS, "on",
          spots=((0.38, 0.0), (-0.38, 0.0)), max_size=(0.35, 0.2, 0.4), no_mods=NOT_TV),
    Decor("vase", "花瓶", "flower vase", ("vase",), TV_STANDS, "on",
          spots=((-0.38, 0.0), (0.38, 0.0)), max_size=(0.3, 0.3, 0.45), no_mods=NOT_TV),
    Decor("rug", "ラグ", "rug", ("area rug", "carpet"), SOFAS, "in_front_of", max_size=(3.0, 2.5, 0.05),
          min_size=(1.0, 0.6, 0.0)),
    # office
    Decor("monitor", "モニター", "computer monitor", ("monitor", "display screen"), DESKS | TABLES, "on",
          places=OFFICE, spots=((0.0, -0.25), (0.0, 0.0)), max_size=(0.8, 0.35, 0.65), no_mods=frozenset({"coffee", "side", "end"})),
    Decor("tray", "書類トレー", "document tray", ("paper tray", "file tray", "letter tray"), DESKS, "on",
          places=OFFICE, spots=((0.38, -0.2), (-0.38, -0.2), (0.35, 0.1)), max_size=(0.45, 0.4, 0.25)),
    # lived in
    Decor("mug", "マグカップ", "coffee mug", ("mug", "cup"), DESKS, "on", level="lived_in",
          places=(*HOME, *OFFICE), spots=((0.2, 0.15), (-0.2, 0.15), (0.4, 0.2)), max_size=(0.15, 0.15, 0.15)),
    Decor("wall_plant", "観葉植物（壁際）", "house plant", ("indoor plant", "potted plant"),
          SOFAS | DESKS, "wall", level="lived_in", places=(*HOME, *OFFICE), max_size=(0.8, 0.8, 1.8),
          min_size=(0.15, 0.15, 0.35)),
    Decor("fruit_bowl", "果物かご", "fruit bowl", ("fruit basket",), TABLES, "on", level="lived_in",
          spots=((-0.2, 0.0), (0.2, 0.0), (0.0, -0.2)), max_size=(0.4, 0.4, 0.2), no_mods=frozenset({"side", "end", "work"})),
    Decor("candle", "キャンドル", "candle", (), TV_STANDS, "on", level="lived_in",
          spots=((0.2, 0.0), (-0.2, 0.0)), max_size=(0.2, 0.2, 0.35), no_mods=NOT_TV),
    # beside the magazine and the fruit bowl a low table had no room left at two spots
    Decor("remote", "リモコン", "remote control", ("tv remote",), TABLES, "on", level="lived_in",
          spots=((-0.25, 0.1), (0.25, 0.1), (0.0, 0.25), (0.35, -0.25), (-0.35, -0.25)), max_size=(0.25, 0.1, 0.06)),
    # restaurants: the tabletop set on every dining table (not on kitchen work tables or cold tables), plates on
    # kitchen racks
    Decor("condiments", "調味料セット", "condiment set", ("soy sauce dispenser", "salt and pepper shaker"), TABLES,
          "on", places=RESTAURANT, spots=((0.0, 0.32), (0.3, 0.3), (-0.3, 0.3)), max_size=(0.3, 0.2, 0.25),
          no_mods=KITCHEN_TABLES),
    Decor("call_bell", "呼び出しボタン", "call bell", ("call button", "table call button"), TABLES, "on",
          places=RESTAURANT, spots=((0.38, 0.32), (-0.38, 0.32), (0.3, 0.0)), max_size=(0.15, 0.15, 0.1),
          no_mods=KITCHEN_TABLES),
    Decor("napkins", "紙ナプキン立て", "napkin holder", ("napkin dispenser",), TABLES, "on", places=RESTAURANT,
          spots=((-0.38, 0.32), (0.38, 0.32), (-0.3, 0.0)), max_size=(0.2, 0.15, 0.2), no_mods=KITCHEN_TABLES),
    Decor("menu", "卓上メニュー", "menu stand", ("table menu", "menu holder"), TABLES, "on", places=RESTAURANT,
          level="lived_in", spots=((-0.2, 0.32), (0.2, 0.32)), max_size=(0.35, 0.25, 0.35), no_mods=KITCHEN_TABLES),
    Decor("water", "お冷や", "water glass", ("tumbler glass",), TABLES, "on", places=RESTAURANT, level="lived_in",
          spots=((-0.25, -0.25), (0.25, -0.25)), count=2, max_size=(0.1, 0.1, 0.16), no_mods=KITCHEN_TABLES),
    Decor("dishes", "食器（棚）", "dinner plate", ("plate", "rice bowl", "bowl"), frozenset({"rack", "shelf", "shelving"}),
          "fill", places=RESTAURANT),
    # electronics store: an open laptop on a plain display table (one with phones and tablets built in is set)
    Decor("laptop", "ノートパソコン", "open laptop", ("laptop",), TABLES, "on", places=("electronics_store",),
          spots=((0.0, 0.0), (-0.25, 0.0), (0.25, 0.0)), max_size=(0.45, 0.35, 0.3),
          no_mods=frozenset({"device", "phone", "smartphone", "tablet", "work", "side", "end", "coffee"})),
    # shops: every unstocked fixture gets products (query by place / cold case)
    Decor("stock", "陳列", "", (), FIXTURES, "fill", places=SHOPS),
)


STOCK_HANG = Decor("hang", "吊り下げ陳列", "", (), FIXTURES, "fill", places=SHOPS)


@dataclass
class Item:
    """One thing to add: the placements to try in order (the first without new problems wins)."""

    decor: Decor
    host: str | None
    label: str
    tries: list[ObjectSpec] = field(default_factory=list)
    # shelf stock: fill the host's levels that no product took (a product found nothing / did not fit there);
    # the service sets the levels, the tries are the host's products in order
    rest: bool = False


def _rank(density: str | None) -> int:
    return DENSITIES.index(density) if density in DENSITIES else 0


def _host_size(o: ObjectSpec, a: AssetRecord) -> tuple[float, float]:
    """(width seen from the front, depth) of one unit."""
    return unit_size(o, a)


def _has(scene: ResolvedScene, asset: dict[str, AssetRecord], nouns: set[str], on: str | None) -> bool:
    """An object of this kind is already there (on this host, or anywhere for a room-level item)."""
    for o in scene.spec.objects:
        if o.decor:
            continue
        rest = o.fill.on if o.fill else o.layout.on if o.layout else None
        if on is not None and rest != on:
            continue
        if heads(o, asset.get(o.id)) & nouns:
            return True
    return False


# what a fixture holds, from the words of its query / id (first match wins; before the cold-case drinks)
BY_FIXTURE: tuple[tuple[frozenset[str], tuple[Product, ...]], ...] = (
    (frozenset({"freezer", "frozen", "ice"}), (("ice cream cup", ("ice cream",)), ("frozen gyoza bag", ("frozen food bag",)),
                                              ("ice bar bag", ("ice pop",)))),
    (frozenset({"deli", "bento", "lunch"}), (("bento", ("lunch box",)), ("onigiri", ("rice ball",)),
                                            ("sandwich pack", ("sandwich",)), ("salad cup", ("salad",)))),
    (frozenset({"bakery", "bread"}), (("sliced bread", ("bread",)), ("melon pan", ("sweet bun",)),
                                      ("croissant", ("pastry",)))),
    (frozenset({"magazine", "newspaper"}), (("magazine", ("magazines",)), ("weekly magazine", ("newspaper",)))),
    (frozenset({"noodle", "ramen", "instant"}), (("cup noodle", ("instant noodle cup",)), ("instant ramen pack", ("instant ramen",)),
                                                ("cup yakisoba", ("cup noodle",)))),
    (frozenset({"snack", "candy", "sweet"}), (("potato chips bag", ("snack bag",)), ("candy bag", ("gummy candy",)),
                                             ("chocolate box", ("chocolate",)))),
    (frozenset({"daily", "household", "sundries"}), (("box tissue", ("tissue box",)), ("toilet paper", ("toilet roll",)),
                                                     ("dish soap", ("detergent",)))),
)


# a variety store's (雑貨店) goods by the fixture's words, before BY_FIXTURE
ZAKKA_BY_FIXTURE: tuple[tuple[frozenset[str], tuple[Product, ...]], ...] = (
    (frozenset({"towel", "textile", "linen", "fabric"}), (("folded towel", ("towel", "hand towel")),)),
    (frozenset({"stationery", "notebook", "pen", "card", "letter"}),
     (("notebook", ("stationery",)), ("pen", ("pencil",)), ("pencil case", ("pen case",)))),
    (frozenset({"plant", "garden", "pot", "flower", "green"}), (("flower pot", ("plant pot", "potted plant", "planter")),)),
    (frozenset({"candle", "aroma", "fragrance", "scent"}), (("candle", ("scented candle", "candle jar")),)),
    (frozenset({"kitchen", "tableware", "dish", "cup", "mug", "ceramic"}),
     (("coffee mug", ("mug",)), ("ceramic bowl", ("bowl",)), ("plate", ("dish",)))),
    (frozenset({"basket", "storage", "box"}), (("storage basket", ("wicker basket", "storage box")),)),
)


# a shop kind's goods by the fixture's words, before BY_FIXTURE (a wine rack holds wine, not cans)
KIND_BY_FIXTURE: dict[str, tuple[tuple[frozenset[str], tuple[Product, ...]], ...]] = {
    "variety_store": ZAKKA_BY_FIXTURE,
    "liquor_store": (
        (frozenset({"wine", "crate"}), (("wine bottle", ("red wine bottle",)), ("sake bottle", ("japanese sake bottle",)),
                                        ("beer bottle", ("bottled beer",)))),
    ),
    "garden_center": (
        (frozenset({"bench", "nursery", "potting", "seedling"}), (("seedling tray", ("plant tray",)),
                                                                  ("potted plant", ("plant pot",)))),
    ),
    # the fresh sweets in the glass case, gift boxes and yokan on the shelves
    "wagashi_shop": (
        (frozenset({"showcase", "case"}), (("dango", ("dango pack", "mitarashi dango")), ("manju", ("manju pack",)),
                                           ("monaka", ("monaka pack",)))),
    ),
    "record_store": ((frozenset({"bin", "crate"}), (("lp record", ("vinyl record",)),)),),
}
# what else a shop kind stocks besides FIXTURES (a liquor store's wine crate, a garden center's nursery bench;
# a bench in a shoe store is a seat)
KIND_FIXTURES: dict[str, frozenset[str]] = {
    "liquor_store": frozenset({"crate"}),
    "garden_center": frozenset({"bench"}),
    # a record store's bins (エサ箱), a cosmetics shop's tester islands and tables
    # (a sports shop's ball cart has no shelf levels, so it is placed as a fixture and not stocked)
    "record_store": frozenset({"bin", "crate"}),
    "cosmetics_store": frozenset({"island", "tester", "table"}),
}


def fixture_heads(place: str | None) -> frozenset[str]:
    """The head nouns of what a shop of this kind stocks."""
    return FIXTURES | KIND_FIXTURES.get(place or "", frozenset())


def _products(place: str | None, host: ObjectSpec, host_heads: set[str]) -> tuple[Product, ...]:
    """The products a fixture holds (first: the main one); none when nothing says (a shop of no particular kind)."""
    words = set(_words(host.query)) | set(_words(host.id.replace("_", " ")))
    table = (*KIND_BY_FIXTURE.get(place or "", ()), *BY_FIXTURE)
    for keys, products in table:
        if words & keys:
            return products
    if host_heads & COLD:
        return COLD_PRODUCTS.get(place or "", DRINK)
    return PRODUCTS.get(place or "", ())


def _stock(d: "Decor", h: ObjectSpec, a: AssetRecord, place: str | None, hh: set[str], n_host: int,
           hung: set[str] = frozenset(), goods: tuple[Product, ...] = ()) -> list[Item]:
    """Shelf stock for fixture `h`: k products, level L holding product (L + n_host) mod k (neighboring fixtures
    start with different products), then one item for the levels left empty."""
    from agentic_sim_world_gen.layout.fill import levels as board_levels

    host_label = h.label or h.id
    products = list(goods or _products(place, h, hh))
    n_lv = len(board_levels(a.shelves or []))
    out = [] if h.id in hung else _hang_stock(h, a, place, n_host)
    if n_lv == 0 or not products:
        return out  # a hook rack: no boards; or nothing says what the boards hold
    k = max(1, min(MIX, len(products), n_lv))
    start = n_host % len(products)
    picked = [products[(start + i) % len(products)] for i in range(k)]

    def spec(i: int, q: str, alt: tuple[str, ...], lv: list[int] | None, rows: int | None, span) -> ObjectSpec:
        fill: dict[str, Any] = {"on": h.id, "rows": rows, "span": span, "physics": STOCK_PHYSICS}
        if lv is not None:
            fill |= {"levels": lv, "fit_only": True}
        return ObjectSpec(id=_id(d.key, h.id, i), query=q, alt_queries=list(alt),
                          label=f"{d.label}（{host_label}）" + (f" {i + 1}" if i else ""), fill=fill, decor=True)

    for i, (q, alt) in enumerate(picked):
        lv = None if k == 1 else [li for li in range(n_lv) if li % k == i]
        tries = [spec(i, q, alt, lv, r, sp) for r, sp in STOCK_TRIES]
        out.append(Item(d, h.id, tries[0].label, tries))
    if k > 1:
        tries = [spec(k + j, q, alt, [], r, sp) for j, (q, alt) in enumerate(picked) for r, sp in STOCK_TRIES]
        out.append(Item(d, h.id, f"{d.label}（{host_label}の空いた段）", tries, rest=True))
    return out


def _hang_stock(h: ObjectSpec, a: AssetRecord, place: str | None, n_host: int) -> list[Item]:
    """Blister packs on the free hooks of fixture `h`: like _stock, hook row R holds product (R + n_host)
    mod k, then one item for the rows left empty."""
    from agentic_sim_world_gen.layout.fill import hook_rows

    rows = [i for i, r in enumerate(hook_rows(a.hooks or [])) if any(not k["busy"] for k in r)]
    if not rows:
        return []
    host_label = h.label or h.id
    products = list(HANG.get(place or "", ()))
    if not products:
        return []  # nothing hangs in this kind of shop
    k = max(1, min(MIX, len(products), len(rows)))
    start = n_host % len(products)
    picked = [products[(start + i) % len(products)] for i in range(k)]

    def spec(i: int, q: str, alt: tuple[str, ...], lv: list[int] | None, n: int | None) -> ObjectSpec:
        fill: dict[str, Any] = {"on": h.id, "hang": True, "rows": n}
        if lv is not None:
            fill |= {"levels": lv, "fit_only": True}
        return ObjectSpec(id=_id("hang", h.id, i), query=q, alt_queries=list(alt),
                          label=f"吊り下げ陳列（{host_label}）" + (f" {i + 1}" if i else ""), fill=fill, decor=True)

    out = []
    for i, (q, alt) in enumerate(picked):
        lv = None if k == 1 else [r for j, r in enumerate(rows) if j % k == i]
        tries = [spec(i, q, alt, lv, n) for n in HANG_TRIES]
        out.append(Item(STOCK_HANG, h.id, tries[0].label, tries))
    if k > 1:
        tries = [spec(k + j, q, alt, [], n) for j, (q, alt) in enumerate(picked) for n in HANG_TRIES]
        out.append(Item(STOCK_HANG, h.id, f"吊り下げ陳列（{host_label}の空いた段）", tries, rest=True))
    return out


def shop_kind(scene: ResolvedScene) -> str | None:
    """The kind of shop: Room.place, or for a plain "shop" the kind the scene's name or request items say
    ("pet_shop", 「ペットショップの…」), or None."""
    place = scene.spec.room.place
    if place in STORE_PLACES:
        return place
    if place != "shop":
        return None
    from agentic_sim_world_gen.scene.questions import guess_place

    texts = [scene.spec.name.replace("_", " "), *(r.text for r in scene.spec.requirements)]
    for t in texts:
        if (g := guess_place(t)) in STORE_PLACES:
            return g
    return None


def unstocked(scene: ResolvedScene, items: list[Item]) -> list[ObjectSpec]:
    """The shop fixtures with boards or free hooks that the plan gave nothing to hold (a shop of no kind whose
    fixtures do not say what they hold)."""
    if scene.spec.room.place not in SHOPS:
        return []
    from agentic_sim_world_gen.layout.fill import hook_rows

    kind = shop_kind(scene)
    asset = {p.object_id: p.asset for p in scene.placements if p.asset is not None}
    held = {o.fill.on for o in scene.spec.objects if o.fill is not None} | {i.host for i in items}
    out = []
    for o in scene.spec.objects:
        a = asset.get(o.id)
        if o.decor or o.fill is not None or a is None or o.id in held or not heads(o, a) & fixture_heads(kind):
            continue
        if a.shelves or any(not k["busy"] for r in hook_rows(a.hooks or []) for k in r):
            out.append(o)
    return out


def plan(scene: ResolvedScene, density: str | None) -> list[Item]:
    """What to add for this density (nothing for sparse / None). The scene must be solved (hosts placed)."""
    rank = _rank(density)
    if rank == 0 or scene.spec.room.outdoor:
        return []
    place = shop_kind(scene) or scene.spec.room.place
    goods: tuple[Product, ...] = tuple((q, ()) for q in scene.spec.room.goods)  # the shop's own products
    asset = {p.object_id: p.asset for p in scene.placements if p.asset is not None}
    hosts = [o for o in scene.spec.objects if not o.decor and o.fill is None and o.opening is None and o.id in asset]
    stocked = {o.fill.on for o in scene.spec.objects if o.fill is not None and not o.fill.hang}
    hung = {o.fill.on for o in scene.spec.objects if o.fill is not None and o.fill.hang}
    out: list[Item] = []
    done_room: set[str] = set()  # room-level items are added once
    n_stock = 0  # fixtures stocked so far (each starts with a different product)
    for d in TABLE:
        if _rank(d.level) > rank or place not in d.places:
            continue
        for h in hosts:
            a = asset[h.id]
            hh = heads(h, a)
            hosts_of = fixture_heads(place) if d.key == "stock" else d.hosts
            if not hh & hosts_of or _mods(h) & d.no_mods:
                continue
            host_label = h.label or h.id
            if d.how == "fill":
                if not d.query and h.id in stocked and h.id not in hung and place in SHOPS:
                    hung.add(h.id)  # once per fixture
                    out.extend(_hang_stock(h, a, place, n_stock))  # boards stocked, hooks empty
                    continue
                if h.id in stocked or (d.key == "shelf_books" and hh & {"shelf", "shelve", "shelving"} and
                                       not ({"book"} & set(_words(h.query)))):
                    continue  # already stocked; a plain shelf in a home holds books only when it is a bookshelf
                if d.query:  # books on a home bookshelf: one product
                    tries = [ObjectSpec(id=_id(d.key, h.id), query=d.query, alt_queries=list(d.alt),
                                        label=f"{d.label}（{host_label}）",
                                        fill={"on": h.id, "rows": r, "span": sp, "physics": STOCK_PHYSICS}, decor=True)
                             for r, sp in STOCK_TRIES]
                    out.append(Item(d, h.id, tries[0].label, tries))
                else:
                    out.extend(_stock(d, h, a, place, hh, n_stock, hung, goods))
                    n_stock += 1
                continue
            if d.how == "wall":
                if d.key in done_room or _has(scene, asset, d.nouns(), None):
                    continue
                done_room.add(d.key)
                o = _spec(d, None, d.label, layout={"against_wall": "any"})
                out.append(Item(d, None, d.label, [o]))
                continue
            if _has(scene, asset, d.nouns(), h.id if d.how == "on" else None):
                continue
            if d.how in ("next_to", "in_front_of"):
                if d.key in done_room:
                    continue
                done_room.add(d.key)
                if d.how == "next_to":
                    tries = [{"next_to": h.id, "side": "left"}, {"next_to": h.id, "side": "right"},
                             {"against_wall": "any"}]
                else:
                    tries = [{"in_front_of": h.id, "gap": 0.05}, {"in_front_of": h.id, "gap": 0.3}]
                specs = [_spec(d, h.id, d.label, layout=t) for t in tries]
                out.append(Item(d, h.id, f"{d.label}（{host_label}の{'横' if d.how == 'next_to' else '前'}）", specs))
                continue
            # on: one item per copy, each tries every spot (the service skips spots already taken)
            w, dep = _host_size(h, a)
            alt_rows = h.array is not None and (h.array.alternate or h.array.back_to_back)
            for k in range(d.count):
                specs = []
                for r, f in d.spots:
                    if alt_rows:
                        f = 0.0  # rows facing both ways: "back" flips per row, keep to the middle
                    lay: dict[str, Any] = {"on": h.id, "offset": (round(r * w, 3), round(f * dep, 3))}
                    if d.on_height is not None:
                        lay["on_height"] = round(d.on_height * a.size_m[2], 3)
                    specs.append(_spec(d, h.id, f"{d.label}（{host_label}）", layout=lay, n=k))
                out.append(Item(d, h.id, specs[0].label, _dedupe(specs)))
    return out


def _dedupe(specs: list[ObjectSpec]) -> list[ObjectSpec]:
    seen, out = set(), []
    for s in specs:
        k = s.layout.model_dump_json() if s.layout else ""
        if k not in seen:
            seen.add(k)
            out.append(s)
    return out


def _id(key: str, host: str | None, n: int = 0) -> str:
    return f"decor_{key}" + (f"_{host}" if host else "") + (f"_{n + 1}" if n else "")


def _spec(d: Decor, host: str | None, label: str, layout: dict[str, Any], n: int = 0) -> ObjectSpec:
    return ObjectSpec(id=_id(d.key, host, n), query=d.query, alt_queries=list(d.alt), label=label + (f" {n + 1}" if n else ""),
                      layout=layout, max_size=d.max_size, min_size=d.min_size, decor=True)


def match_array(o: ObjectSpec, a: AssetRecord, host: ObjectSpec, ha: AssetRecord) -> None:
    """On an array host (rows of desks), repeat the item with the same pitch so one sits on every unit."""
    if host.array is None or o.layout is None or o.layout.on != host.id:
        return
    hl, hd = unit_size(host, ha)
    ol, od = unit_size(o, a)
    ar = host.array
    o.array = Array(count=ar.count, rows=ar.rows, spacing=max(0.0, hl + ar.spacing - ol),
                    aisle=max(0.0, hd + ar.aisle - od), back_gap=max(0.0, hd + ar.back_gap - od),
                    alternate=ar.alternate, back_to_back=ar.back_to_back)
