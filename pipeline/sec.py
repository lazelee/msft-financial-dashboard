"""
Minimal SEC EDGAR client: company facts (XBRL API), filing list, and XBRL instance documents.

SEC requires a descriptive User-Agent with contact details and allows at most 10 requests/second.
Responses are cached under .cache/ so reruns don't hit EDGAR again.
"""
import json
import os
import time
from pathlib import Path

import requests

CIK = "0000789019"  # Microsoft Corporation
ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / ".cache" / "sec"
_last_call = 0.0


def _user_agent():
    ua = os.environ.get("SEC_USER_AGENT", "").strip()
    if "@" not in ua:
        raise SystemExit(
            "SEC EDGAR requires a contact in the User-Agent. Set it first, e.g.\n"
            '  export SEC_USER_AGENT="Your Name your.email@example.com"   (macOS/Linux)\n'
            '  $env:SEC_USER_AGENT="Your Name your.email@example.com"     (Windows PowerShell)'
        )
    return ua


def _get(url, cache_name, max_age_days=7):
    """GET with on-disk cache, polite rate limiting and the SEC-required User-Agent."""
    global _last_call
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / cache_name
    if path.exists() and time.time() - path.stat().st_mtime < max_age_days * 86400:
        return path.read_bytes()
    wait = 0.15 - (time.time() - _last_call)
    if wait > 0:
        time.sleep(wait)
    res = requests.get(url, headers={"User-Agent": _user_agent(), "Accept-Encoding": "gzip, deflate"}, timeout=60)
    _last_call = time.time()
    res.raise_for_status()
    path.write_bytes(res.content)
    return res.content


def company_facts():
    """All non-dimensional XBRL facts Microsoft has ever filed (us-gaap and dei taxonomies)."""
    return json.loads(_get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{CIK}.json", "companyfacts.json"))


def filings(forms=("10-K", "10-Q")):
    """Recent periodic filings, newest first: dicts with form, report_date, accession, primary_doc."""
    sub = json.loads(_get(f"https://data.sec.gov/submissions/CIK{CIK}.json", "submissions.json", max_age_days=1))["filings"]["recent"]
    out = []
    for i, form in enumerate(sub["form"]):
        if form in forms:
            out.append({"form": form, "report_date": sub["reportDate"][i], "accession": sub["accessionNumber"][i],
                        "primary_doc": sub["primaryDocument"][i]})
    return out


def instance_document(filing):
    """The filing's XBRL instance (inline XBRL extracted to *_htm.xml), as bytes. Filed documents never change."""
    folder = filing["accession"].replace("-", "")
    name = filing["primary_doc"].replace(".htm", "_htm.xml")
    url = f"https://www.sec.gov/Archives/edgar/data/{int(CIK)}/{folder}/{name}"
    return _get(url, f"{folder}_{name}", max_age_days=3650)
