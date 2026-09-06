# xlii — System Design

> **Status:** draft for review (2026-09-02). Describes the system as built at
> `main @ 94cbf7b2`. Control-flow and ownership details are kept current in
> [ARCHITECTURE.md](ARCHITECTURE.md); operator walkthroughs are in
> [GOLDEN-PATH.md](GOLDEN-PATH.md) and [HOWTO.md](HOWTO.md); the command and
> configuration reference is [REFERENCE.md](REFERENCE.md). This document is
> the *why*: the problem, the principles, and the decisions that follow from
> them. Where it says "refused," the refusal is deliberate and recorded.

---

## Abstract

xlii is a terminal-native personal AI substrate. It places a language-model
agent at the shell level of a personal machine, under a set of constraints
that most AI tooling does not adopt: local files are the source of truth and
remote services are derived from them; everything the user curates is a plain
file they own; the model is never trusted as a security boundary; and every
inbound channel — web, message, webhook — is treated as a remote-code-execution
surface and gated as one.

The result is closer to an operating environment than an assistant. The user
composes it — personas, reference documents, API plugins, roles, skills — and
the environment compounds over time. A shell-first REPL is the primary
surface; a full-screen terminal UI, a browser client, a desktop client, and a
multi-instance control plane are views over the same session kernel.

The implementation is about 142k lines of Python across 519 modules, with a
hermetic test suite of ~5,400 tests that runs without network access or API
keys. It is alpha software with a settled architecture.

---

## 1. Problem and thesis

### 1.1 The problem

A capable language model on a personal computer should be able to help operate
that computer: read and change files, run commands, keep notes, look things
up, and carry context between tasks. Three properties are required for that to
be acceptable on a machine someone actually lives on:

1. **Privacy as a hard constraint.** What is on the machine stays on the
   machine unless the user explicitly chooses otherwise, per project, with a
   setting that can be turned all the way off.
2. **Ownership.** The accumulated context — memory, preferences, reference
   material, integrations — must be portable text the user can read, edit,
   version, and move to another machine or another vendor.
3. **Safety that does not depend on the model's cooperation.** A model can be
   wrong, can be manipulated by content it reads, and cannot be audited. The
   controls that decide what runs must therefore live in code, outside the
   model.

Existing tools satisfy at most one of these. Vendor coding agents optimize for
a task loop and keep memory vendor-side. Hosted chat products own the memory
outright. Frameworks provide parts, not a place to live. None treats the
operating shell as the integration point.

### 1.2 The thesis

**The vendor provides primitives; the user composes the system.** Model
inference, hosted retrieval, and search are commodities that will be replaced.
The durable asset is the user's curation. xlii therefore treats the model as
an interchangeable component behind a stable interface, and treats the user's
files as the system.

From that follow the positioning choices in §2 and the principles in §3.

---

## 2. Positioning and non-goals

xlii is a substrate that rewards investment. It is deliberately not:

- **An IDE or editor.** The user brings their own. Inline diff views and
  visual context are out of scope.
- **A hosted chatbot.** There is no vendor-side memory; there is no account
  to log into beyond the model provider's API key.
- **A framework.** It is the product one lives in, not a kit for building one.
- **A cloud agent host.** Where cloud agents are useful, xlii delegates to
  them through adapters (§14) rather than re-hosting their execution model.
- **A multi-tenant service.** Hosted compute that runs users' agents on their
  behalf is refused. A hosted tier may relay; it may not think.
- **A mass-market tool.** Casual users may bounce. That trade is accepted in
  exchange for depth for users who will compose.

Privacy properties that depend on the model vendor's account tier (data
retention, training use) are reported as facts from the account, never
asserted by the product.

---

## 3. Principles

These are the load-bearing ideas. Each later section is an application of one
or more of them.

**P1 — The model is not a security boundary.** Anything the model declares
about its own intent is advisory. Whether a command runs, whether a path may be
written, whether a worker may escalate, is decided by code that classifies the
request independently and fails closed. (§9)

**P2 — Local truth, derived remote.** The file on disk is authoritative while
it is being edited. Hosted retrieval, indexes, and mirrors are derived and
disposable. Remote custody is a per-project dial that includes zero. (§5)

