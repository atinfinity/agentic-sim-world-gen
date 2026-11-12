"""Command line entry point (P0). P1 exposes the same functions as MCP tools.

  agentic-sim-world-gen search "office chair" [--limit 8] [--max-h 1.0]
  agentic-sim-world-gen resolve examples/p0_study_room.json -o out/study      # search + previews
  agentic-sim-world-gen preview out/study/scene.resolved.json                 # re-render previews after edits
  agentic-sim-world-gen compose out/study/scene.resolved.json [--localize]    # write scene.usda
  agentic-sim-world-gen check out/study                                       # Isaac Sim headless check (Ubuntu)
  agentic-sim-world-gen check out/study --live                                # ... in the Isaac Sim already open
  agentic-sim-world-gen fronts out/study                                      # each object from its front (Ubuntu)
  agentic-sim-world-gen photo out/living [--input photo]                      # photo vs render from its camera (Ubuntu)
  agentic-sim-world-gen fit-photos landmarks.json -o out/living               # room + cameras from several photos
  agentic-sim-world-gen frames walk.mp4 -o out/living [--max 8]               # sharp frames of a video -> photo inputs
  agentic-sim-world-gen video-poses out/living [--hfov 65]                    # turns between those frames (OpenCV)
  agentic-sim-world-gen sdg out/study --frames 50 [--coco] [--dry-run]         # Replicator synthetic data (Ubuntu)
  agentic-sim-world-gen index /data/usd_assets [--rebuild]                    # index a third-party USD folder
  agentic-sim-world-gen plan floor.dxf -o out/plan [--units mm] [--storey 1F]  # floor plan -> plan.json + plan.svg
  agentic-sim-world-gen prefs [--place home] [--forget lighting [--target place:home]]  # remembered preferences
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import threading

from agentic_sim_world_gen.scene.spec import SceneSpec

from agentic_sim_world_gen import service


def _print_status(st: dict) -> None:
    for o in st["objects"]:
        sel = o["candidates"][o["selected"]]["name"] if o["candidates"] else "(no candidate)"
        print(f"{o['id']}: {len(o['candidates'])} candidates for '{o['query']}' -> {sel}")
    print(f"preview page:          {st['previews']['page']}")
    print(f"preview(1) candidates: {st['previews']['candidates']}")
    print(f"preview(2) layout:     {st['previews']['layout']}")
    for i in st["issues"]:
        print(f"  ! [{i['kind']}] {', '.join(i['objects'])}: {i['detail']}")
    if not st["issues"]:
        print("  layout check: OK")
    for i in st.get("usability") or []:
        print(f"  ~ [{i['kind']}] {', '.join(i['objects'])}: {i['detail']}")
    _print_taste(st.get("taste"))
    for a in st.get("assumptions") or []:
        where = f"{a['object']}: " if a.get("object") else ""
        why = f" ({a['basis']})" if a.get("basis") else ""
        print(f"  {'=' if a['given'] else '?'} {where}{a['item']}: {a['value']}{why}")
    mark = {"met": "o", "unmet": "x", "unknown": "-"}
    for r in st.get("requirements") or []:
        how = "; ".join(c["detail"] for c in r["checks"]) or r.get("note") or ""
        print(f"  {mark[r['status']]} {r['text']}{': ' + how if how else ''}")


def _print_taste(t: dict | None) -> None:
    """`^` lines: chosen assets that clash with the rest and the candidates that fit better."""
    if not t:
        return
    if t.get("skipped"):
        print(f"  ^ [taste] skipped: {t['skipped']}")
    elif t.get("error"):
        print(f"  ^ [taste] error: {t['error']}")
    for r in t.get("outliers") or []:
        alts = ", ".join(f"[{'pool' if a.get('index') is None else a['index']}] {a['name']} ({a['fit']:.2f})"
                         for a in r.get("alternatives") or [])
        looks = f" looks {'/'.join(r['looks'])} vs {'/'.join(r.get('others_look') or [])}" if r.get("looks") else ""
        print(f"  ^ [taste] {r['object']}: {r['asset']} fit {r['fit']:.2f} (median {r['median_fit']:.2f}){looks}"
              + (f"; better: {alts}" if alts else ""))


def cmd_search(a: argparse.Namespace) -> int:
    mx = (a.max_w or 99.0, a.max_d or 99.0, a.max_h or 99.0) if (a.max_w or a.max_d or a.max_h) else None
    print(json.dumps(service.search_assets(a.query, a.limit, max_size=mx), ensure_ascii=False, indent=2))
    return 0


def cmd_questions(a: argparse.Namespace) -> int:
    from agentic_sim_world_gen.scene.questions import plan_questions

    out = plan_questions(a.request, a.place, a.known, a.limit, a.ask_first, a.purpose)
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    print(f"place: {out['place']}{' (guessed)' if out['place_guessed'] else ''}")
    for s in out["skipped"]:
        print(f"  = {s['item']}: {s['reason']}")
    if "no_questions" in out:
        print(f"  (no questions: {out['no_questions']})")
    for q in out.get("ask_now", []):
        print(f"ask now: {q['question']}  " + " / ".join(q["options"]))
    for i, q in enumerate(out["questions"], 1):
        print(f"{i}. {q['question']}  " + " / ".join(q["options"]) + f"  ({q['why']})")
    if out["questions"] and not a.ask_first and "ask_now" not in out:
        print("  (draft first: build with the first options, then ask while showing the draft)")
    return 0


def cmd_kinds(a: argparse.Namespace) -> int:
    for q in a.queries:
        r = service.kind_availability([q])
        if r is None:
            print(f"{q}: no source can tell")
            continue
        counts = ", ".join(f"{s} {n}" for s, n in r["counts"].items())
        others = ", ".join(f"{o['kind']} {o['assets']}" for o in r.get("other_kinds") or [])
        print(f"{'x' if r['absent'] else 'o'} {q} -> {r['kind']}: {counts}" + (f"  (other kinds: {others})" if others else ""))
    return 0


def cmd_missing(a: argparse.Namespace) -> int:
    from agentic_sim_world_gen import missing

    s = missing.summary(a.top, a.why)
    print(json.dumps(s, ensure_ascii=False, indent=2) if a.json else missing.text(s))
    return 0


def cmd_resolve(a: argparse.Namespace) -> int:
    spec = SceneSpec.load(a.spec)
    st = service.create_scene(spec, a.out, a.candidates, preferences=not a.no_preferences)
    _print_status(st)
    for p in st.get("preferences_applied") or []:
        print(f"  * preference {p['item']} ({p['target']}{', ' + p['object'] if p.get('object') else ''}): {p['value']}")
    return 0


def cmd_prefs(a: argparse.Namespace) -> int:
    if a.forget:
        out = service.forget_preference(a.forget, a.target)
        for f in out["forgotten"]:
            print(f"forgot {f['item']} ({f['target']})")
    else:
        out = service.get_preferences(a.place)
    print(f"file: {out['file']}")
    for p in out["defaults"] + out["pending"]:
        mark = "*" if p["default"] else " "
        print(f"{mark} {p['item']} ({p['target']}): {p.get('label') or p['value']}  x{p['count']}  {p['last_used']}")
    return 0


def cmd_preview(a: argparse.Namespace) -> int:
    _print_status(service.refresh(a.resolved))
    if a.open or a.serve:
        r = service.open_preview(a.resolved, browser=a.open)
        print(f"preview: {r['url']}" + (f"\n{r['hint']}" if r.get("hint") else ""))
        print("serving; the page reloads on every change (Ctrl-C to stop)")
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
    return 0


def cmd_compose(a: argparse.Namespace) -> int:
    r = service.compose_scene(a.resolved, a.out, a.localize)
    if r["localized_assets"]:
        print(f"localized {r['localized_assets']} assets")
    print(f"usd: {r['usd']}")
    return 0


def cmd_check(a: argparse.Namespace) -> int:
    r = service.check_in_isaac(a.scene, a.steps, gif=a.gif, live=a.live, orbit=a.orbit, draft=a.draft,
                               force=a.force)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0 if r.get("verdict") in ("passed", "draft") else 1


def cmd_fronts(a: argparse.Namespace) -> int:
    r = service.front_views(a.scene, a.around, ids=a.ids or None, fill=a.fill)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0 if r["ok"] else 1


def cmd_photo(a: argparse.Namespace) -> int:
    r = service.compare_photo(a.scene, a.input)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0 if r["ok"] else 1


def cmd_nav_map(a: argparse.Namespace) -> int:
    start = tuple(float(v) for v in a.start.split(",")) if a.start else None
    r = service.nav_map(a.scene, a.radius, a.height, a.min_height, a.resolution, start, a.isaac)
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        print(r.get("summary") or r.get("error"))
        if r.get("isaac"):
            print("isaac:", r["isaac"]["summary"])
        for w in r.get("warnings", []):
            print("!", w)
        for k, v in (r.get("files") or {}).items():
            print(f"  {k}: {v}")
        if r.get("error") and r.get("summary"):
            print("error:", r["error"], file=sys.stderr)
    return 0 if r["ok"] else 1


def cmd_place_photos(a: argparse.Namespace) -> int:
    outlines = json.loads(Path(a.outlines).read_text()) if a.outlines else None
    r = service.place_from_photos(a.scene, a.objects or None, not a.dry_run, outlines)
    if a.json or not r["ok"]:
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0 if r["ok"] else 1
    for p in r["placed"]:
        print(f"{p['id']:16s} ({p['position'][0]:.2f}, {p['position'][1]:.2f}) facing {p['facing_deg']:6.1f} "
              f"[{p['facing_from']}]  rms {p['rms_m']:.2f} m  {p['points']} points / {len(p['photos'])} photos"
              + (f"  moved {p['moved_m']} m" if "moved_m" in p else ""))
    for k in r.get("skipped", []):
        print(f"- {k['id']}: {k['why']}")
    for w in r["warnings"]:
        print("!", w)
    print("applied" if r["applied"] else "not applied")
    return 0


def cmd_variants(a: argparse.Namespace) -> int:
    r = service.make_variants(a.scene, a.count, a.seed, a.vary.split(",") if a.vary else None, a.size_jitter,
                              not a.no_compose)
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0
    for v in r["variants"]:
        print(f"{v['name']}  issues {v['issues']}" + ("  (more than the source)" if v.get("more_issues") else ""))
        for c in v["changes"]:
            print("   ", c)
    for w in r["warnings"]:
        print("!", w)
    print(r["index"])
    return 0


def cmd_measure_photo(a: argparse.Namespace) -> int:
    pairs = [[float(v) for v in p.split(",")] for p in a.pair or []]
    known = [{"pair": int(k.split(":")[0]), "m": float(k.split(":")[1]), "from": ":".join(k.split(":")[2:]) or None}
             for k in a.known or []]
    for c in a.count or []:  # pair:kind:count[:size_m]
        p = c.split(":")
        known.append({"pair": int(p[0]), "kind": p[1], "count": float(p[2]), **({"size_m": float(p[3])} if len(p) > 3 else {})})
    for c in a.ref or []:  # kind:u1,v1,u2,v2[:count[:size_m]]
        p = c.split(":")
        known.append({"kind": p[0], "line": [float(v) for v in p[1].split(",")], **({"count": float(p[2])} if len(p) > 2 else {}),
                      **({"size_m": float(p[3])} if len(p) > 3 else {})})
    r = service.measure_photo(a.scene, a.input, pairs or None, known or None, a.aisle_pair)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0 if r["ok"] else 1


def cmd_fit_photos(a: argparse.Namespace) -> int:
    r = service.fit_photos(json.loads(Path(a.file).read_text()), a.out, depth=a.depth, apply=a.apply)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0 if r["ok"] else 1


def cmd_video_poses(a: argparse.Namespace) -> int:
    r = service.video_poses(a.dir, a.hfov, a.video, a.rate)
    if not r.get("ok"):
        print(r.get("error"), file=sys.stderr)
        return 1
    for e in r["relative"]:
        extra = f"  {e['steps']} steps" if e.get("steps") else ""
        move = "  +direction" if e.get("direction") else ""
        print(f"{e['a']} -> {e['b']}: turn {e['turn_deg']} deg  ({e['source']}, {e['inliers']} inliers{extra}){move}")
    for w in r["warnings"]:
        print(f"! {w}")
    print(f"-> {r['file']} (put `relative` into the fit-photos request)")
    return 0


def cmd_frames(a: argparse.Namespace) -> int:
    r = service.extract_frames(a.video, a.out, a.max, a.min, url=a.url, title=a.title, author=a.author,
                               license=a.license, start_s=a.start, end_s=a.end, times=a.times, rotate=a.rotate)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0 if r["ok"] else 1


def cmd_sdg(a: argparse.Namespace) -> int:
    sdg = None
    if a.config or a.frames or a.seed is not None or a.resolution or a.coco or a.kitti or a.room_colors:
        base = json.loads(Path(a.config).read_text(encoding="utf-8")) if a.config else (
            service.load(a.scene).spec.sdg.model_dump(mode="json") if service.load(a.scene).spec.sdg else {})
        if a.frames:
            base["frames"] = a.frames
        if a.seed is not None:
            base["seed"] = a.seed
        if a.resolution:
            base["resolution"] = [int(v) for v in a.resolution.lower().split("x")]
        writers = set(base.get("writers", ["basic"]))
        writers |= {w for w, on in (("coco", a.coco), ("kitti", a.kitti)) if on}
        base["writers"] = sorted(writers)
        if a.room_colors:
            base.setdefault("randomize", {})["room_colors"] = True
        sdg = base
    r = service.generate_data(a.scene, sdg, dry_run=a.dry_run)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0 if r["ok"] else 1


def cmd_taste(a: argparse.Namespace) -> int:
    t = service.check_taste(a.scene)["taste"]
    if a.json:
        print(json.dumps(t, ensure_ascii=False, indent=2))
    else:
        _print_taste(t)
        if not t.get("skipped") and not t.get("outliers"):
            print(f"  taste: no outlier ({t.get('objects', 0)} objects, median fit {t.get('median_fit', 0):.2f})"
                  if "median_fit" in t else f"  taste: {t.get('note') or t.get('hint') or 'nothing to compare'}")
    return 0


def cmd_finish(a: argparse.Namespace) -> int:
    r = service.finish(a.scene, draft=a.draft, steps=a.steps, gif=a.gif, orbit=a.orbit, force=a.force)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0 if r.get("done") or (a.draft and "stopped" not in r) else 1


def cmd_auto_fix(a: argparse.Namespace) -> int:
    st = service.set_auto_fix(a.scene, a.state == "on")
    for f in st.get("auto_fixed") or []:
        print(json.dumps(f, ensure_ascii=False))
    print(f"auto fix {a.state}: {len(st['issues'])} issue(s), {len(st['usability'])} usability warning(s)")
    return 0


def cmd_licenses(a: argparse.Namespace) -> int:
    r = service.license_report(a.scene)
    print(Path(r["file"]).read_text(encoding="utf-8"))
    print(f"-> {r['file']}")
    return 0


def cmd_index(a: argparse.Namespace) -> int:
    from collections import Counter

    from agentic_sim_world_gen.assets.providers.local_folder import build_index

    recs = build_index(a.folder, rebuild=a.rebuild)
    print(f"{len(recs)} assets in {Path(a.folder).expanduser().resolve()}")
    print(f"  with size: {sum(any(r.size_m) for r in recs)}, with thumbnail: {sum(bool(r.thumbnail_url) for r in recs)}")
    for lic, n in Counter(r.license or "(none)" for r in recs).most_common():
        print(f"  license {lic}: {n}")
    if a.list:
        for r in recs:
            w, d, h = r.size_m
            print(f"  {r.raw['rel_path']}  {w:.2f}x{d:.2f}x{h:.2f} m  tags={r.tags}")
    print(f"use it with: AGENTIC_SIM_WORLD_GEN_SOURCES=+local:{Path(a.folder).expanduser().resolve()}  (the defaults plus this folder)")
    return 0


def cmd_plan(a: argparse.Namespace) -> int:
    opts = {k: v for k, v in (("units", a.units), ("storey", a.storey)) if v}
    if a.wall_layers:
        opts["wall_layers"] = [x.strip() for x in a.wall_layers.split(",") if x.strip()]
    r = service.import_floor_plan(a.file, a.out, **opts)
    w, d = r["size"]
    print(f"plan {w:.2f} x {d:.2f} m: {len(r['walls'])} walls, {len(r['room']['openings'])} openings, {len(r['zones'])} zones")
    for z in r["zones"]:
        print(f"  zone {z['id']} {z['name'] or ''}: {z['area_m2']} m2")
    for x in r["warnings"]:
        print(f"  ! {x}")
    print(f"plan json: {r['files']['plan']}  (use as room.plan_file)")
    print(f"preview:   {r['files']['preview']}")
    return 0


def cmd_reply_language(a: argparse.Namespace) -> int:
    """The line a Claude Code hook puts back into the conversation after a compaction."""
    from agentic_sim_world_gen import workspace

    print(workspace.language_reminder(a.out, a.compact))
    return 0


def cmd_workspace(a: argparse.Namespace) -> int:
    from agentic_sim_world_gen import workspace

    env = {}
    for kv in a.env:
        k, sep, v = kv.partition("=")
        if not sep or not k:
            print(f"--env needs KEY=VALUE: {kv}", file=sys.stderr)
            return 2
        env[k] = v
    try:
        r = workspace.make(a.folder, out=a.out, env=env, copy_skill=a.copy_skill, allow_tools=a.allow_tools, force=a.force,
                           model=a.model)
    except (ValueError, FileExistsError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"workspace: {r['workspace']}  (skill {r['skill']})")
    print(f"scenes:    {r['scenes']}")
    print(f"next:      {r['next']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="agentic-sim-world-gen")
    sub = ap.add_subparsers(dest="cmd", required=True)

    ws = sub.add_parser("workspace", help="a folder to make worlds in: MCP server + skill, without the repo's CLAUDE.md")
    ws.add_argument("folder")
    ws.add_argument("--out", help="where scenes go (AGENTIC_SIM_WORLD_GEN_OUT; default <folder>/scenes)")
    ws.add_argument("--env", action="append", default=[], metavar="KEY=VALUE", help="more variables for the MCP server")
    ws.add_argument("--copy-skill", action="store_true", help="copy the skill instead of linking it")
    ws.add_argument("--allow-tools", action="store_true", help="run the agentic-sim-world-gen tools without permission prompts")
    ws.add_argument("--model", help="the model Claude Code uses in this folder (settings.json; e.g. sonnet)")
    ws.add_argument("--force", action="store_true", help="replace files that differ")
    ws.set_defaults(fn=cmd_workspace)

    rl = sub.add_parser("reply-language", help="the reminder of the user's language for the workspace's hook after a compaction")
    rl.add_argument("--out", help="where the scenes are (default AGENTIC_SIM_WORLD_GEN_OUT)")
    rl.add_argument("--compact", action="store_true", help="the instruction for the summary instead (PreCompact hook)")
    rl.set_defaults(fn=cmd_reply_language)

    s = sub.add_parser("search", help="search SimReady Central")
    s.add_argument("query")
    s.add_argument("--limit", type=int, default=8)
    s.add_argument("--max-w", type=float)
    s.add_argument("--max-d", type=float)
    s.add_argument("--max-h", type=float)
    s.set_defaults(fn=cmd_search)

    q = sub.add_parser("questions", help="the 2-3 questions worth asking for a request (per place)")
    q.add_argument("request")
    q.add_argument("--place")
    q.add_argument("--known", nargs="*", default=[])
    q.add_argument("--limit", type=int, default=3)
    q.add_argument("--ask-first", action="store_true", help="ask before building (default: draft first)")
    q.add_argument("--purpose", help="the purpose the user answered (asks the robot's size for amr / manipulation)")
    q.add_argument("--json", action="store_true")
    q.set_defaults(fn=cmd_questions)

    kd = sub.add_parser("kinds", help="does the catalog have these kinds of object (kind table)")
    kd.add_argument("queries", nargs="+")
    kd.set_defaults(fn=cmd_kinds)

    mi = sub.add_parser("missing", help="what the asset sources did not have, over every scene made here, most wanted first")
    mi.add_argument("--top", type=int, help="only the first N queries")
    mi.add_argument("--why", choices=["not_in_catalog", "substitute", "none", "decor", "window"], help="only this kind")
    mi.add_argument("--json", action="store_true")
    mi.set_defaults(fn=cmd_missing)

    r = sub.add_parser("resolve", help="search assets for a scene spec and write previews")
    r.add_argument("spec")
    r.add_argument("-o", "--out", required=True)
    r.add_argument("--candidates", type=int, default=6)
    r.add_argument("--no-preferences", action="store_true", help="do not apply the remembered preferences")
    r.set_defaults(fn=cmd_resolve)

    pr = sub.add_parser("prefs", help="remembered preferences (* = used as a default)")
    pr.add_argument("--place", help="only the room items of this place")
    pr.add_argument("--forget", metavar="ITEM", help="forget this item (lighting, floor, walls, gap, aisle, ...; all)")
    pr.add_argument("--target", help="with --forget: only this target (place:home, chair, office chair)")
    pr.set_defaults(fn=cmd_prefs)

    p = sub.add_parser("preview", help="re-render previews (preview.html, candidates.html, layout.svg)")
    p.add_argument("resolved")
    p.add_argument("--open", action="store_true", help="serve preview.html on local HTTP and open it in the browser")
    p.add_argument("--serve", action="store_true", help="serve it without opening a browser (prints the URL)")
    p.set_defaults(fn=cmd_preview)

    c = sub.add_parser("compose", help="write USD from scene.resolved.json")
    c.add_argument("resolved")
    c.add_argument("-o", "--out")
    c.add_argument("--localize", action="store_true", help="download assets and reference local copies")
    c.set_defaults(fn=cmd_compose)

    k = sub.add_parser("check", help="open scene.usda in Isaac Sim headless and run physics")
    k.add_argument("scene", help="scene directory")
    k.add_argument("--steps", type=int, default=240)
    k.add_argument("--gif", action="store_true", help="also record the settle as check.gif (needs ffmpeg)")
    k.add_argument("--orbit", type=int, default=24,
                   help="shoot the settled room from N directions (check_orbit/, drag to rotate on the preview); 0 = skip")
    k.add_argument("--live", action="store_true",
                   help="run in the Isaac Sim already open (python_server on 127.0.0.1:8226, AGENTIC_SIM_WORLD_GEN_ISAAC_LIVE); "
                   "headless if none")
    k.add_argument("--draft", action="store_true", help="no physics: only the pictures of the room as laid out (a first draft)")
    k.add_argument("--force", action="store_true", help="open it even when it draws more faces than the budget")
    k.set_defaults(fn=cmd_check)

    f = sub.add_parser("fronts", help="render objects from their assumed front in Isaac Sim (front_views.png; no fill items)")
    f.add_argument("scene", help="scene directory")
    f.add_argument("--around", action="store_true", help="4 sides per object")
    f.add_argument("--ids", nargs="+", help="only these object ids (an array / fill group, or group/item)")
    f.add_argument("--fill", action="store_true", help="also the fill items (products on shelves; slow)")
    f.set_defaults(fn=cmd_fronts)

    ph = sub.add_parser("photo", help="render the view of an input photo in Isaac Sim, side by side with it")
    ph.add_argument("scene", help="scene directory")
    ph.add_argument("--input", help="input id (default: the first with a camera)")
    ph.set_defaults(fn=cmd_photo)

    pp = sub.add_parser("place-photos", help="put furniture where the photos show it (photo_fit.json's located feet)")
    pp.add_argument("scene", help="scene directory (with photo_fit.json from fit-photos --out <scene>)")
    pp.add_argument("objects", nargs="*", help="only these object ids")
    pp.add_argument("--dry-run", action="store_true", help="only report")
    pp.add_argument("--outlines", help="JSON file: [{object, photo, box: [u0, v0, u1, v1]}], a rectangle around "
                    "each object in a photo; the pose is then refined in the photos")
    pp.add_argument("--json", action="store_true", help="print the whole result")
    pp.set_defaults(fn=cmd_place_photos)

    va = sub.add_parser("variants", help="variations of a scene (other assets, positions, finishes, sizes) in <scene>/variants/")
    va.add_argument("scene", help="scene directory")
    va.add_argument("-n", "--count", type=int, default=4)
    va.add_argument("--seed", type=int, default=0)
    va.add_argument("--vary", help="comma-separated: assets,layout,materials,size (default all)")
    va.add_argument("--size-jitter", type=float, default=0.1, help="room size ± this fraction")
    va.add_argument("--no-compose", action="store_true", help="no scene.usda")
    va.add_argument("--json", action="store_true")
    va.set_defaults(fn=cmd_variants)

    nm = sub.add_parser("nav-map", help="navigation map for a robot (Nav2 map.pgm + map.yaml) and where it reaches")
    nm.add_argument("scene", help="scene directory")
    nm.add_argument("--radius", type=float, default=0.3, help="robot radius [m]")
    nm.add_argument("--height", type=float, default=1.2, help="robot height [m]")
    nm.add_argument("--min-height", type=float, default=0.05, help="ignore objects lower than this [m]")
    nm.add_argument("--resolution", type=float, default=0.05, help="cell size [m]")
    nm.add_argument("--start", help="x,y where the robot starts (default: the doors)")
    nm.add_argument("--isaac", action="store_true", help="also map the colliders in Isaac Sim")
    nm.add_argument("--json", action="store_true", help="print the whole result")
    nm.set_defaults(fn=cmd_nav_map)

    mp = sub.add_parser("measure-photo", help="room and aisle widths as the photo's camera sees them")
    mp.add_argument("scene", help="scene directory")
    mp.add_argument("--input", help="input id (default: the first with a camera)")
    mp.add_argument("--pair", action="append", help="u1,v1,u2,v2[,z]: two floor pixels in the photo (repeatable)")
    mp.add_argument("--known", action="append", help="pair:meters[:from], a width counted off the photo for --pair "
                    "number `pair` (0-based), e.g. 0:2.7:3 shelf units (repeatable)")
    mp.add_argument("--count", action="append", help="pair:kind:count[:size_m], things of known size counted across "
                    "--pair number `pair`, e.g. 0:floor_tile:8:0.3 or 0:shelf_unit:2.5 (repeatable)")
    mp.add_argument("--ref", action="append", help="kind:u1,v1,u2,v2[:count[:size_m]], a thing of known size in the photo "
                    "(its two ends on the floor; shelf_unit / door / floor_tile / basket / cart / pallet), "
                    "e.g. shelf_unit:820,900,1010,900 (repeatable)")
    mp.add_argument("--aisle-pair", type=int, help="the --pair (0-based) that spans an aisle: the layout aims at "
                    "its width")
    mp.set_defaults(fn=cmd_measure_photo)

    sd = sub.add_parser("sdg", help="synthetic data with Replicator (RGB, boxes, segmentation, depth)")
    sd.add_argument("scene", help="scene directory")
    sd.add_argument("--config", help="SdgSpec JSON (replaces the saved settings)")
    sd.add_argument("--frames", type=int)
    sd.add_argument("--seed", type=int)
    sd.add_argument("--resolution", help="WxH, e.g. 1280x720")
    sd.add_argument("--coco", action="store_true", help="also write a COCO dataset")
    sd.add_argument("--kitti", action="store_true", help="also write a KITTI dataset")
    sd.add_argument("--room-colors", action="store_true", help="randomize floor/wall colors")
    sd.add_argument("--dry-run", action="store_true", help="only plan the camera poses (no Isaac Sim)")
    sd.set_defaults(fn=cmd_sdg)

    ta = sub.add_parser("taste", help="flag chosen assets whose look clashes with the rest (needs the taste extra)")
    ta.add_argument("scene", help="scene directory")
    ta.add_argument("--json", action="store_true", help="print the full report")
    ta.set_defaults(fn=cmd_taste)

    fi = sub.add_parser("finish", help="USD if behind, the Isaac Sim check, the request items again, the licenses")
    fi.add_argument("scene", help="scene directory")
    fi.add_argument("--draft", action="store_true", help="only the USD and pictures without physics")
    fi.add_argument("--steps", type=int, default=240)
    fi.add_argument("--gif", action="store_true")
    fi.add_argument("--orbit", type=int, default=24)
    fi.add_argument("--force", action="store_true", help="open a scene over the draw budget anyway")
    fi.set_defaults(fn=cmd_finish)

    af = sub.add_parser("auto-fix", help="turn the automatic fixes on / off for a scene (on applies them now)")
    af.add_argument("scene", help="scene directory")
    af.add_argument("state", choices=["on", "off"])
    af.set_defaults(fn=cmd_auto_fix)

    li = sub.add_parser("licenses", help="write LICENSES.md (attribution) for the selected assets")
    li.add_argument("scene", help="scene directory")
    li.set_defaults(fn=cmd_licenses)

    ix = sub.add_parser("index", help="index a folder of third-party USD assets (cached; incremental)")
    ix.add_argument("folder")
    ix.add_argument("--rebuild", action="store_true", help="ignore the cache")
    ix.add_argument("--list", action="store_true", help="print every asset")
    ix.set_defaults(fn=cmd_index)

    fp = sub.add_parser("fit-photos", help="room size, openings and cameras from landmarks picked in several photos")
    fp.add_argument("file", help="request JSON (photos, openings, points, locate; see agentic_sim_world_gen.photo_fit)")
    fp.add_argument("-o", "--out", help="folder for photo_fit.json")
    fp.add_argument("--depth", action="store_true", help="fill depth at the pixels from <photo stem>.depth.npz "
                    "(MoGe-2 in AGENTIC_SIM_WORLD_GEN_DEPTH_PYTHON; photos need `image`)")
    fp.add_argument("--apply", action=argparse.BooleanOptionalAction, default=None,
                    help="write the room and cameras into the scene in --out (default: when --out is a scene folder)")
    fp.set_defaults(fn=cmd_fit_photos)

    fr = sub.add_parser("frames", help="pick sharp frames of a walk-through video (inputs/ of the scene folder)")
    fr.add_argument("video")
    fr.add_argument("-o", "--out", required=True, help="scene folder (frames are recorded as inputs if it has a scene)")
    fr.add_argument("--max", type=int, default=8, help="at most this many frames (default 8, cap 12)")
    fr.add_argument("--min", type=int, default=4, help="at least this many frames (default 4)")
    fr.add_argument("--start", type=float, help="only from this time (s): one room of a walk through several")
    fr.add_argument("--end", type=float, help="only up to this time (s)")
    fr.add_argument("--times", type=float, nargs="+", help="exactly the frames at these times (s) instead of choosing "
                    "(e.g. to move a blurred frame)")
    fr.add_argument("--rotate", type=int, choices=(0, 90, 180, 270),
                    help="turn the frames clockwise (a video without rotation metadata that comes out on its side: "
                    "90 if the floor is on the right, 270 if on the left); default: as the scene's frames of this video")
    fr.add_argument("--url", help="where the video came from (else <video stem>.source.json)")
    fr.add_argument("--title")
    fr.add_argument("--author")
    fr.add_argument("--license", help="e.g. CC0-1.0, own video")
    fr.set_defaults(fn=cmd_frames)

    vpz = sub.add_parser("video-poses", help="how the camera turned between the frames of `frames` (ORB; needs OpenCV)")
    vpz.add_argument("dir", help="scene folder that holds inputs/ from `agentic-sim-world-gen frames`")
    vpz.add_argument("--hfov", type=float, default=65.0, help="field of view of the video camera [deg] (default 65)")
    vpz.add_argument("--video", help="only the frames of this video")
    vpz.add_argument("--rate", type=float, help="samples per second between the frames (default 10)")
    vpz.set_defaults(fn=cmd_video_poses)

    pl = sub.add_parser("plan", help="import a floor plan (.json/.dxf/.ifc) and preview it")
    pl.add_argument("file")
    pl.add_argument("-o", "--out", required=True, help="output folder for plan.json / plan.svg")
    pl.add_argument("--units", choices=["m", "cm", "mm"], help="DXF without $INSUNITS")
    pl.add_argument("--storey", help="IFC storey name")
    pl.add_argument("--wall-layers", help='DXF wall layers, comma separated; patterns allowed ("E-B-CORE,E-B-SHEL*")')
    pl.set_defaults(fn=cmd_plan)

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
