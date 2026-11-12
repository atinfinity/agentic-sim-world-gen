"""Local HTTP server for preview pages (the page polls preview.json and reloads itself).

One server per root directory, in a daemon thread: the MCP server process lives as long as the
Claude Code session, so the page keeps updating while Claude works. The root is the scene's parent
(usually AGENTIC_SIM_WORLD_GEN_OUT), so every scene there gets its own URL on the same port.
Bound to 127.0.0.1 by default; over ssh forward the port (ssh -L 8765:127.0.0.1:8765 host).
AGENTIC_SIM_WORLD_GEN_PREVIEW = "host:port" (default 127.0.0.1:8765; a busy port falls back to the one the folder had last
time, AGENTIC_SIM_WORLD_GEN_CACHE/preview_ports.json, then the next free one, so an ssh tunnel survives a new session).
AGENTIC_SIM_WORLD_GEN_BROWSER = command that opens the page instead of xdg-open / open, e.g.
"chromium --app={url} --window-size=1200,1400" ({url} is replaced; without it the URL is appended).

Edits from the page: POST <scene>/api/edit with JSON {op: move | try | select, id, ...} calls the same
service functions as the MCP tools (move_object / select_asset), so the page reloads itself as usual and
the history marks the step as the user's (by "preview"). Writes need the server's token, which is put
into preview.html only when it is served from here, plus a loopback client and a loopback Host / Origin
(another web page cannot read the token, and DNS rebinding gets a foreign Host). When bound to loopback,
reads also need a loopback Host, so a rebinding page cannot read the scenes either.
"""

from __future__ import annotations

import json
import os
import secrets
import shlex
import subprocess
import sys
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

from agentic_sim_world_gen.preview.page import PAGE, STATE

GALLERY_PAGE = "gallery.html"  # agentic_sim_world_gen.gallery.PAGE

_servers: dict[Path, ThreadingHTTPServer] = {}
_polled: dict[Path, float] = {}  # scene dir -> last time an open page asked for preview.json
_lock = threading.Lock()
_edit_lock = threading.Lock()  # one edit at a time (each loads, changes and saves the whole scene)
TOKEN = secrets.token_urlsafe(24)  # per process; only pages served by this process carry it
LOOPBACK = {"127.0.0.1", "::1", "localhost"}
MAX_BODY = 64 * 1024


def _host_of(value: str) -> str:
    """'127.0.0.1:8765' / '[::1]:8765' / 'http://localhost:8765' -> the host name."""
    host = urlparse(value if "//" in value else "//" + value).hostname or ""
    return host.lower()