**P3 — One owner per concern; derive, do not store.** Every piece of state has
exactly one owner. Anything that can be computed from other state — write
scope from the active mode, trust flags from a tier — is computed on demand,
so there is no second copy to fall out of sync. (§5, §7)

**P4 — Fail closed.** On unknown input, unparseable input, or a missing human
to ask, the answer is refuse or escalate, never permit. (§9)

**P5 — Clients import the kernel; the kernel never imports a client.** The
session core knows nothing about terminals, browsers, or windows. Every user
interface is a projection over the same kernel and is enforced as such by a
CI import contract. (§4, §13)

**P6 — Everything the user curates is a plain file.** Personas, reference
documents, plugins, roles, skills, memory pages, journals, and plans are
Markdown or JSON the user can read and edit. There is no opaque store. (§10)

**P7 — Every inbound channel is a remote-code-execution surface.** A message
from a phone, a webhook, a browser tab, or a paired device can cause commands
to run on the machine. Each is gated by identity, by trust, and by an explicit
open window; none is trusted because the transport is encrypted. (§12, §13)

**P8 — Pure core, thin glue.** Policy — classification, pairing, gating,
mode contracts — is implemented as pure functions with injected time, tested
offline without extras or network. I/O adapters around them are thin. (§15)

**P9 — Ratchets over hope.** Where a property matters, a mechanical check
enforces it and is only allowed to tighten: import contracts with a frozen,
shrink-only exception list; docs checked against the live command registry;
tests that must be un-marked the moment they start passing. (§15)

---

## 4. System overview

### 4.1 Shape

```
CLI (thin)  →  domain commands  →  session launch (code | chat | home)
                                         │
                                    REPL loop ── bare input → shell
                                         │       ?text     → agent turn
                                         │       /cmd      → slash command
                                         ▼
                                  Agent.run_turn
                                    mode → tool palette
                                    tools → dispatch (parallel-safe / barriers)
                                    dirty paths → end-of-turn sync (if enabled)
                                         │
                                       exit → graceful teardown (journal deferred)
```

A single `Conversation` object owns the turn lifecycle. The inline REPL, the
full-screen TUI, the browser and desktop clients, and the autonomous loop all
drive it and render its events. No surface has a private copy of the turn
logic.

### 4.2 Three session kinds

| Session | Entry | Scope | Remote sync |
|---|---|---|---|
| **Home** | `xlii scratch` | The machine; no project required | Never |
| **Project** | `xlii code` in an initialized folder | That folder's tree and `.xlii/` state | Off (`--local`) or opt-in |
| **Chat** | `xlii chat [name]` | A persona's own memory; project-blind by default | Per persona |

Home is where the user roams the machine — shell, diagnostics, explanations —
with nothing uploaded. A project is a folder the user has chosen to make one.
Chat is conversation with a persona, isolated from any project.

### 4.3 Two input postures

Within a session, input is routed by posture:

- **Lab posture (`[$]`)** — shell-primary. A bare line is a shell command
  with a real TTY (editors and pagers work). `?text` addresses the agent.
  `!cmd` is a one-off styled shell escape. `/cmd` is a slash command.
- **Conversation posture (`[M]`)** — a bare line is addressed to the primary
  persona. Shell and slash escapes remain available.

The posture determines what a bare line means; it does not change which
session the user is in or which tools exist.

### 4.4 Three status axes

Status is always reported along three independent axes, in this order:

```
mode:     plan | rail | debug | discovery | ops | —
trust:    guarded | trusted | expedited
surface:  code | chat | home
```

Mode is exclusive and restricts the tool palette (§8). Trust is a ladder
(§9.1). Surface is the session kind. Overlays (help, image preview, external
harness) are additive and are not modes.

---

## 5. State and storage

### 5.1 Ownership

| Owner | Holds |
|---|---|
| `SessionState` | Per-session flags and attachments, nested by concern (trust, attachments, meter, overlays, model pins, compaction) |
| `Agent` | Conversation history, the active mode controller, client pool, project and global config handles |
| `REPLState` | Surface persistence (workspaces, shell cwd, persona handle); delegates all session flags to the same `SessionState` |
| Project state (`.xlii/`) | Project identity, collection ids, local search index, turns, session snapshot, plans, wiki, journal, inbox |
| Global config (`~/.config/xlii/`) | Models, key references, encrypted vault, loadouts, per-machine certifications |

