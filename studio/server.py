"""
LLM Data Studio: compose, review and export TaskItems (phase 8 store); read-only corpus views.

    uv run python -m studio.server [--port 8767] [--db PATH] [--lan [--token T]]
    (or double-click studio/llm-studio.cmd)

Binds 127.0.0.1. --lan binds every interface and then requires the token (printed at start) on
every API call: open http://<host>:8767/?token=<T>. Reviewer name: LLM_STUDIO_USER or the OS
account; a LAN browser may send its own (X-Studio-User, set in the page header).
"""
import argparse
import json
import mimetypes
import secrets
import sqlite3
import sys
from contextlib import nullcontext
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from studio import db, items, jobs
from studio.routes import api

WEB = Path(__file__).resolve().parent / "web"


class Handler(BaseHTTPRequestHandler):
    con: sqlite3.Connection
    worker: jobs.Worker
    token: str | None = None

    def log_message(self, *args):  # request bodies hold item text; keep the console quiet
        pass

    def send_json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path):
        data = path.read_bytes()
        kind = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", kind + ("; charset=utf-8" if kind.startswith(("text/", "application/j"))
                                                 else ""))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def user(self):
        name = unquote(self.headers.get("X-Studio-User") or "").strip()
        return name or db.user()

    def route(self, method):
        url = urlparse(self.path)
        parts = [unquote(p) for p in url.path.strip("/").split("/") if p]
        try:
            if not parts or parts[0] != "api":
                target = (WEB / "/".join(parts or ["index.html"])).resolve()
                if target.is_relative_to(WEB) and target.is_file():
                    return self.send_file(target)
                return self.send_json({"error": "không có trang này"}, 404)
            if self.token and not secrets.compare_digest(self.headers.get("X-Studio-Token") or "", self.token):
                return self.send_json({"error": "sai hoặc thiếu token"}, 401)
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            body = self.body() if method != "GET" else {}
            # Corpus reads touch only Parquet and may take seconds: they must not hold up item review.
            with nullcontext() if parts[1:2] == ["corpus"] else db.LOCK:
                result = api(self, method, parts[1:], q, body)
            self.send_json(result)
        except (ValueError, KeyError, json.JSONDecodeError) as e:
            self.send_json({"error": str(e).strip("'\"")}, 400)
        except FileNotFoundError as e:
            self.send_json({"error": str(e)}, 404)
        except sqlite3.IntegrityError as e:
            self.send_json({"error": f"không hợp lệ: {e}"}, 409)
        except sqlite3.Error as e:
            self.send_json({"error": f"lỗi SQLite: {e}"}, 500)
        except RuntimeError as e:
            self.send_json({"error": str(e)}, 502)

    def do_GET(self):
        self.route("GET")

    def do_POST(self):
        self.route("POST")

    def do_PUT(self):
        self.route("PUT")


def make_server(db_path, port, lan=False, token=None):
    Handler.con = db.connect(db_path)
    Handler.worker = jobs.Worker(db_path)
    Handler.token = (token or secrets.token_urlsafe(16)) if lan else None
    items.options(Handler.con)  # fail fast on a broken taxonomy before serving
    return ThreadingHTTPServer(("0.0.0.0" if lan else "127.0.0.1", port), Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument("--db", type=Path, default=None, help="task store (default: DATA_ROOT/taskset/taskset.db)")
    parser.add_argument("--lan", action="store_true", help="listen on every interface; API requires the token")
    parser.add_argument("--token")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    from taskset import store
    server = make_server(args.db or store.DEFAULT_DB, args.port, args.lan, args.token)
    suffix = f"/?token={Handler.token}" if Handler.token else ""
    print(f"LLM Data Studio: http://127.0.0.1:{args.port}{suffix}  db={args.db or store.DEFAULT_DB}  (Ctrl+C để tắt)",
          flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
