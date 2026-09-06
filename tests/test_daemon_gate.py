"""Fabric F4 — the daemon's security gate (config + rate limiter), unit-tested.

These live in xlii/daemon_gate.py precisely so they're testable without the
[daemon] extra (no slixmpp import). The gate is what stands between a remote
message and a verb/agent run, so its policy is pinned here:
  - device trust is OFF by default (blind_trust must be explicitly opted in);
  - the rate limiter accepts under the cap, denies over it, and locks out
    repeat offenders, per-JID, on a sliding window.

Run directly:  ./venv/bin/python -m pytest tests/test_daemon_gate.py
"""

import pytest
from types import SimpleNamespace

from xlii import daemon_gate
from xlii.daemon_gate import (
    KEY_ENV,
    DaemonConfig,
    DaemonLaunchDecision,
    NotifyConfig,
    RateLimiter,
    evaluate_daemon_launch,
    normalize_fingerprint,
    omemo_device_trusted,
    valid_bare_jid,
)


# --------------------------------------------------------------------------- #
#  Operator input validation for `xlii daemon trust`
# --------------------------------------------------------------------------- #

def test_normalize_fingerprint_strips_separators_and_lowercases():
    hex64 = "deadbeef0123abcd00112233445566778899aabbccddeeff0123456789abcdef"
    assert len(hex64) == 64
    # how clients present it: 8 groups of 8, uppercased, space-separated
    grouped = " ".join(hex64[i:i + 8] for i in range(0, 64, 8)).upper()
    assert normalize_fingerprint(grouped) == hex64
    # colons (some clients) are stripped too
    assert normalize_fingerprint("aa:bb:" + "c" * 60) == "aabb" + "c" * 60


def test_normalize_fingerprint_rejects_wrong_length_or_nonhex():
    with pytest.raises(ValueError):
        normalize_fingerprint("abc")            # too short
    with pytest.raises(ValueError):
        normalize_fingerprint("z" * 64)         # non-hex
    with pytest.raises(ValueError):
        normalize_fingerprint("a" * 65)         # too long
    with pytest.raises(ValueError):
        normalize_fingerprint("")               # empty


@pytest.mark.parametrize("jid", ["me@phone.tailnet", "a@b", "user.name@host-1.example"])
def test_valid_bare_jid_accepts_bare(jid):
    assert valid_bare_jid(jid)


@pytest.mark.parametrize("jid", [
    "me@phone.tailnet/resource",  # has a resource
    "noatsign",
    "two@at@signs",
    "@nolocal",
    "nodomain@",
    "white space@host",
    "",
])
def test_valid_bare_jid_rejects_malformed(jid):
    assert not valid_bare_jid(jid)


# --------------------------------------------------------------------------- #
#  Inbound OMEMO device trust
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("level", ["TRUSTED", "BLINDLY_TRUSTED"])
def test_omemo_device_trusted_accepts_trusted_levels(level):
    assert omemo_device_trusted(SimpleNamespace(trust_level_name=level))


@pytest.mark.parametrize("level", ["UNDECIDED", "DISTRUSTED", None, ""])
def test_omemo_device_trusted_rejects_untrusted_levels(level):
    assert not omemo_device_trusted(SimpleNamespace(trust_level_name=level))


# --------------------------------------------------------------------------- #
#  DaemonConfig — secure default for device trust
# --------------------------------------------------------------------------- #

def _write_cfg(tmp_path, extra=""):
    p = tmp_path / "daemon.toml"
    p.write_text(
        '[daemon]\n'
        'jid = "daemon@desktop.tailnet"\n'
        '[whitelist]\n'
        'allowed_jids = ["me@phone.tailnet"]\n'
        + extra
    )
    return p


def test_blind_trust_defaults_to_false_when_absent(tmp_path):
    cfg = DaemonConfig.load(_write_cfg(tmp_path))
    assert cfg.blind_trust is False          # secure default
    assert cfg.allowed_jids == ["me@phone.tailnet"]
    assert cfg.jid == "daemon@desktop.tailnet"


