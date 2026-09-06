"""Persona management for `xlii chat`.

A persona is a named conversational personality with:
  - A system prompt at ~/.config/xlii/personas/<name>.md  (config — hand-editable)
  - A backing project at ~/.xlii/chat/<name>/             (state — Collection-synced)

**Mojo = MObile JOurnal.** ``default`` and ``mojo`` are the same seat — the
traveling journal, the vibe. You *have* the mojo. Chat personas are who you
*be* (stock: iXaac). Separate islands: a chat costume knows *of* the mojo,
not what the mojo knows. ``/name`` spells the journal. ``/persona`` /
``/chat`` sits with someone else. Jobs are ``/role`` — not extra personas.
xlii is the substrate.

The two locations follow Linux convention: config separate from state. The
project dir is a normal xlii project (with .xlii/project.json, manifest, and a
remote Collection) so all the existing sync / RAG machinery applies. Each
persona's conversation turns live as `turns/<ts>.md` files inside the project
and sync to the Collection — that's the long-term searchable memory.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from xlii.config import GLOBAL_CONFIG_DIR
from xlii.frontmatter import parse_frontmatter

PERSONAS_DIR = GLOBAL_CONFIG_DIR / "personas"
CHAT_STATE_DIR = Path.home() / ".xlii" / "chat"

# The journal. Display is Mojo; on-disk id is lowercase. Not a chat costume.
DEFAULT_PERSONA_ID = "mojo"
DEFAULT_PERSONA_DISPLAY = "Mojo"
# Who ``xlii chat`` / bare ``/chat`` sit with. Separate island from the journal.
CHAT_DEFAULT_PERSONA_ID = "ixaac"
CHAT_DEFAULT_PERSONA_DISPLAY = "iXaac"
_LEGACY_UNNAMED_ID = "ixaac"

# Seat words for the one mobile journal. Same island — never a second file.
# iXaac is a chat persona, not an alias of the journal. Jobs are /role.
FACTORY_PERSONA_ALIASES = frozenset({"default", "mojo"})
# You cannot *name* the journal "default" or "mojo" — those already mean the seat.
RESERVED_PERSONA_NAMES = frozenset({"default", "mojo"})


def is_legacy_unnamed_journal(name: str) -> bool:
    """The unnamed journal used to be spelled ``ixaac``. That id is now the
    chat costume — leftover knobs must not keep pointing the journal there."""
    return (name or "").strip().lower() == _LEGACY_UNNAMED_ID


def journal_knob_id(raw: str) -> str:
    """Normalize a journal-name knob (config / daemon ``[agent_fallback]``).

    Empty stays empty (daemon: legacy project-scoped turn). Seat words and the
    leftover ``ixaac`` spelling become ``mojo``. Explicit ``ixaac`` as a
    *binding* on a project is a different door — :func:`canonicalize_persona_id`.
    """
    n = (raw or "").strip()
    if not n:
        return ""
    if n.lower() in FACTORY_PERSONA_ALIASES or is_legacy_unnamed_journal(n):
        return DEFAULT_PERSONA_ID
    return n


def factory_persona_id(cfg: Optional[object] = None) -> str:
    """The mobile journal's name — ``fallback_persona``, else shipped ``mojo``.

    A leftover ``fallback_persona=ixaac`` (pre-split unnamed journal) is
    treated as unset — iXaac is the chat costume, not this seat.
    """
    raw = journal_knob_id(getattr(cfg, "fallback_persona", None) or "")
    return raw or DEFAULT_PERSONA_ID


def canonicalize_persona_id(name: str, *, cfg: Optional[object] = None) -> str:
    """Map seat words (default, mojo) onto the journal.

    ``default`` and ``mojo`` are the same seat. ``ixaac`` is a chat persona,
    not this seat. A leftover ``default.md`` is not an island.
    """
    n = (name or "").strip()
    if not n or n.lower() in FACTORY_PERSONA_ALIASES:
        return factory_persona_id(cfg)
    return n


def resolve_default_persona(*, project: Optional[object] = None,
                            cfg: Optional[object] = None) -> str:
    """The single source of truth for "which persona is the default here".

    One journal across every surface (the fabric premise): a project's explicit
    binding wins, else the node/config fallback, else the shipped companion.
    Returns a persona ID (lowercase, on-disk-resolvable) — NEVER the display
    name, so callers can hand the result straight to `_lookup_persona`. The
    human-facing name comes from `DEFAULT_PERSONA_DISPLAY` separately.

    Project binds to ``ixaac`` stay on the costume. Config/daemon leftover
    ``fallback_persona=ixaac`` does not — that knob names the journal.
    """
    bound = (getattr(project, "bound_persona", None) or "").strip()
    if bound:
        return canonicalize_persona_id(bound, cfg=cfg)
    return factory_persona_id(cfg)


def persona_id_from_project_name(name: str) -> Optional[str]:
    """``chat/<id>`` folders are that persona's island. Else None."""
    n = (name or "").strip()
    if not n.startswith("chat/"):
        return None
    pid = n.split("/", 1)[1].strip()
    return pid or None


