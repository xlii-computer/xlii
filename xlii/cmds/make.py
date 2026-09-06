"""`xlii make` — S4: build a static web app from a description and publish it live.

One gesture over the shipped seams (proposals/app-serving.md S4): scaffold a
throwaway local project (`init --local` — no Collection, no keys), have the
iXaac agent BUILD a self-contained static app into it (the same agent turn
`xlii ask` runs), then PUBLISH it to the apps box (the `publish()` mirror) so it
serves at https://<name>.<domain>. Build + publish are the shipped seams; this
only orchestrates create→build→publish and reports the URL.

The build turn runs with `require_management=False`: a static-app build needs no
Collections, and the body that builds (box #1) holds only the inference key.
Everything the build prints goes to stderr, so stdout carries only the final
URL — a caller (the phone `make.sh` verb, a script) can capture it cleanly.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# The app-operator house rules (stock_roles/app-operator.md), condensed into the
# build goal so `make` produces the right shape without equipping the role.
_BUILD_PREAMBLE = (
    "Build a complete, self-contained static web app in the current folder. "
    "Requirements: a single index.html with ALL CSS and JS inlined; NO external "
    "dependencies, CDNs, fonts, or network calls of any kind; responsive (include "
    "a viewport meta tag); a clean dark theme. Write the file(s) directly to disk "
    "with your tools — do not just show code or explain. The app to build: "
)

DEFAULT_APPS_DIR = "~/serve-sandbox"
DEFAULT_DOMAIN = "xlii-code.com"
DEFAULT_CONN = "appbox"
DEFAULT_ROOT = "srv/apps"

# Subdomain-safe: a DNS label (lowercase, digits, hyphens; not hyphen-edged).
_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def register(sub) -> None:
    p = sub.add_parser(
        "make",
        help="Build a static web app from a description and publish it live "
             "(scaffold → iXaac builds → publish to <name>.<domain>).",
        description="S4 instant-apps: one gesture from a description to a live URL. "
                    "iXaac builds a self-contained static app, then it is published to "
                    "the apps box so it serves at https://<name>.<domain>.",
    )
    p.add_argument("name", help="App name → the subdomain + folder (<name>.<domain>)")
    p.add_argument("description", help='What to build, e.g. "a tip calculator"')
    p.add_argument("--domain", default=DEFAULT_DOMAIN, help=f"Apps domain (default: {DEFAULT_DOMAIN})")
    p.add_argument("--conn", default=DEFAULT_CONN,
                   help=f"Remote connection to the apps box (xlii remote add; default: {DEFAULT_CONN})")
    p.add_argument("--root", default=DEFAULT_ROOT,
                   help=f"Remote apps root under the login home (default: {DEFAULT_ROOT})")
    p.add_argument("--dir", dest="apps_dir", default=DEFAULT_APPS_DIR,
                   help=f"Local dir that holds app folders (default: {DEFAULT_APPS_DIR})")
    p.add_argument("--no-publish", action="store_true", dest="no_publish",
                   help="Build locally only; don't publish (prints the local path)")
    p.add_argument("--no-yolo", action="store_false", dest="yolo",
                   help="Prompt before bash during the build (default: auto-approve — "
                        "this is a hands-off one-gesture build in a throwaway dir)")
    p.set_defaults(func=cmd_make, yolo=True)


def cmd_make(args: argparse.Namespace) -> int:
    from rich.console import Console

    ui = Console(stderr=True)  # all build/publish UI → stderr; stdout = the URL only

    name = args.name.strip().lower()
    if not _NAME_RE.match(name) or len(name) > 63:
        print(f"make: invalid app name {args.name!r} — use a DNS label "
              "(lowercase letters, digits, hyphens; not hyphen-edged)", file=sys.stderr)
        return 1

    app_dir = (Path(args.apps_dir).expanduser() / name)

    # 1. Scaffold a throwaway local project (no Collection, no keys needed).
    if not _scaffold(app_dir, name, ui):
        return 1

    # 2. Build: run the iXaac agent turn INTO the app dir.
    goal = _BUILD_PREAMBLE + args.description.strip()
    if _build(app_dir, goal, yolo=getattr(args, "yolo", True), ui=ui) != 0:
        return 1
    if not any(app_dir.glob("index.htm*")):
        ui.print("[yellow]make: the build produced no index.html — nothing to publish[/yellow]")
        return 1

    host = f"{name}.{args.domain}"

    # 3. Publish (unless --no-publish). Print the URL (or local path) to stdout.
    if args.no_publish:
        ui.print(f"[green]built[/green] {host} [dim](not published)[/dim] → {app_dir}")
        print(str(app_dir))
        return 0
    if _publish(app_dir, args.conn, args.root, host, ui) != 0:
        return 1
    url = f"https://{host}"
    ui.print(f"[green]live[/green] → {url}")
    print(url)
    return 0


def _scaffold(app_dir: Path, name: str, ui) -> bool:
    """Create app_dir as a local-only xlii project (idempotent). The build turn
    needs an initialized project (a `.xlii/`) to run in; local-only means no
    Collection and no management key."""
    from xlii.config import ProjectConfig
    from xlii.sync import init_project

    try:
        app_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        ui.print(f"[red]make: cannot create {app_dir}: {e}[/red]")
        return False
    if ProjectConfig.load(app_dir):
        return True  # already a project — reuse it (rebuild in place)
    try:
        init_project(None, app_dir, name=name, local_only=True)
    except Exception as e:
        ui.print(f"[red]make: scaffold failed: {type(e).__name__}: {e}[/red]")
        return False
    return True


def _build(app_dir: Path, goal: str, *, yolo: bool, ui) -> int:
    """Run one iXaac agent turn over the app project — the same machinery as
    `xlii ask`, but built with `require_management=False` (a static build needs
    no Collections, and the building body holds only the inference key) and with
    all output on stderr."""
    from xlii.agent import Agent, SessionState
    from xlii.client import MissingCredentials
    from xlii.config import GlobalConfig, ProjectConfig
    from xlii.pool import ClientPool

    project = ProjectConfig.load(app_dir)
    if not project:
        ui.print(f"[red]make: {app_dir} is not an xlii project[/red]")
        return 1
    cfg = GlobalConfig.load()
    try:
        pool = ClientPool.from_config(cfg, require_management=False)
    except MissingCredentials as e:
        ui.print(f"[red]make: {e}[/red]")
        return 1
    agent = Agent(pool=pool, project=project, cfg=cfg, console=ui,
                  session=SessionState.from_flat(yolo=yolo))
    try:
        agent.run_turn(goal)
    except Exception as e:
        ui.print(f"[red]make: build error: {type(e).__name__}: {e}[/red]")
        return 1
    return 0


def _publish(app_dir: Path, conn_name: str, root: str, host: str, ui) -> int:
    """Mirror the built app to the apps box at <root>/<host>/ (redeploy
    semantics — prune stale files from a previous version)."""
    from xlii.remotefs import manager, publish

    try:
        conn = manager.get(conn_name)
    except Exception as e:
        ui.print(f"[red]make: connect to {conn_name!r} failed ({type(e).__name__}: {e}) — "
                 f"is it configured? `xlii remote add {conn_name} …`[/red]")
        return 1
    remote_base = f"{root.strip('/')}/{host}"
    try:
        uploaded, skipped, deleted = publish(conn, app_dir, remote_base, delete=True)
    except Exception as e:
        ui.print(f"[red]make: publish failed ({type(e).__name__}: {e})[/red]")
        return 1
    ui.print(f"[dim]make: published {host} → {uploaded} uploaded, {skipped} unchanged, "
             f"{deleted} pruned[/dim]")
    try:
        from xlii.config import ProjectConfig
        from xlii.desk_files import bind_files_root

        proj = ProjectConfig.load(app_dir)
        if proj is not None:
            bind_files_root(proj, f"sftp://{conn_name}/{remote_base}")
    except Exception as e:
        ui.print(f"[dim]make: files pointer not saved ({type(e).__name__}: {e})[/dim]")
    return 0
