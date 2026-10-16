"""
SEC EDGAR Scraper — company filings, XBRL financial facts, insider (Form 4)
transactions and full-text search.

Dependency-free (stdlib + Apify REST). No login, no API key, no browser.

Data paths (official SEC open data):
  tickers:      https://www.sec.gov/files/company_tickers.json
  submissions:  https://data.sec.gov/submissions/CIK##########.json
  XBRL facts:   https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json
  full text:    https://efts.sec.gov/LATEST/search-index?q=...
  documents:    https://www.sec.gov/Archives/edgar/data/<cik>/<accn>/...

SEC fair-access policy: declare a descriptive User-Agent and stay under ~10 req/s.
"""
from __future__ import annotations
import json, os, re, ssl, uuid, time, math
from datetime import datetime, timezone
from urllib import request as urlrequest
from urllib.parse import urlencode, quote
from urllib.error import HTTPError, URLError
import xml.etree.ElementTree as ET

SCHEMA_VERSION = "1.0.0"
API = "https://api.apify.com/v2"
SEC = "https://www.sec.gov"
DATA = "https://data.sec.gov"
EFTS = "https://efts.sec.gov"
# SEC requires a declared contact UA.
UA = {
    "User-Agent": os.environ.get("SEC_USER_AGENT", "joey001-apify-sec-edgar/1.0 (contact: joeyang001@gmail.com)"),
    "Accept-Encoding": "gzip, deflate",
    "Accept": "application/json, text/xml, */*;q=0.8",
    "Host": "",  # placeholder, stripped below
}
UA.pop("Host", None)
_CTX = ssl.create_default_context()
DELAY = 0.16


def log(*a):
    print(*a, flush=True)


def _u(path):
    return f"{API}{path}{'&' if '?' in path else '?'}" + urlencode({"token": os.environ.get("APIFY_TOKEN", "")})


def _text(url, timeout=45):
    with urlrequest.urlopen(urlrequest.Request(url, headers=UA), timeout=timeout, context=_CTX) as r:
        data = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            import gzip
            data = gzip.decompress(data)
        return data


def _get(url, timeout=45):
    time.sleep(DELAY)
    return json.loads(_text(url, timeout).decode("utf-8", "ignore"))


def get_input():
    store = os.environ.get("ACTOR_DEFAULT_KEY_VALUE_STORE_ID")
    if store and os.environ.get("APIFY_TOKEN"):
        try:
            return json.loads(_text(_u(f"/key-value-stores/{store}/records/{os.environ.get('ACTOR_INPUT_KEY','INPUT')}")).decode("utf-8", "ignore"))
        except Exception as e:
            log(f"input fetch failed: {e}")
    if os.path.exists("input.json"):
        return json.load(open("input.json"))
    return {}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _num(v):
    if v is None or v == "":
        return None
    try:
        f = float(v)
        return f
    except (TypeError, ValueError):
        return None


def _sanitize(o):
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: _sanitize(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_sanitize(v) for v in o]
    return o


def push_items(items):
    items = [_sanitize(r) for r in items]
    token = os.environ.get("APIFY_TOKEN"); ds = os.environ.get("ACTOR_DEFAULT_DATASET_ID")
    if not (token and ds):
        with open("dataset_local.jsonl", "a") as f:
            for it in items:
                f.write(json.dumps(it) + "\n")
        return len(items)
    n = 0
    for i in range(0, len(items), 300):
        chunk = items[i:i + 300]
        req = urlrequest.Request(_u(f"/datasets/{ds}/items"), data=json.dumps(chunk).encode(),
                                 method="POST", headers={"Content-Type": "application/json"})
        urlrequest.urlopen(req, timeout=120, context=_CTX).read()
        n += len(chunk)
    return n


def charge(event_name, count):
    token = os.environ.get("APIFY_TOKEN"); rid = os.environ.get("ACTOR_RUN_ID")
    if not (token and rid) or count <= 0:
        return
    body = json.dumps({"eventName": event_name, "count": int(count)}).encode()
    req = urlrequest.Request(_u(f"/actor-runs/{rid}/charge"), data=body, method="POST",
                             headers={"Content-Type": "application/json", "idempotency-key": uuid.uuid4().hex})
    try:
        urlrequest.urlopen(req, timeout=30, context=_CTX).read()
    except HTTPError as e:
        try: log(f"charge {event_name} failed: {e.code} {e.read().decode()[:160]}")
        except Exception: log(f"charge {event_name} failed: {e.code}")
    except Exception as e:
        log(f"charge {event_name} failed: {e}")


