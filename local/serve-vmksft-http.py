#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

import argparse

from vmksft_http import create_http_server


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

def main():
    args = parse_args()
    with create_http_server(args.bind, args.port, args.directory) as httpd:
        httpd.serve_forever()


if __name__ == "__main__":
    main()
