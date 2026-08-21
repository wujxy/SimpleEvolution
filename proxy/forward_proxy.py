#!/usr/bin/env python3
"""Small HTTP CONNECT forward proxy with a source-subnet ACL (stdlib only).

Backend for ``proxy/setup_proxy.sh`` when no tinyproxy is installed on the
jump host: binds ``BIND_ADDR:PORT`` and accepts peers whose source address
falls inside one of the ``ALLOW_SUBNETS`` networks.  It speaks plain-HTTP
forwarding (absolute-form request targets) and HTTP CONNECT tunnels restricted
to ports 80/443 — enough for third-party model APIs (DeepSeek / Zhipu /
Anthropic / OpenAI are all HTTPS, so the CONNECT path is the one that
matters).  No root, no pip packages, no external dependencies: python3 alone
is enough, which is exactly the situation on a jump host where the admin has
not installed a proxy.

Why not pproxy: pproxy has no source-IP ACL without root, and ``pip install``
assumes PyPI is reachable — a host allowed outbound to api.deepseek.com may
still block pypi.org.

Environment:
    ALLOW_SUBNETS   space-separated CIDR list of source networks allowed to use
                    the proxy (default: 127.0.0.1/32).
    BIND_ADDR       interface to listen on (default: 0.0.0.0).
    PORT            TCP port to listen on (default: 3128).

Exit code 1 on listen failure; logs one line per event to stderr (the shell
script redirects it into a per-user log file).
"""
from __future__ import annotations

import ipaddress
import os
import socket
import socketserver
import sys
import threading
from urllib.parse import urlsplit

# Plain HTTP forwarding + CONNECT are both limited to these ports, mirroring
# tinyproxy's ConnectPort.  Model API endpoints are 443; 80 is kept for any
# http:// fallback URL.  Overridable (comma-separated) for providers that use
# e.g. 8443, and used by the local tests.
CONNECT_PORTS = frozenset(
    int(p) for p in os.environ.get("CONNECT_PORTS", "80,443").split(",") if p.strip()
)

_MAX_HEADER_BYTES = 64 * 1024  # request start-line + headers cap
_BUF = 64 * 1024               # tunnel relay buffer


def _load_subnets() -> list[ipaddress._BaseNetwork]:
    nets = [n for n in os.environ.get("ALLOW_SUBNETS", "127.0.0.1/32").split() if n]
    return [ipaddress.ip_network(n) for n in nets]


NETS = _load_subnets()
BIND_ADDR = os.environ.get("BIND_ADDR", "0.0.0.0")
PORT = int(os.environ.get("PORT", "3128"))


def _allowed(peer: str) -> bool:
    try:
        ip = ipaddress.ip_address(peer)
    except ValueError:
        return False
    return any(ip in net for net in NETS)


def _pump(src: socket.socket, dst: socket.socket) -> None:
    """Copy src -> dst until EOF, then half-close dst so the far side sees the
    end of the stream.  A broken pipe merely ends the copy."""
    try:
        while True:
            chunk = src.recv(_BUF)
            if not chunk:
                break
            dst.sendall(chunk)
    except OSError:
        pass
    finally:
        try:
            dst.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def _read_request(sock: socket.socket, timeout: float = 60.0) -> bytes | None:
    """Read one request: start line + headers, plus any Content-Length body.

    Returns the raw bytes as received (headers ``\\r\\n\\r\\n``-terminated, body
    appended) or ``None`` if the peer disconnects or exceeds the header cap.
    """
    sock.settimeout(timeout)
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(_BUF)
        if not chunk:
            return None
        buf += chunk
        if len(buf) > _MAX_HEADER_BYTES:
            return None
    head, _, rest = buf.partition(b"\r\n\r\n")
    content_length = 0
    for line in head.split(b"\r\n")[1:]:
        if line.lower().startswith(b"content-length:"):
            try:
                content_length = int(line.split(b":", 1)[1].strip())
            except ValueError:
                pass
    if content_length:
        need = content_length - len(rest)
        if need > 0:
            sock.settimeout(30.0)
            while need > 0:
                chunk = sock.recv(min(need, _BUF))
                if not chunk:
                    return None
                rest += chunk
                need -= len(chunk)
    return buf