The rule is one owner per field and no bidirectional synchronization. The
REPL reads and writes session flags *through* the agent's `SessionState`; it
does not keep its own copy. Historically the two had separate copies with a
sync step, which is the classic agent/REPL desync bug; the structural cause was
removed rather than patched.

### 5.2 Durability

All durable state is written atomically: temporary sibling, `fsync`,
`os.replace`, with mode `0600` for anything sensitive. State that several
processes may contend for (exclusive-input records, pairing windows, serve
grants) is additionally protected by `flock`. Agent edits to *user* files are
plain writes, matching editor behavior.

Ephemeral security state — an open pairing window, an open remote-control
window, current input ownership — lives in the runtime directory. It expires by
clock, is consumed by use, and does not survive reboot. It is never written to
configuration, so nothing has to be remembered and flipped back.

### 5.3 Local truth and derived remote

Files on disk are the truth while editing. Optionally, a project mirrors its
files and metadata into the vendor's hosted retrieval store at the end of each
turn and on exit. That mirror provides retrieval for the agent and durability
for the user; it is not a performance layer and is never required. A project
initialized with `--local`, and every home session, has no mirror at all.

Search does not depend on the mirror. A local SQLite FTS5 index is the floor,
so the value of a project's history is never trapped behind a single vendor
API. Sync refuses to perform a mass remote deletion when the local tree is
empty, so a local mistake cannot become a remote one.

Context compaction operates on the in-memory history only; on-disk turns are
untouched and full fidelity is recoverable.

### 5.4 Secrets

The vendor management key — the credential that can provision other keys and
spend the account — is accepted from the environment only. It is stripped
before any configuration is saved, and the diagnostic command warns if it is
ever found on disk. Per-session chat keys are provisioned by setup, scoped,
expiring, and rotatable. Plugin credentials live in an encrypted vault (Fernet;
master key from environment, OS keyring, or a `0400` file) and are injected
into the agent's shell environment only when a subscribed plugin references
them, with best-effort redaction of the value from captured output.

Export produces a portable tree of the user's curation with no secrets in it.

### 5.5 Memory hierarchy

| Layer | Address | Content |
|---|---|---|
| Episodic | `conv://` | Turns and conversations |
| Working | `xlii://` | Session attachments, staging area, plans |
| Semantic | `wiki://` | Curated pages with provenance |

Project memory pages and the product's own bundled documentation are separate
namespaces and never merge; the former is the user's to edit, the latter is
version-locked.

---

## 6. Sessions, personas, and memory

### 6.1 The primary persona

There is one primary persona per user. Its memory lives under the user's
global state, not under any project, and it is present in every session:
in a home session it is the conversational voice; inside a project it can see
that project's journal and memory pages as ambient context without becoming a
different persona. Switching vendors keeps it; it is the relationship, and the
model behind it is a component.

### 6.2 Chat personas

Additional personas are separate files with separate memory. They are voices,
not jobs: a persona defines how the agent speaks and what it carries; a
**role** (§10) defines a task specialization. Persona memories are isolated by
construction — there is deliberately no field on the tool context through
which one persona's retrieval store could be attached to another's session.
Cross-persona transfer happens only through deliberate bridges: bookmarked
turns (marks) and an explicit reference command.

### 6.3 Detached threads

Code and chat are two threads in one process. Switching parks the current
history and restores the other's; a chat persona never carries the code
conversation and vice versa.

### 6.4 History, snapshots, and journal

Default history is a lightweight re-seed of recent turns. A full session
snapshot for crash continuity is opt-in. The project journal — a passive diary
of lab activity — is opt-in and code-only. On exit the journal defers its
summarization: raw entries are written durably and immediately, no model call
occurs on the exit path, and the next session catches up. Leaving is instant
and nothing is lost.

Opening the same project twice concurrently is refused, because the on-disk
session state has one owner.

---

## 7. The agent loop and tools

### 7.1 Orchestrator and workers

