from xlii.ignore import load_ignore_spec, walk_pruned, walk_project


def _tracked(root):
    spec = load_ignore_spec(root)
    return sorted(p.relative_to(root).as_posix() for p in walk_project(root, spec))


def test_secrets_never_sync(tmp_path):
    for name in (".env", ".env.local", "key.pem", "server.key", "id_rsa",
                 "id_ed25519", ".netrc", ".npmrc", "credentials.json"):
        (tmp_path / name).write_text("secret")
    (tmp_path / ".aws").mkdir()
    (tmp_path / ".aws" / "credentials").write_text("secret")
    (tmp_path / ".ssh").mkdir()
    (tmp_path / ".ssh" / "id_rsa").write_text("secret")
    (tmp_path / "ok.py").write_text("print(1)")
    assert _tracked(tmp_path) == ["ok.py"]


def test_tool_local_config_never_syncs(tmp_path):
    for d in (".claude", ".vscode", ".idea", ".direnv", ".xlii"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "settings.local.json").write_text("{}")
    (tmp_path / "ok.py").write_text("print(1)")
    assert _tracked(tmp_path) == ["ok.py"]


def test_xlii_plans_force_included_in_sync_and_browse(tmp_path):
    """``.xlii/plans/`` is carved out of DEFAULT_IGNORES for Collection sync."""
    internals = [
        ".xlii/session.json",
        ".xlii/project.json",
        ".xlii/turns/t1.json",
        ".xlii/scratch/out.txt",
        ".xlii/repl_history",
    ]
    for rel in internals:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("internal state")
    plan = tmp_path / ".xlii" / "plans" / "plan.md"
    plan.parent.mkdir(parents=True, exist_ok=True)
    plan.write_text("# plan\n")
    (tmp_path / "src.py").write_text("print(1)")

    spec = load_ignore_spec(tmp_path)
    synced = {p.relative_to(tmp_path).as_posix() for p in walk_project(tmp_path, spec)}
    assert synced == {".xlii/plans/plan.md", "src.py"}

    browsed = {p.relative_to(tmp_path).as_posix() for p in walk_pruned(tmp_path, spec)}
    assert browsed == {"src.py"}


def test_nested_gitignore_honored(tmp_path):
    sub = tmp_path / "pkg" / "sub"
    sub.mkdir(parents=True)
    (sub / ".gitignore").write_text("generated.txt\n/anchored.txt\nbuilt/\n")
    (sub / "generated.txt").write_text("x")
    (sub / "anchored.txt").write_text("x")
    (sub / "built").mkdir()
    (sub / "built" / "o.txt").write_text("x")
    deep = sub / "deep"
    deep.mkdir()
    (deep / "generated.txt").write_text("x")     # bare name applies at any depth
    (deep / "anchored.txt").write_text("keep")   # anchored applies only at sub/
    (sub / "keep.py").write_text("x")
    assert _tracked(tmp_path) == [
        "pkg/sub/.gitignore", "pkg/sub/deep/anchored.txt", "pkg/sub/keep.py",
    ]


def test_nested_gitignore_walk_prunes_ignored_dirs(tmp_path):
    """A .gitignore inside an ignored dir (node_modules) must not be read,
    and the discovery walk must not descend there at all."""
    nm = tmp_path / "node_modules" / "dep"
    nm.mkdir(parents=True)
    (nm / ".gitignore").write_text("keepme.txt\n")
    spec = load_ignore_spec(tmp_path)
    assert not spec.match_file("src/keepme.txt")


def test_nested_gitignore_walk_is_bounded(tmp_path, capsys, monkeypatch):
    """Spec construction must never hang — the init bulk-upload guard relies
    on it. With a tiny budget, the walk stops early and says so."""
    from xlii import ignore as ignore_mod
    for i in range(30):
        (tmp_path / f"d{i}").mkdir()
    monkeypatch.setattr(ignore_mod, "_NESTED_GITIGNORE_VISIT_BUDGET", 10)
    spec = load_ignore_spec(tmp_path)  # must return, not hang
    assert spec.match_file(".env")  # defaults still intact
    assert "stopped scanning" in capsys.readouterr().err


def test_xliiignore_used_and_legacy_name_auto_migrated(tmp_path):
    (tmp_path / ".xliiignore").write_text("a.txt\n")
    (tmp_path / "a.txt").write_text("x")
    (tmp_path / "b.txt").write_text("x")
    assert _tracked(tmp_path) == ["b.txt"]

    # A legacy '.xliignore' (pre-rename misspelling) is auto-renamed to the
    # canonical '.xliiignore' on load and then honored — no permanent fallback.
    (tmp_path / ".xliiignore").unlink()
    (tmp_path / ".xliignore").write_text("b.txt\n")
    assert _tracked(tmp_path) == ["a.txt"]
    assert (tmp_path / ".xliiignore").exists()       # renamed to canonical
    assert not (tmp_path / ".xliignore").exists()    # legacy name gone