def test_blind_trust_can_be_opted_in(tmp_path):
    cfg = DaemonConfig.load(_write_cfg(tmp_path, "[trust]\nblind_trust = true\n"))
    assert cfg.blind_trust is True


def test_node_name_defaults_empty_and_parses_from_daemon_table(tmp_path):
    # Fabric: the body's name (throne, node1, …) — surfaced in presence + /whoami,
    # and the provenance key for `xlii fabric pull`.
    assert DaemonConfig.load(_write_cfg(tmp_path)).node_name == ""
    p = tmp_path / "daemon.toml"
    p.write_text(
        '[daemon]\n'
        'jid = "node1@home.example"\n'
        'node_name = "node1"\n'
        '[whitelist]\n'
        'allowed_jids = ["me@phone"]\n'
    )
    assert DaemonConfig.load(p).node_name == "node1"


def test_config_defaults_for_optional_sections(tmp_path):
    cfg = DaemonConfig.load(_write_cfg(tmp_path))
    assert cfg.password_env == "XMPP_DAEMON_PASSWORD"
    assert cfg.max_per_minute == 10
    assert cfg.lockout_threshold == 5
    assert cfg.lockout_duration_s == 300
    assert cfg.fallback_enabled is True


def test_config_overrides_parse(tmp_path):
    cfg = DaemonConfig.load(_write_cfg(
        tmp_path,
        '[rate_limit]\nmax_per_minute = 3\nlockout_threshold = 2\nlockout_duration_s = 30\n'
        '[agent_fallback]\nenabled = false\ndefault_workspace = "myproj"\n',
    ))
    assert (cfg.max_per_minute, cfg.lockout_threshold, cfg.lockout_duration_s) == (3, 2, 30)
    assert cfg.fallback_enabled is False
    assert cfg.fallback_workspace == "myproj"


# --------------------------------------------------------------------------- #
#  Launch policy — secure defaults (Vector E)
# --------------------------------------------------------------------------- #

def test_launch_policy_defaults_secure(tmp_path):
    cfg = DaemonConfig.load(_write_cfg(tmp_path))
    assert cfg.keyed is True             # keyed by default — a key must be presented
    assert cfg.autostart is False
    assert cfg.always_on is False


def test_launch_policy_overrides_parse(tmp_path):
    cfg = DaemonConfig.load(_write_cfg(
        tmp_path, "[policy]\nkeyed = false\nautostart = true\nalways_on = true\n"
    ))
    assert cfg.keyed is False
    assert cfg.autostart is True
    assert cfg.always_on is True


# --------------------------------------------------------------------------- #
#  Keyed launch gate — the daemon's function chokepoint (Vector E)
# --------------------------------------------------------------------------- #

def test_keyed_launch_verifies_provided_key():
    d = evaluate_daemon_launch(
        keyed=True, key_is_set=True, provided_key="s3cret",
        verify_key=lambda k: k == "s3cret",
    )
    assert isinstance(d, DaemonLaunchDecision)
    assert d.allowed is True
    assert "verified" in d.reason


def test_keyed_launch_refuses_when_no_key_set():
    d = evaluate_daemon_launch(
        keyed=True, key_is_set=False, provided_key="anything", verify_key=lambda k: True,
    )
    assert d.allowed is False
    assert "no admin key" in d.reason


def test_keyed_launch_refuses_without_env_key():
    d = evaluate_daemon_launch(
        keyed=True, key_is_set=True, provided_key=None, verify_key=lambda k: True,
    )
    assert d.allowed is False
    assert KEY_ENV in d.reason


def test_keyed_launch_refuses_on_mismatch():
    d = evaluate_daemon_launch(
        keyed=True, key_is_set=True, provided_key="bad", verify_key=lambda k: False,
    )
    assert d.allowed is False
    assert "does not match" in d.reason


