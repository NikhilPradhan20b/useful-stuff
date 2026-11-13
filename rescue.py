"""Second pass with a real browser, for everything the plain fetch could not do.

Three things a headless browser buys us:

  1. JS-rendered pages       - the HTML shell has no links; the DOM does.
  2. Non-link navigation     - "Careers" as a button/onclick, not an <a href>.
  3. Intercepted API calls   - a JS job board fetches its listings as JSON.
     Capturing that response beats reverse-engineering each platform, and it
     works for boards we have never seen before (Keka, mercury360, ...).

Run after scrape.py; it edits jobs.json in place, so the dashboard picks the
rescued companies up on its own.

    python3 rescue.py [--limit N] [--concurrency 4]
"""
import argparse
import asyncio
import hashlib
import json
import pathlib
import re
import sys

from bs4 import BeautifulSoup

from scrape import (CACHE, CAREER_WORDS, COMMON_PATHS, DEEP_LINK, NOT_JOB, ROOT,
                    ats_jobs, heuristic_jobs, jsonld_jobs)

NAV_TIMEOUT = 25_000
SETTLE_MS = 2_500

# Keys a job object is likely to carry, whatever the platform calls them.
TITLE_KEYS = ("title", "jobtitle", "job_title", "jobopeningname", "name",
              "position", "positiontitle", "designation", "text", "roletitle")
LOC_KEYS = ("location", "city", "joblocation", "locationname", "office",
            "cityname", "location_name", "workplace")
URL_KEYS = ("url", "absolute_url", "hostedurl", "applyurl", "joburl",
            "jobopeningshareurl", "careers_url", "link", "permalink", "slug")
# Guards against harvesting blog posts, products, or nav items out of some
# unrelated JSON the page happens to load.
JOBBY_HINT = re.compile(r"job|career|vacan|opening|position|posting|recruit", re.I)
# Generic CMS content APIs serve pages/posts that score exactly like postings:
# a title plus a description. Never harvest these unless the path also says jobs.
CMS_ENDPOINT = re.compile(
    r"/wp-json|/wp/v2|/posts?\b|/pages?\b|/media\b|/categor|/menus?\b|/tags?\b|"
    r"/blog|/news\b|/products?\b|/settings|/comments?\b|/users?\b|/search\b", re.I)


def _get(d, keys):
    low = {k.lower(): v for k, v in d.items() if isinstance(k, str)}
    for k in keys:
        v = low.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, dict):
            for kk in ("name", "label", "title", "city"):
                if isinstance(v.get(kk), str) and v[kk].strip():
                    return v[kk].strip()
    return None


STRONG_TITLE = {"title", "jobtitle", "job_title", "jobopeningname",
                "position", "positiontitle", "designation", "roletitle"}
WEAK_TITLE = {"name", "text"}
RICH = {"description", "jobdescription", "summary", "department", "departmentlabel",
        "employmenttype", "employmentstatus", "experience", "applyurl", "joburl",
        "postedon", "createdon", "publishedat", "workplacetype", "jobcode", "salary"}


def _score(objs):
    """How much does this array look like job postings, rather than the
    departments/categories/nav lists a board also fetches?"""
    keys = set()
    for o in objs[:12]:
        keys |= {k.lower() for k in o if isinstance(k, str)}
    score = 3 if keys & STRONG_TITLE else (1 if keys & WEAK_TITLE else 0)
    if keys & RICH:
        score += 2
    if keys & {k.lower() for k in LOC_KEYS}:
        score += 2
    return score


def _candidates(node, source_url="", depth=0, out=None):
    """Every array of dicts in the payload, with a job-likeness score."""
    if out is None:
        out = []
    if depth > 8:
        return out
    if isinstance(node, list):
        objs = [x for x in node if isinstance(x, dict) and _get(x, TITLE_KEYS)]
        if objs:
            out.append((_score(objs), len(json.dumps(objs)), objs))
        for x in node[:20]:
            _candidates(x, source_url, depth + 1, out)
    elif isinstance(node, dict):
        for k, v in node.items():
            if isinstance(v, list):
                objs = [x for x in v if isinstance(x, dict) and _get(x, TITLE_KEYS)]
                if objs:
                    bonus = 2 if JOBBY_HINT.search(k) else 0
                    out.append((_score(objs) + bonus, len(json.dumps(objs)), objs))
            _candidates(v, source_url, depth + 1, out)
    return out


def jobs_from_json(node, source_url="", depth=0):
    """Best job-shaped array in an arbitrary JSON payload.

    Scored rather than first-match: a board fetches several arrays (departments,
    locations, social links) and the postings are rarely the first one back.
    """
    if CMS_ENDPOINT.search(source_url) and not JOBBY_HINT.search(source_url):
        return []
    cands = _candidates(node, source_url)
    if not cands:
        return []
    # A titled array alone is not enough - blog posts and products look the same.
    # Only trust a bare title list when the endpoint itself is about jobs;
    # otherwise demand corroborating fields (location, description, salary...).
    floor = 3 if JOBBY_HINT.search(source_url) else 5
    best = max(cands, key=lambda c: (c[0], c[1]))
    if best[0] < floor:
        return []
    return [dict(title=_get(x, TITLE_KEYS), url=_get(x, URL_KEYS),
                 location=_get(x, LOC_KEYS)) for x in best[2]]