# ---------------------------------------------------------------- helpers ----

_TICKERS = None


def load_tickers():
    global _TICKERS
    if _TICKERS is None:
        d = _get(f"{SEC}/files/company_tickers.json")
        m = {}
        for v in d.values():
            m[str(v.get("ticker", "")).upper()] = (int(v["cik_str"]), v.get("title"))
        _TICKERS = m
    return _TICKERS


def resolve_cik(v):
    """Accept a CIK (int/str), a ticker, or an EDGAR URL. Returns 10-digit CIK or None."""
    v = str(v).strip()
    if not v:
        return None
    m = re.search(r"CIK(\d{4,10})", v, re.I) or re.search(r"/edgar/data/(\d{4,10})", v)
    if m:
        return str(int(m.group(1))).zfill(10)
    if v.isdigit():
        return v.zfill(10)
    try:
        t = load_tickers()
    except Exception:
        t = {}
    rec = t.get(v.upper())
    return str(rec[0]).zfill(10) if rec else None


def _accn_nodash(a):
    return a.replace("-", "")


def _localname(tag):
    return tag.rsplit("}", 1)[-1]


def _txt(el, *path):
    cur = el
    for p in path:
        if cur is None:
            return None
        nxt = None
        for ch in list(cur):
            if _localname(ch.tag) == p:
                nxt = ch
                break
        cur = nxt
    return (cur.text.strip() if (cur is not None and cur.text) else None)


def _child(el, *path):
    cur = el
    for p in path:
        if cur is None:
            return None
        nxt = None
        for ch in list(cur):
            if _localname(ch.tag) == p:
                nxt = ch
                break
        cur = nxt
    return cur


# ---------------------------------------------------------------- records ----

def company_row(d, cik):
    addrs = d.get("addresses") or {}
    bus = addrs.get("business") or {}
    mail = addrs.get("mailing") or {}
    former = d.get("formerNames") or []
    return {
        "record_type": "company",
        "cik": d.get("cik"),
        "name": d.get("name"),
        "entity_type": d.get("entityType"),
        "sic": d.get("sic"),
        "sic_description": d.get("sicDescription"),
        "tickers": d.get("tickers") or None,
        "exchanges": d.get("exchanges") or None,
        "ein": d.get("ein"),
        "lei": d.get("lei"),
        "fiscal_year_end": d.get("fiscalYearEnd"),
        "state_of_incorporation": d.get("stateOfIncorporationDescription"),
        "category": d.get("category"),
        "website": d.get("website"),
        "investor_website": d.get("investorWebsite"),
        "phone": bus.get("phone"),
        "address_street1": bus.get("street1"),
        "address_city": bus.get("city"),
        "address_state": bus.get("stateOrCountryDescription") or bus.get("stateOrCountry"),
        "address_zip": bus.get("zipCode"),
        "mailing_city": mail.get("city"),
        "former_names": [f.get("name") for f in former] or None,
        "insider_transactions_present": d.get("insiderTransactionForIssuerExists"),
        "source_type": "submissions",
        "source_input": cik,
        "schema_version": SCHEMA_VERSION,
        "fetched_at": _now(),
    }


def filing_row(cik, cname, r):
    accn = r.get("accessionNumber")
    accn_nodash = _accn_nodash(accn) if accn else ""
    doc = r.get("primaryDocument")
    doc_url = f"{SEC}/Archives/edgar/data/{int(cik)}/{accn_nodash}/{doc}" if (doc and accn_nodash) else None
    idx_url = f"{SEC}/Archives/edgar/data/{int(cik)}/{accn_nodash}/{accn}-index.htm" if accn_nodash else None
    form = r.get("form") or ""
    return {
        "record_type": "filing",
        "cik": cik, "company_name": cname,
        "form": form,
        "filing_date": r.get("filingDate"),
        "report_date": r.get("reportDate"),
        "acceptance_datetime": r.get("acceptanceDateTime"),
        "accession_number": accn,
        "primary_document": doc,
        "primary_doc_url": doc_url,
        "filing_index_url": idx_url,
        "film_number": r.get("filmNumber"),
        "items_reported": r.get("items"),
        "size": r.get("size"),
        "is_xbrl": r.get("isXBRL"),
        "is_inline_xbrl": r.get("isInlineXBRL"),
        "is_amendment": form.endswith("/A") or form.endswith("A") and "/A" in form,
        "description": r.get("primaryDocDescription"),
        "source_type": "submissions",
        "source_input": cik,
        "schema_version": SCHEMA_VERSION,
        "fetched_at": _now(),
    }