def test_keyless_launch_allowed_but_warns():
    d = evaluate_daemon_launch(
        keyed=False, key_is_set=False, provided_key=None, verify_key=lambda k: False,
    )
    assert d.allowed is True
    assert any("keyed = false" in w for w in d.warnings)


def test_launch_surfaces_loosened_policy_warnings():
    d = evaluate_daemon_launch(
        keyed=True, key_is_set=True, provided_key="s", verify_key=lambda k: True,
        blind_trust=True, autostart=True, always_on=True,
    )
    assert d.allowed is True             # a clean keyed launch, but loud
    text = " ".join(d.warnings)
    assert "blind_trust" in text and "autostart" in text and "always_on" in text


# --------------------------------------------------------------------------- #
#  NotifyConfig — the send-only rail (F1)
# --------------------------------------------------------------------------- #

def test_notify_config_parses_with_secure_defaults(tmp_path):
    p = tmp_path / "notify.toml"
    p.write_text(
        '[notify]\n'
        'jid = "me@desktop.tailnet"\n'
        'recipient = "me@phone.tailnet"\n'
    )
    cfg = NotifyConfig.load(p)
    assert cfg.jid == "me@desktop.tailnet"
    assert cfg.recipient == "me@phone.tailnet"
    assert cfg.password_env == "XMPP_NOTIFY_PASSWORD"   # default
    assert cfg.blind_trust is False                      # secure default
    assert cfg.state_file.name == "notify-omemo-state.json"


def test_notify_config_blind_trust_opt_in(tmp_path):
    p = tmp_path / "notify.toml"
    p.write_text(
        '[notify]\njid = "a@x"\nrecipient = "b@y"\n[trust]\nblind_trust = true\n'
    )
    assert NotifyConfig.load(p).blind_trust is True


# --------------------------------------------------------------------------- #
#  RateLimiter — abuse gate
# --------------------------------------------------------------------------- #

