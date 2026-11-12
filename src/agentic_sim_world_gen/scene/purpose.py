"""What the scene is for: a robot driving through it, an arm picking things up, synthetic data for a
detector, or a demo to look at. Most users cannot say up front how wide the aisles or how full the room should
be, but they know what they will do with the scene; the purpose decides those defaults, recorded as guesses
with the basis 「用途から」 so the user can correct them on the preview."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class Purpose:
    name: str
    label: str  # Japanese, also the question's option
    words: tuple[str, ...]  # regexes / words in the request that say this purpose
    density: str  # Room.density
    aisle: float | None = None  # walking width the robot needs [m]; the place's own width when wider
    robot: bool = False  # a robot drives in it: ask / record its size, run nav_map
    sdg: bool = False  # sets SceneSpec.sdg
    note: str = ""  # Japanese, why the defaults

    def aisle_for(self, robot_radius: float) -> float | None:
        """Free width a robot of this radius needs: its width plus 0.2 m on each side (none when no robot)."""
        if self.aisle is None:
            return None
        return round(max(self.aisle, 2 * robot_radius + 0.4), 2)


PURPOSES: dict[str, Purpose] = {p.name: p for p in (
    Purpose("demo", "見た目の確認・デモ",
            ("デモ", "見せる", "見栄え", "プレゼン", "展示", "紹介動画", "pv", r"\bdemo", "showcase"),
            density="lived_in", note="見た目が主なので小物を足して生活感を出す"),
    Purpose("amr", "AMR・移動ロボットの走行テスト",
            ("amr", "agv", "移動ロボット", "自律移動", "走行", "ナビゲーション", "経路計画", "搬送ロボット", "配膳ロボット",
             "掃除ロボット", r"ロボット\S{0,6}走[らりるれ]", "nav2", "slam", "navigation", "mobile robot"),
            density="sparse", aisle=1.0, robot=True,
            note="床の小物を減らし、ロボットが通れる通路幅を空ける"),
    Purpose("manipulation", "アームで物をつかむ（マニピュレーション）",
            ("マニピュレーション", "マニピュレータ", "ピッキング", "把持", "つかむ", "掴む", "アーム", "双腕", "ヒューマノイド",
             "pick and place", "manipulation", "grasp", r"\bpicking"),
            density="lived_in", aisle=0.9, robot=True,
            note="机・棚の上につかむ物を置き、ロボットが前に立てる幅を空ける"),
    Purpose("sdg", "合成データ（物体検出などの学習）",
            ("合成データ", "学習データ", "教師データ", "物体検出", "セグメンテーション", "アノテーション", "データセット", "sdg",
             "synthetic data", "dataset", "replicator", "検出したい"),
            density="lived_in", sdg=True,
            note="物の種類と置き方にばらつきを持たせ、照明・材質をランダムにして撮る"),
)}
ORDER = ("demo", "amr", "manipulation", "sdg")  # the question's options; the first is the default


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").lower()


def guess(request: str) -> tuple[str, str] | None:
    """(purpose, the words that say it) when the request names a purpose. Robots first: 「AMR のデモ」 is a robot
    test, 「合成データのデモ」 is synthetic data."""
    text = _norm(request)
    for name in ("amr", "manipulation", "sdg", "demo"):
        for w in PURPOSES[name].words:
            m = re.search(w if "\\" in w else re.escape(_norm(w)), text)
            if m:
                return name, m.group(0)
    return None


def by_label(text: str) -> Purpose | None:
    """The purpose for a name or an answer to the question (the option label, or words in a free answer)."""
    t = (text or "").strip()
    if t in PURPOSES:
        return PURPOSES[t]
    for p in PURPOSES.values():
        if t == p.label:
            return p
    g = guess(t)
    return PURPOSES[g[0]] if g else None


# The robot's size: the aisles, the gaps between shelves and the space before a door follow from it, so it
# is asked (plan_questions) when the purpose has a robot and the request does not say it.
# The question's options -> the footprint radius [m]; the first is the default (DEFAULT 0.3 m = 0.6 m wide).
ROBOT_SIZES: tuple[tuple[str, float | None], ...] = (
    ("中型（幅 0.6 m 前後）", 0.3),
    ("小型（幅 0.4 m 前後）", 0.2),
    ("大型（幅 0.9 m 以上）", 0.45),
    ("わからない（幅 0.6 m として進める）", None),
)
# Known machines: (words, the longer side of the footprint [m]). The radius is half of it: nav_map's robot is a
# circle that turns on the spot. Catalog sizes, rounded; a size written in the request wins.
ROBOT_MODELS: tuple[tuple[tuple[str, ...], float], ...] = (
    (("turtlebot3", "turtlebot 3", "タートルボット3"), 0.31),
    (("turtlebot4", "turtlebot 4", "タートルボット4"), 0.34),
    (("nova carter", "novacarter", "ノバカーター"), 0.72),
    (("jackal",), 0.51),
    (("husky",), 0.99),
    (("spot",), 1.1),
)
_NUMBER = r"([0-9]+(?:\.[0-9]+)?)\s*(mm|cm|m|ミリ|センチ|メートル)"
_SIZED = re.compile(r"(?:ロボット|機体|本体|車体|amr|agv|robot)[^。、,.\n]{0,12}?(幅|直径|半径|width|diameter|radius)\s*(?:は|が|:|=)?\s*約?\s*"
                    + _NUMBER)
# 「幅 45cm のロボット」: the size first
_SIZED_BEFORE = re.compile(r"(幅|直径|半径)\s*(?:は|が)?\s*約?\s*" + _NUMBER + r"\s*(?:ほど|くらい|ぐらい|程度)?\s*の\s*(?:\S{0,6}?)(?:ロボット|機体|amr|agv)")
_BARE = re.compile(r"(幅|直径|半径|width|diameter|radius)\s*(?:は|が|:|=)?\s*約?\s*" + _NUMBER)
_UNIT = {"mm": 0.001, "ミリ": 0.001, "cm": 0.01, "センチ": 0.01, "m": 1.0, "メートル": 1.0}
MAX_ROBOT_RADIUS = 1.0  # a "width" above 2 m is the room's, not the robot's


def _radius(kind: str, number: str, unit: str) -> float | None:
    v = float(number) * _UNIT[unit]
    r = v if kind in ("半径", "radius") else v / 2
    return round(r, 3) if 0.03 <= r <= MAX_ROBOT_RADIUS else None


def robot_from(request: str) -> tuple[float, str] | None:
    """(radius [m], the words that say it) when the request gives the robot's size or names a known machine."""
    text = _norm(request)
    for pattern in (_SIZED, _SIZED_BEFORE):
        m = pattern.search(text)
        if m and (r := _radius(*m.groups())) is not None:
            return r, m.group(0)
    for words, longer in ROBOT_MODELS:
        for w in words:
            if re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", text):
                return round(longer / 2, 3), w
    return None


def robot_radius_of(answer: str) -> float | None:
    """The radius for an answer to the robot question: an option, a machine's name or a size in words
    (「幅 50cm」「半径 0.25 m」). None = not known (the default is used and stays a guess)."""
    t = (answer or "").strip()
    for label, r in ROBOT_SIZES:
        if t == label or (t and t == label.split("（")[0]):
            return r
    if got := robot_from("ロボット " + t):
        return got[0]
    m = _BARE.search(_norm(t))
    return _radius(*m.groups()) if m else None


def get(name: str) -> Purpose:
    p = by_label(name)
    if p is None:
        raise ValueError(f"unknown purpose {name!r}; one of {', '.join(ORDER)}")
    return p
