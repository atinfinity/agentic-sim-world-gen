"""What happens in the place: who, how many, doing what. A user without a picture of the room cannot say
its size or how many desks, but can say 「4 人で会議、画面共有あり」; the size, the main furniture and the counts
follow from that (`derive`), recorded as guesses with the basis 「活動から」 so the user can correct them.
What a robot does there is the purpose (scene/purpose.py)."""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

BASIS = "活動から"
ASPECT = 4 / 3  # width : depth of the derived room
QUESTION = "誰が・何人で・何をする場所ですか？"
WHY = "広さ・主な家具・その数をここから逆算する（derive_activity）"


@dataclass(frozen=True)
class Thing:
    label: str  # Japanese
    query: str  # English search query
    per: float | None = None  # people per piece (count = ceil(people / per)); None = `count` pieces
    count: int = 1

    def number(self, people: int) -> int:
        return self.count if self.per is None else max(1, math.ceil(people / self.per))


@dataclass(frozen=True)
class Extra:
    label: str
    words: tuple[str, ...]
    thing: Thing
    area: float = 0.0  # m² it adds


@dataclass(frozen=True)
class Activity:
    name: str
    label: str  # Japanese with {n}: the question's option
    words: tuple[str, ...]  # words / regexes in the request or the answer that say it
    people: int  # when nobody said how many
    base: float  # m² with nobody in it (walking space, the door, what stands along the walls)
    per_person: float  # m² each person adds
    things: tuple[Thing, ...]
    extras: tuple[Extra, ...] = field(default=())
    shown: tuple[str, ...] = field(default=())  # extras named in the option (「画面共有あり」)

    def option(self, people: int | None = None) -> str:
        n = people or self.people
        more = "".join(f"（{e.label}あり）" for e in self.extras if e.label in self.shown)
        return self.label.format(n=n) + more


_SCREEN = Extra("画面共有", ("画面共有", "モニター", "モニタ", "ディスプレイ", "プレゼン", "投影", "プロジェクタ", "スクリーン",
                             "テレビ会議", "web会議", "オンライン会議", "screen", "monitor", "display"),
                Thing("モニター", "large display monitor on stand"), 1.0)
_BOARD = Extra("ホワイトボード", ("ホワイトボード", "白板", "whiteboard"), Thing("ホワイトボード", "whiteboard"), 1.0)

OFFICE = (
    Activity("deskwork", "{n}人でデスクワーク",
             ("デスクワーク", "執務", "事務作業", "事務仕事", "働く", "仕事をする", "自席", "desk work"),
             8, 0.0, 6.0,  # 6 m² a person: the desk, the aisle behind it and a share of the storage
             (Thing("机", "office desk", 1), Thing("椅子", "office chair", 1), Thing("キャビネット", "office cabinet", 4)),
             (_BOARD,)),
    Activity("meeting", "{n}人で会議", ("会議", "打ち合わせ", "打合せ", "ミーティング", "meeting"),
             4, 3.0, 2.0,  # a seat at the table 2 m² and the way round it
             (Thing("会議テーブル", "conference table", 8), Thing("椅子", "office chair", 1)),
             (_SCREEN, _BOARD), ("画面共有",)),
    Activity("reception", "来客を入れて{n}人で応接", ("応接", "来客", "商談", "接客", "reception"),
             4, 6.0, 2.0,
             (Thing("ソファ", "armchair", 1), Thing("ローテーブル", "coffee table"))),
)

HOME = (
    Activity("relax", "{n}人でくつろぐ（テレビ・ソファ）",
             ("くつろ", "寛", "団らん", "団欒", "リラックス", "テレビを見", "映画を見", "リビング", "居間", "living room"),
             2, 9.0, 2.5,
             (Thing("ソファ", "sofa", 3), Thing("ローテーブル", "coffee table"), Thing("テレビ台", "tv stand"),
              Thing("テレビ", "television"))),
    Activity("telework", "{n}人で在宅ワーク・勉強",
             ("在宅", "テレワーク", "リモートワーク", "勉強", "書斎", "仕事部屋", "作業部屋", "study", "home office"),
             1, 7.0, 3.0,
             (Thing("机", "desk", 1), Thing("椅子", "office chair", 1), Thing("本棚", "bookshelf"))),
    Activity("dining", "{n}人で食事", ("食事", "食べる", "ごはん", "ご飯", "ダイニング", "dining"),
             4, 5.0, 2.0,
             (Thing("ダイニングテーブル", "dining table", 6), Thing("椅子", "dining chair", 1))),
    Activity("sleep", "{n}人で寝る", ("寝る", "眠る", "就寝", "寝室", "ベッドルーム", "bedroom"),
             1, 7.0, 3.0,
             (Thing("ベッド", "bed", 2), Thing("ナイトテーブル", "nightstand"), Thing("ワードローブ", "wardrobe"))),
)

SCHOOL = (
    Activity("lecture", "{n}人が授業を受ける", ("授業", "講義", "講習", "セミナー", "教室", "lecture", "classroom"),
             30, 12.0, 1.5,
             (Thing("生徒の机", "school desk", 1), Thing("椅子", "school chair", 1), Thing("教卓", "teacher desk"),
              Thing("黒板", "blackboard")), (_SCREEN,)),
    Activity("groupwork", "{n}人でグループワーク", ("グループワーク", "班", "実習", "ワークショップ", "workshop"),
             24, 12.0, 2.2,
             (Thing("テーブル", "table", 4), Thing("椅子", "chair", 1), Thing("ホワイトボード", "whiteboard")), (_SCREEN,)),
)

