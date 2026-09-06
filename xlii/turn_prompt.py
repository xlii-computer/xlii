"""System prompt loading and per-turn prompt assembly for the agent loop."""

from __future__ import annotations

from pathlib import Path as _Path
from typing import Optional

_PROMPTS_DIR = _Path(__file__).parent / "prompts"


def load_prompt(name: str, xli_dir=None) -> str:
    """Load a system prompt. `.xlii/prompts/<name>.md` shadows the packaged
    prompt — the user can rewrite how their agent thinks per project (the
    Emacs move). Packaged prompts live in xlii/prompts/*.md: diffable,
    reviewable, and not buried in a .py string."""
    if xli_dir is not None:
        override = xli_dir / "prompts" / f"{name}.md"
        if override.exists():
            return override.read_text().strip()
    return (_PROMPTS_DIR / f"{name}.md").read_text().strip()


def build_code_system_prompt(project) -> str:
    """The base system prompt for a code surface: the packaged/overridden
    `main` prompt plus, for a local-only project, the [LOCAL MODE] addendum and
    (when present) the `.xlii/index.txt` note. Single source of truth for the
    code prompt — used by Agent.__post_init__ at construction AND by
    Profile.system_prompt() on a live /code switch (so the two can't drift)."""
    sys_prompt = load_prompt("main", project.xli_dir)
    addendum: list[str] = []
    try:
        from xlii.desk_files import files_mount_prompt_addendum

        files_hint = files_mount_prompt_addendum(project)
        if files_hint:
            addendum.append(files_hint)
    except Exception:
        # The [FILES] hint is simply omitted -- the prompt is still valid without it.
        pass
    if project.local_only:
        addendum.append(
            "[LOCAL MODE] This project has no remote Collection — "
            "search_project serves from a local full-text index (BM25, "
            "rebuilt at /sync). Use it for content recall; glob/grep for "
            "exact matches."
        )
        index_path = project.xli_dir / "index.txt"
        if index_path.exists():
            addendum.append(
                "A pre-computed file index lives at `.xlii/index.txt` "
                "(format: `<size>\\t<relpath>` per line). Grep that file "
                "for fast structural search instead of walking the live "
                "filesystem — this is the right tool when the tree is large."
            )

    # project-browser.md P5: orient the agent with the cached project skeleton so
    # it doesn't re-derive layout by tool calls. Cache-only (written by /browse or
    # /sync) — never detect here, keeping prompt construction cheap + side-effect-free.
    try:
        from xlii.project_fingerprint import load_project_profile, summary_line

        profile = load_project_profile(project.project_root)
        if profile is not None:
            addendum.append(
                f"[PROJECT] {summary_line(profile)} "
                "(from .xlii/project-profile.json; the human runs /browse to explore)."
            )
    except Exception:
        # The [PROJECT] hint is omitted, keeping prompt construction cheap and side-effect-free per the note
        # above.
        pass

    try:
        from xlii.system_profile import load_system_profile, summary_line as system_summary_line

        sys_profile = load_system_profile()
        if sys_profile is not None and sys_profile.os_name:
            addendum.append(
                f"[SYSTEM] {system_summary_line(sys_profile)}"
            )
    except Exception:
        # System profile enrichment is optional — prompt assembly must continue.
        pass

    # cursor-workflows.md A2: large tool output is spilled to disk, not lost — tell
    # the agent so it pages through with read_file instead of re-running commands.
    addendum.append(
        "[CONTEXT] Large tool output is preserved, not discarded: when it is big it "
        "is written to `.xlii/scratch/tool-output/` and you get a head+tail preview "
        "plus that path. To see the rest, call read_file on the path with offset/limit "
        "and page through it — don't re-run the command."
    )

    # cursor-workflows.md A1: advertise available skills (names + descriptions only);
    # the full body is pulled in when the human runs /skill <name>.
    try:
        from xlii.skills import load_skills, skill_index_line

        index = skill_index_line(load_skills(project.project_root), model_facing=True)
        if index:
            addendum.append(
                "[SKILLS] Workflow skills are available (some imported from grok / "
                "Claude — tagged with their origin). The human attaches one with "
                "`/skill <name>`, inlining its steps. Available:\n"
                + index
            )
    except Exception:
        # The [SKILLS] hint is omitted; the human can still attach one with /skill.
        pass

    if addendum:
        sys_prompt = sys_prompt + "\n\n" + "\n\n".join(addendum)
    return sys_prompt


PLAN_MODE_PREAMBLE = load_prompt("plan-preamble")
DISCOVERY_MODE_PREAMBLE = load_prompt("discovery-preamble")
OPS_MODE_PREAMBLE = load_prompt("ops-preamble")
WORKER_SYSTEM_PROMPT = load_prompt("worker")
WRITER_SYSTEM_PROMPT = load_prompt("writer-worker")


def effective_system_prompt(
    base: str,
    attached_docs: list[tuple[str, str]],
    mode_directive: Optional[str],
) -> str:
    """Base system prompt + attached /doc content + active mode directive."""
    if not attached_docs and mode_directive is None:
        return base
    sections = [base.rstrip()]
    if attached_docs:
        sections += ["", "---", "",
                     "# Attached reference documents",
                     "",
                     ("(These were attached by the user via `/doc <name>`. Treat "
                      "them as authoritative project rules / framework conventions / "
                      "specs the user wants you to follow.)"),
                     ""]
        for name, content in attached_docs:
            sections.append(f"## {name}")
            sections.append("")
            sections.append(content.rstrip())
            sections.append("")
    if mode_directive is not None:
        sections += ["", "---", "", mode_directive]
    return "\n".join(sections)
