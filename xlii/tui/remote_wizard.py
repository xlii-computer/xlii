"""``RemoteAddWizard`` — the one-screen guided ``/remote add`` in Pane 2.

Why a PANEL and not the modal chain: the per-question modals exist to serve a
*blocked worker thread* mid-command (the request_line seam that fixed the
guided-add hang). The wizard **inverts that control**: nothing blocks — the
panel owns the whole flow, the user fills one form (transcript still visible,
so a hostname can be copied from earlier output), and ``add_connection`` runs
once on Save, off the UI thread. No busy spinner, Esc just closes a pane. The
serial-modal walk remains the fallback when no panel host is live.

Field source of truth: the SAME tables the guided walk uses
(``repl_cmds.remote.GUIDED_COMMON`` / ``GUIDED_EXTRA``), so the wizard and the
walk can never drift — pick a protocol and only that wire's fields render.

Auditability: on Save the equivalent **flag-form command is echoed to the
transcript with the secret omitted** (the secret goes masked-field → vault,
never the transcript — the standing contract).

Registration is a published-function call (``register_remote_wizard`` →
``panels.register_panel_view``) from *this* module; textual-guarded exactly like
``plugins_panel`` so the registry stays import-light without the ``[tui]``
extra (the provider degrades to ``None``).
"""

from __future__ import annotations

from typing import Any, Optional


def register_remote_wizard(name: str = "remote-add") -> None:
    """Register the wizard under the panel-view registry (additive, last-wins).
    Called from the ``/remote`` command's ``register()``."""
    from xlii.tui import panels

    panels.register_panel_view(name, remote_wizard_view)


# --------------------------------------------------------------------------- #
#  Pure helpers — textual-free, unit-testable
# --------------------------------------------------------------------------- #

def wizard_fields(protocol: str) -> "list[tuple[str, str, bool]]":
    """The ``(field, label, required)`` rows for a protocol — shared tables, with
    the same webdav host-optional rule the walk applies."""
    from xlii.repl_cmds.remote import GUIDED_COMMON, GUIDED_EXTRA

    rows = []
    for field, label, required in GUIDED_COMMON + GUIDED_EXTRA.get(protocol, ()):
        if field == "host" and protocol == "webdav":
            required = False  # webdav may use base_url instead
        rows.append((field, label, required))
    return rows


def collect_opts(protocol: str, values: "dict[str, str]") -> "tuple[Optional[dict], Optional[str]]":
    """Turn the form's raw strings into ``add_connection`` kwargs.

    Returns ``(opts, error)``: empty fields are dropped, ``insecure`` parses its
    y/n-ish spelling, a missing required field names itself. Mirrors the guided
    walk's semantics exactly (the two must never drift)."""
    opts: dict[str, Any] = {"protocol": protocol}
    for field, _label, required in wizard_fields(protocol):
        raw = (values.get(field) or "").strip()
        if not raw and required:
            return None, f"{field} is required"
        if raw:
            opts[field] = (raw.lower() in ("y", "yes", "true", "1")) if field == "insecure" else raw
    if protocol == "webdav" and not opts.get("host") and not opts.get("base_url"):
        return None, "host or base_url is required"
    return opts, None


def build_flag_echo(name: str, opts: dict) -> str:
    """The audit line: the equivalent flag-form command, SECRET OMITTED."""
    parts = [f"/remote add {name}"]
    proto = opts.get("protocol", "ftp")
    if proto != "ftp":
        parts.append(f"--protocol {proto}")
    flag_names = {"host": "--host", "port": "--port", "user": "--user",
                  "key_path": "--key-path", "base_url": "--base-url", "auth": "--auth",
                  "share": "--share", "domain": "--domain"}
    for field, flag in flag_names.items():
        v = opts.get(field)
        if v:
            parts.append(f"{flag} {v}")
    if opts.get("insecure"):
        parts.append("--insecure")
    return " ".join(parts)


# --------------------------------------------------------------------------- #
#  The widget (textual-only, guarded exactly like plugins_panel)
# --------------------------------------------------------------------------- #

try:
    from textual.binding import Binding
    from textual.containers import Horizontal, Vertical
    from textual.widgets import Button, Input, Select, Static

    _TEXTUAL = True
except Exception:  # pragma: no cover - exercised only without [tui]
    _TEXTUAL = False


