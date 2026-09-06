"""Built-in REPL slash commands, split by domain.

Registration is explicit (not an import side effect): call `register_all()`
once at session start. It is idempotent — the underlying registry hard-errors
on duplicate names, so the module-level guard makes a second call a no-op
rather than a crash.
"""

from __future__ import annotations

from xlii.repl_cmds import account, browse, chat, checkpoint, code, compact, consult, context, cursor, delegate, howto, image, imagine, journal, knowledge, loadout, locker, loop, meta, mode, os, registry_cmds, review, role, session, sh, skill, switch
from xlii.repl_cmds import admin  # noqa: E402 — Vector E: separate line so the parallel-build registration doesn't merge-collide with the combined import above.
from xlii.repl_cmds import edithere  # noqa: E402 — Vector A2 (interaction-ii): own line for the same collision-avoidance reason.
from xlii.repl_cmds import replay  # noqa: E402 — Vector B (interaction-ii): separate line so the parallel-build registration doesn't merge-collide with the combined import above.
from xlii.repl_cmds import tasks  # noqa: E402 — Vector F (interaction-II): own line, same merge-collision avoidance as Vector E above.
from xlii.repl_cmds import jobs  # noqa: E402 — Vector J (interaction-III): own line, same merge-collision avoidance as the vectors above.
from xlii.repl_cmds import file_tab  # noqa: E402 — Vector P (interaction-III): /file-tab on its own line, same merge-collision avoidance.
from xlii.repl_cmds import scratch  # noqa: E402 — Vector S (interaction-III): /scratch mode appended on its own statement (append-only; integrator reconciles the list + one docgen regen).
from xlii.repl_cmds import wiki  # noqa: E402 — wiki authoring vector: /wiki on its own statement, same merge-collision avoidance as the vectors above.
from xlii.repl_cmds import git  # noqa: E402 — git source-control vector: /git on its own statement, same merge-collision avoidance as the vectors above.
from xlii.repl_cmds import send  # noqa: E402 — /send: hand a file/last-output to an external program; own statement, same merge-collision avoidance.
from xlii.repl_cmds import theme  # noqa: E402 — Options → Theme… panel opener + /theme; own statement, same merge-collision avoidance.
from xlii.repl_cmds import nfo  # noqa: E402 — /nfo AI project-status splash generator; own statement, same merge-collision avoidance.
from xlii.repl_cmds import remote  # noqa: E402 — /remote remote-filesystem connections (every wire; /ftp = hidden alias); own statement, same merge-collision avoidance.
from xlii.repl_cmds import claude  # noqa: E402 — open-substrate Vector B: /claude front door; own line, append-only.
from xlii.repl_cmds import grok_build  # noqa: E402 — open-substrate Vector B: /grok-build (+ /build alias); own line, append-only.
from xlii.repl_cmds import attach  # noqa: E402 — the Fold Vector A: /attach + /detach umbrella verbs (doc/undoc ride as hidden aliases); own line, append-only.
from xlii.repl_cmds import map as map_cmd  # noqa: E402 — repo-map vector: /map (render/attach the project's shape); own line, append-only ("map_cmd" so the stdlib builtin stays untouched).
from xlii.repl_cmds import config_panel  # noqa: E402 — /config session-knobs panel (per-role models · budget · theme); own line, append-only.
from xlii.repl_cmds import history as history_cmd  # noqa: E402 — /history input-line scrollback panel (Track F); own line, append-only.
from xlii.repl_cmds import btw  # noqa: E402 — /btw mid-turn steering (bg-default P2); own line, append-only.
from xlii.repl_cmds import episode  # noqa: E402 — /session episode continuity (code-session-resume P0); own line, append-only.
from xlii.repl_cmds import off  # noqa: E402 — /off: leave every overlay/mode at once; own line, append-only.
from xlii.repl_cmds import render as render_cmd  # noqa: E402 — /render PDF export (document-pdf); own line, append-only.
from xlii.repl_cmds import mail as mail_cmd  # noqa: E402 — /mail inbox triage (email); own line, append-only.
from xlii.repl_cmds import alias  # noqa: E402 — /alias task→slash-command (task-args P2); own line, append-only.
from xlii.repl_cmds import media as media_cmd  # noqa: E402 — /media persona media inbox (tauri-face V5); own line, append-only.
from xlii.repl_cmds import mojo  # noqa: E402 — fabric F2: /mojo persona front door; own line, append-only.
from xlii.repl_cmds import sweep  # noqa: E402 — throne housekeep; own line, append-only.

