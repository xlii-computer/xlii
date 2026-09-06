"""SFTP handler — the ``paramiko`` wire (optional extra: ``pip install 'xlii[remote]'``).

Bodies moved verbatim from the pre-seam ``RemoteFsConnection``. ``paramiko`` is
imported lazily inside :meth:`SftpHandler.connect` so the stdlib FTP path never
pays for it and a missing package yields a clean install hint (RuntimeError),
not an import crash. Host-key policy is TOFU/auto-add for v1 (known-hosts
pinning is the standing deferred item from proposals/remote-fs.md).
"""

from __future__ import annotations

import os
from typing import Optional

_INSTALL_HINT = (
    "SFTP support needs the optional 'paramiko' package — "
    "install it with: pip install 'xlii[remote]'"
)


class SftpHandler:
    """One live paramiko SSH transport + SFTP client for an sftp connection."""

    def __init__(self, conn) -> None:
        self._c = conn
        self._ssh = None   # paramiko.SSHClient (owns the transport)
        self._sftp = None  # paramiko.SFTPClient

    # ---- lifecycle ---- #

    @property
    def connected(self) -> bool:
        return self._sftp is not None

    def connect(self) -> None:
        try:
            import paramiko
        except ImportError as e:
            raise RuntimeError(_INSTALL_HINT) from e

        c = self._c
        client = paramiko.SSHClient()
        client.load_system_host_keys()
        # TOFU host-key policy for v1 (per the proposal lean) — unknown hosts are
        # accepted on first connect and remembered by paramiko for the session.
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        # The stored key_path keeps the portable literal (~/… or $VAR) so a config
        # travels between machines/users; paramiko opens the file itself and does
        # NOT expand either, so it must be resolved here or the open dies with
        # "No such file or directory: '~/…'".
        key_file = (
            os.path.expanduser(os.path.expandvars(str(c.key_path)))
            if c.key_path else None
        )
        client.connect(
            c.host,
            port=c.port,
            username=c.user or None,
            password=c._secret or None,
            key_filename=key_file,
            passphrase=c._secret if c.key_path else None,
            timeout=c._timeout,
        )
        self._ssh = client
        self._sftp = client.open_sftp()
        self._sftp.get_channel().settimeout(c._timeout)

    def close(self) -> None:
        sftp, self._sftp = self._sftp, None
        ssh, self._ssh = self._ssh, None
        for obj in (sftp, ssh):
            if obj is None:
                continue
            try:
                obj.close()
            except (OSError, EOFError):
                # A dead channel or transport needs no close -- keep tearing the rest down.
                pass

    # ---- primitives ---- #

    def run(self, command: str, stdin: bytes = b"", timeout: Optional[float] = None) -> bytes:
        """SSH exec on this SFTP session (the via:// hop). Not a shell pipeline.

        OpenSSH runs the payload with the user's shell ``-c``. Callers must
        ``shlex.quote`` any interpolated argument. *stdin* is the exec
        channel's stdin (JSON for the hop).
        """
        if self._ssh is None:
            raise OSError("sftp not connected")
        limit = float(timeout if timeout is not None else self._c._timeout)
        _in, stdout, stderr = self._ssh.exec_command(command, timeout=limit)
        chan = stdout.channel
        if stdin:
            payload = bytes(stdin) if isinstance(stdin, (bytes, bytearray)) else str(stdin).encode("utf-8")
            chan.sendall(payload)
            chan.shutdown_write()
        out = stdout.read()
        err = stderr.read()
        code = chan.recv_exit_status()
        if code != 0:
            msg = (err or out or b"").decode("utf-8", "replace").strip()
            raise OSError(f"remote exec failed ({code}): {msg[:400]}")
        return out

    def listdir(self, path: str = "") -> "list[tuple[str, bool, Optional[int]]]":
        import stat as _stat

        return [
            (a.filename, _stat.S_ISDIR(a.st_mode or 0), a.st_size)
            for a in self._sftp.listdir_attr(path or ".")
        ]

    def read(self, path: str) -> bytes:
        with self._sftp.open(path, "rb") as f:
            return f.read()

    def write(self, path: str, data: bytes) -> None:
        with self._sftp.open(path, "wb") as f:
            f.write(data)

    def delete(self, path: str) -> None:
        self._sftp.remove(path)

    def rmdir(self, path: str) -> None:
        self._sftp.rmdir(path)

    def mkdir(self, path: str) -> None:
        self._sftp.mkdir(path)


HANDLER = SftpHandler