if _TEXTUAL:

    class RemoteAddWizard(Vertical):
        """The form: name · protocol · that wire's fields · masked secret ·
        Save / Save & connect / Cancel."""

        DEFAULT_CSS = """
        RemoteAddWizard {
            padding: 1;
            height: auto;
        }
        RemoteAddWizard .wiz-label { color: $text-muted; margin-top: 1; }
        RemoteAddWizard #wiz-title { text-style: bold; }
        RemoteAddWizard #wiz-buttons { height: auto; margin-top: 1; }
        RemoteAddWizard #wiz-buttons Button { margin-right: 1; min-width: 8; }
        RemoteAddWizard #wiz-error { color: $error; height: auto; }
        """

        BINDINGS = [Binding("escape", "cancel", "close", show=False)]

        def __init__(self, state: Any, *, prefill_name: str = "") -> None:
            super().__init__()
            self._state = state
            self._prefill = prefill_name

        # -- layout -------------------------------------------------------- #

        def compose(self):
            from xlii.remotefs import PROTOCOLS

            yield Static("Add a remote connection", id="wiz-title")
            yield Static("name", classes="wiz-label")
            yield Input(value=self._prefill, placeholder="connection name", id="wiz-name")
            yield Static("protocol", classes="wiz-label")
            yield Select(((p, p) for p in PROTOCOLS), value="ftp",
                         allow_blank=False, id="wiz-protocol")
            yield Vertical(id="wiz-fields")
            yield Static("password / passphrase (empty for none — straight to the vault)",
                         classes="wiz-label")
            yield Input(password=True, id="wiz-secret")
            yield Static("", id="wiz-error")
            with Horizontal(id="wiz-buttons"):
                yield Button("Save", variant="primary", id="wiz-save")
                yield Button("Save & connect", id="wiz-save-test")
                yield Button("Cancel", id="wiz-cancel")

        async def on_mount(self) -> None:
            self._rendered_proto: Optional[str] = None
            await self._render_fields("ftp")
            target = self.query_one("#wiz-protocol" if self._prefill else "#wiz-name")
            target.focus()

        async def _render_fields(self, protocol: str) -> None:
            """Rebuild the per-wire rows — pick a protocol, see only its fields.
            remove_children is awaited BEFORE mounting (same-ID collision
            otherwise) and a same-protocol re-render is a no-op (the Select
            fires an initial Changed on mount)."""
            if getattr(self, "_rendered_proto", None) == protocol:
                return
            self._rendered_proto = protocol
            box = self.query_one("#wiz-fields", Vertical)
            await box.remove_children()
            for field, label, required in wizard_fields(protocol):
                await box.mount(Static(f"{label}{' *' if required else ''}", classes="wiz-label"))
                await box.mount(Input(id=f"wiz-f-{field}"))

        async def on_select_changed(self, event) -> None:
            if event.select.id == "wiz-protocol" and event.value is not None:
                await self._render_fields(str(event.value))

        # -- act ------------------------------------------------------------ #

        def _gather(self) -> "tuple[Optional[str], Optional[dict], Optional[str]]":
            name = self.query_one("#wiz-name", Input).value.strip()
            if not name:
                return None, None, "name is required"
            protocol = str(self.query_one("#wiz-protocol", Select).value or "ftp")
            values = {
                field: self.query_one(f"#wiz-f-{field}", Input).value
                for field, _l, _r in wizard_fields(protocol)
            }
            opts, err = collect_opts(protocol, values)
            if err:
                return None, None, err
            opts["secret"] = self.query_one("#wiz-secret", Input).value or None
            return name, opts, None

        def on_button_pressed(self, event) -> None:
            bid = event.button.id
            if bid == "wiz-cancel":
                self.action_cancel()
                return
            if bid in ("wiz-save", "wiz-save-test"):
                name, opts, err = self._gather()
                if err:
                    self.query_one("#wiz-error", Static).update(err)
                    return
                # Off the UI thread: add_connection does vault + config I/O (and
                # Save & connect dials the host). The panel closes on success.
                self.app.run_worker(
                    lambda: self._do_save(name, opts, test=(bid == "wiz-save-test")),
                    thread=True,
                )

        def _do_save(self, name: str, opts: dict, *, test: bool) -> None:
            from xlii.remotefs import add_connection, manager, scheme_for_protocol

            console = getattr(self._state, "console", None)

            def _say(msg: str) -> None:
                if console is not None:
                    console.print(msg)

            secret = opts.get("secret")
            try:
                entry = add_connection(name, **opts)
            except (OSError, RuntimeError, ValueError) as e:
                self.app.call_from_thread(
                    self.query_one("#wiz-error", Static).update, f"add failed: {e}")
                return
            scheme = scheme_for_protocol(entry.get("protocol", "ftp"))
            # The audit echo: the equivalent command, secret omitted by design.
            _say(f"[dim]{build_flag_echo(name, opts)}[/dim]")
            _say(f"[green]✓[/green] saved [cyan]{name}[/cyan] "
                 f"[dim](secret {'in the vault' if secret else 'none'})[/dim] — "
                 f"open [cyan]{scheme}://{name}[/cyan] in a pane")
            if test:
                try:
                    conn = manager.get(name)
                    n = len(conn.listdir(""))
                    _say(f"[green]✓[/green] connected [dim]({conn.protocol}://{conn.host}:"
                         f"{conn.port}) — {n} entries in the login home[/dim]")
                except (OSError, RuntimeError, ValueError) as e:
                    _say(f"[red]✗ connect failed:[/red] {e}")
            self.app.call_from_thread(self._close)

        def action_cancel(self) -> None:
            self._close()

        def _close(self) -> None:
            from xlii.tui import panels

            panels.hide_panel()


def remote_wizard_view(state: Any, *, actions: Any = None) -> Any:
    """Panel-view provider (the ``register_panel_view`` shape). Reads the one-shot
    prefill name the command stashed on state (the ephemeral-attr convention,
    like ``state.elevated``); degrades to ``None`` without the [tui] extra."""
    if not _TEXTUAL:
        return None
    prefill = getattr(state, "_remote_wizard_prefill", "") or ""
    if prefill:
        state._remote_wizard_prefill = ""
    return RemoteAddWizard(state, prefill_name=prefill)
