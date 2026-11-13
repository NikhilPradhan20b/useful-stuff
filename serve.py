"""Local server for the dashboard.

    python3 serve.py [--port 8000]

Serves dashboard.html at / and adds a /status endpoint the page polls, so the
view refreshes on its own when a scrape writes a new jobs.json.
"""
import argparse
import json
import pathlib
import re
import subprocess
import sys
import time
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).parent
LOG = ROOT / "run.log"
SERVED = {"/", "/index.html", "/dashboard.html", "/jobs.json", "/status"}

PROC = None            # the run.py we launched, if any
PUBLIC = False         # when tunnelled, /run is off: it spawns processes
STAGE_T0 = {}          # stage name -> when we first saw it, for the ETA
STAGE = re.compile(r"^=== (.+?) ===$", re.M)
PROGRESS = re.compile(r"^\s*(\d+)/(\d+)\s*$", re.M)


def scrape_running():
    """True while a scrape is in flight, so the page can say so."""
    if PROC is not None and PROC.poll() is None:
        return True
    return subprocess.run(["pgrep", "-f", r"(scrape|rescue)\.py"],
                          capture_output=True).returncode == 0


def start_run():
    """Launch the pipeline. Returns False if one is already going."""
    global PROC
    if scrape_running():
        return False
    LOG.write_text("")
    STAGE_T0.clear()
    fh = open(LOG, "a")
    PROC = subprocess.Popen([sys.executable, "-u", "run.py"],
                            cwd=str(ROOT), stdout=fh, stderr=fh)
    return True


def progress():
    """Stage name and n/total, scraped off the log both scripts already write."""
    if not LOG.exists():
        return {}
    tail = LOG.read_text(errors="replace")[-8000:]
    out = {}
    stages = STAGE.findall(tail)
    if stages:
        out["stage"] = stages[-1].split(":")[0]
        # time each stage from when it first appeared: the fast pass and the
        # browser pass run at very different rates, so one global rate lies
        out["elapsed"] = time.time() - STAGE_T0.setdefault(out["stage"], time.time())
    hits = PROGRESS.findall(tail)
    if hits:
        out["done"], out["total"] = int(hits[-1][0]), int(hits[-1][1])
    return out


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROOT), **kw)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path not in SERVED:
            self.send_error(404)
            return
        if path == "/status":
            self.send_status()
            return
        if path in ("/", "/index.html"):
            self.path = "/dashboard.html"
        super().do_GET()

    def do_POST(self):
        # state-changing, so POST only: a stray GET (prefetch, history) must
        # never kick off a crawl
        if self.path.split("?")[0] != "/run":
            self.send_error(404)
            return
        if PUBLIC:
            # a tunnelled URL gets scanned; starting processes is not something
            # a stranger should be able to do
            self.send_json({"error": "runs are disabled on a public server"}, 403)
            return
        started = start_run()
        self.send_json({"started": started}, 200 if started else 409)

    def send_status(self):
        jobs = ROOT / "jobs.json"
        self.send_json({
            "updated": jobs.stat().st_mtime if jobs.exists() else 0,
            "scraping": scrape_running(),
            "public": PUBLIC,
            # cached pages doubles as a progress signal during a long run
            "pages": sum(1 for _ in (ROOT / "cache").glob("*")) if (ROOT / "cache").exists() else 0,
            **(progress() if scrape_running() else {}),
        })

    def send_json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self):
        # the whole point is seeing fresh data; never let the browser cache it
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):
        pass  # one line per poll every 4s is just noise


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-open", action="store_true")
    ap.add_argument("--public", action="store_true",
                    help="for tunnelling: serve read-only, no /run")
    args = ap.parse_args()

    global PUBLIC
    PUBLIC = args.public

    if not (ROOT / "dashboard.html").exists():
        import build_ui
        build_ui.build()

    url = f"http://127.0.0.1:{args.port}/"
    # bind loopback only: this is a local viewer, not a public site
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"serving {url}   (ctrl-c to stop)")
    if not args.no_open:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
