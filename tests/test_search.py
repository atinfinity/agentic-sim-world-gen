"""Searches sent at once and answers kept for a day."""

import os
import threading
import time

import pytest

from agentic_sim_world_gen import service
from agentic_sim_world_gen.assets.providers import simready_central as sc
from agentic_sim_world_gen.assets.providers.base import SizeFilter
from agentic_sim_world_gen.assets.record import AssetRecord
from agentic_sim_world_gen.scene.spec import ObjectSpec


def hit(name):
    return {"id": name, "score": 1.0, "source": {"path": f"hf://huggingface.co/datasets/x/{name}.usd", "name": f"{name}.usd",
                                                 "usd_dimensions": {"bbox_max_x": 1, "bbox_max_y": 1, "bbox_max_z": 1}}}


class Session:
    def __init__(self, hits=("desk_a",)):
        self.posts, self.hits = 0, list(hits)

    def post(self, url, data=None, headers=None, timeout=None):
        self.posts += 1
        me = self

        class R:
            def raise_for_status(self): pass
            def json(self): return {"hits": [hit(h) for h in me.hits]}
        return R()


def test_an_answer_is_kept_and_reused():
    s = Session()
    p = sc.SimReadyCentralProvider(session=s)
    a = p.search("desk", limit=8)
    b = p.search("desk", limit=8)
    assert s.posts == 1 and [r.name for r in a] == [r.name for r in b] == ["desk_a.usd"]
    assert b[0].raw["source"]["name"] == "desk_a.usd"  # the whole hit, as rerank reads it
    p.search("desk", limit=12)  # another limit, another size: other questions
    p.search("desk", limit=8, size=SizeFilter(max_z=1.0))
    assert s.posts == 3
    assert sc.SimReadyCentralProvider(session=s).search("desk", limit=8) and s.posts == 3  # another process too


def test_not_kept_when_off_old_or_empty(monkeypatch):
    s = Session()
    p = sc.SimReadyCentralProvider(session=s)
    p.search("desk")
    f = next(sc._cache_dir().glob("*.json.gz"))
    os.utime(f, (time.time() - 25 * 3600,) * 2)
    p.search("desk")
    assert s.posts == 2  # a day old: asked again
    monkeypatch.setenv("AGENTIC_SIM_WORLD_GEN_SEARCH_CACHE_HOURS", "0")
    p.search("desk")
    assert s.posts == 3
    monkeypatch.delenv("AGENTIC_SIM_WORLD_GEN_SEARCH_CACHE_HOURS")
    e = Session(hits=())
    q = sc.SimReadyCentralProvider(session=e)
    assert q.search("zebra") == [] and q.search("zebra") == [] and e.posts == 2  # an empty answer is not kept


class Slow:
    name = "slow"

    def __init__(self, fail=()):
        self.calls, self.threads, self.fail = [], set(), set(fail)

    def search(self, query, *, limit=10, size=None):
        self.calls.append(query)
        self.threads.add(threading.get_ident())
        time.sleep(0.05)
        if query in self.fail:
            raise RuntimeError("down")
        w = query.split()[-1]
        return [AssetRecord(id=f"{query}-{i}", source="t", uri=f"/a/{w}_{i}.usd", name=f"{w}_{i}.usd", bbox_min=(0, 0, 0),
                            bbox_max=(1, 1, 1), semantic_class=w, caption=f"A {w}") for i in range(3)]


OBJS = [ObjectSpec(id=f"o{i}", query=q, alt_queries=[f"big {q}", f"small {q}"])
        for i, q in enumerate(["desk", "chair", "sofa", "lamp", "desk"])]


def test_searches_of_a_call_go_out_at_once(monkeypatch):
    p = Slow()
    monkeypatch.setattr(service, "get_provider", lambda: p)
    plain = [service._find_candidates(o, 4) for o in OBJS]
    assert len(p.calls) == 15 and len(p.threads) == 1
    p.calls.clear()
    t0 = time.monotonic()
    with service._searching(OBJS, 4):
        fast = [service._find_candidates(o, 4) for o in OBJS]
        took = time.monotonic() - t0
    assert fast == plain
    assert sorted(p.calls) == sorted({q for o in OBJS for q in [o.query, *o.alt_queries]})  # 12: each question once
    assert len(p.threads) > 2 and took < 12 * 0.05
    assert service._FOUND.get() is None
    p.calls.clear()
    monkeypatch.setenv("AGENTIC_SIM_WORLD_GEN_SEARCH_WORKERS", "1")
    with service._searching(OBJS, 4):
        assert [service._find_candidates(o, 4) for o in OBJS] == plain
    assert len(p.calls) == 15  # one after another, as before


def test_a_search_that_fails_ahead_fails_where_it_is_used(monkeypatch):
    p = Slow(fail={"big sofa"})
    monkeypatch.setattr(service, "get_provider", lambda: p)
    with service._searching(OBJS, 4):
        assert service._find_candidates(OBJS[0], 4)
        with pytest.raises(RuntimeError):
            service._find_candidates(OBJS[2], 4)
