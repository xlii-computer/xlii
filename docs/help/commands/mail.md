# /mail

`/mail` triages your inbox from the REPL — list, search, read, and (on request)
send. Reading is free and ungated; **sending always confirms** (type `send`, not
just `y`).

## Setup (once)

```
xlii email accounts add personal
```

Prompts for IMAP/SMTP host, user, and password. The password goes to the encrypted
vault (`email:personal`); `config.json` holds only host/port/user descriptors.

Gmail/Outlook users: create an **app password** for IMAP/SMTP (basic auth). OAuth2
is not in v1.

## Day-to-day

```
/mail list [--unread] [--account personal]
/mail search billing [--account personal]
/mail read personal:INBOX:42
/mail send --to you@example.com --subject "Re: …" --body "…"
```

CLI twins: `xlii email list`, `xlii email search`, `xlii email read`, `xlii email send`.

## Agent tools

These tools are advertised only after an account exists. With none configured
they are hidden — otherwise chat retries a guaranteed fail until its
iteration cap is gone.

- `read_email` / `search_email` — read-only, safe in plan mode and workers.
- `send_email` — gated outward action; refuses headless (without `--yolo`) and
  workers. Confirm shows `to` + `subject` + body preview; type **`send`** to approve. Chat never gets send.

Fetched bodies are capped at 256 KiB and cached under `.xlii/mail/` (local,
ignored, pruned). Attachments surface as name/size only — never inlined bytes.

## Role

See `examples/roles/inbox-assistant.md` with the `inbox-triage` skill for a
specialist that drafts before sending.
