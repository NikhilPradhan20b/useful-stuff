"""Second pass: full exception text for ConnectionError + TLS buckets, so the
fix targets the real cause (dead DNS vs refused vs cert-name-mismatch)."""
import json
from concurrent.futures import ThreadPoolExecutor

import requests

from scrape import UA

d = json.load(open("jobs.json"))
targets = [r for r in d if r["error"] == "site unreachable"]


def probe(r):
    try:
        requests.get(r["site"], timeout=25, headers={"User-Agent": UA}, allow_redirects=True)
        return (r["company"], r["site"], "OK")
    except Exception as e:
        return (r["company"], r["site"], f"{type(e).__name__}: {e}")


with ThreadPoolExecutor(max_workers=16) as ex:
    rows = list(ex.map(probe, targets))


def cause(msg):
    if "NameResolutionError" in msg or "Name or service not known" in msg or "nodename nor servname" in msg:
        return "DNS-DEAD"
    if "ConnectionRefused" in msg or "Connection refused" in msg:
        return "REFUSED"
    if "certificate verify failed" in msg and "expired" in msg:
        return "CERT-EXPIRED"
    if "doesn't match" in msg or "Hostname mismatch" in msg or "CERTIFICATE_VERIFY_FAILED] Hostname" in msg:
        return "CERT-NAME-MISMATCH"
    if "self signed" in msg or "self-signed" in msg:
        return "CERT-SELFSIGNED"
    if "unable to get local issuer" in msg:
        return "CERT-CHAIN-INCOMPLETE"
    if "SSLError" in msg:
        return "TLS-OTHER"
    if "Timeout" in msg or "timed out" in msg:
        return "TIMEOUT"
    if "TooManyRedirects" in msg:
        return "REDIRECT-LOOP"
    if "RemoteDisconnected" in msg or "Connection aborted" in msg:
        return "ABORTED"
    return "OTHER"


from collections import Counter

for c, u, m in sorted(rows, key=lambda x: cause(x[2])):
    print(f"{cause(m):<24} {c[:28]:<30} {u[:42]}")
    if cause(m) in ("OTHER", "TLS-OTHER"):
        print(f"      -> {m[:200]}")
print()
for k, v in Counter(cause(m) for _, _, m in rows).most_common():
    print(f"{v:4d}  {k}")
