"""Exec one VFS op on a fabric node (the via:// hop script)."""

from __future__ import annotations

import json
import shlex

_HOP_TIMEOUT = 30.0

_HOP_SCRIPT = (
    "import base64,json,sys\n"
    "from xlii.addressing import ("
    "vfs_delete,vfs_exists,vfs_list,vfs_mkdir,vfs_read,vfs_stat,vfs_write)\n"
    "req=json.load(sys.stdin)\n"
    "op=req.get('op'); addr=req.get('address') or ''\n"
    "try:\n"
    "    if op=='list':\n"
    "        nodes=vfs_list(addr)\n"
    "        json.dump({'ok':True,'nodes':[{'address':n.address,'name':n.name,"
    "'kind':n.kind,'size':n.size} for n in nodes]},sys.stdout)\n"
    "    elif op=='read':\n"
    "        data=vfs_read(addr)\n"
    "        json.dump({'ok':True,'b64':base64.b64encode(data).decode('ascii')},"
    "sys.stdout)\n"
    "    elif op=='write':\n"
    "        vfs_write(addr, base64.b64decode(req.get('b64') or ''))\n"
    "        json.dump({'ok':True},sys.stdout)\n"
    "    elif op=='mkdir':\n"
    "        vfs_mkdir(addr)\n"
    "        json.dump({'ok':True},sys.stdout)\n"
    "    elif op=='delete':\n"
    "        vfs_delete(addr, recursive=bool(req.get('recursive')))\n"
    "        json.dump({'ok':True},sys.stdout)\n"
    "    elif op=='stat':\n"
    "        n=vfs_stat(addr)\n"
    "        json.dump({'ok':True,'node':{'address':n.address,'name':n.name,"
    "'kind':n.kind,'size':n.size}},sys.stdout)\n"
    "    elif op=='exists':\n"
    "        json.dump({'ok':True,'exists':bool(vfs_exists(addr))},sys.stdout)\n"
    "    else:\n"
    "        json.dump({'ok':False,'error':'unknown op %r'%op},sys.stdout); "
    "sys.exit(2)\n"
    "except Exception as e:\n"
    "    json.dump({'ok':False,'error':'%s: %s'%(type(e).__name__,e)},"
    "sys.stdout); sys.exit(1)\n"
)

_FIND_PY = (
    "for p in \"$HOME/xlii-venv/bin/python\" \"$HOME/xlii-venv/bin/python3\" "
    "\"$HOME/iXaac-lab/venv/bin/python\" \"$HOME/iXaac-lab/venv/bin/python3\" "
    "\"/home/admin/xlii-venv/bin/python\"; do "
    "if [ -x \"$p\" ] && \"$p\" -c 'import xlii' >/dev/null 2>&1; then "
    "echo \"$p\"; exit 0; fi; done; "
    "python3 -c 'import xlii,sys; print(sys.executable)' 2>/dev/null && exit 0; "
    "exit 1"
)

_python_cache: dict[str, str] = {}


def hop_op(node: str, op: str, inner: str, extra: dict | None = None) -> dict:
    """Run one VFS op on *node*. Overridable in tests via ``via.hop_op``."""
    from xlii.remotefs import manager

    conn = manager.get(node)
    py = _python_on(conn)
    payload = {"op": op, "address": inner}
    if extra:
        payload.update(extra)
    raw = conn.run(
        f"{shlex.quote(py)} -c {shlex.quote(_HOP_SCRIPT)}",
        stdin=json.dumps(payload).encode("utf-8"),
        timeout=_HOP_TIMEOUT,
    )
    try:
        data = json.loads(raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw)
    except json.JSONDecodeError as e:
        snippet = (raw or b"")[:240]
        raise OSError(f"via://{node}: hop returned non-JSON ({e}): {snippet!r}") from e
    if not isinstance(data, dict) or not data.get("ok"):
        err = (data or {}).get("error") if isinstance(data, dict) else raw
        raise OSError(f"via://{node}: {err}")
    return data


def _python_on(conn) -> str:
    name = getattr(conn, "name", "") or ""
    cached = _python_cache.get(name)
    if cached:
        return cached
    raw = conn.run(f"bash -c {shlex.quote(_FIND_PY)}", timeout=_HOP_TIMEOUT)
    py = (raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw)).strip().splitlines()
    exe = (py[-1] if py else "").strip()
    if not exe:
        raise OSError(
            f"via://{name}: no python with xlii on that node "
            "(need ~/xlii-venv or similar)"
        )
    _python_cache[name] = exe
    return exe
