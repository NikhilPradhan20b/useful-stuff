"""No-AI vacancy scraper for tech-companies-in-nepal.

Pipeline per company:
  1. fetch homepage, find careers page (link scan, then common paths)
  2. extract openings, best method first:
       ats      - known ATS JSON API (exact)
       jsonld   - schema.org JobPosting (exact)
       heuristic- anchors/headings that look like job titles (fuzzy)
Writes jobs.json + report to stdout.
"""
import json, re, sys, time, random, hashlib, pathlib, argparse, threading
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

ROOT = pathlib.Path(__file__).parent
CACHE = ROOT / "cache"
CACHE.mkdir(exist_ok=True)
README_URL = "https://raw.githubusercontent.com/mesaugat/tech-companies-in-nepal/master/README.md"
UA = "Mozilla/5.0 (compatible; vacancy-finder/0.1; +https://github.com/mesaugat/tech-companies-in-nepal)"
TIMEOUT = 15

CAREER_WORDS = re.compile(r"career|job|vacanc|hiring|opening|join[-\s_]?us|work[-\s_]?with[-\s_]?us|we[-\s_]?are[-\s_]?hiring|position", re.I)
COMMON_PATHS = ["/careers", "/career", "/jobs", "/careers/", "/join-us", "/vacancy", "/vacancies", "/opportunities"]

# --- ATS ------------------------------------------------------------------
ATS = [
    ("greenhouse", re.compile(r"(?:boards|job-boards)\.greenhouse\.io/(?:embed/job_board\?for=)?([a-z0-9_-]+)", re.I),
     "https://boards-api.greenhouse.io/v1/boards/{s}/jobs"),
    ("lever", re.compile(r"jobs\.lever\.co/([a-z0-9_-]+)", re.I),
     "https://api.lever.co/v0/postings/{s}?mode=json"),
    # apply.workable.com/<slug> must be tried before <slug>.workable.com, or the
    # host pattern matches "apply" as the slug and the real account is lost.
    ("workable", re.compile(r"apply\.workable\.com/(?:j/)?([a-zA-Z0-9_-]+)", re.I),
     "https://apply.workable.com/api/v1/widget/accounts/{s}?details=true"),
    ("workable", re.compile(r"([a-z0-9_-]+)\.workable\.com", re.I),
     "https://apply.workable.com/api/v1/widget/accounts/{s}?details=true"),
    ("bamboohr", re.compile(r"([a-z0-9_-]+)\.bamboohr\.com", re.I),
     "https://{s}.bamboohr.com/careers/list"),
    ("personio", re.compile(r"([a-z0-9_-]+)\.jobs\.personio\.(?:de|com)", re.I),
     "https://{s}.jobs.personio.de/search.json"),
    ("recruitee", re.compile(r"([a-z0-9_-]+)\.recruitee\.com", re.I),
     "https://{s}.recruitee.com/api/offers/"),
    ("smartrecruiters", re.compile(r"careers\.smartrecruiters\.com/([A-Za-z0-9_-]+)", re.I),
     "https://api.smartrecruiters.com/v1/companies/{s}/postings"),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([a-z0-9_-]+)", re.I),
     "https://api.ashbyhq.com/posting-api/job-board/{s}"),
]

def ats_jobs(html):
    """Return (source, [job,...]) if page embeds a known ATS board."""
    for name, pat, api in ATS:
        m = pat.search(html)
        if not m:
            continue
        slug = m.group(1)
        if slug in ("www", "apply", "jobs", "careers"):
            continue
        r = get(api.format(s=slug), as_json=True)
        if r is None or r == []:
            continue
        jobs = _ats_normalize(name, r)
        if jobs:
            return name, jobs
    return None, []

def _ats_normalize(name, data):
    try:
        return _ats_normalize_inner(name, data)
    except Exception:
        return []

def _dig(node, *keys):
    """Nested .get that tolerates non-dict nodes (ATS payload shapes vary)."""
    for k in keys:
        if not isinstance(node, dict):
            return None
        node = node.get(k)
    return node

def _items(data, key):
    seq = data if key is None else (data.get(key) if isinstance(data, dict) else None)
    return [x for x in seq if isinstance(x, dict)] if isinstance(seq, list) else []

