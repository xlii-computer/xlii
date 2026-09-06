"""Throne sweep — inventory + conservative deletes."""

from __future__ import annotations

from types import SimpleNamespace

from xlii.sweep import apply_sweep, gather, looks_testy, render_report


def test_looks_testy_catches_leftovers_not_real_names():
    assert looks_testy("xlii/proj")
    assert looks_testy("xlii/proj2")
    assert looks_testy("t")
    assert looks_testy("guardtest")
    assert looks_testy("faceproj")
    assert not looks_testy("xlii/iXaac-lab")
    assert not looks_testy("Website")
    assert not looks_testy("chat/ixaac")


def test_gather_flags_empty_untracked_and_claimed():
    cloud = {
        "c-lab": "xlii/iXaac-lab",
        "c-empty": "xlii/proj",
        "c-j": "xlii-journal/iXaac-lab",
    }
    entries = [SimpleNamespace(name="iXaac-lab", path="/home/lab", collection_id="c-lab")]
    report = gather(
        cloud=cloud,
        registry_entries=entries,
        empty_of=lambda cid: cid == "c-empty",
        claimed_of=lambda path: "c-lab" if path == "/home/lab" else None,
        ghost_check=lambda e: True,
    )
    by = {c.name: c for c in report.collections}
    assert by["xlii/iXaac-lab"].claimed_by == "iXaac-lab"
    assert by["xlii/iXaac-lab"].orphan is False
    assert by["xlii/proj"].empty and by["xlii/proj"].testy and by["xlii/proj"].orphan
    assert by["xlii-journal/iXaac-lab"].journal
    assert by["xlii-journal/iXaac-lab"].orphan is False
    assert report.sweepable_empty()[0].cid == "c-empty"
    assert report.sweepable_test()[0].cid == "c-empty"


def test_apply_sweep_only_deletes_untracked_empty(tmp_path):
    cloud = {"keep": "xlii/Website", "drop": "xlii/proj"}
    entries = [SimpleNamespace(name="Website", path=str(tmp_path), collection_id="keep")]
    report = gather(
        cloud=cloud,
        registry_entries=entries,
        empty_of=lambda cid: True,
        claimed_of=lambda path: "keep" if path == str(tmp_path) else None,
        ghost_check=lambda e: True,
    )
    deleted = []
    rc = apply_sweep(
        report, empty=True, yes=True,
        delete_collection=deleted.append,
        confirm=lambda _m: True,
    )
    assert rc == 0
    assert deleted == ["drop"]


def test_sweep_does_not_delete_registry_linked_ghost_collection(tmp_path):
    ghost_path = tmp_path / "gone"
    cloud = {"ghost": "xlii/proj"}
    entries = [SimpleNamespace(name="proj", path=str(ghost_path), collection_id="ghost")]
    report = gather(
        cloud=cloud,
        registry_entries=entries,
        empty_of=lambda cid: True,
        claimed_of=lambda path: None,
        ghost_check=lambda e: False,
    )

    row = report.collections[0]
    assert row.orphan and not row.untracked
    assert report.sweepable_empty() == []
    assert report.sweepable_test() == []

    deleted = []
    rc = apply_sweep(report, empty=True, test=True, yes=True, delete_collection=deleted.append)
    assert rc == 0
    assert deleted == []


def test_sweep_test_requires_confirmed_empty_untracked_collection():
    cloud = {"nonempty": "xlii/proj"}
    report = gather(
        cloud=cloud,
        registry_entries=[],
        empty_of=lambda cid: False,
        ghost_check=lambda e: True,
    )

    row = report.collections[0]
    assert row.untracked and row.testy and not row.empty
    assert report.sweepable_test() == []

    deleted = []
    rc = apply_sweep(report, test=True, yes=True, delete_collection=deleted.append)
    assert rc == 0
    assert deleted == []


def test_render_report_mentions_sweepable(capsys):
    class _C:
        def print(self, *a, **k):
            print(*a)

    cloud = {"c": "xlii/proj"}
    report = gather(
        cloud=cloud, registry_entries=[],
        empty_of=lambda cid: True,
        ghost_check=lambda e: True,
    )
    render_report(report, _C())
    out = capsys.readouterr().out
    assert "throne sweep" in out
    assert "xlii/proj" in out
    assert "empty" in out


def test_cli_registers_sweep():
    from xlii.cli import build_parser

    p = build_parser()
    ns = p.parse_args(["sweep", "--empty", "--yes"])
    assert ns.func.__name__ == "cmd_sweep"
    assert ns.empty and ns.yes


def test_cmd_sweep_keys_reuses_discovered_team_id(monkeypatch):
    import xlii.bootstrap as B
    import xlii.cmds.sweep as cmd_mod
    import xlii.sweep as S

    monkeypatch.setattr(S, "gather_live", lambda: SimpleNamespace(error=None))
    monkeypatch.setattr(S, "apply_sweep", lambda _report, **kw: (kw["prune_keys"](), 0)[1])

    import xlii.client as C
    monkeypatch.setattr(C.Clients, "from_config", lambda _cfg: object())
    import xlii.config as CFG
    monkeypatch.setattr(CFG.GlobalConfig, "load", lambda: object())

    seen = {"discover": 0, "plan_team": None, "exec_team": None}

    def _discover(_cfg):
        seen["discover"] += 1
        return "team-123"

    monkeypatch.setattr(B, "discover_team_id", _discover)
    monkeypatch.setattr(
        B, "plan_prune",
        lambda _cfg, team_id: (
            seen.__setitem__("plan_team", team_id),
            SimpleNamespace(candidates=[{"id": "k1", "disabled": True}]),
        )[1],
    )

    def _execute(_cfg, team_id, targets, on_event):
        seen["exec_team"] = team_id
        assert targets == [{"id": "k1", "disabled": True}]
        on_event("deleted")

    monkeypatch.setattr(B, "execute_prune", _execute)

    args = SimpleNamespace(empty=False, test=False, ghosts=False, keys=True, yes=True)
    assert cmd_mod.cmd_sweep(args) == 0
    assert seen["discover"] == 1
    assert seen["plan_team"] == "team-123"
    assert seen["exec_team"] == "team-123"
