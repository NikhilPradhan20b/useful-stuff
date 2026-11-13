"""Re-read the pages we could only guess at, with an LLM via OpenRouter.

The regex heuristic reads markup, not meaning: it harvests department names as
jobs, misses titles that aren't in a link, and cannot tell "we're not hiring"
from "here are our openings". This pass hands the careers page's *text* to a
model and asks for the openings, which fixes all three.

Only pages already fetched are used, so this costs nothing in crawl time and
never re-visits a site. Runs after scrape.py and rescue.py:

    export OPENROUTER_API_KEY=sk-or-...
    python3 llm.py                     # every inferred / empty page
    python3 llm.py --only heuristic    # just the regex guesses
    python3 llm.py --limit 5 --dry-run # cost check before committing

Responses are cached on disk by (model, prompt version, page text), so a rerun
is free and a prompt change correctly misses the old answers.
"""
import argparse
import concurrent.futures as cf
import hashlib
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request

from bs4 import BeautifulSoup

from scrape import CACHE, ROOT

API = "https://openrouter.ai/api/v1/chat/completions"
MODEL = os.environ.get("OPENROUTER_MODEL", "nvidia/nemotron-3-ultra-550b-a55b:free")
LLM_CACHE = CACHE / "llm"
MAX_CHARS = 14000          # careers pages are small; this is a generous ceiling

# Which records are worth spending a token on. Verified feeds are already
# facts, so they are never re-read.
RETRY_ERRORS = ("no openings detected",)

# Bump whenever PROMPT or the schema changes: it is part of the cache key, so
# old answers in a now-obsolete shape are never served.
PROMPT_VERSION = 2

WORKPLACE = {"remote", "hybrid", "onsite"}
EMPLOYMENT = {"full-time", "part-time", "contract", "internship", "freelance",
              "temporary", "volunteer"}
# what the page actually writes vs. the value we filter on
EMPLOYMENT_ALIASES = {
    "fulltime": "full-time", "full time": "full-time", "permanent": "full-time",
    "parttime": "part-time", "part time": "part-time",
    "intern": "internship", "traineeship": "internship", "trainee": "internship",
    "contractual": "contract", "consultant": "contract", "freelancing": "freelance",
    "temp": "temporary", "work from home": "full-time",
}
WORKPLACE_ALIASES = {
    "work from home": "remote", "wfh": "remote", "fully remote": "remote",
    "remote-first": "remote", "on-site": "onsite", "on site": "onsite",
    "in-office": "onsite", "office": "onsite", "flexible": "hybrid",
}

PROMPT = """You extract job openings from the text of a company careers page.

Return ONLY a JSON array, no prose, no markdown fence. Each element:
{
  "title": "<exact job title as written>",
  "location": "<city and/or country as written, else null>",
  "workplace": "remote" | "hybrid" | "onsite" | null,
  "employment_type": "full-time" | "part-time" | "contract" | "internship" |
                     "freelance" | "temporary" | null,
  "experience": "<experience requirement as written, e.g. '2+ years', else null>",
  "experience_years": <minimum years as a number, else null>,
  "department": "<team or function, e.g. 'Engineering', else null>",
  "deadline": "<application deadline as written, else null>",
  "salary": "<pay as written, else null>"
}

Rules:
- Only currently open positions this company is hiring for.
- NOT departments, teams, categories, filters, or benefits ("Engineering",
  "Life at X", "Why join us", "All departments").
- NOT navigation, blog posts, services the company sells, or page furniture.
- If the page says there are no openings, or lists none, return [].
- Do not invent, translate, or tidy titles. Copy them as written.
- Every field except "title" is null unless the page states it for that job.
  Never guess a location from the company's country. Never infer seniority
  from a title: "Senior Engineer" alone does not give experience_years.
- "department" is the job's team, and belongs in that field only. A bare team
  name is still not a job on its own.
"""


def _enum(value, allowed, aliases):
    """Snap a free-text value onto the filter vocabulary, or drop it."""
    v = " ".join(str(value or "").split()).lower().strip(" .")
    if not v:
        return None
    v = aliases.get(v, v)
    if v in allowed:
        return v
    # "Full-Time / Remote" and friends: take the first token that is known
    for part in re.split(r"[,/|;&]| and ", v):
        part = aliases.get(part.strip(), part.strip())
        if part in allowed:
            return part
    return None


