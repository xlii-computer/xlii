"""FTP / FTPS handler — the stdlib :mod:`ftplib` wire (zero extra deps).

Bodies moved verbatim from the pre-seam ``RemoteFsConnection`` (the-fold's
remote-fs build): the shared/cPanel hosting case. ``ftps`` is the same wire with
a TLS control channel (certs verified unless the connection says ``insecure``)
plus ``PROT P`` for the data channel.
"""

from __future__ import annotations

import io
from typing import Optional


class FtpHandler:
    """One live ftplib session for a connection whose protocol is ftp/ftps."""

    def __init__(self, conn) -> None:
        self._c = conn
        self._ftp = None  # ftplib.FTP | FTP_TLS

    # ---- lifecycle ---- #

    @property
    def connected(self) -> bool:
        return self._ftp is not None

    def connect(self) -> None:
        import ftplib

        c = self._c
        if c.protocol == "ftps":
            import ssl

            # Verify certs by default; a per-connection `insecure: true` is the
            # escape hatch for the many cheap hosts with imperfect certs.
            ctx = ssl.create_default_context()
            if c.insecure:
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            ftp = ftplib.FTP_TLS(context=ctx, timeout=c._timeout)
        else:
            ftp = ftplib.FTP(timeout=c._timeout)
        ftp.connect(c.host, c.port)
        ftp.login(c.user or "anonymous", c._secret or "")
        if c.protocol == "ftps":
            ftp.prot_p()  # encrypt the data channel too, not just the control channel
        self._ftp = ftp

    def close(self) -> None:
        ftp, self._ftp = self._ftp, None
        if ftp is not None:
            try:
                ftp.close()
            except (OSError, EOFError):
                # The reference is already cleared; a dead control connection needs no close.
                pass

    # ---- primitives ---- #

    def listdir(self, path: str = "") -> "list[tuple[str, bool, Optional[int]]]":
        ftp = self._ftp
        try:
            return [
                (name, facts.get("type") in ("dir", "cdir", "pdir"),
                 int(facts["size"]) if facts.get("size") else None)
                for name, facts in ftp.mlsd(path or ".", facts=["type", "size"])
                if name not in (".", "..")
            ]
        except Exception:
            return self._listdir_fallback(path)

    def _listdir_fallback(self, path: str) -> "list[tuple[str, bool, Optional[int]]]":
        """NLST-based listing for servers without MLSD: a SIZE probe separates files
        (answer) from directories (550). A 550 on the NLST itself means the directory
        doesn't exist — surfaced as FileNotFoundError so callers see one OSError family
        regardless of protocol."""
        import ftplib

        from xlii.remotefs import _join

        ftp = self._ftp
        try:
            entries = ftp.nlst(path or ".")
        except ftplib.error_perm as e:
            raise FileNotFoundError(f"{self._c.name}:{path}: {e}") from e
        out: "list[tuple[str, bool, Optional[int]]]" = []
        for entry in entries:
            name = entry.rsplit("/", 1)[-1]
            if name in (".", ".."):
                continue
            try:
                ftp.voidcmd("TYPE I")  # SIZE is only reliable in binary mode
                size = ftp.size(_join(path, name))
                out.append((name, False, size))
            except ftplib.error_perm:
                out.append((name, True, None))
        return out

    def read(self, path: str) -> bytes:
        import ftplib

        buf = io.BytesIO()
        try:
            self._ftp.retrbinary(f"RETR {path}", buf.write)
        except ftplib.error_perm as e:
            raise FileNotFoundError(f"{self._c.name}:{path}: {e}") from e
        return buf.getvalue()

    def write(self, path: str, data: bytes) -> None:
        import ftplib

        try:
            self._ftp.storbinary(f"STOR {path}", io.BytesIO(data))
        except ftplib.error_perm as e:
            raise OSError(f"{self._c.name}:{path}: {e}") from e

    def delete(self, path: str) -> None:
        import ftplib

        try:
            self._ftp.delete(path)
        except ftplib.error_perm as e:
            raise FileNotFoundError(f"{self._c.name}:{path}: {e}") from e

    def rmdir(self, path: str) -> None:
        import ftplib

        try:
            self._ftp.rmd(path)
        except ftplib.error_perm as e:
            raise OSError(f"{self._c.name}:{path}: {e}") from e

    def mkdir(self, path: str) -> None:
        import ftplib

        try:
            self._ftp.mkd(path)
        except ftplib.error_perm as e:
            raise OSError(f"{self._c.name}:{path}: {e}") from e


HANDLER = FtpHandler
