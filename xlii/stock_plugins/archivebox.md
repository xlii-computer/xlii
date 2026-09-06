---
id: archivebox
name: ArchiveBox
description: Self-hosted web archive — snapshot URLs, search your archive, retrieve metadata (research store)
categories: [research, archive, self-hosted]
effect: external-write
trust: subscription
auth_type: token
auth_env_vars:
  - ARCHIVEBOX_API_TOKEN
  - ARCHIVEBOX_BASE
actions:
  - id: set
    description: Store the ArchiveBox URL and API token in the vault
    params:
      ARCHIVEBOX_BASE: {store: true, required: true, description: "Base URL (e.g. https://archive.example:8000)"}
      ARCHIVEBOX_API_TOKEN: {secret: true, store: true, required: true, description: "ArchiveBox API token"}
    output: raw
  - id: list_snapshots
    description: List recent snapshots (paginated)
    method: GET
    url: ${ARCHIVEBOX_BASE}/api/v1/core/snapshots
    params:
      limit: {default: "20", description: "Items per page (max 500)"}
      offset: {default: "0"}
      page: {description: "Page number alternative to offset"}
    headers:
      Authorization: "Bearer ${ARCHIVEBOX_API_TOKEN}"
    response_shape: "{total_items, items[] → {id, url, title, status, timestamp, tags}}"
    output_schema:
      type: object
      additionalProperties: false
      required: [items]
      properties:
        total_items: {type: integer}
        items:
          type: array
          description: "Up to limit snapshots from response.items[]"
          items:
            type: object
            additionalProperties: false
            required: [id, url, status]
            properties:
              id: {type: string, description: "Snapshot UUID"}
              url: {type: string}
              title: {type: string}
              status: {type: string, description: "queued|started|succeeded|failed|sealed"}
              timestamp: {type: string}
              tags: {type: array, items: {type: string}}
    output: schema
    output_renderer: message_list
    output_renderer_args:
      header: "ArchiveBox snapshots"
      list_key: items
      item_template: "  · {title}\n      {url}\n      id={id} · {timestamp}"
      empty_message: "(no snapshots — is ARCHIVEBOX_BASE reachable?)"
  - id: search
    description: Search snapshots by URL, title, tags, or free text
    method: GET
    url: ${ARCHIVEBOX_BASE}/api/v1/core/snapshots
    params:
      search: {required: true, description: "Search URL, title, tags, id, or timestamp"}
      limit: {default: "20"}
      tag: {description: "Optional exact tag filter"}
    headers:
      Authorization: "Bearer ${ARCHIVEBOX_API_TOKEN}"
    response_shape: "{items[] → snapshots matching search}"
    output: schema
    output_renderer: message_list
    output_renderer_args:
      header: "ArchiveBox search"
      list_key: items
      item_template: "  · {title}\n      {url}\n      id={id}"
      empty_message: "(no matching snapshots)"
  - id: get_snapshot
    description: Get one snapshot by UUID (or prefix) / timestamp
    method: GET
    url: ${ARCHIVEBOX_BASE}/api/v1/core/snapshot/{snapshot_id}
    params:
      snapshot_id: {required: true, description: "Snapshot UUID, UUID prefix, or timestamp id"}
      with_archiveresults: {default: "true", description: "Include PDF/screenshot/WARC result list"}
    headers:
      Authorization: "Bearer ${ARCHIVEBOX_API_TOKEN}"
    response_shape: "{id, url, title, status, archive_path, archiveresults[]}"
  - id: add
    description: Queue one or more URLs to archive (write — creates snapshots on your server)
    method: POST
    url: ${ARCHIVEBOX_BASE}/api/v1/cli/add
    params:
      urls: {required: true, description: "JSON array of URL strings, e.g. [\"https://example.com\"]"}
      tag: {default: "", description: "Optional tag string applied to new snapshots"}
      depth: {default: "0", description: "0 = just these URLs; 1 = also one hop of outlinks"}
      parser: {default: "auto"}
      update: {default: "false", description: "true = re-archive if URL already exists"}
      overwrite: {default: "false"}
      index_only: {default: "false", description: "true = index URL only (fast); false = full extractors (slow, may hit client timeout)"}
    headers:
      Authorization: "Bearer ${ARCHIVEBOX_API_TOKEN}"
      Content-Type: "application/json"
    response_shape: "{success, result, stdout, stderr, errors}"
    output_schema:
      type: object
      additionalProperties: false
      required: [success]
      properties:
        success: {type: boolean}
        result: {description: "CLI result payload (varies)"}
        stdout: {type: string}
        stderr: {type: string}
        errors: {type: array, items: {type: string}}
    output: schema
    output_renderer: text_template
    output_renderer_args:
      template: |-
        ArchiveBox add — success={success}
        {stdout}
  - id: list_tags
    description: List tags in the archive
    method: GET
    url: ${ARCHIVEBOX_BASE}/api/v1/core/tags
    params:
      limit: {default: "100"}
    headers:
      Authorization: "Bearer ${ARCHIVEBOX_API_TOKEN}"
    response_shape: "{items[] → {id, name, slug, …}}"