from xlii.repl_cmds import workbench  # noqa: E402 — typed-workbenches B0: /workbench type catalog + switch; own statement, append-only.
from xlii.repl_cmds import providers  # noqa: E402 — typed-workbenches S2: /providers list/run/new; own statement, append-only.

_registered = False

# Order is cosmetic (the registry is keyed by name), but kept stable so any
# future help-generation walks commands in a predictable order.
# apropos is no longer in this list: /apropos·/search-help·/manuals were
# absorbed as hidden aliases of /help (meta) — apropos.py is now the pure
# implementation library those flags dispatch into.
_MODULES = (registry_cmds, session, account, compact, checkpoint, os, sh, mode, knowledge, meta, howto, image, imagine, loadout, locker, browse, skill, role, context, code, chat, review, consult, delegate, cursor, loop, switch, journal)
# Vector E: appended on its own statement (rather than edited into the tuple
# above) for the same collision-avoidance reason as the import.
_MODULES = _MODULES + (admin,)
# Vector A2 (interaction-ii): /edithere appended on its own statement.
_MODULES = _MODULES + (edithere,)
# Vector B (interaction-ii): /replay — appended on its own statement for the
# same collision-avoidance reason as Vector E.
_MODULES = _MODULES + (replay,)
# Vector F (interaction-II): /tasks pipeline appended on its own statement
# (append-only — the integrator reconciles the list + one docgen regen).
_MODULES = _MODULES + (tasks,)
# Vector J (interaction-III): /jobs background-job surface appended on its own
# statement (append-only — same merge-collision avoidance as the vectors above).
_MODULES = _MODULES + (jobs,)
# Vector P (interaction-III): /file-tab split-screen panels — appended on its own
# statement (append-only — the integrator reconciles the list + one docgen regen).
_MODULES = _MODULES + (file_tab,)
# Vector S (interaction-III): /scratch mode appended on its own statement for the
# same collision-avoidance reason (append-only — integrator reconciles + docgen).
_MODULES = _MODULES + (scratch,)
# wiki authoring vector: /wiki appended on its own statement (append-only — same
# merge-collision avoidance; integrator reconciles the list + one docgen regen).
_MODULES = _MODULES + (wiki,)
# git source-control vector: /git appended on its own statement (append-only — same
# merge-collision avoidance; integrator reconciles the list + one docgen regen).
_MODULES = _MODULES + (git,)
# /send: hand a file (or the last output) to an external program — appended on its
# own statement (append-only — same merge-collision avoidance; integrator regen).
_MODULES = _MODULES + (send,)
# Options → Theme… panel opener + /theme — appended on its own statement (append-only —
# same merge-collision avoidance; integrator reconciles the list + one docgen regen).
_MODULES = _MODULES + (theme,)
# /nfo: AI project-status .nfo generator (becomes the startup splash) — appended on its own
# statement (append-only — same merge-collision avoidance; integrator reconciles list + docgen).
_MODULES = _MODULES + (nfo,)
# /remote: remote-filesystem connections (every wire behind the ftp://+sftp://+dav://+smb://
# providers; /ftp = hidden alias) — appended on its own statement (append-only — same
# merge-collision avoidance; integrator reconciles + docgen).
_MODULES = _MODULES + (remote,)
# open-substrate Vector B: /claude and /grok-build harness front doors — each appended on its own
# statement (append-only — integrator reconciles the list + one docgen regen).
_MODULES = _MODULES + (claude,)
_MODULES = _MODULES + (grok_build,)
# the Fold Vector A: /attach + /detach appended on their own statement (append-only —
# integrator reconciles the list + one docgen regen). knowledge still owns the doc
# logic; attach.py registers the umbrella verbs and the hidden doc/undoc aliases.
_MODULES = _MODULES + (attach,)
# repo-map vector: /map appended on its own statement (append-only — same
# merge-collision avoidance; integrator reconciles the list + one docgen regen).
_MODULES = _MODULES + (map_cmd,)
# /config session-knobs panel — appended on its own statement (append-only).
_MODULES = _MODULES + (config_panel,)
# /history input-line scrollback panel (Track F) — appended on its own statement.
_MODULES = _MODULES + (history_cmd,)
# /btw mid-turn steering (bg-default P2) — appended on its own statement.
_MODULES = _MODULES + (btw,)
# /session episode continuity (code-session-resume P0) — own statement.
_MODULES = _MODULES + (episode,)
# /off: one command to leave every overlay/mode — appended on its own statement.
_MODULES = _MODULES + (off,)
# /render PDF export (document-pdf) — appended on its own statement.
_MODULES = _MODULES + (render_cmd,)
# /mail inbox triage (email) — appended on its own statement.
_MODULES = _MODULES + (mail_cmd,)
# /alias: promote a saved task into a slash command (task-args P2) — own statement.
_MODULES = _MODULES + (alias,)
from xlii.repl_cmds import bind as bind_cmd  # noqa: E402 — /bind task→menu/F-key
_MODULES = _MODULES + (bind_cmd,)
# /media: the persona media inbox (tauri-face V5) — appended on its own statement.
_MODULES = _MODULES + (media_cmd,)


