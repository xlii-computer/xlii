---
id: appwrite
name: Appwrite
description: Self-hosted Appwrite REST — auth, databases, storage, functions
categories: [backend, self-host]
effect: external-write
trust: subscription
auth_type: header
auth_env_vars:
  - APPWRITE_ENDPOINT
  - APPWRITE_API_KEY
  - APPWRITE_PROJECT
  - APPWRITE_JWT
actions:
  - id: set
    description: Store the self-hosted Appwrite origin, project id, API key, and optional user JWT
    params:
      APPWRITE_ENDPOINT: {store: true, required: true, description: "Instance API origin including /v1, no trailing slash (SDK setEndpoint). e.g. https://appwrite.example.com/v1"}
      APPWRITE_PROJECT: {store: true, required: true, description: "Project id — sent as X-Appwrite-Project"}
      APPWRITE_API_KEY: {secret: true, store: true, required: true, description: "Server API key — sent as X-Appwrite-Key"}
      APPWRITE_JWT: {secret: true, store: true, description: "Optional user JWT for account whoami (X-Appwrite-JWT). Server keys cannot call GET /account."}
    output: raw
  - id: health
    description: Ping the instance HTTP health check (headers work)
    method: GET
    url: ${APPWRITE_ENDPOINT}/health
    headers:
      Content-Type: "application/json"
      X-Appwrite-Project: "${APPWRITE_PROJECT}"
      X-Appwrite-Key: "${APPWRITE_API_KEY}"
    response_shape: "{status, ping}"
    output_schema:
      type: object
      additionalProperties: false
      required: [status]
      properties:
        status: {type: string, description: "From status (pass|fail)"}
        ping: {type: integer, description: "From ping (microseconds)"}
    output: schema
    output_renderer: text_template
    output_renderer_args:
      template: |-
        Appwrite health: {status}
          ping: {ping}
  - id: databases
    description: List databases in the current project
    method: GET
    url: ${APPWRITE_ENDPOINT}/databases
    headers:
      Content-Type: "application/json"
      X-Appwrite-Project: "${APPWRITE_PROJECT}"
      X-Appwrite-Key: "${APPWRITE_API_KEY}"
    response_shape: "{total, databases[] → {$id, name, enabled}}"
    output_schema:
      type: object
      additionalProperties: false
      required: [databases]
      properties:
        total: {type: integer}
        databases:
          type: array
          description: "From response.databases[]"
          items:
            type: object
            additionalProperties: false
            required: [id, name]
            properties:
              id: {type: string, description: "From databases[i].$id"}
              name: {type: string, description: "From databases[i].name"}
              enabled: {type: boolean, description: "From databases[i].enabled"}
    output: schema
    output_transforms:
      - {op: rename, list: databases, from: "$id", to: id}
    output_renderer: table
    output_renderer_args:
      header: "Appwrite databases"
      list_key: databases
      columns:
        - {key: id, label: "Id", max_width: 36}
        - {key: name, label: "Name", max_width: 32}
        - {key: enabled, label: "On", max_width: 6}
      empty_message: "(no databases)"
  - id: collections
    description: List collections in one database
    method: GET
    url: ${APPWRITE_ENDPOINT}/databases/{databaseId}/collections
    params:
      databaseId: {required: true, description: "Database id from appwrite.databases"}
    headers:
      Content-Type: "application/json"
      X-Appwrite-Project: "${APPWRITE_PROJECT}"
      X-Appwrite-Key: "${APPWRITE_API_KEY}"
    response_shape: "{total, collections[] → {$id, name, enabled}}"
    output_schema:
      type: object
      additionalProperties: false
      required: [collections]
      properties:
        total: {type: integer}
        collections:
          type: array
          description: "From response.collections[]"
          items:
            type: object
            additionalProperties: false
            required: [id, name]
            properties:
              id: {type: string, description: "From collections[i].$id"}
              name: {type: string, description: "From collections[i].name"}
              enabled: {type: boolean, description: "From collections[i].enabled"}
    output: schema
    output_transforms:
      - {op: rename, list: collections, from: "$id", to: id}
    output_renderer: table
    output_renderer_args:
      header: "Appwrite collections"
      list_key: collections
      columns:
        - {key: id, label: "Id", max_width: 36}
        - {key: name, label: "Name", max_width: 32}
        - {key: enabled, label: "On", max_width: 6}
      empty_message: "(no collections)"
  - id: documents
    description: List documents in one collection
    method: GET
    url: ${APPWRITE_ENDPOINT}/databases/{databaseId}/collections/{collectionId}/documents
    params:
      databaseId: {required: true, description: "Database id"}
      collectionId: {required: true, description: "Collection id"}
    headers:
      Content-Type: "application/json"
      X-Appwrite-Project: "${APPWRITE_PROJECT}"
      X-Appwrite-Key: "${APPWRITE_API_KEY}"
    response_shape: "{total, documents[] → {$id, …attributes}}"
    output_schema:
      type: object
      additionalProperties: false
      required: [documents]
      properties:
        total: {type: integer}
        documents:
          type: array
          description: "From response.documents[]"
          items:
            type: object
            additionalProperties: true
            required: [id]
            properties:
              id: {type: string, description: "From documents[i].$id"}
    output: schema
    output_transforms:
      - {op: rename, list: documents, from: "$id", to: id}
    output_renderer: table
    output_renderer_args:
      header: "Appwrite documents"
      list_key: documents
      columns:
        - {key: id, label: "Id", max_width: 36}
      empty_message: "(no documents)"
  - id: buckets
    description: List storage buckets in the current project
    method: GET
    url: ${APPWRITE_ENDPOINT}/storage/buckets
    headers:
      Content-Type: "application/json"
      X-Appwrite-Project: "${APPWRITE_PROJECT}"
      X-Appwrite-Key: "${APPWRITE_API_KEY}"
    response_shape: "{total, buckets[] → {$id, name, enabled}}"
    output_schema:
      type: object
      additionalProperties: false
      required: [buckets]
      properties:
        total: {type: integer}
        buckets:
          type: array
          description: "From response.buckets[]"
          items:
            type: object
            additionalProperties: false
            required: [id, name]
            properties:
              id: {type: string, description: "From buckets[i].$id"}
              name: {type: string, description: "From buckets[i].name"}
              enabled: {type: boolean, description: "From buckets[i].enabled"}
    output: schema
    output_transforms:
      - {op: rename, list: buckets, from: "$id", to: id}
    output_renderer: table
    output_renderer_args:
      header: "Appwrite buckets"
      list_key: buckets
      columns:
        - {key: id, label: "Id", max_width: 36}
        - {key: name, label: "Name", max_width: 32}
        - {key: enabled, label: "On", max_width: 6}
      empty_message: "(no buckets)"
  - id: files
    description: List files in one storage bucket
    method: GET
    url: ${APPWRITE_ENDPOINT}/storage/buckets/{bucketId}/files
    params:
      bucketId: {required: true, description: "Bucket id from appwrite.buckets"}
    headers:
      Content-Type: "application/json"
      X-Appwrite-Project: "${APPWRITE_PROJECT}"
      X-Appwrite-Key: "${APPWRITE_API_KEY}"
    response_shape: "{total, files[] → {$id, name, sizeOriginal, mimeType}}"
    output_schema:
      type: object
      additionalProperties: false
      required: [files]
      properties:
        total: {type: integer}
        files:
          type: array
          description: "From response.files[]"
          items:
            type: object
            additionalProperties: false
            required: [id, name]
            properties:
              id: {type: string, description: "From files[i].$id"}
              name: {type: string, description: "From files[i].name"}
              sizeOriginal: {type: integer, description: "From files[i].sizeOriginal"}
              mimeType: {type: string, description: "From files[i].mimeType"}
    output: schema
    output_transforms:
      - {op: rename, list: files, from: "$id", to: id}
    output_renderer: table
    output_renderer_args:
      header: "Appwrite files"
      list_key: files
      columns:
        - {key: id, label: "Id", max_width: 36}
        - {key: name, label: "Name", max_width: 32}
        - {key: sizeOriginal, label: "Bytes", max_width: 10}
        - {key: mimeType, label: "Type", max_width: 24}
      empty_message: "(no files)"
  - id: functions
    description: List functions in the current project
    method: GET
    url: ${APPWRITE_ENDPOINT}/functions
    headers:
      Content-Type: "application/json"
      X-Appwrite-Project: "${APPWRITE_PROJECT}"
      X-Appwrite-Key: "${APPWRITE_API_KEY}"
    response_shape: "{total, functions[] → {$id, name, enabled, runtime}}"
    output_schema:
      type: object
      additionalProperties: false
      required: [functions]
      properties:
        total: {type: integer}
        functions:
          type: array
          description: "From response.functions[]"
          items:
            type: object
            additionalProperties: false
            required: [id, name]
            properties:
              id: {type: string, description: "From functions[i].$id"}
              name: {type: string, description: "From functions[i].name"}
              enabled: {type: boolean, description: "From functions[i].enabled"}
              runtime: {type: string, description: "From functions[i].runtime"}
    output: schema
    output_transforms:
      - {op: rename, list: functions, from: "$id", to: id}
    output_renderer: table
    output_renderer_args:
      header: "Appwrite functions"
      list_key: functions
      columns:
        - {key: id, label: "Id", max_width: 36}
        - {key: name, label: "Name", max_width: 32}
        - {key: runtime, label: "Runtime", max_width: 16}
        - {key: enabled, label: "On", max_width: 6}
      empty_message: "(no functions)"
  - id: users
    description: List users (Users API — this is what a server API key can see)
    method: GET
    url: ${APPWRITE_ENDPOINT}/users
    headers:
      Content-Type: "application/json"
      X-Appwrite-Project: "${APPWRITE_PROJECT}"
      X-Appwrite-Key: "${APPWRITE_API_KEY}"
    response_shape: "{total, users[] → {$id, name, email, status}}"
    output_schema:
      type: object
      additionalProperties: false
      required: [users]
      properties:
        total: {type: integer}
        users:
          type: array
          description: "From response.users[]"
          items:
            type: object
            additionalProperties: false
            required: [id]
            properties:
              id: {type: string, description: "From users[i].$id"}
              name: {type: string, description: "From users[i].name"}
              email: {type: string, description: "From users[i].email"}
              status: {type: boolean, description: "From users[i].status"}
    output: schema
    output_transforms:
      - {op: rename, list: users, from: "$id", to: id}
    output_renderer: table
    output_renderer_args:
      header: "Appwrite users"
      list_key: users
      columns:
        - {key: id, label: "Id", max_width: 36}
        - {key: name, label: "Name", max_width: 24}
        - {key: email, label: "Email", max_width: 36}
        - {key: status, label: "On", max_width: 6}
      empty_message: "(no users)"
  - id: account
    description: Whoami via the Account API (GET /account). Needs APPWRITE_JWT — a server API key cannot call this route.
    method: GET
    url: ${APPWRITE_ENDPOINT}/account
    headers:
      Content-Type: "application/json"
      X-Appwrite-Project: "${APPWRITE_PROJECT}"
      X-Appwrite-JWT: "${APPWRITE_JWT}"
    response_shape: "{$id, name, email, status}"
    output_schema:
      type: object
      additionalProperties: false
      required: [id]
      properties:
        id: {type: string, description: "From $id"}
        name: {type: string, description: "From name"}
        email: {type: string, description: "From email"}
        status: {type: string, description: "From status"}
    output: schema
    output_transforms:
      - {op: rename, from: "$id", to: id}
    output_renderer: text_template
    output_renderer_args:
      template: |-
        {name} ({email})
          id:     {id}
          status: {status}
  - id: create_database
    description: Create a database (JSON write)
    method: POST
    url: ${APPWRITE_ENDPOINT}/databases
    params:
      databaseId: {default: "unique()", description: "New id, or unique() to mint one"}
      name: {required: true, description: "Database display name"}
    headers:
      Content-Type: "application/json"
      X-Appwrite-Project: "${APPWRITE_PROJECT}"
      X-Appwrite-Key: "${APPWRITE_API_KEY}"
    response_shape: "{$id, name, enabled}"
    output_schema:
      type: object
      additionalProperties: false
      required: [id, name]
      properties:
        id: {type: string, description: "From $id"}
        name: {type: string, description: "From name"}
        enabled: {type: boolean, description: "From enabled"}
    output: schema
    output_transforms:
      - {op: rename, from: "$id", to: id}
    output_renderer: text_template
    output_renderer_args:
      template: |-
        created database {name} ({id})
  - id: create_collection
    description: Create a collection in a database (JSON write)
    method: POST
    url: ${APPWRITE_ENDPOINT}/databases/{databaseId}/collections
    params:
      databaseId: {required: true, description: "Database id"}
      collectionId: {default: "unique()", description: "New id, or unique() to mint one"}
      name: {required: true, description: "Collection display name"}
    headers:
      Content-Type: "application/json"
      X-Appwrite-Project: "${APPWRITE_PROJECT}"
      X-Appwrite-Key: "${APPWRITE_API_KEY}"
    response_shape: "{$id, name, enabled}"
    output_schema:
      type: object
      additionalProperties: false
      required: [id, name]
      properties:
        id: {type: string, description: "From $id"}
        name: {type: string, description: "From name"}
        enabled: {type: boolean, description: "From enabled"}
    output: schema
    output_transforms:
      - {op: rename, from: "$id", to: id}
    output_renderer: text_template
    output_renderer_args:
      template: |-
        created collection {name} ({id})
  - id: create_bucket
    description: Create a storage bucket (JSON write)
    method: POST
    url: ${APPWRITE_ENDPOINT}/storage/buckets
    params:
      bucketId: {default: "unique()", description: "New id, or unique() to mint one"}
      name: {required: true, description: "Bucket display name"}
    headers:
      Content-Type: "application/json"
      X-Appwrite-Project: "${APPWRITE_PROJECT}"
      X-Appwrite-Key: "${APPWRITE_API_KEY}"
    response_shape: "{$id, name, enabled}"
    output_schema:
      type: object
      additionalProperties: false
      required: [id, name]
      properties:
        id: {type: string, description: "From $id"}
        name: {type: string, description: "From name"}
        enabled: {type: boolean, description: "From enabled"}
    output: schema
    output_transforms:
      - {op: rename, from: "$id", to: id}
    output_renderer: text_template
    output_renderer_args:
      template: |-
        created bucket {name} ({id})