BULLET = re.compile(r"^[\s\u2022\u25cf\u00b7*\-\u2013\u2014>\|]+")

def clean(jobs, base=""):
    out, seen = [], set()
    for j in jobs:
        t = BULLET.sub("", (j.get("title") or "")).strip()
        t = re.sub(r"\s+", " ", t)
        key = t.lower()
        if not (2 < len(t) <= 90) or key in seen:
            continue
        if NOT_JOB.search(t):        # "Privacy Policy", "Terms of Use", ...
            continue
        seen.add(key)
        u = j.get("url") or ""
        if u and not u.startswith("http") and base:
            from urllib.parse import urljoin
            u = urljoin(base, u)
        out.append(dict(title=t, url=u or base or None, location=j.get("location")))
    return out


async def careers_in_dom(page, base):
    """Careers URL from the rendered DOM: real links first, then clickable
    elements that merely look like links (button, div with onclick)."""
    try:
        hrefs = await page.eval_on_selector_all(
            "a[href]", "els => els.map(e => [e.getAttribute('href'), e.innerText])")
    except Exception:
        hrefs = []
    from urllib.parse import urljoin, urlparse
    host = urlparse(base).netloc
    best = []
    for href, text in hrefs:
        if not href:
            continue
        blob = f"{href} {text or ''}"
        if not CAREER_WORDS.search(blob):
            continue
        u = urljoin(base, href)
        if not u.startswith("http") or re.search(r"facebook|twitter|linkedin|instagram|/blog/", u, re.I):
            continue
        best.append((0 if urlparse(u).netloc == host else 1, len(u), u))
    if best:
        return sorted(best)[0][2]
    return None


async def click_careers(page, base):
    """Some sites navigate with JS. Click the careers control and see where we land."""
    for sel in ("button", "[role=button]", "[onclick]", "li", "span", "div"):
        try:
            els = await page.query_selector_all(sel)
        except Exception:
            continue
        for el in els[:120]:
            try:
                text = (await el.inner_text() or "").strip()
            except Exception:
                continue
            if not text or len(text) > 30 or not CAREER_WORDS.search(text):
                continue
            try:
                before = page.url
                await el.click(timeout=3000)
                await page.wait_for_timeout(SETTLE_MS)
                if page.url != before:
                    return page.url
            except Exception:
                continue
    return None


class Harvest:
    """Collects JSON responses the page fetches while it renders."""

    def __init__(self):
        self.payloads = []

    def attach(self, page):
        page.on("response", self._on)

    async def _on(self, resp):
        try:
            ct = (resp.headers or {}).get("content-type", "")
            if "json" not in ct.lower() or resp.status >= 400:
                return
            if len(self.payloads) > 40:
                return
            body = await resp.json()
            self.payloads.append((resp.url, body))
        except Exception:
            pass

    def jobs(self):
        """Best single payload wins; merging them mixes postings with filters."""
        best = []
        for url, body in self.payloads:
            j = jobs_from_json(body, url)
            if len(j) > len(best):
                best = j
        return best


async def visit(page, url):
    await page.goto(url, timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
    try:
        await page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass
    await page.wait_for_timeout(SETTLE_MS)
    html = await page.content()
    # keep the rendered DOM: it is the only copy of these pages that has any
    # content in it, and llm.py re-reads from the cache rather than re-crawling
    stash(page.url, html)
    return html


def stash(url, html):
    """Save a rendered page beside the raw one, under its own suffix."""
    try:
        key = hashlib.sha1(url.encode()).hexdigest()
        (CACHE / f"{key}.rendered.html").write_text(html)
    except OSError:
        pass


async def rescue_one(browser, rec, sem):
    """Re-attempt one company with a browser. Returns True if it now has jobs."""
    async with sem:
        ctx = await browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/122.0 Safari/537.36",
            viewport={"width": 1366, "height": 900}, ignore_https_errors=True)
        page = await ctx.new_page()
        harvest = Harvest()
        harvest.attach(page)
        try:
            start = rec.get("careers_url") or rec["site"]
            try:
                html = await visit(page, start)
            except Exception:
                if start == rec["site"]:
                    raise
                html = await visit(page, rec["site"])

            # if we started at the homepage, get to a careers page first
            if not rec.get("careers_url") or page.url.rstrip("/") == rec["site"].rstrip("/"):
                target = await careers_in_dom(page, page.url)
                if not target:
                    target = await click_careers(page, page.url)
                if not target:
                    from urllib.parse import urljoin
                    for p in COMMON_PATHS[:4]:
                        try:
                            await visit(page, urljoin(rec["site"], p))
                            if CAREER_WORDS.search(await page.content()):
                                target = page.url
                                break
                        except Exception:
                            continue
                if target and target != page.url:
                    html = await visit(page, target)
                rec["careers_url"] = page.url

            def extract(h, base):
                src, j = ats_jobs(h)
                if j:
                    return f"ats:{src}", j
                j = jsonld_jobs(base, h)
                if j:
                    return "jsonld", j
                j = harvest.jobs()
                if j:
                    return "xhr", clean(j, base)
                j = heuristic_jobs(base, h)
                if j:
                    return "rendered", j
                return None, []

            method, jobs = extract(html, page.url)

            # still nothing: follow a "view openings" control
            if not jobs:
                for hop in _deep(html, page.url):
                    try:
                        sub = await visit(page, hop)
                    except Exception:
                        continue
                    method, jobs = extract(sub, page.url)
                    if jobs:
                        rec["careers_url"] = page.url
                        break

            if jobs:
                rec["method"] = method
                rec["jobs"] = clean(jobs, page.url)
                rec["error"] = None
                return bool(rec["jobs"])
            rec["error"] = rec.get("error") or "no openings detected"
            return False
        except Exception as e:
            rec["error"] = rec.get("error") or f"browser: {type(e).__name__}"
            return False
        finally:
            await ctx.close()