class _V7Gitpain:
    """V7 overnight slam: register /gitpain (primary) alongside the /git deprecated alias."""

    @staticmethod
    def register() -> None:
        from xlii.repl_cmds.git import register_gitpain

        register_gitpain()


from xlii.repl_cmds import gigwork as gigwork_cmd  # noqa: E402 — /gigwork (+ /gig alias): hire a non-xAI worker brain; own statement, append-only.
_MODULES = _MODULES + (gigwork_cmd,)
from xlii.repl_cmds import jam as jam_cmd  # noqa: E402 — /jam: named multi-brain presets over the gigwork seam; own statement, append-only.
_MODULES = _MODULES + (jam_cmd,)
from xlii.repl_cmds import xtool as xtool_cmd  # noqa: E402 — /xtool lint/format catalog (overnight V1); own statement, append-only.
_MODULES = _MODULES + (xtool_cmd,)
# V7 gitpain (overnight slam): /gitpain primary verb — own statement (append-only).
_MODULES = _MODULES + (_V7Gitpain,)
# Throne housekeep: /sweep — own statement, append-only.
_MODULES = _MODULES + (sweep,)
# fabric F2: /mojo persona front door — own statement (append-only).
_MODULES = _MODULES + (mojo,)
# typed-workbenches B0: /workbench — own statement (append-only).
_MODULES = _MODULES + (workbench,)
# typed-workbenches S2: /providers — own statement (append-only).
_MODULES = _MODULES + (providers,)
from xlii.repl_cmds import hygiene as hygiene_cmd  # noqa: E402 — /hygiene text hygiene + credibility; own statement, append-only.
_MODULES = _MODULES + (hygiene_cmd,)
from xlii.repl_cmds import destroy  # noqa: E402 — footprints D: own line
_MODULES = _MODULES + (destroy,)
from xlii.repl_cmds import lock as lock_cmd  # noqa: E402 — footprints F: /lock /unlock
_MODULES = _MODULES + (lock_cmd,)
from xlii.repl_cmds import remote_lab  # noqa: E402 — footprints R: /remote-control /mute
_MODULES = _MODULES + (remote_lab,)


def register_all() -> None:
    """Register every built-in slash command. Safe to call more than once."""
    global _registered
    if _registered:
        return
    for mod in _MODULES:
        mod.register()
    try:
        from xlii.door_tools import register as register_door_tools

        register_door_tools()
    except Exception:
        pass
    _registered = True
