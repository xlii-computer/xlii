"""Mode contract (godzilla-mothra V0d): the registry must DESCRIBE today's
modes truthfully, so Stage 3 flipping enforcement ON starts from truth.

Every pin here compares declarative contract data against the live behavior
surface it mirrors: the palette functions in xlii.tool_schemas, the
controllers' per-stage palettes and write scopes, the trust-tier constants in
xlii.session_state, and the per-REPL command registration (REPLCommand.repls).
The contract module itself must stay kernel-pure (stdlib-only imports) — the
tests may import anything.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import xlii.mode_contract as mc
from xlii import session_state
from xlii.commands import find_repl_command
from xlii.debug_mode import DebugController, DebugPhase
from xlii.mode_controller import (
    DiscoveryController,
    OpsController,
    PlanController,
    non_planner_write_scope,
)
from xlii.rail import RailController, RailStage
from xlii.repl_cmds import register_all
from xlii.tool_schemas import (
    BUILTIN_TOOLS,
    WORKER_REGISTRY,
    WRITER_REGISTRY,
    debug_instrument_schemas,
    debug_reproduce_schemas,
    ops_mode_schemas,
    plan_mode_schemas,
    plan_write_schemas,
    tool_schemas,
    worker_tool_schemas,
)

BUILTIN_NAMES = {t.name for t in BUILTIN_TOOLS}


def _names(schemas: list[dict]) -> set[str]:
    return {s["function"]["name"] for s in schemas}


def _builtin_names(schemas: list[dict]) -> set[str]:
    """Advertised names, project-tool-proof: other tests in the same process
    may have loaded .xlii project tools into the global palette; the built-in
    subset is the stable truth the snapshots pin."""
    return _names(schemas) & BUILTIN_NAMES


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #


def test_registry_describes_every_live_mode():
    assert mc.mode_names() == (
        "chat",
        "code",
        "debug",
        "default",
        "discovery",
        "harness",
        "howto",
        "ops",
        "plan",
        "rail",
        "scratch",
    )


def test_live_overlays_and_controllers_have_contracts():
    """The live→contract ratchet: a NEW overlay flag or mode controller added
    to the tree must gain a contract before this suite goes green again. (The
    surface/harness families have no mechanical enumeration to pin against —
    controllers are Protocol-conformant, not subclasses — so this covers the
    two families that DO have one.)"""
    import dataclasses
    import re

    # Every OverlayState *_mode flag is a described overlay (howto, image, …).
    overlay_modes = {
        f.name[: -len("_mode")]
        for f in dataclasses.fields(session_state.OverlayState)
        if f.name.endswith("_mode")
    }
    assert overlay_modes, "OverlayState grew out from under this pin"
    for name in overlay_modes:
        assert mc.get_mode(name).kind == mc.KIND_OVERLAY, name

    # Every controller class constructed at a set_mode entry point is a
    # described controller mode.
    root = Path(mc.__file__).resolve().parent
    entry_sources = (
        (root / "repl_cmds" / "mode.py").read_text()
        + (root / "agent.py").read_text()
    )
    controllers = set(re.findall(r"\b([A-Z]\w+?)Controller\(", entry_sources))
    assert controllers >= {"Plan", "Discovery", "Ops", "Rail", "Debug"}
    for cls in controllers:
        contract = mc.get_mode(cls.lower())
        assert contract.kind == mc.KIND_CONTROLLER, cls


def test_unknown_mode_is_an_explicit_error():
    with pytest.raises(mc.UnknownModeError) as exc:
        mc.get_mode("warp")
    # An explicit, self-describing refusal — and a KeyError so existing
    # dict-shaped call sites can catch it without importing the module.
    assert "warp" in str(exc.value)
    assert "plan" in str(exc.value)
    assert isinstance(exc.value, KeyError)


def test_alias_resolves_to_the_same_contract():
    assert mc.get_mode("research") is mc.get_mode("discovery")
    assert mc.get_mode("research").name == "discovery"


def test_duplicate_registration_is_refused(monkeypatch):
    monkeypatch.setattr(mc, "_REGISTRY", {})
    a = mc.ModeContract(name="m", kind=mc.KIND_OVERLAY, awareness=mc.AWARENESS_READ)
    mc.register_mode(a)
    with pytest.raises(ValueError, match="collision"):
        mc.register_mode(a)
    # replace=True may update the same mode…
    mc.register_mode(
        mc.ModeContract(name="m", kind=mc.KIND_OVERLAY, awareness=mc.AWARENESS_WRITE),
        replace=True,
    )
    assert mc.get_mode("m").awareness == mc.AWARENESS_WRITE
    # …but never lets a DIFFERENT mode steal a taken name/alias.
    with pytest.raises(ValueError, match="collision"):
        mc.register_mode(
            mc.ModeContract(
                name="other",
                kind=mc.KIND_OVERLAY,
                awareness=mc.AWARENESS_READ,
                aliases=("m",),
            ),
            replace=True,
        )


def test_replace_purges_dropped_aliases(monkeypatch):
    monkeypatch.setattr(mc, "_REGISTRY", {})
    mc.register_mode(
        mc.ModeContract(
            name="m", kind=mc.KIND_OVERLAY, awareness=mc.AWARENESS_READ, aliases=("mm",)
        )
    )
    # Replacing without the alias must not leave /mm resolving to the dead
    # contract — the silent-shadowing failure the registry exists to prevent.
    mc.register_mode(
        mc.ModeContract(name="m", kind=mc.KIND_OVERLAY, awareness=mc.AWARENESS_WRITE),
        replace=True,
    )
    assert mc.get_mode("m").awareness == mc.AWARENESS_WRITE
    with pytest.raises(mc.UnknownModeError):
        mc.get_mode("mm")


def test_self_colliding_aliases_are_refused(monkeypatch):
    monkeypatch.setattr(mc, "_REGISTRY", {})
    with pytest.raises(ValueError, match="repeats"):
        mc.register_mode(
            mc.ModeContract(
                name="m",
                kind=mc.KIND_OVERLAY,
                awareness=mc.AWARENESS_READ,
                aliases=("m",),
            )
        )


def test_all_modes_yields_one_entry_per_mode():
    modes = mc.all_modes()
    assert len(modes) == len(mc.mode_names())
    assert mc.get_mode("discovery") in modes  # aliased mode appears once


def test_contract_validation_rejects_unknown_vocabulary():
    with pytest.raises(ValueError, match="kind"):
        mc.ModeContract(name="x", kind="popup", awareness=mc.AWARENESS_READ)
    with pytest.raises(ValueError, match="awareness"):
        mc.ModeContract(name="x", kind=mc.KIND_OVERLAY, awareness="omniscient")
    with pytest.raises(ValueError, match="awareness"):
        mc.ModeStage("s", mc.PERMIT_ALL, "omniscient")


# --------------------------------------------------------------------------- #
# NamePolicy — allowlist/denylist semantics, defined once
# --------------------------------------------------------------------------- #


def test_name_policy_semantics():
    assert mc.NamePolicy.everything().permits("anything")
    only = mc.NamePolicy.allow_only({"read_file"})
    assert only.permits("read_file") and not only.permits("bash")
    strip = mc.NamePolicy.all_except({"bash"})
    assert strip.permits("read_file") and not strip.permits("bash")
    # Deny wins — the write_scope precedence, restated for names.
    both = mc.NamePolicy(allow=frozenset({"bash"}), deny=frozenset({"bash"}))
    assert not both.permits("bash")


# --------------------------------------------------------------------------- #
# Trust tiers + the entry-gate evaluator
# --------------------------------------------------------------------------- #


def test_trust_tiers_mirror_session_state():
    # The module stays kernel-pure by mirroring the strings; this is the pin
    # that keeps the mirror honest.
    assert (mc.TIER_SAFE, mc.TIER_YOLO, mc.TIER_FREEBALL) == (
        session_state.TIER_SAFE,
        session_state.TIER_YOLO,
        session_state.TIER_FREEBALL,
    )
    assert [mc.trust_tier_rank(t) for t in mc.TRUST_TIERS] == [0, 1, 2]
    with pytest.raises(ValueError, match="unknown trust tier"):
        mc.trust_tier_rank("sudo")


def test_entry_gate_evaluator_truth_table():
    gate = mc.EntryGate(
        requires_project=True,
        min_trust_tier=mc.TIER_YOLO,
        not_during_loop=True,
        surfaces=(mc.SURFACE_CODE,),
    )
    ok = gate.evaluate(
        mc.GateContext(has_project=True, trust_tier=mc.TIER_YOLO, surface="code")
    )
    assert ok and ok.reasons == ()
    # Freeball satisfies a yolo floor (ladder, not equality).
    assert gate.evaluate(
        mc.GateContext(has_project=True, trust_tier=mc.TIER_FREEBALL, surface="code")
    )
    # Every failed condition is reported, not just the first.
    verdict = gate.evaluate(
        mc.GateContext(
            has_project=False,
            trust_tier=mc.TIER_SAFE,
            loop_active=True,
            surface="chat",
        )
    )
    assert not verdict and len(verdict.reasons) == 4
    joined = " ".join(verdict.reasons)
    assert "project" in joined and "trust tier" in joined
    assert "/loop" in joined and "chat" in joined


def test_unknown_vocabulary_fails_at_construction():
    # Unknown tiers/surfaces never default-allow or default-deny — they raise
    # the moment they exist, not at the first gate that happens to check them.
    with pytest.raises(ValueError, match="unknown trust tier"):
        mc.GateContext(trust_tier="sudo")
    with pytest.raises(ValueError, match="unknown surface"):
        mc.GateContext(surface="kode")
    with pytest.raises(ValueError, match="unknown trust tier"):
        mc.EntryGate(min_trust_tier="sudo")
    with pytest.raises(ValueError, match="unknown surface"):
        mc.EntryGate(surfaces=("kode",))
    with pytest.raises(ValueError, match="at least one surface"):
        mc.EntryGate(surfaces=())


def test_frozen_types_detach_from_callers_containers():
    # frozen= only freezes the reference; the coercion in __post_init__ is
    # what stops a caller's live set from mutating a policy after the fact.
    live = {"read_file"}
    policy = mc.NamePolicy(allow=live)
    live.add("bash")
    assert not policy.permits("bash")
    assert isinstance(policy.allow, frozenset) and isinstance(policy.deny, frozenset)
    gate = mc.EntryGate(surfaces=["code"])
    assert gate.surfaces == ("code",)
    contract = mc.ModeContract(
        name="tmp",
        kind=mc.KIND_OVERLAY,
        awareness=mc.AWARENESS_READ,
        aliases=["t"],
        stages=[mc.ModeStage("s", mc.PERMIT_ALL, mc.AWARENESS_READ)],
    )
    assert isinstance(contract.aliases, tuple) and isinstance(contract.stages, tuple)


def test_open_gate_defaults():
    verdict = mc.EntryGate().evaluate(mc.GateContext())
    assert verdict and verdict.reasons == ()


def test_described_gates_match_todays_refusals():
    # Every controller-mode entry command applies the "loop is active —
    # /loop off first" refusal; default (no controller) has no entry at all.
    for name in ("plan", "rail", "debug", "discovery", "ops"):
        assert mc.get_mode(name).entry.not_during_loop, name
    assert not mc.get_mode("default").entry.not_during_loop
    # No mode gates on the trust ladder today — the field exists for Stage 3.
    for contract in mc.all_modes():
        assert contract.entry.min_trust_tier is None, contract.name
    # code is the only project-gated door today (the V3a gate).
    assert mc.get_mode("code").entry.requires_project
    refusal = mc.get_mode("code").entry.evaluate(mc.GateContext(has_project=False))
    assert not refusal and "project" in refusal.reasons[0]
    for name in ("chat", "scratch", "howto", "harness", "plan"):
        assert not mc.get_mode(name).entry.requires_project, name


# --------------------------------------------------------------------------- #
# Tool palettes: contract snapshots vs the live palette functions
# --------------------------------------------------------------------------- #


def test_read_only_snapshot_matches_plan_mode_palette():
    assert mc.READ_ONLY_TOOLS == {t.name for t in BUILTIN_TOOLS if t.plan_mode_safe}
    assert _builtin_names(plan_mode_schemas()) == mc.READ_ONLY_TOOLS


def test_plan_contract_matches_plan_write_palette():
    policy = mc.get_mode("plan").capabilities.tools
    live = _builtin_names(plan_write_schemas())
    assert policy.allow == live == mc.PLAN_WRITE_TOOLS
    # Plan-mode's read-only-ness, at the type level: the palette carries the
    # plans/-domain writers but never shell, fan-out, or code execution.
    for granted in ("read_file", "grep", "write_file", "edit_file"):
        assert policy.permits(granted)
    for withheld in ("bash", "dispatch_subagent", "code_execute", "send_email"):
        assert not policy.permits(withheld)


def test_discovery_contract_matches_read_only_palette():
    policy = mc.get_mode("discovery").capabilities.tools
    assert policy.allow == mc.READ_ONLY_TOOLS
    assert not policy.permits("write_file") and not policy.permits("bash")


def test_ops_contract_matches_ops_palette():
    contract = mc.get_mode("ops")
    policy = contract.capabilities.tools
    assert policy.allow == _builtin_names(ops_mode_schemas()) == mc.OPS_TOOLS
    assert policy.permits("bash") and not policy.permits("write_file")
    # Awareness records capability, not posture: no file writers, but an
    # unrestricted bash (and codex agent runs — Agent.read_only_tool_palette
    # is False for OpsController) can mutate the repo.
    assert contract.awareness == mc.AWARENESS_WRITE


def test_default_contract_is_the_open_palette():
    contract = mc.get_mode("default")
    policy = contract.capabilities.tools
    # allow=None is deliberate: project tools and the conversational
    # request_deep_search extend the default palette at runtime, so an
    # enumerated allowlist would be a lie.
    assert policy.allow is None and policy.deny == frozenset()
    for name in _names(tool_schemas()) | {"dispatch_subagent", "request_deep_search"}:
        assert policy.permits(name)
    assert contract.awareness == mc.AWARENESS_WRITE


def test_chat_contract_is_the_safe_repl():
    # V3b landed: chat's contract now carries the stripped palette (write tools
    # + subagent dispatch gone), the conversation-local slash allowlist, and
    # project-blind as the default awareness. The old "unstripped" pin flipped
    # exactly as its comment said it would.
    contract = mc.get_mode("chat")
    tools = contract.capabilities.tools
    assert tools.allow is not None
    assert "read_file" not in tools.allow
    assert "write_file" not in tools.allow
    assert "bash" not in tools.allow
    assert "search_project" in tools.allow       # own-memory recall stays
    assert "web_search" in tools.allow           # external reads stay
    assert "xai_docs" in tools.allow             # DNA: how Grok/the API run
    slash = contract.capabilities.slash_commands
    assert slash.allow is not None
    assert "help" in slash.allow and "recall" in slash.allow
    assert "sync" not in slash.allow and "yolo" not in slash.allow
    assert contract.awareness == mc.AWARENESS_PROJECT_BLIND


def test_chat_tools_policy_read_proj_widens_to_read_only():
    blind = mc.chat_tools_policy()
    assert blind.permits("search_project") and blind.permits("web_search")
    assert blind.permits("xai_docs")
    assert not blind.permits("read_file") and not blind.permits("bash")
    assert not blind.permits("write_file") and not blind.permits("dispatch_subagent")
    read = mc.chat_tools_policy(read_proj=True)
    assert read.permits("read_file") and read.permits("grep")
    assert read.permits("request_deep_search")
    assert read.permits("xai_docs")
    assert not read.permits("bash") and not read.permits("write_file")


# --------------------------------------------------------------------------- #
# Staged modes: walk the live controllers stage-by-stage
# --------------------------------------------------------------------------- #


def _assert_stage_palette(stage: mc.ModeStage, advertised_builtin: set[str]) -> None:
    if stage.tools.allow is not None:
        assert advertised_builtin == stage.tools.allow, stage.name
    else:
        # Open stage: the full write palette is advertised.
        assert {"write_file", "edit_file", "bash"} <= advertised_builtin
    for name in advertised_builtin:
        assert stage.tools.permits(name), (stage.name, name)


def test_rail_stages_match_controller():
    contract = mc.get_mode("rail")
    ctrl = RailController()
    assert [s.name for s in contract.stages] == [s.name.lower() for s in RailStage]
    for i, stage in enumerate(contract.stages):
        advertised = _names(ctrl.tool_schemas()) & (BUILTIN_NAMES | {"dispatch_subagent"})
        _assert_stage_palette(stage, advertised - {"dispatch_subagent"})
        # Awareness mirrors the controller's read-only gate exactly.
        assert (stage.awareness == mc.AWARENESS_READ) == ctrl.is_read_only_stage, (
            stage.name
        )
        assert ctrl.suppresses_claim_check == ctrl.is_read_only_stage
        if i < len(contract.stages) - 1:
            assert ctrl.advance()
    assert not ctrl.advance()  # SELF_REVIEW is terminal


def test_debug_stages_match_controller():
    contract = mc.get_mode("debug")
    ctrl = DebugController()
    assert [s.name for s in contract.stages] == [p.name.lower() for p in DebugPhase]
    expected_tool_mode = {
        "hypothesize": "read_only",
        "instrument": "instrument",
        "reproduce": "reproduce",
        "analyze": "read_only",
        "fix": "write",
        "verify": "read_only",
    }
    for i, stage in enumerate(contract.stages):
        assert ctrl.tool_mode == expected_tool_mode[stage.name], stage.name
        advertised = _names(ctrl.tool_schemas()) & (BUILTIN_NAMES | {"dispatch_subagent"})
        _assert_stage_palette(stage, advertised - {"dispatch_subagent"})
        # Awareness == "can this stage mutate the repo": instrument's
        # marker-gated edit_file counts, and so does reproduce's bash — a
        # shell is a writer, whatever the directive says.
        can_mutate = any(
            stage.tools.permits(t) for t in ("write_file", "edit_file", "bash")
        )
        assert (stage.awareness == mc.AWARENESS_WRITE) == can_mutate, stage.name
        if i < len(contract.stages) - 1:
            assert ctrl.advance()
    assert not ctrl.advance()  # VERIFY is terminal


def test_debug_stage_snapshots_match_palette_functions():
    assert _builtin_names(debug_instrument_schemas()) == mc.DEBUG_INSTRUMENT_TOOLS
    assert _builtin_names(debug_reproduce_schemas()) == mc.DEBUG_REPRODUCE_TOOLS


def test_staged_awareness_envelope():
    order = {level: i for i, level in enumerate(mc.AWARENESS_LEVELS)}
    for contract in mc.all_modes():
        if contract.stages:
            assert order[contract.awareness] == max(
                order[s.awareness] for s in contract.stages
            ), contract.name


# --------------------------------------------------------------------------- #
# Write scopes: the awareness claims tie back to controller write_scope
# --------------------------------------------------------------------------- #


def test_plan_awareness_read_is_the_plans_only_write_scope(tmp_path):
    plans = str((tmp_path / "plans").resolve())
    # The planner writes ONLY the plan domain — repo-read-only, hence "read".
    assert PlanController().write_scope(tmp_path) == ((plans,), ())
    assert mc.get_mode("plan").awareness == mc.AWARENESS_READ


def test_non_planner_modes_deny_the_plan_domain(tmp_path):
    plans = str((tmp_path / "plans").resolve())
    expected = (None, (plans,))
    assert non_planner_write_scope(tmp_path) == expected
    for ctrl in (
        OpsController(),
        DiscoveryController(),
        RailController(),
        DebugController(),
    ):
        assert ctrl.write_scope(tmp_path) == expected, type(ctrl).__name__


# --------------------------------------------------------------------------- #
# Surfaces: entry.surfaces mirrors the live per-REPL command registration
# --------------------------------------------------------------------------- #

# mode name -> the slash command that enters it (default has no entry command;
# harness's canonical door today is the /cursor foreground toggle).
_ENTRY_COMMANDS = {
    "plan": "/plan",
    "discovery": "/discovery",
    "ops": "/ops",
    "rail": "/rail",
    "debug": "/debug",
    "code": "/code",
    "chat": "/chat",
    "scratch": "/scratch",
    "howto": "/howto",
    "harness": "/cursor",
}


def test_entry_surfaces_match_command_registration():
    register_all()
    for mode_name, command in _ENTRY_COMMANDS.items():
        contract = mc.get_mode(mode_name)
        for surface in mc.ALL_SURFACES:
            registered = find_repl_command(command, repl=surface) is not None
            declared = surface in contract.entry.surfaces
            assert registered == declared, (mode_name, surface)


def test_discovery_alias_matches_command_alias():
    register_all()
    assert find_repl_command("/research", repl="code") is not None
    assert mc.get_mode("research").name == "discovery"


# --------------------------------------------------------------------------- #
# Worker toolset shapes: the types must express them (V0d "done when")
# --------------------------------------------------------------------------- #


def test_worker_role_shapes_are_expressible():
    general = _builtin_names(worker_tool_schemas(writer=False, role="general"))
    explore = _names(worker_tool_schemas(writer=False, role="explore"))
    bash_role = _names(worker_tool_schemas(writer=False, role="bash"))
    writer = _builtin_names(worker_tool_schemas(writer=True, role="general"))

    # The live shapes: role allowlists narrow the read-only worker set;
    # writer adds exactly the two file writers.
    assert general == {t.name for t in BUILTIN_TOOLS if t.worker_safe}
    assert explore < general and "bash" not in explore and "read_file" in explore
    assert bash_role == {"read_file", "bash"}
    assert writer == general | {"write_file", "edit_file"}
    assert set(WORKER_REGISTRY) == general
    assert set(WRITER_REGISTRY) == writer

    # Expressed as contract types — a CapabilityProfile per role, no tui.
    for shape in (general, explore, bash_role, writer):
        profile = mc.CapabilityProfile(tools=mc.NamePolicy.allow_only(shape))
        assert all(profile.tools.permits(n) for n in shape)
    explore_policy = mc.NamePolicy.allow_only(explore)
    assert not explore_policy.permits("bash")
    assert not mc.NamePolicy.allow_only(general).permits("write_file")


# --------------------------------------------------------------------------- #
# Kernel purity: the module must pass V0a's import contract on its own
# --------------------------------------------------------------------------- #

_STDLIB_ALLOWED = {"__future__", "dataclasses", "typing"}


def test_module_is_kernel_pure():
    tree = ast.parse(Path(mc.__file__).read_text())
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.partition(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            roots.add((node.module or "").partition(".")[0])
    assert roots <= _STDLIB_ALLOWED, roots
    # Belt and braces: the face packages must never appear, even lazily.
    for banned in ("xlii", "rich", "textual", "prompt_toolkit"):
        assert banned not in roots