def talk_persona_id(*, state: Optional[object] = None,
                    project: Optional[object] = None,
                    cfg: Optional[object] = None) -> str:
    """Who talk addresses this turn — the live island, not factory mojo.

    A ``chat/<name>`` folder is that persona alone. Then a live ``state.persona``.
    Only then the project's bound default / mojo.
    """
    project = project if project is not None else getattr(state, "project", None)
    from_folder = persona_id_from_project_name(
        getattr(project, "name", "") or ""
    )
    if from_folder:
        return canonicalize_persona_id(from_folder, cfg=cfg)
    live = getattr(state, "persona", None) if state is not None else None
    if live is not None:
        n = (getattr(live, "name", None) or "").strip()
        if n:
            return canonicalize_persona_id(n, cfg=cfg)
    return resolve_default_persona(project=project, cfg=cfg)

# Used as the template the editor opens with for a brand-new blank persona
# (`xlii chat --new <name>` / `/edit --id <name> --new`). The commented
# frontmatter block is the persona "loadout": a persona is a portable custom
# agent (instructions + memory + this loadout). It parses to nothing until you
# uncomment a line, so a fresh persona behaves exactly like a plain prompt.
DEFAULT_PROMPT = """---
# loadout — uncomment to give this persona tools/knowledge/model on every chat:
# plugins: [open-meteo, hackernews]   # subscribe these plugins (invoke via /get)
# docs: [my-conventions]              # auto-attach these reference docs
# skills: [grounded-analysis]         # attach these user-authored skills (/skill)
# model: grok-4                       # pin a model for this persona
---
You are a helpful general-purpose assistant.

Be terse, accurate, and useful. State results, don't predict them. If you don't
know something, say so — don't fabricate. When the user asks for code or runs a
real-world task, prefer to actually do it (with the tools available) over
describing what you would do.

Edit this file in $EDITOR to change the personality. The system prompt is
appended with a small fixed footer telling you about your tools and memory."""

# Appended to every persona's prompt at chat-start so the model knows what
# tools and memory it has. Kept short — the persona prompt is the user-facing
# voice; this is the operational reality.
TOOL_FOOTER = """

---

This is talk ([M]), not the lab ([$]). Tools you actually have:

- `search_project` — this journal / prior turns (other bodies tagged
  `[via <node>]` in history, `(via <node>)` on search hits). Recent turns
  are already inline; search when older context might help.
- `xai_docs` — official xAI docs (how Grok and the API actually run).
  Prefer this over web_search for models, Responses, Imagine, tools, limits.
- `web_search` / `x_search` — public facts the rider asked about *this turn*.
- `browser` / `plugin_call` when those apply.

You do **not** have write_file, edit_file, list_dir, bash, or code_execute.
Do not say you can create or edit files. If they want a file made, one
line: tap [$] and ask again. Do not web_search as a stand-in for a tool
you lack.

Do not look up the rider's home, street, GPS, or identity unless they
asked you to look that place up this turn. A pin or address in the
journal is memory, not a search request."""


