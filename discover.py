"""Second attempt at the companies whose careers page we never found.

find_careers() reads the homepage nav and guesses a few paths. That misses a
site whose nav is built by a script, whose careers link is worded oddly, or
whose careers page simply lives somewhere unguessable. Three cheap angles fix
most of it, in cost order:

  1. sitemap.xml / robots.txt   - free, no model, works on any CMS
  2. LLM link pick             - hand the homepage's links to the model
  3. browser render + click    - last resort, for nav that is a script

Whatever it finds is then parsed by the normal extractors, so a page found
here still gets a proper provenance tier rather than being trusted blindly.

    python3 discover.py                 # every "no careers page found"
    python3 discover.py --limit 5 -v    # try a few, verbosely
    python3 discover.py --no-llm        # sitemap only, spends nothing
"""
import argparse
import asyncio
import json
import sys

import llm
import scrape

TARGET = "no careers page found"


def sitemap_hit(rec, verbose=False):
    """Angle 1: a careers URL from the sitemap, confirmed by fetching it."""
    base = scrape.final_url(rec["site"])
    for u in scrape.sitemap_urls(base)[:4]:
        html = scrape.get(u, attempts=1, variants=False, timeout=12)
        if html and scrape.CAREER_WORDS.search(html[:200000]):
            if verbose:
                print(f"      sitemap: {u}", file=sys.stderr)
            return u, html
    return None, None


def llm_hit(key, rec, verbose=False):
    """Angle 2: the model picks the careers link out of the homepage's links."""
    base = scrape.final_url(rec["site"])
    # the homepage must be cached for there to be links to offer
    if not llm._cached_html(base) and not scrape.get(base):
        return None, None
    u = llm.pick_careers(key, rec["company"], base)
    if not u:
        return None, None
    if verbose:
        print(f"      llm pick: {u}", file=sys.stderr)
    html = scrape.get(u, attempts=1, variants=False, timeout=12)
    return (u, html) if html else (None, None)


def parse_page(rec, url, html):
    """Run the normal extractors, so provenance stays honest."""
    for src, pat, api in scrape.ATS:
        m = pat.search(html)
        if m and m.group(1) not in ("apply", "www", "jobs"):
            jobs = scrape.ats_jobs(src, api.format(s=m.group(1)))
            if jobs:
                return f"ats:{src}", jobs
    jobs = scrape.jsonld_jobs(url, html)
    if jobs:
        return "jsonld", jobs
    jobs = scrape.heuristic_jobs(url, html)
    if jobs:
        return "heuristic", jobs
    return None, []


async def browser_hit(recs, verbose=False):
    """Angle 3: render the homepage and let rescue.py click its way in."""
    import rescue
    from playwright.async_api import async_playwright

    found = 0
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        for rec in recs:
            ctx = await browser.new_context(ignore_https_errors=True)
            page = await ctx.new_page()
            try:
                await rescue.visit(page, scrape.final_url(rec["site"]))
                # a real link in the rendered DOM first; only then start clicking
                target = await rescue.careers_in_dom(page, page.url)
                if target:
                    sub = await rescue.visit(page, target)
                else:
                    target = await rescue.click_careers(page, page.url)
                    sub = await page.content() if target else None
                if not target:
                    continue
                rec["careers_url"] = target
                method, jobs = parse_page(rec, target, sub)
                if jobs:
                    rec.update(method=method, jobs=rescue.clean(jobs, target),
                               error=None)
                    found += 1
                    if verbose:
                        print(f"      browser: {target} ({len(jobs)})", file=sys.stderr)
                else:
                    rec["error"] = "no openings detected"
            except Exception as e:
                if verbose:
                    print(f"      browser failed: {type(e).__name__}", file=sys.stderr)
            finally:
                await ctx.close()
        await browser.close()
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    path = scrape.ROOT / "jobs.json"
    data = json.loads(path.read_text())
    todo = [r for r in data if r.get("error") == TARGET][:args.limit]
    print(f"{len(todo)} companies with no careers page", file=sys.stderr)

    key = None if args.no_llm else llm.api_key()
    if not key and not args.no_llm:
        print("no OPENROUTER_API_KEY: sitemap only", file=sys.stderr)

    pages, jobs_found, still = 0, 0, []
    for i, rec in enumerate(todo, 1):
        scrape.set_deadline(scrape.BUDGET)
        if args.verbose:
            print(f"  [{i}/{len(todo)}] {rec['company'][:34]}", file=sys.stderr)

        url, html = sitemap_hit(rec, args.verbose)
        if not url and key:
            try:
                url, html = llm_hit(key, rec, args.verbose)
            except Exception as e:
                print(f"      llm error: {type(e).__name__}: {e}", file=sys.stderr)

        if not url:
            still.append(rec)
            continue

        pages += 1
        rec["careers_url"] = url
        method, jobs = parse_page(rec, url, html)
        if jobs:
            rec.update(method=method, jobs=jobs, error=None)
            jobs_found += len(jobs)
            print(f"  + {rec['company'][:32]:<34} {len(jobs)} via {method}",
                  file=sys.stderr)
        else:
            # found the page but could not read it: llm.py handles it next
            rec["error"] = "no openings detected"
            print(f"  ? {rec['company'][:32]:<34} page found, no openings parsed",
                  file=sys.stderr)

    if still and not args.no_browser:
        print(f"\n{len(still)} left: trying a browser", file=sys.stderr)
        try:
            asyncio.run(browser_hit(still, args.verbose))
        except Exception as e:
            print(f"browser pass failed: {type(e).__name__}: {e}", file=sys.stderr)

    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    tmp.replace(path)
    remaining = sum(1 for r in data if r.get("error") == TARGET)
    print(f"\n{pages} careers pages found, {jobs_found} openings, "
          f"{remaining} still undiscovered", file=sys.stderr)


def demo():
    # a sitemap entry only counts as the careers page if it is one
    assert scrape.CAREER_SLUG.search("/careers")
    assert not scrape.CAREER_SLUG.search("/blog/how-to-start-a-career")
    rec = {"company": "x", "site": "https://x.test/", "error": TARGET}
    data = [rec, {"company": "y", "error": "site unreachable"}]
    assert [r for r in data if r.get("error") == TARGET] == [rec]
    print("ok")


if __name__ == "__main__":
    if "--demo" in sys.argv:
        demo()
    else:
        main()
