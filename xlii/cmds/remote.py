"""``xlii remote`` — manage remote-filesystem connections (every wire) from the shell.

The CLI half of :mod:`xlii.remotefs` (``xlii ftp`` is the hidden legacy alias):
``add``/``list``/``rm`` maintain the named-connection registry (non-secrets in
config.json, the one secret in the encrypted vault), ``test`` is the
connect-and-list-root smoke check, ``publish`` mirrors a local site into a remote
docroot. This is the **scriptable flag form** of the manager (the REPL's
``/remote add`` adds a guided walk); browsing/transfer of individual files needs
no verbs here — ``xlii ls|cat|cp|mv|rm|mkdir <scheme>://<name>/…`` already work
through the provider, one address space for every backend.
"""

from __future__ import annotations

from xlii.ui import console


def cmd_add(args) -> int:
    from xlii.remotefs import add_connection, scheme_for_protocol

    import getpass

    # Validate BEFORE the secret prompt — never make the user type a password
    # into a call that was always going to fail usage validation.
    if not args.host and not args.base_url:
        console.print("[red]add failed:[/red] --host is required (or --base-url for a webdav connection)")
        return 1
    # Secret goes straight from the prompt to the vault — never argv (visible in
    # `ps`/shell history) and never config.json.
    secret = getpass.getpass(f"password/passphrase for {args.name} (empty for none): ")
    try:
        entry = add_connection(
            args.name,
            host=args.host,
            port=args.port,
            user=args.user or "",
            protocol=args.protocol,
            secret=secret or None,
            key_path=args.key_path,
            insecure=args.insecure,
            base_url=args.base_url,
            auth=args.auth,
            share=args.share,
            domain=args.domain,
        )
    except (OSError, RuntimeError, ValueError) as e:
        console.print(f"[red]add failed:[/red] {e}")
        return 1
    scheme = scheme_for_protocol(entry.get("protocol", "ftp"))
    console.print(f"[green]✓[/green] saved [cyan]{args.name}[/cyan] "
                  f"[dim]({scheme}://{entry.get('host') or entry.get('base_url', '?')}"
                  f"{':' + str(entry['port']) if entry.get('port') else ''} · "
                  f"{entry.get('protocol')} · "
                  f"secret {'in the vault' if secret else 'none'})[/dim]")
    console.print(f"[dim]smoke check: [/dim][cyan]xlii remote test {args.name}[/cyan]")
    return 0


def cmd_list(args) -> int:
    from xlii.remotefs import manager, scheme_for_protocol

    names = manager.names()
    if not names:
        console.print("[dim](no remote connections configured — "
                      "[/dim][cyan]xlii remote add <name> --host <host>[/cyan][dim])[/dim]")
        return 0
    for n in names:
        spec = manager.spec(n) or {}
        scheme = scheme_for_protocol(spec.get("protocol", "ftp"))
        user = f"{spec.get('user')}@" if spec.get("user") else ""
        # A base_url-only connection (webdav) has no host — show the URL instead
        # (the REPL sibling's convention), and skip the :port suffix (the URL
        # already carries any non-default port).
        target = spec.get("host") or spec.get("base_url", "?")
        port = f":{spec['port']}" if spec.get("port") and spec.get("host") else ""
        extras = " · key" if spec.get("key_path") else ""
        extras += " · insecure-tls" if spec.get("insecure") else ""
        console.print(f"[cyan]{n}[/cyan]  [dim]{scheme}://"
                      f"{user}{target}{port} "
                      f"({spec.get('protocol', 'ftp')}){extras}[/dim]")
    return 0


def cmd_rm(args) -> int:
    from xlii.remotefs import remove_connection

    if not args.yes:
        console.print(f"[yellow]{args.name}[/yellow]: pass --yes to confirm removal "
                      "(drops the config entry and its vault secret)")
        return 1
    if not remove_connection(args.name):
        console.print(f"[red]no connection named {args.name!r}[/red]")
        return 1
    console.print(f"[green]removed[/green] {args.name}")
    return 0