def _ats_normalize_inner(name, data):
    out = []
    if not isinstance(data, (dict, list)):
        return out
    if name == "greenhouse":
        for j in _items(data, "jobs"):
            out.append(dict(title=j.get("title"), url=j.get("absolute_url"),
                            location=(j.get("location") or {}).get("name")))
    elif name == "lever":
        for j in _items(data, None):
            out.append(dict(title=j.get("text"), url=j.get("hostedUrl"),
                            location=(j.get("categories") or {}).get("location")))
    elif name == "workable":
        for j in _items(data, "jobs"):
            out.append(dict(title=j.get("title"), url=j.get("url") or j.get("application_url"),
                            location=", ".join(filter(None, [j.get("city"), j.get("country")]))))
    elif name == "recruitee":
        for j in _items(data, "offers"):
            out.append(dict(title=j.get("title"), url=j.get("careers_url"),
                            location=", ".join(filter(None, [j.get("city"), j.get("country")]))))
    elif name == "smartrecruiters":
        for j in _items(data, "content"):
            loc = j.get("location") or {}
            out.append(dict(title=j.get("name"), url=_dig(j, "ref", "jobAd"),
                            location=", ".join(filter(None, [loc.get("city"), loc.get("country")]))))
    elif name == "bamboohr":
        for j in _items(data, "result"):
            out.append(dict(title=j.get("jobOpeningName"),
                            url=j.get("jobOpeningShareUrl"),
                            location=_dig(j, "location", "city")))
    elif name == "personio":
        for j in (data if isinstance(data, list) else []):
            if not isinstance(j, dict):
                continue
            out.append(dict(title=j.get("name"), url=j.get("url"),
                            location=j.get("office")))
    elif name == "ashby":
        for j in _items(data, "jobs"):
            out.append(dict(title=j.get("title"), url=j.get("jobUrl"), location=j.get("location")))
    return [j for j in out if j.get("title")]

# --- fetch ----------------------------------------------------------------
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
}
RETRY_STATUS = {403, 408, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524, 525}
INSECURE_TLS = False   # opt-in via --insecure-tls; see README note
BUDGET = 75            # seconds of network time per company, then give up

_local = threading.local()

def set_deadline(seconds):
    """Bound total network time for one company. Without this a single dead host
    can burn minutes: every URL variant times out, and each careers-path guess
    adds more on top. This crawl is wide, not deep - better to move on."""
    _local.deadline = time.time() + seconds

def expired():
    d = getattr(_local, "deadline", None)
    return d is not None and time.time() > d

def session():
    """One requests.Session per thread: keeps cookies (fixes redirect loops) and
    reuses connections."""
    s = getattr(_local, "s", None)
    if s is None:
        s = _local.s = requests.Session()
        s.headers.update(HEADERS)
    return s

def url_variants(url):
    """Same site, spellings a misconfigured host may actually answer on.

    Order matters: try exactly what we were given, then toggle www (the usual
    cause of a cert name mismatch), then fall back to http.
    """
    p = urlparse(url)
    host = p.netloc
    alt = host[4:] if host.startswith("www.") else "www." + host
    out = [url, p._replace(netloc=alt).geturl()]
    if p.scheme == "https":
        out += [p._replace(scheme="http").geturl(),
                p._replace(scheme="http", netloc=alt).geturl()]
    seen, uniq = set(), []
    for u in out:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq

def _fetch_once(url, verify=True, timeout=None):
    """Return (response, retryable). retryable=True means worth another attempt."""
    try:
        r = session().get(url, timeout=timeout or TIMEOUT, allow_redirects=True, verify=verify)
    except requests.exceptions.SSLError:
        return None, "tls"
    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
        return None, True
    except Exception:
        return None, False
    if r.status_code == 200:
        return r, False
    return None, r.status_code in RETRY_STATUS

