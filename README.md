# SEC EDGAR Scraper — Filings, Financials & Insider Trades

Scrape **SEC EDGAR**, the U.S. regulator's public filing database: company profiles, filing lists, **XBRL financial facts**, **Form 4 insider transactions** and **full-text search** — no login, no API key, no browser.

EDGAR data is **public-domain** U.S. government information from official SEC endpoints.

## What you get

- **Company** — profile: name, CIK, tickers, exchanges, SIC industry, EIN, LEI, fiscal year end, address, website, former names.
- **Filings** — list filings with form type, filing/report dates, accession number, primary document, **direct document URL**, filing index URL, film number and size — filterable by form type (10-K, 10-Q, 8-K, S-1, 13F, …).
- **Facts** — **XBRL financial facts** (structured financials): concept, label, unit, value, period start/end, fiscal year/period, form, filed date and frame — filterable by concept.
- **Form 4** — **insider transactions**: reporting owner (CIK, name, officer/director/10% flags, title), security, transaction date, code (buy/sell/award/grant…), shares, price, shares owned after, direct/indirect, plus derivative details.
- **Search** — **EDGAR full-text search** across all filings, filterable by form type and date range.

All rows live in one dataset, tagged by `record_type` (`company`, `filing`, `fact`, `transaction`, `search_result`).

## Use cases

- **Fintech / quant** — pull structured financials for screening and backtesting.
- **Compliance & legal** — track filings and disclosures.
- **Insider-trading signals** — Form 4 buys/sells by executives.
- **Research & journalism** — full-text search across corporate disclosures.
- **AI datasets** — clean, normalized filings data.
- **IR & competitive intel** — monitor peer filings.

## Input

```json
{ "scrapeType": "filings", "companies": ["AAPL", "TSLA"], "formTypes": ["10-K", "10-Q"] }
```
```json
{ "scrapeType": "facts", "companies": ["AAPL"], "concepts": ["Revenue", "NetIncomeLoss"] }
```
```json
{ "scrapeType": "form4", "companies": ["AAPL"], "maxForm4PerCompany": 20 }
```
```json
{ "scrapeType": "search", "searchQuery": "artificial intelligence", "searchForms": ["10-K"], "dateFrom": "2026-01-01" }
```

## Pricing

**Pay per result.** You are charged per record by type (company, filing, fact, transaction, search result) plus a small per-run fee. No subscription, no minimum.

## Notes

- Uses official SEC open-data endpoints (`data.sec.gov`, `efts.sec.gov`, `sec.gov/Archives`).
- The Actor sends a descriptive User-Agent and paces requests to respect SEC's fair-access policy (~10 req/s).
- Data is U.S. public-domain government information. Not investment advice.