def cmd_test(args) -> int:
    from xlii.remotefs import manager, scheme_for_protocol

    try:
        conn = manager.get(args.name)
        entries = conn.listdir("")
    except (OSError, RuntimeError, ValueError) as e:
        console.print(f"[red]✗ {args.name}:[/red] {e}")
        return 1
    finally:
        manager.close(args.name)
    scheme = scheme_for_protocol(conn.protocol)
    # base_url-only connections (webdav) have no host — name the URL, not ":443".
    where = (f"{conn.protocol}://{conn.host}:{conn.port}" if conn.host
             else conn.spec.get("base_url", "?"))
    console.print(f"[green]✓[/green] [cyan]{args.name}[/cyan] connected "
                  f"[dim]({where}) — "
                  f"{len(entries)} entries in the login home[/dim]")
    for name, is_dir, size in entries[:10]:
        console.print(f"  {name}{'/' if is_dir else ''}"
                      + ("" if size is None else f"  [dim]{size}[/dim]"))
    if len(entries) > 10:
        console.print(f"  [dim]… {len(entries) - 10} more — xlii ls {scheme}://{args.name}/[/dim]")
    return 0


def cmd_publish(args) -> int:
    from pathlib import Path

    from xlii.addressing import Address
    from xlii.remotefs import REMOTE_SCHEMES, manager, publish, scheme_for_protocol

    dest = args.dest
    if "://" not in dest:
        # name/docroot shorthand → the connection's honest scheme
        spec = manager.spec(dest.split("/", 1)[0]) or {}
        dest = f"{scheme_for_protocol(spec.get('protocol', 'ftp'))}://{dest}"
    addr = Address.parse(dest)
    if addr.scheme not in REMOTE_SCHEMES or not addr.key.strip():
        console.print(f"[red]{args.dest}[/red]: destination must be <scheme>://<name>/<docroot>")
        return 1
    try:
        conn = manager.get(addr.key.strip())
        uploaded, skipped, deleted = publish(
            conn, Path(args.local), addr.subpath,
            delete=bool(getattr(args, "delete", False)),
            progress=lambda rel, status: console.print(f"  [dim]{status:>8}[/dim]  {rel}"),
        )
    except (OSError, RuntimeError, ValueError) as e:
        console.print(f"[red]publish failed:[/red] {e}")
        return 1
    finally:
        manager.close(addr.key.strip())
    pruned = f" · {deleted} deleted" if getattr(args, "delete", False) else ""
    console.print(f"[green]✓[/green] published [cyan]{args.local}[/cyan] → [cyan]{addr}[/cyan] "
                  f"[dim]({uploaded} uploaded · {skipped} unchanged{pruned})[/dim]")
    return 0


def cmd_unpublish(args) -> int:
    from xlii.addressing import Address
    from xlii.remotefs import REMOTE_SCHEMES, _normalize_remote_subpath, manager, scheme_for_protocol, unpublish

    dest = args.dest
    if "://" not in dest:
        spec = manager.spec(dest.split("/", 1)[0]) or {}
        dest = f"{scheme_for_protocol(spec.get('protocol', 'ftp'))}://{dest}"
    addr = Address.parse(dest)
    if (addr.scheme not in REMOTE_SCHEMES or not addr.key.strip()
            or not _normalize_remote_subpath(addr.subpath)):
        console.print(f"[red]{args.dest}[/red]: destination must be <scheme>://<name>/<docroot>")
        return 1
    if not args.yes:
        console.print(f"[yellow]{addr}[/yellow]: pass --yes to confirm "
                      "(recursively deletes the remote docroot)")
        return 1
    try:
        conn = manager.get(addr.key.strip())
        unpublish(conn, addr.subpath)
    except FileNotFoundError:
        console.print(f"[red]{addr}[/red]: no such remote path")
        return 1
    except (OSError, RuntimeError, ValueError) as e:
        console.print(f"[red]unpublish failed:[/red] {e}")
        return 1
    finally:
        manager.close(addr.key.strip())
    console.print(f"[green]✓[/green] unpublished [cyan]{addr}[/cyan]")
    return 0


