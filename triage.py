"""Diagnose why sites failed: classify the actual exception per company."""
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import requests

from scrape import UA

d = json.load(open("jobs.json"))
targets = [r for r in d if r["error"] == "site unreachable"]


def probe(r):
    url = r["site"]
    try:
        resp = requests.get(url, timeout=25, headers={"User-Agent": UA}, allow_redirects=True)
        return (r["company"], url, f"HTTP {resp.status_code}", len(resp.text))
    except Exception as e:
        return (r["company"], url, f"{type(e).__name__}: {str(e)[:70]}", 0)


with ThreadPoolExecutor(max_workers=16) as ex:
    rows = list(ex.map(probe, targets))


def bucket(msg):
    if msg.startswith("HTTP"):
        return msg
    if "SSLError" in msg or "CertificateError" in msg:
        return "TLS/cert failure"
    if "NameResolution" in msg or "gaierror" in msg:
        return "DNS does not resolve (dead domain)"
    if "ConnectTimeout" in msg or "ReadTimeout" in msg or "Timeout" in msg:
        return "timeout"
    if "TooManyRedirects" in msg:
        return "redirect loop"
    return msg.split(":")[0]


for c, u, m, n in sorted(rows, key=lambda x: bucket(x[2])):
    print(f"{bucket(m)[:40]:<42} {c[:30]:<32} {u[:45]}")
print()
for k, v in Counter(bucket(m) for _, _, m, _ in rows).most_common():
    print(f"{v:4d}  {k}")