---

# Appwrite

Self-hosted Appwrite REST. One plugin: P0 health + list databases/buckets,
P1 one level down (collections, documents, files, functions, users) and
JSON creates (database, collection, bucket). Multipart file upload is
not an action (`plugin_call` has no multipart body) — the stock skill
`appwrite` is the upload recipe.

This is a markdown HTTP descriptor. It is not an MCP server, not a
provider, and not the Cursor marketplace bundle. Point
`APPWRITE_ENDPOINT` at **your** instance — do not hardcode a hosted
cloud host.

Re-install after this cut if you already had the P0 file:

```
xlii plugin --install-stock --force
```

## Auth

Official REST headers ([appwrite.io/docs/apis/rest](https://appwrite.io/docs/apis/rest)):

| Header | Env | Used for |
|---|---|---|
| `X-Appwrite-Project` | `${APPWRITE_PROJECT}` | every call |
| `X-Appwrite-Key` | `${APPWRITE_API_KEY}` | server routes (health, databases, storage, users, functions, creates) |
| `X-Appwrite-JWT` | `${APPWRITE_JWT}` | `account` only — GET /account is a **user** route |

`APPWRITE_ENDPOINT` is the SDK `setEndpoint` value: origin **including**
`/v1`, no trailing slash. Example: `https://appwrite.example.com/v1`.

Run `appwrite.set`. Secrets are form fields — never put them in this
file or in chat. Server API key scopes for the reads: `health.read`,
`databases.read`, `buckets.read`, `files.read`, `users.read`,
`functions.read`. Creates need the matching write scopes.

```
/plugin subscribe appwrite
/plugin call appwrite.set
/plugin call appwrite.health
/plugin call appwrite.databases
/plugin call appwrite.collections databaseId=main
/plugin call appwrite.documents databaseId=main collectionId=notes
/plugin call appwrite.buckets
/plugin call appwrite.files bucketId=files
/plugin call appwrite.functions
/plugin call appwrite.users
/plugin call appwrite.account
/plugin call appwrite.create_database name=lab
/plugin call appwrite.create_collection databaseId=main name=notes
/plugin call appwrite.create_bucket name=files
```

`users` is the API-key whoami-adjacent list. `account` needs a JWT from
a logged-in user session (`APPWRITE_JWT`); a key alone 401s on that
route — that is Appwrite, not a bug in this file.

## Next

- Multipart `create_file` as a plugin action needs a kernel body type
  `plugin_call` does not have yet. Until then the stock skill
  `appwrite` (`xlii/stock_skills/appwrite/SKILL.md`) is the vault-backed
  upload recipe.
