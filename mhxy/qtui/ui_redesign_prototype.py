"""Throwaway UI study. Run: python -m mhxy.qtui.ui_redesign_prototype.

Two visual editions of the A workbench: frozen 1.0 and light desktop study 3.0.
The real app uses Qt Widgets and has no web route; this separate preview never
imports the runtime, reads configuration, or controls game windows.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
import argparse


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8768)
    args = parser.parse_args()
    source = Path(__file__).with_suffix(".html")

    class Preview(BaseHTTPRequestHandler):
        def do_GET(self):
            request = urlsplit(self.path)
            if request.path not in ("/", "/ui-prototype"):
                self.send_error(404)
                return
            version = parse_qs(request.query).get("version", ["3"])[0]
            if version == "1":
                # Keep the frozen source unchanged; add comparison links only
                # to the preview dock of the served response.
                content = source.with_name("ui_redesign_prototype_v1.html").read_text(encoding="utf-8")
                content = content.replace(
                    '<span class="dock-title">A · 青玉工作台</span>',
                    '<span class="dock-title">1.0 · 青玉工作台（候选）</span>'
                    '<a href="?variant=A&amp;version=3" style="color:inherit">查看 3.0</a>',
                ).encode("utf-8")
            else:
                content = source.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

    print(f"UI 原型（模拟数据）: http://127.0.0.1:{args.port}/ui-prototype?variant=A&version=3", flush=True)
    ThreadingHTTPServer(("127.0.0.1", args.port), Preview).serve_forever()


if __name__ == "__main__":
    main()