def limb_addendum(cfg: Optional[object] = None) -> str:
    """Which named body this mouth is. Same soul; not a second throne.

    Empty when this install has not named its node — do not invent a body.
    """
    if cfg is None:
        try:
            from xlii.config import GlobalConfig

            cfg = GlobalConfig.load()
        except Exception:
            return ""
    try:
        from xlii.farm import job_gig, jobs_cfg
    except Exception:
        return ""
    node = str((jobs_cfg(cfg).get("node") or "") or "").strip()
    if not node:
        node = str(getattr(cfg, "node_name", "") or "").strip()
    if not node:
        return ""
    gig = job_gig(cfg)
    lines = [
        f"[BODY] You are speaking from fabric node `{node}`. "
        "You are the same Mojo as every other surface — this is a limb of "
        "one body, not a second mind and not a second throne."
    ]
    if gig:
        lines.append(f"This limb's larynx (chat backend) is `{gig}`.")
    lines.append(
        "Projects and files on this box belong to this limb. "
        "The throne is the center that pulls the journal home."
    )
    return "\n".join(lines)


# Persona names go on disk as filenames; keep them tame.
_VALID_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def is_valid_name(name: str) -> bool:
    return bool(_VALID_NAME.match(name))


def is_reserved_persona_name(name: str) -> bool:
    """True for seat words (``default``, ``mojo``) — not a real island."""
    return (name or "").strip().lower() in RESERVED_PERSONA_NAMES


@dataclass
class Persona:
    name: str

    @property
    def prompt_path(self) -> Path:
        return PERSONAS_DIR / f"{self.name}.md"

    @property
    def project_root(self) -> Path:
        """Working directory + project root for this persona's chat."""
        return CHAT_STATE_DIR / self.name

    @property
    def turns_dir(self) -> Path:
        return self.project_root / "turns"

    def exists(self) -> bool:
        return self.prompt_path.exists()

    def read_prompt(self) -> str:
        return self.prompt_path.read_text()

    def write_prompt(self, text: str) -> None:
        PERSONAS_DIR.mkdir(parents=True, exist_ok=True)
        self.prompt_path.write_text(text)

    def loadout(self) -> dict:
        """Parsed frontmatter loadout (plugins / docs / skills / model, …).

        Empty dict when the persona file has no frontmatter (the common case;
        backward-compatible). Materialized into session state on chat start.
        """
        meta, _ = parse_frontmatter(self.read_prompt())
        return meta

    def prompt_body(self) -> str:
        """The instruction text the model sees — frontmatter stripped."""
        _, body = parse_frontmatter(self.read_prompt())
        return body

    def system_prompt(self) -> str:
        """Persona instructions (frontmatter stripped) + the fixed tool/memory footer."""
        return self.prompt_body().rstrip() + TOOL_FOOTER

    def first_line(self) -> str:
        """One-line summary of the prompt for `--list` (frontmatter stripped)."""
        for line in self.prompt_body().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                return line[:100]
        return "(empty)"

    def collection_id(self) -> Optional[str]:
        """Return this persona's xAI collection_id, or None if not yet
        initialized. The persona project (and its Collection) are created
        lazily on first `xlii chat <name>` — until then there's nothing to
        attach to."""
        proj_json = self.project_root / ".xlii" / "project.json"
        if not proj_json.is_file():
            return None
        import json
        try:
            data = json.loads(proj_json.read_text())
        except (json.JSONDecodeError, OSError):
            return None
        cid = data.get("collection_id")
        return cid if cid else None

    def touch_used(self) -> None:
        """Record that this persona was used most recently. Used for naked
        `xlii chat` (no name) to pick the right persona."""
        marker = PERSONAS_DIR / ".last-used"
        PERSONAS_DIR.mkdir(parents=True, exist_ok=True)
        marker.write_text(self.name)


def list_personas() -> list[Persona]:
    if not PERSONAS_DIR.exists():
        return []
    out = []
    for p in sorted(PERSONAS_DIR.glob("*.md")):
        out.append(Persona(name=p.stem))
    return out


def list_bindable_personas() -> list[Persona]:
    """Personas a project may bind — never a leftover ``default.md`` role-word file."""
    return [
        p for p in list_personas()
        if p.name == DEFAULT_PERSONA_ID or not is_reserved_persona_name(p.name)
    ]


class FactoryRenameError(ValueError):
    """``rename_factory_persona`` refused the spelling."""


