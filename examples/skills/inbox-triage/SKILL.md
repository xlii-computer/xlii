---
name: inbox-triage
description: Triage an inbox — summarize what needs a reply, group by thread, draft without sending unless asked.
---

# Inbox triage

Use when the user wants help staying on top of email.

## Workflow

1. **Search or list first** — `search_email` for a name/topic, or ask the user
   which account/folder if several are configured.
2. **Summarize, don't dump** — group by sender/thread; call out unread items that
   need action vs FYI.
3. **Read on demand** — `read_email` with the stable id (`account:folder:uid`) when
   a thread needs detail.
4. **Draft, don't send** — compose replies in the chat for review. Only call
   `send_email` when the user explicitly asks to send.
5. **Sending** — confirm `to` and `subject` with the user before invoking
   `send_email` (the tool will still require typing `send` in the UI).

## Privacy

Mail is private local cache under `.xlii/mail/`. Never forward, CC, or publish
content the user did not request.
