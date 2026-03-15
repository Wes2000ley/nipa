#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

import argparse
import functools
import mimetypes
import os

from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer


def parse_args():
    parser = argparse.ArgumentParser(
        description="Serve local vmksft artifacts with inline text rendering for raw logs.",
    )
    parser.add_argument("--bind", default="0.0.0.0",
                        help="Bind address")
    parser.add_argument("--port", type=int, required=True,
                        help="TCP port to listen on")
    parser.add_argument("--directory", required=True,
                        help="Document root to serve")
    return parser.parse_args()


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


def main():
    args = parse_args()
    handler = functools.partial(
        VmksftHTTPRequestHandler,
        directory=args.directory,
    )
    mimetypes.add_type("text/plain", ".log")
    mimetypes.add_type("text/plain", ".config")

    with ThreadingHTTPServer((args.bind, args.port), handler) as httpd:
        httpd.serve_forever()


if __name__ == "__main__":
    main()