def fact_rows(data, cik, concepts, limit):
    out = []
    ents = data.get("facts") or {}
    cname = data.get("entityName")
    for taxonomy, tags in ents.items():
        for concept, spec in tags.items():
            if concepts and not any(c.lower() in concept.lower() for c in concepts):
                continue
            label = spec.get("label"); desc = spec.get("description")
            for unit, points in (spec.get("units") or {}).items():
                for p in points:
                    out.append({
                        "record_type": "fact",
                        "cik": cik, "company_name": cname,
                        "taxonomy": taxonomy, "concept": concept,
                        "label": label, "unit": unit,
                        "value": _num(p.get("val")),
                        "start": p.get("start"), "end": p.get("end"),
                        "fy": p.get("fy"), "fp": p.get("fp"),
                        "form": p.get("form"), "filed": p.get("filed"),
                        "frame": p.get("frame"), "accn": p.get("accn"),
                        "source_type": "companyfacts",
                        "source_input": cik,
                        "schema_version": SCHEMA_VERSION,
                        "fetched_at": _now(),
                    })
                    if len(out) >= limit:
                        return out
    return out


def _owner_flags(owner):
    rel = _child(owner, "reportingOwnerRelationship")
    def b(x):
        v = _txt(owner, "reportingOwnerRelationship", x)
        return (v or "").lower() in ("1", "true")
    return {
        "owner_cik": _txt(owner, "reportingOwnerId", "rptOwnerCik"),
        "owner_name": _txt(owner, "reportingOwnerId", "rptOwnerName"),
        "is_director": b("isDirector"),
        "is_officer": b("isOfficer"),
        "is_ten_percent_owner": b("isTenPercentOwner"),
        "is_other": b("isOther"),
        "officer_title": _txt(owner, "reportingOwnerRelationship", "officerTitle"),
    }


def _txn_common(txn):
    def g(*p):
        return _txt(txn, *p)
    return {
        "security_title": g("securityTitle", "value"),
        "transaction_date": g("transactionDate", "value"),
        "transaction_form_type": g("transactionCoding", "transactionFormType"),
        "transaction_code": g("transactionCoding", "transactionCode"),
        "equity_swap_involved": g("transactionCoding", "equitySwapInvolved"),
        "shares": _num(g("transactionAmounts", "transactionShares", "value")),
        "price_per_share": _num(g("transactionAmounts", "transactionPricePerShare", "value")),
        "acquired_disposed": g("transactionAmounts", "transactionAcquiredDisposedCode", "value"),
        "shares_owned_after": _num(g("postTransactionAmounts", "sharesOwnedFollowingTransaction", "value")),
        "ownership_nature": g("ownershipNature", "directOrIndirectOwnership", "value"),
    }