def rename_factory_persona(new_name: str, *, cfg: Optional[object] = None) -> str:
    """Put a name on the mobile journal (``mojo`` → ``stuart``).

    Moves the persona file and the ``~/.xlii/chat/<old>`` island. Does **not**
    adopt another existing persona. ``default`` / ``mojo`` are the seat, not a
    name you can choose.
    """
    wanted = (new_name or "").strip()
    if not is_valid_name(wanted):
        raise FactoryRenameError(
            f"invalid name {wanted!r}. Use letters, digits, _ . - "
            "(start with letter/digit; max 64 chars)."
        )
    if is_reserved_persona_name(wanted):
        raise FactoryRenameError(
            f"{wanted!r} already means the mobile journal (same seat as mojo). "
            f"Pick a name — or leave it {DEFAULT_PERSONA_ID!r}."
        )

    if cfg is None:
        from xlii.config import GlobalConfig

        cfg = GlobalConfig.load()

    old = factory_persona_id(cfg)
    if wanted == old:
        return old

    dest = Persona(wanted)
    if dest.exists():
        raise FactoryRenameError(
            f"{wanted!r} already exists — that is someone else's island. "
            "Pick a spelling that is not an existing persona."
        )

    src = Persona(old)
    old_root = src.project_root
    new_root = dest.project_root
    if new_root.exists():
        raise FactoryRenameError(
            f"chat island {new_root} already exists — refusing to overwrite"
        )

    if not src.exists():
        ensure_default_persona(old)
        src = Persona(old)
    dest.prompt_path.parent.mkdir(parents=True, exist_ok=True)
    src.prompt_path.rename(dest.prompt_path)

    old_root_s = str(old_root.resolve()) if old_root.exists() else ""
    if old_root.exists():
        new_root.parent.mkdir(parents=True, exist_ok=True)
        old_root.rename(new_root)
        try:
            from xlii.config import ProjectConfig

            pc = ProjectConfig.load(new_root)
            if pc is not None and pc.name == f"chat/{old}":
                pc.name = f"chat/{wanted}"
                pc.save()
        except Exception:
            # The directory rename already succeeded; a stale config name is cosmetic and must not fail it.
            pass

    marker = PERSONAS_DIR / ".last-used"
    try:
        if marker.exists() and marker.read_text().strip() == old:
            marker.write_text(wanted)
    except OSError:
        # The marker is a convenience pointer -- a stale one only affects which persona is offered first.
        pass

    cfg.fallback_persona = "" if wanted == DEFAULT_PERSONA_ID else wanted
    save = getattr(cfg, "save", None)
    if callable(save):
        try:
            save()
        except Exception:
            # The rename already landed on disk; failing to persist cfg just leaves the old pointer.
            pass

    _retarget_factory_binds(old, wanted, old_root_s, new_root)
    return wanted


def _retarget_factory_binds(old: str, new: str, old_path: str, new_root: Path) -> None:
    """Best-effort: registry path + project binds that pointed at the old factory id."""
    try:
        from xlii.registry import Registry

        reg = Registry.load()
        changed = False
        new_path = str(new_root.resolve()) if new_root.exists() else ""
        for e in reg.entries:
            if old_path and e.path == old_path and new_path:
                e.path = new_path
                changed = True
            if e.name == f"chat/{old}":
                e.name = f"chat/{new}"
                changed = True
        if changed:
            reg.save()
    except Exception:
        # Best-effort per the docstring: a stale registry entry is cosmetic and
        # must not fail the rename that triggered it.
        pass
    try:
        from xlii.config import ProjectConfig
        from xlii.registry import Registry

        for e in Registry.load().entries:
            pc = ProjectConfig.load(Path(e.path))
            if pc is not None and (getattr(pc, "bound_persona", None) or "") == old:
                pc.bound_persona = new
                pc.save()
    except Exception:
        # Same as the bind pass above: a stale registry entry is cosmetic and must not fail the rename.
        pass


def last_used() -> Optional[Persona]:
    marker = PERSONAS_DIR / ".last-used"
    if not marker.exists():
        return None
    name = marker.read_text().strip()
    if not name or not is_valid_name(name):
        return None
    p = Persona(name)
    return p if p.exists() else None


def open_in_editor(path: Path) -> int:
    """Open `path` in $EDITOR (or vi as fallback). Returns exit status.

    Caller is responsible for ensuring the file exists with starter content
    before calling this."""
    from xlii.editor import open_for_edit

    return open_for_edit(path)


