---
id: coingecko
name: CoinGecko
description: Cryptocurrency prices, market data, history, and exchange info — no API key needed
categories: [finance, crypto]
effect: read-only
trust: subscription
auth_type: none
auth_env_vars: []
actions:
  - id: price
    description: Quick price lookup for one or more coins in one or more currencies
    method: GET
    url: https://api.coingecko.com/api/v3/simple/price
    params:
      ids: {required: true, description: "Comma-separated CoinGecko slugs (bitcoin, ethereum, solana)"}
      vs_currencies: {default: "usd", description: "Comma-separated currency codes (usd, eur, btc)"}
    response_shape: "{coin_id: {currency: number}}"
    # Stays interpret: a 2-level {coin:{currency:price}} flatten with variable
    # currencies — beyond the single-level flatten_map op. Small model turn.
    output_schema:
      type: object
      additionalProperties: false
      required: [prices]
      properties:
        prices:
          type: array
          description: "Flatten the nested {coin: {currency: price}} object into a list of {coin, currency, price} rows."
          items:
            type: object
            additionalProperties: false
            required: [coin, currency, price]
            properties:
              coin: {type: string, description: "Outer key (CoinGecko slug)"}
              currency: {type: string, description: "Inner key (uppercased — usd→USD)"}
              price: {type: number, description: "The numeric value"}
    output_renderer: table
    output_renderer_args:
      header: "CoinGecko prices"
      list_key: prices
      columns:
        - {key: coin, label: "Coin", max_width: 20}
        - {key: currency, label: "Cur", max_width: 6}
        - {key: price, label: "Price", max_width: 16}
  - id: search
    description: Search coins by ticker or name to find the canonical CoinGecko id
    method: GET
    url: https://api.coingecko.com/api/v3/search
    params:
      query: {required: true, description: "Ticker or coin name"}
    response_shape: ".coins[] → {id, name, symbol, market_cap_rank}"
    output_schema:
      type: object
      additionalProperties: false
      required: [coins]
      properties:
        coins:
          type: array
          description: "Up to 10 entries from response.coins[]"
          items:
            type: object
            additionalProperties: false
            required: [id, name, symbol, rank]
            properties:
              id: {type: string, description: "From coins[i].id (CoinGecko slug — use this for /get price)"}
              name: {type: string, description: "From coins[i].name"}
              symbol: {type: string, description: "From coins[i].symbol (uppercase)"}
              rank: {type: integer, description: "From coins[i].market_cap_rank, or 0 if null"}
    output: schema
    output_transforms:
      - {op: rename, list: coins, from: market_cap_rank, to: rank}
    output_renderer: table
    output_renderer_args:
      header: "CoinGecko search results"
      list_key: coins
      columns:
        - {key: rank, label: "Rank", max_width: 6}
        - {key: symbol, label: "Sym", max_width: 8}
        - {key: name, label: "Name", max_width: 30}
        - {key: id, label: "Slug", max_width: 24}
  - id: coin_detail
    description: Detailed info for a coin — description, links, market data
    method: GET
    url: https://api.coingecko.com/api/v3/coins/{coin_id}
    params:
      coin_id: {required: true, description: "CoinGecko slug (e.g. bitcoin)"}
    output_schema:
      type: object
      additionalProperties: false
      required: [name, symbol, rank, price_usd, market_cap_usd, volume_24h_usd, change_24h_pct, ath_usd, atl_usd, homepage, summary]
      properties:
        name: {type: string, description: "From response.name"}
        symbol: {type: string, description: "From response.symbol (uppercase it)"}
        rank: {type: integer, description: "From response.market_cap_rank, or 0 if null"}
        price_usd: {type: number, description: "From response.market_data.current_price.usd"}
        market_cap_usd: {type: number, description: "From response.market_data.market_cap.usd"}
        volume_24h_usd: {type: number, description: "From response.market_data.total_volume.usd"}
        change_24h_pct: {type: number, description: "From response.market_data.price_change_percentage_24h"}
        ath_usd: {type: number, description: "From response.market_data.ath.usd"}
        atl_usd: {type: number, description: "From response.market_data.atl.usd"}
        homepage: {type: string, description: "First non-empty entry from response.links.homepage[]"}
    output: schema
    output_transforms:
      - {op: rename, from: market_data.current_price.usd, to: price_usd}
      - {op: rename, from: market_data.market_cap.usd, to: market_cap_usd}
      - {op: rename, from: market_data.total_volume.usd, to: volume_24h_usd}
      - {op: rename, from: market_data.price_change_percentage_24h, to: change_24h_pct}
      - {op: rename, from: market_data.ath.usd, to: ath_usd}
      - {op: rename, from: market_data.atl.usd, to: atl_usd}
      - {op: rename, from: market_cap_rank, to: rank}
      - {op: first_of, from: [links.homepage.0, links.homepage.1, links.homepage.2], to: homepage}
    output_renderer: text_template
    output_renderer_args:
      # summary (first two sentences of description.en) is not deterministically
      # extractable — dropped from the render rather than left blank.
      template: |-
        {name} ({symbol}) — rank #{rank}

        Price (USD):     ${price_usd:,.2f}
        Market cap:      ${market_cap_usd:,.0f}
        24h volume:      ${volume_24h_usd:,.0f}
        24h change:      {change_24h_pct:+.2f}%
        All-time high:   ${ath_usd:,.2f}
        All-time low:    ${atl_usd:,.4f}
        Homepage:        {homepage}
  - id: market_chart
    description: Historical price/volume over last N days
    method: GET
    url: https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart
    params:
      coin_id: {required: true, description: "CoinGecko slug"}
      vs_currency: {default: "usd"}
      days: {required: true, description: "Number of days (1, 7, 30, 90, 365, max)"}
    response_shape: ".prices[] → [unix_ms, value]; .market_caps[]; .total_volumes[]"
  - id: trending
    description: Top trending coin searches in the last 24 hours
    method: GET
    url: https://api.coingecko.com/api/v3/search/trending
    params: {}
    output_schema:
      type: object
      additionalProperties: false
      required: [trending]
      properties:
        trending:
          type: array
          description: "From response.coins[].item — typically 7-15 coins"
          items:
            type: object
            additionalProperties: false
            required: [name, symbol, slug, rank]
            properties:
              name: {type: string, description: "From coins[i].item.name"}
              symbol: {type: string, description: "From coins[i].item.symbol (uppercase)"}
              slug: {type: string, description: "From coins[i].item.id (CoinGecko slug)"}
              rank: {type: integer, description: "From coins[i].item.market_cap_rank, or 0 if null"}
    output: schema
    output_renderer: table
    output_renderer_args:
      header: "Trending on CoinGecko (24h)"
      list_key: coins             # raw list is coins[]; each row's fields are under item.* (dotted keys)
      columns:
        - {key: item.market_cap_rank, label: "Rank", max_width: 6}
        - {key: item.symbol, label: "Sym", max_width: 8}
        - {key: item.name, label: "Name", max_width: 30}
        - {key: item.id, label: "Slug", max_width: 24}
  - id: global
    description: Global crypto market summary — total market cap, BTC dominance
    method: GET
    url: https://api.coingecko.com/api/v3/global
    params: {}
    output_schema:
      type: object
      additionalProperties: false
      required: [active_cryptocurrencies, markets, total_market_cap_usd, total_volume_usd_24h, btc_dominance_pct, eth_dominance_pct, market_cap_change_pct_24h]
      properties:
        active_cryptocurrencies: {type: integer, description: "From data.active_cryptocurrencies"}
        markets: {type: integer, description: "From data.markets"}
        total_market_cap_usd: {type: number, description: "From data.total_market_cap.usd"}
        total_volume_usd_24h: {type: number, description: "From data.total_volume.usd"}
        btc_dominance_pct: {type: number, description: "From data.market_cap_percentage.btc"}
        eth_dominance_pct: {type: number, description: "From data.market_cap_percentage.eth"}
        market_cap_change_pct_24h: {type: number, description: "From data.market_cap_change_percentage_24h_usd"}
    output: schema
    output_transforms:
      - {op: lift, from: data}     # everything lives under .data
      - {op: rename, from: total_market_cap.usd, to: total_market_cap_usd}
      - {op: rename, from: total_volume.usd, to: total_volume_usd_24h}
      - {op: rename, from: market_cap_percentage.btc, to: btc_dominance_pct}
      - {op: rename, from: market_cap_percentage.eth, to: eth_dominance_pct}
      - {op: rename, from: market_cap_change_percentage_24h_usd, to: market_cap_change_pct_24h}
    output_renderer: text_template
    output_renderer_args:
      template: |-
        Global crypto market
          total market cap (USD): ${total_market_cap_usd:,.0f}
          24h volume (USD):       ${total_volume_usd_24h:,.0f}
          24h change:             {market_cap_change_pct_24h:+.2f}%
          BTC dominance:          {btc_dominance_pct:.2f}%
          ETH dominance:          {eth_dominance_pct:.2f}%
          active coins:           {active_cryptocurrencies:,}
          markets:                {markets:,}
