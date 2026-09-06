"""Argparse registration for session subcommands."""

from __future__ import annotations

from .ask import cmd_ask
from .chat import cmd_chat
from .code import cmd_code
from .loop import cmd_loop


def register(sub) -> None:
        p_ask = sub.add_parser(
            "ask",
            help="Run a single agent turn and print the reply (headless; for scripts + the XMPP daemon).",
        )
        p_ask.add_argument("prompt", help="The prompt/task for the one-shot agent turn")
        p_ask.add_argument("--attach", metavar="PATH", action="append", default=None,
                           help="Attach a file for the agent to SEE this turn (image → vision, "
                                "PDF/text → inlined; repeatable). The media-in path the XMPP mouth "
                                "uses to hand iXaac a photo or document.")
        p_ask.add_argument("--workspace", metavar="NAME",
                           help="Project name (registry) or path to run in (default: cwd / most-recent)")
        p_ask.add_argument("--persona", metavar="NAME",
                           help="Run the turn AS this persona over its own memory (the mojo read: "
                                "recalls the persona's long-term memory; overrides --workspace/--session)")
        p_ask.add_argument("--no-accrue", action="store_true", dest="no_accrue",
                           help="(with --persona) don't write this turn into the persona's memory "
                                "(default: the turn accrues so texting it is a continuing conversation)")
        p_ask.add_argument("--no-sync", action="store_true", dest="no_sync",
                           help="(with --persona, on the center) accrue the turn locally but DON'T "
                                "drain it to the shared remote Collection — keep it private/local")
        p_ask.add_argument("--outbox", metavar="DIR",
                           help="Grant the turn a delivery channel: the send_file tool queues files "
                                "into DIR and the caller delivers them after the turn (the XMPP "
                                "mouth uploads them encrypted into the owner's chat). Also unlocks "
                                "generate_image on the persona surface — a mouth that can deliver "
                                "media may make media.")
        p_ask.add_argument("--yolo", action="store_true",
                           help="Auto-approve bash without prompting (for trusted non-interactive use)")
        p_ask.add_argument("--session", metavar="ID",
                           help="Persist this conversation under the project's .xlii/ keyed by a "
                                "caller-chosen ID — repeated calls with the same ID share context "
                                "(the multi-turn primitive for the daemon and scripts)")
        p_ask.add_argument("--new-session", action="store_true", dest="new_session",
                           help="Reset the --session conversation before running this turn")
        p_ask.set_defaults(func=cmd_ask)

        p_loop = sub.add_parser(
            "loop",
            help="Autonomous build→test→fix loop (headless; walk away to green).",
        )
        p_loop.add_argument("goal", nargs="?", help="Task goal (omit with --status or --resume)")
        p_loop.add_argument("--status", action="store_true", help="Show active loop state")
        p_loop.add_argument("--resume", action="store_true", help="Resume a persisted loop")
        p_loop.add_argument("--workspace", metavar="NAME",
                            help="Project name (registry) or path (default: cwd / most-recent)")
        p_loop.add_argument("--judge", metavar="NAMES", default="tests",
                            help="Comma-separated judge profiles (default: tests)")
        p_loop.add_argument("--max", type=int, default=5, dest="max_cycles",
                            help="Maximum build→test cycles (default: 5)")
        p_loop.add_argument("--test", metavar="CMD", default="pytest -q",
                            help="Shell test command (default: pytest -q)")
        p_loop.add_argument("--budget", type=float, default=None,
                            help="Stop if judge spend exceeds this USD cap (L2+)")
        p_loop.add_argument("--from-plan", action="store_true",
                            help="Use goal from .xlii/plan-last.md (after /plan + /execute)")
        p_loop.add_argument("--drain-inbox", action="store_true", dest="drain_inbox",
                            help="Run every .xlii/inbox/*.md task in order, archiving each to inbox/done/")
        p_loop.add_argument("--commit", metavar="MODE", default=None,
                            choices=["never", "each", "final"],
                            help="Git commit on test pass (each) or loop done (final); implied each when --push is set")
        p_loop.add_argument("--push", metavar="MODE", default=None,
                            choices=["never", "each", "final"],
                            help="Git push before CI judge (must pair with --commit each|final)")
        p_loop.add_argument("--read-budget", type=int, default=3, dest="read_budget",
                            help="Max file excerpts per judge READ_REQUEST (default: 3)")
        p_loop.add_argument("--swarm", type=int, default=1, metavar="N",
                            help="Writer-workers per build phase (default: 1; capped by /swarm ceiling)")
        p_loop.add_argument("--merge", metavar="MODE", default="auto", choices=["auto", "llm"],
                            help="Merge strategy for overlapping writers: auto=git-only fail-closed, llm=merge-agent")
        p_loop.add_argument("--merge-judge", metavar="PROFILE", default=None, dest="merge_judge",
                            help="Cross-vendor judge for LLM merge resolutions (default: loop_defaults.merge_judge)")
        p_loop.add_argument("--yolo", action="store_true",
                            help="Auto-approve bash without prompting")
        p_loop.set_defaults(func=cmd_loop)

        p_code = sub.add_parser(
            "code",
            help="Project-scoped code agent REPL. Pass a project NAME (registry lookup) or PATH; default cwd.",
        )
        p_code.add_argument("target", nargs="?", help="Project name (registry) or path")
        p_code.add_argument("--yolo", action="store_true",
                            help="Auto-approve every bash command regardless of intent (no confirmation prompts)")
        p_code.add_argument("--rail", action="store_true",
                            help="Start in Coding Rail mode: gate each turn through "
                                 "Requirements → Architecture → Edge Cases → Pseudocode → "
                                 "Implementation → Self-Review (toggle in-session with /rail).")
        p_code.add_argument("--discovery", "--disc", dest="discovery", action="store_true",
                            help="Start in discovery mode: read-only discussion/research — the "
                                 "agent reads & explains but won't change code (toggle in-session "
                                 "with /discovery).")
        p_code.add_argument("--ops", action="store_true",
                            help="Start in ops mode: OS diagnostics & workflow — platform-correct "
                                 "shell probes, read-only first (toggle in-session with /ops).")
        p_code.add_argument("--no-sync", action="store_true",
                            help="Skip startup and end-of-turn syncing to the Collection this session")
        p_code.add_argument("--no-startup", action="store_true", dest="no_startup",
                            help="Skip the per-project startup-task ritual this launch")
        p_code.add_argument("--tui", action="store_true",
                            help="Launch the experimental full-screen Textual UI (needs `pip install 'xlii[tui]'`)")
        p_code.add_argument("--tauri", action="store_true",
                            help="Launch the desktop face (the xlii-desktop Tauri app) over this "
                                 "project — iXaac-first chat, [$] flips to code")
        # Note: --resume is already episode continuity on `code`. Face "continue
        # the live desk" is the default when a face is running; force a clean
        # start with --replace (or XLII_FACE_INSTANCE=resume|replace).
        p_code.add_argument(
            "--replace", dest="face_replace", action="store_true",
            help="With --tauri: if a face is already running, stop it and start a new one",
        )
        # Episode continuity (code-session-resume P1). --resume takes an optional
        # id ("" sentinel = most recent episode); dest avoids the loop cmd's flag.
        p_code.add_argument("--keep-session", action="store_true", dest="keep_session",
                            help="Start an episode at launch AND make it sticky for this "
                                 "project: every turn snapshots the full live history, and "
                                 "each later launch offers to restart where you left "
                                 "(same as /session on; /session off clears it)")
        p_code.add_argument("--resume", nargs="?", const="", default=None,
                            metavar="ID", dest="resume_episode",
                            help="Resume a stored episode (omit ID for the most recent): "
                                 "full history + conversation id restored")
        p_code.add_argument("--force", action="store_true",
                            help="Proceed even when launched from within another xlii session for this project")
        # Launch-gate bypass (skip the interactive 'launch here?' prompt).
        p_code.add_argument("--preview", action="store_true",
                            help="Open the REPL without initializing: no .xlii, no snapshot, no sync "
                                 "(ephemeral for a non-project; skips startup sync for an existing one)")
        p_code.add_argument("--init", dest="init", action="store_true",
                            help="Initialize a local-only .xlii here, then launch (no snapshot/Collection)")
        p_code.add_argument("--launch", "-y", action="store_true",
                            help="Skip the launch gate and open an existing project normally")
        p_code.set_defaults(func=cmd_code)

        p_chat = sub.add_parser(
            "chat",
            help="Persona-based conversational agent with persistent memory (each persona has its own Collection).",
        )
        p_chat.add_argument("name", nargs="?", help="Persona name (default: most-recently-used or the iXaac companion)")
        p_chat.add_argument("--new", metavar="NAME", help="Create a new persona; opens $EDITOR on its prompt file")
        p_chat.add_argument("--list", action="store_true", help="List all personas and exit")
        p_chat.add_argument("--edit", metavar="NAME", help="Open an existing persona's prompt in $EDITOR")
        p_chat.add_argument("--delete", metavar="NAME", help="Delete a persona (prompt + state dir)")
        p_chat.add_argument("--yolo", action="store_true", help="Auto-approve bash commands")
        p_chat.add_argument("--tui", action="store_true",
                            help="Launch the experimental full-screen Textual UI (needs `pip install 'xlii[tui]'`)")
        p_chat.add_argument("--yes", action="store_true", help="Skip confirmation prompts (used with --delete)")
        p_chat.add_argument("--force", action="store_true",
                            help="Proceed even when launched from within another xlii session for this persona")
        p_chat.set_defaults(func=cmd_chat)
