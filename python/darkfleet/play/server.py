"""Local web server for the 2D tactical client (standard library only).

    python -m darkfleet.play.server [--port 8765]

Single local player, bound to 127.0.0.1. Endpoints:
    GET  /                 the client
    GET  /api/state        current role-filtered state (+ debrief once the episode has ended)
    POST /api/new          {"role": "hunter"|"runner", "seed"?: int, "runner_kind"?: str}
    POST /api/act          {"actions": [ActionV1-ish dicts without role/tick]}
    POST /api/step         {"n": 1}
    GET  /api/replay       the verified replay (only after the episode has ended)
"""
from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .session import Session, new_session

STATIC = Path(__file__).parent / "static"


class App:
    def __init__(self):
        self.session: Session | None = None
        self.lock = threading.Lock()

    def handle(self, method: str, path: str, body: dict) -> tuple[int, dict | str, str]:
        """Returns (status, payload, content_type). Pure function of state; used by tests."""
        with self.lock:
            if method == "GET" and path in ("/", "/index.html"):
                return 200, (STATIC / "index.html").read_text(encoding="utf-8"), "text/html; charset=utf-8"
            if method == "POST" and path == "/api/new":
                try:
                    self.session = new_session(str(body.get("role", "hunter")), body.get("seed"), body.get("runner_kind"))
                except (ValueError, TypeError) as e:
                    return 400, {"error": str(e)}, "application/json"
                return 200, self.session.state(), "application/json"
            if self.session is None:
                return 409, {"error": "no session: POST /api/new first"}, "application/json"
            s = self.session
            if method == "GET" and path == "/api/state":
                return 200, s.state(), "application/json"
            if method == "POST" and path == "/api/act":
                acts = body.get("actions")
                if not isinstance(acts, list) or len(acts) > 16 or not all(isinstance(a, dict) for a in acts):
                    return 400, {"error": "actions must be a list of at most 16 objects"}, "application/json"
                if s.world.ended:
                    return 409, {"error": "episode has ended"}, "application/json"
                results = s.act(acts)
                return 200, {"results": results, "state": s.state()}, "application/json"
            if method == "POST" and path == "/api/step":
                try:
                    n = int(body.get("n", 1))
                except (TypeError, ValueError):
                    return 400, {"error": "n must be an integer"}, "application/json"
                s.step(n)
                return 200, s.state(), "application/json"
            if method == "GET" and path == "/api/replay":
                try:
                    return 200, s.replay_text(), "application/json"
                except PermissionError as e:
                    return 403, {"error": str(e)}, "application/json"
            return 404, {"error": "not found"}, "application/json"


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, payload, ctype: str) -> None:
            data = payload.encode("utf-8") if isinstance(payload, str) else json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _run(self, method: str) -> None:
            body: dict = {}
            if method == "POST":
                n = int(self.headers.get("Content-Length") or 0)
                if n > 1_000_000:
                    return self._send(413, {"error": "body too large"}, "application/json")
                try:
                    body = json.loads(self.rfile.read(n) or b"{}")
                except json.JSONDecodeError:
                    return self._send(400, {"error": "invalid JSON"}, "application/json")
                if not isinstance(body, dict):
                    return self._send(400, {"error": "body must be an object"}, "application/json")
            try:
                status, payload, ctype = app.handle(method, self.path.split("?")[0], body)
            except Exception as e:  # keep the server alive; report the failure
                status, payload, ctype = 500, {"error": f"{type(e).__name__}: {e}"}, "application/json"
            self._send(status, payload, ctype)

        def do_GET(self):  # noqa: N802
            self._run("GET")

        def do_POST(self):  # noqa: N802
            self._run("POST")

        def log_message(self, *args):  # quiet
            pass

    return Handler


def serve(port: int = 8765) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), make_handler(App()))


def main() -> None:
    ap = argparse.ArgumentParser(description="Dark Fleet 2D tactical client (local)")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    srv = serve(args.port)
    print(f"Dark Fleet client: http://127.0.0.1:{args.port}/  (Ctrl+C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
