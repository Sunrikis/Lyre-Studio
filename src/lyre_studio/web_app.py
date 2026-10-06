"""Local-only web control surface. Python 3.8+, standard library only."""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import threading
from urllib.parse import urlsplit, parse_qs
import webbrowser

from .library import Library, ScoreError, MAX_FILE_BYTES
from .studio_engine import Engine
from .settings import song_directory, initialize_library
from . import __version__

ROOT = Path(__file__).resolve().parent
ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/favicon.svg": ("favicon.svg", "image/svg+xml"),
}


class StudioServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, port, library, engine):
        super().__init__(("127.0.0.1", port), Handler)
        self.library, self.engine = library, engine
        self.token = secrets.token_urlsafe(32)
        self.hosts = {
            "127.0.0.1:{}".format(self.server_port),
            "localhost:{}".format(self.server_port),
        }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def send(self, status, body, content_type="application/json; charset=utf-8", download=False):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; media-src 'self' blob:; object-src 'none'; frame-ancestors 'none'",
        )
        if download:
            self.send_header("Content-Disposition", 'attachment; filename="lyre-score.json"')
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def trusted_host(self):
        if self.headers.get("Host", "").lower() not in self.server.hosts:
            self.send(403, {"error": "只接受本机演奏台地址。"})
            return False
        return True

    def do_GET(self):
        if not self.trusted_host():
            return
        parsed = urlsplit(self.path)
        params = parse_qs(parsed.query)
        try:
            if parsed.path in ASSETS:
                name, mime = ASSETS[parsed.path]
                content = (ROOT / "web" / name).read_bytes()
                if name == "index.html":
                    content = content.replace(
                        b"__SESSION_TOKEN__", self.server.token.encode("ascii")
                    )
                self.send(200, content, mime)
            elif parsed.path == "/api/status":
                self.send(
                    200,
                    dict(
                        self.server.engine.snapshot(),
                        application="lyre-studio-v1",
                        version=__version__,
                    ),
                )
            elif parsed.path == "/api/library":
                self.send(200, {"songs": self.server.library.list()})
            elif parsed.path in ("/api/song", "/api/export"):
                identity = params.get("id", [""])[0]
                song = self.server.library.load(identity)
                if parsed.path == "/api/song":
                    self.send(200, song.detail())
                else:
                    self.send(
                        200, (self.server.library.directory / song.id).read_bytes(), download=True
                    )
            else:
                self.send(404, {"error": "页面不存在。"})
        except (ScoreError, OSError, ValueError) as exc:
            self.send(400, {"error": str(exc)})

    def do_POST(self):
        if not self.trusted_host():
            return
        origin = self.headers.get("Origin")
        if origin is not None and origin not in {"http://" + h for h in self.server.hosts}:
            self.send(403, {"error": "请求来源不匹配，请在本机演奏台操作。"})
            return
        if not secrets.compare_digest(self.headers.get("X-Lyre-Token", ""), self.server.token):
            self.send(403, {"error": "会话已更新，请刷新页面。"})
            return
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            self.send(415, {"error": "请求必须为 JSON。"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size <= 0 or size > MAX_FILE_BYTES:
                self.send(413, {"error": "文件内容为空或超过 10 MB。"})
                return
            data = json.loads(self.rfile.read(size).decode("utf-8-sig"))
            if not isinstance(data, dict):
                raise ScoreError("请求必须为 JSON 对象。")
            if self.path == "/api/command":
                payload = data.get("payload", {})
                if not isinstance(payload, dict):
                    raise ScoreError("操作参数应为对象。")
                self.send(200, self.server.engine.submit(data.get("action"), payload))
            elif self.path == "/api/import":
                song = self.server.library.import_data(data.get("score"))
                self.send(201, song.summary())
            else:
                self.send(404, {"error": "操作不存在。"})
        except (ScoreError, OSError, ValueError, TypeError, UnicodeError) as exc:
            self.send(400, {"error": str(exc)})
        except Exception:
            self.send(500, {"error": "操作未完成，请重试或重新启动演奏台。"})


def main(argv=None):
    parser = argparse.ArgumentParser(description="21键网页演奏台")
    parser.add_argument("--port", type=int, default=8771)
    parser.add_argument("--demo", action="store_true", help="仅网页试听，不创建游戏按键连接")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--songs", help="曲库目录；也可设置 LYRE_STUDIO_SONGS")
    args = parser.parse_args(argv)
    library = Library(initialize_library(song_directory(args.songs)))
    engine = Engine(library, demo=args.demo)
    try:
        server = StudioServer(args.port, library, engine)
    except OSError:
        print("端口 {} 已占用。如果演奏台已经打开，请直接使用现有页面。".format(args.port))
        return 1
    try:
        engine.start()
        worker = threading.Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True
        )
        worker.start()
        url = "http://127.0.0.1:{}/".format(server.server_port)
        print(
            "网页演奏台：{}\nF5 完整 / F6 旋律 / F7 伴奏；F8 开始/暂停，F9 停止，F10 退出；Insert/Delete 跳段，PageUp/PageDown 切歌。".format(
                url
            ),
            flush=True,
        )
        if not args.no_browser:
            webbrowser.open(url)
        try:
            while not engine.stop_event.wait(0.25):
                pass
        except KeyboardInterrupt:
            pass
        finally:
            engine.close()
            server.shutdown()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