The **orchestrator** is the agent the user talks to. It holds the full tool
palette: file read/write/edit, directory listing, glob and grep, shell, project
search, web and social search, plugin discovery and invocation, and
`dispatch_subagent`.

**Workers** are dispatched by the orchestrator (or by roles, skills, and the
autonomous loop) to investigate in parallel. They are read-only by
architecture: their palette contains no write tools and no dispatch; they
receive no client pool, so they cannot recurse; and their shell access is
capped to read-only by the independent classifier (§9.2), not by instruction.
A worker returns a single summary and retains no history. A worker that runs
out of budget returns "inconclusive," never a verdict, so a starved verifier
cannot fail a build.

**Writer-workers** exist only inside the autonomous loop's swarm mode. Each
works in its own git worktree located outside any project root (so sync cannot
see it), may write and run shell, may not run system-level commands, and may
not reach the network without the trusted tier.

**External-provider workers** run other vendors' models on xlii's own tool
loop for one read-only pass. Their palette is filtered at schema time by
capability flags — tools the provider cannot support are removed, not merely
discouraged. They are never promoted to orchestrator.

### 7.2 Four postures

The system separates four kinds of work into four trust postures with
distinct palettes. Coordinating (the orchestrator, full palette, dispatch).
Operating the machine (the ops mode: read tools plus shell, with the shell
gate as the brake). Building (code and rail modes: writes unlocked, plan
domain locked). Verifying (judges, cold-context verification, blind peer
review: read-only, often on a different model or vendor than the builder).
These are not personalities layered on one agent; they are different tool
sets enforced in code.

### 7.3 Dispatch

A batch of tool calls executes in declaration order, segmented: consecutive
parallel-safe tools (reads, searches, dispatch) run concurrently; each mutator
is a barrier, so a later read observes an earlier write. Parallel-safety is a
property declared on the tool, not inferred.

### 7.4 Derived write scope

Which paths a turn may write is derived from the active mode at the start of
every turn and is never stored. The planner may write only to the plan
directory; the implementer may write the tree and may only tick checkboxes in
the plan. Deny wins over allow, and a domain root itself is never a write
target.

### 7.5 Turn ergonomics

Turns run as background jobs by default; the terminal remains usable while
the agent works, and the user can interject steering notes that are folded
into the conversation at the next safe boundary (between tool calls, never
mid-tool). A turn that claims completed work in the past tense while having
made no tool calls is flagged to the user as a probable hallucination.

Hooks observe turn events and may schedule bounded follow-ups; they never gate
or abort the operation that fired them.

---

## 8. Modes

A mode restricts the tool palette in code. The prompt preamble tells the model
what the mode is for; the palette is what makes the restriction real.

| Mode | Palette | Purpose |
|---|---|---|
| **execute** (default) | Full; plan directory denied | Normal work |
| **plan** | Read tools; write only to the plan directory | Investigate, produce a numbered plan; `/execute` acts on it |
| **discovery** | Read tools only | Research and explanation; produces no deliverable |
| **ops** | Read tools + shell | Machine diagnostics with platform-correct probes; shell gate is the brake |
| **rail** | Stages 0–3 read-only; 4–5 full | Six-stage coding flow: requirements → architecture → edge cases → pseudocode → implementation → self-review; the human advances each gate |
| **debug** | Phase-specific | Hypothesize (read) → instrument (edits must carry a marker; whole-file writes refused) → reproduce (shell) → analyze (read) → fix → verify (read) |
| **chat** | Memory and external reads; no filesystem, shell, or writers | Project-blind conversation |

Unknown modes and unknown trust tiers are hard errors; nothing falls back to a
guess, because a typo that silently selected the permissive default would be
an accidental security decision.

The autonomous loop (§11) is mutually exclusive with plan and rail. Plan
gating is offered, not imposed, for small jobs; a hard plan requirement is
reserved for work with real blast radius.

---

## 9. Trust and safety

### 9.1 The trust ladder

Trust is one field with three values. The flags the rest of the system reads
are derived from it, so the tiers cannot desynchronize.

