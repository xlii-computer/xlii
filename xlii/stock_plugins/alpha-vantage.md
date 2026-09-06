---
id: alpha-vantage
name: Alpha Vantage
description: Stock quotes, time-series, fundamentals, FX, crypto, and technical indicators
categories: [finance, stocks]
effect: read-only
trust: subscription
auth_type: query_param
auth_env_vars:
  - ALPHA_VANTAGE_KEY
actions:
  - id: set
    description: Store the Alpha Vantage API key in the vault
    params:
      ALPHA_VANTAGE_KEY: {secret: true, store: true, required: true, description: "API key from alphavantage.co"}
    output: raw
  - id: quote
    description: Latest quote for a stock symbol
    method: GET
    url: https://www.alphavantage.co/query
    params:
      function: {const: "GLOBAL_QUOTE"}
      symbol: {required: true, description: "Ticker symbol (e.g. AAPL, MSFT, .LON suffix for non-US)"}
      apikey: {const: "${ALPHA_VANTAGE_KEY}"}
    response_shape: ".['Global Quote'] → {symbol, open, high, low, price, volume, change, change_percent}"
    output_schema:
      type: object
      additionalProperties: false
      required: [symbol, price, open, high, low, volume, change, change_pct, latest_day]
      properties:
        symbol: {type: string, description: "From response['Global Quote']['01. symbol']"}
        price: {type: number, description: "From response['Global Quote']['05. price'] — cast string to number"}
        open: {type: number, description: "From response['Global Quote']['02. open'] — cast"}
        high: {type: number, description: "From response['Global Quote']['03. high'] — cast"}
        low: {type: number, description: "From response['Global Quote']['04. low'] — cast"}
        volume: {type: integer, description: "From response['Global Quote']['06. volume'] — cast"}
        change: {type: number, description: "From response['Global Quote']['09. change'] — cast"}
        change_pct: {type: number, description: "From response['Global Quote']['10. change percent'] — strip the '%' and cast"}
        latest_day: {type: string, description: "From response['Global Quote']['07. latest trading day']"}
    output: schema
    output_transforms:                    # AV uses numbered string-keys under "Global Quote" — segment-list paths reach them
      - {op: rename, from: ["Global Quote", "01. symbol"], to: symbol}
      - {op: rename, from: ["Global Quote", "05. price"], to: price}
      - {op: rename, from: ["Global Quote", "02. open"], to: open}
      - {op: rename, from: ["Global Quote", "03. high"], to: high}
      - {op: rename, from: ["Global Quote", "04. low"], to: low}
      - {op: rename, from: ["Global Quote", "06. volume"], to: volume}
      - {op: rename, from: ["Global Quote", "09. change"], to: change}
      - {op: rename, from: ["Global Quote", "10. change percent"], to: change_pct}
      - {op: rename, from: ["Global Quote", "07. latest trading day"], to: latest_day}
      - {op: cast, fields: [price, open, high, low, change, volume], to: number}
      - {op: cast, fields: [change_pct], to: number, strip: ["%"]}
    output_renderer: text_template
    output_renderer_args:
      template: |-
        {symbol} — {latest_day}

          price:    ${price:,.2f}
          change:   {change:+.2f}  ({change_pct:+.2f}%)
          open:     ${open:,.2f}
          high:     ${high:,.2f}
          low:      ${low:,.2f}
          volume:   {volume:,}
  - id: daily_series
    description: Daily OHLCV time series for a stock
    method: GET
    url: https://www.alphavantage.co/query
    params:
      function: {const: "TIME_SERIES_DAILY"}
      symbol: {required: true, description: "Ticker symbol"}
      outputsize: {default: "compact", enum: ["compact", "full"], description: "compact=100 days, full=20+ years"}
      apikey: {const: "${ALPHA_VANTAGE_KEY}"}
    response_shape: ".['Time Series (Daily)'] → {date: {open, high, low, close, volume}}"
  - id: intraday
    description: Intraday bars (1/5/15/30/60 min) for a stock
    method: GET
    url: https://www.alphavantage.co/query
    params:
      function: {const: "TIME_SERIES_INTRADAY"}
      symbol: {required: true, description: "Ticker symbol"}
      interval: {required: true, enum: ["1min", "5min", "15min", "30min", "60min"]}
      apikey: {const: "${ALPHA_VANTAGE_KEY}"}
  - id: symbol_search
    description: Search for a stock symbol by keyword
    method: GET
    url: https://www.alphavantage.co/query
    params:
      function: {const: "SYMBOL_SEARCH"}
      keywords: {required: true, description: "Search keywords (company name, partial ticker)"}
      apikey: {const: "${ALPHA_VANTAGE_KEY}"}
    response_shape: ".bestMatches[] → {symbol, name, type, region, currency}"
    output_schema:
      type: object
      additionalProperties: false
      required: [matches]
      properties:
        matches:
          type: array
          description: "Up to 10 entries from response.bestMatches[]"
          items:
            type: object
            additionalProperties: false
            required: [symbol, name, type, region, currency]
            properties:
              symbol: {type: string, description: "From bestMatches[i]['1. symbol']"}
              name: {type: string, description: "From bestMatches[i]['2. name']"}
              type: {type: string, description: "From bestMatches[i]['3. type']"}
              region: {type: string, description: "From bestMatches[i]['4. region']"}
              currency: {type: string, description: "From bestMatches[i]['8. currency']"}
    output: schema
    output_transforms:
      - {op: rename, list: bestMatches, from: ["1. symbol"], to: symbol}
      - {op: rename, list: bestMatches, from: ["2. name"], to: name}
      - {op: rename, list: bestMatches, from: ["3. type"], to: type}
      - {op: rename, list: bestMatches, from: ["4. region"], to: region}
      - {op: rename, list: bestMatches, from: ["8. currency"], to: currency}
    output_renderer: table
    output_renderer_args:
      header: "Symbol matches"
      list_key: bestMatches
      columns:
        - {key: symbol, label: "Symbol", max_width: 10}
        - {key: name, label: "Name", max_width: 36}
        - {key: type, label: "Type", max_width: 10}
        - {key: region, label: "Region", max_width: 16}
        - {key: currency, label: "Cur", max_width: 4}
  - id: company_overview
    description: Fundamentals — PE, market cap, EPS, dividend yield, sector
    method: GET
    url: https://www.alphavantage.co/query
    params:
      function: {const: "OVERVIEW"}
      symbol: {required: true, description: "Ticker symbol"}
      apikey: {const: "${ALPHA_VANTAGE_KEY}"}
    output_schema:
      type: object
      additionalProperties: false
      required: [symbol, name, sector, industry, market_cap, pe, eps, dividend_yield_pct, beta, week52_high, week52_low]
      properties:
        symbol: {type: string, description: "From response.Symbol"}
        name: {type: string, description: "From response.Name"}
        sector: {type: string, description: "From response.Sector"}
        industry: {type: string, description: "From response.Industry"}
        market_cap: {type: number, description: "From response.MarketCapitalization — cast string to number"}
        pe: {type: number, description: "From response.PERatio — cast, default 0 if 'None'"}
        eps: {type: number, description: "From response.EPS — cast, default 0 if 'None'"}
        dividend_yield_pct: {type: number, description: "From response.DividendYield — cast and multiply by 100, default 0 if 'None'"}
        beta: {type: number, description: "From response.Beta — cast, default 0 if 'None'"}
        week52_high: {type: number, description: "From response['52WeekHigh'] — cast"}
        week52_low: {type: number, description: "From response['52WeekLow'] — cast"}
    output: schema
    output_transforms:
      - {op: rename, from: Symbol, to: symbol}
      - {op: rename, from: Name, to: name}
      - {op: rename, from: Sector, to: sector}
      - {op: rename, from: Industry, to: industry}
      - {op: rename, from: MarketCapitalization, to: market_cap}
      - {op: rename, from: PERatio, to: pe}
      - {op: rename, from: EPS, to: eps}
      - {op: rename, from: DividendYield, to: dividend_yield_pct}
      - {op: rename, from: Beta, to: beta}
      - {op: rename, from: ["52WeekHigh"], to: week52_high}
      - {op: rename, from: ["52WeekLow"], to: week52_low}
      - {op: cast, fields: [market_cap, pe, eps, beta, week52_high, week52_low], to: number}
      - {op: scale, fields: [dividend_yield_pct], by: 100}   # AV returns a fraction (0.0052 → 0.52%)
    output_renderer: text_template
    output_renderer_args:
      template: |-
        {name} ({symbol})
          sector:           {sector}
          industry:         {industry}
          market cap:       ${market_cap:,.0f}
          P/E ratio:        {pe:.2f}
          EPS:              ${eps:.2f}
          dividend yield:   {dividend_yield_pct:.2f}%
          beta:             {beta:.2f}
          52w high / low:   ${week52_high:,.2f} / ${week52_low:,.2f}
  - id: fx_rate
    description: Real-time FX exchange rate between two currencies
    method: GET
    url: https://www.alphavantage.co/query
    params:
      function: {const: "CURRENCY_EXCHANGE_RATE"}
      from_currency: {required: true, description: "Source currency code (USD, EUR, BTC, etc.)"}
      to_currency: {required: true, description: "Target currency code"}
      apikey: {const: "${ALPHA_VANTAGE_KEY}"}
    output_schema:
      type: object
      additionalProperties: false
      required: [from_code, from_name, to_code, to_name, rate, last_refreshed]
      properties:
        from_code: {type: string, description: "From response['Realtime Currency Exchange Rate']['1. From_Currency Code']"}
        from_name: {type: string, description: "From response['Realtime Currency Exchange Rate']['2. From_Currency Name']"}
        to_code: {type: string, description: "From response['Realtime Currency Exchange Rate']['3. To_Currency Code']"}
        to_name: {type: string, description: "From response['Realtime Currency Exchange Rate']['4. To_Currency Name']"}
        rate: {type: number, description: "From response['Realtime Currency Exchange Rate']['5. Exchange Rate'] — cast"}
        last_refreshed: {type: string, description: "From response['Realtime Currency Exchange Rate']['6. Last Refreshed']"}
    output: schema
    output_transforms:
      - {op: rename, from: ["Realtime Currency Exchange Rate", "1. From_Currency Code"], to: from_code}
      - {op: rename, from: ["Realtime Currency Exchange Rate", "2. From_Currency Name"], to: from_name}
      - {op: rename, from: ["Realtime Currency Exchange Rate", "3. To_Currency Code"], to: to_code}
      - {op: rename, from: ["Realtime Currency Exchange Rate", "4. To_Currency Name"], to: to_name}
      - {op: rename, from: ["Realtime Currency Exchange Rate", "5. Exchange Rate"], to: rate}
      - {op: rename, from: ["Realtime Currency Exchange Rate", "6. Last Refreshed"], to: last_refreshed}
      - {op: cast, fields: [rate], to: number}
    output_renderer: text_template
    output_renderer_args:
      template: |-
        1 {from_code} ({from_name}) = {rate:,.6f} {to_code} ({to_name})
          last refreshed: {last_refreshed}
  - id: technical_indicator
    description: Technical indicator (RSI, SMA, EMA, MACD, BBANDS, etc.)
    method: GET
    url: https://www.alphavantage.co/query
    params:
      function: {required: true, description: "Indicator name: RSI, SMA, EMA, MACD, BBANDS, ADX, STOCH, etc."}
      symbol: {required: true, description: "Ticker symbol"}
      interval: {default: "daily", enum: ["1min", "5min", "15min", "30min", "60min", "daily", "weekly", "monthly"]}
      time_period: {default: "14", description: "Number of data points for the indicator"}
      series_type: {default: "close", enum: ["close", "open", "high", "low"]}
      apikey: {const: "${ALPHA_VANTAGE_KEY}"}