---

# ArchiveBox

Self-hosted web archive ([archivebox.io](https://archivebox.io)). Your durable research store: WARC, PDF, screenshot, DOM, title, tags — all local. The REST API is **alpha** (django-ninja); endpoints live under `/api/v1/`.

This plugin is the **API side** of the research loop. A spawned browser is the **read/analysis side** — open a live page or an already-archived snapshot, let the agent inspect, then `add` anything worth keeping.

## Auth setup

1. Run ArchiveBox and create a **superuser** (API requires `is_superuser`).
2. Get an API token:
   - Admin UI → API → API Keys, or
   - `POST /api/v1/auth/get_api_token` with username/password
3. Run `archivebox.set` — base URL (plain) + API token (secret).

No trailing slash on `ARCHIVEBOX_BASE`. Docker setups often use `http://127.0.0.1:8000` or a reverse-proxy host.

Auth header: `Authorization: Bearer <token>` (also accepted: `X-ArchiveBox-API-Key`).

## Usage

### List / search snapshots

```bash
curl -s "${ARCHIVEBOX_BASE}/api/v1/core/snapshots?limit=20" \
  -H "Authorization: Bearer ${ARCHIVEBOX_API_TOKEN}"

curl -s "${ARCHIVEBOX_BASE}/api/v1/core/snapshots?search=arxiv.org&limit=20" \
  -H "Authorization: Bearer ${ARCHIVEBOX_API_TOKEN}"
```

### One snapshot (+ archive results)

```bash
curl -s "${ARCHIVEBOX_BASE}/api/v1/core/snapshot/{SNAPSHOT_ID}?with_archiveresults=true" \
  -H "Authorization: Bearer ${ARCHIVEBOX_API_TOKEN}"
```

### Add URLs (archive)

```bash
curl -s -X POST "${ARCHIVEBOX_BASE}/api/v1/cli/add" \
  -H "Authorization: Bearer ${ARCHIVEBOX_API_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"urls":["https://arxiv.org/abs/1706.03762"],"tag":"research","depth":0}'
```

`urls` must be a JSON array. On this server (`archivebox add` via `/api/v1/cli/add`) the
HTTP call often **blocks until extractors finish** — can exceed the default 30s plugin
timeout for heavy pages. Prefer small pages, set `index_only=true` for a fast index
row, or re-check with `list_snapshots` / `search` after a longer wait. Background
workers may still finish after a client timeout.

## Research assistant pattern

1. Discover: `arxiv.search`, `rss.fetch`, `hackernews.search`, `gdelt`, …
2. Park: `archivebox.add` with a tag (`project-x`, `lit-review`)
3. Retrieve: `archivebox.search` / `get_snapshot` for offline metadata + archive_path
4. Analyze (next substrate): spawn browser → open abs URL or local snapshot → agent reads / notes → optional KG / canvas write

Same spawned-browser door later serves PDF reading, wiki/KG inspection, and canvas — ArchiveBox is the **store**, not the viewer.

## Response shape

List endpoints return pagination wrappers:

```json
{"total_items": N, "page": 0, "limit": 20, "items": [/* Snapshot */]}
```

Snapshot fields of interest: `id`, `url`, `title`, `status`, `timestamp`, `tags`, `archive_path`, `num_archiveresults`.

## Notes

- API is alpha — fields may move between ArchiveBox releases.
- Only superuser tokens work; 403 usually means non-superuser or expired token (default ~30 days).
- `add` is a write against *your* server (`external-write`). Don't point `ARCHIVEBOX_BASE` at someone else's instance.
- For public Internet Archive / Wayback, this plugin is the wrong tool — use those APIs separately.