class _Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def _patch_clock(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(daemon_gate.time, "time", clock)
    return clock


def test_accepts_under_the_limit(monkeypatch):
    _patch_clock(monkeypatch)
    rl = RateLimiter(max_per_minute=3, lockout_threshold=5, lockout_duration_s=60)
    for _ in range(3):
        ok, _ = rl.check("a@x")
        assert ok


def test_denies_over_the_limit(monkeypatch):
    _patch_clock(monkeypatch)
    rl = RateLimiter(max_per_minute=2, lockout_threshold=5, lockout_duration_s=60)
    assert rl.check("a@x")[0]
    assert rl.check("a@x")[0]
    ok, reason = rl.check("a@x")
    assert not ok and "rate limit" in reason


def test_lockout_after_repeated_denials(monkeypatch):
    _patch_clock(monkeypatch)
    rl = RateLimiter(max_per_minute=1, lockout_threshold=2, lockout_duration_s=60)
    assert rl.check("a@x")[0]            # 1 accept (fills the window)
    rl.check("a@x")                       # denial 1
    ok, reason = rl.check("a@x")          # denial 2 → lockout
    assert not ok and "locked out" in reason


def test_lockout_expires(monkeypatch):
    clock = _patch_clock(monkeypatch)
    # lockout (90s) longer than the 60s rate window, so the lockout is what's
    # binding and its expiry is what we're testing.
    rl = RateLimiter(max_per_minute=1, lockout_threshold=2, lockout_duration_s=90)
    rl.check("a@x"); rl.check("a@x"); rl.check("a@x")   # → locked out
    assert not rl.check("a@x")[0]
    clock.t += 91                                        # past lockout (and the window)
    assert rl.check("a@x")[0]                            # accepts again


def test_sliding_window_frees_up_after_60s(monkeypatch):
    clock = _patch_clock(monkeypatch)
    rl = RateLimiter(max_per_minute=2, lockout_threshold=99, lockout_duration_s=60)
    assert rl.check("a@x")[0] and rl.check("a@x")[0]
    assert not rl.check("a@x")[0]        # window full
    clock.t += 61                         # the two accepts age out
    assert rl.check("a@x")[0]


def test_per_jid_isolation(monkeypatch):
    _patch_clock(monkeypatch)
    rl = RateLimiter(max_per_minute=1, lockout_threshold=5, lockout_duration_s=60)
    assert rl.check("a@x")[0]
    assert not rl.check("a@x")[0]        # a is over its limit
    assert rl.check("b@x")[0]            # b is unaffected


# --------------------------------------------------------------------------- #
#  X2 reply ergonomics — progress ping config
# --------------------------------------------------------------------------- #

def test_progress_after_s_defaults_off(tmp_path):
    cfg = DaemonConfig.load(_write_cfg(tmp_path))
    assert cfg.progress_after_s == 0        # no unprompted messages by default


def test_progress_after_s_parses(tmp_path):
    p = tmp_path / "daemon.toml"
    p.write_text(
        '[daemon]\n'
        'jid = "daemon@desktop.tailnet"\n'
        'progress_after_s = 45\n'
        '[whitelist]\n'
        'allowed_jids = ["me@phone.tailnet"]\n'
    )
    cfg = DaemonConfig.load(p)
    assert cfg.progress_after_s == 45


# --------------------------------------------------------------------------- #
#  Admin tier ([trust] admin_jids) + grammar knob ([policy] grammar)
# --------------------------------------------------------------------------- #

def test_admin_jids_default_empty_means_every_allowlisted_jid(tmp_path):
    from xlii.daemon_gate import is_admin_jid

    cfg = DaemonConfig.load(_write_cfg(tmp_path))
    assert cfg.admin_jids == []
    assert is_admin_jid(cfg, "me@phone.tailnet/Conversations") is True


def test_admin_jids_restrict_by_bare_jid_case_insensitive(tmp_path):
    from xlii.daemon_gate import is_admin_jid

    cfg = DaemonConfig.load(_write_cfg(
        tmp_path, '[trust]\nadmin_jids = ["Me@Phone.tailnet"]\n'))
    assert is_admin_jid(cfg, "me@phone.tailnet/dev") is True
    assert is_admin_jid(cfg, "node2@home.example") is False
    assert is_admin_jid(cfg, "") is False


def test_grammar_defaults_to_slash_and_validates(tmp_path):
    assert DaemonConfig.load(_write_cfg(tmp_path)).grammar == "slash"
    cfg = DaemonConfig.load(_write_cfg(tmp_path, '[policy]\ngrammar = "bare"\n'))
    assert cfg.grammar == "bare"
    with pytest.raises(ValueError):
        DaemonConfig.load(_write_cfg(tmp_path, '[policy]\ngrammar = "yolo"\n'))


def test_agent_fallback_persona_loads(tmp_path):
    cfg = DaemonConfig.load(_write_cfg(tmp_path))
    assert cfg.fallback_persona == ""          # toml default; runtime still Mojo
    cfg = DaemonConfig.load(_write_cfg(
        tmp_path, '[agent_fallback]\npersona = "ixaac"\n'))
    assert cfg.fallback_persona == "ixaac"


def test_daemon_agent_persona_empty_is_still_mojo():
    from types import SimpleNamespace

    from xlii.daemon_gate import daemon_agent_persona

    cfg = SimpleNamespace(fallback_persona="")
    assert daemon_agent_persona(cfg) == "mojo"
    assert daemon_agent_persona(cfg, force_lab=True) == ""
    cfg.fallback_persona = "scout"
    assert daemon_agent_persona(cfg) == "scout"
    cfg.fallback_persona = "ixaac"  # leftover journal spelling → the seat
    assert daemon_agent_persona(cfg) == "mojo"
