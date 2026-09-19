"""Local preview server for web/index.html - zero extra dependencies.

Usage:
    python web/serve.py [--port 8000] [--no-browser]

Serves the web/ directory over plain HTTP using only the standard library
(http.server), consistent with this project's stance of not adding
dependencies for anything that doesn't need one. Opens the default browser
automatically unless --no-browser is passed.
"""

from __future__ import annotations

import argparse
import functools
import http.server
import webbrowser
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent


def run(port: int, open_browser: bool) -> None:
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(WEB_DIR))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    url = f"http://127.0.0.1:{port}/index.html"

    print(f"Trip Hunter landing page preview: {url}")
    print("Ctrl+C zum Beenden.")

    if open_browser:
        webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve web/index.html locally for preview.")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true", help="Do not auto-open a browser tab.")
    args = parser.parse_args()

    run(port=args.port, open_browser=not args.no_browser)


if __name__ == "__main__":
    main()