def _deep(html, base):
    from urllib.parse import urljoin
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for a in soup.find_all("a", href=True):
        t = " ".join(a.get_text(" ", strip=True).split())
        if DEEP_LINK.search(t):
            u = urljoin(base, a["href"])
            if u.startswith("http") and u.rstrip("/") != base.rstrip("/"):
                out.append(u)
    return out[:2]


async def main_async(args):
    from playwright.async_api import async_playwright

    path = ROOT / "jobs.json"
    data = json.loads(path.read_text())
    todo = [r for r in data if not r["jobs"] and r.get("error") != "pending"]
    if args.limit:
        todo = todo[:args.limit]
    print(f"{len(todo)} companies to rescue", file=sys.stderr)

    lock = asyncio.Lock()
    done = [0]

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        sem = asyncio.Semaphore(args.concurrency)

        async def run(rec):
            ok = await rescue_one(browser, rec, sem)
            async with lock:
                done[0] += 1
                if ok:
                    print(f"  + {rec['company'][:38]:<40} "
                          f"{rec['method']:<10} {len(rec['jobs'])} jobs", file=sys.stderr)
                if done[0] % 10 == 0:
                    path.write_text(json.dumps(data, indent=2))
                    print(f"  {done[0]}/{len(todo)}", file=sys.stderr)

        await asyncio.gather(*(run(r) for r in todo))
        await browser.close()

    path.write_text(json.dumps(data, indent=2))
    rescued = sum(1 for r in todo if r["jobs"])
    print(f"\nrescued {rescued} companies, "
          f"{sum(len(r['jobs']) for r in todo)} new openings")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--concurrency", type=int, default=4)
    args = ap.parse_args()
    asyncio.run(main_async(args))


def demo():
    payload = {"data": {"jobs": [
        {"title": "Golang Developer", "location": {"name": "Kathmandu"}, "url": "/j/1"},
        {"title": "QA Intern", "city": "Lalitpur"}]}}
    assert [j["title"] for j in clean([{"title": "\u2022 Sales Executive"},
                                      {"title": "Sales  Executive"}])] == ["Sales Executive"]
    got = clean(jobs_from_json(payload, "https://x/api/jobs"), "https://x/careers")
    assert [j["title"] for j in got] == ["Golang Developer", "QA Intern"], got
    assert got[0]["location"] == "Kathmandu" and got[0]["url"] == "https://x/j/1"
    # unrelated JSON must not be harvested
    blog = {"posts": [{"title": "Why we love Go"}, {"title": "Company retreat"}]}
    assert jobs_from_json(blog, "https://x/api/posts") == []
    # a real board also returns departments; postings must win
    keka = [{"id": 1, "identifier": "a", "name": "Admin", "source": 0},
            {"id": 2, "identifier": "b", "name": "Engineering", "source": 0}]
    assert jobs_from_json(keka, "https://x/careers/api/embedjobs/departments/1") == []
    active = [{"id": 9, "title": "Bookkeeper", "description": "<p>about</p>",
               "jobLocation": {"city": "Remote"}, "departmentName": "Admin"}]
    got2 = jobs_from_json(active, "https://x/careers/api/embedjobs/default/active/1")
    assert [j["title"] for j in got2] == ["Bookkeeper"], got2
    cms = [{"title": "Privacy Policy", "description": "<p>x</p>"},
           {"title": "Digitalize Your School?", "description": "<p>y</p>"}]
    assert jobs_from_json(cms, "https://wms.x.com/wms/api/v2/x.com/post?sortBy=rank") == []
    print("self-check ok")


if __name__ == "__main__":
    if "--demo" in sys.argv:
        demo()
    else:
        main()