| Tier | Waives | Never waives |
|---|---|---|
| **guarded** (default) | Nothing | — |
| **trusted** | Confirmation for network commands | System-level confirmation |
| **expedited** | Remaining per-action friction (spend prompts, pipeline step confirms) | System-level confirmation, delete guard, admin gates, budget, untrusted-content carry |

In the code these are the `/safe`, `/yolo`, and `/freeball` commands. The
expedited tier is never persisted: a reopened session is at most trusted.
Standing grants (`/approve`) can pre-authorize network only; system-level
approval cannot be granted by any mechanism.

**Unattended operation** is not a fourth tier. It is task-scoped: a saved task
pipeline can be certified on a specific machine and then run without an
operator, with the same code gates in force. A session-wide autonomous mode
was considered and refused. Certifications and startup bindings live in the
per-machine configuration directory, never in the clonable project tree,
because a hostile repository must not be able to arm itself.

### 9.2 The shell gate

Every shell command the agent proposes carries a declared intent — read-only,
modifies-project, network, or modifies-system — and the declared intent is
ignored for the purpose of authorization. The command text is classified
independently: binary names against known sets; wrappers (`env`, `timeout`,
`nice`, `xargs`) peeled to the inner command; `find -exec` bodies, `bash -c`
and `python -c` script bodies, command and process substitutions classified
recursively; redirect targets and mutating commands checked against the
project root. The effective intent is the stronger of declared and classified.

`modifies-system` always prompts a human. Network prompts unless the tier or a
standing grant waives it. Read-only workers are refused anything that
classifies above read-only. In a background or non-interactive context, a
prompt that cannot be shown is answered "no."

**Known limitation.** The classifier currently defaults an unrecognized binary
to read-only and its command-substitution scan does not handle nesting, so
container runtimes, tunnel tools, and nested `$()` can be underclassified. A
fail-closed revision — unknown binaries classify as project-mutating,
read-only becomes allowlist membership, substitution scanning becomes a
depth-tracking parser — is specified and scheduled.

### 9.3 Path containment

File tools resolve every path against the project root after following
symlinks and refuse anything that lands outside it. Write domains (§7.4)
layer on top. Remote filesystem providers strip a leading `/` and never honor
it as absolute.

### 9.4 Human-only operations

Destruction and administration require a genuine foreground human: a real
console, and a fresh presentation of the admin secret that is never satisfied
by a remembered elevation. No flag on a request context is accepted as proof
of a human. Agents, tasks, jobs, and headless invocations cannot reach these
operations at all — they are a separate dispatch class.

The teardown ladder that revokes and removes everything an instance has
created runs dry by default, requires a typed phrase, proceeds only downward
(nodes before the controller), and identifies what to remove by a local
ownership manifest of minted ids — never by name prefix, which is
account-wide and would catch sibling instances. The product makes no forensic
erasure promise; full-disk encryption remains the real control and the
documentation says so.

### 9.5 Review before run

Every UI affordance that would run a command — a menu, a pane action, the git
panel, the tool catalog — seeds the exact line into the input for the human to
submit. Menus act; they do not type on the user's behalf, except by that
deliberate prefill.

### 9.6 What is not defended

Content the agent reads — tool output, fetched pages, plugin responses,
webhook payloads — enters its context unsanitized beyond secret redaction and
a Unicode-credibility note. The system's defense against prompt injection is
therefore that the *consequences* are gated (§9.2–9.4), not that the input is
filtered. This is appropriate for a personal substrate with a single operator
and is stated here so it is not mistaken for a stronger property.

---

## 10. Knowledge and composition

Everything the user composes is a plain file with YAML frontmatter where
metadata is needed.

| Artifact | Form | Purpose |
|---|---|---|
| **Persona** | `personas/<name>.md` | Voice, memory scope, and an optional loadout |
| **Document** | attached via `/attach doc` | Reference material inlined or pointed to |
| **Plugin** | Markdown HTTP descriptor | A callable API, invoked by `/get` or by the agent |
| **Role** | `roles/<name>.md` | A task specialization (architect, debugger, test engineer, …) |
| **Skill** | `skills/<name>/` | A reusable method, often fanning out workers |
| **Loadout** | frontmatter or saved bundle | Docs + plugins + skills + model pinned together |
| **Wiki page** | `.xlii/wiki/*.md` | Semantic memory with sources |
| **Plan** | `.xlii/plans/current.md` | Living plan with checkboxes and amendments |
| **Project command / tool** | `.xlii/commands.py`, `.xlii/tools.py` | User-defined slash commands and agent tools |

