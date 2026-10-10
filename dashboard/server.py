"""Local HTTP server: static files from ./static plus a small JSON API.

Binds to 127.0.0.1 by default and rejects requests whose Host header is not a loopback name, so a web page in the
browser cannot reach it through DNS rebinding. GET/HEAD serve runs, config, input lists and symbol search. The write
paths (POST /api/config for config.toml, POST /api/inputs for the watchlist and holdings, POST /api/rerun to start a
screener run) are allowed only from this machine (unless --allow-remote-edit), require the per-process CSRF token
handed out by GET /api/config, and reject cross-site Origins. --read-only removes them.
"""
from __future__ import annotations

import json
import mimetypes
import secrets
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import search as yahoo_search
from .data import Store
from .jobs import Runner

STATIC = Path(__file__).resolve().parent / "static"
LOOPBACK = {"127.0.0.1", "localhost", "[::1]"}
LOOPBACK_BIND = {"127.0.0.1", "localhost", "::1"}
MAX_BODY = 256 * 1024
# headers a reverse proxy adds (Tailscale Serve identity, cloudflared, nginx/Caddy); their presence means the request
# came from another machine even though the socket peer is loopback, and the Host header is client-controlled
PROXY_HEADERS = ("Tailscale-User-Login", "X-Forwarded-For", "X-Forwarded-Host", "Forwarded", "X-Real-Ip",
                 "Cf-Connecting-Ip")


def _host_of(raw: str) -> str:
    raw = (raw or "").lower()
    return raw.split("]")[0] + "]" if raw.startswith("[") else raw.rsplit(":", 1)[0] if ":" in raw else raw


