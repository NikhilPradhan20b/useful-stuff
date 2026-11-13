"""Diagnose failures from the last run, using the cached HTML.

Answers the two questions that decide what to fix next:
  no careers page found  -> was there a careers link we failed to see, or is the
                            page rendered by JS so no link exists in the HTML?
  no openings detected   -> is the page genuinely empty ("no vacancies"), or is
                            it a board we cannot parse (iframe, JS widget)?
"""
import collections
import hashlib
import json
import pathlib
import re
import sys

from bs4 import BeautifulSoup

from scrape import CAREER_WORDS, CACHE, ROOT

JS_APP = re.compile(r"__NEXT_DATA__|ng-version|data-reactroot|id=\"root\"|id=\"app\"|vue|nuxt", re.I)
NO_OPENINGS = re.compile(
    r"no (current |open |available )?(vacanc|opening|position|job)|"
    r"not (currently )?hiring|check back|no openings at (this|the) (time|moment)", re.I)
BOARD_IFRAME = re.compile(r"<iframe[^>]+(greenhouse|lever|workable|recruitee|bamboo|zoho|smartrecruiters|jazzhr|breezy)", re.I)
FORM_ONLY = re.compile(r"docs\.google\.com/forms|forms\.gle|typeform", re.I)


def cached(url):
    if not url:
        return None
    p = CACHE / (hashlib.sha1(url.encode()).hexdigest() + ".html")
    return p.read_text(errors="replace") if p.exists() else None


def diagnose_no_careers(rec):
    html = cached(rec["site"])
    if not html:
        return "homepage not cached"
    soup = BeautifulSoup(html, "html.parser")
    hrefs = [a.get("href", "") + " " + a.get_text(" ", strip=True) for a in soup.find_all("a")]
    if any(CAREER_WORDS.search(h) for h in hrefs):
        return "link EXISTS but was rejected"          # our filter is too strict
    if CAREER_WORDS.search(html):
        return "career word in HTML, not in a link"    # button/JS nav
    if len(hrefs) < 5 and JS_APP.search(html):
        return "JS-rendered shell (no links in HTML)"
    if len(html) < 2000:
        return "near-empty page (parked/placeholder)"
    return "genuinely no careers page"


def diagnose_no_openings(rec):
    html = cached(rec["careers_url"])
    if not html:
        return "careers page not cached"
    if BOARD_IFRAME.search(html):
        return "embedded ATS iframe (parseable, we miss it)"
    if FORM_ONLY.search(html):
        return "applications via Google Form/Typeform"
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    if NO_OPENINGS.search(text):
        return "explicitly says no openings (correct!)"
    if JS_APP.search(html) and len(text) < 1500:
        return "JS-rendered board (empty HTML)"
    if len(text) < 400:
        return "near-empty careers page"
    return "has text, our title patterns missed it"


def main():
    data = json.loads((ROOT / "jobs.json").read_text())
    data = [r for r in data if r.get("error") != "pending"]

    groups = collections.defaultdict(list)
    for r in data:
        e = r.get("error") or ""
        if "no careers page" in e:
            groups[diagnose_no_careers(r)].append(r)
        elif "no openings" in e:
            groups["openings: " + diagnose_no_openings(r)].append(r)

    for k, v in sorted(groups.items(), key=lambda x: -len(x[1])):
        print(f"\n{len(v):4d}  {k}")
        for r in v[:6]:
            print(f"        {r['company'][:34]:<36} {(r['careers_url'] or r['site'])[:56]}")

    if "-v" in sys.argv:
        print("\n--- sample text from 'title patterns missed it' ---")
        for r in groups.get("openings: has text, our title patterns missed it", [])[:3]:
            html = cached(r["careers_url"])
            text = BeautifulSoup(html, "html.parser").get_text("\n", strip=True)
            print(f"\n### {r['company']} {r['careers_url']}")
            print(text[:900])


if __name__ == "__main__":
    main()