LAB = (
    Activity("experiment", "{n}人で実験・測定", ("実験", "測定", "分析", "計測", "experiment"),
             4, 10.0, 5.0,
             (Thing("実験台", "laboratory bench", 2), Thing("スツール", "lab stool", 1), Thing("薬品・器具の棚", "lab cabinet"))),
    Activity("development", "{n}人で機器・ロボットの開発", ("開発", "試作", "組立", "組み立て", "デバッグ", "prototyp"),
             4, 12.0, 5.0,  # the base leaves a patch of free floor to try the machine on
             (Thing("作業台", "workbench", 2), Thing("椅子", "office chair", 1), Thing("工具の棚", "tool shelf"))),
)

TABLE: dict[str, tuple[Activity, ...]] = {"office": OFFICE, "home": HOME, "school": SCHOOL, "lab": LAB}
# the place's questions the activity answers (scene/questions.py keys)
ANSWERS = ("size", "people")

_KANJI = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_PEOPLE = re.compile(r"([0-9]+|[一二三四五六七八九十])\s*(?:人|名)")
_WORDS_PEOPLE = (("一人暮らし", 1), ("ひとり", 1), ("ふたり", 2), ("夫婦", 2), ("家族", 4))


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").lower()


def _hit(words: tuple[str, ...], text: str) -> str | None:
    for w in words:
        m = re.search(w if "\\" in w else re.escape(_norm(w)), text)
        if m:
            return m.group(0)
    return None


def for_place(place: str | None) -> tuple[Activity, ...]:
    return TABLE.get(place or "", ())


def people_in(text: str) -> tuple[int, str] | None:
    """(how many, the words) when the text says how many people."""
    t = _norm(text)
    if m := _PEOPLE.search(t):
        n = _KANJI.get(m.group(1)) or int(m.group(1))
        return (n, m.group(0)) if 1 <= n <= 500 else None
    for w, n in _WORDS_PEOPLE:
        if w in t:
            return n, w
    return None


def read(place: str | None, text: str) -> tuple[Activity, str] | None:
    """(the activity, the words that say it) when the text names one of the place's activities."""
    t = _norm(text)
    best = None
    for a in for_place(place):
        if a.option() == text.strip() or a.name == t.strip():
            return a, a.name
        m = None
        for w in a.words:
            m = re.search(w if "\\" in w else re.escape(_norm(w)), t)
            if m:
                break
        if m and (best is None or m.start() < best[2]):  # the first named: 「会議もできる執務室」 is not decided here
            best = (a, m.group(0), m.start())
    return (best[0], best[1]) if best else None


def room_for(area: float) -> tuple[float, float]:
    """(width, depth) [m] of a 4:3 room of the area, to 0.1 m."""
    w = round(math.sqrt(area * ASPECT), 1)
    return w, round(area / w, 1)


def derive(place: str | None, text: str, people: int | None = None) -> dict[str, Any] | None:
    """The room and the main furniture for the activity `text` says (an option of the question, or free words:
    「6人で会議、ホワイトボードも」). None when the place has no activities or the text names none."""
    got = read(place, text)
    if got is None:
        return None
    a, said = got
    said_n = people_in(text)
    n = people or (said_n[0] if said_n else a.people)
    t = _norm(text)
    extras = [e for e in a.extras if _hit(e.words, t) or (text.strip() == a.option(n) and e.label in a.shown)]
    area = round(a.base + a.per_person * n + sum(e.area for e in extras), 1)
    w, d = room_for(area)
    label = a.label.format(n=n) + "".join(f"、{e.label}あり" for e in extras)
    basis = f"{BASIS}（{label}）"
    things = [(th, th.number(n)) for th in a.things] + [(e.thing, 1) for e in extras]
    rows = [{"item": "activity", "value": label, "given": True, "basis": "指示どおり"},
            {"item": "people", "value": f"{n}人", "given": bool(people or said_n),
             "basis": "指示どおり" if (people or said_n) else f"{BASIS}（この活動の既定の人数）"},
            {"item": "size", "value": f"{w:g} × {d:g} m（約 {area:g} m²）", "given": False, "basis": basis},
            *({"item": f"{th.label}の数", "value": f"{k}", "given": False, "basis": basis} for th, k in things)]
    return {"activity": a.name, "label": label, "said": said, "people": n, "people_given": bool(people or said_n),
            "extras": [e.label for e in extras], "area": area, "room": {"width": w, "depth": d},
            "objects": [{"label": th.label, "query": th.query, "count": k} for th, k in things],
            "assumptions": rows,
            "hint": ("use `room` unless the user gave a size, and `objects` (count pieces of each: an array or "
                     "separate objects) as the main furniture - what the user named wins; after create_scene record "
                     "`assumptions` with set_assumptions as they are (basis 「活動から」: guesses the user can correct)")}


def question(place: str | None, people: int | None = None) -> dict[str, Any] | None:
    """The activity question for the place (None when it has none): the options carry a number of people."""
    acts = for_place(place)
    if not acts:
        return None
    options = [a.option(people) for a in acts]
    return {"key": "activity", "item": "activity", "question": QUESTION, "options": options, "default": options[0],
            "why": WHY}
