---
id: courtlistener
name: CourtListener
description: US federal and state court opinions, dockets, judges, and oral arguments (Free Law Project)
categories: [legal, research]
effect: read-only
trust: subscription
auth_type: token
auth_env_vars:
  - COURTLISTENER_TOKEN
actions:
  - id: set
    description: Store the CourtListener API token in the vault
    params:
      COURTLISTENER_TOKEN: {secret: true, store: true, required: true, description: "Token from courtlistener.com"}
    output: raw
  - id: search_opinions
    description: Search US court opinions by query, optionally filtered by court and date
    method: GET
    url: https://www.courtlistener.com/api/rest/v3/search/
    params:
      q: {required: true, description: "Search query"}
      type: {const: "o"}
      court: {description: "Court code (scotus, ca1-ca11, cadc, cafc, nysd, cand, etc.)"}
      filed_after: {description: "Date filter YYYY-MM-DD"}
      filed_before: {description: "Date filter YYYY-MM-DD"}
    headers:
      Authorization: "Token ${COURTLISTENER_TOKEN}"
    response_shape: ".results[] → {id, caseName, court, dateFiled, citation, snippet}"
    output_schema:
      type: object
      additionalProperties: false
      required: [results]
      properties:
        results:
          type: array
          description: "Up to 15 entries from response.results[]"
          items:
            type: object
            additionalProperties: false
            required: [id, case_name, court, date_filed, citation, snippet]
            properties:
              id: {type: integer, description: "From results[i].id (use for /get opinion <id>)"}
              case_name: {type: string, description: "From results[i].caseName"}
              court: {type: string, description: "From results[i].court"}
              date_filed: {type: string, description: "From results[i].dateFiled"}
              citation: {type: string, description: "From results[i].citation, or empty if missing"}
              snippet: {type: string, description: "From results[i].snippet — strip HTML tags"}
    output: schema
    output_transforms:
      - {op: rename, list: results, from: caseName, to: case_name}
      - {op: rename, list: results, from: dateFiled, to: date_filed}
    output_renderer: message_list
    output_renderer_args:
      header: "Court opinions"
      list_key: results
      item_template: "  · {case_name}\n      {court} · filed {date_filed} · cite: {citation}\n      id={id}\n      {snippet}"
      empty_message: "(no opinions matched)"
  - id: get_opinion
    description: Get full text of a specific court opinion by ID
    method: GET
    url: https://www.courtlistener.com/api/rest/v3/opinions/{opinion_id}/
    params:
      opinion_id: {required: true, description: "Opinion ID from search results"}
    response_shape: "{id, plain_text, html, date_created, cluster}"
  - id: search_dockets
    description: Search case dockets (case-level metadata)
    method: GET
    url: https://www.courtlistener.com/api/rest/v3/search/
    headers:
      Authorization: "Token ${COURTLISTENER_TOKEN}"
    params:
      q: {required: true, description: "Search query"}
      type: {const: "r"}
    output_schema:
      type: object
      additionalProperties: false
      required: [dockets]
      properties:
        dockets:
          type: array
          description: "Up to 15 entries from response.results[]"
          items:
            type: object
            additionalProperties: false
            required: [id, case_name, court, date_filed]
            properties:
              id: {type: integer, description: "From results[i].id"}
              case_name: {type: string, description: "From results[i].caseName"}
              court: {type: string, description: "From results[i].court"}
              date_filed: {type: string, description: "From results[i].dateFiled, or empty"}
    output: schema
    output_transforms:
      - {op: rename, list: results, from: caseName, to: case_name}
      - {op: rename, list: results, from: dateFiled, to: date_filed}
    output_renderer: message_list
    output_renderer_args:
      header: "Case dockets"
      list_key: results     # search API returns .results[] for every type
      item_template: "  · {case_name}\n      {court} · filed {date_filed} · id={id}"
      empty_message: "(no dockets matched)"
  - id: search_judges
    description: Look up a judge by name
    method: GET
    url: https://www.courtlistener.com/api/rest/v3/people/
    headers:
      Authorization: "Token ${COURTLISTENER_TOKEN}"
    params:
      name_first: {description: "First name"}
      name_last: {description: "Last name"}
    output_schema:
      type: object
      additionalProperties: false
      required: [judges]
      properties:
        judges:
          type: array
          description: "Up to 15 entries from response.results[]"
          items:
            type: object
            additionalProperties: false
            required: [id, name, gender, dob, dod]
            properties:
              id: {type: integer, description: "From results[i].id"}
              name: {type: string, description: "Concatenate name_first, name_middle (if present), name_last"}
              gender: {type: string, description: "From results[i].gender, or empty"}
              dob: {type: string, description: "From results[i].date_dob, or empty"}
              dod: {type: string, description: "From results[i].date_dod, or empty"}
    output: schema
    output_transforms:
      - {op: concat, list: results, from: [name_first, name_middle, name_last], to: name, sep: " "}
      - {op: rename, list: results, from: date_dob, to: dob}
      - {op: rename, list: results, from: date_dod, to: dod}
    output_renderer: message_list
    output_renderer_args:
      header: "Judges"
      list_key: results     # people API returns .results[]
      item_template: "  · {name}  ·  id={id}\n      gender: {gender}  ·  dob: {dob}  ·  dod: {dod}"
      empty_message: "(no judges matched)"
---

# CourtListener

Free Law Project's open database of US case law, dockets, judges, and oral argument audio. Reads work without a token at lower rate limits; a free account token raises limits substantially. Get one at https://www.courtlistener.com/sign-in/ → profile → API tokens.

## Auth (optional but recommended)

Run `courtlistener.set`. Sent as `Authorization: Token …` (not Bearer).

## Usage

### Search opinions

```bash
curl -s -H "Authorization: Token ${COURTLISTENER_TOKEN}" \
  'https://www.courtlistener.com/api/rest/v3/search/?q={QUERY}&type=o'
```

`type=o` for opinions. Other types: `r` (RECAP/PACER docket entries), `oa` (oral arguments), `p` (people/judges).

### Filter by court / date

```bash
curl -s -H "Authorization: Token ${COURTLISTENER_TOKEN}" \
  'https://www.courtlistener.com/api/rest/v3/search/?q={QUERY}&type=o&court=scotus&filed_after=2024-01-01'
```

Common court codes: `scotus`, `ca1`–`ca11`, `cadc`, `cafc`, `nysd`, `cand`, `txnd`. Full list at /api/rest/v3/courts/.

### Get one opinion in full

```bash
curl -s 'https://www.courtlistener.com/api/rest/v3/opinions/{OPINION_ID}/'
```

### Search dockets (case-level metadata)

```bash
curl -s -H "Authorization: Token ${COURTLISTENER_TOKEN}" \
  'https://www.courtlistener.com/api/rest/v3/search/?q={QUERY}&type=r'
```

### Look up a judge

```bash
curl -s 'https://www.courtlistener.com/api/rest/v3/people/?name_first={FIRST}&name_last={LAST}'
```

## Response shape

JSON with `count`, `next`, `previous`, `results[]`. Each opinion result: `id`, `caseName`, `court`, `dateFiled`, `citation` (Bluebook), `snippet` (matched excerpt).

## Notes

- The full opinion text isn't in search hits — fetch `/opinions/{id}/` to get `plain_text`.
- Without a token: rate-limited to ~5000 calls/day per IP. With a token: 5000/hour.
- For PACER/RECAP docket *contents* (not just metadata), some endpoints require additional permissions — see the docs.