def fetch(url, attempts=2, variants=True, timeout=None):
    """Robust GET. Tries URL variants, retries transient failures with backoff.

    Speculative callers (guessing /careers, /jobs, ...) pass attempts=1,
    variants=False: a guess that does not answer immediately is just a wrong
    guess, and paying the retry ladder on eight of them per company costs
    more than the whole rest of the crawl.
    """
    for variant in (url_variants(url) if variants else [url]):
        for i in range(attempts):
            if expired():
                return None
            r, retryable = _fetch_once(variant, timeout=timeout)
            if r is not None:
                return r
            if retryable == "tls":
                if INSECURE_TLS:
                    r, _ = _fetch_once(variant, verify=False, timeout=timeout)
                    if r is not None:
                        return r
                break          # cert problem will not fix itself on retry
            if not retryable:
                break          # 404 and friends: try the next variant instead
            time.sleep(1.5 * (2 ** i) + random.random())
    return None

META_REFRESH = re.compile(
    r"""<meta[^>]+http-equiv=["']?refresh["']?[^>]+content=["'][^"']*?url=([^"'>\s]+)""",
    re.I)
JS_REDIRECT = re.compile(
    r"""(?:window\.)?location\s*(?:\.href\s*)?(?:=|\.replace\s*\()\s*["']([^"']+)["']""",
    re.I)


# A redirect to a marketplace/parking host means the company's domain lapsed;
# scraping it would attribute a squatter's page to the company.
PARKED = re.compile(
    r"expireddomains|hugedomains|buydomains|afternic|sedoparking|dan\.com|"
    r"domainmarket|undeveloped\.com|parkingcrew|bodis\.com|namecheap\.com/domains|"
    r"godaddy\.com/(?:domainsearch|forsale)", re.I)


def final_url(url):
    """Where this URL actually landed. Relative links must resolve against the
    post-redirect address, or every link on a redirected site points at the
    old host."""
    p = CACHE / (hashlib.sha1(url.encode()).hexdigest() + ".url")
    return p.read_text().strip() if p.exists() else url


def _client_redirect(html, base):
    """Redirects the HTTP layer cannot see: meta-refresh and a bare JS
    location assignment on an otherwise empty landing page."""
    m = META_REFRESH.search(html)
    if m:
        return urljoin(base, m.group(1).strip())
    # only trust a JS redirect when the page has essentially no content of its
    # own, otherwise we would follow analytics and widget code
    if len(html) < 4000:
        m = JS_REDIRECT.search(html)
        if m and not m.group(1).startswith(("#", "javascript:")):
            return urljoin(base, m.group(1).strip())
    return None


def get(url, as_json=False, use_cache=True, _hops=0, **kw):
    h = hashlib.sha1(url.encode()).hexdigest()
    key = CACHE / (h + (".json" if as_json else ".html"))
    miss = CACHE / (h + ".miss")
    if use_cache:
        if key.exists():
            txt = key.read_text(errors="replace")
            return json.loads(txt) if (as_json and txt.strip()) else txt or None
        if miss.exists():
            return None
    r = fetch(url, **kw)
    if r is None:
        miss.touch()
        return None
    if as_json:
        try:
            data = r.json()
        except Exception:
            return None
        key.write_text(json.dumps(data))
        return data

    landed = r.url or url
    hop = _client_redirect(r.text, landed)
    if hop and hop.rstrip("/") != landed.rstrip("/") and _hops < 3:
        sub = get(hop, use_cache=use_cache, _hops=_hops + 1, **kw)
        if sub is not None:
            key.write_text(sub, errors="replace")
            (CACHE / (h + ".url")).write_text(final_url(hop))
            return sub

    key.write_text(r.text, errors="replace")
    if landed.rstrip("/") != url.rstrip("/"):
        (CACHE / (h + ".url")).write_text(landed)
    return r.text

# --- careers page ---------------------------------------------------------
SITEMAP_HINT = re.compile(r"^\s*sitemap:\s*(\S+)", re.I | re.M)
# a sitemap lists every page, so match on the URL alone and keep it tight:
# "career" would also catch /blog/careers-advice
# the word must be a whole path segment: "/careers" is the careers page,
# "/blog/career-advice-2024" is an article about careers
CAREER_SLUG = re.compile(
    r"/(?:careers?|jobs?|vacanc(?:y|ies)|openings?|join[-_]?us|"
    r"work[-_]?with[-_]?us|we[-_]are[-_]hiring|hiring|opportunit(?:y|ies)|"
    r"life[-_]at[-_][^/]+)/?$",
    re.I)


