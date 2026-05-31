#!/usr/bin/env python3
"""Serve the HTML report on LAN so phones / WeChat can open it via http://."""

from __future__ import annotations

import http.server
import socket
import socketserver
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
PORT = 8765


def _lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(REPORTS), **kwargs)

    def log_message(self, format: str, *args) -> None:
        print(f"[{self.address_string()}] {format % args}")


def main() -> None:
    if not (REPORTS / "FINAL_REPORT.html").exists():
        raise SystemExit("Run scripts/generate_report.py first.")

    ip = _lan_ip()
    url = f"http://{ip}:{PORT}/FINAL_REPORT.html"
    mobile_url = f"http://{ip}:{PORT}/FINAL_REPORT.mobile.html"

    print("Serving reports/ on LAN")
    print(f"  标准版: {url}")
    print(f"  单文件: {mobile_url}")
    print("  微信：复制上述链接发到「文件传输助手」或聊天，点击打开")
    print("  Ctrl+C 停止\n")

    with socketserver.TCPServer(("0.0.0.0", PORT), Handler) as httpd:
        try:
            webbrowser.open(f"http://127.0.0.1:{PORT}/FINAL_REPORT.html")
        except Exception:
            pass
        httpd.serve_forever()


if __name__ == "__main__":
    main()
