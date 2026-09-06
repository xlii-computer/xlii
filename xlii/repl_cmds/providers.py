"""/providers — the data-provider substrate on the command line (typed-workbenches S2).

The runner is xlii/providers.py (S0); this is its honest UI: list the
manifests with their configured/quota state, run one, scaffold a new one.
BYO stays data: a provider is a TOML manifest in .xlii/providers/ — the
`author-provider` stock skill teaches the agent to write one from API docs.
"""

from __future__ import annotations

import os
import shlex
from pathlib import Path
from typing import Any, Optional

from xlii.commands import REPLCommand, register_repl_command
from xlii.providers import (
    ProviderError,
    get_manifest,
    load_manifests,
    quota_bucket,
    run_provider,
    usage_today,
    validate_manifest,
)


def _xli_dir(ctx: dict[str, Any]) -> Optional[Path]:
    project = ctx.get("project")
    xli_dir = getattr(project, "xli_dir", None) if project is not None else None
    return Path(xli_dir) if xli_dir is not None else None


def _key_status(m) -> str:
    if not m.auth.kind:
        return "—"
    return "✓" if os.environ.get(m.auth.key_env, "").strip() else "✗ " + m.auth.key_env


def _today(m, xli_dir) -> str:
    if m.quota.daily <= 0:
        return ""
    # the shared account bucket, not the manifest name — sibling manifests on
    # one key spend one budget (xlii.providers.quota_bucket)
    return f"{usage_today(xli_dir, quota_bucket(m))}/{m.quota.daily}"


def _list(console, xli_dir) -> None:
    reg = load_manifests(xli_dir)
    console.print(f"[cyan]{len(reg)} providers[/cyan] [dim](/providers run <name> [k=v…] · /providers new <name> <url> · show <name>)[/dim]")
    for m in sorted(reg.values(), key=lambda x: (x.category, x.name)):
        src = "" if m.source == "builtin" else " [dim](project)[/dim]"
        quota = _today(m, xli_dir)
        console.print(
            f"  [cyan]{m.name:<28}[/cyan] {m.category:<11} key {_key_status(m):<20}"
            + (f" today {quota}" if quota else "")
            + src
        )


def _run(console, xli_dir, args: list[str]) -> None:
    if not args:
        console.print("[yellow]usage: /providers run <name> [k=v …][/yellow]")
        return
    name, pairs = args[0], args[1:]
    params = {}
    for p in pairs:
        if "=" not in p:
            console.print(f"[yellow]bad param {p!r} — expected k=v[/yellow]")
            return
        k, v = p.split("=", 1)
        params[k] = v
    if xli_dir is None:
        console.print("[yellow]/providers run needs a project context[/yellow]")
        return
    try:
        result = run_provider(name, xli_dir, params=params)
    except ProviderError as e:
        console.print(f"[yellow]{e}[/yellow]")
        return
    except Exception as e:  # noqa: BLE001
        console.print(f"[yellow]provider run failed: {type(e).__name__}: {e}[/yellow]")
        return
    console.print(
        f"[green]{result.provider}[/green]: {result.count} records over "
        f"{result.pages} page(s)"
        + (f" · {result.quota_note}" if result.quota_note else "")
    )
    for rec in result.records[:5]:
        console.print(f"  [dim]{str(rec)[:140]}[/dim]")
    if result.count > 5:
        console.print(f"  [dim]… {result.count - 5} more in {result.latest_path}[/dim]")


_SCAFFOLD = '''name = "{name}"
version = 1
summary = "TODO — what this provider returns"
category = "general"
# required_params = ["query"]

[request]
method = "GET"
url = "{url}"

# [request.params]
# q = "{{query}}"

# [auth]            # a key the runner injects from the environment
# kind = "query"    # "header" | "query"
# key_env = "{env}" # the env var NAME — never the value
# param = "api_key"
# required = true

# [quota]
# daily = 500       # advisory; the counter is shared by key_env (see providers.py)

[shape]
records = ""        # dotted path to the record list; "" = root

# [shape.fields]    # optional projection: out_key = "dotted.path"
'''


