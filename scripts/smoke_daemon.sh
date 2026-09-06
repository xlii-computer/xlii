#!/usr/bin/env bash
# Smoke gate for the XMPP fabric's offline half — run in CI WITHOUT the [daemon]
# extra installed. Two invariants the dev pytest run can't guarantee in a no-extra
# environment:
#   (A) the security gate + dispatch routing (xlii.daemon_gate) imports and works
#       with ZERO slixmpp/OMEMO deps — the whole point of extracting it;
#   (B) `xlii daemon` / `xlii notify` degrade to a friendly install hint (exit 1,
#       no traceback) when the extra is absent, instead of an ImportError dump.
set -u

XLII="${XLII_BIN:-xlii}"
WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

fail=0

# --- (A) the gate is dependency-free and routes correctly -------------------
# If xlii.daemon_gate ever grows a slixmpp import, this fails to import here.
out="$(python - "$WORKDIR" 2>&1 <<'PY'
import sys
from pathlib import Path

from xlii.daemon_gate import (
    DaemonConfig, RateLimiter, classify_dispatch, list_verbs,
)
from xlii.atomicio import write_text_atomic

tmp = Path(sys.argv[1])

# routing: kill / verb / agent / unknown, plus the [alias] prefix override
verbs = tmp / "verbs"; verbs.mkdir()
sh = verbs / "status.sh"; sh.write_text("#!/bin/sh\necho ok\n"); sh.chmod(0o755)

assert classify_dispatch("kill", verbs_dir=verbs, fallback_enabled=True).kind == "kill"
assert classify_dispatch("status", verbs_dir=verbs, fallback_enabled=True).kind == "verb"
d = classify_dispatch("[proj] do a thing", verbs_dir=verbs, fallback_enabled=True)
assert d.kind == "agent" and d.workspace == "proj" and d.prompt == "do a thing", d
u = classify_dispatch("nope", verbs_dir=verbs, fallback_enabled=False)
assert u.kind == "unknown" and "status" in u.reply, u
# the kill switch can't ride behind a workspace prefix
assert classify_dispatch("[proj] kill", verbs_dir=verbs, fallback_enabled=True).kind == "agent"
assert "status" in list_verbs(verbs)

# config round-trips with secure defaults
cfg_path = tmp / "daemon.toml"
cfg_path.write_text(
    '[daemon]\njid = "daemon@desktop.tailnet"\n'
    '[whitelist]\nallowed_jids = ["me@phone.tailnet"]\n'
)
cfg = DaemonConfig.load(cfg_path)
assert cfg.blind_trust is False and cfg.allowed_jids == ["me@phone.tailnet"]

# rate limiter denies over the cap
rl = RateLimiter(max_per_minute=1, lockout_threshold=9, lockout_duration_s=60)
assert rl.check("a@x")[0] and not rl.check("a@x")[0]

# atomic state write leaves a readable file with own-only perms
state = tmp / "omemo-state.json"
write_text_atomic(state, '{"k": 1}')
assert state.read_text() == '{"k": 1}'
assert (state.stat().st_mode & 0o777) == 0o600

print("gate-ok")
PY
)"
code=$?
if grep -q "Traceback" <<<"$out" || [ "$code" != 0 ] || ! grep -q "gate-ok" <<<"$out"; then
  echo "FAIL [gate: dependency-free routing/config/atomic]"
  echo "$out" | tail -30
  fail=1
else
  echo "ok   [gate: dependency-free routing/config/atomic]"
fi

# --- (B) daemon/notify degrade with a friendly hint, not a traceback --------
check_hint() {
  local label="$1"; shift
  local out code
  out="$("$XLII" "$@" 2>&1)"; code=$?
  if grep -q "Traceback" <<<"$out"; then
    echo "FAIL [$label]: traceback instead of friendly hint"; echo "$out" | tail -20; fail=1
  elif [ "$code" != 1 ]; then
    echo "FAIL [$label]: exit $code (wanted 1)"; echo "$out" | tail -20; fail=1
  elif ! grep -q 'pip install "xlii\[daemon\]"' <<<"$out"; then
    echo "FAIL [$label]: missing install hint"; echo "$out" | tail -20; fail=1
  else
    echo "ok   [$label]"
  fi
}

check_hint "cli: xlii daemon (extra absent)" daemon
check_hint "cli: xlii notify (extra absent)" notify "smoke message"

exit $fail
