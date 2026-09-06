"""Encrypted credential vault for plugin auth.

Per the design proposal: plugins authenticate via env vars, and those env
vars live in a single Fernet-encrypted JSON at ``~/.config/xlii/vault.enc``
keyed by plugin id. Plaintext only exists in process memory during a call.

Master-key resolution order (first source that *decrypts* ``vault.enc`` wins):

1. ``XLII_VAULT_KEY`` env var (alias: ``XLI_VAULT_KEY``) — raw 44-char Fernet
   key. Headless / CI path. See ``docs/LEGACY.md`` for the alias.
2. OS keyring (Secret Service / Keychain / Credential Locker) via the
   ``keyring`` library — service ``xli``, username ``vault-master``.
   **Service name stays ``xli`` forever** (changing it strands existing vaults).
   The default for desktop sessions; zero user-managed key files.
3. ``~/.config/xlii/.vault-key`` (chmod 0o400) — fallback for systems where
   keyring isn't available (broken Secret Service, sandboxed shells, etc.)
   **or** when an earlier backend holds a key that does not match this vault.

If none of those produces a key when one is needed *and no vault file exists*,
``Vault.unlock()`` provisions a fresh Fernet key into the best available
backend (keyring preferred, falling back to the on-disk file). An existing
``vault.enc`` is never re-keyed that way — minting a new master would strand
the ciphertext.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from typing import Optional

from xlii.config import GLOBAL_CONFIG_DIR

VAULT_FILE = GLOBAL_CONFIG_DIR / "vault.enc"
KEY_FILE = GLOBAL_CONFIG_DIR / ".vault-key"

# Stable identifiers for the OS keyring entry. Keep these stable forever —
# changing them strands existing vaults until users migrate manually.
# Product rename xli→xlii deliberately did NOT change KEYRING_SERVICE.
KEYRING_SERVICE = "xli"
KEYRING_USERNAME = "vault-master"

# Env-var fallback. Canonical name is XLII_*; XLI_* remains a permanent alias
# (docs/LEGACY.md) so headless/CI scripts do not break.
ENV_VAR = "XLII_VAULT_KEY"
ENV_VAR_LEGACY = "XLI_VAULT_KEY"

BACKEND_ENV = "env"
BACKEND_KEYRING = "keyring"
BACKEND_FILE = "file"

# Secret Service / D-Bus can hang indefinitely (NoReply). Bound so Face boot
# does not sit on "sidecar starting". Importing `keyring` itself talks to the
# daemon, so the whole probe (import + lookup) runs on a daemon thread.
KEYRING_WAIT_S = 2.0
# Once a probe times out in this process, later unlocks skip the keyring
# (Face would otherwise wait again on panic-mail then key_pairs). A latch
# rather than a rebound module global: the probe runs off-thread, so the
# flag has to be safe to read and set from either side.
_KEYRING_DEAD = threading.Event()
_SAVE_LOCK = threading.Lock()


class VaultError(Exception):
    """Anything that prevents read/write of the vault."""


# --------------------------------------------------------------------------- #
#  Master-key storage backends
# --------------------------------------------------------------------------- #

def _read_env_key() -> Optional[bytes]:
    raw = os.environ.get(ENV_VAR) or os.environ.get(ENV_VAR_LEGACY)
    return raw.encode() if raw else None


def _read_keyring_key() -> Optional[bytes]:
    """Try the OS keyring. Returns None on any failure (no backend available,
    backend rejects access, key missing, hang, etc.) — caller falls through.

    Do not join the worker. ``ThreadPoolExecutor.shutdown(wait=True)`` (and a
    main-thread ``import keyring``) is what made an earlier 2s timeout useless:
    Secret Service blocked the importer, then the executor waited on the hung
    lookup, and Face never printed a handshake.
    """
    if _KEYRING_DEAD.is_set():
        return None

    box: dict[str, Optional[bytes]] = {"k": None}
    done = threading.Event()

    def _probe() -> None:
        try:
            import keyring

            v = keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME)
            box["k"] = v.encode() if v else None
        except Exception:
            # Missing lib, KeyringError, D-Bus NoReply raised as a non-Keyring
            # type — all of these are "no keyring this boot."
            box["k"] = None
        finally:
            done.set()

    threading.Thread(target=_probe, name="xlii-keyring", daemon=True).start()
    if not done.wait(KEYRING_WAIT_S):
        _KEYRING_DEAD.set()
        return None
    return box["k"]


def _write_keyring_key(key: bytes) -> bool:
    """Persist ``key`` into the OS keyring. Returns True on success."""
    try:
        import keyring
    except ImportError:
        return False
    try:
        keyring.set_password(KEYRING_SERVICE, KEYRING_USERNAME, key.decode())
    except Exception:
        # keyring.set_password can raise various backend errors
        return False
    return True


def _read_file_key() -> Optional[bytes]:
    if not KEY_FILE.exists():
        return None
    try:
        return KEY_FILE.read_bytes().strip()
    except OSError:
        return None


def _write_file_key(key: bytes) -> None:
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    # Write atomically with own-only perms regardless of umask.
    fd = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o400)
    try:
        os.fchmod(fd, 0o400)
    except OSError:
        # The fd was opened 0o400 and umask can only clear bits, so a filesystem that refuses fchmod still
        # leaves own-only perms.
        pass
    with os.fdopen(fd, "wb") as f:
        f.write(key)


def _candidate_keys() -> list[tuple[bytes, str]]:
    """Every present master key, env → keyring → file. Duplicates skipped.

    Unlock tries each until ``vault.enc`` decrypts. A stale OS-keyring
    entry must not strand a still-valid ``.vault-key`` (or ``$XLII_VAULT_KEY``).
    """
    out: list[tuple[bytes, str]] = []
    seen: set[bytes] = set()
    for reader, backend in (
        (_read_env_key, BACKEND_ENV),
        (_read_keyring_key, BACKEND_KEYRING),
        (_read_file_key, BACKEND_FILE),
    ):
        k = reader()
        if k is None or k in seen:
            continue
        seen.add(k)
        out.append((k, backend))
    return out


def _resolve_key() -> tuple[Optional[bytes], Optional[str]]:
    """Return ``(key, backend)`` from the first source that has one, else
    ``(None, None)``. Backend is one of BACKEND_ENV/KEYRING/FILE."""
    candidates = _candidate_keys()
    if not candidates:
        return (None, None)
    return candidates[0]


def master_key_backend() -> tuple[Optional[bytes], Optional[str]]:
    """Public, non-creating probe of the master-key chain.

    Same resolution as :func:`_resolve_key` (env → keyring → key file) and the
    same ``(key, backend)`` / ``(None, None)`` return — but stable API, so
    diagnostics (``xlii doctor``) and other bodies can report vault state
    without reaching into the private resolver. Never provisions a key."""
    return _resolve_key()


def _provision_key() -> tuple[bytes, str]:
    """Generate a fresh master key, store it via the best available backend,
    and return ``(key, backend_used)``. Tries keyring first, then file."""
    from cryptography.fernet import Fernet
    key = Fernet.generate_key()
    if _write_keyring_key(key):
        return (key, BACKEND_KEYRING)
    _write_file_key(key)
    return (key, BACKEND_FILE)


# --------------------------------------------------------------------------- #
#  Vault
# --------------------------------------------------------------------------- #

@dataclass
class Vault:
    """Decrypted in-memory view of the vault. Mutations re-encrypt on save.

    Use ``Vault.unlock()`` to load. Use ``Vault.set(...)``/``unset(...)`` to
    mutate; both call ``save()`` internally so callers don't accidentally
    leave the on-disk vault stale.
    """
    _key: bytes
    _data: dict[str, dict[str, str]]
    backend: str  # which master-key backend produced the key

    # ---- factory ---- #

    @classmethod
    def unlock(cls, *, create_if_missing: bool = True) -> "Vault":
        """Decrypt the vault file and return a Vault instance.

        If no master key exists and there is no ``vault.enc`` yet,
        ``create_if_missing=True`` (the default) provisions one via the best
        available backend. An existing vault is never re-keyed. Set False to
        require an already-present master.
        """
        from cryptography.fernet import Fernet, InvalidToken

        candidates = _candidate_keys()
        if not candidates:
            if VAULT_FILE.exists():
                # Never mint a new master while vault.enc is on disk — that
                # writes a useless key into the OS keyring and the next boot
                # prefers it over a still-valid .vault-key.
                raise VaultError(
                    "vault is sealed — no master key. Unlock the OS keyring "
                    f"(service {KEYRING_SERVICE!r}) or export ${ENV_VAR} "
                    f"for this vault. Do not run `xlii auth set` "
                    f"(a new key cannot read {VAULT_FILE})."
                )
            if not create_if_missing:
                raise VaultError(
                    "no master key found — set $XLII_VAULT_KEY "
                    f"(or legacy ${ENV_VAR_LEGACY}), or run any "
                    "`xlii auth set` to provision one automatically."
                )
            key, backend = _provision_key()
            return cls(_key=key, _data={}, backend=backend)

        if not VAULT_FILE.exists():
            key, backend = candidates[0]
            return cls(_key=key, _data={}, backend=backend)

        try:
            token = VAULT_FILE.read_bytes()
        except OSError as e:
            raise VaultError(f"cannot read vault file: {e}") from e

        for key, backend in candidates:
            try:
                plaintext = Fernet(key).decrypt(token)
            except (InvalidToken, ValueError):
                continue
            try:
                data = json.loads(plaintext.decode())
            except json.JSONDecodeError as e:
                raise VaultError(f"vault contents corrupted: {e}") from e
            if not isinstance(data, dict):
                raise VaultError("vault contents corrupted (root is not a dict)")
            return cls(_key=key, _data=data, backend=backend)

        tried = ", ".join(b for _, b in candidates)
        raise VaultError(
            f"vault decrypt failed — master key (tried {tried}) "
            f"doesn't match {VAULT_FILE}. Check ${ENV_VAR} "
            f"(or ${ENV_VAR_LEGACY}), the OS keyring entry "
            f"(service {KEYRING_SERVICE!r}), or {KEY_FILE}."
        )

    # ---- accessors ---- #

    def get(self, plugin_id: str) -> dict[str, str]:
        """Return the env-var dict for ``plugin_id``, or empty dict."""
        return dict(self._data.get(plugin_id, {}))

    def has(self, plugin_id: str, env_var: str) -> bool:
        return env_var in self._data.get(plugin_id, {})

    def list_plugins(self) -> list[str]:
        return sorted(self._data.keys())

    def list_keys(self, plugin_id: str) -> list[str]:
        return sorted(self._data.get(plugin_id, {}).keys())

    # ---- mutators ---- #

    def set(self, plugin_id: str, env_var: str, value: str) -> None:
        slot = self._data.setdefault(plugin_id, {})
        slot[env_var] = value
        self.save()

    def unset(self, plugin_id: str, env_var: Optional[str] = None) -> bool:
        """Remove one env var (or the whole plugin if ``env_var`` is None).
        Returns True if anything was removed."""
        if plugin_id not in self._data:
            return False
        if env_var is None:
            del self._data[plugin_id]
            self.save()
            return True
        if env_var not in self._data[plugin_id]:
            return False
        del self._data[plugin_id][env_var]
        if not self._data[plugin_id]:
            # Drop the empty plugin entry so list_plugins() stays clean.
            del self._data[plugin_id]
        self.save()
        return True

    # ---- persistence ---- #

    def save(self) -> None:
        from cryptography.fernet import Fernet

        from xlii.atomicio import write_bytes_atomic

        VAULT_FILE.parent.mkdir(parents=True, exist_ok=True)
        token = Fernet(self._key).encrypt(json.dumps(self._data, sort_keys=True).encode())
        # Same atomic temp+fsync+replace as config/registry — a crash mid-write
        # must not truncate vault.enc (every plugin/FTP/chat secret lives here).
        with _SAVE_LOCK:
            write_bytes_atomic(VAULT_FILE, token, mode=0o600)


# --------------------------------------------------------------------------- #
#  Convenience for bash injection (the hot path — no need to load secrets
#  for every call, only when the command actually references one)
# --------------------------------------------------------------------------- #

def env_for_plugins(
    subscribed_plugins: list,  # list[Plugin] — annotated loosely to dodge cycle
) -> dict[str, str]:
    """Return vault secrets for every ``auth_env_vars`` entry on *plugins*.

    Used by L2 ``plugin_call`` (and face/REPL wrappers) where the HTTP
    template already names ``${VAR}`` in the manifest — there is no shell
    command string to scan. Returns ``{}`` when nothing is declared or the
    vault is unavailable.
    """
    if not subscribed_plugins:
        return {}

    declared: dict[str, str] = {}  # env_var -> plugin_id (first-declarer wins)
    for plugin in subscribed_plugins:
        try:
            auth_vars = plugin.auth_env_vars()
        except Exception:
            continue
        for v in auth_vars:
            if v and v not in declared:
                declared[v] = plugin.id

    if not declared:
        return {}

    try:
        vault = Vault.unlock(create_if_missing=False)
    except VaultError:
        return {}

    out: dict[str, str] = {}
    for var, pid in declared.items():
        secrets = vault.get(pid)
        if var in secrets:
            out[var] = secrets[var]
    return out


def env_for_command(
    cmd: str,
    subscribed_plugins: list,  # list[Plugin] — annotated loosely to dodge cycle
) -> dict[str, str]:
    """Return env-var overrides that should be injected into ``cmd``'s subprocess.

    Scans ``cmd`` for ``${VAR}`` and ``$VAR`` references. For each match, if
    a subscribed plugin declares ``VAR`` in its ``auth_env_vars`` and the
    vault holds a value, emit it. Vault is unlocked lazily — if no referenced
    var resolves to a vault entry, we skip the decrypt entirely.

    Returns ``{}`` when nothing matches (the common case for read-only public
    APIs — agent uses bash without any plugin secrets at all).

    For plugin_call (no shell string), use :func:`env_for_plugins` instead.
    """
    if not subscribed_plugins:
        return {}

    import re as _re
    refs = set(_re.findall(r"\$\{([A-Z_][A-Z0-9_]*)\}|\$([A-Z_][A-Z0-9_]*)", cmd))
    referenced = {a or b for (a, b) in refs}
    if not referenced:
        return {}

    declared: dict[str, str] = {}  # env_var -> plugin_id (first-declarer wins)
    for plugin in subscribed_plugins:
        try:
            auth_vars = plugin.auth_env_vars()
        except Exception:
            # Plugin is broken or missing auth_env_vars — skip it
            continue
        for v in auth_vars:
            if v in referenced and v not in declared:
                declared[v] = plugin.id

    if not declared:
        return {}

    try:
        vault = Vault.unlock(create_if_missing=False)
    except VaultError:
        return {}

    out: dict[str, str] = {}
    for var, pid in declared.items():
        secrets = vault.get(pid)
        if var in secrets:
            out[var] = secrets[var]
    return out


# --------------------------------------------------------------------------- #
#  Admin secret — the elevation key for the capability axis (Vector E)
# --------------------------------------------------------------------------- #
#
# One passphrase, stored as a salted PBKDF2-HMAC-SHA256 hash inside the same
# encrypted vault. We hash it (not just rely on the vault's at-rest Fernet
# encryption) so that even an *unlocked* vault never reveals the passphrase
# itself. It backs two gates that share one secret:
#   • `/admin unlock <secret>` → REPLState.elevated (capability seam #1); and
#   • the keyed XMPP daemon launch — daemon_gate.evaluate_daemon_launch verifies
#     $XLII_DAEMON_KEY against this same secret at the function chokepoint, so a
#     CLI/cron `xlii daemon` can't bypass a REPL-only gate.
#
# Lives in a reserved vault slot (not a real plugin id). All readers fail closed:
# if the vault or its master key is absent there is simply no secret set.

ADMIN_VAULT_ID = "__xlii_admin__"
_PBKDF2_ALGO = "sha256"
_PBKDF2_ITERATIONS = 200_000


def _derive(secret: str, salt: bytes, *, algo: str = _PBKDF2_ALGO,
            iterations: int = _PBKDF2_ITERATIONS) -> bytes:
    import hashlib
    return hashlib.pbkdf2_hmac(algo, secret.encode("utf-8"), salt, iterations)


def set_admin_secret(secret: str) -> None:
    """Set (or replace) the admin secret.

    Provisions a vault master key if none exists yet (via Vault.unlock), so
    first-time setup Just Works. Raises ValueError on an empty secret.
    """
    import secrets as _secrets

    if not secret:
        raise ValueError("admin secret must be non-empty")
    salt = _secrets.token_bytes(16)
    digest = _derive(secret, salt)
    vault = Vault.unlock(create_if_missing=True)
    # One re-encrypt+write for the whole record (set() would write 4×); we're in
    # vault.py so touching _data directly is in-module, not a public reach-in.
    vault._data[ADMIN_VAULT_ID] = {
        "algo": _PBKDF2_ALGO,
        "iterations": str(_PBKDF2_ITERATIONS),
        "salt": salt.hex(),
        "hash": digest.hex(),
    }
    vault.save()


def admin_secret_is_set() -> bool:
    """True if an admin secret is configured. Never raises — a missing vault or
    master key means nothing is set yet, so returns False."""
    try:
        vault = Vault.unlock(create_if_missing=False)
    except VaultError:
        return False
    return vault.has(ADMIN_VAULT_ID, "hash")


def verify_admin_secret(secret: str) -> bool:
    """Constant-time check of `secret` against the stored admin hash.

    Fail-closed: returns False (never raises) when the secret is empty, nothing
    is set, the vault can't be opened, or the stored record is malformed.
    """
    import hmac

    if not secret:
        return False
    try:
        vault = Vault.unlock(create_if_missing=False)
    except VaultError:
        return False
    rec = vault.get(ADMIN_VAULT_ID)
    salt_hex = rec.get("salt")
    expected_hex = rec.get("hash")
    if not salt_hex or not expected_hex:
        return False
    try:
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(expected_hex)
    except ValueError:
        return False
    algo = rec.get("algo") or _PBKDF2_ALGO
    try:
        iterations = int(rec.get("iterations") or _PBKDF2_ITERATIONS)
    except (TypeError, ValueError):
        iterations = _PBKDF2_ITERATIONS
    actual = _derive(secret, salt, algo=algo, iterations=iterations)
    return hmac.compare_digest(actual, expected)


def clear_admin_secret() -> bool:
    """Remove the admin secret entirely. Returns True if one was present."""
    try:
        vault = Vault.unlock(create_if_missing=False)
    except VaultError:
        return False
    return vault.unset(ADMIN_VAULT_ID)


@dataclass
class VaultWipeResult:
    wiped: bool
    residuals: list[str] = field(default_factory=list)


def wipe_vault_verified() -> VaultWipeResult:
    """Delete vault files, keyring master entry, and env key — verify or disclose."""
    residuals: list[str] = []
    for path in (VAULT_FILE, KEY_FILE):
        try:
            if path.exists():
                path.unlink()
        except OSError as exc:
            residuals.append(f"could not delete {path}: {exc}")

    keyring_ok = True
    try:
        import keyring
        from keyring.errors import KeyringError, InitError

        try:
            keyring.delete_password(KEYRING_SERVICE, KEYRING_USERNAME)
        except (KeyringError, InitError):
            keyring_ok = False
        try:
            remaining = keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME)
        except (KeyringError, InitError):
            remaining = "unknown"
            keyring_ok = False
        if remaining is not None:
            keyring_ok = False
            residuals.append(
                "master key may survive in OS keyring "
                f"({KEYRING_SERVICE}/{KEYRING_USERNAME}) — remove manually"
            )
    except ImportError:
        keyring_ok = False
        residuals.append("keyring backend unavailable — vault master may survive")

    os.environ.pop(ENV_VAR, None)
    os.environ.pop(ENV_VAR_LEGACY, None)

    wiped = not residuals and keyring_ok
    if not wiped and not any("master key" in r for r in residuals):
        if VAULT_FILE.exists() or KEY_FILE.exists():
            residuals.append("vault files may remain on disk")
    return VaultWipeResult(wiped=wiped, residuals=residuals)