def _new(console, xli_dir, args: list[str]) -> None:
    if len(args) < 2:
        console.print("[yellow]usage: /providers new <name> <url>[/yellow]")
        return
    if xli_dir is None:
        console.print("[yellow]/providers new needs a project context[/yellow]")
        return
    name, url = args[0].strip().lower(), args[1].strip()
    if not name.replace("-", "").replace("_", "").isalnum():
        console.print(f"[yellow]bad provider name {name!r}[/yellow]")
        return
    if not url.startswith(("http://", "https://")):
        console.print("[yellow]url must be http(s)[/yellow]")
        return
    if get_manifest(name, xli_dir) is not None:
        console.print(f"[yellow]provider {name!r} already exists[/yellow]")
        return
    env = name.upper().replace("-", "_") + "_API_KEY"
    mdir = xli_dir / "providers"
    mdir.mkdir(parents=True, exist_ok=True)
    dest = mdir / f"{name}.toml"
    dest.write_text(_SCAFFOLD.format(name=name, url=url, env=env))
    m = get_manifest(name, xli_dir)
    problems = validate_manifest(m) if m else ["did not parse"]
    if problems:
        console.print(f"[yellow]scaffolded {dest} but it needs attention: {'; '.join(problems)}[/yellow]")
    else:
        console.print(f"[green]scaffolded {dest}[/green] — edit the summary/params/shape, then [cyan]/providers run {name}[/cyan]")


def _show(console, xli_dir, args: list[str]) -> None:
    if not args:
        console.print("[yellow]usage: /providers show <name>[/yellow]")
        return
    m = get_manifest(args[0], xli_dir)
    if m is None:
        console.print(f"[yellow]unknown provider {args[0]!r}[/yellow]")
        return
    console.print(f"[cyan]{m.name}[/cyan] ({m.source}) — {m.summary}")
    console.print(f"  {m.method} {m.url}")
    if m.request_params:
        console.print(f"  params: {dict(m.request_params)}")
    if m.required_params:
        console.print(f"  required: {', '.join(m.required_params)}")
    console.print(f"  key: {_key_status(m)}")
    if m.quota.daily:
        console.print(f"  quota: {_today(m, xli_dir)} today (daily {m.quota.daily})")
    console.print(f"  records: {m.records_path or '<root>'}" + (f" → {dict(m.fields)}" if m.fields else ""))


def h_providers(line: str, ctx: dict[str, Any]) -> bool:
    console = ctx["console"]
    xli_dir = _xli_dir(ctx)
    try:
        # shlex so quoted values round-trip: query="climate change"
        parts = shlex.split(line)
    except ValueError:
        console.print("[yellow]unbalanced quotes in /providers[/yellow]")
        return True
    sub = parts[1].strip().lower() if len(parts) > 1 else ""
    rest = parts[2:]
    if not sub:
        _list(console, xli_dir)
    elif sub == "run":
        _run(console, xli_dir, rest)
    elif sub == "new":
        _new(console, xli_dir, rest)
    elif sub == "show":
        _show(console, xli_dir, rest)
    else:
        # bare "/providers <name>" is sugar for run — the common case
        _run(console, xli_dir, [sub] + rest)
    return True


def register() -> None:
    register_repl_command(
        REPLCommand(
            name="providers",
            handler=h_providers,
            usage="/providers [run <name> [k=v…] | new <name> <url> | show <name>]",
            description=(
                "Data providers — list manifests (key/quota state), run one "
                "into .xlii/provider-results/, scaffold a BYO manifest"
            ),
            category="knowledge",
            # Code REPL only: /providers reaches the network and writes into
            # the project (.xlii/providers/, .xlii/provider-results/) — both
            # outside the chat surface's project-blind contract, which gates
            # its slash commands through mode_contract.CHAT_SLASH_COMMANDS.
            repls=["code"],
        )
    )