def stock_personas_dir() -> Path:
    """Personas shipped with the package (mirrors `stock_roles`/`stock_skills`):
    the mojo starter template lives here and is copied into the user's config on
    install. Lowest precedence — a real persona on disk always wins."""
    return Path(__file__).resolve().parent / "stock_personas"


def stock_persona_prompt(display_name: str, *, template: str = DEFAULT_PERSONA_ID) -> str:
    """The bundled mojo persona text, with its `{name}` self-reference resolved to
    `display_name`. `.replace` (not `.format`) so literal braces in the body/loadout
    never trip substitution. Raises FileNotFoundError if the package asset is missing
    (a packaging error we want loud, not a silent fallback to a generic prompt)."""
    path = stock_personas_dir() / f"{template}.md"
    text = path.read_text(encoding="utf-8")
    return text.replace("{name}", display_name)


def create_persona(name: str, *, prompt: Optional[str] = None) -> Persona:
    """Create a new persona file with the given prompt (or DEFAULT_PROMPT).
    Does NOT init the project dir — caller does that next."""
    if not is_valid_name(name):
        raise ValueError(
            f"invalid persona name: {name!r}. Use letters, digits, _ . - only "
            "(start with letter/digit; max 64 chars)."
        )
    if is_reserved_persona_name(name) and name != DEFAULT_PERSONA_ID:
        raise ValueError(
            f"{name!r} is reserved for the factory companion (mojo). "
            f"Use {DEFAULT_PERSONA_ID!r} or pick another name."
        )
    p = Persona(name)
    if p.exists():
        raise FileExistsError(f"persona {name!r} already exists at {p.prompt_path}")
    p.write_prompt(prompt if prompt is not None else DEFAULT_PROMPT)
    return p


def ensure_default_persona(name: str = DEFAULT_PERSONA_ID) -> Persona:
    """Guarantee the shipped default companion exists — an install-time identity so
    a fresh machine always has something for naked `xlii chat` / binding.

    Seeds the prompt from the bundled mojo template (`stock_personas/mojo.md`); a
    custom `name` (from the setup naming ritual) reuses that template under the new
    identity. Idempotent and NON-clobbering: an existing persona of this name is
    returned untouched, so re-running setup never overwrites an edited companion.
    A leftover unnamed ``ixaac`` island is adopted as ``mojo`` once.

    Creates ONLY the prompt file (the project dir + Collection stay lazy,
    materialized on first `xlii chat` — same contract as `create_persona` and the
    lazy bootstrap in `_resolve_persona_to_load`)."""
    if name == DEFAULT_PERSONA_ID:
        _adopt_legacy_unnamed_ixaac()
    p = Persona(name)
    if not p.exists():
        display = DEFAULT_PERSONA_DISPLAY if name == DEFAULT_PERSONA_ID else name
        create_persona(name, prompt=stock_persona_prompt(display))
    return p


def _on_real_install() -> bool:
    """True when persona paths are the live ~/.config/xlii tree, not a test tmp."""
    return PERSONAS_DIR == GLOBAL_CONFIG_DIR / "personas"


def _adopt_legacy_unnamed_ixaac() -> None:
    """One-time: unnamed factory spelling was ``ixaac``. Move the island to mojo.

    Also heals the half-migrated case: mojo.md already exists (so the prompt
    move is a no-op) but the journal island still claims ``chat/ixaac`` in
    project.json, or ``fallback_persona`` is still the leftover spelling.
    Either leftover makes [M] / the daemon look up the costume and fail to
    open the journal.
    """
    old = Persona(_LEGACY_UNNAMED_ID)
    new = Persona(DEFAULT_PERSONA_ID)
    if not new.exists() and old.exists():
        new.prompt_path.parent.mkdir(parents=True, exist_ok=True)
        if old.prompt_path.exists() and not new.prompt_path.exists():
            old.prompt_path.rename(new.prompt_path)
        if old.project_root.exists() and not new.project_root.exists():
            new.project_root.parent.mkdir(parents=True, exist_ok=True)
            old.project_root.rename(new.project_root)
    _retarget_mojo_island_from_ixaac()
    if _on_real_install():
        _clear_legacy_ixaac_fallback()
        _retarget_legacy_ixaac_registry()