def form4_transactions(root, meta):
    """Yield transaction records from a parsed ownershipDocument."""
    out = []
    issuer = _child(root, "issuer")
    issuer_name = _txt(issuer, "issuerName") if issuer is not None else None
    issuer_cik = _txt(issuer, "issuerCik") if issuer is not None else None
    sym = _txt(issuer, "issuerTradingSymbol") if issuer is not None else None
    owners = [o for o in list(root) if _localname(o.tag) == "reportingOwner"]
    owner = owners[0] if owners else None
    flags = _owner_flags(owner) if owner is not None else {}

    def base(txn, deriv):
        r = {
            "record_type": "transaction",
            "issuer_cik": issuer_cik, "issuer_name": issuer_name, "issuer_symbol": sym,
            "filing_date": meta.get("filing_date"), "accession_number": meta.get("accession_number"),
            "is_derivative": deriv,
        }
        r.update(flags)
        r.update(_txn_common(txn))
        if deriv:
            r["underlying_security_title"] = _txt(txn, "underlyingSecurity", "underlyingSecurityTitle", "value")
            r["underlying_shares"] = _num(_txt(txn, "underlyingSecurity", "underlyingSecurityShares", "value"))
            r["conversion_or_exercise_price"] = _num(_txt(txn, "conversionOrExercisePrice", "value"))
            r["exercise_date"] = _txt(txn, "exerciseDate", "value")
            r["expiration_date"] = _txt(txn, "expirationDate", "value")
        r["source_type"] = "form4"
        r["source_input"] = meta.get("source_input")
        r["schema_version"] = SCHEMA_VERSION
        r["fetched_at"] = _now()
        return r

    nd = _child(root, "nonDerivativeTable")
    if nd is not None:
        for t in list(nd):
            if _localname(t.tag) == "nonDerivativeTransaction":
                out.append(base(t, False))
    dt = _child(root, "derivativeTable")
    if dt is not None:
        for t in list(dt):
            if _localname(t.tag) == "derivativeTransaction":
                out.append(base(t, True))
    return out


def fetch_form4(cik, accn, filing_date, source_input, max_txn):
    accn_nodash = _accn_nodash(accn)
    base = f"{SEC}/Archives/edgar/data/{int(cik)}/{accn_nodash}"
    xml_name = None
    try:
        idx = _get(f"{base}/index.json")
        names = [i["name"] for i in (idx.get("directory", {}).get("item") or [])]
        cands = [n for n in names if n.lower().endswith(".xml") and "form4" in n.lower()]
        if not cands:
            cands = [n for n in names if re.match(r"^primary_doc\.xml$", n, re.I)]
        xml_name = cands[0] if cands else None
    except Exception:
        xml_name = "form4.xml"
    if not xml_name:
        return []
    try:
        raw = _text(f"{base}/{xml_name}")
        root = ET.fromstring(raw)
    except Exception as e:
        log(f"form4 parse failed {cik}/{accn}: {e}")
        return []
    meta = {"filing_date": filing_date, "accession_number": accn, "source_input": source_input}
    return form4_transactions(root, meta)[:max_txn]


def search_rows(q, forms, date_from, date_to, limit):
    params = {"q": q}
    if forms:
        params["forms"] = ",".join(forms)
    if date_from or date_to:
        params["dateRange"] = "custom"
        if date_from:
            params["startdt"] = date_from
        if date_to:
            params["enddt"] = date_to
    d = _get(f"{EFTS}/LATEST/search-index?" + urlencode(params))
    hits = ((d.get("hits") or {}).get("hits") or [])[:limit]
    out = []
    for h in hits:
        src = h.get("_source") or {}
        fid = h.get("_id") or ""
        accn, _, fname = fid.partition(":")
        ciks = src.get("ciks") or []
        cik = str(ciks[0]).lstrip("0") if ciks else None
        url = None
        if cik and accn:
            url = f"{SEC}/Archives/edgar/data/{cik}/{_accn_nodash(accn)}/{fname}" if fname else None
        out.append({
            "record_type": "search_result",
            "query": q,
            "cik": (str(cik).zfill(10) if cik else None),
            "company_name": (src.get("display_names") or [None])[0],
            "form": (src.get("root_forms") or src.get("file_type") or [None])[0] if isinstance(src.get("root_forms") or src.get("file_type"), list) else (src.get("root_forms") or src.get("file_type")),
            "file_date": src.get("file_date"),
            "period_ending": src.get("period_ending"),
            "accession_number": accn or None,
            "document": fname or None,
            "document_url": url,
            "source_type": "full_text_search",
            "source_input": q,
            "schema_version": SCHEMA_VERSION,
            "fetched_at": _now(),
        })
    return out


# ------------------------------------------------------------------ main -----

def _as_list(v):
    if v is None:
        return []
    return [v] if isinstance(v, str) else list(v)


