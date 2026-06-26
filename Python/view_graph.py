#!/usr/bin/env python3
"""
AXIOM - View Graph

Convenience wrapper around 04_graph_export.py. Exports the curated
knowledge graph in cytoscape-js format, serves it on a local HTTP
server with the companion viewer, and opens a browser tab automatically.

On every fetch of graph.json, checks whether axiom_graph.db has changed
since the last export and re-runs the export if so. Hitting F5 in the
browser is therefore enough to see graph updates committed since the
wrapper was launched. No need to restart the wrapper.

Output directory: Python/export/ (gitignore as you see fit).
Press Ctrl+C in the launching console to shut down.
"""

import http.server
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
EXPORT_SCRIPT = SCRIPT_DIR / "04_graph_export.py"
EXPORT_DIR = SCRIPT_DIR / "export"
GRAPH_DB = SCRIPT_DIR / "lib" / "data" / "axiom_graph.db"
GRAPH_JSON = EXPORT_DIR / "graph.json"

_export_lock = threading.Lock()


def find_free_port():
    """Bind to port 0 to get an OS-assigned free port, return it."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run_export():
    """Run 04_graph_export.py in cytoscape-js mode. Return True on success."""
    cmd = [
        sys.executable,
        str(EXPORT_SCRIPT),
        "--format", "cytoscape-js",
        "--output-dir", str(EXPORT_DIR),
    ]
    result = subprocess.run(cmd, cwd=str(SCRIPT_DIR))
    return result.returncode == 0


def export_is_stale():
    """True if the DB is newer than the exported graph.json, or no export exists."""
    if not GRAPH_JSON.exists():
        return True
    if not GRAPH_DB.exists():
        # Nothing to export from. Whatever's already there is what we have.
        return False
    return GRAPH_DB.stat().st_mtime > GRAPH_JSON.stat().st_mtime


class GraphHandler(http.server.SimpleHTTPRequestHandler):
    """Serves files from EXPORT_DIR. Re-exports on graph.json fetch if stale."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(EXPORT_DIR), **kwargs)

    def do_GET(self):
        if self.path.split("?")[0] == "/graph.json":
            with _export_lock:
                if export_is_stale():
                    print(
                        "axiom_graph.db has changed since last export; re-exporting...",
                        file=sys.stderr,
                    )
                    if not run_export():
                        self.send_error(500, "Re-export failed; see wrapper console.")
                        return
        return super().do_GET()


def wait_for_server(port, timeout=5.0):
    """Poll the port until it accepts connections, or until timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def main():
    if not EXPORT_SCRIPT.exists():
        print(f"ERROR: {EXPORT_SCRIPT} not found.", file=sys.stderr)
        return 1
    if not GRAPH_DB.exists():
        print(f"ERROR: graph database not found at {GRAPH_DB}.", file=sys.stderr)
        return 1

    print("Running initial graph export...", file=sys.stderr)
    if not run_export():
        print("Initial export failed; aborting.", file=sys.stderr)
        return 1

    port = find_free_port()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), GraphHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    if not wait_for_server(port):
        print(f"ERROR: server did not come up on port {port}.", file=sys.stderr)
        server.shutdown()
        server.server_close()
        return 1

    url = f"http://127.0.0.1:{port}/viewer.html"
    print(f"\nServing AXIOM graph viewer at {url}", file=sys.stderr)
    print(
        "F5 in the browser refreshes; the wrapper re-exports if the DB has changed.",
        file=sys.stderr,
    )
    print("Press Ctrl+C to stop.\n", file=sys.stderr)

    webbrowser.open(url)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down...", file=sys.stderr)
        server.shutdown()
        server.server_close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
