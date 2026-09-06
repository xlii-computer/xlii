# job (CLI)

Named **advisory** farm jobs. The throne posts an ad; a node that *offers*
that name claims it, runs a read-only `explore` worker, and writes text
back. Not a guest shell. Not a clone. Not sitting.

```
xlii job post --job explore --task "…" [--context FILE|-] [--accept …]
xlii job post --job explore --task "…" --project iXaac-lab
xlii job post --kind market --job explore --task "…"
xlii job ls
xlii job show <id>
xlii job result <id>
xlii job watch [--once] [--poll 2]
xlii job cancel <id>
xlii job bench <node> [--until …]
xlii job evict <node>
xlii job accept <id> --fp <fingerprint>
xlii job invite <fp> [--venue host]
```

Node permit lives in `config.json`:

```json
"jobs": {
  "offers": ["explore"],
  "gig": "kimi",
  "node": "kimi-laptop",
  "muc": "jobs@conference.home.xlii-remote.com",
  "allowance": { "usd_left": 5.0, "per_iter_est": 0.05 },
  "specialty": "Python code review, terse",
  "skills": ["review"],
  "startup_ad": { "job": "explore", "task": "nightly wiki-drift sweep" },
  "projects": { "iXaac-lab": "/home/you/iXaac-lab" }
}
```

Empty `offers` = this box never picks up. `bash` / `lab` / harness names
are never offerable. Overlay (Tailscale) is optional.

`jobs.muc` is the room. Not OMEMO — members-only on the hub. The node
daemon joins it on connect (same JID as `xlii daemon`). File board
`~/.xlii/jobs/` is the local mirror.

Workplace `brief` (default) is the ad itself. `local-project` needs a
checkout the human already put on the node. House ads may carry
`--context`; market ads may not.

## Authority (C1)

Ops that steer the board — post (`ad`), `cancel`, `bench` — are honored
only from a MUC occupant whose affiliation is **owner** (the throne).
JSON `by` / `from` / `posted_by` are informational. A compromised node
cannot hire the fleet or cancel its neighbors. An occupant is only ever
a resource *of the room* — `someone@else/throne` is not the throne. On
join, history from a departed owner is vouched by the real JID the
service put on the stanza (XEP-0033 `ofrom`, or the `muc#user` item a
MAM-backed non-anonymous room appends) matched against the room's
owner affiliation list — not by the nick, which is reusable. If the list
cannot be fetched (`forbidden` unless the room is members-only and
non-anonymous), departed senders fail closed.

## Cancel rungs (C2)

| Rung | Verb | What it is |
|------|------|------------|
| 1 | `xlii job cancel <id>` | Withdraw the ticket. A running node aborts within one iteration. **Cooperative.** |
| 2 | `xlii job bench <node>` | Flip that node pickup → observe. Daemon stays up. **Cooperative.** |
| 3 | `xlii job evict <node>` | XEP-0045 membership revoke + kick. **Enforced** (Prosody). Not a room JSON op. |
| 4 | `kill` | The process. Unchanged. |

A hostile node can ignore cancel and bench. That is why evict exists and
is the only rung documented as enforced. Evict needs MUC owner
affiliation; it fails clearly if `jobs.muc` is empty or the extra
`xlii[daemon]` is missing.

## Beacon (C3)

A node posts `{op: offer, node, offers, gig, busy, allowance, specialty?,
skills?}` on join and whenever offers / busy / allowance change.
`allowance` mirrors the ad's `budget` vocabulary: `usd_left`,
`per_iter_est`. Match is a comparison (`ad.max_usd ≤ node.usd_left`),
not an auction. `specialty` is one free-text line; `skills` is a list of
skill *names* the bench carries — a description, not tool access.

## Pools (C4)

Pools are rooms. Nothing else hides anything. A ticket in a room this
node is not in is **absent**, not labelled hidden. The throne sits in
the rooms it creates.

## Pane (C5)

`jobs://` is the session's background-job chip (the `[jobs N]` strip).
The classifieds board is **`farm://`**: ads on the front (pool · state ·
poster · budget · claimant · age), node beacons on the flip (pool ·
offers · gig · allowance · busy/benched). The page is read-only;
throne-only buttons seed `xlii job cancel|bench|evict|post` for
review-before-run. Non-owner panes have no control buttons.

## Posters (C6, C7)

`dispatch_subagent(where=pool)` publishes an ad instead of hiring a
local worker. The `post_job` door is the same from talk. A remote post
**always confirms**; the prompt names the pool and the budget.
`auto_deny` writes no ad.

Scheduled poster: `jobs.startup_ad` fires at most once per calendar day
when the daemon joins the room (stamp `~/.xlii/jobs/startup-ad.day`).
Workers stay residents; the cron sits on the poster. `xlii pr watch`
stays an inbox producer.

## Market (M1–M9, stub)

`--kind market` is a different ticket class, never a guest in the house
pool. Agreement and payment happen **before** the door. xlii never
custodies value.

- Refused at post and at claim: `context`, `stage`, `lab`, write-shaped
  names, `local-project` workplace.
- Foreign results land in `~/.xlii/jobs/quarantine/` and are never
  auto-fused into Mojo's ambient. A market result is local only when
  this node ran the ticket itself; anything read from the room — even
  under our own nick, nicks are reusable — is foreign. Occupant nick
  must match `result.node` (same for `offer` and `claim`); unsigned
  JSON is not identity. Unknown results (no trusted ad) are dropped,
  never written to `done/`; a ticket already in `done/` is never
  rewritten. `xlii job accept <id> --fp <fp>` bumps accepted-count for
  that fingerprint only.
- `xlii job invite <fp>` opens a two-party deal room (XEP-0045) after
  terms exist off-venue.
- `market://` is a read-only offers wall of beacons + rep. Never
  tickets, never a negotiation surface.

Reputation is accepted / disputed / abandoned per OMEMO fingerprint.
Fingerprints are cheap; the wall is a phone book, not a trust oracle.