def sitemap_urls(base):
    """Careers-looking URLs from the site's sitemap, closest to the root first.

    Most CMS sites publish one, and it names the careers page even when the
    homepage nav hides it behind a script.
    """
    seen, found, queue = set(), [], []
    robots = get(urljoin(base, "/robots.txt"), attempts=1, variants=False, timeout=8)
    if robots:
        queue += SITEMAP_HINT.findall(robots)[:3]
    queue += [urljoin(base, p) for p in ("/sitemap.xml", "/sitemap_index.xml",
                                         "/wp-sitemap.xml", "/sitemap-index.xml")]

    # one level of nesting: sitemap indexes point at child sitemaps
    for depth in range(2):
        nxt = []
        for sm in queue[:8]:
            if sm in seen or expired():
                continue
            seen.add(sm)
            xml = get(sm, attempts=1, variants=False, timeout=10)
            if not xml or "<" not in xml:
                continue
            locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml, re.I)
            for u in locs:
                if u.endswith(".xml") and depth == 0:
                    nxt.append(u)
                elif CAREER_SLUG.search(urlparse(u).path):
                    found.append(u)
        if found or not nxt:
            break
        queue = nxt
    # a shallow path is the real careers page; a deep one is usually a posting
    return sorted(set(found), key=lambda u: (urlparse(u).path.count("/"), len(u)))


def find_careers(base, html):
    """Best careers URL, or None. Prefer same-host links, then ATS links, then guessed paths."""
    soup = BeautifulSoup(html, "html.parser")
    host = urlparse(base).netloc
    cands = []
    for a in soup.find_all("a", href=True):
        text = " ".join(a.get_text(" ", strip=True).split())
        href = a["href"]
        if CAREER_WORDS.search(text) or CAREER_WORDS.search(href):
            u = urljoin(base, href)
            if not u.startswith("http"):
                continue
            # deprioritize blog posts / social links masquerading as careers
            if re.search(r"facebook|twitter|linkedin|instagram|/blog/", u, re.I):
                continue
            same = urlparse(u).netloc == host
            cands.append((0 if same else 1, len(u), u))
    if cands:
        return sorted(cands)[0][2]
    for p in COMMON_PATHS:
        u = urljoin(base, p)
        h = get(u, attempts=1, variants=False, timeout=10)
        if h and CAREER_WORDS.search(h[:200000]):
            return u
    # nothing in the nav and nothing at a guessable path: ask the sitemap
    for u in sitemap_urls(base)[:3]:
        h = get(u, attempts=1, variants=False, timeout=10)
        if h and CAREER_WORDS.search(h[:200000]):
            return u
    return None

# --- JSON-LD --------------------------------------------------------------
def jsonld_jobs(base, html):
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for tag in soup.find_all("script", type=lambda t: t and "ld+json" in t):
        raw = tag.string or tag.get_text()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        for node in _walk(data):
            if not isinstance(node, dict):
                continue
            t = node.get("@type")
            types = t if isinstance(t, list) else [t]
            if "JobPosting" not in types:
                continue
            loc = node.get("jobLocation")
            if isinstance(loc, list):
                loc = loc[0] if loc else None
            addr = (loc or {}).get("address") if isinstance(loc, dict) else None
            out.append(dict(
                title=node.get("title"),
                url=node.get("url") or base,
                location=(addr or {}).get("addressLocality") if isinstance(addr, dict) else None,
            ))
    return [j for j in out if j.get("title")]

def _walk(node):
    """Yield every dict nested anywhere in a JSON-LD document (handles @graph)."""
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)

# --- second hop -----------------------------------------------------------
# Plenty of careers pages are a pitch with a button ("View job openings") and the
# actual list one click away, often on a separate ATS host.
DEEP_LINK = re.compile(
    r"view\s+(all\s+)?(job|opening|position|vacan)|open\s+position|"
    r"current\s+(opening|vacan)|browse\s+job|see\s+(all\s+)?(job|opening)|"
    r"apply\s+now|join\s+our\s+team", re.I)

