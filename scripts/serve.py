"""Local dev server for the dashboard.

Most browsers block file:// URLs from fetching local JSON files for security
reasons, which would prevent the dashboard from loading data. This helper
serves the workspace folder over HTTP and opens the dashboard in the default
browser.

    python scripts/serve.py             # serves on port 8000
    python scripts/serve.py --port 8080 # custom port

Press Ctrl+C to stop.
"""

from __future__ import annotations

import argparse
import http.server
import socketserver
import webbrowser
from functools import partial
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the dashboard locally.")
    parser.add_argument("--port", type=int, default=8000, help="Port (default 8000)")
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Don't auto-open the browser",
    )
    args = parser.parse_args()

    handler = partial(http.server.SimpleHTTPRequestHandler, directory=str(REPO_ROOT))
    url = f"http://localhost:{args.port}/dashboard/index.html"

    print(f"Serving {REPO_ROOT}")
    print(f"Dashboard: {url}")
    print("Press Ctrl+C to stop.")
    print()

    if not args.no_browser:
        webbrowser.open(url)

    # Bind to 127.0.0.1 (loopback only) so Windows Firewall doesn't prompt.
    # Loopback is unreachable from other devices on the network, which is
    # exactly what we want for a personal dashboard.
    try:
        with socketserver.TCPServer(("127.0.0.1", args.port), handler) as httpd:
            httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