def register(sub) -> None:
    p = sub.add_parser(
        "remote",
        aliases=["ftp"],  # hidden-ish legacy spelling; the docs teach `remote`
        help="Remote hosts (FTP/FTPS/SFTP + growing): named connections, smoke test, site "
             "publish (browse with `xlii ls <scheme>://<name>/`).",
    )
    f = p.add_subparsers(dest="remote_cmd", required=True)

    p_add = f.add_parser("add", help="Add/replace a named connection (secret prompted, stored in the vault).")
    p_add.add_argument("name", help="Connection name — becomes <scheme>://<name>/…")
    p_add.add_argument("--host", default="", help="Remote hostname or IP (webdav may give --base-url instead)")
    p_add.add_argument("--port", type=int, default=None, help="Port (per-protocol default: 21/22/443/445)")
    p_add.add_argument("--user", default="", help="Login user (empty → anonymous FTP)")
    p_add.add_argument("--protocol", choices=["ftp", "ftps", "sftp", "webdav", "smb"], default="ftp",
                       help="Wire protocol for this connection (decides its scheme: ftp/sftp/dav/smb)")
    p_add.add_argument("--key-path", default=None, help="SSH private key file (sftp; secret becomes its passphrase)")
    p_add.add_argument("--insecure", action="store_true",
                       help="Skip TLS certificate verification (ftps/webdav; webdav: also allow plain http)")
    p_add.add_argument("--base-url", default="",
                       help="WebDAV base URL, may carry a path "
                            "(https://cloud.example.com/remote.php/dav/files/me — replaces --host)")
    p_add.add_argument("--auth", default=None, choices=["basic", "digest", "bearer"],
                       help="WebDAV auth mode (default basic; bearer sends the vault secret as a token)")
    p_add.add_argument("--share", default=None, help="Share name (smb only; required for that wire)")
    p_add.add_argument("--domain", default=None, help="Domain/workgroup (smb only; optional)")
    p_add.set_defaults(func=cmd_add)

    p_list = f.add_parser("list", help="List configured connections (no secrets shown).")
    p_list.set_defaults(func=cmd_list)

    p_rm = f.add_parser("rm", help="Remove a connection and its vault secret.")
    p_rm.add_argument("name", help="Connection name")
    p_rm.add_argument("--yes", "-y", action="store_true", help="Confirm the removal")
    p_rm.set_defaults(func=cmd_rm)

    p_test = f.add_parser("test", help="Smoke check: connect and list the login home.")
    p_test.add_argument("name", help="Connection name")
    p_test.set_defaults(func=cmd_test)

    from xlii.remotefs import _DOCROOT_RELATIVE

    p_pub = f.add_parser("publish", help="Mirror a local dir into a remote docroot (skips size-unchanged files; "
                                         f"{_DOCROOT_RELATIVE}).")
    p_pub.add_argument("local", help="Local directory (the built site)")
    p_pub.add_argument("dest", help=f"Destination — <scheme>://<name>/<docroot> (or <name>/<docroot>; "
                                    f"{_DOCROOT_RELATIVE})")
    p_pub.add_argument("--delete", action="store_true",
                       help="Prune remote files/dirs not present locally (clean redeploy)")
    p_pub.set_defaults(func=cmd_publish)

    p_unpub = f.add_parser("unpublish", help=f"Recursively delete a published remote docroot ({_DOCROOT_RELATIVE}).")
    p_unpub.add_argument("dest", help=f"Destination — <scheme>://<name>/<docroot> (or <name>/<docroot>; "
                                     f"{_DOCROOT_RELATIVE})")
    p_unpub.add_argument("--yes", action="store_true", help="Confirm the recursive remote delete")
    p_unpub.set_defaults(func=cmd_unpublish)