def _years(value):
    """Minimum years of experience as an int, from a number or a phrase."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value) if 0 <= value <= 50 else None
    m = re.search(r"\d+", str(value or ""))
    if not m:
        return None
    n = int(m.group())
    return n if 0 <= n <= 50 else None


def _text(value, cap=120):
    v = " ".join(str(value or "").split())
    return v[:cap] or None if v and v.lower() not in ("null", "none", "n/a", "-") else None


LINK_PROMPT = """You are given the links from a company's homepage.

Reply with ONLY the single URL of the page that lists their job openings —
careers, vacancies, "join us", "work with us", "life at <company>".

- Reply with the URL exactly as given, nothing else. No prose, no quotes.
- Prefer the company's own site over a third-party job board, unless the board
  is where they actually post (Greenhouse, Lever, Workable, and similar).
- A page about the services they sell, their team, their blog, or a single
  news article is NOT a careers page.
- If no link plausibly lists openings, reply exactly: NONE
"""


def links_of(url, cap=120):
    """(text, absolute url) for every on-page link, deduped, nav included."""
    from urllib.parse import urljoin, urlparse
    html = _cached_html(url)
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    seen, out = set(), []
    for a in soup.find_all("a", href=True):
        u = urljoin(url, a["href"])
        if not u.startswith("http") or u in seen:
            continue
        if re.search(r"facebook|twitter|linkedin\.com/(?!company)|instagram|"
                     r"youtube|\.(?:png|jpg|jpeg|pdf|zip|svg)$", u, re.I):
            continue
        seen.add(u)
        text = " ".join(a.get_text(" ", strip=True).split())[:70]
        out.append((text, u))
        if len(out) >= cap:
            break
    return out


def pick_careers(key, company, url):
    """Ask which homepage link is the careers page. Returns a URL or None."""
    links = links_of(url)
    if not links:
        return None
    listing = "\n".join(f"{t or '(no text)'} -> {u}" for t, u in links)
    digest = hashlib.sha1(f"link|{MODEL}|{PROMPT_VERSION}|{listing}".encode()).hexdigest()
    hit = LLM_CACHE / (digest + ".json")
    if hit.exists():
        answer = json.loads(hit.read_text()).get("raw")
    else:
        answer = ask(key, listing, company, system=LINK_PROMPT)
        LLM_CACHE.mkdir(parents=True, exist_ok=True)
        hit.write_text(json.dumps({"raw": answer}))
    answer = (answer or "").strip().strip('"\'<>').split()[-1:] or [""]
    answer = answer[0]
    # only a link that was actually on the page: no inventing URLs
    return answer if any(answer == u for _, u in links) else None


def api_key():
    key = os.environ.get("OPENROUTER_API_KEY")
    if key:
        return key
    # a .env next to the script is the least surprising place to keep it
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            k, _, v = line.partition("=")
            if k.strip() == "OPENROUTER_API_KEY":
                return v.strip().strip("\"'")
    return None


def _cached_html(url):
    """Cached markup for a URL: the rendered DOM if we have it, else the raw."""
    if not url:
        return None
    key = hashlib.sha1(url.encode()).hexdigest()
    for p in (CACHE / f"{key}.rendered.html", CACHE / f"{key}.html"):
        if p.exists():
            return p.read_text(errors="replace")
    return None


def page_text(url):
    """Visible text of an already-cached page, or None if we never fetched it."""
    if not url:
        return None
    html = _cached_html(url)
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "svg"]):
        tag.decompose()
    text = re.sub(r"\n{3,}", "\n\n", soup.get_text("\n", strip=True))
    return text[:MAX_CHARS] or None


def ask(key, text, company, system=None):
    """One completion. Returns raw content string; raises on transport error."""
    body = json.dumps({
        "model": MODEL,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": system or PROMPT},
            {"role": "user", "content": f"Company: {company}\n\n{text}"},
        ],
    }).encode()
    req = urllib.request.Request(API, data=body, headers={
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        # OpenRouter attributes usage to these; harmless but polite
        "HTTP-Referer": "https://github.com/mesaugat/tech-companies-in-nepal",
        "X-Title": "vacancy-finder",
    })
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                payload = json.loads(r.read())
            # OpenRouter reports upstream rate limits and provider faults in a
            # 200 body, so the HTTPError branch below never sees them
            if "choices" not in payload:
                msg = (payload.get("error") or {}).get("message", str(payload)[:200])
                code = (payload.get("error") or {}).get("code")
                if code in (408, 429, 500, 502, 503, 504) and attempt < 5:
                    time.sleep(2 ** attempt * 5)
                    continue
                raise RuntimeError(f"openrouter: {msg}")
            return payload["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            # 429/5xx are worth another go; a 400 never is
            if e.code not in (408, 429, 500, 502, 503, 504) or attempt == 5:
                raise
            # :free models return 429 often; back off properly rather than burn
            # the daily quota on retries
            time.sleep(2 ** attempt * 5)
        except (urllib.error.URLError, TimeoutError):
            if attempt == 5:
                raise
            time.sleep(2 ** attempt * 5)


def parse(content):
    """Model output -> job dicts. Tolerates a stray fence or preamble."""
    s = (content or "").strip()
    s = re.sub(r"^```(?:json)?|```$", "", s, flags=re.M).strip()
    start, end = s.find("["), s.rfind("]")
    if start == -1 or end == -1:
        return []
    try:
        items = json.loads(s[start:end + 1])
    except json.JSONDecodeError:
        return []
    out = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            it = {"title": it}
        title = " ".join(str(it.get("title") or "").split())
        if not (2 < len(title) <= 90):
            continue
        out.append({
            "title": title,
            "location": _text(it.get("location"), 60),
            "workplace": _enum(it.get("workplace"), WORKPLACE, WORKPLACE_ALIASES),
            "employment_type": _enum(it.get("employment_type"), EMPLOYMENT,
                                     EMPLOYMENT_ALIASES),
            "experience": _text(it.get("experience"), 200),
            "experience_years": _years(it.get("experience_years")
                                       if it.get("experience_years") is not None
                                       else it.get("experience")),
            "department": _text(it.get("department"), 60),
            "deadline": _text(it.get("deadline"), 60),
            "salary": _text(it.get("salary"), 80),
        })
    return out


def extract(key, rec, dry_run=False, refresh=False):
    """Jobs for one record.

    The cache holds the model's *raw* answer, so changing how we parse it costs
    nothing — only a prompt change (via PROMPT_VERSION) has to be re-billed.
    """
    text = page_text(rec.get("careers_url")) or page_text(rec.get("site"))
    if not text:
        return None
    digest = hashlib.sha1(f"{MODEL}|{PROMPT_VERSION}|{text}".encode()).hexdigest()
    hit = LLM_CACHE / (digest + ".json")
    if hit.exists() and not refresh:
        blob = json.loads(hit.read_text())
        # a list is the old format, where the parsed jobs were what got stored
        return blob if isinstance(blob, list) else parse(blob.get("raw"))
    if dry_run:
        return None
    raw = ask(key, text, rec["company"])
    LLM_CACHE.mkdir(parents=True, exist_ok=True)
    hit.write_text(json.dumps({"raw": raw}))
    return parse(raw)


def targets(data, only):
    for r in data:
        m = r.get("method")
        if only and m != only:
            continue
        if m in ("heuristic", "rendered"):
            yield r
        elif m is None and (r.get("error") or "") in RETRY_ERRORS:
            yield r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["heuristic", "rendered"])
    ap.add_argument("--limit", type=int)
    ap.add_argument("--workers", type=int, default=2,
                    help="keep low: :free models are rate-limited")
    ap.add_argument("--dry-run", action="store_true",
                    help="count the pages and cost nothing")
    ap.add_argument("--refresh", action="store_true",
                    help="ignore cached answers and ask again")
    args = ap.parse_args()

    key = api_key()
    if not key and not args.dry_run:
        sys.exit("set OPENROUTER_API_KEY (env or a .env file next to this script)")

    path = ROOT / "jobs.json"
    data = json.loads(path.read_text())
    todo = list(targets(data, args.only))[:args.limit]
    print(f"{len(todo)} pages to re-read with {MODEL}", file=sys.stderr)
    if args.dry_run:
        cached = sum(1 for r in todo if extract(None, r, dry_run=True) is not None)
        print(f"{cached} already cached, {len(todo) - cached} would be billed",
              file=sys.stderr)
        return

    done = changed = 0
    with cf.ThreadPoolExecutor(args.workers) as pool:
        futures = {pool.submit(extract, key, r, refresh=args.refresh): r
                   for r in todo}
        for fut in cf.as_completed(futures):
            rec = futures[fut]
            done += 1
            try:
                jobs = fut.result()
            except Exception as e:                     # one bad page, not the run
                print(f"  ! {rec['company']}: {type(e).__name__}: {e}", file=sys.stderr)
                continue
            if jobs is None:
                continue
            before = len(rec.get("jobs") or [])
            url = rec.get("careers_url") or rec.get("site")
            rec["jobs"] = [dict(j, url=url) for j in jobs]
            rec["method"] = "llm" if jobs else None
            rec["error"] = None if jobs else "no openings detected"
            changed += 1
            print(f"  {rec['company'][:34]:<36} {before} -> {len(jobs)}", file=sys.stderr)
            if done % 10 == 0:
                print(f"  {done}/{len(todo)}", file=sys.stderr)

    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    tmp.replace(path)
    n = sum(len(r.get("jobs") or []) for r in data)
    print(f"\n{changed} pages re-read, {n} openings total", file=sys.stderr)


def demo():
    one = parse('[{"title":"Backend Engineer","location":"Kathmandu"}]')[0]
    assert one["title"] == "Backend Engineer" and one["location"] == "Kathmandu"
    # every field is present even when the model omits it, so the UI can filter
    assert set(one) == {"title", "location", "workplace", "employment_type",
                        "experience", "experience_years", "department",
                        "deadline", "salary"}
    assert one["workplace"] is None and one["experience_years"] is None

    assert parse("Here you go: []") == []
    assert parse("not json at all") == []
    assert parse('```json\n[{"title":"QA Lead"}]\n```')[0]["title"] == "QA Lead"
    assert parse('[{"title":"  Senior   QA  "}]')[0]["title"] == "Senior QA"
    assert parse('[{"title":"x"}]') == []                      # too short
    assert parse(f'[{{"title":"{"y" * 200}"}}]') == []          # too long
    assert parse('["Plain String Title"]')[0]["title"] == "Plain String Title"

    # enums snap onto the filter vocabulary, junk drops rather than polluting it
    assert _enum("Full Time", EMPLOYMENT, EMPLOYMENT_ALIASES) == "full-time"
    assert _enum("INTERN", EMPLOYMENT, EMPLOYMENT_ALIASES) == "internship"
    assert _enum("Full-Time / Remote", EMPLOYMENT, EMPLOYMENT_ALIASES) == "full-time"
    assert _enum("Work From Home", WORKPLACE, WORKPLACE_ALIASES) == "remote"
    assert _enum("On-Site", WORKPLACE, WORKPLACE_ALIASES) == "onsite"
    assert _enum("whatever", WORKPLACE, WORKPLACE_ALIASES) is None
    assert _enum(None, WORKPLACE, WORKPLACE_ALIASES) is None

    assert _years(3) == 3 and _years("2+ years") == 2 and _years("at least 5") == 5
    assert _years(None) is None and _years("fresher") is None
    assert _years(99) is None                       # nonsense, not a career
    assert _years(True) is None                     # bool is not a year count

    # experience_years falls back to reading the phrase
    got = parse('[{"title":"Dev","experience":"3+ years of Django"}]')[0]
    assert got["experience_years"] == 3 and got["experience"] == "3+ years of Django"

    assert _text("  ") is None and _text("N/A") is None and _text("null") is None

    data = [
        {"company": "a", "method": "ats:lever", "jobs": [1], "error": None},
        {"company": "b", "method": "heuristic", "jobs": [1], "error": None},
        {"company": "c", "method": None, "jobs": [], "error": "no openings detected"},
        {"company": "d", "method": None, "jobs": [], "error": "site unreachable"},
    ]
    assert [r["company"] for r in targets(data, None)] == ["b", "c"]
    assert [r["company"] for r in targets(data, "heuristic")] == ["b"]
    print("ok")


if __name__ == "__main__":
    if "--demo" in sys.argv:
        demo()
    else:
        main()
