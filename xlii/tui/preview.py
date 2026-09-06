"""The file/attachment surface — preview · edit · discuss · author (seam #2).

This is the published seam for *opening any one thing in a focused surface*:

- ``register_preview(kind, provider)`` / ``preview_for(kind, payload)`` — a
  registry that turns a (kind, payload) into a Rich renderable. The built-ins
  (doc · skill · role · ref · image) are the first client (``preview_providers``);
  other vectors register their own kinds **from their own files** (B a captured
  exchange, C a harness session, D a bookmark ref) — never by editing this one.
- ``PreviewSurface`` — the ``ModalScreen`` that hosts the four modes:
  **view** (read the renderable), **edit** (live-edit the text in a ``TextArea``,
  no external editor), **discuss** (run one AI turn scoped to the open thing and
  read the reply inline), and **author** (start in edit over a *seed*, optionally
  agent-drafted, with an ``on_save`` commit callback).
- ``surface(kind, payload)`` — build a read surface for a kind/payload (A1's
  tab-click does ``push_screen(surface(kind, payload))``).
- ``open_editor(seed, on_save, *, draft_prompt=None)`` — the **author** entry: a
  seed text + a commit callback + an optional agent-draft. ``/edithere`` opens it
  for a file; F's ``/tasks new --from`` opens it for a drafted pipeline.

Who *pushes* the modal: a Textual host. The host installs itself once via
``set_surface_host`` (the integrator wires the running app in ``launch()``, the
same way it installs the renderable sink and the confirm modal). A slash command
(``/edithere``) or another vector then calls ``open_surface(screen)`` without
importing the app; off the TUI (inline REPL, headless, tests) there is no host
and the caller falls back to ``cat`` + ``$EDITOR``.

Design rule (the parallel-build merge contract): the *signatures* here are
published day 1 and are additive — registering a provider or opening the surface
is a published-function call, not a shared edit.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from rich.console import RenderableType
from rich.text import Text
from textual.css.query import NoMatches, WrongType

# A provider renders one (kind, payload) into a Rich renderable. It is called as
# ``provider(payload, state=state)``; a provider that doesn't care about session
# state may declare just ``provider(payload)`` (we adapt the call).
PreviewProvider = Callable[..., RenderableType]


# --------------------------------------------------------------------------- #
#  The kind → provider registry (seam #2, read side)
# --------------------------------------------------------------------------- #

_PROVIDERS: dict[str, PreviewProvider] = {}
_builtins_loaded = False


def register_preview(kind: str, provider: PreviewProvider) -> None:
    """Register a renderer for a context *kind* (``doc``/``skill``/``ref``/…).

    Last registration wins so a vector (or a project) can override a built-in,
    and a double call (re-import) is a harmless no-op rather than a crash — the
    point of the seam is that registration is additive and collision-free."""
    if not isinstance(kind, str) or not kind:
        raise ValueError("preview kind must be a non-empty string")
    _PROVIDERS[kind] = provider


def get_preview_provider(kind: str) -> Optional[PreviewProvider]:
    _ensure_builtins()
    return _PROVIDERS.get(kind)


def registered_preview_kinds() -> list[str]:
    _ensure_builtins()
    return sorted(_PROVIDERS)


def _ensure_builtins() -> None:
    """Lazily register the built-in providers (client #1). Imported here, not at
    module top, so ``preview_providers`` can import ``register_preview`` from us
    without an import cycle."""
    global _builtins_loaded
    if _builtins_loaded:
        return
    _builtins_loaded = True  # set first: a provider import error must not loop
    try:
        from xlii.tui import preview_providers

        preview_providers.register_builtin_previews()
    except (ImportError, ModuleNotFoundError):
        # The surface still works for any explicitly-registered kind; built-ins
        # are best-effort (a provider's optional dep may be missing).
        pass


def _call_provider(provider: PreviewProvider, payload: Any, state: Any) -> RenderableType:
    """Invoke a provider, passing ``state`` only when it accepts it."""
    try:
        return provider(payload, state=state)
    except TypeError:
        return provider(payload)


def preview_for(kind: str, payload: Any, *, state: Any = None) -> RenderableType:
    """Render ``(kind, payload)`` to a Rich renderable via its provider.

    Falls back to a dim note when no provider claims the kind, so a caller (a
    tab-click on a kind that hasn't shipped its provider yet) degrades to a
    legible message instead of an error."""
    _ensure_builtins()
    provider = _PROVIDERS.get(kind)
    if provider is None:
        return Text(f"no preview for '{kind}'", style="dim italic")
    try:
        return _call_provider(provider, payload, state)
    except Exception as exc:  # a provider must never take down the surface
        return Text(f"preview failed for '{kind}': {exc}", style="red")


# --------------------------------------------------------------------------- #
#  The host seam — who pushes the modal
# --------------------------------------------------------------------------- #

class SurfaceHost:
    """Anything that can push a ``ModalScreen`` for us. The concrete host is
    ``AppSurfaceHost`` (wraps the running Textual app); a test may install its
    own. Kept tiny on purpose — one method."""

    def open_surface(self, screen: Any, on_result: Optional[Callable[[Any], None]] = None) -> None:
        raise NotImplementedError


class AppSurfaceHost(SurfaceHost):
    """Bridge a running Textual ``App`` into the host seam.

    Pushing a screen must happen on the app's own thread; a slash command runs on
    a worker thread, so we hop via ``call_from_thread`` (the same bridge the bash
    confirm modal uses). When already on the app thread (an in-app caller), push
    directly. The integrator installs one of these in ``launch()``:

        from xlii.tui import preview
        prev = preview.set_surface_host(preview.AppSurfaceHost(app))
        try: app.run()
        finally: preview.set_surface_host(prev)
    """

    def __init__(self, app: Any) -> None:
        self._app = app

    def open_surface(self, screen: Any, on_result: Optional[Callable[[Any], None]] = None) -> None:
        def _push() -> None:
            if on_result is not None:
                self._app.push_screen(screen, on_result)
            else:
                self._app.push_screen(screen)

        try:
            self._app.call_from_thread(_push)
        except RuntimeError:
            # Already on the app thread (or no running loop) — push directly.
            _push()


_HOST: Optional[SurfaceHost] = None


def set_surface_host(host: Optional[SurfaceHost]) -> Optional[SurfaceHost]:
    """Install (or clear, with ``None``) the surface host; return the previous
    one so the caller can restore it on teardown."""
    global _HOST
    prev = _HOST
    _HOST = host
    return prev


def current_surface_host() -> Optional[SurfaceHost]:
    return _HOST


def open_surface(screen: Any, on_result: Optional[Callable[[Any], None]] = None) -> bool:
    """Push ``screen`` through the installed host. Returns ``True`` when a host
    took it (we're under the TUI), ``False`` when there is none (the caller does
    its inline ``cat`` + ``$EDITOR`` fallback)."""
    host = _HOST
    if host is None:
        return False
    host.open_surface(screen, on_result)
    return True


# --------------------------------------------------------------------------- #
#  The surface modal (view · edit · discuss · author)
# --------------------------------------------------------------------------- #
#
# Imported lazily-at-class-definition: textual is an optional [tui] dep, so the
# registry/host above must import cleanly without it. We guard the import and
# only define PreviewSurface / the constructors when textual is present.

try:
    from textual.app import ComposeResult
    from textual.binding import Binding
    from textual.containers import Horizontal, Vertical, VerticalScroll
    from textual.screen import ModalScreen
    from textual.widgets import Button, Input, Static, TextArea

    _TEXTUAL = True
except Exception:  # pragma: no cover - exercised only without [tui]
    _TEXTUAL = False


if _TEXTUAL:

    class PreviewSurface(ModalScreen[Optional[str]]):
        """One thing, four modes. Dismisses with the last saved text (or None).

        - **view** — the read renderable (provider output or a passed body).
        - **edit** — a ``TextArea`` over the seed/body; ``ctrl+s`` commits via
          ``on_save`` (no external editor).
        - **discuss** — type a question; ``on_discuss(q)`` runs one AI turn scoped
          to the open thing and the reply lands inline (the text analog of the
          image *discuss-don't-regenerate* contract: edits stay explicit).
        - **author** — opened via ``open_editor`` starting in edit over a seed,
          optionally agent-drafted (``draft_prompt`` + ``on_draft``); the draft
          seeds the editor and is **never auto-saved**.
        """

        DEFAULT_CSS = """
        PreviewSurface {
            align: center middle;
        }
        PreviewSurface > #surface-box {
            width: 100%;
            height: 100%;
            border: round $accent;
            background: $panel;
            padding: 1 2;
        }
        PreviewSurface #surface-header {
            height: 1;
            margin-bottom: 1;
        }
        PreviewSurface #surface-title {
            width: 1fr;
            text-style: bold;
            color: $accent;
        }
        PreviewSurface #surface-close {
            width: auto;
            min-width: 3;
            height: 1;
            border: none;
            background: $error 20%;
            color: $text;
        }
        PreviewSurface #surface-close:hover {
            background: $error;
        }
        PreviewSurface #surface-view {
            height: 1fr;
        }
        PreviewSurface #surface-edit {
            height: 1fr;
        }
        PreviewSurface #surface-discuss {
            height: 1fr;
        }
        PreviewSurface #discuss-log {
            height: 1fr;
        }
        PreviewSurface #discuss-input {
            margin-top: 1;
        }
        PreviewSurface #surface-status {
            margin-top: 1;
            color: $text-muted;
        }
        PreviewSurface #surface-status.-ok { color: $success; text-style: bold; }
        PreviewSurface #surface-status.-warn { color: $warning; }
        PreviewSurface #surface-status.-err { color: $error; text-style: bold; }
        """

        # priority=True so the modal's chrome keys fire even when the focused
        # child claims them — TextArea binds ctrl+d (delete) and ctrl+e/ctrl+f
        # (cursor moves), which would otherwise eat the mode switches in edit mode.
        BINDINGS = [
            Binding("escape", "back", "back/close", show=False, priority=True),
            Binding("ctrl+e", "edit", "edit", show=False, priority=True),
            Binding("ctrl+r", "view", "read", show=False, priority=True),
            Binding("ctrl+s", "save", "save", show=False, priority=True),
            Binding("ctrl+d", "discuss", "discuss", show=False, priority=True),
        ]

        def __init__(
            self,
            *,
            title: str = "preview",
            body: Optional[RenderableType] = None,
            kind: Optional[str] = None,
            payload: Any = None,
            state: Any = None,
            seed: Optional[str] = None,
            editable: bool = False,
            on_save: Optional[Callable[[str], Any]] = None,
            on_discuss: Optional[Callable[[str], str]] = None,
            on_draft: Optional[Callable[[str], str]] = None,
            draft_prompt: Optional[str] = None,
            start_mode: str = "view",
            read_as: str = "text",
        ) -> None:
            super().__init__()
            self._title = title
            self._body = body
            self._kind = kind
            self._payload = payload
            self._state = state
            self._seed = seed if seed is not None else ""
            # how the read view renders the *edit buffer* when there's no body/kind
            # of its own: "markdown" (a .md file) or "text" (anything else).
            self._read_as = read_as if read_as in ("text", "markdown") else "text"
            self._editable = bool(editable or on_save is not None)
            self._on_save = on_save
            self._on_discuss = on_discuss
            self._on_draft = on_draft
            self._draft_prompt = draft_prompt
            self._mode = start_mode if start_mode in ("view", "edit", "discuss") else "view"
            self._last_saved: Optional[str] = None

        # -- compose / mount ------------------------------------------------

        def compose(self) -> ComposeResult:
            with Vertical(id="surface-box"):
                with Horizontal(id="surface-header"):
                    yield Static(self._title_line(), id="surface-title")
                    yield Button("✕", id="surface-close")
                with VerticalScroll(id="surface-view"):
                    yield Static(self._read_renderable(), id="surface-view-body")
                yield TextArea(self._seed, id="surface-edit")
                with Vertical(id="surface-discuss"):
                    with VerticalScroll(id="discuss-log"):
                        yield Static(self._discuss_intro(), id="discuss-log-body")
                    yield Input(placeholder="ask about this…", id="discuss-input")
                yield Static(self._hint_text(), id="surface-status")

        def on_mount(self) -> None:
            self._apply_mode(self._mode, initial=True)
            if self._draft_prompt and self._on_draft is not None:
                self._set_status("drafting…")
                self.run_worker(self._draft_worker, thread=True, exclusive=True)

        # -- rendering helpers ----------------------------------------------

        def _title_line(self) -> Text:
            return Text(self._title, style="bold")

        def _render_buffer(self, text: str) -> RenderableType:
            """Render the edit buffer for the read view: Markdown for a .md file,
            else plain text."""
            if self._read_as == "markdown":
                from rich.markdown import Markdown

                return Markdown(text or "_(empty)_")
            return Text(text)

        def _read_renderable(self) -> RenderableType:
            if self._body is not None:
                return self._body
            if self._kind is not None:
                return preview_for(self._kind, self._payload, state=self._state)
            if self._seed:
                return self._render_buffer(self._seed)
            return Text("(nothing to preview)", style="dim italic")

        def _discuss_intro(self) -> Text:
            return Text(f"discuss · {self._title}", style="dim")

        def _hint_text(self) -> str:
            parts: list[str] = []
            if self._editable:
                parts.append("ctrl+e edit")
                parts.append("ctrl+s save")
            if self._on_discuss is not None:
                parts.append("ctrl+d discuss")
            parts.append("ctrl+r read")
            parts.append("esc back")
            return "  ·  ".join(parts)

        # -- mode switching --------------------------------------------------

        def _apply_mode(self, mode: str, *, initial: bool = False) -> None:
            self._mode = mode
            view = self.query_one("#surface-view")
            edit = self.query_one("#surface-edit", TextArea)
            discuss = self.query_one("#surface-discuss")
            view.display = mode == "view"
            edit.display = mode == "edit"
            discuss.display = mode == "discuss"
            if mode == "edit":
                edit.read_only = False
                edit.focus()
            elif mode == "discuss":
                self.query_one("#discuss-input", Input).focus()
            if not initial:
                self._set_status(self._hint_text())

        def _set_status(self, msg: str, tone: str = "muted") -> None:
            """Set the footer status. ``tone`` ∈ muted|ok|warn|err drives the color
            via a CSS class (a widget ``color`` overrides inline Rich styles, so the
            class is the reliable way to make 'saved ✓' actually green)."""
            try:
                status = self.query_one("#surface-status", Static)
            except NoMatches:
                return
            status.remove_class("-ok", "-warn", "-err")
            if tone in ("ok", "warn", "err"):
                status.add_class(f"-{tone}")
            status.update(str(msg))

        # -- actions ---------------------------------------------------------

        def action_view(self) -> None:
            # Refresh the read view from the live edit buffer when we have one, so
            # "read" after an edit shows what you typed (even before saving) —
            # rendered as Markdown for a .md file, else plain text.
            if self._editable and self._body is None and self._kind is None:
                try:
                    self.query_one("#surface-view-body", Static).update(
                        self._render_buffer(self.query_one("#surface-edit", TextArea).text)
                    )
                except (NoMatches, WrongType):
                    # Best-effort refresh: if widgets are not currently available or
                    # are of an unexpected type, still continue switching to view mode.
                    pass
            self._apply_mode("view")

        def action_edit(self) -> None:
            if not self._editable:
                self._set_status("not editable", "warn")
                return
            self._apply_mode("edit")

        def action_discuss(self) -> None:
            if self._on_discuss is None:
                self._set_status("discuss unavailable here", "warn")
                return
            self._apply_mode("discuss")

        def action_save(self) -> None:
            if not self._editable or self._on_save is None:
                self._set_status("nothing to save", "warn")
                return
            text = self.query_one("#surface-edit", TextArea).text
            try:
                self._on_save(text)
            except (TypeError, ValueError, KeyError, OSError) as exc:
                self._set_status(f"save failed: {exc}", "err")
                return
            self._last_saved = text
            self._set_status("saved ✓", "ok")

        def action_back(self) -> None:
            # From edit/discuss, esc returns to the read view; from view it closes.
            if self._mode in ("edit", "discuss"):
                self.action_view()
                return
            self.dismiss(self._last_saved)

        def on_button_pressed(self, event) -> None:
            # The [x] always closes outright — unlike esc, which first steps
            # edit/discuss back to the read view.
            if event.button.id == "surface-close":
                self.dismiss(self._last_saved)

        # -- discuss / draft workers ----------------------------------------

        def on_input_submitted(self, event: "Input.Submitted") -> None:
            if event.input.id != "discuss-input":
                return
            q = (event.value or "").strip()
            event.input.value = ""
            if not q or self._on_discuss is None:
                return
            self._append_discuss(Text(f"› {q}", style="cyan"))
            self._set_status("thinking…")
            self.run_worker(lambda: self._discuss_worker(q), thread=True)

        def _discuss_worker(self, question: str) -> None:
            try:
                reply = self._on_discuss(question)  # type: ignore[misc]
            except Exception as exc:
                reply = f"discuss failed: {exc}"
            self.app.call_from_thread(self._show_reply, reply)

        def _show_reply(self, reply: str) -> None:
            from rich.markdown import Markdown

            self._append_discuss(Markdown(reply or "(empty)"))
            self._set_status(self._hint_text())

        def _append_discuss(self, renderable: RenderableType) -> None:
            from rich.console import Group

            body = self.query_one("#discuss-log-body", Static)
            existing = getattr(body, "_xlii_lines", None)
            if existing is None:
                existing = [self._discuss_intro()]
            existing = list(existing) + [renderable]
            body._xlii_lines = existing  # type: ignore[attr-defined]
            body.update(Group(*existing))

        def _draft_worker(self) -> None:
            try:
                drafted = self._on_draft(self._draft_prompt or "")  # type: ignore[misc]
            except Exception as exc:
                self.app.call_from_thread(self._set_status, f"draft failed: {exc}", "err")
                return
            self.app.call_from_thread(self._apply_draft, drafted or "")

        def _apply_draft(self, drafted: str) -> None:
            ta = self.query_one("#surface-edit", TextArea)
            ta.load_text(drafted)
            self._set_status("drafted — review, edit, then ctrl+s to save", "ok")


def surface(
    kind: str,
    payload: Any,
    *,
    state: Any = None,
    title: Optional[str] = None,
    on_discuss: Optional[Callable[[str], str]] = None,
    on_save: Optional[Callable[[str], Any]] = None,
    editable: bool = False,
) -> "PreviewSurface":
    """Build a read surface for ``(kind, payload)``.

    A1's tab-click does ``push_screen(surface(kind, payload, state=state))``. The
    read view comes from the kind's provider (``preview_for``); pass ``on_discuss``
    to enable the discuss tab and ``on_save``/``editable`` to allow editing."""
    if not _TEXTUAL:  # pragma: no cover - requires [tui]
        raise RuntimeError("textual is not installed — the preview surface needs the [tui] extra")
    _ensure_builtins()
    return PreviewSurface(
        title=title or kind,
        kind=kind,
        payload=payload,
        state=state,
        on_discuss=on_discuss,
        on_save=on_save,
        editable=editable,
    )


def open_editor(
    seed: str,
    on_save: Callable[[str], Any],
    *,
    draft_prompt: Optional[str] = None,
    on_draft: Optional[Callable[[str], str]] = None,
    on_discuss: Optional[Callable[[str], str]] = None,
    title: str = "edit",
    kind: str = "file",
    state: Any = None,
    read_as: str = "text",
) -> "PreviewSurface":
    """The **author** entry (seam #2): a seed text + a commit callback + an
    optional agent-draft.

    ``/edithere <file>`` opens it seed=file / save=write; ``/edithere --draft`` and
    F's ``/tasks new --from`` pass a ``draft_prompt`` (+ ``on_draft``) so the agent
    drafts the starting content — surfaced live for review, never auto-applied.
    ``read_as="markdown"`` renders the read view (ctrl+r) as Markdown for a .md
    file; the default plain text suits any other file."""
    if not _TEXTUAL:  # pragma: no cover - requires [tui]
        raise RuntimeError("textual is not installed — the editor surface needs the [tui] extra")
    return PreviewSurface(
        title=title,
        kind=None,
        payload=None,
        state=state,
        seed=seed,
        editable=True,
        on_save=on_save,
        on_discuss=on_discuss,
        on_draft=on_draft,
        draft_prompt=draft_prompt,
        # AI-focus on the file by default (discuss), per the /editthis vision; a
        # fresh draft still opens in edit so you review the drafted content first.
        start_mode="discuss" if (on_discuss is not None and not draft_prompt) else "edit",
        read_as=read_as,
    )
