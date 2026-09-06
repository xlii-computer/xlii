"""Tools → Gigwork / Remotes closed forms seed slash, they do not write."""

from __future__ import annotations

from types import SimpleNamespace


def test_gig_form_lists_presets_and_seeds_add(monkeypatch):
    from xlii.gig_form import form_spec

    monkeypatch.setattr(
        "xlii.addressing.builtins.gigwork.ambient_gig_cfg",
        lambda: SimpleNamespace(gigwork={}),
    )
    spec = form_spec()
    ids = {p["id"] for p in spec["presets"]}
    assert "kimi" in ids and "ollama" in ids
    html = spec["html"]
    assert "/gigwork add" in html
    assert "/jam add" in html
    assert "/gigwork add --custom" in html
    assert spec["kits"]
    kit_ids = {k["id"] for k in spec["kits"]}
    assert kit_ids == {"explore", "bash", "general"}
    assert "search / read" in html
    assert "+ member" in html
    assert "not a comma list" in html
    assert "backend[:kit][@model]" not in html


def test_remote_form_lists_protocols(monkeypatch):
    from xlii.remote_form import form_spec

    class _Mgr:
        def names(self):
            return []

        def open_names(self):
            return []

        def spec(self, name):
            return {}

    monkeypatch.setattr("xlii.remotefs.manager", _Mgr())
    spec = form_spec()
    assert "sftp" in spec["protocols"] or spec["protocols"]
    assert "/remote add" in spec["html"]
    assert "password" not in spec["html"].lower() or "never" in spec["lead"].lower()


def test_emit_pipeline_toml_round_trips_steps(tmp_path):
    from xlii import tasks as T

    spec = {
        "name": "nightly",
        "description": "do the thing",
        "class": "system",
        "steps": [
            {"kind": "shell", "id": "ls", "body": "ls -la"},
            {"kind": "slash", "id": "enter", "body": "/project switch demo"},
            {"kind": "agent", "id": "", "body": "summarize {{prev}}"},
        ],
        "params": [{"name": "kind", "required": False, "default": "code", "enum": [], "help": ""}],
        "edges": [],
    }
    text = T.emit_pipeline_toml(spec)
    assert 'run = "ls -la"' in text
    assert 'slash = "/project switch demo"' in text
    assert "ask =" in text
    path = T.write_pipeline_spec(tmp_path, spec)
    pipe = T.load_pipeline(tmp_path, "nightly")
    assert path.name == "nightly.toml"
    assert pipe.task_class == "system"
    assert [s.kind for s in pipe.steps] == [T.KIND_SHELL, T.KIND_SLASH, T.KIND_AGENT]


def test_task_form_loads_existing(tmp_path):
    from xlii import tasks as T
    from xlii.task_form import form_spec

    T.scaffold_pipeline(tmp_path, "bump-note")
    spec = form_spec(tmp_path, "bump-note")
    assert spec["name"] == "bump-note"
    assert spec["steps"]
    html = spec["html"]
    assert "+ bash" in html
    assert "Save .toml" in html
    assert "xlii-task-write" in html
    assert "bump-note" in html


def test_jobs_chrome_pills_include_finished_unseen():
    from xlii.jobs import JobRegistry, chrome_pills

    state = SimpleNamespace(job_registry=JobRegistry())
    jid = state.job_registry.adopt("task", "nightly")
    state.job_registry.finish(jid)
    pills = chrome_pills(state)
    assert any(p["id"] == jid and p["unseen"] and p["kind"] == "task" for p in pills)
    state.job_registry.mark_seen(jid)
    assert chrome_pills(state) == []
