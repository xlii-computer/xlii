---
id: google-news
name: Google News (RSS)
description: Topic, region, and search-driven headlines via Google News' public RSS endpoint
categories: [news, headlines]
effect: read-only
trust: subscription
auth_type: none
auth_env_vars: []
actions:
  - id: search
    description: Search Google News headlines by query
    method: GET
    url: https://news.google.com/rss/search
    params:
      q: {required: true, description: "Search query (supports when:7d, site:, quoted phrases)"}
      hl: {default: "en-US", description: "UI language"}
      gl: {default: "US", description: "Country code"}
      ceid: {default: "US:en", description: "Country:language edition"}
    response_shape: "RSS XML → item[] → {title, link, pubDate, source, description}"
    output_schema: &gn_items_schema
      type: object
      additionalProperties: false
      required: [headlines]
      properties:
        headlines:
          type: array
          description: "Up to 15 entries from the RSS <item> elements. Parse each <item> into {title, source, published, link}."
          items:
            type: object
            additionalProperties: false
            required: [title, source, published, link]
            properties:
              title: {type: string, description: "From <item><title> (strip the trailing ' - Source' suffix Google appends)"}
              source: {type: string, description: "From <item><source> text"}
              published: {type: string, description: "From <item><pubDate> (keep the original RFC 2822 string)"}
              link: {type: string, description: "From <item><link> (Google redirect URL — keep as-is)"}
    output: &gn_items_output schema
    body_format: &gn_items_format rss     # endpoint returns RSS XML → parsed to {items:[…]}
    output_renderer: &gn_items_renderer message_list
    output_renderer_args: &gn_items_args
      header: "Google News"
      list_key: items                     # parse_rss emits {items:[{title, link, published, source}]}
      item_template: "  · {title}\n      {source} · {published}\n      {link}"
      empty_message: "(no headlines)"
  - id: top_headlines
    description: Top headlines (no query, general front page)
    method: GET
    url: https://news.google.com/rss
    params:
      hl: {default: "en-US"}
      gl: {default: "US"}
      ceid: {default: "US:en"}
    output_schema: *gn_items_schema
    output: *gn_items_output
    body_format: *gn_items_format
    output_renderer: *gn_items_renderer
    output_renderer_args: *gn_items_args
  - id: topic
    description: Headlines for a specific topic section
    method: GET
    url: https://news.google.com/rss/headlines/section/topic/{topic}
    params:
      topic: {required: true, enum: ["WORLD", "NATION", "BUSINESS", "TECHNOLOGY", "ENTERTAINMENT", "SCIENCE", "SPORTS", "HEALTH"]}
      hl: {default: "en-US"}
      gl: {default: "US"}
      ceid: {default: "US:en"}
    output_schema: *gn_items_schema
    output: *gn_items_output
    body_format: *gn_items_format
    output_renderer: *gn_items_renderer
    output_renderer_args: *gn_items_args
  - id: geo
    description: Headlines for a geographic location
    method: GET
    url: https://news.google.com/rss/headlines/section/geo/{location}
    params:
      location: {required: true, description: "City or region name (e.g. Chicago, London)"}
      hl: {default: "en-US"}
      gl: {default: "US"}
      ceid: {default: "US:en"}
    output_schema: *gn_items_schema
    output: *gn_items_output
    body_format: *gn_items_format
    output_renderer: *gn_items_renderer
    output_renderer_args: *gn_items_args
---

# Google News (RSS)

The Google News site exposes RSS feeds for every search/topic/region combination. No auth, no key, no published rate limit (be reasonable). Returns RSS XML — pipe through `xmllint` or a Python one-liner to parse.

## Usage

### Search by query

```bash
curl -s 'https://news.google.com/rss/search?q={QUERY}&hl=en-US&gl=US&ceid=US:en'
```

`{QUERY}` URL-encoded. Supports operators (`when:7d` last week, `site:bbc.com`, quoted phrases).

### Top headlines (no query)

```bash
curl -s 'https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en'
```

### Section-based (built-in topics)

```bash
curl -s 'https://news.google.com/rss/headlines/section/topic/{TOPIC}?hl=en-US&gl=US&ceid=US:en'
```

`{TOPIC}` ∈ `WORLD`, `NATION`, `BUSINESS`, `TECHNOLOGY`, `ENTERTAINMENT`, `SCIENCE`, `SPORTS`, `HEALTH`.

### Geographic location

```bash
curl -s 'https://news.google.com/rss/headlines/section/geo/{LOCATION}?hl=en-US&gl=US&ceid=US:en'
```

## Locale parameters

- `hl` — UI language (`en-US`, `en-GB`, `ja`, `de`, `fr`, …)
- `gl` — country code (`US`, `GB`, `JP`, `DE`, …)
- `ceid` — combined country/edition (`US:en`, `JP:ja`, `GB:en`)

## Response shape

RSS 2.0 XML. Each `<item>` has `<title>`, `<link>`, `<pubDate>`, `<source>`, `<description>` (HTML snippet).

Quick parse with Python:

```bash
curl -s '...' | python3 -c "import sys, xml.etree.ElementTree as ET; \
  tree = ET.fromstring(sys.stdin.read()); \
  [print(item.findtext('title'), '|', item.findtext('link')) for item in tree.iter('item')]"
```

## Notes

- `link` is a Google redirect URL, not the publisher direct link. Follow it (or extract the `url=` query param) for the canonical source.
- Some queries return zero items — Google's editorial filter is opaque. Try broader phrasing if a search comes back empty.