def deep_links(base, html):
    """Links on a careers page that promise the actual listing."""
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for a in soup.find_all("a", href=True):
        text = " ".join(a.get_text(" ", strip=True).split())
        if not DEEP_LINK.search(text):
            continue
        u = urljoin(base, a["href"])
        if u.startswith("http") and u.rstrip("/") != base.rstrip("/"):
            out.append(u)
    seen, uniq = set(), []
    for u in out:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq[:3]

# --- heuristic ------------------------------------------------------------
JOB_TITLE = re.compile(
    r"\b(engineer|developer|designer|manager|analyst|intern|internship|architect|"
    r"scientist|administrator|officer|executive|specialist|lead|consultant|"
    r"trainee|associate|assistant|qa|sdet|devops|sre|technician|writer|marketer|recruiter)\b", re.I)
# Marketing and nav copy is where the fuzzy matcher goes wrong: "Developer docs",
# "AI Analyst - answers in seconds". These read like titles but are never jobs.
NOT_JOB = re.compile(
    r"privacy|terms|cookie|contact|about|blog|news|home|login|sign\s?in|"
    r"apply now|apply for|read more|learn more|view all|see all|"
    r"\bdocs?\b|documentation|pricing|demo|guide|tutorial|webinar|case stud|"
    r"resources|platform|solutions?\b|products?\b|features?\b|customers?\b|"
    r"get started|book a|talk to|contact us|our team|meet the|why us|"
    r"testimonial|partner|award|press|events?\b|newsletter|subscribe|"
    r"copyright|all rights", re.I)

# A job title is a role, not a sentence. Marketing copy that happens to contain
# "engineer" is nearly always longer and carries verbs/punctuation.
SENTENCEY = re.compile(r"[.!?;:]|\b(we|our|your|you|us|get|learn|discover|explore|find)\b", re.I)

# Listings pad the title with a headcount: "1 QA Engineer", "PHP Developer (2)".
COUNT = re.compile(r"^\d+(?:\s*-\s*\d+)?\s+|\s*\(\d+\)$")

def heuristic_jobs(base, html):
    """Anchors and headings on a careers page whose text reads like a job title."""
    soup = BeautifulSoup(html, "html.parser")
    # Site chrome repeats on every page and never holds the listing.
    for chrome in soup.find_all(["nav", "header", "footer", "script", "style"]):
        chrome.decompose()
    seen, hits = set(), []
    # Any element, not a tag whitelist: real listings put titles in <p>, <span>,
    # <div>, <h6> and <button> as often as in <a>/<h2>. The text filters below
    # are what separate a title from copy, so the tag was only ever losing rows.
    for el in soup.find_all(True):
        text = " ".join(el.get_text(" ", strip=True).split())
        if not (3 < len(text) <= 90):
            continue
        if not JOB_TITLE.search(text) or NOT_JOB.search(text):
            continue
        if SENTENCEY.search(text):
            continue
        if len(text.split()) > 8:
            continue
        hits.append((el, text))

    # A row element reads "Aug 7, 2026 Web Designer" around the real title in a
    # child. Whenever an element and its own descendant both look like titles,
    # the descendant is the title. Structural, not lexical: pruning on substring
    # would throw away "Senior Software Developer" for containing "Software
    # Developer", which are two different jobs.
    inner = {id(el) for el, _ in hits}
    for el, _ in hits:
        if any(id(c) in inner for c in el.find_all(True)):
            inner.discard(id(el))

    out = []
    for el, text in hits:
        text = COUNT.sub("", text).strip()
        key = text.lower()
        if id(el) not in inner or key in seen:
            continue
        seen.add(key)
        href = el.get("href") if el.name == "a" else None
        out.append(dict(title=text, url=urljoin(base, href) if href else base, location=None))
    return out

# --- per company ----------------------------------------------------------
def scrape(company):
    try:
        return _scrape(company)
    except Exception as e:
        return dict(company=company["name"], site=company["url"], careers_url=None,
                    method=None, jobs=[], error=f"crash: {type(e).__name__}: {e}")

