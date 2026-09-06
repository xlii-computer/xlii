---
id: sec-edgar
name: SEC EDGAR
description: Company submissions and full-text filing search on data.sec.gov / efts.sec.gov
categories: [finance, compliance]
effect: read-only
trust: subscription
auth_type: header
auth_env_vars:
  - SEC_EDGAR_USER_AGENT
actions:
  - id: set
    description: Store the SEC-required User-Agent (your name and email — not a secret key)
    params:
      SEC_EDGAR_USER_AGENT: {secret: true, store: true, required: true, description: "Name email@domain (SEC fair-access header)"}
    output: raw
  - id: submissions
    description: Company submission file — tickers, SIC, recent 10-K / 10-Q / 8-K list. cik is 10 digits, zero-padded.
    method: GET
    url: https://data.sec.gov/submissions/CIK{cik}.json
    headers:
      User-Agent: "${SEC_EDGAR_USER_AGENT}"
      Accept: "application/json"
    params:
      cik: {required: true, description: "10-digit CIK, zero-padded (0000320193 for Apple)"}
    response_shape: "{name, cik, tickers[], exchanges[], sic, sicDescription, filings.recent.form[]}"
    output_schema:
      type: object
      additionalProperties: false
      required: [name, cik]
      properties:
        name: {type: string, description: "From name"}
        cik: {type: string, description: "From cik"}
        ticker: {type: string, description: "From tickers[0] if present"}
        exchange: {type: string, description: "From exchanges[0] if present"}
        sic: {type: string, description: "From sic"}
        sic_description: {type: string, description: "From sicDescription"}
        fiscal_year_end: {type: string, description: "From fiscalYearEnd"}
    output: schema
    output_transforms:
      - {op: first_of, to: ticker, from: [[tickers, 0]]}
      - {op: first_of, to: exchange, from: [[exchanges, 0]]}
      - {op: rename, from: sicDescription, to: sic_description}
      - {op: rename, from: fiscalYearEnd, to: fiscal_year_end}
    output_renderer: text_template
    output_renderer_args:
      template: |-
        {name}  CIK {cik}
          ticker:   {ticker}
          exchange: {exchange}
          SIC:      {sic}  {sic_description}
          FYE:      {fiscal_year_end}
  - id: search
    description: Full-text search of recent EDGAR filings (efts). Optional form type and date bounds.
    method: GET
    url: https://efts.sec.gov/LATEST/search-index
    headers:
      User-Agent: "${SEC_EDGAR_USER_AGENT}"
      Accept: "application/json"
    params:
      q: {required: true, description: "Search text (company name, phrase, or ticker)"}
      forms: {description: "Form filter (10-K, 10-Q, 8-K, 4)"}
      startdt: {description: "Start date YYYY-MM-DD"}
      enddt: {description: "End date YYYY-MM-DD"}
    response_shape: ".hits.hits[]._source → {display_names, file_date, form, sics}"
    output: interpret
---

# SEC EDGAR

SEC requires a **User-Agent with a real contact** (`Name email@domain`). That is
not an API key; they block anonymous crawlers. Store it once:

```
/plugin subscribe sec-edgar
/plugin call sec-edgar.set
/plugin call sec-edgar.submissions cik=0000320193
/plugin call sec-edgar.search q=Apple forms=10-K
```

CIK is ten digits, zero-padded. `search` is for discovery; `submissions` is the
company file.

## Next

- Filing HTML lives under `https://www.sec.gov/Archives/edgar/data/…` — open
  those URLs in the browser, do not scrape the index as this plugin.
- Insider Form 4 detail is a second request; not in this first cut.