**Plugins are descriptors, not servers.** A plugin is a Markdown file that
describes an HTTP API; the runtime performs the call. No process is spawned
per capability. Subscription is mandatory: the agent sees only plugins the
user has subscribed to in this session. The product speaks MCP *outward* — it
exposes the workspace to external tools — but does not adopt an MCP
marketplace as its inward capability model. Text returned by an API is never
interpolated into console markup, and plugin secrets never travel back through
chat.

**Skills** resolve with precedence stock < imported < global < project; only
an index is injected into context, with the full body attached on demand.
**Roles** may equip loadouts and reference skills but never redefine them.

**Wiki pages carry provenance and are born unverified.** A page written by the
agent is not promoted to fact until a skeptical verification pass marks it so.
This is the anti-confabulation mechanism for long-lived memory.

The canonical attachment verb is `/attach` with three cost shapes kept
distinct: inline every turn, live pointer, and search on demand. A loadout is
declared in a persona's frontmatter and is never itself placed in the prompt.

---

## 11. Verification

The same model that wrote code is never the only judge of it.

**The autonomous loop** runs build → test → judge → fix until the judges are
green or budget is exhausted. Judges include shell test commands, a CI status
poll (read-only via the platform CLI; it never pushes), a same-vendor
cold-context verifier, a cross-vendor verifier, and an external harness.
Judges can lock test files so that a builder cannot weaken a test to pass it,
and a collusion detector alarms when assertion counts fall across cycles while
judges continue to pass. Same-vendor builder and checker is treated as de
facto collusion; the cross-vendor path quarantines the verdict.

**On-demand review** offers `/verify` (a cold-context check of uncommitted
work against the stated task), `/peer` (a blind review of a committed diff in
which the reviewer does not see the author's intent), and `/consult` (a
cross-vendor second opinion that never enters the conversation history).

**Turn receipts** record the evidence a turn produced against the claims it
made, at a single finalization point. They warn; they never block, and they
never rewrite the model's output.

---

## 12. Remote operation and multiple instances

### 12.1 Topology

Instances form a **star, not a mesh**. One **controller** holds the
management key and is the only instance that can provision. **Nodes** hold
capped inference keys and revocable access; a compromised node can waste its
own budget but cannot command a peer, provision a key, or spend the account.
Server-to-server federation stays off; enabling it would re-introduce mesh
authority.

There is no code path that writes the management key into a remote
configuration. Remote provisioning mints a scoped key locally and seeds only
that.

### 12.2 Identity and pairing

A node's identity is a pinned OMEMO device fingerprint, not a name; enrollment
happens on the controller. Trust-on-first-use is **bound**: a device is
trusted only when it proves possession of a fresh, single-use, short-lived
pairing code, minted by the controller and presented by the device over the
encrypted channel. Only the code's hash is ever stored. The pairing window is
runtime state that expires by clock and is consumed by use; it never edits
configuration and never loosens the default of *no* blind trust. Successful
decryption is not authorization — the device must also be trusted.

### 12.3 Remote control

Remote control requires an open, time-bounded window on the workstation. A
closed window means closed ingest. Because store-and-forward transports allow
a message planted while the client is down to fire when it reconnects, the
preferred path for remote turns is live-only: an HTTP entry point reachable
only over the tailnet, authenticating callers by the local Tailscale daemon's
WhoIs — never by inferring identity from an address range, and denying when no
answer is available. The messaging transport remains available, opt-in, for
off-tailnet use.

Input is exclusively owned: one identity types at a time, and a paired phone
taking input stops keystrokes from the workstation except lock and unlock. A
remote identity cannot enter the shell posture unless the workstation has
explicitly opened a remote-lab window. A phone is a thin client; a native or
rooted phone build was refused.

### 12.4 Elevation and teardown