def _retarget_mojo_island_from_ixaac() -> None:
    """Re-bind a mojo island whose project.json still says it is chat/ixaac."""
    import json

    from xlii.atomicio import write_text_atomic

    root = Persona(DEFAULT_PERSONA_ID).project_root
    path = root / ".xlii" / "project.json"
    if not path.is_file():
        return
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return
    if not isinstance(data, dict):
        return
    name = str(data.get("name") or "")
    recorded = str(data.get("root") or "")
    actual = str(root.resolve())
    stale_name = name in (f"chat/{_LEGACY_UNNAMED_ID}", _LEGACY_UNNAMED_ID)
    stale_root = bool(recorded) and Path(recorded).resolve() != Path(actual)
    if recorded:
        try:
            stale_root = stale_root or Path(recorded).name == _LEGACY_UNNAMED_ID
        except (OSError, ValueError):
            # An unparseable recorded path leaves stale_root exactly as computed above.
            pass
    if not stale_name and not stale_root:
        return
    if stale_name:
        data["name"] = f"chat/{DEFAULT_PERSONA_ID}"
    data["root"] = actual
    try:
        write_text_atomic(path, json.dumps(data, indent=2) + "\n", mode=0o644)
    except OSError:
        return


def _clear_legacy_ixaac_fallback() -> None:
    try:
        from xlii.config import GlobalConfig

        cfg = GlobalConfig.load()
        if is_legacy_unnamed_journal(cfg.fallback_persona or ""):
            cfg.fallback_persona = ""
            cfg.save()
    except Exception:
        return


def _retarget_legacy_ixaac_registry() -> None:
    """Ghost ``chat/ixaac`` row whose tree moved to mojo — point it at mojo."""
    mojo_root = Persona(DEFAULT_PERSONA_ID).project_root
    ixaac_root = Persona(_LEGACY_UNNAMED_ID).project_root
    if not mojo_root.is_dir() or ixaac_root.exists():
        return
    try:
        from xlii.registry import Registry

        reg = Registry.load()
        changed = False
        mojo_path = str(mojo_root.resolve())
        try:
            old_path = str(ixaac_root.resolve())
        except OSError:
            old_path = str(ixaac_root)
        for e in reg.entries:
            if e.path != old_path and e.name != f"chat/{_LEGACY_UNNAMED_ID}":
                continue
            try:
                live = Path(e.path).exists()
            except OSError:
                live = False
            if live and Path(e.path).resolve() != mojo_root.resolve():
                continue
            e.path = mojo_path
            e.name = f"chat/{DEFAULT_PERSONA_ID}"
            changed = True
        if changed:
            reg.save()
    except Exception:
        return


def ensure_stock_persona(name: str) -> Persona:
    """Guarantee a persona seeded from the stock template of the SAME name
    (``stock_personas/<name>.md``) — the generic form of
    :func:`ensure_default_persona`, for personas a feature ships (the
    research workbench's non-editing companion, typed-workbenches B2).
    Idempotent and non-clobbering; raises FileNotFoundError when no stock
    template of that name ships (a packaging error, loud on purpose).

    Does **not** mint jobs. Stock chat voices are files; jobs are ``/role``.
    """
    if not is_valid_name(name):
        raise ValueError(f"invalid persona name: {name!r}")
    p = Persona(name)
    if not p.exists():
        display = CHAT_DEFAULT_PERSONA_DISPLAY if name == CHAT_DEFAULT_PERSONA_ID else name
        create_persona(name, prompt=stock_persona_prompt(display, template=name))
    return p


def delete_persona(name: str) -> tuple[bool, bool]:
    """Remove the prompt file and the project state dir.

    Returns (prompt_removed, state_removed). Caller is responsible for
    deleting the remote Collection (use the project's collection_id and
    `xlii gc` style cleanup).
    """
    import shutil
    p = Persona(name)
    prompt_removed = False
    state_removed = False
    if p.prompt_path.exists():
        p.prompt_path.unlink()
        prompt_removed = True
    if p.project_root.exists():
        shutil.rmtree(p.project_root)
        state_removed = True
    return prompt_removed, state_removed