class _Handler(SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self) -> None:
        if self.server.server_address[0] in LOOPBACK and _host_of(self.headers.get("Host", "")) not in LOOPBACK:
            # bound to loopback but asked for another host name: DNS rebinding from a web page
            return self._json(403, {"error": "loopback only"})
        path = unquote(urlparse(self.path).path)
        if path.endswith("/" + STATE):
            _polled[(Path(self.directory) / path.lstrip("/")).parent.resolve()] = time.time()
        if (path.endswith("/" + PAGE) or path == "/" + GALLERY_PAGE) and self._local():
            f = self._file(path)
            if f is not None and f.is_file():
                html = f.read_text(encoding="utf-8").replace(
                    "<head>", f"<head><meta name='wg-token' content='{TOKEN}'>", 1)
                return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
        super().do_GET()

    def do_POST(self) -> None:
        path = unquote(urlparse(self.path).path)
        if path == "/api/gallery/pick":
            return self._gallery_pick()
        if not path.endswith("/api/edit"):
            return self._json(404, {"error": "not found"})
        if not self._local():
            return self._json(403, {"error": "loopback only"})
        if not secrets.compare_digest(self.headers.get("X-Agentic-Sim-World-Gen-Token", ""), TOKEN):
            return self._json(403, {"error": "bad token (reload the page)"})
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            return self._json(413, {"error": "too large"})
        try:
            req = json.loads(self.rfile.read(n) or b"{}")
            scene = self._file(path[: -len("/api/edit")] + "/scene.resolved.json")
            if scene is None or not scene.is_file():
                return self._json(404, {"error": "no scene here"})
            return self._json(200, edit(scene.parent, req))
        except (KeyError, ValueError, IndexError, TypeError) as e:
            return self._json(400, {"error": str(e)})
        except Exception as e:  # noqa: BLE001 — the page shows it; the server must keep running
            return self._json(500, {"error": f"{type(e).__name__}: {e}"})

    def _gallery_pick(self) -> None:
        """The entry the user clicked on gallery.html, kept for start_from_example."""
        from agentic_sim_world_gen import gallery

        if not self._local():
            return self._json(403, {"error": "loopback only"})
        if not secrets.compare_digest(self.headers.get("X-Agentic-Sim-World-Gen-Token", ""), TOKEN):
            return self._json(403, {"error": "bad token (reload the page)"})
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            return self._json(413, {"error": "too large"})
        try:
            req = json.loads(self.rfile.read(n) or b"{}")
            return self._json(200, gallery.pick(self.directory, str(req["id"])))
        except (KeyError, ValueError, TypeError) as e:
            return self._json(400, {"error": str(e)})

    def _local(self) -> bool:
        if self.client_address[0] not in LOOPBACK or _host_of(self.headers.get("Host", "")) not in LOOPBACK:
            return False
        origin = self.headers.get("Origin")
        return origin is None or _host_of(origin) in LOOPBACK

    def _file(self, path: str) -> Path | None:
        """The file under the served root, or None when the path leaves it."""
        root = Path(self.directory).resolve()
        f = (root / path.lstrip("/")).resolve()
        return f if f.is_relative_to(root) else None

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj: dict) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json")

    def log_message(self, *args) -> None:  # stdout/stderr belong to the MCP stdio transport
        pass