Killing a remote process (revivable) and destroying an instance (irreversible)
are separate families and never merge. Destructive remote verbs require TOTP
elevation with a strike lockout. Provisioning has no remote elevation path;
it is controller-local, permanently. An email-borne emergency channel can
issue deny-only instructions (kill, destroy) under allowlist, DKIM, phrase,
and second-round confirmation; it cannot converse or reach the shell.

### 12.5 Memory across instances

Memory flows to the controller. Nodes journal locally first and acknowledge
to disk before anything is mirrored; a node never blocks on the control plane
to remember. The controller pulls node turns into the primary persona's
history. Optional hosted retrieval is a controller-side index, not the
transport and not the source of truth.

---

## 13. Client surfaces

Every client is a view over the same kernel session; none reimplements turn
logic or holds a private copy of state. The import contract (§3, P5) enforces
this in CI.

| Surface | Reach | Authentication | Notes |
|---|---|---|---|
| **REPL** | Local terminal | Operating-system user | Shell-primary; full TTY passthrough |
| **TUI** | Local terminal | Operating-system user | Full-screen view; panes, dock, image preview |
| **Web serve** | Loopback or tailnet | **None, by design** | The port is a shell. Wildcard binds are always refused; a non-loopback bind requires an explicit flag and prints a warning |
| **Public serve** | Internet via reverse proxy | Pairing code → grant cookie | Process binds loopback only; TLS is the proxy's job; cookie is a random token distinct from the session id; forwarded-for headers trusted only from loopback peers, rightmost hop; lockout on failed codes |
| **WebSocket protocol** | Loopback or tailnet | Token on connect, constant-time compare | JSON events are the kernel's own turn events; frames size-capped |
| **Desktop client** | Local window | Operating-system user + exclusive-input records | Pane deck over the WebSocket protocol; lock overlay; theme packs |
| **Message daemon** | Tailnet or Internet | Allowlisted identity + trusted OMEMO device + rate limits + audit log | Long-lived; verbs dispatched as argv, never through a shell |
| **Webhook inbox** | Configurable | Bearer token (auto-minted) | Only enqueues goals into `.xlii/inbox/`; a separate drain step executes; payload frontmatter is treated as attacker-controlled and cannot auto-commit |
| **MCP context server** | Local process | Process trust | Exposes workspaces to external tools |

Two UI rules hold across clients. There is a single input line and no modal
popups; panels claim and transform that line. Panes are pure projections of
an address and a selection with no cached tree; the kernel emits plain status
segments and the client owns all markup.

---

## 14. Extensibility and interoperation

- **Project commands and tools.** A project may add slash commands
  (`.xlii/commands.py`) and agent tools (`.xlii/tools.py`) that load
  alongside the built-ins.
- **Prompt overrides.** System prompt layers are files a project may override.
- **Hooks.** Observers on turn events with a hard cap on follow-ups.
- **Uniform addressing.** `scheme://target` across local files, remote
  filesystems (FTP/SFTP/WebDAV/SMB), conversations, wiki, git, and the
  staging area; the same verbs (`ls`, `cat`, `cp`, …) operate on all of them.
- **Harnesses.** The same task can be run through external agents (Claude,
  Cursor, Codex, Grok Build) under *their* permission models. xlii delegates
  and compares; it does not re-host their execution.
- **MCP outward.** Workspaces and project tools are exposed to external
  clients through an MCP server.

---

## 15. Engineering method

The codebase is developed largely by AI agents under a single maintainer. The
method exists to keep that coherent.

**Contracts.** An import-linter contract forbids kernel → client imports, with
a frozen list of historical exceptions that may only shrink. Custom contract
checks baseline the use of `Path.home`, TTY detection, and keyring access and
fail on any new site. A script verifies the contract's module list matches
the filesystem so a new module cannot dodge it.

**Generated documentation.** Command tables in the guides are generated from
the live registry and CI fails on drift. The bundled help corpus and
self-documentation are built from source and checked.

**Hermetic tests.** The suite redirects `HOME` and the configuration directory
before any import, scrubs the management key from the environment, and drives
agent turns from scripted iterations rather than a network. Roughly 5,400
tests run in about three minutes with no API key. Security-critical
behaviors are pinned — forwarded-header trust, cookie attributes, process-group
reaping of orphaned servers, worker shell refusal — and several tests refuse
cooperative fakes on purpose.

