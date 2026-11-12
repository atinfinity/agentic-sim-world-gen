"""Questions worth asking before building a scene.

Per place, the few items that change the result the most (size, entrance, register, number of people...).
`plan_questions` drops the ones the request already answers (keyword match on the user's words) and returns
at most `limit`, each with 2-4 choices, the first being the default used when the user does not care.
Answers are recorded as assumptions with given=true under the question's `item`.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from agentic_sim_world_gen.scene.spec import PLACES, STORE_PLACES


@dataclass(frozen=True)
class Question:
    key: str
    item: str  # assumption item the answer is recorded under
    question: str
    options: tuple[str, ...]  # first = default
    why: str
    given: tuple[str, ...] = field(default=())  # regexes: the request already answers it
    moot: tuple[str, ...] = field(default=())  # regexes: the answer is obvious (a study is for one person)
    rework: int | None = None  # how much is rebuilt when the default was wrong (REWORK by key when None)

    def asked(self, place: str) -> dict[str, Any]:
        return {"key": self.key, "item": self.item, "question": self.question, "options": list(self.options),
                "default": self.options[0], "why": self.why, "rework": REWORK_JA[rework_of(self)],
                "score": score(self)}


# The order of the questions: impact x uncertainty.
# Impact = how much is rebuilt when the default was wrong: 3 the room and most of what stands in it (its size, how
# many people / seats / racks, what kind of room), 2 where several things stand (entrance, register, window; the
# default for a key not listed), 1 a surface or a few small things - fixed on the preview afterwards (a note,
# a mood), so never asked: its default is a guess (`later`).
REWORK = {"size": 3, "room_kind": 3, "people": 3, "seats": 3, "racks": 3, "kind": 3, "line": 3, "site": 3,
          "desk_layout": 3, "style": 1}
REWORK_JA = {3: "部屋ごと作り直し", 2: "いくつかの物を置き直し", 1: "後から直せる"}
ASK_FROM = 2  # questions with less impact are not asked
DERIVED = 0.3  # uncertainty of a default that follows another answer (「人数に合わせる」)


def rework_of(q: Question) -> int:
    return q.rework if q.rework is not None else REWORK.get(q.key, 2)


def unsure(q: Question) -> float:
    """How likely the default is wrong: with n choices and nothing else known, 1 - 1/n; a default that follows
    another answer (「席数に合わせる」) is rarely wrong."""
    if "合わせる" in q.options[0]:
        return DERIVED
    return 1 - 1 / max(len(q.options), 1)


def score(q: Question) -> float:
    return round(rework_of(q) * unsure(q), 2)


# questions a remembered preference answers: question key -> preference items (room items of the place;
# object items of any kind)
PREFERRED = {"style": ("floor", "walls", "lighting"), "aisle": ("aisle",)}


def _preferred(q: Question, place: str) -> list[dict[str, Any]]:
    from agentic_sim_world_gen import preferences as pf

    items = PREFERRED.get(q.key)
    if not items:
        return []
    return [{"item": r["item"], "value": pf._text(r["item"], r["value"], r.get("label"))}
            for r in pf.preferences(place)["defaults"] if r["item"] in items]


_NUM = r"[0-9０-９一二三四五六七八九十百]+(?:\.[0-9]+)?"
SIZE = (rf"{_NUM}\s*(?:畳|帖|㎡|m2|平米|坪|m\b|メートル)", rf"{_NUM}\s*[x×]\s*{_NUM}", "広さ", "狭い", "広い", "小さめ", "大きめ")
ENTRANCE = ("入口", "入り口", "出入口", "出入り口", "玄関", "ドア", "扉", "自動ドア", r"\bdoor", r"\bentrance")
PEOPLE = (rf"{_NUM}\s*(?:人|名|席)", "一人暮らし", "ひとり暮らし", "夫婦", "家族", "人数")


def _size(options: tuple[str, ...], why: str = "部屋の広さで置ける物の数と通路が変わる") -> Question:
    return Question("size", "size", "広さはどのくらいですか？", options, why, SIZE)


def _entrance(options: tuple[str, ...] = ("南の壁の中央", "南の壁の端（角寄り）", "東の壁"),
              why: str = "入口の位置で通路と什器の向きが決まる") -> Question:
    return Question("entrance", "entrance", "入口はどこですか？", options, why, ENTRANCE)


HOME = (
    Question("room_kind", "room kind", "どの部屋ですか？", ("リビング", "寝室", "書斎", "子供部屋"),
             "部屋の種類で置く家具が変わる",
             ("書斎", "寝室", "リビング", "居間", "ダイニング", "LDK", "LD", "子供部屋", "和室", "キッチン", "台所",
              "洋室", "ワンルーム", "1K", "1R")),
    _size(("6畳（3.64×2.73 m）", "8畳（3.64×3.64 m）", "10畳（4.55×3.64 m）", "12畳（5.46×3.64 m）")),
    Question("style", "style", "テイストは？", ("ナチュラル（木目）", "モダン（白・黒）", "和風"),
             "床・壁の材質と家具の色が変わる",
             ("ナチュラル", "モダン", "和風", "和室", "北欧", "木目", "テイスト", "インダストリアル", "シンプル", "畳の部屋",
              "白い", "黒い", "ウッド")),
    Question("window", "window", "窓はどの壁ですか？", ("南の壁", "東の壁", "西の壁", "窓なし"),
             "机・棚・ベッドの位置が変わる（窓の前に背の高い物を置かない）", ("窓",)),
    Question("people", "people", "何人で使いますか？", ("1人", "2人", "4人家族"),
             "椅子・ベッド・食器の数が変わる", PEOPLE, ("書斎", "子供部屋", "ワンルーム", "1k", "1r", "study"),
             rework=2),  # at home the room stays: chairs and beds are added
)

OFFICE = (
    Question("people", "people", "何人分の席ですか？", ("4人", "8人", "16人"), "机と椅子の数・部屋の広さが決まる", PEOPLE),
    Question("desk_layout", "desk layout", "机の並べ方は？", ("島型（向かい合わせ）", "壁向き（窓向き）", "1人ずつのブース"),
             "配置と通路がまったく変わる",
             ("島", "対面", "向かい合", "ブース", "フリーアドレス", "壁向き", "窓向き", "スクール形式", "並べ方")),
    Question("meeting", "meeting space", "打ち合わせスペースは？", ("なし", "4人用のテーブル", "仕切った会議室"),
             "部屋の一部が会議用になる", ("会議", "打ち合わせ", "ミーティング", "応接")),
    _size(("人数に合わせる（1人 6 m² 目安）", "8 × 6 m", "12 × 8 m")),
)

_REGISTER = Question("register", "register", "レジはどこですか？", ("入口の横", "奥の壁"),
                     "レジの前の通路と入口からの見通しが変わる", ("レジ", "カウンター", "会計", "checkout"))
_COLD = Question("cold_cases", "cold cases", "冷蔵ケースはどの壁に並べますか？", ("奥の壁", "入口から見て右の壁", "左の壁"),
                 "大きな什器なので通路の取り方が変わる",
                 ("冷蔵", "冷凍", "リーチイン", "ショーケース", "ケース", "飲料", "ドリンク"))
SHOP = (
    _size(("標準（10 × 8 m）", "小さめ（8 × 6 m）", "大きめ（14 × 10 m）")),
    _entrance(),
    _REGISTER,
    _COLD,
)
STORE_SIZES = {
    "convenience_store": ("標準（10 × 8 m）", "小さめ（8 × 6 m）", "大きめ（14 × 10 m）"),
    "supermarket": ("小型（20 × 15 m）", "標準（30 × 20 m）", "大型（40 × 30 m）"),
    "drugstore": ("標準（20 × 15 m）", "小さめ（12 × 10 m）", "大きめ（30 × 20 m）"),
    "home_center": ("標準（40 × 30 m）", "小さめ（25 × 20 m）"),
    "hundred_yen_shop": ("標準（15 × 10 m）", "小さめ（10 × 8 m）"),
    "bookstore": ("標準（15 × 10 m）", "小さめ（10 × 8 m）", "大きめ（25 × 15 m）"),
    "electronics_store": ("標準（30 × 20 m）", "小さめ（20 × 15 m）"),
    "apparel_store": ("標準（15 × 10 m）", "小さめ（10 × 8 m）"),
    "bakery": ("標準（8 × 6 m）", "小さめ（6 × 5 m）", "大きめ（12 × 8 m）"),
    "variety_store": ("標準（8 × 6 m）", "小さめ（6 × 5 m）", "大きめ（12 × 8 m）"),
    "florist": ("標準（6 × 5 m）", "小さめ（4 × 4 m）", "大きめ（8 × 6 m）"),
    "greengrocer": ("標準（8 × 6 m）", "小さめ（6 × 5 m）", "大きめ（12 × 8 m）"),
    "toy_store": ("標準（15 × 10 m）", "小さめ（10 × 8 m）", "大きめ（25 × 15 m）"),
    "pet_shop": ("標準（12 × 10 m）", "小さめ（8 × 6 m）", "大きめ（20 × 15 m）"),
    "garden_center": ("標準（30 × 20 m）", "小さめ（20 × 15 m）"),
    "shoe_store": ("標準（12 × 8 m）", "小さめ（8 × 6 m）", "大きめ（20 × 12 m）"),
    "optician": ("標準（8 × 6 m）", "小さめ（6 × 5 m）", "大きめ（10 × 8 m）"),
    "liquor_store": ("標準（10 × 8 m）", "小さめ（8 × 6 m）", "大きめ（15 × 10 m）"),
    "bicycle_shop": ("標準（10 × 8 m）", "小さめ（8 × 5 m）", "大きめ（15 × 10 m）"),
    "wagashi_shop": ("標準（6 × 5 m）", "小さめ（4 × 4 m）", "大きめ（8 × 6 m）"),
    "record_store": ("標準（8 × 6 m）", "小さめ（6 × 5 m）", "大きめ（12 × 8 m）"),
    "sporting_goods_store": ("標準（20 × 15 m）", "小さめ（12 × 10 m）", "大きめ（30 × 20 m）"),
    "cosmetics_store": ("標準（10 × 8 m）", "小さめ（6 × 5 m）", "大きめ（15 × 10 m）"),
    "furniture_store": ("標準（30 × 20 m）", "小さめ（15 × 10 m）", "大きめ（40 × 30 m）"),
    "asian_grocery": ("標準（8 × 6 m）", "小さめ（6 × 5 m）", "大きめ（12 × 8 m）"),
}
# which shop questions apply (cold cases only where food is sold)
_STORE_KEYS = {k: ("size", "entrance", "register", "cold_cases")
               for k in ("shop", "convenience_store", "supermarket", "drugstore", "greengrocer", "liquor_store", "asian_grocery")}

FOOD = (
    Question("seats", "seats", "席の数は？", ("12席", "20席", "40席"), "テーブルと椅子の数・部屋の広さが決まる", PEOPLE + ("席",)),
    Question("counter", "counter", "カウンターは？", ("注文カウンターのみ", "カウンター席もある", "なし（テーブルのみ）"),
             "部屋の一辺の使い方が変わる", ("カウンター", "レジ")),
    _entrance(("南の壁の中央", "南の壁の端（角寄り）", "東の壁"), "入口と通路の位置で席の並びが変わる"),
    _size(("席数に合わせる", "8 × 6 m", "12 × 8 m")),
)

RESTAURANT = (
    Question("kind", "kind", "どんなお店ですか？", ("ファミリーレストラン（ボックス席）", "定食屋・食堂", "居酒屋（座敷あり）"),
             "席の形（ボックス席・テーブル・座敷）と厨房の大きさが変わる",
             ("ファミレス", "ファミリーレストラン", "定食", "食堂", "居酒屋", "ラーメン", "そば", "うどん", "中華", "洋食",
              "和食", "焼肉", "寿司", "座敷", "ボックス席", "family restaurant", "diner", "izakaya")),
    Question("seats", "seats", "席の数は？", ("40席", "24席", "80席"), "テーブルと椅子の数・部屋の広さが決まる", PEOPLE + ("席",)),
    Question("kitchen", "kitchen", "厨房も作りますか？", ("奥に厨房（デシャップ台で仕切る）", "客席だけ", "オープンキッチン（カウンター越し）"),
             "部屋の一部が厨房になり、冷蔵庫・レンジ・シンクが並ぶ", ("厨房", "キッチン", "調理場", "客席だけ", "客席のみ", "kitchen")),
    _entrance(("南の壁の中央", "南の壁の端（角寄り）", "東の壁"), "入口と通路の位置で席の並びが変わる"),
    _size(("席数に合わせる", "15 × 10 m", "10 × 8 m")),
)

WAREHOUSE = (
    Question("racks", "racks", "ラックは何列ですか？", ("4列", "6列", "8列"), "倉庫のほとんどを占める",
             (rf"{_NUM}\s*列", "ラック.{0,6}" + _NUM, "棚.{0,6}" + _NUM)),
    Question("dock", "dock", "荷さばき場（搬入口）はどこですか？", ("南の壁（シャッター）", "北の壁", "なし"),
             "ラックの向きと通路の向きが決まる", ("荷さばき", "搬入", "ドック", "シャッター", "トラック", "dock")),
    Question("aisle", "aisle", "通路の幅は？", ("フォークリフト用（3 m）", "人と台車（1.5 m）", "AMR 用（2 m）"),
             "ラックの列数と配置が変わる", ("通路", "フォークリフト", "AMR", "AGV", "台車", "aisle")),
    _size(("20 × 15 m", "30 × 20 m", "50 × 30 m"), "列数と通路で広さが決まる"),
)

FACTORY = (
    Question("line", "line", "何を置きますか？", ("作業台の列（組立）", "工作機械", "コンベアのライン"),
             "置く物の種類が変わる",
             ("作業台", "機械", "ライン", "コンベア", "組立", "旋盤", "ロボットセル", "workbench", "conveyor")),
    Question("dock", "dock", "搬入口はどこですか？", ("南の壁（シャッター）", "なし"), "通路の向きが決まる",
             ("荷さばき", "搬入", "シャッター", "トラック")),
    _size(("20 × 15 m", "30 × 20 m"), "ラインの長さで広さが決まる"),
)

PLANT_FACTORY = (
    Question("racks", "racks", "栽培棚は何列・何段ですか？", ("4列 × 3段", "6列 × 3段", "2列 × 5段"), "部屋のほとんどを占める",
             (rf"{_NUM}\s*(?:列|段)", "棚.{0,6}" + _NUM)),
    Question("aisle", "aisle", "棚の間の通路は？", ("人と収穫ロボット（1.2 m）", "人だけ（0.9 m）", "台車も通る（1.5 m）"),
             "棚の列数と配置が変わる", ("通路", "ロボット", "台車", "AMR", "AGV", "aisle")),
    _size(("10 × 8 m", "20 × 12 m", "6 × 5 m（試験室）"), "列数と通路で広さが決まる"),
)

DATACENTER = (
    Question("racks", "racks", "ラックは何台ですか？", ("2列 × 10 台", "4列 × 10 台", "1列 × 6 台"), "部屋のほとんどを占める",
             (rf"{_NUM}\s*(?:台|列|本)",)),
    Question("aisle", "aisle", "通路は？", ("ホット / コールドアイル（向かい合わせ）", "全部同じ向き"), "ラックの向きが変わる",
             ("ホット", "コールド", "アイル", "aisle", "向かい合", "同じ向き")),
    _size(("ラックに合わせる", "12 × 8 m", "20 × 12 m")),
)

GENERIC = (
    _size(("標準（8 × 6 m）", "小さめ（5 × 4 m）", "大きめ（15 × 10 m）")),
    _entrance(),
    Question("people", "people", "何人くらいが使う場所ですか？", ("少人数（〜4人）", "10人前後", "30人以上"),
             "机・椅子・ベッドの数が変わる", PEOPLE),
)

OUTDOOR = (
    Question("site", "site", "何がある場所ですか？", ("歩道と車道のある通り", "建物の前の構内（駐車場）", "公園・広場"),
             "エリア（道路・歩道・芝）の並びが変わる",
             ("道路", "車道", "歩道", "駐車場", "構内", "公園", "広場", "交差点", "横断歩道")),
    _size(("30 × 20 m", "50 × 30 m", "100 × 50 m"), "敷地の広さ"),
)

TABLE: dict[str, tuple[Question, ...]] = {
    "home": HOME, "office": OFFICE, "cafe": FOOD, "restaurant": RESTAURANT,
    "warehouse": WAREHOUSE, "factory": FACTORY, "plant_factory": PLANT_FACTORY, "datacenter": DATACENTER, "outdoor": OUTDOOR,
}
for _p in ("shop", *STORE_PLACES):
    _keys = _STORE_KEYS.get(_p, ("size", "entrance", "register"))
    _sizes = STORE_SIZES.get(_p)
    TABLE[_p] = tuple(_size(_sizes) if (q.key == "size" and _sizes) else q for q in SHOP if q.key in _keys)
# a bakery: the eat-in corner and the middle of the shop change the layout more than cold cases
_EAT_IN = Question("eat_in", "eat-in", "イートインはありますか？", ("窓際にカウンター席", "なし", "テーブル席（4〜8席）"),
                   "部屋の一辺かひと隅が席になる", ("イートイン", "席", "カフェスペース", "テイクアウトのみ"))
_ISLAND = Question("island", "center table", "店の中央にパンを並べる平台を置きますか？", ("置く（1〜2台）", "置かない（壁際の棚だけ）"),
                   "中央が空くか、平台を回る通路になるかが変わる", ("中央", "平台", "島", "アイランド", "真ん中"))
TABLE["bakery"] = (*TABLE["bakery"][:2], _ISLAND, _EAT_IN, *TABLE["bakery"][2:])
for _p in PLACES:
    TABLE.setdefault(_p, GENERIC)

def _purpose_question() -> Question:
    from agentic_sim_world_gen.scene.purpose import ORDER, PURPOSES

    return Question("purpose", "purpose", "何に使うシーンですか？", tuple(PURPOSES[n].label for n in ORDER),
                    "通路幅・小物の量・合成データの設定が変わる（set_purpose）",
                    tuple(w for p in PURPOSES.values() for w in p.words))


# asked for every place on top of the place's own questions: the purpose sets several defaults at once
PURPOSE = _purpose_question()

def _robot_question() -> Question:
    from agentic_sim_world_gen.scene.purpose import ROBOT_SIZES

    return Question("robot", "robot", "ロボットの機体はどのくらいの大きさですか？（機種名や幅でも）",
                    tuple(label for label, _ in ROBOT_SIZES),
                    "通路幅・棚の間隔・ドア前の空きが機体で決まる。後から分かると配置をやり直す（set_purpose robot_radius）")


# asked before the draft when the purpose has a robot and the request does not say its size
ROBOT = _robot_question()
# asked before the draft when the request is about a place that exists and comes with no plan / photo / video
SOURCE = Question("source", "source", "その場所の図面か写真はありますか？",
                  ("ない（文章から作る）", "写真がある", "間取り図・図面がある", "動画がある"),
                  "実在の場所は、文章から部屋を作るより図面・写真から始める方が早く、やり直しが少ない")
# the request is about a place that exists (kept narrow: a made-up room is never asked)
# 「再現」「私の」 alone are left out: 「昭和の喫茶店を再現」「私の理想の書斎」 are made up
EXISTING_WORDS = ("うちの", "我が家", "わが家", "弊社", "当社", "自社", "実家", "実際の", "実在",
                  "この店", "この部屋", "この倉庫", "この工場", "このオフィス", "既存の", "今ある",
                  "デジタルツイン", r"\bour\b", r"\bexisting\b", "digital twin")
# ... and already comes with something to read it from
INPUT_WORDS = ("写真", "画像", "図面", "間取り図", "間取図", "平面図", "見取り図", "動画", "ビデオ", "スキャン", "点群",
               "cad", "dxf", "dwg", "ifc", "photo", "picture", "floor plan", "floorplan", "drawing", "video", "scan")

# words that name the place (checked in order: more specific first)
PLACE_WORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("convenience_store", ("コンビニ", "convenience store", "konbini")),
    # before supermarket: 「アジアンスーパー」
    ("asian_grocery", ("アジア食材", "アジアン食材", "アジアンスーパー", "輸入食材店", "エスニック食材", "中華食材", "韓国食材",
                       "asian grocery", "asian food shop", "asian food store", "asian supermarket")),
    ("supermarket", ("スーパー", "supermarket")),
    ("drugstore", ("ドラッグストア", "薬局", "drugstore", "pharmacy")),
    ("home_center", ("ホームセンター", "home center", "hardware store")),
    # after drugstore: a drugstore's cosmetics corner is the drugstore
    ("cosmetics_store", ("化粧品店", "化粧品売り場", "コスメショップ", "コスメ店", "コスメ売り場", "cosmetics store",
                         "cosmetics shop", "beauty store")),
    ("bicycle_shop", ("自転車店", "自転車屋", "サイクルショップ", "bicycle shop", "bicycle store", "bike shop", "cycle shop")),
    ("wagashi_shop", ("和菓子屋", "和菓子店", "和菓子の店", "和菓子のお店", "wagashi")),
    ("record_store", ("レコード店", "レコード屋", "レコードショップ", "中古レコード", "record store", "record shop",
                      "vinyl shop")),
    ("sporting_goods_store", ("スポーツ用品店", "スポーツ用品", "スポーツ店", "スポーツショップ", "sporting goods",
                              "sports store", "sports shop")),
    ("furniture_store", ("家具店", "家具屋", "家具売り場", "インテリアショップ", "furniture store", "furniture shop")),
    ("hundred_yen_shop", ("100円ショップ", "百円ショップ", "100均", "百均")),
    ("variety_store", ("雑貨店", "雑貨屋", "雑貨ショップ", "雑貨のお店", "雑貨の店", "雑貨売り場", "zakka", "variety store",
                       "gift shop", "土産", "みやげ", "souvenir")),
    ("florist", ("花屋", "生花店", "フラワーショップ", "お花屋", "florist", "flower shop")),
    ("greengrocer", ("八百屋", "青果店", "青果", "果物屋", "greengrocer", "produce store", "fruit shop")),
    ("toy_store", ("おもちゃ屋", "おもちゃ売り場", "玩具店", "玩具売り場", "トイショップ", "toy store", "toy shop")),
    ("pet_shop", ("ペットショップ", "ペット用品", "pet shop", "pet store")),
    ("garden_center", ("園芸店", "ガーデンセンター", "園芸用品", "garden center", "garden centre", "plant nursery")),
    ("shoe_store", ("靴屋", "靴店", "シューズショップ", "シューズ店", "靴売り場", "shoe store", "shoe shop")),
    ("optician", ("眼鏡店", "眼鏡屋", "メガネ店", "メガネ屋", "めがね屋", "眼鏡売り場", "optician", "eyewear store")),
    ("bookstore", ("書店", "本屋", "bookstore")),
    ("electronics_store", ("家電量販店", "家電店", "electronics store")),
    ("apparel_store", ("衣料品店", "洋服屋", "アパレル", "服屋", "apparel")),
    ("bakery", ("パン屋", "ベーカリー", "パン工房", "bakery")),  # before cafe: a "ベーカリーカフェ" sells bread
    ("cafe", ("カフェ", "喫茶", "cafe", "coffee shop")),
    ("restaurant", ("レストラン", "ファミレス", "飲食店", "食堂", "定食屋", "居酒屋", "ラーメン屋", "ラーメン店", "中華料理店",
                    "restaurant", "diner", "izakaya")),
    # after restaurant: 居酒屋 says 酒屋
    ("liquor_store", ("酒屋", "酒店", "リカーショップ", "酒販店", "liquor store", "liquor shop", "wine shop")),
    ("datacenter", ("データセンター", "サーバールーム", "サーバ室", "data center", "datacenter", "server room")),
    ("warehouse", ("倉庫", "物流", "warehouse")),
    # before factory: 植物工場 says 工場
    ("plant_factory", ("植物工場", "水耕", "養液栽培", "栽培棚", "栽培室", "多段栽培", "イチゴ栽培", "いちご栽培",
                       "plant factory", "vertical farm", "indoor farm", "hydroponic")),
    ("factory", ("工場", "製造", "factory", "plant")),
    ("lab", ("研究室", "実験室", "ラボ", "lab")),
    ("hospital", ("病院", "病室", "診察室", "クリニック", "hospital", "clinic")),
    ("school", ("教室", "学校", "school", "classroom")),
    ("station", ("駅", "ホーム", "改札", "station")),
    # 会議 / 打ち合わせ: 「4人で会議する部屋」 is an office, not a home
    ("office", ("オフィス", "事務所", "会議", "打ち合わせ", "ミーティング", "執務", "office", "meeting room")),
    # shops with no table of their own (「おもちゃ屋」「花屋」 were not shops, so the generic table asked 人数)
    ("shop", ("店", "ショップ", "売り場", "花屋", "八百屋", "青果", "おもちゃ屋", "玩具", "靴屋", "メガネ屋", "眼鏡屋", "酒屋",
              "鮮魚", "魚屋", "肉屋", "精肉", "文房具屋", "shop", "store", "florist", "greengrocer",
              # any 〜屋 / 〜屋さん, but not 部屋・小屋・母屋 or 屋外・屋内・屋上・屋根
              r"(?<![部小母\s])屋(?!外|内|上|根)")),
    ("outdoor", ("屋外", "歩道", "道路", "駐車場", "公園", "広場", "outdoor", "street")),
    ("home", ("書斎", "寝室", "リビング", "居間", "ダイニング", "LDK", "子供部屋", "和室", "キッチン", "畳", "帖",
              "ワンルーム", "自宅", "家", "部屋", "home", "bedroom", "living room", "study")),
)

ROBOT_WORDS = ("ロボット", "robot")
PHOTO_WORDS = ("写真", "画像", "photo", "picture")

SKIP_WORDS = ("おまかせ", "お任せ", "任せ", "確認は不要", "確認不要", "聞かないで", "質問なし", "質問は不要", "最後まで進めて",
              "そのまま進めて", "全部決めて")


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").lower()


def _hit(patterns: tuple[str, ...], text: str) -> str | None:
    for p in patterns:
        m = re.search(_norm(p) if not any(c in p for c in "\\[(") else p, text, re.IGNORECASE)
        if m:
            return m.group(0)
    return None


# file paths and URLs in a request ("/home/user/.../souvenir.jpg" said home)
_PATH = re.compile(r"(?:[a-z]+://|[~.]?/)\S+|\S+\.(?:jpe?g|png|webp|heic|mp4|mov|pdf|dxf|ifc|json)\b", re.IGNORECASE)


def guess_place(request: str) -> str | None:
    text = _PATH.sub(" ", _norm(request))
    for place, words in PLACE_WORDS:
        if _hit(words, text):
            return place
    return None


PLACE_QUESTION = {"key": "place", "item": "place", "question": "どんな場所ですか？",
                  "options": ["リビング", "書斎", "オフィス", "コンビニ", "倉庫"], "default": "リビング",
                  "why": "場所の種類で置く物がまるごと変わる"}


VAGUE_SHARE = 2 / 3  # of the place's questions left to defaults: the request says little
PHOTO_INVITE = "イメージに近い写真があれば貼ってください（無ければ、プレビューの「雰囲気」の見本から選べます）。"
REFERENCE_WORDS = ("参考", "イメージ図", "ムードボード", "moodboard", "mood board", "reference", "pinterest", "みたいな感じ",
                   "のような雰囲気", "風の")


def _vague(place: str, out: dict[str, Any], known: set[str], skip: str | None, reference: str | None) -> bool:
    """The request leaves most of the place's items open (or names no place at all) and shows no picture: the
    time to ask once for a photo that is close. Not when the user left it all to us or already answered."""
    if skip or reference or known & {"reference", "photo", "look", "taste"}:
        return False
    if any(q.get("key") == "place" for q in out.get("ask_now") or []):
        return True
    table = TABLE.get(place or "other", GENERIC)
    keys = {q.key for q in table}
    # what the request or `known` answers; not what is left for later or asked as the activity
    asked = [s for s in out["skipped"] if s["key"] in keys and s["reason"].startswith(("known", "given", "obvious"))]
    open_ = len(table) - len(asked)
    return open_ >= 2 and open_ / len(table) >= VAGUE_SHARE


def plan_questions(request: str = "", place: str | None = None, known: list[str] | None = None,
                   limit: int = 3, ask_first: bool = False, purpose: str | None = None) -> dict[str, Any]:
    """The questions for `request`: per place, the items that change the result most, minus what the
    request already says (`skipped` with the words that answer it) and the `known` keys/items.
    Order: impact (how much is rebuilt when the default was wrong) x uncertainty, biggest first. What can
    be fixed on the preview afterwards is not asked (`later`: its default, a guess), nor what a remembered
    preference answers (`preferred`).
    Draft first (the default): the questions are not asked before the scene; their defaults build a draft
    and the user corrects it. Only when the request names no kind of place at all is the place asked first
    (`ask_now`). ask_first=True: the old way, ask the questions before building.
    purpose: the purpose the user answered (a name or the option's label), as if the request said it.
    Two more questions, each only when its condition holds, asked before the draft (`ask_now`): the robot's
    size (the purpose is amr / manipulation and the request names no machine or size; `robot` when it does) and
    whether there is a plan / photo (the request is about a place that exists and comes with none)."""
    text = _norm(request)
    guessed = place is None
    if place is None:
        place = guess_place(request) or "other"
    if place not in TABLE:
        raise ValueError(f"unknown place {place!r}; one of {', '.join(PLACES)}")
    out: dict[str, Any] = {"place": place, "place_guessed": guessed}
    skip = _hit(SKIP_WORDS, text)
    known_set = {k.lower() for k in known or ()}
    questions, skipped, later, preferred = [], [], [], []
    for q in sorted(TABLE[place], key=lambda q: -score(q)):  # stable: the table's order on a tie
        if q.key in known_set or q.item in known_set:
            skipped.append({"key": q.key, "item": q.item, "reason": "known"})
            continue
        words = _hit(q.given, text)
        if words:
            skipped.append({"key": q.key, "item": q.item, "reason": f"given: {words}"})
            continue
        words = _hit(q.moot, text)
        if words:
            skipped.append({"key": q.key, "item": q.item, "reason": f"obvious from: {words}"})
            continue
        if got := _preferred(q, place):
            said = "、".join(f"{g['item']} {g['value']}" for g in got)
            skipped.append({"key": q.key, "item": q.item, "reason": f"preference: {said}"})
            preferred.append({"key": q.key, "item": q.item, "remembered": got})
            continue
        if rework_of(q) < ASK_FROM:
            skipped.append({"key": q.key, "item": q.item, "reason": "later: fixed on the preview afterwards"})
            later.append({"item": q.item, "value": q.options[0], "options": list(q.options)})
            continue
        questions.append(q.asked(place))
    # the activity: who, how many, doing what - the size and the main furniture follow from it
    from agentic_sim_world_gen.scene import activity as act

    size_open = any(q["key"] == "size" for q in questions)
    if act.for_place(place):
        got = act.derive(place, request)
        if got:  # the request says it: the size is derived, not asked
            out["activity"] = got
            for q in [q for q in questions if q["key"] == "size"]:
                questions.remove(q)
                skipped.append({"key": q["key"], "item": q["item"], "reason": f"derived from the activity: {got['label']}"})
        elif size_open and "activity" not in known_set:  # asked in place of the size and the number of people
            n = act.people_in(request)
            for q in [q for q in questions if q["key"] in act.ANSWERS]:
                questions.remove(q)
                skipped.append({"key": q["key"], "item": q["item"], "reason": "asked as the activity"})
            questions.insert(0, act.question(place, n[0] if n else None))
    # the purpose: one more question on top of `limit` (4 at most, what one AskUserQuestion takes)
    from agentic_sim_world_gen.scene import purpose as pp

    said = pp.guess(request)
    if purpose and (answered := pp.by_label(purpose)) is not None:
        said = (answered.name, purpose)
    purpose_q = None
    if said:
        out["purpose"] = said[0]
        skipped.append({"key": "purpose", "item": "purpose", "reason": f"given: {said[1]}"})
    elif "purpose" in known_set:
        skipped.append({"key": "purpose", "item": "purpose", "reason": "known"})
    else:
        purpose_q = PURPOSE.asked(place)
        robot = _hit(ROBOT_WORDS, text)
        if robot:
            # 「ロボット」 alone does not say whether it drives, picks or is just shown; amr was assumed
            out["purpose_unclear"] = (f"the request says {robot!r} but not what the robot does: ask the purpose before "
                                      "building (do not assume amr)")
    if skip and "purpose_unclear" in out:
        out["ask_now"] = [purpose_q]
        purpose_q = None
    if skip:
        out["no_questions"] = f"the user said {skip!r}: ask nothing, use the defaults and record them as guesses"
        out["defaults"] = [{"item": q["item"], "value": q["default"]} for q in questions + [purpose_q] if q]
        questions, purpose_q = [], None
    out["questions"] = questions[:max(0, limit)] + ([purpose_q] if purpose_q and limit > 0 else [])
    out["skipped"] = skipped
    if later:
        out["later"] = later
    if preferred:
        out["preferred"] = preferred
    if not out["questions"] and out.get("ask_now"):
        out["hint"] = ("ask only `ask_now` (what the robot does), even though the user left the rest to you; "
                       "build everything else with the defaults")
    elif not out["questions"]:
        out["hint"] = "nothing to ask: build with the request and the defaults"
    elif ask_first:
        out["hint"] = ("ask these in ONE message, each with its options (the first is the default; allow a free answer); "
                       "after the answer record each with set_assumptions(item=<item>, given=true); anything the user "
                       "leaves to you is a guess with basis 'おまかせ（既定）'")
    elif "purpose_unclear" in out:
        out["ask_now"] = [purpose_q] + ([PLACE_QUESTION] if guessed and place == "other" and "place" not in known_set else [])
        rest = [q for q in out["questions"] if q is not purpose_q]
        out["defaults"] = [{"item": q["item"], "value": q["default"], "options": q["options"]} for q in rest]
        out["hint"] = ("a robot is named but not its job: ask only `ask_now` now (the purpose decides aisle widths and how "
                       "full the room is); the other questions wait for the draft, built with their defaults")
    elif guessed and place == "other" and "place" not in known_set:
        out["ask_now"] = [PLACE_QUESTION]
        out["hint"] = ("the request names no kind of place: ask only `ask_now` (the place) now, then call plan_questions "
                       "again with the answer; the other questions wait for the draft")
    else:
        out["defaults"] = [{"item": q["item"], "value": q["default"], "options": q["options"]} for q in out["questions"]]
        if size_open:
            # nobody said how big - three plans to pick from say more than one draft and a question in words
            out["drafts"] = {"axis": "size", "how": "create_scene(drafts='size') with the default size: the server "
                             "draws small / middle / large as plans (no 3D yet); show the three, ask which is "
                             "closest, choose_layout(name), then finish(draft=true)"}
        out["hint"] = ("draft first: do not ask these now. Build the scene with each default, record it with "
                       "set_assumptions(item=<item>, value=<default>, given=false, basis='叩き台の既定', options=<options>), "
                       "and ask them while showing the draft create_scene made (started.draft; finish(draft=true) after fixes), "
                       "each with its other options")
    # the robot's size and a plan / photo of a place that exists, each only when its condition holds
    extra, robot_q = [], None
    has_input = _hit(INPUT_WORDS, text) or _PATH.search(text)
    if "source" in known_set:
        skipped.append({"key": "source", "item": "source", "reason": "known"})
    elif (words := _hit(EXISTING_WORDS, text)) and not has_input:
        out["existing_place"] = words
        extra.append(SOURCE.asked(place))
    robot_said = pp.robot_from(request)
    if robot_said:
        out["robot"] = {"radius": robot_said[0], "from": robot_said[1]}
        skipped.append({"key": "robot", "item": "robot", "reason": f"given: {robot_said[1]}"})
    elif "robot" in known_set:
        skipped.append({"key": "robot", "item": "robot", "reason": "known"})
    else:
        robot_q = ROBOT.asked(place)
        robot_q["radius"] = {label: r for label, r in pp.ROBOT_SIZES}
        if said and pp.PURPOSES[said[0]].robot:
            extra.append(robot_q)
        elif not said and "purpose" not in known_set:
            out["after_purpose"] = {"when": [n for n in pp.ORDER if pp.PURPOSES[n].robot], "ask": robot_q}
    if extra and skip:
        out["defaults"] = out.get("defaults", []) + [{"item": q["item"], "value": q["default"]} for q in extra]
    elif extra and ask_first:
        out["questions"] = (extra + out["questions"])[:max(0, limit) + 1] if limit > 0 else []
    elif extra:
        out["ask_now"] = out.get("ask_now", []) + extra
        tail = "ask `ask_now` before building (" + "; ".join(
            "the robot's size decides the aisles" if q["key"] == "robot" else
            "a place that exists: with a plan / photo / video, read it first (set_input, plans.md / photos.md / "
            "videos.md) instead of drafting from words" for q in extra) + ")"
        out["hint"] = tail if out["hint"].startswith("nothing to ask") else out["hint"] + "; " + tail
    if "after_purpose" in out:
        out["hint"] += ("; when the purpose is answered amr / manipulation, ask `after_purpose.ask` (the robot's size) "
                        "with it")
    if robot_said or any(q["key"] == "robot" for q in extra) or "after_purpose" in out:
        out["hint"] += ("; pass the robot's radius to create_scene / set_purpose (robot_radius: `robot.radius`, or the "
                        "answer's `radius`; わからない / a free answer you cannot read = leave it out, 0.3 m as a guess)")
    photos = re.findall(r"[^\s「」'\"]+\.(?:jpe?g|png|heic|webp)\b", request or "", re.IGNORECASE)
    if photos or _hit(PHOTO_WORDS, text):
        # a trial only looked at the photo with Read; it was never recorded nor compared
        out["photos"] = photos
        out["hint"] += ("; the request has a photo: after create_scene record it with set_input (kind photo, its camera), "
                        "and after compose_scene run compare_photo and fix what differs before reporting")
    elif _vague(place, out, known_set, skip, _hit(REFERENCE_WORDS, text)):
        # who cannot put the picture in their head into words can often show a photo that is close
        out["ask_photo"] = {"say": PHOTO_INVITE, "once": "say it once, in your first reply (with ask_now's question or "
                            "with the draft); pass known=['reference'] from then on and never ask again",
                            "then": "a photo comes -> look.md (kind reference: its taste only); none -> go on"}
        out["hint"] += "; the request is vague: add `ask_photo.say` to your first reply, once"
    if "activity" in out:
        out["hint"] += ("; `activity`: the request says what happens there - build with its `room` (unless a size was "
                        "given) and `objects`, and record its `assumptions` (basis 「活動から」)")
    elif any(q["key"] == "activity" for q in out["questions"] + out.get("ask_now", [])) or \
            any(d["item"] == "activity" for d in out.get("defaults", [])):
        out["hint"] += ("; the activity question stands for the size and the counts: call derive_activity(<the answer, "
                        "or its default>, place) for the room and the main furniture")
    if later:
        out["hint"] += ("; `later`: never ask these - use the default, record it as a guess (set_assumptions, "
                        "given=false, basis '叩き台の既定', options); the user fixes it on the preview if it is off")
    if preferred:
        out["hint"] += ("; `preferred`: the user's earlier scenes answer these - do not ask and leave those fields "
                        "out of the spec (create_scene applies the remembered values, basis 「前回の好み」)")
    if (TABLE.get(place or "other", GENERIC) is GENERIC and not skip
            and not any(q.get("key") == "place" for q in out.get("ask_now") or [])):
        # no question table for this place: a near example is a better start than thin questions
        out["gallery"] = ("this place has no questions of its own: show_gallery(request) first and offer the near "
                          "examples as the start (start_from_example)")
        out["hint"] += "; no question table for this place: show_gallery first (`gallery`)"
    out["hint"] += ("; pass the purpose to create_scene (purpose=<the answer>, purpose_given=true; left out = demo "
                    "as a guess) so its defaults are applied and recorded")
    return out


def options_for(place: str | None, item: str) -> list[str]:
    """The choices of the question recorded under `item` for `place` (the preview's 「違う」 form offers them)."""
    item = item.strip().lower()
    if item in ("purpose", "用途"):
        return list(PURPOSE.options)
    if item in ("activity", "活動"):
        from agentic_sim_world_gen.scene import activity as act

        q = act.question(place)
        return q["options"] if q else []
    if item == "robot":
        return list(ROBOT.options)
    for q in TABLE.get(place or "other", GENERIC):
        if q.item == item or q.key == item:
            return list(q.options)
    return []
