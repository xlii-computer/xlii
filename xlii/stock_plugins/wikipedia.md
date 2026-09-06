---
id: wikipedia
name: Wikipedia
description: Article search and summary lookup via the Wikipedia REST and Action APIs
categories: [reference, facts]
effect: read-only
trust: subscription
auth_type: none
auth_env_vars: []
actions:
  - id: title_search
    description: Autocomplete-style search for Wikipedia article titles
    method: GET
    url: https://en.wikipedia.org/w/api.php
    params:
      action: {const: "opensearch"}
      search: {required: true, description: "Search query"}
      limit: {default: "10"}
      format: {const: "json"}
    response_shape: "[query, [titles], [descriptions], [urls]]"
    output_schema:
      type: object
      additionalProperties: false
      required: [matches]
      properties:
        matches:
          type: array
          description: "Zip the parallel arrays at response[1], response[2], response[3] into objects."
          items:
            type: object
            additionalProperties: false
            required: [title, description, url]
            properties:
              title: {type: string, description: "From response[1][i]"}
              description: {type: string, description: "From response[2][i] (may be empty)"}
              url: {type: string, description: "From response[3][i]"}
    output: schema
    output_transforms:
      - op: zip                 # opensearch returns a top-level [q, [titles], [descs], [urls]]
        from: ["1", "2", "3"]
        names: [title, description, url]
        into: matches
    output_renderer: message_list
    output_renderer_args:
      header: "Wikipedia title matches"
      list_key: matches
      item_template: "  · {title} — {url}"
      empty_message: "(no matches)"
  - id: fulltext_search
    description: Full-text search across Wikipedia articles
    method: GET
    url: https://en.wikipedia.org/w/api.php
    params:
      action: {const: "query"}
      list: {const: "search"}
      srsearch: {required: true, description: "Search query"}
      format: {const: "json"}
      srlimit: {default: "10"}
    response_shape: ".query.search[] → {title, snippet, timestamp, wordcount}"
    output_schema:
      type: object
      additionalProperties: false
      required: [results]
      properties:
        results:
          type: array
          description: "Up to 10 entries from .query.search[]"
          items:
            type: object
            additionalProperties: false
            required: [title, snippet, wordcount]
            properties:
              title: {type: string, description: "From query.search[i].title"}
              snippet: {type: string, description: "From query.search[i].snippet — strip HTML span/match tags"}
              wordcount: {type: integer, description: "From query.search[i].wordcount"}
    output: schema
    output_renderer: message_list
    output_renderer_args:
      header: "Wikipedia full-text matches"
      list_key: query.search      # raw list lives at .query.search[] (dotted list_key, no transform)
      item_template: "  · {title} ({wordcount} words)\n      {snippet}"
      empty_message: "(no matches)"
  - id: page_summary
    description: Clean 3–4 sentence summary of a Wikipedia article
    method: GET
    url: https://en.wikipedia.org/api/rest_v1/page/summary/{title}
    params:
      title: {required: true, description: "Article title (underscores OK, e.g. Albert_Einstein)"}
    response_shape: "{title, extract, description, thumbnail}"
    output_schema:
      type: object
      additionalProperties: false
      required: [title, description, extract]
      properties:
        title: {type: string, description: "From response.title"}
        description: {type: string, description: "From response.description (short tagline)"}
        extract: {type: string, description: "From response.extract (3-4 sentence summary)"}
    output: schema     # raw REST body is already flat {title, description, extract} — no transform needed
    output_renderer: text_template
    output_renderer_args:
      template: |-
        {title}
        {description}

        {extract}
  - id: page_intro
    description: Plain-text intro section of a Wikipedia article (longer than summary)
    method: GET
    url: https://en.wikipedia.org/w/api.php
    params:
      action: {const: "query"}
      prop: {const: "extracts"}
      exintro: {const: "1"}
      explaintext: {const: "1"}
      titles: {required: true, description: "Article title"}
      format: {const: "json"}
      redirects: {const: "1"}
    response_shape: ".query.pages[id].extract → plain text"
    # Stays interpret: the extract lives under a DYNAMIC page-id key
    # (query.pages.<id>.extract) that no fixed path/transform can address.
    # page_summary is the deterministic summary path; this one needs the model.
    output_schema:
      type: object
      additionalProperties: false
      required: [title, intro]
      properties:
        title: {type: string, description: "From the single page object under query.pages — its title field"}
        intro: {type: string, description: "From the single page object under query.pages — its extract field"}
    output_renderer: text_template
    output_renderer_args:
      template: |-
        {title}

        {intro}
---

# Wikipedia

Two complementary APIs: the modern REST API for clean per-page summaries/HTML, and the legacy Action API for everything else (search, full-text, links, etc.). No auth needed for reads; please send a descriptive `User-Agent` header on any non-trivial volume.

## Usage

### Title search (autocomplete-style)

```bash
curl -s 'https://en.wikipedia.org/w/api.php?action=opensearch&search={QUERY}&limit=10&format=json'
```

Returns `[query, [titles], [descriptions], [urls]]` — quick fuzzy match for "what's the article called".

### Full-text search

```bash
curl -s 'https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch={QUERY}&format=json&srlimit=10'
```

### Page summary (clean, short)

```bash
curl -s 'https://en.wikipedia.org/api/rest_v1/page/summary/{TITLE}'
```

`{TITLE}` URL-encoded; underscores are fine (`Albert_Einstein` or `Albert%20Einstein`). Response includes `extract` (3-4 sentence summary), `description`, `thumbnail`.

### Full HTML

```bash
curl -s 'https://en.wikipedia.org/api/rest_v1/page/html/{TITLE}'
```

### Plain-text intro section only

```bash
curl -s 'https://en.wikipedia.org/w/api.php?action=query&prop=extracts&exintro=1&explaintext=1&titles={TITLE}&format=json&redirects=1'
```

`redirects=1` follows Wikipedia's redirects (e.g. `JFK` → `John_F._Kennedy`).

## Other languages

Replace `en.wikipedia.org` with `<lang>.wikipedia.org` (`ja`, `de`, `fr`, `es`, …). Same URL shapes everywhere.

## Response shape

Action API returns `{ query: { ... } }` with the result list under varying keys (`search`, `pages`, etc.). REST API returns one flat object per page.

## Notes

- Best for `/get` use case: a quick summary the agent uses to ground a follow-up answer. Pair with `web_search` when Wikipedia stops short.
- For programmatic mass scraping, prefer Wikipedia's bulk dumps over the API.
