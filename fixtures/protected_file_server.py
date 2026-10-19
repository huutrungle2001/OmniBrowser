"""Local, test-only fixtures. No real credentials or external resources."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import time
from urllib.parse import urlsplit, parse_qs

PDF = b"%PDF-1.4\n% isolated fixture\n" + b"fixture-data\n" * 10000 + b"%%EOF\n"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def handle(self):
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def send(self, body=b"", status=200, content_type="text/html", headers=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        parts = urlsplit(self.path)
        path = parts.path
        self.server.requests.append((path, self.headers.get("Cookie", ""), self.headers.get("Authorization")))
        if path in ("/interactive.html", "/content.html"):
            return self.send((Path(__file__).parent / path[1:]).read_bytes())
        if path == "/login":
            return self.send(b"<html><form>Log in</form></html>")
        if path.startswith("/protected/"):
            if "session=fixture-secret" not in self.headers.get("Cookie", ""):
                return self.send(status=302, headers={"Location": "/login"})
            return self.send(PDF, content_type="application/pdf")
        if path == "/html.pdf":
            return self.send(b"<!doctype html><html>Login</html>")
        if path == "/disguised.pdf":
            return self.send(b"<html>Login</html>", content_type="application/octet-stream")
        if path == "/prefixed-login":
            return self.send(b'\xef\xbb\xbf<!-- gateway -->\n<?xml version="1.0"?>\n<html><form>Log in</form></html>',
                             content_type="application/octet-stream")
        if path == "/wrong.pdf":
            return self.send(b"not a PDF", content_type="application/pdf")
        if path == "/unauthorized":
            return self.send(status=401)
        if path == "/post-only":
            return self.send(status=405, headers={"Allow": "POST"})
        if path == "/redirect":
            target = parse_qs(parts.query)["to"][0]
            return self.send(status=302, headers={"Location": target})
        if path == "/loop":
            return self.send(status=302, headers={"Location": "/loop"})
        if path == "/cookie.pdf":
            return self.send(PDF, content_type="application/pdf", headers={"Set-Cookie": "hop=redirect-secret; Path=/"})
        if path == "/stream.pdf":
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            try:
                for i in range(12):
                    self.wfile.write((b"%PDF-1.4\n" if i == 0 else b"") + b"x" * 8192)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        if path == "/slow":
            time.sleep(2)
            return self.send(PDF, content_type="application/pdf")
        if path == "/drip.pdf":
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            try:
                self.wfile.write(b"%PDF-1.4\n")
                self.wfile.flush()
                for _ in range(100):
                    time.sleep(.03)
                    self.wfile.write(b"x")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        if path == "/dense":
            return self.send(('<main><h1>Dense unicode 測試</h1><p>' + '測試 text\\\" ' * 10000 +
                             '</p>' + ''.join(f'<button>Control {i}</button><a href="/file/{i}.pdf">File {i}</a>' for i in range(200)) + '</main>').encode())
        if path == "/endless":
            return self.send(b'<main>Never network idle</main><script>fetch("/slow")</script>')
        if path == "/frame":
            return self.send(b'<button>Frame-only control</button>')
        return self.send(b"<main>Other fixture page</main>")


def make_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    server.requests = []
    return server
