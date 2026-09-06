---
id: arxiv
name: arXiv
description: Search and fetch arXiv preprints (Atom API) — papers, abstracts, PDF links
categories: [research, academic, papers]
effect: read-only
trust: subscription
auth_type: none
auth_env_vars: []
actions:
  - id: search
    description: Search arXiv papers by query (title, abstract, author, category, …)
    method: GET
    url: https://export.arxiv.org/api/query
    params:
      search_query: {required: true, description: "arXiv query, e.g. all:transformer, ti:attention, au:Hinton, cat:cs.AI, or combined with AND/OR"}
      start: {default: "0", description: "Result offset (0-based)"}
      max_results: {default: "10", description: "How many papers to return (1–50; keep small)"}
      sortBy: {default: "relevance", description: "relevance | lastUpdatedDate | submittedDate", enum: [relevance, lastUpdatedDate, submittedDate]}
      sortOrder: {default: "descending", description: "ascending | descending", enum: [ascending, descending]}
    response_shape: "Atom feed → entry[] → {title, id, summary, author, published, link[pdf]}"
    body_format: rss
    output_schema:
      type: object
      additionalProperties: false
      required: [items]
      properties:
        items:
          type: array
          description: "Up to max_results entries from the Atom feed"
          items:
            type: object
            additionalProperties: false
            required: [title, link, published]
            properties:
              title: {type: string, description: "Paper title"}
              link: {type: string, description: "abs page URL (https://arxiv.org/abs/…)"}
              published: {type: string, description: "ISO published/updated date"}
    output: schema
    output_renderer: message_list
    output_renderer_args:
      header: "arXiv papers"
      list_key: items
      item_template: "  · {title}\n      {published}\n      {link}"
      empty_message: "(no papers matched)"
  - id: get
    description: Fetch one or more papers by arXiv id (e.g. 1706.03762 or 1706.03762v7)
    method: GET
    url: https://export.arxiv.org/api/query
    params:
      id_list: {required: true, description: "Comma-separated arXiv ids without version, or with vN (e.g. 1706.03762,2103.00020v1)"}
      max_results: {default: "10"}
    response_shape: "Atom feed → entry for each id"
    body_format: rss
    output: schema
    output_renderer: message_list
    output_renderer_args:
      header: "arXiv paper(s)"
      list_key: items
      item_template: "  · {title}\n      {published}\n      {link}"
      empty_message: "(no papers for those ids)"
  - id: recent_category
    description: Recent papers in an arXiv category (e.g. cs.AI, cs.LG, quant-ph, hep-th)
    method: GET
    url: https://export.arxiv.org/api/query
    params:
      search_query: {required: true, description: "Use cat:PREFIX form, e.g. cat:cs.AI or cat:cs.LG"}
      sortBy: {const: "submittedDate"}
      sortOrder: {const: "descending"}
      start: {default: "0"}
      max_results: {default: "15"}
    body_format: rss
    output: schema
    output_renderer: message_list
    output_renderer_args:
      header: "arXiv recent"
      list_key: items
      item_template: "  · {title}\n      {published}\n      {link}"
      empty_message: "(no recent papers)"
---

# arXiv

Official arXiv Atom API. No auth, free for research use. Be polite: a few requests per second max; prefer batching ids with `get` over hammering `search`.

## Usage

### Search

```bash
curl -sG 'https://export.arxiv.org/api/query' \
  --data-urlencode 'search_query=all:attention is all you need' \
  --data-urlencode 'start=0' \
  --data-urlencode 'max_results=10' \
  --data-urlencode 'sortBy=relevance'
```

### By id (abs page + PDF)

```bash
curl -sG 'https://export.arxiv.org/api/query' \
  --data-urlencode 'id_list=1706.03762'
```

PDF for a known id: `https://arxiv.org/pdf/{ID}.pdf` (no version → latest).

### Recent in a category

```bash
curl -sG 'https://export.arxiv.org/api/query' \
  --data-urlencode 'search_query=cat:cs.AI' \
  --data-urlencode 'sortBy=submittedDate' \
  --data-urlencode 'sortOrder=descending' \
  --data-urlencode 'max_results=15'
```

## Query language (search_query)

| Prefix | Meaning |
|--------|---------|
| `all:` | title + abstract + authors + comments + journal + category |
| `ti:`  | title |
| `abs:` | abstract |
| `au:`  | author |
| `cat:` | category (cs.AI, cs.LG, quant-ph, …) |
| `id:`  | arXiv id |

Combine with `AND` / `OR` / `ANDNOT` and parentheses:

```
ti:transformer AND cat:cs.LG
au:Hinton ANDNOT ti:review
```

## Response shape

Atom XML feed. Each `<entry>` has title, id (`http://arxiv.org/abs/…`), published/updated, summary (abstract), author names, and link rels (abs HTML + pdf).

The L2 renderer lists title / date / abs link. For full abstracts, use the raw Atom body (interpret) or open the abs URL / PDF.

## Notes

- Rate limit: arXiv asks for ≤1 request / 3 seconds for automated clients. Burst gently.
- Ids look like `YYMM.NNNNN` (new) or `arch-ive/YYMMNNN` (legacy). Version suffix `vN` is optional.
- Pair with the `archivebox` plugin to snapshot abs/PDF pages into your local research store.
- Pair with a spawned browser later to read the PDF/HTML in-session for agent analysis.