---

# CoinGecko

Free tier (the "Demo" plan) is open and unauthenticated, 30 calls/min, no signup. Endpoints below all work without a key. (CoinGecko Pro adds higher limits + extra endpoints; not used here.)

## Usage

### Quick price (one or many coins, one or many vs currencies)

```bash
curl -s 'https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum,solana&vs_currencies=usd,eur'
```

`ids` are CoinGecko's slugs (lowercase, kebab-case) — *not* tickers. `bitcoin`, `ethereum`, `solana`, `dogecoin`, `the-graph`, etc. Use `/search` if uncertain.

### Search by ticker or name

```bash
curl -s 'https://api.coingecko.com/api/v3/search?query={QUERY}'
```

Returns matching coins with their canonical `id`.

### Detailed coin info

```bash
curl -s 'https://api.coingecko.com/api/v3/coins/{COIN_ID}'
```

Includes description, links, market data across many currencies, community/developer scores.

### Historical (daily) over last N days

```bash
curl -s 'https://api.coingecko.com/api/v3/coins/{COIN_ID}/market_chart?vs_currency=usd&days=30'
```

Returns parallel arrays for `prices`, `market_caps`, `total_volumes` — each entry `[unix_ms, value]`.

### Trending (top searches in last 24h)

```bash
curl -s 'https://api.coingecko.com/api/v3/search/trending'
```

### Global market summary

```bash
curl -s 'https://api.coingecko.com/api/v3/global'
```

Total market cap, BTC/ETH dominance, active cryptocurrencies count.

## Response shape

Simple JSON. The `simple/price` endpoint returns `{coin_id: {currency: number}}`. Most other endpoints return rich objects — pipe through `jq` for the specific field.

## Notes

- The free demo tier doesn't carry a stable SLA — occasional 429s under load. Backing off a few seconds usually clears them.
- For seconds-level intraday charts use `market_chart/range?from=&to=` with unix timestamps (granularity adjusts based on the window).
- Coin IDs are stable but new tokens get added daily — `/search` is the safe entry point when unsure.
