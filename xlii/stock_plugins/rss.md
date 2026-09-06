---
id: rss
name: RSS / Atom feeds
description: Fetch any RSS or Atom feed URL and list recent items (title, link, date, source)
categories: [news, research, feeds]
effect: read-only
trust: subscription
auth_type: none
auth_env_vars: []
actions:
  - id: fetch
    description: Fetch a feed by full URL and list recent items
    method: GET
    url: "{feed_url}"
    params:
      feed_url: {required: true, description: "Full RSS or Atom feed URL (https://…)"}
    response_shape: "RSS/Atom → items[] → {title, link, published, source}"
    body_format: rss
    output_schema:
      type: object
      additionalProperties: false
      required: [items]
      properties:
        items:
          type: array
          description: "Feed entries (RSS item or Atom entry)"
          items:
            type: object
            additionalProperties: false
            required: [title, link]
            properties:
              title: {type: string}
              link: {type: string}
              published: {type: string}
              source: {type: string}
    output: schema
    output_renderer: message_list
    output_renderer_args:
      header: "Feed items"
      list_key: items
      item_template: "  · {title}\n      {published} · {link}"
      empty_message: "(empty feed or unparseable XML)"
  - id: headlines
    description: Alias of fetch — same call, header phrased for news-style feeds
    method: GET
    url: "{feed_url}"
    params:
      feed_url: {required: true, description: "Full RSS or Atom feed URL"}
    body_format: rss
    output: schema
    output_renderer: message_list
    output_renderer_args:
      header: "Headlines"
      list_key: items
      item_template: "  · {title}\n      {source} · {published}\n      {link}"
      empty_message: "(no headlines)"
---

# RSS / Atom feeds

Generic feed reader for any public RSS 2.0 or Atom URL. No auth. Use when you have a specific feed (blog, journal TOC, podcast, project changelog) rather than Google News / HN search.

For Google News specifically, prefer the `google-news` stock plugin (locale + topic helpers). For HN, use `hackernews`.

## Usage

### Fetch a feed

```bash
curl -sL '{FEED_URL}'
```

Via plugin_call: `rss.fetch feed_url=https://example.com/feed.xml`

### Common research feeds

- arXiv category Atom: `https://rss.arxiv.org/rss/cs.AI` (or use the `arxiv` plugin)
- Nature / Science journal RSS pages list section feeds
- Project blogs, RFCs, security advisories — whatever publishes a feed

## Response shape

The L2 path parses RSS/Atom into `{items:[{title, link, published, source}]}` (stdlib only). Full content / content:encoded is not extracted — open the `link` (or archive it) for the body.

## Notes

- Feed URL must be a complete `http(s)://…` address. Relative URLs will fail.
- Some sites block bare curl UAs or require HTTPS redirects — if empty, try the browser or ArchiveBox snapshot of the HTML page.
- For a durable research trail, pipe interesting links into `archivebox.add` so they land in your local store with WARC/PDF/screenshot.
- A future spawned-browser loop can open each `link` for agent-side reading; this plugin stays the lightweight list step.
