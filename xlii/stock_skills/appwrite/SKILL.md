---
name: appwrite
description: >
  Operate a self-hosted Appwrite instance through the stock `appwrite` plugin
  (`/plugin call`). Use when the user wants health, databases, collections,
  documents, buckets, files, users, functions, or JSON creates on their box —
  not Appwrite Cloud MCP, not a Python SDK.
metadata:
  primitive: "/plugin call appwrite.<action> (stock plugin xlii/stock_plugins/appwrite.md)"
  use-before: "talking to Appwrite, storing files there, or inventing a backend"
---

# Skill: appwrite

Appwrite on **their** instance is one subscribed markdown plugin. You do
not spawn MCP, do not import an Appwrite SDK, and do not hardcode
`cloud.appwrite.io`. Endpoints and headers live in the plugin file —
this skill is routing + the file-upload hole.

## Setup (once)

```
xlii plugin --install-stock
/plugin subscribe appwrite
/plugin call appwrite.set
/plugin call appwrite.health
```

`set` stores origin (`…/v1`), project id, server API key, optional JWT.
Never write those values into markdown or chat. If health fails, stop
and show the body — do not invent a different host.

## Route through `/plugin call`

| Intent | Action |
| --- | --- |
| ping | `health` |
| list databases | `databases` |
| collections in a db | `collections databaseId=` |
| documents | `documents databaseId= collectionId=` |
| buckets / files in a bucket | `buckets` / `files bucketId=` |
| functions | `functions` |
| people the **API key** can see | `users` |
| whoami for a **logged-in user** | `account` (needs `APPWRITE_JWT`) |
| create db / collection / bucket | `create_database` / `create_collection` / `create_bucket` |

Creates default `unique()` for the new id. Do not invent ids unless the
user named one. JSON writes are `plugin_call`. Multipart is not.

## Auth split

Server key (`X-Appwrite-Key`) is for health, lists, creates, `users`.
`GET /account` is a user route — `account` sends `X-Appwrite-JWT` only.
A 401 on `account` with a key is Appwrite, not a broken plugin. Do not
"fix" it by pointing at Cloud.

## Files (until plugin_call grows multipart)

`plugin_call` cannot `POST` multipart, so there is no `create_file`
action. For a file **under 5 MiB**, upload from a vault-backed script
that **never prints the key**:

```
python - <<'PY'
from pathlib import Path
import urllib.request
from xlii.vault import Vault

path = Path("FILE")  # the user's file
bucket = "BUCKET_ID"
creds = Vault.unlock().get("appwrite")
endpoint = creds["APPWRITE_ENDPOINT"].rstrip("/")
boundary = "xliiappwrite"
body = (
    f"--{boundary}\r\n"
    f'Content-Disposition: form-data; name="fileId"\r\n\r\nunique()\r\n'
    f"--{boundary}\r\n"
    f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
    f"Content-Type: application/octet-stream\r\n\r\n"
).encode() + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
req = urllib.request.Request(
    f"{endpoint}/storage/buckets/{bucket}/files",
    data=body,
    method="POST",
    headers={
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "X-Appwrite-Project": creds["APPWRITE_PROJECT"],
        "X-Appwrite-Key": creds["APPWRITE_API_KEY"],
    },
)
with urllib.request.urlopen(req, timeout=60) as resp:
    print(resp.read().decode())
PY
```

Substitute `FILE` and `BUCKET_ID` only. Larger files need Appwrite
chunked `Content-Range` — tell the user to use the console or an SDK;
do not fake chunking here.

Then `/plugin call appwrite.files bucketId=…` to confirm.

## Guardrails

- One plugin, several actions. Do not author a second Appwrite plugin.
- Not a provider (`/providers new`). Not an MCP server.
- Do not paste API keys, JWTs, or raw vault dumps into the transcript.
