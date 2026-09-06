"""TasksProvider provider."""
from __future__ import annotations


from xlii.addressing import (
    Address,
    Node,
    Resolution,
    ShellExport,
)


class TasksProvider:
    """``tasks://`` — the project's saved ``/tasks`` pipelines as a browseable list (the Panel-menu
    "Tasks" doorway's destination).

    ``tasks://`` lists every saved pipeline (``.xlii/tasks/*.toml``) by name; ``tasks://<name>`` reads
    that pipeline's rendered plan. Read-only through the VFS — pipelines are authored by the F9 Task
    Builder / ``/tasks new`` and run via ``/tasks``. Reaches the project's ``.xlii`` dir through the
    ambient session (:mod:`xlii.active_session`); outside a project it lists empty. Nodes carry
    ``type=task`` so the Panel opens the TasksPane (whose action seeds ``/tasks run <name>`` into the
    command line for review-before-run).
    """

    scheme = "tasks"

    def resolve(self, address: Address) -> Resolution:
        name = address.key.strip()
        if not name:
            return Resolution(ok=True, address=address, kind="tasks")  # the browseable root
        ok = self._has(name)
        return Resolution(ok=ok, address=address, kind="tasks",
                          reason="" if ok else f"no saved task {name!r}")

    def stat(self, address: Address) -> Node:
        name = address.key.strip()
        return Node(address=str(address), name=name or "tasks",
                    kind="container" if not name else "leaf", extra={"type": "task"})

    def list(self, address: Address) -> "list[Node]":
        if address.key.strip():
            return []
        xli_dir = self._xli_dir()
        if xli_dir is None:
            return []
        from xlii import tasks as T

        bound = ""
        try:
            from xlii.active_session import active_session
            from xlii.session_boot import bound_startup_task

            st = active_session()
            root = getattr(getattr(st, "project", None), "project_root", None)
            bound = bound_startup_task(root)
        except Exception:
            bound = ""
        nodes = []
        for name in T.list_pipelines(xli_dir):
            extra: dict = {"type": "task"}
            if bound and name == bound:
                extra["badge"] = "startup"
            nodes.append(
                Node(address=f"tasks://{name}", name=name, kind="leaf", extra=extra)
            )
        return nodes

    def read(self, address: Address) -> bytes:
        name = address.key.strip()
        if not name:
            raise IsADirectoryError("tasks://: a tasks root — use ls")
        xli_dir = self._xli_dir()
        if xli_dir is None:
            raise FileNotFoundError("tasks://: no active project")
        from xlii import tasks as T

        try:
            pipeline = T.load_pipeline(xli_dir, name)
        except T.TaskError as e:
            raise FileNotFoundError(f"tasks://{name}: {e}") from e
        header = f"# task: {pipeline.name}\n"
        if pipeline.description:
            header += f"\n{pipeline.description}\n"
        body = "\n".join(T.render_plan(pipeline))
        return (header + "\n" + body + "\n").encode()

    def exists(self, address: Address) -> bool:
        name = address.key.strip()
        return True if not name else self._has(name)

    def shell_export(self, address: Address) -> ShellExport:
        name = address.key.strip()
        if not name:
            return ShellExport(kind="address")  # the root spans project + stock — no one dir
        xli_dir = self._xli_dir()
        if xli_dir is None:
            raise FileNotFoundError("tasks://: no active project")
        from xlii import tasks as T

        # The durable .toml source is the artifact (read() renders a PLAN, a view);
        # project wins over stock, mirroring load_pipeline.
        p = T.pipeline_path(xli_dir, name)
        if p.is_file():
            return ShellExport(kind="path", path=p)
        stock = T.stock_tasks_dir() / f"{name}.toml"
        if stock.is_file():
            return ShellExport(kind="path", path=stock)
        raise FileNotFoundError(f"tasks://{name}: no saved task")

    def _has(self, name: str) -> bool:
        xli_dir = self._xli_dir()
        if xli_dir is None:
            return False
        from xlii import tasks as T

        return name in set(T.list_pipelines(xli_dir))

    @staticmethod
    def _xli_dir():
        from xlii.active_session import active_xli_dir

        return active_xli_dir()


