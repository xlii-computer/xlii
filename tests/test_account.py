"""Tests for `xlii account` — the read-only xAI account hub."""

from __future__ import annotations

from types import SimpleNamespace

import xlii.cmds.account as acct
from xlii.xai_mgmt import money_usd


def test_money_usd_parses_cents():
    assert money_usd({"val": "300000"}) == 3000.0
    assert money_usd("10000") == 100.0
    assert money_usd(0) == 0.0
    assert money_usd(None) is None
    assert money_usd({"nope": 1}) is None


def test_prepaid_total_reads_documented_total_field():
    from xlii import xai_mgmt as mgmt

    assert mgmt.pick_usd({"total": {"val": "12300"}}, "total", "balance") == 123.0
    assert mgmt.pick_usd({"balance": {"val": "500"}}, "total", "balance") == 5.0


def _patch_cfg(monkeypatch, key="xai-mgmt-key"):
    monkeypatch.setattr(
        "xlii.cmds.account.GlobalConfig",
        SimpleNamespace(load=lambda: SimpleNamespace(management_api_key=key, team_id=None)),
    )


def test_account_status_renders_real_dollars(monkeypatch, capsys):
    _patch_cfg(monkeypatch)
    monkeypatch.setattr(acct.mgmt, "resolve_active_team", lambda k, p=None: "team-1")
    monkeypatch.setattr(acct.mgmt, "team_status", lambda k, t: {
        "name": "Personal team", "tierId": "100", "mfaRequired": False,
        "isSelfServeZdrEligible": True, "allowApiKeysAllModels": True, "blockedReasons": [],
    })
    monkeypatch.setattr(acct.mgmt, "spending_limits", lambda k, t: {
        "effectiveHardSl": {"val": "300000"}, "softSl": {"val": "10000"},
    })
    # status sums real usage (dollars, already) for "used vs cap"
    monkeypatch.setattr(acct.mgmt, "usage_by_description", lambda k, t, **kw: [
        ("API grok-4.3", 50.0), ("API grok-build-0.1", 25.64)])
    monkeypatch.setattr(acct.mgmt, "list_keys", lambda k, t: [{}, {}, {}])
    monkeypatch.setattr(acct.mgmt, "prepaid_total_usd", lambda k, t: 0.0)
    monkeypatch.setattr(acct.mgmt, "invoice_preview_usd", lambda k, t: None)

    rc = acct.cmd_account(SimpleNamespace(what="status"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "Personal team" in out
    assert "$3,000.00" in out  # hard limit — money_usd (cents) applied exactly once
    assert "$75.64" in out     # used = summed usage (dollars, no division)
    assert "remaining" in out
    assert "$2,924.36" in out  # hard - used
    assert "api keys: 3" in out


def test_account_status_handles_zero_hard_limit(monkeypatch, capsys):
    _patch_cfg(monkeypatch)
    monkeypatch.setattr(acct.mgmt, "resolve_active_team", lambda k, p=None: "team-1")
    monkeypatch.setattr(acct.mgmt, "team_status", lambda k, t: {
        "name": "Trial", "tierId": "0", "mfaRequired": False,
        "isSelfServeZdrEligible": False, "allowApiKeysAllModels": False,
        "blockedReasons": [],
    })
    monkeypatch.setattr(acct.mgmt, "spending_limits", lambda k, t: {
        "effectiveHardSl": {"val": "0"}, "softSl": None,
    })
    monkeypatch.setattr(acct.mgmt, "usage_by_description", lambda k, t, **kw: [("API", 0.0)])
    monkeypatch.setattr(acct.mgmt, "list_keys", lambda k, t: [])
    monkeypatch.setattr(acct.mgmt, "prepaid_total_usd", lambda k, t: None)
    monkeypatch.setattr(acct.mgmt, "invoice_preview_usd", lambda k, t: None)

    rc = acct.cmd_account(SimpleNamespace(what="status"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "remaining" in out
    assert "$0.00 of $0.00" in out


def test_account_usage_table(monkeypatch, capsys):
    _patch_cfg(monkeypatch)
    monkeypatch.setattr(acct.mgmt, "resolve_active_team", lambda k, p=None: "team-1")
    monkeypatch.setattr(acct.mgmt, "usage_by_description", lambda k, t, **kw: [
        ("API grok-4.3", 57.73), ("API grok-build-0.1", 15.88)])
    rc = acct.cmd_account(SimpleNamespace(what="usage", days=0))
    out = capsys.readouterr().out
    assert rc == 0
    assert "grok-4.3" in out
    assert "$57.73" in out
    assert "$73.61" in out  # total


def test_account_keys_table(monkeypatch, capsys):
    _patch_cfg(monkeypatch)
    monkeypatch.setattr(acct.mgmt, "resolve_active_team", lambda k, p=None: "team-1")
    monkeypatch.setattr(acct.mgmt, "list_keys", lambda k, t: [
        {"name": "primary", "qps": 30, "qpm": 2000, "aclStrings": ["a", "b"], "disabled": False},
        {"name": "retired", "disabled": True, "aclStrings": []},
    ])
    rc = acct.cmd_account(SimpleNamespace(what="keys"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "primary" in out
    assert "disabled" in out


def test_account_billing_tables(monkeypatch, capsys):
    _patch_cfg(monkeypatch)
    monkeypatch.setattr(acct.mgmt, "resolve_active_team", lambda k, p=None: "team-1")
    monkeypatch.setattr(acct.mgmt, "list_invoices", lambda k, t: [
        {"invoice_number": "AAAA-BBBB", "createTime": "2026-06-14T00:00:00Z",
         "invoiceStatus": "PAID", "total": {"val": "10000"}},
    ])
    monkeypatch.setattr(acct.mgmt, "transactions", lambda k, t: [
        {"changeOrigin": "PURCHASE", "createTime": "2026-06-14T00:00:00Z",
         "amount": {"val": "-10000"}, "topupStatus": "SUCCEEDED"},
        {"changeOrigin": "SPEND", "amount": {"val": "500"}},  # money-out, filtered
    ])
    rc = acct.cmd_account(SimpleNamespace(what="billing"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "AAAA-BBBB" in out and "PAID" in out
    assert "$100.00" in out      # invoice total; top-up flips -100 → +100 too
    assert "PURCHASE" in out
    assert "SPEND" not in out     # raw spend rows are filtered out of money-in


def test_account_without_key_explains(monkeypatch, capsys):
    monkeypatch.setattr(
        "xlii.cmds.account.GlobalConfig",
        SimpleNamespace(load=lambda: SimpleNamespace(management_api_key=None, team_id=None)),
    )
    rc = acct.cmd_account(SimpleNamespace(what="status"))
    assert rc == 1
    assert "XAI_MANAGEMENT_API_KEY not set" in capsys.readouterr().out


def test_account_repl_command(monkeypatch):
    """`/account` renders into the REPL console via the shared render_account."""
    from tests.helpers import FakeConsole
    from xlii.commands import dispatch_repl_command
    from xlii.repl_cmds import register_all

    register_all()
    _patch_cfg(monkeypatch)
    monkeypatch.setattr(acct.mgmt, "resolve_active_team", lambda k, p=None: "team-1")
    monkeypatch.setattr(acct.mgmt, "team_status", lambda k, t: {"name": "Personal team", "tierId": "100"})
    monkeypatch.setattr(acct.mgmt, "spending_limits", lambda k, t: {"effectiveHardSl": {"val": "300000"}})
    monkeypatch.setattr(acct.mgmt, "usage_by_description", lambda k, t, **kw: [("API grok-4.3", 10.0)])
    monkeypatch.setattr(acct.mgmt, "list_keys", lambda k, t: [{}])
    monkeypatch.setattr(acct.mgmt, "prepaid_total_usd", lambda k, t: None)
    monkeypatch.setattr(acct.mgmt, "invoice_preview_usd", lambda k, t: None)

    ctx = {"console": FakeConsole(), "state": SimpleNamespace(), "command_scope": "code"}
    assert dispatch_repl_command("/account status", ctx) is True
    assert "Personal team" in ctx["console"].text


def test_budget_note_summary(monkeypatch):
    monkeypatch.setattr(acct.mgmt, "spending_limits", lambda k, t: {"effectiveHardSl": {"val": "300000"}})
    monkeypatch.setattr(acct.mgmt, "usage_by_description", lambda k, t, **kw: [("API grok-4.3", 75.64)])
    note = acct.budget_note("key", "team")
    assert "$75.64" in note and "$3,000.00" in note and "3%" in note


def test_account_budget_toggles_session_note(monkeypatch):
    from tests.helpers import FakeConsole
    from xlii.commands import dispatch_repl_command
    from xlii.repl_cmds import register_all

    register_all()
    monkeypatch.setattr(
        "xlii.config.GlobalConfig",
        SimpleNamespace(load=lambda: SimpleNamespace(management_api_key="k", team_id=None)),
    )
    monkeypatch.setattr(acct.mgmt, "resolve_active_team", lambda k, p=None: "team-1")
    monkeypatch.setattr(acct.mgmt, "spending_limits", lambda k, t: {"effectiveHardSl": {"val": "300000"}})
    monkeypatch.setattr(acct.mgmt, "usage_by_description", lambda k, t, **kw: [("x", 30.0)])

    session = SimpleNamespace(budget_note=None)
    state = SimpleNamespace(agent=SimpleNamespace(session=session))
    ctx = {"console": FakeConsole(), "state": state, "command_scope": "code"}

    assert dispatch_repl_command("/account budget", ctx) is True
    assert session.budget_note is not None and "$30.00" in session.budget_note
    assert dispatch_repl_command("/account budget off", ctx) is True
    assert session.budget_note is None


def test_budget_note_injected_into_system_prompt():
    from tests.test_rail import _bare_agent

    agent = _bare_agent()
    agent.base_system_prompt = "BASE PROMPT"
    agent.session.budget_note = "xAI spend month-to-date: $30.00 of $3,000.00 cap (1%)."
    prompt = agent._effective_system_prompt()
    assert "[BUDGET]" in prompt and "$30.00 of $3,000.00" in prompt

    agent.session.budget_note = None
    assert "[BUDGET]" not in agent._effective_system_prompt()


def test_account_snapshot_compact(monkeypatch):
    _patch_cfg(monkeypatch)
    monkeypatch.setattr(acct.mgmt, "resolve_active_team", lambda k, p=None: "team-1")
    monkeypatch.setattr(acct.mgmt, "team_status", lambda k, t: {
        "name": "Personal team", "tierId": "100", "allowApiKeysAllModels": True,
    })
    monkeypatch.setattr(acct.mgmt, "spending_limits", lambda k, t: {
        "effectiveHardSl": {"val": "300000"},
    })
    monkeypatch.setattr(acct.mgmt, "usage_by_description", lambda k, t, **kw: [
        ("API grok-4.3", 75.64)])
    monkeypatch.setattr(acct.mgmt, "list_keys", lambda k, t: [{}, {"disabled": True}])
    monkeypatch.setattr(acct.mgmt, "prepaid_total_usd", lambda k, t: 500.0)
    monkeypatch.setattr(acct.mgmt, "invoice_preview_usd", lambda k, t: None)
    snap = acct.account_snapshot()
    assert snap["team"] == "Personal team"
    assert "$2,924.36" in snap["spend"] and "left of" in snap["spend"]
    assert "1 active / 2" in snap["keys"]
    assert snap["prepaid"] == "$500.00"
    assert snap["remaining"] == "$2,924.36"


def test_accounts_alias_is_account():
    from xlii.commands import find_repl_command
    from xlii.repl_cmds import register_all

    register_all()
    assert find_repl_command("/accounts", "code").name == "account"


def test_account_error_is_caught(monkeypatch, capsys):
    _patch_cfg(monkeypatch)

    def boom(k, p=None):
        raise acct.mgmt.AccountError("GET /auth/teams → 403")

    monkeypatch.setattr(acct.mgmt, "resolve_active_team", boom)
    rc = acct.cmd_account(SimpleNamespace(what="status"))
    assert rc == 1
    assert "xAI account error" in capsys.readouterr().out