def _scrape(company):
    name, url = company["name"], company["url"]
    set_deadline(BUDGET)
    res = dict(company=name, site=url, careers_url=None, method=None, jobs=[], error=None)
    home = get(url)
    if not home:
        res["error"] = "site unreachable"
        return res

    # A redirect makes the landing URL the correct base for every relative link,
    # and often moves the site to a different domain entirely.
    landed = final_url(url)
    if landed.rstrip("/") != url.rstrip("/"):
        res["redirected_to"] = landed
        if PARKED.search(landed):
            res["error"] = "domain expired (redirects to a parking page)"
            return res
    url = landed

    src, jobs = ats_jobs(home)
    if jobs:
        res.update(method=f"ats:{src}", jobs=jobs, careers_url=url)
        return res

    careers = find_careers(url, home)
    if not careers:
        res["error"] = "no careers page found"
        return res
    res["careers_url"] = careers

    page = get(careers)
    if not page:
        res["error"] = "careers page unreachable"
        return res
    careers = final_url(careers)
    res["careers_url"] = careers

    src, jobs = ats_jobs(page)
    if jobs:
        res.update(method=f"ats:{src}", jobs=jobs)
        return res

    jobs = jsonld_jobs(careers, page)
    if jobs:
        res.update(method="jsonld", jobs=jobs)
        return res

    for hop in deep_links(careers, page):
        sub = get(hop, attempts=1)
        if not sub:
            continue
        src, jobs = ats_jobs(sub)
        if jobs:
            res.update(method=f"ats:{src}", jobs=jobs, careers_url=hop)
            return res
        jobs = jsonld_jobs(hop, sub)
        if jobs:
            res.update(method="jsonld", jobs=jobs, careers_url=hop)
            return res
        jobs = heuristic_jobs(hop, sub)
        if jobs:
            res.update(method="heuristic", jobs=jobs, careers_url=hop)
            return res

    jobs = heuristic_jobs(careers, page)
    if jobs:
        res.update(method="heuristic", jobs=jobs)
        return res

    res["error"] = "no openings detected"
    return res

# --- companies ------------------------------------------------------------
# Real company rows always carry the "| location | description" tail; this keeps
# prose links out of the list (the contributing section links "Fork" and friends).
ENTRY = re.compile(r"^- \[(?P<name>[^\]]+)\]\((?P<url>https?://[^)]+)\)\s*\|")

def load_companies(md):
    out, in_fence = [], False
    for line in md.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence          # the format example lives in a fence
            continue
        if in_fence:
            continue
        m = ENTRY.match(line.strip())
        if m:
            out.append(dict(name=m["name"].strip(), url=m["url"].strip()))
    return out

# --- main -----------------------------------------------------------------
def main():
    global INSECURE_TLS, BUDGET
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="only first N companies")
    ap.add_argument("--workers", type=int, default=6,
                    help="keep low; high concurrency triggers rate-limit 403/503")
    ap.add_argument("--budget", type=int, default=BUDGET,
                    help="max seconds of network time per company")
    ap.add_argument("--insecure-tls", action="store_true",
                    help="also accept invalid/expired certs (unverified data; opt-in)")
    ap.add_argument("--out", default="jobs.json")
    args = ap.parse_args()

    INSECURE_TLS = args.insecure_tls
    BUDGET = args.budget

    md = get(README_URL)
    companies = load_companies(md)
    if args.limit:
        companies = companies[:args.limit]
    print(f"{len(companies)} companies", file=sys.stderr)

    # Write as results land: a long crawl that gets killed still leaves usable
    # data, and the dashboard fills in live instead of staying blank for an hour.
    out = ROOT / args.out
    results = []

    def flush():
        tmp = out.with_suffix(".tmp")
        tmp.write_text(json.dumps(results + pending_stubs(), indent=2))
        tmp.replace(out)          # atomic: the server never reads a half file

    done = set()

    def pending_stubs():
        return [dict(company=c["name"], site=c["url"], careers_url=None,
                     method=None, jobs=[], error="pending")
                for c in companies if c["name"] not in done]

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for i, r in enumerate(ex.map(scrape, companies), 1):
            results.append(r)
            done.add(r["company"])
            if i % 5 == 0 or i == len(companies):
                flush()
            if i % 25 == 0:
                print(f"  {i}/{len(companies)}", file=sys.stderr)
    flush()

    tally, total = {}, 0
    for r in results:
        k = r["method"] or f"FAIL: {r['error']}"
        tally[k] = tally.get(k, 0) + 1
        total += len(r["jobs"])
    print(f"\n{total} openings across {sum(1 for r in results if r['jobs'])} companies\n")
    for k, v in sorted(tally.items(), key=lambda x: -x[1]):
        print(f"{v:4d}  {k}")