def edit(scene_dir: Path, req: dict) -> dict:
    """One edit from the page. try: issues if the object stood there (nothing saved, for coloring a drag);
    move: move_object (position and / or facing_deg; clears its layout); select: select_asset. A saved edit
    also rewrites scene.usda when there is one, so the USD follows the page. feedback: the user's words on one
    assumption row, kept for the agent; the scene is not changed. choose_layout: keep one proposed layout.
    note / note_delete: the user's words at a spot of the top view or the 3D viewer, kept for the agent."""
    from agentic_sim_world_gen import service

    op = req["op"]
    if op == "feedback":
        with _edit_lock:
            tok = service.ACTOR.set("preview")
            try:
                service.assumption_feedback(scene_dir, str(req["item"]), str(req.get("text") or ""),
                                            object=str(req["object"]) if req.get("object") else None)
            finally:
                service.ACTOR.reset(tok)
        return {"ok": True}
    if op in ("mood", "mood_undo"):  # a look from the sample pictures: asks before overwriting, undo
        with _edit_lock:
            tok = service.ACTOR.set("preview")
            try:
                got = service.apply_mood(scene_dir, "undo" if op == "mood_undo" else str(req["name"]),
                                         confirm=not req.get("force"))
            finally:
                service.ACTOR.reset(tok)
        if got.get("applied") is False:  # what the user decided would change: the page asks first
            return {"ok": False, "confirm": got["confirm"]}
        # The saved scene already rewrote the page; the USD follows in the background so the page shows the new
        # Colors at once (a shop with thousands of products takes a while to compose).
        return {"ok": True, "usd": _compose_later(scene_dir)}
    if op in ("note", "note_delete"):
        with _edit_lock:
            tok = service.ACTOR.set("preview")
            try:
                if op == "note_delete":
                    service.resolve_notes(scene_dir, [str(req["id"])])
                else:
                    num = lambda k: float(req[k]) if req.get(k) is not None else None  # noqa: E731
                    service.add_note(scene_dir, str(req.get("text") or ""),
                                     position=_xy(req["position"]) if req.get("position") is not None else None,
                                     object=str(req["object"]) if req.get("object") else None,
                                     view=str(req.get("view") or "plan"),
                                     frame=int(req["frame"]) if req.get("frame") is not None else None,
                                     u=num("u"), v=num("v"), aspect=num("aspect"))
            finally:
                service.ACTOR.reset(tok)
        return {"ok": True}
    if op == "choose_layout":  # keep one of the proposed layouts
        with _edit_lock:
            tok = service.ACTOR.set("preview")
            try:
                service.choose_layout(scene_dir, str(req["name"]))
            finally:
                service.ACTOR.reset(tok)
        return {"ok": True, "usd": _compose_later(scene_dir)}
    if op in ("restore", "remove", "duplicate", "density", "room", "light"):  # undo and the direct edits
        num = lambda k: float(req[k]) if req.get(k) is not None else None  # noqa: E731
        with _edit_lock:
            tok = service.ACTOR.set("preview")
            try:
                if op == "restore":
                    st = service.restore(scene_dir, int(req["version"]) if req.get("version") is not None else None)
                elif op == "remove":
                    st = service.remove_object(scene_dir, str(req["id"]))
                elif op == "duplicate":
                    st = service.duplicate_object(scene_dir, str(req["id"]))
                elif op == "density":
                    st = service.set_room(scene_dir, density=str(req["density"]), correction=True)
                elif op == "room":
                    st = service.set_room(scene_dir, width=num("width"), depth=num("depth"), height=num("height"),
                                          correction=True)
                else:
                    st = service.set_room(scene_dir, lighting_scale=num("lighting_scale"),
                                          light_kelvin=num("light_kelvin"), correction=True)
            finally:
                service.ACTOR.reset(tok)
        new = set(st.get("added") or [])
        mine = [i for i in st["issues"] if new & set(i["objects"])] if new else []
        return {"ok": True, "issues": mine, "usd": _compose_later(scene_dir)}
    oid = str(req["id"])
    if op == "try":
        return {"issues": service.try_move(scene_dir, oid, _xy(req["position"]), float(req["yaw_deg"]))}
    with _edit_lock:
        tok = service.ACTOR.set("preview")
        try:
            if op == "move":
                pos = _xy(req["position"]) if req.get("position") is not None else None
                fd = float(req["facing_deg"]) % 360 if req.get("facing_deg") is not None else None
                st = service.move_object(scene_dir, oid, position=pos, facing_deg=fd)
            elif op == "select":
                st = (service.select_asset(scene_dir, oid, uri=str(req["uri"])) if req.get("uri")
                      else service.select_asset(scene_dir, oid, int(req["index"])))
            else:
                raise ValueError(f"unknown op '{op}'")
        finally:
            service.ACTOR.reset(tok)
    # The page redraws from the save at once; the USD follows in the background (a drag kept "保存中…"
    # for seconds and the page jumped while the shop composed)
    usd = _compose_later(scene_dir)
    mine = [i for i in st["issues"] if any(x == oid or x.startswith(oid + ".") for x in i["objects"])]
    return {"ok": True, "issues": mine, "usd": usd}


_composing: dict[Path, threading.Thread] = {}
_pending: set[Path] = set()
_compose_lock = threading.Lock()  # guards _composing / _pending; composing itself does not hold _edit_lock


