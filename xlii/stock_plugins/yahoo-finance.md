---
id: yahoo-finance
name: Yahoo Finance
description: Ticker search, last quote, and a short daily chart — unofficial public endpoints, no API key
categories: [finance, stocks]
effect: read-only
trust: subscription
auth_type: none
auth_env_vars: []
actions:
  - id: search
    description: Find ticker symbols by company name or partial symbol
    method: GET
    url: https://query1.finance.yahoo.com/v1/finance/search
    params:
      q: {required: true, description: "Company name or ticker fragment (Apple, AAPL, BRK)"}
      quotesCount: {const: "10"}
      newsCount: {const: "0"}
    response_shape: ".quotes[] → {symbol, shortname, quoteType, exchDisp}"
    output_schema:
      type: object
      additionalProperties: false
      required: [quotes]
      properties:
        quotes:
          type: array
          description: "Up to 10 entries from response.quotes[]"
          items:
            type: object
            additionalProperties: false
            required: [symbol, name, type, exchange]
            properties:
              symbol: {type: string, description: "From quotes[i].symbol"}
              name: {type: string, description: "From quotes[i].shortname, else longname, else symbol"}
              type: {type: string, description: "From quotes[i].quoteType"}
              exchange: {type: string, description: "From quotes[i].exchDisp, else exchange"}
    output: schema
    output_transforms:
      - {op: rename, list: quotes, from: shortname, to: name}
      - {op: first_of, list: quotes, to: name, from: [name, longname, symbol]}
      - {op: rename, list: quotes, from: quoteType, to: type}
      - {op: first_of, list: quotes, to: exchange, from: [exchDisp, exchange]}
    output_renderer: table
    output_renderer_args:
      header: "Yahoo Finance search"
      list_key: quotes
      columns:
        - {key: symbol, label: "Sym", max_width: 10}
        - {key: name, label: "Name", max_width: 28}
        - {key: type, label: "Type", max_width: 10}
        - {key: exchange, label: "Exch", max_width: 16}
  - id: quote
    description: Last regular-session price for a ticker (uses the public chart meta, not the gated quote API)
    method: GET
    url: https://query1.finance.yahoo.com/v8/finance/chart/{symbol}
    params:
      symbol: {required: true, description: "Ticker (AAPL, MSFT, ^GSPC)"}
      range: {const: "1d"}
      interval: {const: "1d"}
    headers:
      User-Agent: "Mozilla/5.0"
    response_shape: ".chart.result[0].meta → {symbol, regularMarketPrice, chartPreviousClose, currency}"
    output_schema:
      type: object
      additionalProperties: false
      required: [symbol, price, currency]
      properties:
        symbol: {type: string, description: "From meta.symbol"}
        name: {type: string, description: "From meta.shortName, else longName"}
        price: {type: number, description: "From meta.regularMarketPrice"}
        prev_close: {type: number, description: "From meta.chartPreviousClose, else previousClose"}
        currency: {type: string, description: "From meta.currency"}
        exchange: {type: string, description: "From meta.fullExchangeName, else exchangeName"}
        market_state: {type: string, description: "From meta.marketState"}
    output: schema
    output_transforms:
      - {op: lift, from: [chart, result, 0]}
      - {op: lift, from: meta}
      - {op: rename, from: regularMarketPrice, to: price}
      - {op: first_of, to: prev_close, from: [chartPreviousClose, previousClose]}
      - {op: first_of, to: name, from: [shortName, longName, symbol]}
      - {op: first_of, to: exchange, from: [fullExchangeName, exchangeName]}
      - {op: rename, from: marketState, to: market_state}
    output_renderer: text_template
    output_renderer_args:
      template: |-
        {symbol}  {name}

          price:     {price} {currency}
          prev close:{prev_close}
          exchange:  {exchange}
          market:    {market_state}
  - id: chart
    description: Daily OHLCV bars for a range (1mo default). Unofficial — datacenter IPs often 429.
    method: GET
    url: https://query1.finance.yahoo.com/v8/finance/chart/{symbol}
    params:
      symbol: {required: true, description: "Ticker"}
      range: {default: "1mo", enum: ["5d", "1mo", "3mo", "6mo", "1y", "5y", "ytd", "max"], description: "Chart range"}
      interval: {default: "1d", enum: ["1d", "1wk", "1mo"], description: "Bar size"}
    headers:
      User-Agent: "Mozilla/5.0"
    response_shape: ".chart.result[0] → {meta.symbol, timestamp[], indicators.quote[0].{open,high,low,close,volume}}"
    output: interpret
---

# Yahoo Finance

Unofficial public `query1.finance.yahoo.com` endpoints. No key. Yahoo
rate-limits datacenter IPs (HTTP 429) — if quote fails, wait or use
`alpha-vantage`.

```
/plugin subscribe yahoo-finance
/plugin call yahoo-finance.search q=Apple
/plugin call yahoo-finance.quote symbol=AAPL
/plugin call yahoo-finance.chart symbol=AAPL range=3mo
```

## Next

- `search` first when you only have a company name.
- Official bars + fundamentals stay on `alpha-vantage` (keyed).
