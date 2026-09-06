---
id: gdelt
name: GDELT 2.0
description: Global news/event database — query worldwide article coverage and trends over time
categories: [news, trends, research]
effect: read-only
trust: subscription
auth_type: none
auth_env_vars: []
actions:
  - id: search_articles
    description: Search global news articles matching a query
    method: GET
    url: https://api.gdeltproject.org/api/v2/doc/doc
    params:
      query: {required: true, description: "Search query — supports phrases, boolean, sourcecountry:XX filters"}
      mode: {const: "ArtList"}
      maxrecords: {default: "25"}
      format: {const: "json"}
      timespan: {default: "24h", description: "Time window: 1h, 4h, 12h, 24h, 3d, 1w, 1month, etc."}
    response_shape: ".articles[] → {url, title, seendate, sourcecountry, language, domain}"
    output_schema:
      type: object
      additionalProperties: false
      required: [articles]
      properties:
        articles:
          type: array
          description: "Up to 15 entries from response.articles[]"
          items:
            type: object
            additionalProperties: false
            required: [title, domain, country, seendate, url]
            properties:
              title: {type: string, description: "From articles[i].title"}
              domain: {type: string, description: "From articles[i].domain"}
              country: {type: string, description: "From articles[i].sourcecountry"}
              seendate: {type: string, description: "From articles[i].seendate (YYYYMMDDhhmmss)"}
              url: {type: string, description: "From articles[i].url"}
    output: schema
    output_transforms:
      - {op: rename, list: articles, from: sourcecountry, to: country}
    output_renderer: message_list
    output_renderer_args:
      header: "GDELT articles"
      list_key: articles
      item_template: "  · {title}\n      {domain} ({country}) · {seendate}\n      {url}"
      empty_message: "(no articles)"
  - id: coverage_volume
    description: Article coverage volume over time (is this story growing or fading?)
    method: GET
    url: https://api.gdeltproject.org/api/v2/doc/doc
    params:
      query: {required: true, description: "Search query"}
      mode: {const: "TimelineVol"}
      format: {const: "json"}
      timespan: {default: "1month"}
    response_shape: ".timeline[] → time-bucketed article counts"
  - id: tone_over_time
    description: Average article sentiment/tone over time (–10 very negative, +10 very positive)
    method: GET
    url: https://api.gdeltproject.org/api/v2/doc/doc
    params:
      query: {required: true, description: "Search query"}
      mode: {const: "TimelineTone"}
      format: {const: "json"}
      timespan: {default: "1month"}
---

# GDELT 2.0

Open database of global news and events, updated every 15 minutes, going back to 2015. No auth, no rate limits documented (but be reasonable). The DOC API matches articles by query; the GKG (Global Knowledge Graph) layer surfaces entities, themes, tones.

## Usage

### Search articles matching a query

```bash
curl -s 'https://api.gdeltproject.org/api/v2/doc/doc?query={QUERY}&mode=ArtList&maxrecords=25&format=json&timespan=24h'
```

`{QUERY}` supports phrases (`"climate accord"`), boolean (`AAPL OR NVDA`), and source filters (`sourcecountry:US`).

### Coverage volume over time (the "trends" use case)

```bash
curl -s 'https://api.gdeltproject.org/api/v2/doc/doc?query={QUERY}&mode=TimelineVol&format=json&timespan=1month'
```

Returns time-bucketed counts — useful for "is this story growing or fading?"

### Tone over time

```bash
curl -s 'https://api.gdeltproject.org/api/v2/doc/doc?query={QUERY}&mode=TimelineTone&format=json&timespan=1month'
```

Average article tone (–10 = very negative, +10 = very positive) bucketed over the timespan.

### Image filter

```bash
curl -s 'https://api.gdeltproject.org/api/v2/doc/doc?query={QUERY}&mode=ImageCollage&format=html&timespan=24h'
```

## Response shape

JSON with `articles[]` for ArtList mode, or `timeline[]` for the various Timeline modes. Each article entry: `url`, `title`, `seendate`, `sourcecountry`, `language`, `domain`.

## Common timespans

- `1h`, `4h`, `12h`, `24h`, `3d`, `1w`, `2w`, `1month`, `3months`, `6months`, `1year`
- For arbitrary windows: `startdatetime=YYYYMMDDhhmmss&enddatetime=...`

## Notes

- GDELT is most useful for breadth-of-coverage questions. For "what is the latest", pair with `google-news` — GDELT lags by ~15min and doesn't cover every blog.
- Results truncate at 250 articles per query; paginate with `startdatetime`/`enddatetime` for deeper sweeps.