---

# Alpha Vantage

Free-tier US/global stocks, FX, crypto, and ~50 technical indicators. Free key at https://www.alphavantage.co/support/#api-key (no email verification, instant). Free limit: 25 calls/day, 5 calls/minute. Premium starts at $50/mo for higher limits.

## Auth

Run `alpha-vantage.set`. The key is a form secret. The agent must not ask for it.

## Usage

### Latest quote

```bash
curl -s "https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol={SYMBOL}&apikey=${ALPHA_VANTAGE_KEY}"
```

### Daily time-series (full history)

```bash
curl -s "https://www.alphavantage.co/query?function=TIME_SERIES_DAILY&symbol={SYMBOL}&outputsize=compact&apikey=${ALPHA_VANTAGE_KEY}"
```

`outputsize=compact` returns last 100 trading days; `full` returns 20+ years.

### Intraday (1, 5, 15, 30, 60 minute bars)

```bash
curl -s "https://www.alphavantage.co/query?function=TIME_SERIES_INTRADAY&symbol={SYMBOL}&interval=5min&apikey=${ALPHA_VANTAGE_KEY}"
```

### Symbol search

```bash
curl -s "https://www.alphavantage.co/query?function=SYMBOL_SEARCH&keywords={QUERY}&apikey=${ALPHA_VANTAGE_KEY}"
```

