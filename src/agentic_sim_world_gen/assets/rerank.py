"""Cheap, deterministic re-ranking of search hits by metadata match.

Vector search alone returns things like a sofa for "office desk". We score each hit by how well
the query (and its English synonyms) match the asset's semantic class, VLM tags, caption and name,
then sort by that score, keeping the search order as the tie-breaker. The calling agent makes the
final call (it can look at thumbnails with view_candidates).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from agentic_sim_world_gen.assets import cards
from agentic_sim_world_gen.assets.record import AssetRecord

WEAK = 1.0  # below this the head noun of no query was found in class/tags/caption
# score of the best way a query names the asset, plus `modifier` per modifier word found, times `synonym` for
# alt_queries. scripts/search_eval/choices.py --grid tries other values against the logged choices.
WEIGHTS = {"phrase": 3.0, "compound": 2.75, "class": 2.5, "other_kind": 0.9, "part": 0.6, "tag": 1.5, "caption": 1.0,
           "name": 0.5, "modifier": 0.25, "shape": 0.5, "synonym": 0.9}
# nouns a tag uses for a part of the thing the class names: a refrigerator's tag "double door" or a
# cabinet's "hinged doors" say what it has, so they lose to a real door (even of another kind) as a door
PART_NOUNS = frozenset({"door", "drawer", "handle", "knob", "lid", "hinge", "caster"})


def _sing(w: str) -> str:
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 4 and w.endswith("ves"):
        return w[:-3] + "f"
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def words(text: str | None) -> list[str]:
    return [_sing(w) for w in re.findall(r"[a-z0-9]+", (text or "").lower())]


# words that may follow the noun of a phrase: "desk with drawers" names a desk, "desk lamp" does not
_BEFORE_PLACE = {"under", "beside", "near", "behind", "above", "below", "over", "next", "for", "on", "in", "at", "by"}
# "cabinet with drawers": what follows names a part or feature of the thing, not the thing
_PARTS = {"with", "featuring", "having"}
_FILLER = {"a", "an", "the", "and", "or", "some"}
_AFTER_NOUN = {"with", "and", "or", "for", "in", "on", "of", "from", "by", "to", "at", "that", "featuring", "having"}


_DIGITS = {w: str(i) for i, w in enumerate(
    ["one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve"], 1)}
_NUMBERS = set(_DIGITS)


def _num(w: str) -> bool:
    return w.isdigit() or w in _NUMBERS


def _measure(w: str) -> bool:
    """A count or size in one token: "6", "600ml", "3pc", "x2", "2x"."""
    return w in _NUMBERS or bool(re.fullmatch(r"\d+[a-z]*|x\d+", w))


_UNITS = {"pk": "pack", "pc": "piece"}  # "6pk" is "6pack"


def _qty(n: str, unit: str = "") -> str:
    """One token for a count and its unit, however it was written: "6 pack", "six pack", "pack of 6", "6pk" -> "6pack"."""
    return _DIGITS.get(n, n) + _UNITS.get(unit, unit)


def phrases(rec: AssetRecord) -> tuple[list[str], list[list[str]]]:
    """The asset's class and tags as the query is read. "X with Y" is kept as written (class "rack with shelves" is a
    shelf, "desk with drawers" was always found for "drawers") and also read as "Y X" like the query ("cabinet with
    drawers" -> "drawer cabinet"), so a tag that names the query whole still names it."""
    def both(tw: list[str]) -> list[list[str]]:
        kept = noun_last(tw, join=True, parts=False)
        alt = noun_last(tw, join=True)
        return [kept] if alt == kept else [kept, alt]

    cls = both(words(rec.semantic_class) or words(rec.raw.get("kind")))
    return cls[0], [*cls[1:], *(p for t in rec.tags for p in both(words(t)))]


def noun_last(tw: list[str], join: bool = False, parts: bool = True) -> list[str]:
    """Move trailing quantity phrases, and what follows "with" (featuring, having), to the front so the last
    word is the object noun: "cabinet with drawers" -> "drawer cabinet",
    "sofa 3 seat" -> "3 seat sofa", "water bottle pack of 6" -> "6 pack water bottle", "chair x2" -> "x2 chair".
    A count alone ("2 chairs") keeps its noun. With join=True each moved quantity is one token
    ("6pack water bottle"), so "beer can 6pack" and "beer can 6 pack" read the same. parts=False leaves "with" in
    place: an asset's class "rack with shelves" is also a shelf (see phrases())."""
    k = next((i for i, w in enumerate(tw) if w in _PARTS and 0 < i < len(tw) - 1), None) if parts else None
    if k is not None:  # "cabinet with drawers" -> "drawer cabinet": the head noun last as everywhere else
        tail = [w for w in tw[k + 1:] if w not in _FILLER and w not in _PARTS]
        if join:  # "sofa with 3 seats" -> "3seat sofa"
            out, i = [], 0
            while i < len(tail):
                if i + 1 < len(tail) and _num(tail[i]) and not _num(tail[i + 1]):
                    out.append(_qty(tail[i], tail[i + 1]))
                    i += 2
                else:
                    out.append(tail[i])
                    i += 1
            tail = out
        return [*tail, *noun_last(tw[:k], join)]
    tw, front = list(tw), []
    while len(tw) > 1:
        if len(tw) > 3 and tw[-2] == "of" and _num(tw[-1]):  # "pack of 6", "set of 4"
            q, tw = [tw[-1], tw[-3]], tw[:-3]
        elif len(tw) > 2 and _num(tw[-2]):  # "3 seat", "3 pack", "three seater"
            q, tw = tw[-2:], tw[:-2]
        elif _measure(tw[-1]):  # "600ml", "x2"
            q, tw = [tw[-1]], tw[:-1]
        else:
            break
        if join:
            m = re.fullmatch(r"(\d+)([a-z]*)", q[0]) if len(q) == 1 else None
            q = [_qty(*m.groups()) if m else _qty(*q) if len(q) == 2 else q[0]]
        front = [*q, *front]
    return [*front, *tw]


def _named_at(seq: list[str], sub: list[str]) -> int:
    """Index where `sub` occurs in `seq` as the noun of a phrase, not as a modifier of a following
    noun ("desk" names "white desk with drawers" but not "desk lamp" or "desk chair"); -1 if nowhere."""
    n = len(sub)
    for i in range(len(seq) - n + 1):
        if i > 0 and seq[i - 1] in _BEFORE_PLACE:  # "under desk" is where it goes, not what it is
            continue
        if seq[i : i + n] == sub and (i + n == len(seq) or seq[i + n] in _AFTER_NOUN):
            return i
    return -1


def _names(seq: list[str], sub: list[str]) -> bool:
    return _named_at(seq, sub) >= 0


# what a thing is made of or its color: "metal locker" asks for a locker, not a kind of locker, so a "storage
# locker" made of metal is one (the rebuilt kind table had no metal locker left)
_LOOKS = frozenset("""metal metallic steel stainless iron aluminum aluminium chrome brass copper wood wooden timber oak
    pine walnut birch bamboo plastic glass ceramic porcelain leather fabric velvet marble stone concrete rattan wicker
    white black gray grey silver gold red blue green yellow orange pink purple brown beige cream ivory""".split())


# the shape or build of a thing: "oval table" asks for a table, so a round table is one and a rectangular
# desk is a worse stand-in. Each word asked for is matched by its group and counts against the opposite group.
_SHAPE_GROUPS = {
    "round": frozenset({"round", "circular", "oval", "elliptical", "ellipse", "ovoid"}),
    "angular": frozenset({"rectangular", "rectangle", "square", "oblong", "boxy"}),
    "soft": frozenset({"upholstered", "upholstery", "padded", "cushioned", "tufted", "fabric", "velvet", "plush",
                       "leather"}),
    "glazed": frozenset({"glass", "glazed", "glazing", "vitrine"}),
}
_SHAPE_OPPOSITE = {"round": "angular", "angular": "round"}
_SHAPE_OF = {w: g for g, ws in _SHAPE_GROUPS.items() for w in ws}
_SHAPES = frozenset(_SHAPE_OF)
_NOT_KIND = _LOOKS | _SHAPES  # words that say how a thing looks, not what kind it is


def _shape_score(mods: list[str], everything: set[str], whole: set[str]) -> tuple[float, list[str]]:
    """Plus WEIGHTS["shape"] per shape word asked for that the asset has (or a word of its group), minus as much
    when it names the opposite shape instead (a rectangular desk for an "oval table"), minus WEIGHTS["modifier"]
    when it says neither, or both. A round / angular shape is the whole thing's: `whole` leaves out the caption
    after its noun ("a rectangular table with round legs" is no round table); fabric or glass may be a part."""
    s, why = 0.0, []
    for g in dict.fromkeys(_SHAPE_OF[m] for m in mods if m in _SHAPE_OF):
        asked = next(m for m in mods if _SHAPE_OF.get(m) == g)
        opp = _SHAPE_OPPOSITE.get(g)
        has = whole if opp else everything
        said = sorted(_SHAPE_GROUPS[opp] & has) if opp else []
        if _SHAPE_GROUPS[g] & has and not said:
            s += WEIGHTS["shape"]
            why.append(f"shape '{asked}'")
        elif said and not _SHAPE_GROUPS[g] & has:
            s -= WEIGHTS["shape"]
            why.append(f"shape: {said[0]}, not {asked}")
        else:  # nothing says which shape it is: a little below one that says the shape asked for
            s -= WEIGHTS["modifier"]
            why.append(f"shape '{asked}' not found")
    return s, why


def _other_kind(seq: list[str], head: str, mods: list[str]) -> bool:
    """`seq` names `head` with modifiers of its own, none of which the query has: for "dining table",
    "coffee table" is another kind of table, while plain "table" or "dining table" is not."""
    i = _named_at(seq, [head])
    if i < 0 or not mods:
        return False
    own = seq[:i]
    for j in range(len(own) - 1, -1, -1):  # "rack with storage shelves" -> "storage"
        if own[j] in _AFTER_NOUN:
            own = own[j + 1 :]
            break
    # a "wooden pallet" is no other kind of pallet, a "round table" no other kind of table
    own = [w for w in own if w not in _NOT_KIND and not _measure(w)]
    return bool(own) and not set(own) & set(mods)


def _a_part(cap: list[str], head: str) -> str | None:
    """What the caption says the asset is when it names `head` only as a part of it: "UV-C disinfection robot
    with vertical lamp tower" is a robot, not a lamp, though its class says lamp."""
    lead = _lead(cap)
    rest = cap[len(lead):]
    # only "with ...": "six pack of beer cans" is cans, "robot with a lamp tower" is no lamp
    if lead and rest and rest[0] in _PARTS and lead[-1] != head and head not in lead and head in rest:
        return lead[-1]
    return None




def _lead(seq: list[str]) -> list[str]:
    """The leading noun phrase of a caption: "black office chair for a desk" -> "black office chair"."""
    for i, w in enumerate(seq):
        if w in _AFTER_NOUN:
            return seq[:i]
    return seq


@dataclass
class Match:
    score: float = 0.0
    matched: list[str] = field(default_factory=list)

    @property
    def weak(self) -> bool:
        return self.score < WEAK


def score(rec: AssetRecord, query: str, alt_queries: list[str] | None = None) -> Match:
    # no USD class: a local asset's asset.json slug names the thing just as well ("croissant", "curry bread")
    # class and tags get the same word order as the query: tag "beer can 6pack" names a beer can like "beer can 6 pack"
    cls, tags = phrases(rec)
    cap = words(rec.caption)
    name = words(re.sub(r"[_\-.]|(?<=[a-z])(?=[A-Z])", " ", rec.name))  # also split camelCase
    everything = set(cls) | set(cap) | set(name) | {w for t in tags for w in t}
    card = cards.of(rec) or {}
    everything |= {w for t in [*(card.get("colors") or []), *(card.get("materials") or [])] for w in words(t)}
    whole = everything - set(cap) | set(_lead(cap))  # the caption's own noun phrase, not its parts
    # what a look at the asset said it is: its names, each with the noun last
    known = [noun_last(words(t), join=True) for t in [card.get("kind"), *(card.get("also") or [])] if t]
    known = [k for k in known if k]

    W = WEIGHTS
    best = Match()
    # every modifier of the query and its synonyms: "floor lamp" / "standing lamp" both make a tag
    # "standing lamp" the same kind, while "table lamp" stays another kind
    all_mods = {w for t in [query, *(alt_queries or [])] for w in noun_last(words(t), join=True)[:-1]
                if not _measure(w) and w not in _NOT_KIND}
    # heads asked for with modifiers: a bare synonym "lamp" next to "floor lamp" would let any lamp, bulb or
    # lantern in as a good match and hide that no floor lamp was found
    specific = {tw[-1] for tw in (noun_last(words(t), join=True) for t in [query, *(alt_queries or [])])
                if any(not _measure(w) for w in tw[:-1])}
    for i, term in enumerate([query, *(alt_queries or [])]):
        tw = noun_last(words(term), join=True)  # "sofa 3 seat" is a sofa
        if not tw:
            continue
        head, mods = tw[-1], tw[:-1]
        if not mods and head in specific:
            continue
        # "floor" in "floor lamp"; not "3seat" in "sofa 3 seat" nor "metal" in "metal locker"
        kinds_asked = [m for m in mods if not _measure(m) and m not in _NOT_KIND]
        # The head noun must name the thing: class "desk lamp" or tag "desk chair" is not a desk.
        if len(tw) > 1 and (_names(cls, tw) or any(_names(t, tw) for t in tags)):
            s, why = W["phrase"], f"'{term}' in class/tags"
        elif tail := next((tw[-k:] for k in range(len(tw) - 1, 1, -1)
                           if _names(cls, tw[-k:]) or any(_names(t, tw[-k:]) for t in tags)), None):
            # "ceiling hung digital signage" -> tag "digital signage": a compound noun beats a bare head
            s, why = W["compound"], f"'{' '.join(tail)}' in class/tags"
        elif _names(cls, [head]):
            s, why = W["class"], f"'{head}' in class"
            named = [t for t in tags if _names(t, [head]) and t != [head]]  # a bare tag "lamps" says no kind
            if _other_kind(cls, head, kinds_asked):  # "vending machine" -> class "recharge machine"
                s, why = W["other_kind"], f"class '{' '.join(cls)}' is another kind of {head}"
            elif what := _a_part(cap, head):  # a robot with a lamp tower is no lamp
                s, why = W["other_kind"], f"caption: a {what} with a {head}"
            elif kinds_asked and cls == [head] and named and all(_other_kind(t, head, list(all_mods)) for t in named):
                # bare class "lamp", tags "table lamp" / "ceiling lamp": no floor lamp
                s, why = W["other_kind"], f"tags '{' '.join(named[0])}' name another kind of {head}"
        elif any(_names(t, [head]) and not _other_kind(t, head, kinds_asked) for t in tags):
            s, why = W["tag"], f"'{head}' in tags"
            named = [t for t in tags if _names(t, [head]) and t != [head]]
            if kinds_asked and named and all(_other_kind(t, head, list(all_mods)) for t in named):
                s, why = W["other_kind"], f"tags '{' '.join(named[0])}' name another kind of {head}"
        elif (lead := _lead(cap)) and lead[-1] == head:
            s, why = W["caption"], f"'{head}' in caption"
        elif head in name or head in cls:
            s, why = W["name"], f"'{head}' only in name or as a modifier"
        else:
            s, why = 0.0, ""
        if (head in PART_NOUNS and s in (W["phrase"], W["compound"], W["tag"]) and cls and not _names(cls, [head])
                and not any(_names(k, [head]) for k in known or [])):
            s, why = W["part"], f"'{head}' in tags is a part of a {' '.join(cls)}"  # the class names another thing
        shape, shape_why = _shape_score(mods, everything, whole)
        plain = [m for m in mods if m not in _SHAPES]
        syn = W["synonym"] if i > 0 else 1.0  # synonyms count slightly less than the main query
        used = s not in (0.0, W["other_kind"], W["part"])
        if s not in (W["other_kind"], W["part"]):  # another kind stays a substitute, whatever modifier words it also has
            s += W["modifier"] * sum(m in everything for m in plain)
        s *= syn
        if used:  # the shape ranks matches; it never makes one weak (a table of another shape is still a table)
            s = max(s + shape * syn, WEAK) if s >= WEAK else s + shape * syn
        if known:  # the card wins over class, tags and caption, in both directions
            same = next((k for k in known if k[-1] == head), None)
            if same is None:
                if s >= WEAK:  # labelled as the thing asked for, but the look says it is something else
                    s, why, used = W["other_kind"], f"card: it is a {card['kind']}", False
            elif kinds_asked and _other_kind(same, head, kinds_asked) and not any(_names(k, tw) for k in known):
                if s >= WEAK:
                    s, why, used = W["other_kind"], f"card: a {' '.join(same)}, another kind of {head}", False
            else:  # the look says it is one: as good as a class that names it
                s2 = W["phrase"] if len(tw) > 1 and any(_names(k, tw) for k in known) else W["class"]
                s2 = (s2 + W["modifier"] * sum(m in everything for m in plain)) * syn
                s2 = max(s2 + shape * syn, WEAK)
                if s2 > s:
                    s, why, used = s2, f"card: a {' '.join(same)}", True
                elif s == W["other_kind"]:
                    used = False
        if s > best.score:
            best = Match(round(s, 3), ([why] + (shape_why if used else [])) if why else [])
    return best


def rerank(recs: list[AssetRecord], query: str, alt_queries: list[str] | None = None) -> list[tuple[AssetRecord, Match]]:
    scored = [(r, score(r, query, alt_queries), i) for i, r in enumerate(recs)]
    scored.sort(key=lambda x: (-x[1].score, x[2]))
    return [(r, m) for r, m, _ in scored]
