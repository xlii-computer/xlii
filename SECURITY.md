# Security Policy

## Supported Versions

xlii is pre-1.0 and under active development; there are no long-term support
branches yet. Security fixes land on the latest release line and `main` only —
if you are on an older `0.x`, upgrade before reporting.

| Version            | Supported          |
| ------------------ | ------------------ |
| `main` (unreleased)| :white_check_mark: |
| 0.5.x (latest)     | :white_check_mark: |
| < 0.5              | :x:                |

Runtime support tracks `requires-python` in `pyproject.toml` (currently
**Python 3.11+**). Vulnerabilities that only reproduce on unsupported Python
versions are out of scope.

## Reporting a Vulnerability

**Please do not open a public GitHub issue or PR for security problems** — that
discloses the issue before there is a fix.

Report privately through either channel:

- **GitHub** — *Security → Report a vulnerability* (private advisory), if enabled
  on the repo. Preferred, since it keeps the report attached to the code.
- **Email** — **hello@xlii.computer**. Use the subject line `xlii security`.

Helpful things to include (only what you have):

- A description of the issue and the impact you think it has.
- Steps to reproduce, or a minimal proof of concept.
- The xlii version (`xlii --version` or `xlii.__version__`), OS, and Python version.
- Any relevant config — **with real secrets redacted** (see below).

### What to expect

- **Acknowledgement within 3 business days** that the report was received.
- A follow-up with an initial assessment (accepted / needs-info / declined) and,
  if accepted, a rough remediation timeline.
- Coordinated disclosure: we will agree on a disclosure window with you and
  credit you in the fix notes unless you prefer to stay anonymous.

This is a small project — timelines are best-effort, not contractual.

## Handling secrets (important for this tool)

xlii stores and migrates provider API keys (e.g. xAI keys via its local vault).
When reporting:

- **Never paste a real API key, token, or `.xlii/` vault contents** into an issue,
  email, or log excerpt. Redact them.
- If you believe a credential was exposed *by* xlii (written to disk in the clear,
  leaked to a log, sent to the wrong endpoint), treat it as in-scope and report it
  privately — and rotate the affected key on the provider's side immediately.

## Scope

**In scope** — issues in this repository's code, for example:

- Credential handling: keys written in plaintext, leaked to logs/stdout, or sent
  to an unintended destination.
- Command/argument injection via untrusted input (shell steps, task pipelines,
  harness/delegate invocations).
- Path traversal or unintended file read/write outside the project sandbox.
- Insecure deserialization, SSRF, or auth bypass in any networked feature
  (daemon, MCP/ACP surfaces, sync).

**Out of scope** — for example:

- Vulnerabilities in third-party dependencies with no xlii-specific exploit path
  (report those upstream; our CI dependency scan tracks fixable CVEs).
- Findings that require an already-compromised host or local root.
- Social engineering, and missing hardening that has no concrete exploit.

## Automated scanning

Every push and pull request to `main` runs secret, dependency, and SAST scans
(Gitleaks, Trivy, Semgrep) that fail the build on findings — see
[`.github/workflows/security-scans.yml`](.github/workflows/security-scans.yml).
This complements, and does not replace, responsible disclosure.