def main():
    inp = get_input() or {}
    scrape = (inp.get("scrapeType") or "filings").lower()
    companies = _as_list(inp.get("companies"))
    form_types = [f.upper() for f in _as_list(inp.get("formTypes"))]
    concepts = _as_list(inp.get("concepts"))
    max_filings = int(inp.get("maxFilingsPerCompany") or 40)
    max_facts = int(inp.get("maxFactsPerCompany") or 2000)
    max_form4 = int(inp.get("maxForm4PerCompany") or 10)
    max_txn = int(inp.get("maxTransactionsPerFiling") or 100)
    max_items = int(inp.get("maxItems") or 100000)
    search_q = (inp.get("searchQuery") or "").strip()
    search_forms = [f.upper() for f in _as_list(inp.get("searchForms"))]
    date_from = inp.get("dateFrom"); date_to = inp.get("dateTo")
    search_limit = int(inp.get("searchLimit") or 100)

    charge("actor-start", 1)
    rows = []; errors = []; counts = {}

    def add(kind, batch):
        for r in batch:
            rows.append(r)
        counts[kind] = counts.get(kind, 0) + len(batch)

    # resolve companies
    ciks = []
    for c in companies:
        try:
            cik = resolve_cik(c)
        except Exception as e:
            errors.append(f"resolve '{str(c)[:30]}': {str(e)[:50]}")
            cik = None
        if cik:
            ciks.append(cik)

    if scrape == "search":
        if not search_q and not companies:
            search_q = "artificial intelligence"
        if not search_q:
            errors.append("searchQuery required for search mode")
        else:
            try:
                add("search_result", search_rows(search_q, search_forms, date_from, date_to, search_limit))
            except (HTTPError, URLError, ValueError) as e:
                errors.append(f"search: {str(e)[:70]}")
    else:
        if not ciks:
            ciks = [resolve_cik("AAPL")]
        for cik in ciks:
            try:
                sub = _get(f"{DATA}/submissions/CIK{cik}.json")
            except (HTTPError, URLError, ValueError) as e:
                errors.append(f"submissions {cik}: {str(e)[:60]}")
                continue
            cname = sub.get("name")
            if scrape == "company":
                add("company", [company_row(sub, cik)])
            elif scrape == "filings":
                recent = (sub.get("filings") or {}).get("recent") or {}
                cols = list(recent.keys())
                recs = []
                for i in range(len(recent.get("form", []))):
                    recs.append({k: recent[k][i] for k in cols})
                # extra submission files (older filings)
                extra_files = (sub.get("filings") or {}).get("files") or []
                for f in extra_files:
                    if len(recs) >= max_filings:
                        break
                    try:
                        ex = _get(f"{DATA}/submissions/{f['name']}")
                    except Exception:
                        continue
                    for i in range(len(ex.get("form", []))):
                        recs.append({k: ex[k][i] for k in ex.keys()})
                out = []
                for r in recs:
                    if form_types and (r.get("form") or "").upper() not in form_types:
                        continue
                    out.append(filing_row(cik, cname, r))
                    if len(out) >= max_filings:
                        break
                add("filing", out)
            elif scrape == "facts":
                try:
                    cf = _get(f"{DATA}/api/xbrl/companyfacts/CIK{cik}.json", timeout=90)
                    add("fact", fact_rows(cf, cik, concepts, max_facts))
                except (HTTPError, URLError, ValueError) as e:
                    errors.append(f"facts {cik}: {str(e)[:60]}")
            elif scrape == "form4":
                recent = (sub.get("filings") or {}).get("recent") or {}
                cols = list(recent.keys())
                recs = [{k: recent[k][i] for k in cols} for i in range(len(recent.get("form", [])))]
                forms4 = [r for r in recs if (r.get("form") or "").upper() in ("4", "4/A")][:max_form4]
                for r in forms4:
                    try:
                        add("transaction", fetch_form4(cik, r["accessionNumber"], r.get("filingDate"), cik, max_txn))
                    except (HTTPError, URLError, ValueError) as e:
                        errors.append(f"form4 {r.get('accessionNumber')}: {str(e)[:50]}")
            else:
                errors.append(f"unknown scrapeType '{scrape}'")
            if len(rows) >= max_items:
                break

    rows = rows[:max_items]
    pushed = push_items(rows)
    for k in ("company", "filing", "fact", "transaction", "search_result"):
        charge(k, counts.get(k, 0))
    log(f"FINISHED scrape={scrape} pushed={pushed} counts={counts} errors={len(errors)}")
    if errors:
        log("Errors: " + "; ".join(errors[:10]))


if __name__ == "__main__":
    main()
