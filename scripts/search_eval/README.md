# Asset search evaluation

35 queries (Japanese + English translation + synonyms), 15 search systems, judged 0/1/2 by looking at the thumbnails.

```bash
python scripts/search_eval/score.py --per-query   # numbers from the committed data, no network
python scripts/search_eval/score_a2.py --per-query  # A (before) vs A2 (candidate filtering), same data
```

A2 = A after the candidate filtering (head noun must name the asset, weak hits dropped when a good one exists).
`PYTHONPATH=src python scripts/search_eval/run_a2.py` re-runs it (network) into `data/runs_a2.json`.
`data/grades_a2.json` grades the 14 A2 top-5 hits nobody had judged (all 0, from name + caption, not blind).
`PYTHONPATH=src python scripts/search_eval/run_size.py` (network) compares the pipeline without and with the typical-size
size ranges (`assets/sizes.py`) on one memoized search per query; `data/grades_size.json` grades the 20 top-5 hits of
either run nobody had judged (from name + caption, not blind).
`PYTHONPATH=src python scripts/search_eval/run_image.py` (network, the `taste` extra and SigLIP2 in the HF cache, CPU)
marks candidates weak when their thumbnail is unlike the query text and writes `data/runs_image.json`;
`python scripts/search_eval/score_image.py [--per-query abs:0.08]` scores it offline. `data/grades_image.json` grades the 47
top-5 hits nobody had judged (from name + caption, not blind).

Choices: every `select_asset` appends the query, alt_queries, place, size bounds, the candidate order, the
index before / after, who (agent / preview), `correction` and `reason` to the scene's `choices.jsonl`. Swaps that say the
ranking was wrong (preview picks, corrections, reason `"match"`) become an evaluation set; swaps for looks, variety or
space are left out.

```bash
python scripts/search_eval/choices.py collect ~/agentic_sim_world_gen_scenes out [--prefs ~/.config/agentic-sim-world-gen/preferences.json]
PYTHONPATH=src python scripts/search_eval/choices.py score          # where each chosen asset ranks now (network)
PYTHONPATH=src python scripts/search_eval/choices.py score --grid   # rerank.WEIGHTS one at a time, plus P@1 on the 35
```

`data/choices.json` is the merged set (queries and asset names / uris only, home as `~`). It starts with two seeds;
do not tune `rerank.WEIGHTS` on it until it holds a few dozen choices from different scenes.

`data/`
- `queries.json`: `{id, ja, q, alt, rel}`. `rel` is what counts as a hit.
- `runs.json`: `{system: {query id: [asset name or URL, ...]}}` (top 10; F has top 5).
- `choices.json`: `{query, alt_queries, place, min_size, max_size, uri, name, rank_then, count, sources}`.
- `grades.json`: `{query id: {asset name or URL: 0|1|2}}`. 2 = that object, 1 = acceptable substitute, 0 = wrong.
  Pooled top 5 of every system, shuffled so the judge could not see which system found what.

Systems
- A: current pipeline (`service._find_candidates`: query + alt_queries over SimReady Central + Isaac index, `rerank.py`).
- B: SimReady Central `search_hybrid` as is (that service is NVIDIA USD Search: SigLIP2 + keyword).
- C: SigLIP2 (`google/siglip2-so400m-patch14-384`) text → thumbnail. `_alt` = mean of query + synonyms.
- D: `intfloat/multilingual-e5-large` over class + caption + tags + name words.
- E1: RRF(A, C_alt, D_alt). E2: `rerank.py` over RRF(C_alt, D_alt).
- F: NVIDIA's public USD Search (`search.simready.omniverse.nvidia.com`, Isaac Sim 6.2 content) with the quickstart settings (`f_probe.json`).
- `*_ja`: the Japanese query without translation.

Re-running from scratch (needs network, a GPU for `embed.py`, and `torch transformers` in a separate venv — not the project
venv, not Isaac Sim's Python). `E` is a work directory holding `queries.json` and `simready_all.json.gz`
(every SimReady Central hit, from `search_hybrid` with a large `limit`):

```bash
PYTHONPATH=src python scripts/search_eval/build_corpus.py $E     # corpus.json + thumbs/
<venv with torch>/python scripts/search_eval/embed.py $E         # emb/*.npy
PYTHONPATH=src python scripts/search_eval/run_ab.py $E           # runs_ab.json
PYTHONPATH=src python scripts/search_eval/run_cde.py $E          # runs_cde.json
python scripts/search_eval/run_f.py $E                           # runs_f.json
python scripts/search_eval/sheets.py $E; python scripts/search_eval/sheets_f.py $E   # judging sheets
```
