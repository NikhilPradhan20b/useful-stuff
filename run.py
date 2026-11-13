"""Full pipeline: fast pass, browser rescue, dashboard rebuild.

scrape.py rewrites jobs.json from scratch, so running it alone throws away
whatever rescue.py recovered. Running the stages in order is what you almost
always want, so this does it as one command.

    python3 run.py              # everything
    python3 run.py --skip-scrape   # browser pass + rebuild only
    python3 run.py --no-rescue     # fast pass only (no browser)
"""
import argparse
import subprocess
import sys
import time

import build_ui


def stage(name, argv):
    print(f"\n=== {name} ===", flush=True)
    t = time.time()
    r = subprocess.run([sys.executable] + argv)
    print(f"=== {name}: {time.time() - t:.0f}s (exit {r.returncode}) ===", flush=True)
    return r.returncode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--concurrency", type=int, default=5, help="browser tabs")
    ap.add_argument("--skip-scrape", action="store_true")
    ap.add_argument("--no-rescue", action="store_true")
    ap.add_argument("--no-llm", action="store_true")
    args = ap.parse_args()

    if not args.skip_scrape:
        if stage("scrape (fast pass)", ["scrape.py", "--workers", str(args.workers)]):
            print("scrape failed; leaving jobs.json alone", file=sys.stderr)
            return 1
    if not args.no_rescue:
        stage("rescue (browser pass)", ["rescue.py", "--concurrency", str(args.concurrency)])

    # only the pages the regex could merely guess at, and only if a key exists
    if not args.no_llm:
        import llm
        if llm.api_key():
            stage("llm (re-read inferred pages)", ["llm.py"])
        else:
            print("\nskipping llm pass: no OPENROUTER_API_KEY", flush=True)

    print("\n=== dashboard ===")
    build_ui.build()
    print("\nserve it with:  python3 serve.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
