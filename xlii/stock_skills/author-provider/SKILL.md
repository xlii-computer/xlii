---
name: author-provider
description: Author a new data-provider manifest from API documentation — read the docs, write the TOML manifest into .xlii/providers/, test it with /providers run, iterate until real records land. The BYO ("build your own") flow for the provider substrate (typed-workbenches S2).
metadata:
  primitive: "/providers new + /providers run (repo: xlii/providers.py)"
  use-before: "adding any external data source to a project"
---

# Skill: author-provider

A *provider* is data, not code: a TOML manifest the single runner
(`xlii/providers.py`) executes — auth → fetch → shape → store. Writing a
provider means writing the manifest well, never writing Python.

## The method

1. **Read the API's docs first.** You need: the endpoint URL, the query/path
   params, the auth style (header name or query param; whether a key is
   required or optional), the JSON shape (where the record list lives), and
   the rate limit. Do not guess these — a manifest written from memory fails
   the first run.

2. **Scaffold, then fill.** `/providers new <name> <url>` writes a commented
   skeleton into `.xlii/providers/<name>.toml`. Fill in:
   - `summary`, `category`, `required_params` (params the caller MUST give).
   - `[request.params]` — `{name}` placeholders (work in the URL path too).
   - `[auth]` — `key_env` is the env var **name**, never the value. Match the
     provider's real param/header name (`api_key` vs `apikey` vs `api-key`
     vs `token` — providers disagree; check the docs).
   - `[pagination]` — only if the API pages (`kind="page"`, `param`, `max_pages`).
   - `[shape] records` — dotted path to the list (`""` = root).
   - `[shape.fields]` — optional projection; fields the API omits come back
     `null`, so project generously.
   - `[quota] daily` — from the docs' rate limit; the ledger shows "used/limit".

3. **Test against the real API immediately.** `/providers run <name> k=v …`.
   Iterate on the actual error: unfilled placeholder → params wrong; "not
   configured" → `key_env` unset or `required=true` without a key; "not JSON"
   → the endpoint moved or is HTML-gated; empty records → `records` path
   wrong (inspect the raw body — run with no `[shape.fields]` first, add the
   projection after the raw shape is confirmed).

4. **Done = records land.** The run stores `.xlii/provider-results/<name>/latest.json`,
   browsable via addressing. Tell the user what env var to set if the
   provider is keyed — never ask for the key itself, never write it anywhere.

## Guardrails

- GET, or POST with a flat JSON body template (`[request.body]` — `{param}`
  holes, optional holes omitted). Arrays/nested bodies or multi-call
  composition are runner work (flag it), not manifest work.
- Secrets live in the environment; manifests name env vars, never values.
  Auth value prefixes (`Token `, `Bearer `) belong in `auth.prefix`.
- One endpoint per manifest. A family of endpoints is N manifests
  (`<api>-<method>.toml`), the argus-cohort convention.
