"""Preview page: one HTML page per scene with everything the user should see while the scene is built.

preview.html = progress (candidates -> layout -> USD -> check), what is happening now, layout issues,
the top-down layout, the Isaac Sim screenshot and its report, the candidates of every object (the
selected one framed) and the licenses. preview.json holds a version stamp; the page polls it when
served over HTTP (agentic_sim_world_gen.preview.server) and, when the stamp changes, patches itself into the new
page in place (no reload, so nothing flashes), so the page next to the terminal follows every tool call.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from agentic_sim_world_gen.preview import changes
from agentic_sim_world_gen.scene.spec import Assumption, ResolvedScene, record_from_dict

PAGE, STATE = "preview.html", "preview.json"
FILES = ".preview"  # local thumbnails copied next to the page (file:// does not load over http)

_CSS = """
:root { --bg:#f5f6f8; --panel:#fff; --ink:#1c2230; --muted:#5d6678; --line:#dde2ea; --accent:#5f9500;
  --accent-bg:#eef6e0; --warn:#b25e00; --warn-bg:#fff3e0; --bad:#c62828; --bad-bg:#fdecea; --img:#fff; }
@media (prefers-color-scheme: dark) { :root { --bg:#0c1016; --panel:#151a22; --ink:#e4e9f0; --muted:#97a1b0;
  --line:#28303b; --accent:#8fd400; --accent-bg:#1d2a10; --warn:#ffb74d; --warn-bg:#2d2110; --bad:#ff8a80;
  --bad-bg:#331716; --img:#f3f4f6; } }
* { box-sizing:border-box; }
body { background:var(--bg); color:var(--ink); margin:0; padding:12px 16px 40px;
  font:14px/1.5 -apple-system,"Hiragino Sans","Noto Sans JP","Noto Sans CJK JP",sans-serif; }
header { position:sticky; top:0; z-index:2; background:var(--bg); padding:4px 0 10px; max-height:38vh; overflow:auto;
  box-shadow:0 6px 6px -6px rgba(0,0,0,.25); }
html { scroll-padding-top:9em; }  /* jumps to a section land below the sticky header */
h1 { font-size:18px; margin:0; } h2 { font-size:15px; margin:0 0 8px; }
.muted { color:var(--muted); font-size:12px; }
.steps { display:flex; gap:6px; margin:8px 0 6px; flex-wrap:wrap; }
.step { border:1px solid var(--line); border-radius:999px; padding:2px 12px; font-size:13px; color:var(--muted); background:var(--panel); }
.step.done { border-color:var(--accent); color:var(--accent); }
.step.now { background:var(--accent); border-color:var(--accent); color:#fff; font-weight:600; }
.step.warn { border-color:var(--warn); color:var(--warn); background:var(--warn-bg); }
.now-line { display:flex; gap:8px; align-items:center; font-size:13px; }
.now-line .badge, ol.hist .badge { flex:none; white-space:nowrap; margin-left:0; }
.lv-bad > span:not(.badge):not(.muted):not(.t) { color:var(--bad); font-weight:600; }
.lv-warn > span:not(.badge):not(.muted):not(.t) { color:var(--warn); }
.spin { width:12px; height:12px; border:2px solid var(--line); border-top-color:var(--accent); border-radius:50%;
  animation:spin 1s linear infinite; flex:none; }
@keyframes spin { to { transform:rotate(360deg); } }
.grid { display:grid; gap:12px; grid-template-columns:1fr; }
@media (min-width:1100px) { .grid.two { grid-template-columns:1fr 1fr; } }
section { background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:12px 14px; margin-top:12px; min-width:0; }
.grid > section { margin-top:0; }
.grid { margin-top:12px; }
section.shot.ng { border-color:var(--bad); }
section.shot ul.issues { margin-bottom:6px; }
section.focus { border-color:var(--accent); box-shadow:0 0 0 2px var(--accent-bg); }
.layout svg { max-width:100%; height:auto; display:block; background:var(--img); border-radius:6px; }
.shot img { max-width:100%; max-height:460px; display:block; border-radius:6px; margin:0 auto; }
.shot th { width:9em; }
.orbit { position:relative; cursor:grab; touch-action:pan-y; user-select:none; -webkit-user-select:none; outline:none; }
.orbit.drag { cursor:grabbing; }
.orbit:focus-visible img { box-shadow:0 0 0 2px var(--accent); }
.orbit-hint { position:absolute; left:50%; bottom:8px; transform:translateX(-50%); background:rgba(0,0,0,.55); color:#fff;
  font-size:12px; padding:2px 10px; border-radius:999px; pointer-events:none; white-space:nowrap; }
.badge { display:inline-block; border-radius:6px; padding:0 6px; font-size:12px; margin-left:6px; vertical-align:1px; }
.ok { background:var(--accent-bg); color:var(--accent); } .warn { background:var(--warn-bg); color:var(--warn); }
.bad { background:var(--bad-bg); color:var(--bad); }
.lic { background:var(--bg); color:var(--muted); border:1px solid var(--line); }
.card .badge { margin-left:0; margin-top:3px; }
ul.issues { margin:0; padding-left:18px; color:var(--bad); }
ul.issues.soft { color:var(--warn); }
table.assume { border-collapse:collapse; width:100%; }
table.assume td, table.assume th { padding:3px 8px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; }
table.assume tr.guess td { background:var(--warn-bg); }
table.assume td:first-child { white-space:nowrap; }
table.assume tr.unmet td { background:var(--bad-bg); }
.row { display:flex; gap:10px; overflow-x:auto; padding-bottom:4px; }
.card { flex:0 0 150px; border:2px solid var(--line); border-radius:10px; padding:6px; background:var(--panel); }
.card.sel { border-color:var(--accent); box-shadow:0 0 0 2px var(--accent-bg); }
.card img, .card .noimg { width:134px; height:134px; object-fit:contain; background:var(--img); border-radius:6px; display:block; }
.card .noimg { display:flex; align-items:center; justify-content:center; color:#888; font-size:12px; }
.name { font-weight:600; font-size:12px; word-break:break-all; margin-top:4px; }
.cap { font-size:11.5px; color:var(--muted); display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }
table { border-collapse:collapse; width:100%; font-size:12.5px; }
td, th { text-align:left; padding:3px 6px; border-bottom:1px solid var(--line); vertical-align:top; }
th { color:var(--muted); font-weight:500; }
.files a { color:var(--muted); margin-right:10px; font-size:12px; }
td a { color:inherit; text-decoration-color:var(--muted); text-underline-offset:2px; }
.tools { display:flex; gap:10px; align-items:center; flex-wrap:wrap; font-size:12.5px; margin-bottom:6px; }
.tools button { font:inherit; font-size:12px; border:1px solid var(--line); background:var(--bg); color:var(--ink);
  border-radius:6px; padding:1px 8px; cursor:pointer; }
details.obj { border-top:1px solid var(--line); }
details.obj > summary { display:flex; gap:8px; align-items:center; padding:5px 2px; cursor:pointer; list-style:none; min-width:0; }
details.obj > summary::-webkit-details-marker { display:none; }
details.obj > summary::before { content:"▸"; color:var(--muted); flex:none; width:10px; }
details.obj[open] > summary::before { content:"▾"; }
details.obj.focus > summary { background:var(--accent-bg); border-radius:6px; }
details.obj > summary img, details.obj > summary .noimg { width:36px; height:36px; object-fit:contain; background:var(--img);
  border-radius:4px; flex:none; }
details.obj > summary .noimg { display:inline-block; }
summary .label { font-weight:600; white-space:nowrap; }
summary .pick { color:var(--muted); font-size:12px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; min-width:0; }
summary .badge { flex:none; }
details.obj > .body { padding:4px 0 10px 18px; }
#cands:has(#only-attn:checked) details.obj:not(.attn) { display:none; }
details.hist > summary { cursor:pointer; }
ol.hist { list-style:none; margin:6px 0 0; padding:0; max-height:320px; overflow-y:auto; font-size:12.5px; }
ol.hist li { display:flex; flex-wrap:wrap; gap:0 8px; padding:2px 0; border-bottom:1px solid var(--line); }
ol.hist ul.ch { flex-basis:100%; margin:0 0 2px; padding-left:7.5em; color:var(--muted); }
ol.hist ul.ch li { display:list-item; border:0; padding:0; }
.changes { margin-top:6px; font-size:13px; border-left:3px solid var(--accent); padding:2px 10px; background:var(--accent-bg); border-radius:0 6px 6px 0; }
.changes ul { margin:2px 0; padding-left:1.2em; }
header .changes ul { max-height:5.4em; overflow:auto; }  /* a long change list must not cover the page */
ol.hist .t { color:var(--muted); flex:none; font-variant-numeric:tabular-nums; }
ol.hist a { color:var(--accent); flex:none; }
.edit-hint { display:none; }
.editable .edit-hint { display:inline; }
.edit-tools { display:none; }
.editable .edit-tools { display:flex; gap:8px; align-items:center; flex-wrap:wrap; font-size:12.5px; margin:6px 0; }
.edit-tools button, button.restore, #undo { font:inherit; font-size:12px; border:1px solid var(--line); background:var(--bg); color:var(--ink); border-radius:6px; padding:2px 10px; cursor:pointer; }
.edit-tools input[type=number] { width:5.5em; font:inherit; font-size:12px; } .edit-tools select { font:inherit; font-size:12px; }
.edit-tools fieldset { border:1px solid var(--line); border-radius:8px; padding:4px 10px 6px; display:flex; gap:8px; align-items:center; flex-wrap:wrap; margin:0; } .edit-tools legend { font-size:11.5px; color:var(--muted); padding:0 4px; }
button.restore, #undo { display:none; margin-left:8px; } .editable button.restore, .editable #undo { display:inline-block; }
.edit-only { display:none; }
.opts { display:grid; grid-template-columns:repeat(auto-fit, minmax(260px, 1fr)); gap:10px; }
.opt-card { border:2px solid var(--line); border-radius:10px; padding:8px; background:var(--panel); }
.opt-card.best { border-color:var(--accent); }
.opt-card img { width:100%; height:auto; display:block; background:var(--img); border-radius:6px; }
.opt-card h3 { margin:0 0 4px; font-size:15px; } .opt-card .note { margin:4px 0; font-size:13px; }
.opt-card button.choose, .opt-card button.mood-pick, .opt-card button.mood-undo { margin-top:6px; padding:4px 12px; }
.mood-confirm { margin-top:8px; padding:6px 8px; border:1px solid var(--bad, #c0392b); border-radius:6px; font-size:.9em; }
.mood-confirm ul { margin:4px 0 6px 1.2em; padding:0; }
.opts.moods { grid-template-columns:repeat(auto-fit, minmax(200px, 1fr)); }
.mood-swatch { width:100%; aspect-ratio:16/9; border-radius:6px; border:1px solid var(--line); }
.editable td.edit-only, .editable th.edit-only { display:table-cell; } .editable button.edit-only { display:inline-block; }
table.assume tr.sent td { background:var(--bad-bg); }
.fb-sent { margin-top:3px; } .fb-sent .badge { margin-left:0; margin-right:4px; }
.fb-form { margin-top:4px; display:flex; flex-wrap:wrap; gap:4px; align-items:center; }
.fb-form input[type=text] { flex:1 1 14em; min-width:0; font:inherit; padding:2px 6px; border:1px solid var(--line); border-radius:6px;
  background:var(--panel); color:inherit; }
table.assume button { font:inherit; font-size:12px; padding:1px 8px; border-radius:6px; border:1px solid var(--line);
  background:var(--panel); color:inherit; cursor:pointer; white-space:nowrap; }
table.assume button:hover { border-color:var(--accent); }
table.assume button.opt.on { background:var(--accent-bg); border-color:var(--accent); color:var(--accent); }
table.assume button.send { background:var(--accent); border-color:var(--accent); color:#fff; }
.note-form { position:fixed; z-index:5; display:flex; gap:4px; padding:6px; background:var(--panel); border:1px solid var(--accent);
  border-radius:8px; box-shadow:0 4px 16px rgba(0,0,0,.2); width:min(320px, calc(100vw - 32px)); }
.note-form input { flex:1 1 auto; min-width:0; font:inherit; padding:2px 6px; border:1px solid var(--line); border-radius:6px;
  background:var(--panel); color:inherit; }
.note-form button, .notes button { font:inherit; font-size:12px; padding:1px 8px; border-radius:6px; border:1px solid var(--line);
  background:var(--panel); color:inherit; cursor:pointer; white-space:nowrap; }
.note-form button.send { background:var(--accent); border-color:var(--accent); color:#fff; }
.pin-no { display:inline-block; min-width:1.5em; padding:0 4px; border-radius:9px; background:#d81b60; color:#fff; font-weight:bold;
  font-size:11px; text-align:center; }
.orbit .pin { position:absolute; transform:translate(-50%, -100%); pointer-events:none; }
.orbit .pin span { display:block; padding:1px 6px; border-radius:9px 9px 9px 0; background:#d81b60; color:#fff; font-size:11px;
  font-weight:bold; border:1.5px solid #fff; }
.wg-note { pointer-events:none; }
.editable .wg-plan { touch-action:none; user-select:none; -webkit-user-select:none; }
.editable .wg-obj.edit { cursor:grab; }
.editable .wg-obj.edit.drag { cursor:grabbing; }
.wg-obj.sel > polygon { stroke:#1565c0; stroke-width:2.5; }
.wg-obj.drag > polygon { fill-opacity:.55; }
.wg-obj.good > polygon { fill:#43a047; stroke:#2e7d32; }
.wg-obj.ng > polygon { fill:#e53935; stroke:#b71c1c; stroke-width:2.5; stroke-dasharray:none; }
.wg-obj.hit > polygon { stroke:#d00; stroke-width:3; }
.wg-handle { cursor:alias; }
.editable .card:not(.sel) { cursor:pointer; }
.editable .card:not(.sel):hover { border-color:var(--accent); }
.card.busy { opacity:.5; }
#wg-toast { position:fixed; left:50%; bottom:16px; transform:translateX(-50%); max-width:calc(100% - 32px); background:rgba(20,20,20,.86);
  color:#fff; font-size:13px; padding:6px 14px; border-radius:999px; z-index:10; pointer-events:none; transition:opacity .3s; }
#wg-toast.bad { background:#b71c1c; }
#wg-toast.hide { opacity:0; }
"""

_JS = """
const store = {  // sessionStorage can throw (private windows, blocked site data): the page works without it
  get(k, d) { try { const v = sessionStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } },
  set(k, v) { try { sessionStorage.setItem(k, JSON.stringify(v)); } catch (e) {} },
};

// objects the user opened or closed by hand keep that state across updates and reloads
const applyOpen = () => {
  const open = store.get("wg-open", {});
  document.querySelectorAll("details[id]").forEach(el => { if (el.id in open) el.open = open[el.id]; });
};
document.addEventListener("toggle", e => {
  if (!e.target.matches || !e.target.matches("details[id]") || e.target._auto) return;
  const open = store.get("wg-open", {});
  open[e.target.id] = e.target.open;
  store.set("wg-open", open);
}, true);
const openAll = on => {
  const open = store.get("wg-open", {});
  document.querySelectorAll("details.obj").forEach(el => { el.open = on; open[el.id] = on; });
  store.set("wg-open", open);
};

// links in the change history open the object they point at
document.addEventListener("click", e => {
  const a = e.target.closest && e.target.closest("a[href^='#obj-']");
  const el = a && document.getElementById(a.getAttribute("href").slice(1));
  if (el && el.tagName === "DETAILS") el.open = true;
});

// a new step opens and scrolls to the object being worked on (or the top when there is none); the
// same step again keeps where the user scrolled
const scrollFor = (focus, last) => {
  if (last === focus) return;
  const el = focus && document.getElementById("obj-" + focus);
  if (el && el.tagName === "DETAILS" && !el.open) { el._auto = true; el.open = true; setTimeout(() => { el._auto = false; }); }
  el ? el.scrollIntoView({block: "center"}) : window.scrollTo(0, 0);
};

// drag-to-rotate viewer of the check: one frame per direction, dragging right turns the room to the right
const initOrbit = o => {
  if (o._ready) return;
  o._ready = true;
  const srcs = JSON.parse(o.dataset.frames), img = o.querySelector("img"), n = srcs.length;
  srcs.forEach(s => { new Image().src = s; });  // preload, so dragging does not wait for the network
  let i = (+store.get("wg-orbit", 0) || 0) % n;
  // the user's notes as pins: each spot projected with the frame's camera
  const pins = () => {
    o.querySelectorAll(".pin").forEach(p => p.remove());
    const c = JSON.parse(o.dataset.cams || "[]")[i], notes = JSON.parse(o.dataset.notes || "[]");
    if (!c || !img.naturalWidth) return;
    const asp = img.naturalWidth / img.naturalHeight, t = Math.tan(c.hfov_deg * Math.PI / 360);
    const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
    const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
    const f = c.forward, r0 = cross(f, c.up), rn = Math.hypot(...r0), r = r0.map(x => x / rn), up = cross(r, f);
    for (const nt of notes) {
      const d = [0, 1, 2].map(k => nt.p[k] - c.eye[k]), z = dot(d, f);
      if (z <= 0) continue;
      const u = (dot(d, r) / z / t + 1) / 2, v = (1 - dot(d, up) / z / (t / asp)) / 2;
      if (u < 0 || u > 1 || v < 0 || v > 1) continue;
      const pin = document.createElement("div");
      pin.className = "pin";
      pin.style.left = (img.offsetLeft + u * img.clientWidth) + "px";
      pin.style.top = (img.offsetTop + v * img.clientHeight) + "px";
      pin.title = nt.text;
      pin.appendChild(Object.assign(document.createElement("span"), {textContent: nt.n}));
      o.appendChild(pin);
    }
  };
  o._pins = pins;
  img.addEventListener("load", pins);
  addEventListener("resize", pins);
  const show = k => {
    i = ((k % n) + n) % n;
    if (img.getAttribute("src") !== srcs[i]) img.src = srcs[i];
    store.set("wg-orbit", i);
    pins();
  };
  show(i);
  let x0 = null, i0 = 0;
  o.addEventListener("pointerdown", e => { x0 = e.clientX; i0 = i; o.setPointerCapture(e.pointerId); o.classList.add("drag"); });
  o.addEventListener("pointermove", e => {
    if (x0 !== null) show(i0 - Math.round((e.clientX - x0) / Math.max(8, o.clientWidth / n)));
  });
  const up = () => { x0 = null; o.classList.remove("drag"); };
  o.addEventListener("pointerup", up);
  o.addEventListener("pointercancel", up);
  o.addEventListener("dblclick", e => {  // write at this spot
    if (!TOKEN || ed.pending || !o.dataset.cams) return;
    const fi = JSON.parse(o.dataset.fi || "[]")[i], b = img.getBoundingClientRect();
    if (fi === undefined || fi === null) return;
    openNote(e, {view: "3d", frame: fi, u: (e.clientX - b.left) / b.width, v: (e.clientY - b.top) / b.height,
                 aspect: img.naturalWidth / img.naturalHeight});
  });
  o.addEventListener("keydown", e => {
    if (e.key === "ArrowRight" || e.key === "ArrowLeft") { show(i + (e.key === "ArrowRight" ? -1 : 1)); e.preventDefault(); }
  });
};

// Editing on the page, only when served by the agentic-sim-world-gen preview server (it puts a token in the page):
// drag an object on the plan to move it, select it and drag the ⟳ handle to turn it, click a candidate card to
// use it. Saves go through the same service calls as the MCP tools, so the page then updates as usual.
const TOKEN = (document.querySelector("meta[name=wg-token]") || {}).content;
const KINDS = __KINDS__;
const ed = {drag: null, pending: false, sel: store.get("wg-sel", null), trying: false, again: false};
const toast = (text, bad, keep) => {
  let t = document.getElementById("wg-toast");
  if (!t) { t = document.createElement("div"); t.id = "wg-toast"; document.documentElement.appendChild(t); }
  t.textContent = text;
  t.className = bad ? "bad" : "";
  clearTimeout(t._h);
  if (!keep) t._h = setTimeout(() => t.classList.add("hide"), bad ? 6000 : 2500);
};
const api = async body => {
  const r = await fetch("api/edit", {method: "POST", headers: {"Content-Type": "application/json", "X-Agentic-Sim-World-Gen-Token": TOKEN},
                                     body: JSON.stringify(body)});
  const j = await r.json().catch(() => ({error: r.statusText}));
  if (!r.ok || j.error) throw new Error(j.error || r.statusText);
  return j;
};
const kinds = issues => [...new Set(issues.map(i => KINDS[i.kind] || i.kind))].join("・");
const plan = () => document.querySelector("svg.wg-plan");
const frame = svg => ({x0: +svg.dataset.x0, y1: +svg.dataset.y1, k: +svg.dataset.ppm, m: +svg.dataset.margin});
const X = (f, x) => f.m + (x - f.x0) * f.k, Y = (f, y) => f.m + (f.y1 - y) * f.k;
const toM = (svg, e) => {  // pointer -> meters
  const pt = svg.createSVGPoint(); pt.x = e.clientX; pt.y = e.clientY;
  const u = pt.matrixTransform(svg.getScreenCTM().inverse()), f = frame(svg);
  return {x: f.x0 + (u.x - f.m) / f.k, y: f.y1 - (u.y - f.m) / f.k};
};
const gOf = id => [...document.querySelectorAll("g.wg-obj")].find(g => g.dataset.id === id);
const NS = "http://www.w3.org/2000/svg";
const drawHandle = () => {
  document.querySelectorAll(".wg-handle, .wg-handle-line").forEach(el => el.remove());
  document.querySelectorAll("g.wg-obj.sel").forEach(g => g.classList.remove("sel"));
  const svg = plan(), g = ed.sel && gOf(ed.sel);
  if (!svg || !g || !g.classList.contains("edit")) return;
  g.classList.add("sel");
  const f = frame(svg), x = +g.dataset.x, y = +g.dataset.y, a = (+g.dataset.yaw + +g.dataset.front) * Math.PI / 180;
  const r = +g.dataset.r + 18 / f.k, hx = X(f, x + Math.cos(a) * r), hy = Y(f, y + Math.sin(a) * r);
  const line = document.createElementNS(NS, "line");
  Object.entries({x1: X(f, x), y1: Y(f, y), x2: hx, y2: hy, stroke: "#1565c0", "stroke-width": 1, "stroke-dasharray": "3 2",
                  class: "wg-handle-line"}).forEach(([k, v]) => line.setAttribute(k, v));
  const c = document.createElementNS(NS, "circle");
  Object.entries({cx: hx, cy: hy, r: 8, fill: "#1565c0", stroke: "#fff", "stroke-width": 2, class: "wg-handle"})
    .forEach(([k, v]) => c.setAttribute(k, v));
  const t = document.createElementNS(NS, "title"); t.textContent = "ドラッグで回転（Shift で 1° 刻み）"; c.appendChild(t);
  g.appendChild(line); g.appendChild(c);
};
const readout = d => {
  const facing = ((d.yaw + d.front + d.dyaw) % 360 + 360) % 360, bad = !!(d.issues && d.issues.length);
  toast(`${d.g.dataset.label}：x ${(d.x + d.dx).toFixed(2)} m, y ${(d.y + d.dy).toFixed(2)} m, 向き ${facing.toFixed(0)}°` +
        (bad ? `　⚠ ${kinds(d.issues)}` : ""), bad, true);
};
const show = d => {  // the dragged object where the pointer is: transform + live check
  const svg = plan(), f = frame(svg), g = d.g;
  g.setAttribute("transform", `translate(${(d.dx * f.k).toFixed(1)} ${(-d.dy * f.k).toFixed(1)}) ` +
                              `rotate(${(-d.dyaw).toFixed(2)} ${X(f, d.x).toFixed(1)} ${Y(f, d.y).toFixed(1)})`);
  readout(d);
  if (ed.trying) { ed.again = true; return; }
  ed.trying = true;
  api({op: "try", id: d.id, position: [d.x + d.dx, d.y + d.dy], yaw_deg: d.yaw + d.dyaw}).then(j => {
    if (ed.drag !== d) return;
    d.issues = j.issues;
    readout(d);
    g.classList.toggle("ng", j.issues.length > 0);
    g.classList.toggle("good", j.issues.length === 0);
    document.querySelectorAll("g.wg-obj.hit").forEach(h => h.classList.remove("hit"));
    for (const i of j.issues) for (const o of i.objects) {
      const h = gOf(o.split(".")[0]);
      if (h && h !== g) h.classList.add("hit");
    }
  }).catch(() => {}).finally(() => {
    ed.trying = false;
    if (ed.again && ed.drag === d) { ed.again = false; show(d); }
  });
};
const endDrag = (d, save) => {
  document.querySelectorAll("g.wg-obj.hit").forEach(h => h.classList.remove("hit"));
  d.g.classList.remove("drag", "ng", "good");
  ed.drag = null;
  const moved = Math.hypot(d.dx, d.dy) > 0.005, turned = Math.abs(d.dyaw) > 0.05;
  if (!save || !(moved || turned)) { d.g.removeAttribute("transform"); toast("", false); return; }
  ed.pending = true;
  toast("保存中…", false, true);
  api({op: "move", id: d.id, position: moved ? [d.x + d.dx, d.y + d.dy] : null,
       facing_deg: turned ? d.yaw + d.front + d.dyaw : null}).then(j => {
    const why = j.issues.length ? `（問題：${kinds(j.issues)}）` : "";
    toast(`${d.g.dataset.label} を${moved ? "移動" : "回転"}して保存${d.layout ? "・配置ルールは解除" : ""}${j.usd ? "・scene.usda も更新" : ""}${why}`,
          j.issues.length > 0);
  }).catch(e => { d.g.removeAttribute("transform"); toast("保存できません：" + e.message, true); })
    .finally(() => { ed.pending = false; });
};
const initPlan = svg => {
  if (!TOKEN || svg._ready) return;
  svg._ready = true;
  svg.addEventListener("pointerdown", e => {
    if (e.button !== 0 || ed.pending) return;
    const handle = e.target.closest(".wg-handle"), g = e.target.closest("g.wg-obj.edit");
    if (!g) { ed.sel = null; store.set("wg-sel", null); drawHandle(); return; }
    e.preventDefault();
    svg.setPointerCapture(e.pointerId);
    const p = toM(svg, e);
    ed.drag = {g, id: g.dataset.id, x: +g.dataset.x, y: +g.dataset.y, yaw: +g.dataset.yaw, front: +g.dataset.front,
               layout: !!g.dataset.layout, mode: handle ? "turn" : "move", p0: p, c0: [e.clientX, e.clientY],
               dx: 0, dy: 0, dyaw: 0, live: false};
    if (!handle) { ed.sel = g.dataset.id; store.set("wg-sel", ed.sel); drawHandle(); }
  });
  svg.addEventListener("pointermove", e => {
    const d = ed.drag;
    if (!d) return;
    if (!d.live && Math.hypot(e.clientX - d.c0[0], e.clientY - d.c0[1]) < 4) return;  // a click, not a drag yet
    d.live = true;
    d.g.classList.add("drag");
    const p = toM(svg, e);
    if (d.mode === "move") { d.dx = p.x - d.p0.x; d.dy = p.y - d.p0.y; }
    else {
      const step = e.shiftKey ? 1 : 15;
      const want = Math.round(Math.atan2(p.y - d.y, p.x - d.x) * 180 / Math.PI / step) * step;
      d.dyaw = ((want - (d.yaw + d.front)) % 360 + 540) % 360 - 180;
    }
    show(d);
  });
  svg.addEventListener("dblclick", e => {  // write at this spot
    if (ed.pending) return;
    const p = toM(svg, e);
    openNote(e, {view: "plan", position: [p.x, p.y]});
  });
  svg.addEventListener("pointerup", () => ed.drag && endDrag(ed.drag, ed.drag.live));
  svg.addEventListener("pointercancel", () => ed.drag && endDrag(ed.drag, false));
  addEventListener("keydown", e => { if (e.key === "Escape" && ed.drag) endDrag(ed.drag, false); });
};
document.addEventListener("click", e => {  // a candidate card: use it
  const c = TOKEN && e.target.closest && e.target.closest(".card[data-obj]");
  if (!c || c.classList.contains("sel") || ed.pending) return;
  ed.pending = true;
  c.classList.add("busy");
  toast("差し替え中…", false, true);
  api(c.dataset.uri ? {op: "select", id: c.dataset.obj, uri: c.dataset.uri}
                    : {op: "select", id: c.dataset.obj, index: +c.dataset.i})
    .then(j => toast(`${c.dataset.uri ? "提案" : "候補 [" + c.dataset.i + "]"} に差し替え${j.usd ? "・scene.usda も更新" : ""}` +
                     (j.issues.length ? `（問題：${kinds(j.issues)}）` : ""), j.issues.length > 0))
    .catch(err => { c.classList.remove("busy"); toast("差し替えできません：" + err.message, true); })
    .finally(() => { ed.pending = false; });
});
// 「仮定」 rows: 「違う」 opens a small form (the question's options and / or own words); the words are saved
// on the row for Claude to act on in the next turn. Nothing in the scene changes here.
const fbForm = () => document.querySelector(".fb-form");
const sendFeedback = (tr, text, what) => {
  ed.pending = true;
  toast(what + "…", false, true);
  return api({op: "feedback", item: tr.dataset.item, object: tr.dataset.object || null, text})
    .then(() => { fbForm() && fbForm().remove(); toast(text ? "送りました。次のターンで Claude が直します" : "指摘を取り消しました"); })
    .catch(err => toast("送れません：" + err.message, true))
    .finally(() => { ed.pending = false; });
};
const openForm = tr => {
  if (fbForm()) fbForm().remove();
  const f = document.createElement("div");
  f.className = "fb-form";
  const opts = JSON.parse(tr.dataset.options || "[]").filter(o => o !== tr.dataset.value);
  const box = Object.assign(document.createElement("input"), {type: "text", placeholder: "こうしたい（例：西の壁にして）"});
  for (const o of opts) {
    const b = Object.assign(document.createElement("button"), {type: "button", className: "opt", textContent: o});
    b.onclick = () => sendFeedback(tr, o, "送信中");  // a choice is sent with one click; 送る is for own words
    f.appendChild(b);
  }
  const send = Object.assign(document.createElement("button"), {type: "button", className: "send", textContent: "送る"});
  const cancel = Object.assign(document.createElement("button"), {type: "button", textContent: "やめる"});
  const go = () => { const v = box.value.trim(); if (!v) { box.focus(); return; } sendFeedback(tr, v, "送信中"); };
  send.onclick = go;
  cancel.onclick = () => f.remove();
  box.addEventListener("keydown", e => {
    if (e.key === "Enter" && !e.isComposing) { e.preventDefault(); go(); }
    if (e.key === "Escape") f.remove();
  });
  f.append(box, send, cancel);
  tr.children[2].appendChild(f);
  box.focus();
};
document.addEventListener("click", e => {
  if (!TOKEN || !e.target.closest || ed.pending) return;
  const tr = e.target.closest("table.assume tr[data-item]");
  if (!tr) return;
  if (e.target.closest(".fb-open")) openForm(tr);
  else if (e.target.closest(".fb-undo")) sendFeedback(tr, "", "取り消し中");
});
// Notes: double-click a spot on the top view or the 3D viewer and write there (「ここに棚」); Claude acts
// on it in the next turn and the pin goes away. Nothing in the scene changes here.
const noteForm = () => document.querySelector(".note-form");
const openNote = (e, where) => {
  if (noteForm()) noteForm().remove();
  const f = document.createElement("div");
  f.className = "note-form";
  f.style.left = Math.max(8, Math.min(e.clientX - 20, innerWidth - 336)) + "px";
  f.style.top = Math.min(e.clientY + 12, innerHeight - 48) + "px";
  const box = Object.assign(document.createElement("input"), {type: "text", placeholder: "例：ここに棚 / この辺が狭い"});
  const send = Object.assign(document.createElement("button"), {type: "button", className: "send", textContent: "書き込む"});
  const cancel = Object.assign(document.createElement("button"), {type: "button", textContent: "やめる"});
  const go = () => {
    const v = box.value.trim();
    if (!v) { box.focus(); return; }
    ed.pending = true;
    toast("書き込み中…", false, true);
    api({op: "note", text: v, ...where})
      .then(() => { f.remove(); toast("書き込みました。次のターンで Claude が対応します"); })
      .catch(err => toast("書き込めません：" + err.message, true))
      .finally(() => { ed.pending = false; });
  };
  send.onclick = go;
  cancel.onclick = () => f.remove();
  box.addEventListener("keydown", k => {
    if (k.key === "Enter" && !k.isComposing) { k.preventDefault(); go(); }
    if (k.key === "Escape") f.remove();
  });
  f.append(box, send, cancel);
  document.body.appendChild(f);
  box.focus();
};
document.addEventListener("click", e => {
  const b = TOKEN && e.target.closest && e.target.closest(".notes button.note-undo");
  if (!b || ed.pending) return;
  ed.pending = true;
  toast("取り消し中…", false, true);
  api({op: "note_delete", id: b.dataset.id})
    .then(() => toast("書き込みを取り消しました"))
    .catch(err => toast("取り消せません：" + err.message, true))
    .finally(() => { ed.pending = false; });
});
// undo / back to a version of the history, and the direct edits (copy / delete an object, the room's numbers)
const act = (body, doing, done, fail) => {
  if (!TOKEN || ed.pending) return;
  ed.pending = true;
  toast(doing, false, true);
  api(body).then(j => toast(done + (j.issues && j.issues.length ? `（問題：${kinds(j.issues)}）` : ""), !!(j.issues && j.issues.length)))
    .catch(err => toast(fail + "：" + err.message, true))
    .finally(() => { ed.pending = false; });
};
document.addEventListener("click", e => {
  const t = e.target.closest && e.target.closest("button");
  if (!t) return;
  if (t.id === "undo") return act({op: "restore"}, "取り消しています…", "直前の変更を取り消しました（もう一度でやり直し）", "取り消せません");
  if (t.classList.contains("restore")) {
    e.preventDefault();
    return act({op: "restore", version: +t.dataset.version}, "戻しています…", "その時点に戻しました（「取り消す」で元に戻せます）", "戻せません");
  }
  const o = t.closest(".obj-tools");
  if (o) {
    const dup = t.classList.contains("dup");
    return act({op: dup ? "duplicate" : "remove", id: o.dataset.id}, dup ? "複製中…" : "削除中…",
               `${o.dataset.label} を${dup ? "複製" : "削除"}しました（「取り消す」で戻せます）`, dup ? "複製できません" : "削除できません");
  }
  const f = t.closest("#room-edit fieldset");
  if (f) {
    const body = {op: f.dataset.op};
    let n = 0;
    for (const i of f.querySelectorAll("input, select")) {  // only what the user changed
      const same = i.tagName === "SELECT" ? i.selectedOptions[0].hasAttribute("selected") : i.value === i.getAttribute("value");
      if (same || i.value === "") continue;
      body[i.name] = i.type === "number" || i.name === "light_kelvin" ? +i.value : i.value;
      n++;
    }
    if (!n) return toast("変更はありません");
    return act(body, "変更中…", f.querySelector("legend").textContent.replace(/ \\[.*/, "") + "を変えました（「取り消す」で戻せます）", "変えられません");
  }
});
document.addEventListener("click", e => {  // keep one of the proposed layouts
  const b = e.target.closest && e.target.closest(".opt-card button.choose");
  if (!TOKEN || !b || ed.pending) return;
  ed.pending = true;
  toast("「" + b.dataset.label + "」にしています", false, true);
  api({op: "choose_layout", name: b.dataset.name})
    .then(() => toast("「" + b.dataset.label + "」にしました"))
    .catch(err => toast("選べません：" + err.message, true))
    .finally(() => { ed.pending = false; });
});
const pickMood = (b, force) => {  // pick a look from the sample pictures: ask before overwriting
  ed.pending = true;
  const card = b.closest(".opt-card"), old = card.querySelector(".mood-confirm");
  if (old) old.remove();
  toast("雰囲気を「" + b.dataset.label + "」にしています", false, true);
  api({op: "mood", name: b.dataset.name, force: !!force})
    .then(r => {
      if (r.confirm) {  // what the user decided would change: say what, then 上書きする / やめる
        toast("指示どおりの項目が変わります", false);
        const box = document.createElement("div");
        box.className = "mood-confirm";
        box.innerHTML = "<b>指示どおりの項目を変えます</b><ul></ul>"
          + "<button type='button' class='mood-force'>上書きする</button> <button type='button' class='mood-cancel'>やめる</button>";
        for (const c of r.confirm) {
          const li = document.createElement("li");
          li.textContent = c.item + "：" + c.value + "（" + c.from + "）";
          box.querySelector("ul").appendChild(li);
        }
        box.querySelector(".mood-force").onclick = () => { if (!ed.pending) pickMood(b, true); };
        box.querySelector(".mood-cancel").onclick = () => box.remove();
        card.appendChild(box);
        return;
      }
      toast("雰囲気を「" + b.dataset.label + "」にしました。" + (r && r.usd ? "USD は裏で作り直しています。" : "")
            + "家具は次のターンで Claude が探します");
      return update().catch(() => location.reload());  // show the new colors now, not at the next poll
    })
    .catch(err => toast("選べません：" + err.message, true))
    .finally(() => { ed.pending = false; });
};
document.addEventListener("click", e => {
  const b = e.target.closest && e.target.closest(".opt-card button.mood-pick");
  if (TOKEN && b && !ed.pending) pickMood(b, false);
});
document.addEventListener("click", e => {  // back to the room before the first mood
  const b = e.target.closest && e.target.closest(".opt-card button.mood-undo");
  if (!TOKEN || !b || ed.pending) return;
  ed.pending = true;
  api({op: "mood_undo"})
    .then(() => { toast("雰囲気を取り消し、元の床・壁・照明に戻しました"); return update().catch(() => location.reload()); })
    .catch(err => toast("戻せません：" + err.message, true))
    .finally(() => { ed.pending = false; });
});
if (TOKEN) document.documentElement.classList.add("editable");

const roomOnly = () => {  // a room with its site: show the room alone or the whole site
  const svg = plan(), b = document.getElementById("room-only");
  if (!svg || !svg.dataset.room || !b) return;
  const on = store.get("wg-room-only", true), full = svg.dataset.full || svg.getAttribute("viewBox");
  svg.dataset.full = full;
  const box = on ? svg.dataset.room : full, [, , w, h] = box.split(" ").map(Number);
  svg.setAttribute("viewBox", box);
  svg.setAttribute("width", w); svg.setAttribute("height", h);
  b.textContent = on ? "敷地まで表示" : "部屋だけ表示";
  if (!b._ready) { b._ready = true; b.addEventListener("click", () => { store.set("wg-room-only", !store.get("wg-room-only", true)); roomOnly(); }); }
};
const wire = () => {
  roomOnly();
  for (const i of document.querySelectorAll("#room-edit input, #room-edit select")) {
    if (i === document.activeElement) continue;
    if (i.tagName === "SELECT") { const s = [...i.options].find(x => x.hasAttribute("selected")); if (s) i.value = s.value; }
    else i.value = i.getAttribute("value");
  }
  document.querySelectorAll(".orbit").forEach(initOrbit);
  document.querySelectorAll("svg.wg-plan").forEach(initPlan);
  drawHandle();
  const only = document.getElementById("only-attn");
  if (only && !only._ready) {
    only._ready = true;
    only.checked = store.get("wg-only-attn", false);
    only.addEventListener("change", () => store.set("wg-only-attn", only.checked));
  }
  for (const [id, on] of [["open-all", true], ["close-all", false]]) {
    const b = document.getElementById(id);
    if (b && !b._ready) { b._ready = true; b.addEventListener("click", () => openAll(on)); }
  }
};

try {
  history.scrollRestoration = "manual";
  applyOpen();
  wire();
  const focus = document.body.dataset.focus, last = store.get("wg-focus", null), y = store.get("wg-y", null);
  const go = () => (last !== null && last !== focus) || (focus && last === null) ? scrollFor(focus, null)
                                                                               : y && window.scrollTo(0, +y);
  go();
  addEventListener("load", go);  // again once the images have their size
  store.set("wg-focus", focus);
  addEventListener("beforeunload", () => store.set("wg-y", scrollY));
} catch (e) {}

// Patch the live page into the new one node by node: unchanged images, the SVG, open / closed objects and
// the scroll position stay as they are, so an update does not blank or flash the page (a full reload did,
// most of all with many assets or over ssh -L).
const morph = (a, b) => {
  if (a.nodeType !== 1) { if (a.nodeValue !== b.nodeValue) a.nodeValue = b.nodeValue; return; }
  if (a.tagName === "SCRIPT") return;
  if (a.classList.contains("orbit") && a.dataset.frames === b.dataset.frames) {  // keep the angle
    if (a.dataset.notes !== b.dataset.notes) { a.dataset.notes = b.dataset.notes || "[]"; a._pins && a._pins(); }
    return;
  }
  const keep = a.tagName === "DETAILS" ? "open" : null;
  for (const {name} of [...a.attributes]) if (name !== keep && !b.hasAttribute(name)) a.removeAttribute(name);
  for (const {name, value} of [...b.attributes]) if (name !== keep && a.getAttribute(name) !== value) a.setAttribute(name, value);
  const ac = [...a.childNodes], bc = [...b.childNodes];
  bc.forEach((y, i) => {
    const x = ac[i];
    const fresh = () => document.importNode(y, true);
    if (!x) a.appendChild(fresh());
    else if (x.nodeName !== y.nodeName || x.nodeType !== y.nodeType || (x.id || "") !== (y.id || "")) x.replaceWith(fresh());
    else morph(x, y);
  });
  for (let i = bc.length; i < ac.length; i++) ac[i].remove();
};
const update = async () => {
  const r = await fetch("preview.html?t=" + Date.now(), {cache: "no-store"});
  const doc = new DOMParser().parseFromString(await r.text(), "text/html");
  const before = document.body.dataset.focus;
  document.title = doc.title;
  morph(document.body, doc.body);
  applyOpen();
  wire();
  const focus = document.body.dataset.focus;
  store.set("wg-focus", focus);
  if (document.body.dataset.by !== "preview") scrollFor(focus, before);  // the user's own edit / note: stay put
};
if (location.protocol.startsWith("http")) {
  const poll = async () => {
    if (ed.drag || ed.pending || fbForm()) { setTimeout(poll, 300); return; }  // an edit in progress: update after it
    try {
      const r = await fetch("preview.json?t=" + Date.now(), {cache: "no-store"});
      if ((await r.json()).v !== document.body.dataset.v) {
        try { await update(); } catch (e) { location.reload(); return; }
      }
    } catch (e) {}
    setTimeout(poll, 1000);
  };
  setTimeout(poll, 1000);
} else {
  document.getElementById("live").textContent = "自動更新なし（agentic-sim-world-gen preview --open か MCP の open_preview で開くと追従します）";
}
"""


def _mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


# match.off_place (agentic_sim_world_gen.assets.domain) -> badge text
OFF_PLACE_JA = {"medical": "医療", "automotive": "自動車", "industrial": "業務・倉庫", "lab": "実験室",
                "airport": "空港", "retail": "店舗"}


def _thumb(url: str | None, d: Path) -> str | None:
    """Thumbnail URL usable from the page: remote as is, local files copied into .preview/."""
    if not url or not url.startswith("file://"):
        return url
    src = Path(unquote(urlparse(url).path))
    if not src.is_file():
        return None
    dst = d / FILES / (hashlib.sha1(str(src).encode()).hexdigest()[:16] + src.suffix.lower())
    if not dst.exists() or _mtime(dst) < _mtime(src):
        dst.parent.mkdir(exist_ok=True)
        shutil.copyfile(src, dst)
    return f"{FILES}/{dst.name}"


# spec fields that never reach the USD or the check: the user's notes, the decisions shown to the user,
# the request matched after generating (check_request), layout proposals, the mood picked and decor left out
NOT_IN_USD = ("notes", "assumptions", "requirements", "layout_options", "mood", "contrast", "decor_skipped", "lang")


def scene_hash(d: Path) -> str:
    """The scene as the USD and the check see it. Spec fields that do not change the scene (NOT_IN_USD) are
    left out, so recording check_request does not make the check look stale (written the same way as
    ResolvedScene.save, so a scene without those fields hashes as before)."""
    try:
        raw = (d / "scene.resolved.json").read_bytes()
    except OSError:
        return ""
    try:
        data = json.loads(raw)
        spec = data.get("spec", {})
        if any(k in spec for k in NOT_IN_USD):
            for k in NOT_IN_USD:
                spec.pop(k, None)
            raw = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    except (ValueError, AttributeError):
        pass
    return hashlib.sha1(raw).hexdigest()[:16]


def mark(d: str | Path, what: str) -> None:
    """Remember which scene.resolved.json the USD ("usd") or the Isaac Sim check ("check") was made from,
    so re-saving an unchanged scene does not make them look outdated."""
    d = Path(d)
    try:
        st = json.loads((d / STATE).read_text())
    except (OSError, ValueError):
        st = {}
    st.setdefault("made_from", {})[what] = scene_hash(d)
    (d / STATE).write_text(json.dumps(st, ensure_ascii=False))


def _state(d: Path, issues: list[dict[str, Any]], made_from: dict[str, str]) -> dict[str, Any]:
    """Where the scene is: USD written and up to date? checked since? check result."""
    now = scene_hash(d)
    resolved, usd, report = _mtime(d / "scene.resolved.json"), _mtime(d / "scene.usda"), _mtime(d / "check.json")

    def fresh(what: str, t: float) -> bool:  # recorded hash if any, else file times
        return made_from[what] == now if what in made_from else t + 1e-3 >= resolved

    out: dict[str, Any] = {"usd": "none" if not usd else ("ok" if fresh("usd", usd) else "stale"),
                           "check": "none", "report": None}
    if report:
        try:
            r = json.loads((d / "check.json").read_text())
        except (OSError, ValueError):
            r = {"error": "check.json unreadable"}
        out["report"] = r
        bad = r.get("error") or r.get("unstable") or r.get("sunk") or r.get("no_collider")
        out["check"] = "ok" if fresh("check", report) and report + 1e-3 >= usd else "stale"
        if out["check"] == "ok" and bad:
            out["check"] = "ng"
        elif out["check"] == "ok" and r.get("physics") is False:  # a draft look: pictures only
            out["check"] = "draft"
    out["issues"] = issues
    return out


def _steps(st: dict[str, Any], has_objects: bool) -> str:
    def chip(label: str, cls: str) -> str:
        return f"<span class='step {cls}'>{label}</span>"

    n = len(st["issues"])
    steps = [chip("候補", "done" if has_objects else "now"),
             chip(f"配置{f'（問題 {n}）' if n else ''}", "warn" if n else ("done" if has_objects else "")),
             chip("USD", {"ok": "done", "stale": "warn"}.get(st["usd"], "now" if has_objects and not n else "")),
             chip("Isaac Sim チェック" + ("（叩き台・物理まだ）" if st["check"] == "draft" else ""),
                  {"ok": "done", "ng": "warn", "stale": "", "draft": "now"}.get(st["check"],
                  "now" if st["usd"] == "ok" else ""))]
    return "<div class='steps'>" + "<span class='muted'>→</span>".join(steps) + "</div>"


ORBIT_DIR = "check_orbit"  # = service.ORBIT_DIR


def _orbit_frames(d: Path, report: dict[str, Any]) -> list[str]:
    """Frames of the drag-to-rotate viewer, in orbit order, when they belong to this check."""
    o = d / ORBIT_DIR
    names = [f for f in ((report.get("orbit") or {}).get("frames") or []) if f and (o / f).exists()]
    if len(names) < 2:
        return []
    return names if _mtime(o / names[0]) >= _mtime(d / "check.json") - 600 else []


def prim_label(labels: dict[str, str], name: str) -> str:
    """check reports objects by path under /World/Objects ("desk", "rack/rack_r0c1"): show the label,
    keeping the unit part ("ラック r0c1")."""
    head, _, rest = name.partition("/")
    unit = rest.rsplit("/", 1)[-1].removeprefix(head + "_") if rest else ""
    return (labels.get(head, head) + (f" {unit}" if unit else ""))


def prim_summary(labels: dict[str, str], names: list[str], limit: int = 12) -> str:
    """Bodies of one kind of check problem, one entry per object: units of the same object (a shelf of 168
    glasses) are counted instead of listed one by one, and past `limit` objects the rest is a count."""
    groups: dict[str, list[str]] = {}
    for n in names:
        groups.setdefault(n.split("/")[0], []).append(n)
    out = [prim_label(labels, v[0]) if len(v) == 1 else f"{labels.get(k, k)} ×{len(v)}"
           for k, v in groups.items()]
    if len(out) > limit:
        out = out[:limit] + [f"ほか {len(out) - limit} 物体"]
    return "・".join(out)


PHYSICS_KINDS = (("unstable", "物理で動いた"), ("sunk", "床を抜けた"), ("no_collider", "当たり判定なし"))


def obj_name(scene: ResolvedScene, o) -> str:
    """The object's name on the page. Stock: 「陳列：Chips Bag ×48（壁の陳列棚（西））」 instead of the planned
    label 「陳列（壁の陳列棚（西））6」 (nested, numbered by the product slot)."""
    if not (o.decor and o.fill):
        return o.label or o.id
    p = next((p for p in scene.placements if p.object_id == o.id), None)
    what = p.asset.name if p is not None and p.asset else o.query
    try:
        host = scene.obj(o.fill.on)
        host = host.label or host.id
    except (KeyError, ValueError, StopIteration):
        host = o.fill.on
    head = (o.label or "陳列").split("（")[0]
    return f"{head}：{what} ×{len(o.fill.slots)}（{host}）"


def _names(scene: ResolvedScene) -> dict[str, str]:
    return {o.id: obj_name(scene, o) for o in scene.spec.objects}


def _physics_lines(scene: ResolvedScene, st: dict[str, Any]) -> list[str]:
    """Problems the last Isaac Sim check found, listed with the layout issues (only while the check is
    current: a stale one says nothing about the scene as it is now)."""
    r = st.get("report")
    if not r or st.get("check") != "ng":
        return []
    labels = _names(scene)
    return [f"<li><b>{label}</b>：{escape(prim_summary(labels, r[k]))}"
            f"<span class='muted'>（Isaac Sim チェック）</span></li>" for k, label in PHYSICS_KINDS if r.get(k)]


def _orbit_cams(r: dict[str, Any], frames: list[str]) -> tuple[list, list]:
    """The orbit cameras of the shown frames and their index in the report, when the check recorded them."""
    o = r.get("orbit") or {}
    names, cams = o.get("frames") or [], o.get("cameras") or []
    if len(cams) != len(names):
        return [], []
    idx = [names.index(f) for f in frames]
    return [cams[k] for k in idx], idx


def _check_section(d: Path, st: dict[str, Any], labels: dict[str, str], notes: list | None = None) -> str:
    r = st["report"]
    if r is None:
        return ("<section class='shot'><h2>Isaac Sim チェック</h2><div class='muted'>まだ実行していません"
                "（compose_scene → check_in_isaac）。</div></section>")
    badge = {"ok": "<span class='badge ok'>OK</span>", "ng": "<span class='badge bad'>要修正</span>",
             "stale": "<span class='badge warn'>古い（シーン変更後に未実行）</span>",
             "draft": "<span class='badge warn'>叩き台（物理は未確認）</span>"}[st["check"]]
    mode = {"live": "開いている Isaac Sim", None: "headless"}.get(r.get("mode"), r.get("mode"))
    probs = [f"<li><b>{label}</b>：{escape(prim_summary(labels, r[k]))}</li>"
             for k, label in PHYSICS_KINDS if r.get(k)]
    if r.get("error"):
        probs.insert(0, f"<li><b>失敗</b>：{escape(str(r['error']))}</li>")
    ng = " ng" if st["check"] == "ng" else ""
    parts = [f"<section class='shot{ng}'><h2>Isaac Sim チェック{badge}</h2>"]
    if probs:  # first, above the picture: this is what needs fixing
        parts.append("<ul class='issues'>" + "".join(probs) + "</ul>")
    v = int(_mtime(d / "check.json"))
    fresh = [n for n in ("check.gif", "check.png") if (d / n).exists() and _mtime(d / n) >= _mtime(d / "check.json") - 600]
    frames = _orbit_frames(d, r)
    if frames:
        srcs = [f"{ORBIT_DIR}/{f}?v={v}" for f in frames]
        cams, idx = _orbit_cams(r, frames)
        # an old picture no longer shows the scene, so its pins point at things that moved (top view keeps them)
        pins = [] if st["check"] == "stale" else [
            {"n": x.id[1:], "text": x.text, "p": [*x.position, x.z or 0.0]} for x in notes or [] if x.position is not None]
        extra = (f" data-cams='{escape(json.dumps(cams))}' data-fi='{json.dumps(idx)}'"
                 f" data-notes='{escape(json.dumps(pins, ensure_ascii=False))}'") if cams else ""
        parts.append(f"<div class='orbit' tabindex='0' data-frames='{escape(json.dumps(srcs))}'{extra}>"
                     f"<img src='{escape(srcs[0])}' alt='check' draggable='false'>"
                     f"<span class='orbit-hint'>ドラッグで回転（{len(srcs)} 方向）"
                     f"{'・古い画像なので書き込みのピンは上面図だけ' if st['check'] == 'stale' and notes else ''}"
                     f"{'<span class=edit-hint>・ダブルクリックで書き込み</span>' if cams else ''}</span></div>")
        if "check.gif" in fresh:
            parts.append("<div class='muted'><a href='check.gif?v=%d'>物理の様子（check.gif）</a></div>" % v)
    elif fresh:
        parts.append(f"<img src='{fresh[0]}?v={v}' alt='{fresh[0]}'>")
    rows = []
    if r.get("static"):  # not a problem: walls, fixtures without a rigid body
        rows.append(f"<tr><th>固定（剛体なし）</th><td>{escape(prim_summary(labels, r['static']))}</td></tr>")
    objs = r.get("objects") or {}
    far = max(objs.items(), key=lambda kv: kv[1].get("max_displacement_m") or 0, default=None)
    moved = f" · 最大移動 {escape(prim_label(labels, far[0]))} {far[1]['max_displacement_m']:.3f} m" if far and far[1].get("max_displacement_m") else ""
    parts.append(f"<div class='muted'>{escape(str(mode))} · {len(objs)} 物体{moved}"
                 + f" · {time.strftime('%H:%M:%S', time.localtime(v))}</div>")
    if rows:
        parts.append("<table>" + "".join(rows) + "</table>")
    parts.append("</section>")
    return "".join(parts)


def _issue_objects(issues: list[dict[str, Any]]) -> set[str]:
    """Object ids named in layout issues (array / fill units "<id>.r0c1" count for their object)."""
    return {o.split(".")[0] for i in issues for o in i.get("objects") or []}


def _candidates(scene: ResolvedScene, d: Path, focus: str | None, issues: list[dict[str, Any]]) -> str:
    """One collapsible row per object (thumbnail of the pick, name, badges); the cards open under it. Only
    the object being worked on is open, so a scene with many objects stays one screen of rows."""
    bad = _issue_objects(issues)
    rows, n_attn = [], 0
    for p in scene.placements:
        o = scene.obj(p.object_id)
        n = o.array.count if o.array else 1
        sel = p.asset
        weak = bool(sel and (p.candidates[p.selected].get("match") or {}).get("weak"))
        level = _license_level(sel.license) if sel else "bad"
        why = [w for w, on in (("候補なし", not p.candidates), ("代用", weak or p.substitute),
                               ("ライセンス要確認", sel is not None and level == "bad"),
                               ("非商用", sel is not None and level == "nc"),
                               ("配置の問題", o.id in bad)) if on]
        n_attn += bool(why)
        src = _thumb(sel.thumbnail_url, d) if sel else None
        img = f"<img loading='lazy' src='{escape(src)}' alt=''>" if src else "<span class='noimg'></span>"
        badges = "".join(f"<span class='badge {'bad' if w in ('候補なし', 'ライセンス要確認', '配置の問題') else 'warn'}'>"
                         f"{w}</span>" for w in why if w not in ("ライセンス要確認", "非商用"))
        head = (f"<summary>{img}<span class='label'>{escape(obj_name(scene, o))}{f' ×{n}' if n > 1 else ''}</span>"
                f"<span class='pick'>{escape(sel.name) if sel else '—'} · “{escape(o.query)}” · {len(p.candidates)} 件</span>"
                f"{_license_badge(sel.license) if sel else ''}{badges}</summary>")
        cls = "obj" + (" attn" if why else "") + (" focus" if o.id == focus else "")
        parts = [f"<details id='obj-{escape(o.id)}' class='{cls}'{' open' if o.id == focus else ''}>{head}<div class='body'>"]
        copyable = o.array is None and o.fill is None and o.opening is None and o.covers is None and o.position is not None
        parts.append(f"<div class='edit-tools obj-tools' data-id='{escape(o.id)}' data-label='{escape(obj_name(scene, o))}'>"
                     + ("<button type='button' class='dup'>複製</button>" if copyable else "")
                     + "<button type='button' class='del'>削除</button></div>")
        if not p.candidates:
            parts.append("<div class='issues'>候補なし（クエリかサイズ条件を変える）</div>")
        else:
            if p.substitute:
                parts.append("<div class='issues'>該当なし：どの候補も別物（代用）。近いものを選ぶか、"
                             "<code>local:</code> フォルダでアセットを追加</div>")
            parts.append("<div class='row'>")
            for i, c in enumerate(p.candidates):
                r = record_from_dict(c)
                w, dd, h = r.size_m
                src = _thumb(r.thumbnail_url, d)
                cimg = f"<img loading='lazy' src='{escape(src)}' alt=''>" if src else "<div class='noimg'>no image</div>"
                m = c.get("match") or {}
                tag = "<span class='badge warn'>別物?</span>" if m.get("weak") else ""
                if m.get("off_place"):
                    tag += f"<span class='badge warn'>{escape(OFF_PLACE_JA.get(m['off_place'], m['off_place']))}用?</span>"
                if m.get("off_size"):  # agentic_sim_world_gen.assets.sizes
                    why = "; ".join(f"{k} {v}" for k, v in m["off_size"].items() if k != "kind")
                    tag += (f"<span class='badge warn' title='{escape(m['off_size'].get('kind') or '')} の標準サイズ外："
                            f"{escape(why)}'>寸法違い?</span>")
                if m.get("heavy"):  # service.heavy_faces
                    tag += f"<span class='badge warn' title='{m['heavy']:,} 面'>面が多い</span>"
                faces = f" · {r.polygons / 1e3:,.0f}k 面" if r.polygons else ""
                parts.append(f"<div class='card{' sel' if i == p.selected else ''}' data-obj='{escape(o.id)}' data-i='{i}'>{cimg}"
                             f"<div class='name'>[{i}] {escape(r.name)}{tag}</div>"
                             f"<div class='cap'>{escape(r.caption or r.semantic_class or '')}</div>"
                             f"<div class='muted'>{w:.2f}×{dd:.2f}×{h:.2f} m{faces}</div>{_license_badge(r.license)}</div>")
            parts.append("</div>")
        parts.append("</div></details>")
        rows.append("".join(parts))
    if not rows:
        return ""
    tools = ("<div class='tools'>"
             f"<label><input type='checkbox' id='only-attn'> 要確認のみ（{n_attn} / {len(rows)}）</label>"
             "<button type='button' id='open-all'>すべて開く</button>"
             "<button type='button' id='close-all'>すべて閉じる</button></div>")
    return (f"<section id='cands'><h2>アセット候補 <span class='muted'>{len(rows)} 物体 · 行をクリックで候補を表示"
            "<span class='edit-hint'> · 候補のカードをクリックで差し替え</span></span></h2>"
            + tools + "".join(rows) + "</section>")


def _license_level(lic: str | None) -> str:
    """ok (CC0) / lic (attribution) / warn (share-alike, Isaac bundled) / nc (non-commercial) / bad (unknown)."""
    from agentic_sim_world_gen.license import KNOWN, normalize

    spdx = normalize(lic)
    if spdx not in KNOWN:
        return "bad"
    note = KNOWN[spdx][2]
    return "ok" if spdx == "CC0-1.0" else "nc" if "non-commercial" in note else "warn" if note else "lic"


def _license_badge(lic: str | None) -> str:
    """CC0 green, attribution-only grey, non-commercial / share-alike / Isaac bundled orange, unknown red."""
    from agentic_sim_world_gen.license import KNOWN, normalize

    spdx = normalize(lic)
    if spdx not in KNOWN:
        text = "ライセンス不明" if spdx == "UNKNOWN" else spdx
        why = escape(lic) if lic else "ライセンスの記録なし"
        return f"<span class='badge bad' title='{why}：使う前に確認'>{escape(text)}</span>"
    name, _, note = KNOWN[spdx]
    cls = "ok" if spdx == "CC0-1.0" else "warn" if note else "lic"
    text = "Isaac Sim 同梱" if spdx == "NVIDIA-Isaac-Sim" else name
    return f"<span class='badge {cls}' title='{escape(note or name)}'>{escape(text)}</span>"


def _licenses(scene: ResolvedScene) -> str:
    """One row per license (badge, count, which objects), not one per object."""
    from agentic_sim_world_gen.license import report

    r = report(scene)
    if not r["assets"]:
        return ""
    rows = []
    for lic, items in sorted(r["assets"].items(), key=lambda kv: -len(kv[1])):
        names = list(dict.fromkeys(i["label"] or i["object_id"] for i in items))
        rows.append(f"<tr><td>{_license_badge(lic)}</td><td>{len(items)}</td>"
                    f"<td>{escape('、'.join(names))}</td></tr>")
    warn = "".join(f"<li>{escape(w)}</li>" for w in r["warnings"])
    srcs = "".join(f"<tr><td>{escape(g['from'])}</td><td>{g['count']}</td>"
                   f"<td>{escape('、'.join(g['versions']))}</td></tr>" for g in r["sources"])
    per = "".join(
        f"<tr><td>{escape(i['label'] or i['object_id'])}</td><td>{escape(i['asset'])}</td><td>{_license_badge(lic)}</td>"
        f"<td><a href='{escape(i['origin'].get('url') or i['uri'])}'>{escape(_version_ja(i['origin']))}</a> "
        f"{_pin_badge(i['origin'])}</td></tr>"
        for lic, items in r["assets"].items() for i in items)
    return ("<section><h2>ライセンスと入手先 <span class='muted'>詳細は LICENSES.md</span></h2>"
            "<table><tr><th>ライセンス</th><th>数</th><th>物</th></tr>" + "".join(rows) + "</table>"
            "<table style='margin-top:8px'><tr><th>入手先</th><th>数</th><th>版</th></tr>" + srcs + "</table>"
            f"<details id='origins'><summary class='muted'>アセットごとの版（{sum(len(v) for v in r['assets'].values())} 件）</summary>"
            "<table><tr><th>物</th><th>アセット</th><th>ライセンス</th><th>入手先と版</th></tr>" + per + "</table></details>"
            + (f"<details class='warnings'><summary class='muted'>注意 {len(r['warnings'])} 件</summary>"
               f"<ul class='issues'>{warn}</ul></details>" if warn else "") + "</section>")


def _version_ja(o: dict[str, Any]) -> str:
    from agentic_sim_world_gen.origin import label

    return label({**o, "dirty": False})


def _pin_badge(o: dict[str, Any]) -> str:
    """Can the same file be fetched again from where it came from?"""
    if o.get("dirty"):
        return "<span class='badge bad' title='アセットのフォルダに未コミットの変更：この版は取り直せない'>未コミットの変更</span>"
    if o.get("pinned"):
        return "<span class='badge ok' title='同じファイルを取り直せる'>版を固定</span>"
    if o.get("from") == "Isaac Sim assets":
        return "<span class='badge warn' title='Isaac Sim のリリース単位。リリース内でファイルが更新されても分からない'>リリース単位</span>"
    return "<span class='badge warn' title='{}'>版の記録なし</span>".format(escape(o.get("note") or ""))


HISTORY = 200  # events kept in preview.json for the change history

# results that need a look (a check that found problems) vs. all clear
_LEVEL_CLASS = {"bad": " lv-bad", "warn": " lv-warn", "ok": " lv-ok"}


def _level_badge(level: str | None) -> str:
    return {"bad": "<span class='badge bad'>要修正</span>", "warn": "<span class='badge warn'>要確認</span>",
            "ok": "<span class='badge ok'>OK</span>"}.get(level or "", "")


def _changes(event: dict[str, Any]) -> str:
    """What the last step changed, under the status line (empty when it changed nothing)."""
    ch = event.get("changes") or []
    if not ch:
        return ""
    return ("<div class='changes'><span class='muted'>変更内容</span><ul>"
            + "".join(f"<li>{escape(c)}</li>" for c in ch) + "</ul></div>")


def _history(events: list[dict[str, Any]], labels: dict[str, str], kept: list[int] | None = None) -> str:
    """Log of what was done to the scene, newest first. A row whose version is still kept (service.SNAPSHOTS)
    has 「ここに戻す」; the newest is the scene as it is."""
    if not events:
        return ""
    kept = kept or []
    back = set(kept[:-1])
    items = []
    for e in reversed(events):
        v = e.get("version")
        btn = (f"<button type='button' class='restore' data-version='{int(v)}'>ここに戻す</button>" if v in back else "")
        t = time.strftime("%m/%d %H:%M:%S", time.localtime(e.get("t", 0)))
        f = e.get("focus")
        jump = f"<a href='#obj-{escape(f)}'>{escape(labels.get(f) or f)}</a>" if f and f in labels else ""
        ch = e.get("changes") or []
        sub = "<ul class='ch'>" + "".join(f"<li>{escape(c)}</li>" for c in ch) + "</ul>" if ch else ""
        lv = e.get("level")
        who = "<span class='badge lic'>手動</span>" if e.get("by") == "preview" else ""
        items.append(f"<li class='{_LEVEL_CLASS.get(lv, '').strip()}'><span class='t'>{t}</span>{_level_badge(lv)}{who}"
                     f"<span>{escape(e.get('text') or '')}</span>{jump}{btn}{sub}</li>")
    return (f"<section><details id='history' class='hist'><summary><h2 style='display:inline'>変更履歴</h2> "
            f"<span class='muted'>{len(events)} 件 · 最新：{escape(events[-1].get('text') or '')}"
            f"{f' · 戻せるのは直近 {len(back)} 件' if back else ''}</span></summary>"
            f"<ol class='hist'>{''.join(items)}</ol></details></section>")


KELVINS = ((None, "プリセットのまま"), (2700, "電球色 2700K"), (3500, "温白色 3500K"), (4000, "白色 4000K"),
           (5000, "昼白色 5000K"), (6500, "昼光色 6500K"))


def _room_edit(scene: ResolvedScene) -> str:
    """The room's numbers the user can change on the page without a turn of the agent: size, how many
    small things, brightness and color of the lights. Each goes through set_room (correction=true)."""
    from agentic_sim_world_gen.scene.decor import DENSITY_JA

    room = scene.spec.room
    num = lambda k, v, lo, hi, step: (  # noqa: E731
        f"<input type='number' name='{k}' value='{v:g}' min='{lo}' max='{hi}' step='{step}'>")
    size = ""
    if room.plan is None and not room.outdoor:  # a floor plan / a site has its own shape
        size = ("<fieldset data-op='room'><legend>部屋の寸法 [m]</legend>"
                f"<label>幅 {num('width', room.width, 1, 200, 0.1)}</label>"
                f"<label>奥行 {num('depth', room.depth, 1, 200, 0.1)}</label>"
                f"<label>天井高 {num('height', room.height, 1.8, 30, 0.05)}</label>"
                "<button type='button'>適用</button></fieldset>")
    cur = room.density or "sparse"
    dens = "".join(f"<option value='{k}'{' selected' if k == cur else ''}>{escape(v)}</option>" for k, v in DENSITY_JA.items())
    kel = "".join(f"<option value='{k or ''}'{' selected' if k == room.light_kelvin else ''}>{v}</option>" for k, v in KELVINS
                  if k is not None or room.light_kelvin is None)  # set_room cannot clear it: back with 「取り消す」
    if room.light_kelvin is not None and room.light_kelvin not in dict(KELVINS):
        kel += f"<option value='{room.light_kelvin:g}' selected>{room.light_kelvin:g}K</option>"
    return ("<section id='room-edit' class='edit-tools'>" + size
            + f"<fieldset data-op='density'><legend>小物の量</legend><select name='density'>{dens}</select>"
              "<button type='button'>適用</button></fieldset>"
            + "<fieldset data-op='light'><legend>照明</legend>"
              f"<label>明るさ ×{num('lighting_scale', room.lighting_scale, 0.1, 5, 0.1)}</label>"
              f"<label>色 <select name='light_kelvin'>{kel}</select></label>"
              "<button type='button'>適用</button></fieldset></section>")


# layout issues in Japanese on the page (the issue data itself stays English: the agent reads it)
ISSUE_KINDS = {
    "overlap": "重なり", "out_of_room": "部屋からはみ出し", "too_tall": "天井より高い", "hits_wall": "壁にめり込み", "hits_beam": "梁・下がり壁に当たる", "hits_fixture": "吊り照明に当たる",
    "blocks_door": "ドアの前をふさぐ", "bad_opening": "開口の指定が不正", "missing_asset": "アセットなし",
    "no_position": "位置が未設定", "unsupported": "載せた先からはみ出し", "bad_layout": "配置の指定が解けない",
    "hits_building": "建物にめり込み", "on_curb": "縁石をまたぐ", "on_step_edge": "段差をまたぐ", "hits_stairs": "階段の上にある", "hits_rail": "手すりに当たる", "hits_column": "柱に当たる", "stairs_into_level": "階段が段差にぶつかる", "in_keep_clear": "空けておく場所にある",
    "shelf_fit": "棚に入らない", "bad_variant": "バリアントの指定が不正", "misaligned_array": "列の向きが揃っていない",
    "too_close": "関節付きの物が接している",
    # usability warnings
    "narrow_aisle": "通路が狭い", "unreachable": "たどり着けない", "no_front_space": "前に立てない",
    "chair_facing": "椅子が机を向いていない", "chair_far": "椅子が机から遠い", "tv_facing": "テレビとソファが向き合っていない",
    "tv_distance": "テレビとソファの距離", "register_hidden": "入口からレジが見えない", "blocks_window": "窓をふさぐ",
    "empty_floor": "売り場が空いている", "wide_aisle": "通路が広すぎる", "photo_aisle": "通路幅が写真と違う",
    "empty_shelves": "棚が空いている", "in_photo_view": "写真に無い物が視野をふさぐ",
}
_DETAILS = [  # (English detail pattern, Japanese); the first match wins
    (r"not in photo (\S+) but covers (\d+%) of its view: .*", r"写真 \1 の視野の \2（カメラの後ろか横へ、写っているなら seen_in に）"),
    (r"penetration ([\d.]+) m", r"\1 m めり込み"),
    (r"inside the wall (\S+) by ([\d.]+) m", r"壁 \1 に \2 m"),
    (r"in the way of the door (\S+) by ([\d.]+) m", r"開口 \1 の前に \2 m"),
    (r"inside the building (\S+) by ([\d.]+) m", r"建物 \1 に \2 m"),
    (r"cuts into the raised area (\S+) by ([\d.]+) m", r"段差 \1 に \2 m（zone と layout を指定するか、z を段の高さに）"),
    (r"stands in the keep-clear area (\S+) by ([\d.]+) m", r"\1 に \2 m（意図したものなら zone をそのエリアに）"),
    (r"footprint x\[([-\d.]+),([-\d.]+)\] y\[([-\d.]+),([-\d.]+)\] vs (?:room|site) ([\d.]+)x([\d.]+)",
     r"足元 x \1〜\2, y \3〜\4（部屋 \5 × \6 m）"),
    (r"footprint x\[([-\d.]+),([-\d.]+)\] y\[([-\d.]+),([-\d.]+)\] outside the plan's rooms",
     r"足元 x \1〜\2, y \3〜\4 が間取りの部屋の外"),
    (r"top ([\d.]+) m > room ([\d.]+) m", r"上端 \1 m ＞ 天井 \2 m"),
    (r"center is not over its support", "中心が載せた先の上にない"),
    (r"no asset found for '(.*)'", r"「\1」の候補が見つからない"),
    (r"no asset selected, cannot place", "アセット未選択のため置けない"),
    (r"no asset selected", "アセット未選択"),
    (r"position not set", "position も layout もない"),
    (r"layout has no placement rule and no position", "layout に配置ルールも position もない"),
    (r"refers to unknown object '(.*)'", r"存在しない物 '\1' を参照"),
    (r"circular layout references", "layout の参照が循環している"),
    (r"(?:support|target) '(.*)' is not placed", r"'\1' がまだ置けていない"),
    (r"fixture '(.*)' is not placed", r"什器 '\1' がまだ置けていない"),
    (r"no wall '(.*?)' in zone '(.*)'", r"ゾーン '\2' に壁 '\1' がない"),
    (r"no wall '(.*)'", r"壁 '\1' がない"),
    (r"an outdoor site has no ceiling to hang from", "屋外には吊る天井がない"),
    (r"wall '(.*?)' does not exist; walls: (.*)", r"壁 '\1' がない（ある壁：\2）"),
    (r"opening '(.*?)' does not exist; openings: (.*)", r"開口 '\1' がない（ある開口：\2）"),
    (r"no variant set '(.*?)'; the asset has (.*)", r"バリアントセット '\1' がない（アセットにあるもの：\2）"),
    (r"'(.*?)' has no '(.*?)'; values: (.*)", r"'\1' に '\2' がない（選べる値：\3）"),
    (r"nothing fits on '(.*?)':.*", r"'\1' の棚板に商品が入らない"),
    (r"levels (\[.*\]) skipped: .*", r"段 \1 には商品が入らないので飛ばした"),
    (r"(\d+) of (\d+) shelf levels hold no products \((\d+) %\)", r"\2 段のうち \1 段に商品がない（\3%。陳列を足す）"),
    (r"only (\d+)% of the ([\d.]+) m in front of it is free; .*", r"前 \2 m のうち空きが \1% だけ（人が立てる場所を空ける）"),
    (r"no way from the doors to the front of it \(even ([\d.]+) m wide\)", r"ドアから前まで行けない（幅 \1 m でも）"),
    (r"no way from the open floor to the front of it \(even ([\d.]+) m wide\)", r"空いた床から前まで行けない（幅 \1 m でも）"),
    (r"zone (.*) cannot be reached from the doors \(even ([\d.]+) m wide\)", r"ゾーン \1 にドアから行けない（幅 \2 m でも）"),
    (r"the way to it is narrower than ([\d.]+) m.*", r"そこまでの通路が \1 m より狭い"),
    (r"an empty patch ([\d.]+) m across around \(([-\d.]+), ([-\d.]+)\) with no fixture \(by a door.*",
     r"(\2, \3) のまわり \1 m に什器が無い（ドアのそば：わざと空けているなら可）"),
    (r"an empty patch ([\d.]+) m across around \(([-\d.]+), ([-\d.]+)\) with no fixture;.*",
     r"(\2, \3) のまわり \1 m に什器が無い。列を足すか伸ばす、または部屋を小さく"),
    (r"the aisle between them is ([\d.]+) m wide \(place \S+ needs ([\d.]+) m\);.*",
     r"前の通路が \1 m（必要 \2 m）。列を寄せて 1 列足すか、列を伸ばす"),
    (r"faces (\d+)° away from (\S+); .*", r"\2 から \1° それている（机に向ける）"),
    (r"([\d.]+) m from (\S+) \(> ([\d.]+) m\); .*", r"\2 から \1 m 離れている（\3 m 以内に）"),
    (r"the TV points (\d+)° off; .*", r"テレビがソファから \1° それている（向かい合わせる）"),
    (r"the sofa points (\d+)° off; .*", r"ソファがテレビから \1° それている（向かい合わせる）"),
    (r"([\d.]+) m apart; a ([\d.]+) m wide screen is best seen from ([\d.]+)–([\d.]+) m",
     r"距離 \1 m（幅 \2 m の画面は \3〜\4 m が目安）"),
    (r"cannot be seen from entrance (\S+): (.*) in the way \(at eye height ([\d.]+) m\)", r"入口 \1 から \2 にさえぎられて見えない"),
    (r"([\d.]+) m tall in front of window (\S+) \(sill ([\d.]+) m\), covering (\d+)% of it",
     r"高さ \1 m で窓 \2 の \4% をふさぐ（窓台 \3 m）"),
]


def _taste(scene: ResolvedScene, d: Path, taste: dict[str, Any] | None) -> str:
    """Selected assets that do not go with the rest and candidates that fit better. Only a warning:
    the user picks (clicking a card swaps, as in the candidate list)."""
    if not taste or not taste.get("outliers"):
        return ""
    pools: dict[str, list[dict[str, Any]]] = {}
    if any(a.get("index") is None for r in taste["outliers"] for a in r.get("alternatives") or []):
        from agentic_sim_world_gen import taste as tm

        try:
            pools = {k: v["candidates"] for k, v in json.loads((d / tm.POOL_FILE).read_text(encoding="utf-8")).items()}
        except (OSError, ValueError, KeyError, TypeError):
            pools = {}
    rows = []
    for r in taste["outliers"]:
        p = next((p for p in scene.placements if p.object_id == r["object"]), None)
        if p is None:
            continue
        why = ""
        if r.get("looks") or r.get("others_look"):
            why = (f"（これは{'・'.join(r.get('looks') or []) or '別の感じ'}、"
                   f"ほかは{'・'.join(r.get('others_look') or []) or '別の感じ'}）")
        cards = []
        for a in r.get("alternatives") or []:
            if a.get("index") is None:  # from the wider search, not yet among the candidates
                c = next((c for c in pools.get(p.object_id) or [] if c.get("uri") == a.get("uri")), None)
                if c is None:
                    continue
                rec, attr, tag = record_from_dict(c), f"data-uri='{escape(a['uri'])}'", "提案"
            elif 0 <= a["index"] < len(p.candidates):
                rec, attr, tag = record_from_dict(p.candidates[a["index"]]), f"data-i='{a['index']}'", f"[{a['index']}]"
            else:
                continue
            src = _thumb(rec.thumbnail_url, d)
            img = f"<img loading='lazy' src='{escape(src)}' alt=''>" if src else "<div class='noimg'>no image</div>"
            cards.append(f"<div class='card' data-obj='{escape(p.object_id)}' {attr}>{img}"
                         f"<div class='name'>{tag} {escape(rec.name)}</div>"
                         f"<div class='muted'>なじみ {a['fit']:.2f}（今 {r['fit']:.2f}）</div></div>")
        alt = (f"<div class='row'>{''.join(cards)}</div>" if cards
               else "<div class='muted'>候補の中にもっと合うものはない（research_object で別の語を探す）</div>")
        rows.append(f"<li><b>{escape(r.get('label') or r['object'])}</b>："
                    f"{escape(r['asset'])} がほかの物から浮いている{escape(why)} "
                    f"<span class='muted'>なじみ {r['fit']:.2f} / 中央値 {r['median_fit']:.2f}</span>{alt}</li>")
    if not rows:
        return ""
    return ("<section><h2>テイストの注意 <span class='muted'>選んだ物どうしの見た目の近さ（画像埋め込み）。"
            "警告だけ。差し替えるかは選んでください</span></h2><ul class='issues soft'>" + "".join(rows) + "</ul></section>")


def _issue_line(scene: ResolvedScene, i: dict[str, Any]) -> str:
    import re

    def name(oid: str) -> str:  # array / fill units "<id>.r0c1" -> "<label> r0c1"
        base, _, unit = oid.partition(".")
        try:
            label = obj_name(scene, scene.obj(base))
        except (KeyError, ValueError, StopIteration):
            label = base
        return label + (f" {unit}" if unit else "")

    detail = i.get("detail") or ""
    ja = next((m.expand(rep) for pat, rep in _DETAILS if (m := re.fullmatch(pat, detail))), None)
    text = escape(ja) if ja is not None else f"<span class='muted'>{escape(detail)}</span>"
    return (f"<li><b>{escape(ISSUE_KINDS.get(i['kind'], i['kind']))}</b>："
            f"{escape('・'.join(name(o) for o in i['objects']))} {text}</li>")


def render(scene: ResolvedScene, d: Path, issues: list[dict[str, Any]], event: dict[str, Any], stamp: str,
           made_from: dict[str, str] | None = None, history: list[dict[str, Any]] | None = None,
           usability: list[dict[str, Any]] | None = None, taste: dict[str, Any] | None = None) -> str:
    from agentic_sim_world_gen.service import versions

    kept = versions(d)
    undo = "<button type='button' id='undo' title='直前の変更を取り消す（もう一度押すとやり直し）'>取り消す</button>" if len(kept) > 1 else ""
    st = _state(d, issues, made_from or {})
    room = scene.spec.room
    size = f"{room.width:g} × {room.depth:g} m" + ("（屋外）" if room.outdoor else "")
    when = time.strftime("%H:%M:%S", time.localtime(event.get("t", time.time())))
    spin = "<span class='spin'></span>" if event.get("busy") else ""
    running = event.get("running")
    also = (f"<div class='now-line'><span class='spin'></span><span>{escape(running)}</span></div>"
            if running and running != event.get("text") else "")
    phys = _physics_lines(scene, st)
    if issues or phys:
        iss = "<ul class='issues'>" + "".join(_issue_line(scene, i) for i in issues) + "".join(phys) + "</ul>"
    else:
        iss = "<div class='muted'>はみ出し・重なり・ドア前の問題なし</div>"
    usd_note = {"none": "", "ok": "<span class='badge ok'>scene.usda 最新</span>",
                "stale": "<span class='badge warn'>scene.usda は古い（compose_scene で更新）</span>"}[st["usd"]]
    try:
        svg = (d / "layout.svg").read_text(encoding="utf-8")
    except OSError:
        svg = "<div class='muted'>layout.svg なし</div>"
    room_only = (" <span class='tools' style='display:inline-flex;margin:0'><button id='room-only' type='button'>敷地まで表示</button></span>"
                 if "data-room=" in svg else "")  # a room with its site
    links = " ".join(f"<a href='{f}'>{f}</a>" for f in ("candidates.html", "layout.svg", "scene.resolved.json",
                                                        "scene.usda", "check.json", "LICENSES.md") if (d / f).exists())
    return "".join([
        "<!doctype html><html lang='ja'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        f"<title>{escape(scene.spec.name)} — agentic-sim-world-gen</title><style>{_CSS}</style></head>"
        f"<body data-v='{escape(stamp)}' data-focus='{escape(event.get('focus') or '')}' data-by='{escape(event.get('by') or '')}'>",
        f"<header><h1>{escape(scene.spec.name)} <span class='muted'>{escape(size)} · {len(scene.placements)} 物体</span></h1>",
        _steps(st, bool(scene.placements)),
        f"<div class='now-line{_LEVEL_CLASS.get(event.get('level'), '')}'>{spin}{_level_badge(event.get('level'))}"
        f"<span>{escape(event.get('text') or '')}</span>"
        f"<span class='muted'>{when}</span>{undo}<span class='muted' id='live'></span></div>{also}{_changes(event)}</header>",
        _options(d),
        _requests(scene),
        _moods(scene, d),
        _assumptions(scene),
        _notes(scene),
        _references(scene, d),
        f"<section><h2>レイアウトの問題{usd_note}</h2>{iss}</section>",
        (f"<section><h2>使い勝手の注意 <span class='muted'>置けるが使いにくい（警告）</span></h2><ul class='issues soft'>"
         + "".join(_issue_line(scene, i) for i in usability) + "</ul></section>") if usability else "",
        _taste(scene, d, taste),
        _nav(d),
        "<div class='grid two'>",
        f"<section class='layout'><h2>上から見た配置 <span class='muted'>矢印 = 正面<span class='edit-hint'> · ドラッグで移動、選んで ● を回すと回転、ダブルクリックで書き込み</span></span>{room_only}</h2>{svg}</section>",
        _check_section(d, st, _names(scene), scene.spec.notes), "</div>",
        _room_edit(scene),
        _history(history or [], _names(scene), kept),
        _candidates(scene, d, event.get("focus"), issues),
        _licenses(scene),
        f"<div class='files' style='margin-top:12px'>{links}</div>",
        "<script>", _JS.replace("__KINDS__", json.dumps(ISSUE_KINDS, ensure_ascii=False)), "</script>",
        "</body></html>",
    ])


ITEM_JA = {"size": "部屋の寸法", "room size": "部屋の寸法", "place": "場所の種類", "floor": "床", "walls": "壁", "wall": "壁",
           "lighting": "照明", "openings": "ドア・窓", "cameras": "写真のカメラ", "door": "ドア", "ceiling": "天井", "ceiling height": "天井高", "asset": "アセット", "count": "台数", "position": "位置",
           "facing": "向き", "orientation": "向き", "height": "高さ",
           # the plan_questions items
           "room kind": "部屋の種類", "style": "テイスト", "window": "窓", "people": "人数", "desk layout": "机の並べ方",
           "meeting space": "打ち合わせ", "entrance": "入口", "register": "レジ", "cold cases": "冷蔵ケース",
           "seats": "席数", "counter": "カウンター", "racks": "ラック", "dock": "搬入口", "aisle": "通路幅",
           "line": "置く設備", "site": "場所の様子", "layout": "配置",
           # the purpose and its defaults
           "purpose": "用途", "density": "小物の量", "robot": "ロボット", "sdg": "合成データ", "targets": "検出したい物",
           # remembered preferences
           "lighting_scale": "明るさ", "gap": "隙間", "spacing": "間隔"}


def item_ja(item: str) -> str:
    return ITEM_JA.get(item.strip().lower(), item)


ASSET_ITEMS = ("アセット", "asset", "見た目", "種類", "商品", "モデル", "デザイン")
WHERE_OPTIONS = ["北の壁", "南の壁", "東の壁", "西の壁", "部屋の中央", "ドアのそば", "窓のそば"]
FACING_OPTIONS = ["北向き", "南向き", "東向き", "西向き", "部屋の中央向き", "ドア向き"]


def _object_options(scene: ResolvedScene, a: Assumption) -> list[str]:
    """Choices for an object's row of the assumptions table (only free text before): its other candidates
    for the asset, walls for the place, directions for the facing."""
    item = a.item.lower()
    if any(k in item for k in ASSET_ITEMS):
        try:
            p = next(p for p in scene.placements if p.object_id == a.object)
        except StopIteration:
            return []
        names = [c.get("name") for i, c in enumerate(p.candidates) if i != p.selected and c.get("name")]
        return [f"{n} にする" for n in dict.fromkeys(names)][:6]
    if "向き" in item or "facing" in item:
        return FACING_OPTIONS
    if "位置" in item or "場所" in item or "position" in item or "where" in item:
        return WHERE_OPTIONS
    return []


def _assumptions(scene: ResolvedScene) -> str:
    """「仮定」: what was decided for the scene, guesses first; the user only reads the guessed rows."""
    rows = sorted(scene.spec.assumptions, key=lambda a: a.given)
    if not rows:
        return ""
    from agentic_sim_world_gen.scene.questions import options_for

    names = _names(scene)
    guessed = sum(not a.given for a in rows)
    sent = sum(a.feedback is not None for a in rows)
    body = ""
    for a in rows:
        opts = a.options or (_object_options(scene, a) if a.object else options_for(scene.spec.room.place, a.item))
        data = (f" data-item='{escape(a.item, quote=True)}' data-object='{escape(a.object or '', quote=True)}'"
                f" data-value='{escape(a.value, quote=True)}' data-options='{escape(json.dumps(opts, ensure_ascii=False), quote=True)}'")
        fb = (f"<div class='fb-sent'><span class='badge bad'>指摘</span>「{escape(a.feedback.text)}」"
              f"<span class='muted'>Claude の対応待ち</span> <button type='button' class='fb-undo edit-only'>取り消す</button></div>"
              if a.feedback else "")
        if a.review:
            fb += f"<div class='fb-sent'><span class='badge warn'>要見直し</span>{escape(a.review)}</div>"
        body += (f"<tr class='{'given' if a.given else 'guess'}{' sent' if a.feedback else ''}'{data}><td>"
                 f"<span class='badge {'ok' if a.given else 'warn'}'>{'指示どおり' if a.given else '推測'}</span></td><td>"
                 f"{escape(names.get(a.object, a.object) + '：' if a.object else '')}{escape(item_ja(a.item))}</td>"
                 f"<td>{escape(a.value)}{fb}</td><td class='muted'>{escape(a.basis or '')}</td>"
                 f"<td class='edit-only'>{'' if a.feedback else '<button type=button class=fb-open>違う</button>'}</td></tr>")
    note = (f"。指摘 {sent} 件（次のターンで Claude が直します）" if sent else "") + (
        "<span class='edit-hint'>。違う行は「違う」から直し方を送れます</span>")
    return (f"<section><h2>仮定 <span class='muted'>推測 {guessed} 件 / 全 {len(rows)} 件。違う所だけ言ってください{note}</span></h2>"
            f"<table class='assume'><tr><th></th><th>項目</th><th>決めた内容</th><th>根拠</th><th class='edit-only'></th></tr>"
            f"{body}</table></section>")


def _notes(scene: ResolvedScene) -> str:
    """「書き込み」: what the user wrote at spots on the top view / 3D viewer, waiting for Claude."""
    from agentic_sim_world_gen.notes import where_ja

    if not scene.spec.notes:
        return ""
    rows = "".join(f"<li><span class='pin-no'>{escape(x.id[1:])}</span> 「{escape(x.text)}」"
                   f"<span class='muted'> {escape(where_ja(scene, x))}{'（3D で）' if x.view == '3d' else ''} · Claude の対応待ち</span>"
                   f" <button type='button' class='note-undo edit-only' data-id='{escape(x.id)}'>取り消す</button></li>"
                   for x in scene.spec.notes)
    return (f"<section class='notes'><h2>書き込み <span class='muted'>{len(scene.spec.notes)} 件。次のターンで Claude が対応すると消えます</span></h2>"
            f"<ul class='issues'>{rows}</ul></section>")


def _references(scene: ResolvedScene, d: Path) -> str:
    """「参考画像」: each reference image with the taste read off it and its source."""
    from agentic_sim_world_gen.inputs import taste_rows

    refs = [i for i in scene.spec.inputs if i.kind == "reference"]
    if not refs:
        return ""
    cards = ""
    for i in refs:
        src = _thumb(Path(i.path).as_uri(), d) if i.path and Path(i.path).is_file() else None
        img = (f"<img src='{escape(src)}' alt='{escape(i.title or i.id)}' style='max-width:240px;max-height:180px;"
               "border-radius:6px'>" if src else "")
        t = i.taste
        rows = "".join(f"<tr><td>{escape(k)}</td><td>{escape(v)}</td></tr>" for k, v in (taste_rows(t) if t else []))
        credit = escape(" / ".join(x for x in (i.title, i.author, i.license) if x))
        link = f" <a href='{escape(i.url)}'>出典</a>" if i.url else " <span class='badge bad'>出典 URL なし</span>"
        cards += (f"<div style='display:flex;gap:12px;flex-wrap:wrap;margin:6px 0'>{img}<div style='flex:1 1 16em'>"
                  f"<div><b>{escape(i.id)}</b> {escape(t.note) if t and t.note else ''}</div>"
                  f"<div class='muted'>{credit}{link}</div>"
                  + (f"<table>{rows}</table>" if rows else "<div class='muted'>テイスト未記録</div>") + "</div></div>")
    return (f"<section><h2>参考画像 <span class='muted'>色・素材・照明・様式だけを使う（寸法・配置は使わない）</span></h2>"
            f"{cards}</section>")


def _moods(scene: ResolvedScene, d: Path) -> str:
    """「雰囲気」: sample pictures of the looks the place offers; one click applies the room part."""
    from agentic_sim_world_gen.scene import looks, moods as md

    room = scene.spec.room
    offered = [] if room.outdoor else md.for_place(room.place)
    if not offered:
        return ""
    cur = scene.spec.mood.name if scene.spec.mood else None
    cards = ""
    for m in offered:
        f = md.image(room.place, m.name)
        src = _thumb(f.as_uri(), d) if f else None
        if src:
            pic = f"<img src='{escape(src)}' alt='{escape(m.label)}'>"
        else:  # not drawn yet: the floor and wall colors
            wall = m.walls if isinstance(m.walls, str) else m.walls["*"]
            fc, wc = (looks.hex_color(looks.SURFACES[k]["color"]) for k in (m.floor, wall))
            pic = (f"<div class='mood-swatch' style='background:linear-gradient({wc} 0 62%,{fc} 62% 100%)'></div>")
        on = m.name == cur
        badge = "<span class='badge ok'>選択中</span> " if on else ""
        if not on:
            btn = (f"<button type='button' class='mood-pick edit-only' data-name='{escape(m.name, quote=True)}'"
                   f" data-label='{escape(m.label, quote=True)}'>これにする</button>")
        elif scene.spec.mood.before_room is not None:
            btn = "<button type='button' class='mood-undo edit-only'>元に戻す</button>"
        else:
            btn = ""
        cards += (f"<div class='opt-card mood{' best' if on else ''}'><h3>{badge}"
                  f"{escape(m.label)}</h3>{pic}<div class='note'>{escape(m.note)}</div>{btn}</div>")
    head = (f"雰囲気 <span class='muted'>見本は標準の部屋です（家具はこのシーンと違います）。選ぶと床・壁・照明の色が"
            f"変わり、家具は次のターンで Claude が近い色の物を探します<span class='edit-hint'>。「これにする」で決まります"
            f"</span></span>")
    given = sorted({i.kind for i in scene.spec.inputs if i.kind in ("photo", "reference")})
    if given and not cur:  # the look already comes from the user's pictures: folded, still there to pick
        what = "・".join({"photo": "写真", "reference": "参考画像"}[k] for k in given)
        return (f"<section><details class='moods-fold'><summary><h2 style='display:inline'>{head}</h2>"
                f" <span class='muted'>（{what}から見た目を決めたので畳んでいます）</span></summary>"
                f"<div class='opts moods'>{cards}</div></details></section>")
    return f"<section><h2>{head}</h2><div class='opts moods'>{cards}</div></section>"


def _options(d: Path) -> str:
    """「配置案」: the proposed layouts side by side, waiting for the user to pick one."""
    try:
        rated = json.loads((d / "layout_options" / "summary.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    cards = ""
    for r in rated:
        f = d / "layout_options" / f"{r['name']}.svg"
        v = int(f.stat().st_mtime_ns // 1_000_000) if f.exists() else 0
        unmet = r.get("requirements_unmet")
        counts = ([r["room"]] if r.get("room") else []) + ([f"物 {r['objects']} 点"] if "objects" in r else []) + [
            f"問題 {r['issues']}", f"使い勝手の注意 {r['usability']}"] + (
            [f"指示を満たしていない {unmet}"] if unmet is not None else [])
        bad = r["issues"] or unmet
        best = "<span class='badge ok'>おすすめ</span> " if r.get("best") else ""
        cards += (f"<div class='opt-card{' best' if r.get('best') else ''}'><h3>{best}{escape(r['label'])}</h3>"
                  f"<img src='layout_options/{escape(r['name'])}.svg?v={v}' alt='{escape(r['label'])}'>"
                  + (f"<div class='note'>{escape(r['note'])}</div>" if r.get("note") else "")
                  + f"<div class='{'issues' if bad else 'muted'}'>{escape(' · '.join(counts))}</div>"
                  f"<button type='button' class='choose edit-only' data-name='{escape(r['name'], quote=True)}'"
                  f" data-label='{escape(r['label'], quote=True)}'>この案にする</button></div>")
    if rated and rated[0].get("contrast"):  # the scene as it is and one changed in one thing
        axis = {"size": "広さ", "amount": "物の量", "layout": "配置の型"}.get(rated[0]["contrast"], rated[0]["contrast"])
        return (f"<section><h2>どちらが近いですか <span class='muted'>{escape(axis)}だけが違う 2 案です"
                f"（どちらも違えば、チャットで「どちらも違う」と伝えてください）"
                f"<span class='edit-hint'>。「この案にする」で決まります</span></span></h2>"
                f"<div class='opts'>{cards}</div></section>")
    if rated and rated[0].get("axis"):  # three drafts that differ in one thing nobody decided
        axis = {"size": "広さ", "amount": "物の量", "composition": "構成"}.get(rated[0]["axis"], rated[0]["axis"])
        return (f"<section><h2>叩き台 3 案 <span class='muted'>{escape(axis)}だけを変えた {len(rated)} 案です。"
                f"一番近いものを選んでください（どれも違えば、どこが違うかをチャットで）"
                f"<span class='edit-hint'>。「この案にする」で決まります</span></span></h2>"
                f"<div class='opts'>{cards}</div></section>")
    return (f"<section><h2>配置案 <span class='muted'>{len(rated)} 案。どれにするか選んでください"
            f"（おすすめ = 問題が少ない案）<span class='edit-hint'>。「この案にする」で決まります</span></span></h2>"
            f"<div class='opts'>{cards}</div></section>")


def _nav(d: Path) -> str:
    """「ロボットの走行」: the navigation map(s) and what the robot cannot reach."""
    cards = ""
    for name, title in (("nav.json", "箱で近似"), ("nav_isaac.json", "Isaac Sim のコライダー")):
        try:
            r = json.loads((d / "nav" / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        f = d / "nav" / r["files"]["png"]
        v = int(f.stat().st_mtime_ns // 1_000_000) if f.exists() else 0
        lines = [f"床の {r['reachable']:.0%} に届く（{r['reachable_m2']:g} / {r['free_m2']:g} m²）"]
        lines += [f"行けない部屋：{z['name']}" for z in r.get("unreachable_zones", [])]
        lines += [f"近づけない物：{o['id']}" + (f"（あと {o['nearest_m']:g} m）" if "nearest_m" in o else "")
                  for o in r.get("unreachable_objects", [])]
        lines += [f"入れない空き：({p['at'][0]:g}, {p['at'][1]:g}) {p['m2']:g} m²" for p in r.get("pockets", [])]
        stale = "" if r.get("made_from") == scene_hash(d) else " <span class='badge warn'>古い</span>"
        bad = len(lines) > 1
        cards += (f"<div class='opt-card'><h3>{escape(title)}{stale}</h3>"
                  f"<img src='nav/{escape(r['files']['png'])}?v={v}' alt='{escape(title)}'>"
                  f"<ul class='{'issues soft' if bad else 'muted'}'>" + "".join(f"<li>{escape(x)}</li>" for x in lines)
                  + "</ul></div>")
    if not cards:
        return ""
    rb = r["robot"]
    return (f"<section><h2>ロボットの走行 <span class='muted'>半径 {rb['radius']:g} m・高さ {rb['height']:g} m。"
            f"濃い緑 = 中心が行ける所、薄い緑 = 体が通る所、白 = 届かない床、赤 = 入れない空き"
            f"（地図 nav/map.yaml）</span></h2><div class='opts'>{cards}</div></section>")


REQ_BADGE = {"met": ("ok", "満たした"), "unmet": ("bad", "満たしていない"), "unknown": ("warn", "確かめられない")}
REQ_BY = {"measured": "計測", "judged": "目視の判断", "none": ""}


def _requests(scene: ResolvedScene) -> str:
    """「指示との突き合わせ」: each item of the request and whether the result meets it."""
    if not scene.spec.requirements:
        return ""
    from agentic_sim_world_gen.layout.request import evaluate, summary

    try:
        rows = evaluate(scene)
    except Exception as e:
        return f"<section><h2>指示との突き合わせ</h2><p class='muted'>判定できない：{escape(str(e))}</p></section>"
    order = {"unmet": 0, "unknown": 1, "met": 2}
    body = ""
    for r in sorted(rows, key=lambda r: order[r["status"]]):
        cls, text = REQ_BADGE[r["status"]]
        how = "<br>".join(escape(("✓ " if c["ok"] else "✗ " if c["ok"] is False else "? ") + c["detail"])
                          for c in r["checks"])
        note = escape(r.get("note") or "")
        body += (f"<tr class='{r['status']}'><td><span class='badge {cls}'>{text}</span></td><td>{escape(r['text'])}</td>"
                 f"<td>{how}{'<br>' if how and note else ''}<span class='muted'>{note}</span></td>"
                 f"<td class='muted'>{REQ_BY[r['by']]}</td></tr>")
    n = summary(rows)
    return (f"<section><h2>指示との突き合わせ <span class='muted'>満たした {n['met']} / 満たしていない {n['unmet']} / "
            f"確かめられない {n['unknown']}</span></h2><table class='assume'><tr><th></th><th>指示</th><th>結果</th>"
            f"<th>判定</th></tr>{body}</table></section>")


def write(scene: ResolvedScene, d: str | Path, issues: list[dict[str, Any]], event: dict[str, Any] | None = None,
          usability: list[dict[str, Any]] | None = None, taste: dict[str, Any] | None = None) -> Path:
    """Write preview.html and bump preview.json. `event` = {text, focus (object id), busy}; without
    one the last event is kept (e.g. a hand-edited scene re-rendered)."""
    d = Path(d)
    old: dict[str, Any] = {}
    try:
        old = json.loads((d / STATE).read_text())
    except (OSError, ValueError):
        pass
    ev = {"text": "", "focus": None, "busy": False, **(old.get("event") or {})}
    hist = list(old.get("history") or [])
    snap = changes.snapshot(scene, issues)
    diff = changes.diff(old.get("snap"), snap)
    if event is None and diff:  # scene.resolved.json edited by hand, then re-rendered
        event = {"text": "シーンを更新", "focus": ev.get("focus")}
    if event is not None:
        ev = {"text": "", "focus": None, "busy": False, **event, "t": time.time(), "changes": diff}
        last = hist[-1] if hist else {}
        if ev["text"] and not ev["busy"] and (diff or (ev["text"], ev["focus"]) != (last.get("text"), last.get("focus"))):
            hist = (hist + [{"t": ev["t"], "text": ev["text"], "focus": ev["focus"], "changes": diff,
                              **({"level": ev["level"]} if ev.get("level") else {}),
                              **({"version": ev["version"]} if ev.get("version") else {}),
                              **({"by": ev["by"]} if ev.get("by") else {})}])[-HISTORY:]
        elif diff:  # not logged (busy): keep the old snapshot so the change shows up with the next entry
            snap = old.get("snap")
    stamp = f"{time.time():.6f}"
    page = d / PAGE
    tmp = page.with_suffix(".tmp")
    made_from = old.get("made_from") or {}
    tmp.write_text(render(scene, d, issues, ev, stamp, made_from, hist, usability, taste), encoding="utf-8")
    tmp.replace(page)  # the page must exist before the stamp tells the browser to reload
    (d / STATE).write_text(json.dumps({"v": stamp, "event": ev, "made_from": made_from, "history": hist, "snap": snap}, ensure_ascii=False))
    return page
