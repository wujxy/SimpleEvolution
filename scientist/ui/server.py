"""Read-only HTTP and SSE transport for Scientist Observatory."""
from __future__ import annotations

import argparse
import json
import threading
from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from .projector import RunProjector
from .reader import RunLayout, RunReader


_CSP = (
    "default-src 'self'; connect-src 'self'; script-src 'self'; "
    "style-src 'self'; object-src 'none'; base-uri 'none'"
)


class Observatory:
    """Own the in-memory projection and its service-lifetime deltas."""

    def __init__(self, layout: RunLayout, poll_seconds: float = 1.0):
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive")
        self.reader = RunReader(layout)
        self.projector = RunProjector(layout.safe_metadata())
        self.poll_seconds = poll_seconds
        self.condition = threading.Condition()
        self.stop_event = threading.Event()
        self._deltas: deque[dict[str, object]] = deque(maxlen=10_000)
        self._next_delta = 1
        self._thread: threading.Thread | None = None

    def snapshot(self) -> dict[str, object]:
        return self.projector.snapshot()

    def poll_once(self) -> list[dict[str, object]]:
        projected = self.projector.apply(self.reader.poll())
        deltas: list[dict[str, object]] = []
        with self.condition:
            for item in projected:
                delta = {
                    "id": f"delta-{self._next_delta}",
                    "type": item["type"],
                    "data": item["data"],
                }
                self._next_delta += 1
                self._deltas.append(delta)
                deltas.append(delta)
            if deltas:
                self.condition.notify_all()
        return deltas

    def events_after(
        self,
        cursor: str | None,
    ) -> list[dict[str, object]]:
        with self.condition:
            items = list(self._deltas)
        if cursor is None:
            return items
        for index, item in enumerate(items):
            if item["id"] == cursor:
                return items[index + 1:]
        if not items:
            return [{
                "id": "snapshot-required",
                "type": "snapshot_required",
                "data": {},
            }]
        return [{
            "id": str(items[-1]["id"]),
            "type": "snapshot_required",
            "data": {},
        }]

    def start(self) -> None:
        if self._thread is not None:
            return
        self.stop_event.clear()

        def _poll() -> None:
            while not self.stop_event.is_set():
                self.poll_once()
                self.stop_event.wait(self.poll_seconds)

        self._thread = threading.Thread(
            target=_poll, name="scientist-observatory", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        with self.condition:
            self.condition.notify_all()
        if self._thread is not None:
            self._thread.join(timeout=max(2.0, self.poll_seconds * 2))
            self._thread = None


def encode_sse(delta: dict[str, object]) -> bytes:
    payload = json.dumps(delta["data"], ensure_ascii=False)
    return (
        f"id: {delta['id']}\n"
        f"event: {delta['type']}\n"
        f"data: {payload}\n\n"
    ).encode("utf-8")


class _ObservatoryServer(ThreadingHTTPServer):
    daemon_threads = True
    observatory: Observatory


def _handler_class():
    class Handler(BaseHTTPRequestHandler):
        server: _ObservatoryServer

        def log_message(self, format, *args):
            return

        def _headers(
            self,
            status: int,
            content_type: str,
            length: int | None = None,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", _CSP)
            if length is not None:
                self.send_header("Content-Length", str(length))
            self.end_headers()

        def _json(self, value: object, status: int = 200) -> None:
            body = json.dumps(
                value, ensure_ascii=False, separators=(",", ":"),
            ).encode("utf-8")
            self._headers(status, "application/json; charset=utf-8", len(body))
            self.wfile.write(body)

        def _not_found(self) -> None:
            self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

        def _static(self, name: str, content_type: str) -> None:
            try:
                body = files("scientist.ui").joinpath(
                    "static", name).read_bytes()
            except (FileNotFoundError, OSError):
                self._not_found()
                return
            self._headers(HTTPStatus.OK, content_type, len(body))
            self.wfile.write(body)

        def _method_not_allowed(self) -> None:
            body = b'{"error":"method not allowed"}'
            self.send_response(HTTPStatus.METHOD_NOT_ALLOWED)
            self.send_header("Allow", "GET")
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        do_POST = _method_not_allowed
        do_PUT = _method_not_allowed
        do_PATCH = _method_not_allowed
        do_DELETE = _method_not_allowed

        def do_GET(self) -> None:
            parsed = urlsplit(self.path)
            if parsed.path == "/":
                self._static("index.html", "text/html; charset=utf-8")
                return
            if parsed.path == "/static/app.js":
                self._static(
                    "app.js", "application/javascript; charset=utf-8")
                return
            if parsed.path == "/static/style.css":
                self._static("style.css", "text/css; charset=utf-8")
                return
            if parsed.path == "/api/snapshot":
                self._json(self.server.observatory.snapshot())
                return
            if parsed.path == "/api/events":
                cursor = parse_qs(parsed.query).get("after", [None])[0]
                self._json(self.server.observatory.events_after(cursor))
                return
            if parsed.path.startswith("/api/details/"):
                detail_id = unquote(parsed.path[len("/api/details/"):])
                try:
                    detail = self.server.observatory.reader.detail_index.read(
                        detail_id)
                except (KeyError, OSError):
                    self._not_found()
                    return
                self._json(detail)
                return
            if parsed.path == "/api/stream":
                self._stream(parsed.query)
                return
            self._not_found()

        def _stream(self, query: str) -> None:
            cursor = (
                self.headers.get("Last-Event-ID")
                or parse_qs(query).get("after", [None])[0]
            )
            self._headers(HTTPStatus.OK, "text/event-stream; charset=utf-8")
            try:
                while not self.server.observatory.stop_event.is_set():
                    deltas = self.server.observatory.events_after(cursor)
                    if deltas:
                        for delta in deltas:
                            self.wfile.write(encode_sse(delta))
                            cursor = str(delta["id"])
                        self.wfile.flush()
                    else:
                        self.wfile.write(b": heartbeat\n\n")
                        self.wfile.flush()
                    with self.server.observatory.condition:
                        self.server.observatory.condition.wait(timeout=15)
            except (BrokenPipeError, ConnectionResetError):
                return

    return Handler


def make_server(
    observatory: Observatory,
    host: str,
    port: int,
) -> ThreadingHTTPServer:
    server = _ObservatoryServer((host, port), _handler_class())
    server.observatory = observatory
    return server


def positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="scientist-observatory")
    parser.add_argument(
        "--run-dir", required=True, type=Path,
        help="one run directory containing world/.scientist")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8765, type=int)
    parser.add_argument(
        "--poll-seconds", default=1.0, type=positive_float)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    layout = RunLayout.discover(args.run_dir)
    observatory = Observatory(layout, poll_seconds=args.poll_seconds)
    server = make_server(observatory, args.host, args.port)
    observatory.start()
    print(
        f"Scientist Observatory: http://{args.host}:{server.server_port} "
        f"(run: {layout.run_dir})",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        observatory.stop()
        server.server_close()
    return 0