**Versioning as receipt.** The version is `MAJOR.YYDDD.TESTS` — date plus
green-test count — and the stamping script refuses to run on a red suite.

**Security CI.** Secret scanning across full history, dependency
vulnerability scanning with critical/high failing the build, and static
analysis, on every push and weekly.

**Design proposals.** A change of design begins as a proposal that states the
facts it depends on as verified against the tree on a date, the principles
that are not negotiable, a phased plan in which every phase has a mechanical
exit gate, a threat analysis of what an attacker gains or loses, an explicit
list of what is out of scope, and sequencing. Proposals are the source of
truth for implementers while live; when shipped they are archived so the
active queue stays small. Structural refactors are zero-behavior-change moves
executed as file-disjoint parallel workstreams under a machine-checked merge
contract.

---

## 16. Known limitations and open problems

Stated so the reader does not have to discover them.

- **Shell classifier coverage.** Unknown binaries default to read-only and
  nested command substitution is not parsed (§9.2). Fail-closed revision
  specified.
- **No content sanitization.** Prompt-injection defense is consequence gating,
  not input filtering (§9.6).
- **Middle-tier confirmation.** The guarded tier prompts for network and
  system commands but not for project-mutating ones. This is a deliberate
  ergonomic choice for a coding agent; it means classifier misses matter
  more than they otherwise would.
- **Web serve is unauthenticated on trusted networks.** Documented loudly;
  still the easiest catastrophic misconfiguration.
- **Vendor coupling.** The primary provider is xAI (inference, retrieval,
  key management). External providers are supported for workers; provider
  and search backends as first-class ports are future work.
- **Complexity.** The turn pipeline and REPL cores remain large, and the
  space of mode × trust × surface × overlay × loadout is wide. The engineering
  method (§15) exists to manage this; it has not eliminated it.
- **Single maintainer.** The bus factor is one. Proposals, contracts, and the
  test suite are the mitigation.
- **Typing.** Core APIs lean on dynamic typing with no static checker in CI;
  refactors rely on tests and contracts.

---

## 17. Glossary

| Term | Meaning |
|---|---|
| **Substrate** | The durable layer — files, memory, agents, addressing — that every client sits on |
| **Kernel** | The session core: turn lifecycle, state ownership, tool dispatch |
| **Client surface** | Any user interface projecting the kernel: REPL, TUI, browser, desktop |
| **Home session** | A session with no project and no remote sync |
| **Lab** | The shell-primary project surface |
| **Posture** | Which meaning a bare input line has: conversation or shell |
| **Primary persona** | The one persona whose memory follows the user across projects and instances |
| **Persona / role** | A voice with its own memory / a task specialization; the two are distinct |
| **Loadout** | Docs, plugins, skills, and model pinned to a persona or saved as a bundle |
| **Mode** | An exclusive restriction of the tool palette enforced in code |
| **Trust tier** | guarded / trusted / expedited — which confirmations are waived |
| **Intent** | Shell command severity: read-only < modifies-project < network < modifies-system |
| **Shell gate** | The code-level classifier and authorization for shell commands |
| **Invariant gates** | Confirmations no tier waives: system-level commands, delete guard, admin, budget |
| **Orchestrator / worker** | The user-facing agent with the full palette / a read-only parallel investigator |
| **Judge** | A verification oracle in the autonomous loop |
| **Collusion** | A builder and checker converging on green without the work being correct |
| **Rail** | The six-stage gated coding workflow |
| **Controller / node** | The instance holding the management key / an instance holding a capped key |
| **Fabric** | The multi-instance control plane |
| **Bound TOFU** | Trust-on-first-use granted only against a fresh single-use secret |
| **Remote session window** | The time-bounded state that permits remote control |
| **Exclusive input ownership** | One identity types at a time on a client |
| **Teardown ladder** | The human-only, dry-run-first sequence that revokes and removes what an instance created |
| **Review-before-run** | UI seeds a command; the human submits it |
| **Never-sync** | No remote mirror, by construction |
| **Ratchet** | A mechanical check that is only allowed to tighten |
| **Seam** | An explicit boundary between components |
