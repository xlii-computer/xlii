---
id: nyt
name: New York Times
description: Article Search API — headlines, abstracts, and permalinks (keyed, ~10 req/min)
categories: [news, intel]
effect: read-only
trust: subscription
auth_type: query_param
auth_env_vars:
  - NYT_API_KEY
actions:
  - id: set
    description: Store the NYT Article Search API key in the vault
    params:
      NYT_API_KEY: {secret: true, store: true, required: true, description: "Key from developer.nytimes.com"}
    output: raw
  - id: search
    description: Search NYT articles by keyword, newest first. Optional begin_date / end_date as YYYYMMDD.
    method: GET
    url: https://api.nytimes.com/svc/search/v2/articlesearch.json
    params:
      q: {required: true, description: "Search keywords"}
      sort: {const: "newest"}
      begin_date: {description: "Start YYYYMMDD (no dashes)"}
      end_date: {description: "End YYYYMMDD (no dashes)"}
      api-key: {const: "${NYT_API_KEY}"}
    response_shape: ".response.docs[] → {headline.main, abstract, byline.original, pub_date, web_url, section_name}"
    output_schema:
      type: object
      additionalProperties: false
      required: [docs]
      properties:
        docs:
          type: array
          description: "Up to 10 entries from response.docs[]"
          items:
            type: object
            additionalProperties: false
            required: [title, url, published]
            properties:
              title: {type: string, description: "From docs[i].headline.main"}
              abstract: {type: string, description: "From docs[i].abstract, else snippet"}
              byline: {type: string, description: "From docs[i].byline.original"}
              section: {type: string, description: "From docs[i].section_name"}
              published: {type: string, description: "From docs[i].pub_date"}
              url: {type: string, description: "From docs[i].web_url"}
    output: schema
    output_transforms:
      - {op: lift, from: response}
      - {op: rename, list: docs, from: [headline, main], to: title}
      - {op: first_of, list: docs, to: abstract, from: [abstract, snippet]}
      - {op: rename, list: docs, from: [byline, original], to: byline}
      - {op: rename, list: docs, from: section_name, to: section}
      - {op: rename, list: docs, from: pub_date, to: published}
      - {op: rename, list: docs, from: web_url, to: url}
    output_renderer: message_list
    output_renderer_args:
      header: "NY Times"
      list_key: docs
      item_template: "  · {title}\n      {section} · {published}\n      {byline}\n      {url}"
      empty_message: "(no articles)"
---

# New York Times

Article Search only. Get a key at [developer.nytimes.com](https://developer.nytimes.com).
Free tier is about 10 requests/minute.

```
/plugin subscribe nyt
/plugin call nyt.set
/plugin call nyt.search q=federal reserve
/plugin call nyt.search q=boeing begin_date=20260101
```

Dates are `YYYYMMDD` with no dashes — that is the API, not a typo.

## Next

- Top Stories / Most Popular are other NYT products (different keys).
- Images are on `static01.nyt.com` when `multimedia[0].url` is present; this
  action does not fetch them.
