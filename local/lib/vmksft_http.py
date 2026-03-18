#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

import functools
import mimetypes
import os
import threading

from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer


class VmksftHTTPRequestHandler(SimpleHTTPRequestHandler):
    def guess_type(self, path):
        ctype = super().guess_type(path)
        norm = path.replace(os.sep, "/")
        base = os.path.basename(path)
        _, ext = os.path.splitext(base)

        if "/query/" in norm:
            return "application/json; charset=utf-8"
        if not ext:
            return "text/plain; charset=utf-8"
        if ext == ".json":
            return "application/json; charset=utf-8"
        if ext in [".html", ".htm"]:
            return "text/html; charset=utf-8"
        if ext in [".log", ".txt", ".config"]:
            return "text/plain; charset=utf-8"
        return ctype

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


def create_http_server(bind, port, directory):
    handler = functools.partial(
        VmksftHTTPRequestHandler,
        directory=str(directory),
    )
    mimetypes.add_type("text/plain", ".log")
    mimetypes.add_type("text/plain", ".config")
    return ThreadingHTTPServer((bind, port), handler)


def serve_http_in_thread(httpd):
    thread = threading.Thread(
        target=httpd.serve_forever,
        name="local-vmksft-http",
        daemon=True,
    )
    thread.start()
    return thread