def demo():
    assert load_companies(
        "```\n- [Company Name](https://my.company) | location, city | what\n```\n"
        "- [Foo](https://foo.com) | KTM | bar\n"
        "- [Fork](https://guides.github.com/activities/forking) prose link\n"
        "not an entry") == [{"name": "Foo", "url": "https://foo.com"}]
    ld = '<script type="application/ld+json">{"@graph":[{"@type":"JobPosting","title":"Backend Engineer","url":"https://x/1","jobLocation":{"address":{"addressLocality":"Kathmandu"}}}]}</script>'
    assert jsonld_jobs("https://x", ld) == [
        {"title": "Backend Engineer", "url": "https://x/1", "location": "Kathmandu"}]
    h = '<a href="/c">Careers</a><a href="https://facebook.com/jobs">jobs</a>'
    assert find_careers("https://x.com", h) == "https://x.com/c"
    hj = heuristic_jobs("https://x",
        '<nav><a href="/n">Lead Engineer</a></nav>'          # chrome: dropped
        '<a href="/j/1">Senior QA Engineer</a>'              # real title: kept
        '<a href="/d">Developer docs</a>'                    # product copy
        '<a href="/m">AI Analyst Get expert answers in seconds</a>'
        '<a href="/p">Privacy Policy</a>')
    assert [j["title"] for j in hj] == ["Senior QA Engineer"], hj
    assert _ats_normalize("lever", [{"text": "SRE", "hostedUrl": "u", "categories": {"location": "KTM"}}]) == [
        {"title": "SRE", "url": "u", "location": "KTM"}]
    v = url_variants("https://www.x.com/a")
    assert v == ["https://www.x.com/a", "https://x.com/a", "http://www.x.com/a", "http://x.com/a"], v
    assert url_variants("http://x.com")[:2] == ["http://x.com", "http://www.x.com"]
    assert _client_redirect(
        '<meta http-equiv="refresh" content="0; url=/new/home">', "https://x.com/a") \
        == "https://x.com/new/home"
    assert _client_redirect('<script>window.location.href="https://y.com/"</script>',
                            "https://x.com") == "https://y.com/"
    assert _client_redirect("<p>hi</p>", "https://x.com") is None
    assert PARKED.search("https://expireddomains.com/domain/web-robo.com")
    assert not PARKED.search("https://www.yarsalabs.com/")
    dl = deep_links("https://x.com/careers",
        '<a href="/jobs">View job openings</a><a href="/about">About</a>')
    assert dl == ["https://x.com/jobs"], dl
    set_deadline(-1)
    assert expired() and fetch("http://example.invalid") is None
    _local.deadline = None
    # the sitemap slug matcher is the whole point of that pass: it must accept
    # real careers paths and reject the prose pages that merely mention them
    for good in ("/careers", "/career/", "/jobs", "/en/vacancies",
                 "/join-us", "/work-with-us", "/opportunities", "/life-at-acme"):
        assert CAREER_SLUG.search(good), good
    for bad in ("/blog/career-advice-2024", "/about", "/services/staffing",
                "/news/hiring-trends", "/"):
        assert not CAREER_SLUG.search(bad), bad
    assert SITEMAP_HINT.findall(
        "User-agent: *\nDisallow: /wp-admin\nSitemap: https://x.com/sitemap.xml\n"
    ) == ["https://x.com/sitemap.xml"]

    print("self-check ok")

if __name__ == "__main__":
    if "--demo" in sys.argv:
        demo()
    else:
        main()