class ProxyHandler(socketserver.BaseRequestHandler):
    """One accepted connection; ``self.request`` is the raw socket."""

    def handle(self) -> None:
        sock = self.request
        if not _allowed(self.client_address[0]):
            sock.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
            return
        try:
            req = _read_request(sock)
            if req is None:
                return
            start = req.split(b"\r\n", 1)[0]
            try:
                method, target, _version = start.decode("latin1").split(" ", 2)
            except ValueError:
                return
            if method.upper() == "CONNECT":
                self._connect(sock, target)
            else:
                self._http_forward(sock, method, target, req)
        except (ConnectionError, OSError, ValueError):
            return
        finally:
            try:
                sock.close()
            except OSError:
                pass

    # -- CONNECT tunnel -------------------------------------------------

    def _connect(self, sock: socket.socket, target: str) -> None:
        host, _, port_s = target.partition(":")
        try:
            port = int(port_s)
        except ValueError:
            sock.sendall(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
            return
        if port not in CONNECT_PORTS:
            sock.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
            return
        try:
            remote = socket.create_connection((host, port), timeout=30)
        except OSError:
            sock.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
            return
        sock.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
        sock.settimeout(None)
        remote.settimeout(None)
        # The handler's own thread drives one direction so the tunnel tears
        # down the moment either side closes; the other direction is a daemon
        # thread that does not outlive the process.
        pump = threading.Thread(target=_pump, args=(remote, sock), daemon=True)
        pump.start()
        try:
            _pump(sock, remote)
        finally:
            remote.close()

    # -- plain HTTP forwarding ------------------------------------------

    def _http_forward(
        self, sock: socket.socket, method: str, target: str, req: bytes
    ) -> None:
        try:
            parts = urlsplit(target)
        except ValueError:
            sock.sendall(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
            return
        if parts.scheme != "http" or not parts.hostname:
            sock.sendall(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
            return
        host = parts.hostname
        port = parts.port or 80
        if port not in CONNECT_PORTS:
            port = 80
        # Rewrite to origin-form: absolute path, Host from the request target,
        # hop-by-hop headers stripped, connection forced to close so the
        # response ends cleanly for the client.
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        head, _, body = req.partition(b"\r\n\r\n")
        out = [f"{method} {path} HTTP/1.1"]
        for line in head.split(b"\r\n")[1:]:
            name = line.split(b":", 1)[0].strip().lower()
            if name in (b"proxy-connection", b"connection", b"proxy-authorization"):
                continue
            if name == b"host":
                out.append("Host: " + parts.netloc)
            else:
                out.append(line.decode("latin1"))
        out.append("Connection: close")
        payload = "\r\n".join(out).encode("latin1") + b"\r\n\r\n" + body
        try:
            remote = socket.create_connection((host, port), timeout=30)
        except OSError:
            sock.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
            return
        try:
            remote.sendall(payload)
            _pump(remote, sock)
        finally:
            remote.close()


class ProxyServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> int:
    if not NETS:
        print("forward_proxy: ALLOW_SUBNETS is empty — refusing to run an open proxy",
              file=sys.stderr)
        return 1
    try:
        server = ProxyServer((BIND_ADDR, PORT), ProxyHandler)
    except OSError as exc:
        print(f"forward_proxy: cannot listen on {BIND_ADDR}:{PORT}: {exc}",
              file=sys.stderr)
        return 1
    print(f"forward_proxy: listening on {BIND_ADDR}:{PORT} "
          f"(allowed subnets: {', '.join(str(n) for n in NETS)})",
          file=sys.stderr, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