def _compose_later(scene_dir: Path) -> bool:
    """Rewrite scene.usda in a thread, after the edit's save. Edits that come while it composes are folded into
    one more compose of the latest save (compose only reads the scene, so edits go on meanwhile). False when there
    is no USD yet."""
    from agentic_sim_world_gen import service

    if not (scene_dir / "scene.usda").exists():
        return False

    def run() -> None:
        while True:
            with _compose_lock:
                if scene_dir not in _pending:
                    _composing.pop(scene_dir, None)
                    return
                _pending.discard(scene_dir)
            tok = service.ACTOR.set("preview")
            try:
                service.compose_scene(scene_dir, log=False)
            except Exception:  # noqa: BLE001 — the next compose_scene by the agent reports it
                pass
            finally:
                service.ACTOR.reset(tok)

    with _compose_lock:
        _pending.add(scene_dir)
        if scene_dir in _composing:
            return True
        t = threading.Thread(target=run, daemon=True)
        _composing[scene_dir] = t
    t.start()
    return True


def wait_composed(timeout: float | None = None) -> None:
    """Wait for every background USD (tests)."""
    for t in list(_composing.values()):
        t.join(timeout)


def _xy(v) -> tuple[float, float]:
    x, y = (float(a) for a in v)
    return round(x, 3), round(y, 3)


def _address() -> tuple[str, int]:
    host, _, port = os.environ.get("AGENTIC_SIM_WORLD_GEN_PREVIEW", "127.0.0.1:8765").rpartition(":")
    return host or "127.0.0.1", int(port or 8765)


def _ports_file() -> Path:
    return Path(os.environ.get("AGENTIC_SIM_WORLD_GEN_CACHE", "~/.cache/agentic-sim-world-gen")).expanduser() / "preview_ports.json"


def _last_port(root: Path) -> int | None:
    try:
        return int(json.loads(_ports_file().read_text())[str(root)])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _remember_port(root: Path, port: int) -> None:
    f = _ports_file()
    try:
        ports = json.loads(f.read_text())
    except (OSError, ValueError):
        ports = {}
    if ports.get(str(root)) == port:
        return
    ports[str(root)] = port
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(ports))
    except OSError:
        pass


def serve(root: str | Path) -> ThreadingHTTPServer:
    """Start (once) a server for `root` in the background and return it."""
    root = Path(root).resolve()
    with _lock:
        if root in _servers:
            return _servers[root]
        host, port = _address()
        handler = partial(_Handler, directory=str(root))
        httpd = None
        # the configured port, then the one this folder had last time (an ssh -L tunnel keeps working across
        # Turns), then the next few, then any free port
        for p in dict.fromkeys([port, _last_port(root), *range(port + 1, port + 20), 0]):
            if p is None:
                continue
            try:
                httpd = ThreadingHTTPServer((host, p), handler)
                break
            except OSError:  # taken (another session)
                continue
        _remember_port(root, httpd.server_address[1])
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        _servers[root] = httpd
        return httpd


def url_for(scene_dir: str | Path) -> str:
    d = Path(scene_dir).resolve()
    httpd = serve(d.parent)
    host, port = httpd.server_address[:2]
    shown = "127.0.0.1" if host in ("0.0.0.0", "") else host
    return f"http://{shown}:{port}/{quote(d.name)}/{PAGE}"


def page_is_open(scene_dir: str | Path, within_s: float = 3.0) -> bool:
    return time.time() - _polled.get(Path(scene_dir).resolve(), 0) < within_s


def can_open_browser() -> bool:
    if os.environ.get("AGENTIC_SIM_WORLD_GEN_BROWSER"):
        return True
    if sys.platform == "darwin":
        return not os.environ.get("SSH_CONNECTION")
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def open_browser(url: str) -> bool:
    """AGENTIC_SIM_WORLD_GEN_BROWSER or xdg-open / open, detached and silent (never a text browser on the MCP's stdio)."""
    if not can_open_browser():
        return False
    if custom := os.environ.get("AGENTIC_SIM_WORLD_GEN_BROWSER"):
        cmd = [a.replace("{url}", url) for a in shlex.split(custom)]
        if "{url}" not in custom:
            cmd.append(url)
    else:
        cmd = ["open", url] if sys.platform == "darwin" else ["xdg-open", url]
    try:
        subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    except OSError:
        return False
    return True