def make_handler(store: Store, quiet: bool = True, allowed_hosts: tuple[str, ...] = (), base_path: str = "",
                 read_only: bool = False, allow_remote_edit: bool = False, csrf_token: str | None = None,
                 runner: Runner | None = None, search=yahoo_search.search):
    """allowed_hosts: extra Host names accepted besides loopback (e.g. the Tailscale name of this machine).
    base_path: optional mount prefix such as "/screener" when a reverse proxy serves the dashboard under a path."""
    allowed = LOOPBACK | {h.lower() for h in allowed_hosts}
    base_path = "/" + base_path.strip("/") if base_path.strip("/") else ""
    token = csrf_token or secrets.token_urlsafe(32)
    runner = runner or Runner(store.root)

    def is_local(handler) -> bool:
        """This machine, not proxied: loopback socket peer, loopback Host and no proxy headers."""
        peer = handler.client_address[0] if handler.client_address else ""
        return (peer in ("127.0.0.1", "::1") and _host_of(handler.headers.get("Host")) in LOOPBACK
                and not any(handler.headers.get(h) for h in PROXY_HEADERS))

    def editable(handler) -> bool:
        return not read_only and (allow_remote_edit or is_local(handler))

    class Handler(BaseHTTPRequestHandler):
        server_version = "screener-dashboard"

        def log_message(self, fmt, *args):  # keep the console quiet unless asked
            if not quiet:
                super().log_message(fmt, *args)

        def _send(self, status: int, body: bytes, ctype: str, cache: bool = False):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "max-age=60" if cache else "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
                             "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cross-Origin-Opener-Policy", "same-origin")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, obj, status: int = 200):
            self._send(status, json.dumps(obj, allow_nan=False, default=str).encode("utf-8"),
                       "application/json; charset=utf-8")

        def _route(self) -> tuple[str, dict] | None:
            """(path without base prefix, query) or None after a redirect/forbidden response was sent."""
            host = _host_of(self.headers.get("Host"))
            if host not in allowed:
                self._json({"error": "forbidden host; start the dashboard with --allow-host " + host}, HTTPStatus.FORBIDDEN)
                return None
            url = urlsplit(self.path)
            path = url.path
            if base_path:
                if path == base_path:  # relative asset and API URLs need the trailing slash
                    self.send_response(HTTPStatus.PERMANENT_REDIRECT)
                    self.send_header("Location", base_path + "/" + ("?" + url.query if url.query else ""))
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return None
                if path.startswith(base_path + "/"):
                    path = path[len(base_path):]  # proxies that do not strip the prefix themselves
            return path, {k: v[0] for k, v in parse_qs(url.query).items()}

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            routed = self._route()
            if routed is None:
                return
            path, q = routed
            try:
                if path == "/api/runs":
                    return self._json(store.runs_payload())
                if path == "/api/run":
                    dates = store.run_dates()
                    run = q.get("date") or (dates[-1] if dates else None)
                    if not run:
                        return self._json({"error": "no runs", "root": str(store.root)}, HTTPStatus.NOT_FOUND)
                    return self._json(store.run_payload(run, q.get("prev")))
                if path == "/api/symbol":
                    return self._json(store.symbol_history(q.get("s", "")))
                if path == "/api/config":
                    payload = store.config_payload()
                    payload.update(editable=editable(self), csrf=token, read_only=read_only)
                    return self._json(payload)
                if path == "/api/inputs":
                    return self._json({**store.inputs_payload(), "editable": editable(self)})
                if path == "/api/search":
                    try:
                        return self._json({"results": search(q.get("q", ""))})
                    except RuntimeError as exc:
                        return self._json({"results": [], "error": str(exc)}, HTTPStatus.BAD_GATEWAY)
                if path == "/api/rerun":
                    return self._json(runner.status())
            except KeyError as exc:
                return self._json({"error": f"unknown run {exc}"}, HTTPStatus.NOT_FOUND)
            except Exception as exc:
                return self._json({"error": f"{type(exc).__name__}: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return self._static(path)

        def do_POST(self):
            # drain the body first: answering before reading it makes clients see a connection reset
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            raw = self.rfile.read(min(length, MAX_BODY)) if length > 0 else b""
            if length > MAX_BODY:
                self.close_connection = True  # the rest of the body is unread
            routed = self._route()
            if routed is None:
                return
            path, _ = routed
            if path not in ("/api/config", "/api/inputs", "/api/rerun"):
                return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            if read_only:
                return self._json({"error": "dashboard started with --read-only"}, HTTPStatus.METHOD_NOT_ALLOWED)
            if not editable(self):
                return self._json({"error": "config edits are only accepted from this machine (loopback); "
                                            "start with --allow-remote-edit to change that"}, HTTPStatus.FORBIDDEN)
            origin = self.headers.get("Origin")
            if origin and _host_of(urlsplit(origin).netloc) not in allowed:
                return self._json({"error": "cross-site request rejected"}, HTTPStatus.FORBIDDEN)
            if not secrets.compare_digest(self.headers.get("X-CSRF-Token", ""), token):
                return self._json({"error": "missing or stale CSRF token; reload the page"}, HTTPStatus.FORBIDDEN)
            if length <= 0 or length > MAX_BODY:
                return self._json({"error": "body missing or too large"}, HTTPStatus.BAD_REQUEST)
            try:
                body = json.loads(raw.decode("utf-8"))
                if not isinstance(body, dict):
                    raise ValueError("expected a JSON object")
            except (ValueError, UnicodeDecodeError) as exc:
                return self._json({"error": f"bad JSON: {exc}"}, HTTPStatus.BAD_REQUEST)
            try:
                if path == "/api/rerun":
                    started = runner.start()
                    return self._json({**runner.status(), "started_now": started},
                                      HTTPStatus.ACCEPTED if started else HTTPStatus.CONFLICT)
                if path == "/api/inputs":
                    payload, status = store.edit_inputs(str(body.get("kind", "")), body.get("rows"),
                                                        bool(body.get("dry_run")), body.get("base_sha"))
                    return self._json(payload, status)
                payload, status = store.edit_config(body.get("valuation") or {}, body.get("rates") or {},
                                                    bool(body.get("dry_run")), body.get("base_sha"))
            except Exception as exc:
                return self._json({"ok": False, "errors": [f"{type(exc).__name__}: {exc}"], "changes": [], "written": False},
                                  HTTPStatus.INTERNAL_SERVER_ERROR)
            return self._json(payload, status)

        def _static(self, path: str):
            rel = "index.html" if path in ("", "/") else path.lstrip("/")
            target = (STATIC / rel).resolve()
            if STATIC not in target.parents or not target.is_file():
                return self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain; charset=utf-8")
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            self._send(HTTPStatus.OK, target.read_bytes(), ctype)

    return Handler


def serve(store: Store, host: str, port: int, quiet: bool = True, allowed_hosts: tuple[str, ...] = (),
          base_path: str = "", read_only: bool = False, allow_remote_edit: bool = False,
          unsafe_bind: bool = False, runner: Runner | None = None, search=yahoo_search.search) -> ThreadingHTTPServer:
    """Refuses a non-loopback bind unless unsafe_bind: the Host check stops browsers (DNS rebinding), not other
    machines, which can simply send "Host: 127.0.0.1" and read every run, holdings included. To reach the dashboard
    from elsewhere, keep it on loopback and put a private reverse proxy (Tailscale Serve) in front."""
    if host not in LOOPBACK_BIND and not unsafe_bind:
        raise ValueError(f"refusing to bind {host!r}: the dashboard has no login and would expose your data to the "
                         "network. Keep it on 127.0.0.1 behind a private proxy, or pass unsafe_bind (--unsafe-bind).")
    mimetypes.add_type("text/javascript", ".js")
    mimetypes.add_type("text/css", ".css")
    httpd = ThreadingHTTPServer((host, port), make_handler(store, quiet, allowed_hosts, base_path, read_only, allow_remote_edit,
                                                              runner=runner, search=search))
    httpd.daemon_threads = True
    return httpd