### Company overview / fundamentals

```bash
curl -s "https://www.alphavantage.co/query?function=OVERVIEW&symbol={SYMBOL}&apikey=${ALPHA_VANTAGE_KEY}"
```

PE, market cap, EPS, dividend yield, sector, etc. — one big flat object.

### FX rate

```bash
curl -s "https://www.alphavantage.co/query?function=CURRENCY_EXCHANGE_RATE&from_currency={FROM}&to_currency={TO}&apikey=${ALPHA_VANTAGE_KEY}"
```

### Technical indicator (e.g. 14-day RSI)

```bash
curl -s "https://www.alphavantage.co/query?function=RSI&symbol={SYMBOL}&interval=daily&time_period=14&series_type=close&apikey=${ALPHA_VANTAGE_KEY}"
```

Other functions: `SMA`, `EMA`, `MACD`, `BBANDS`, `ADX`, `STOCH`, …

## Response shape

JSON. Time-series endpoints nest data under keys like `"Time Series (Daily)"` keyed by date. Quote endpoint returns under `"Global Quote"`. Numbers are strings — cast before doing arithmetic.

## Notes

- **Rate-limit watch:** when you hit the free quota, the API still returns 200 OK but with an `Information` or `Note` field instead of data. Always check for those before parsing.
- **Symbol coverage:** US tickers (`AAPL`, `MSFT`) work directly; non-US use exchange suffix (`.LON`, `.TYO`) or full ISIN search via `SYMBOL_SEARCH`.
- For crypto OHLCV use `DIGITAL_CURRENCY_DAILY`; for forex daily use `FX_DAILY`.
