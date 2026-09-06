#!/usr/bin/env bash
# Smoke gate: both REPLs must survive start → slash commands → exit with no
# traceback, fully offline, with a fake key. This is the ratchet that makes
# the Phase-0 class of regression (NameError in a handler killing the
# session) impossible to land.
set -u

XLII="${XLII_BIN:-xlii}"
WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

export HOME="$WORKDIR/home"
export XDG_CONFIG_HOME="$HOME/.config"
export XLII_CONFIG_DIR="$XDG_CONFIG_HOME/xlii"
export XAI_API_KEY="fake-key-for-smoke"
mkdir -p "$XLII_CONFIG_DIR"
cat > "$XLII_CONFIG_DIR/config.json" <<'EOF'
{"keys": ["fake-key-for-smoke"]}
EOF

fail=0

check_no_traceback() {
  local label="$1" output="$2" code="$3" want_code="$4"
  if grep -q "Traceback" <<<"$output"; then
    echo "FAIL [$label]: traceback in output"
    echo "$output" | tail -30
    fail=1
  elif [ "$code" != "$want_code" ]; then
    echo "FAIL [$label]: exit code $code (wanted $want_code)"
    echo "$output" | tail -30
    fail=1
  else
    echo "ok   [$label]"
  fi
}

# --- code REPL on a local-only project (no network needed at all) ---------
proj="$WORKDIR/proj"
mkdir -p "$proj/.xlii"
cat > "$proj/.xlii/project.json" <<EOF
{"name": "smoke", "root": "$proj", "collection_id": "fake", "created_at": "2026-01-01",
 "conversation_id": "deadbeef", "local_only": true}
EOF
echo "print('hello')" > "$proj/main.py"

out="$(printf '/help\n/status\n/ref\n/doc\n/attachments\n/plan\n/cancel\n/exit\n' \
  | "$XLII" code "$proj" 2>&1)"
check_no_traceback "code: slash commands + /exit" "$out" "$?" 0

out="$(printf '/help\n' | "$XLII" code "$proj" 2>&1)"   # EOF (Ctrl-D) exit path
check_no_traceback "code: EOF exit" "$out" "$?" 0

# --- chat without credentials must degrade with a message, not a traceback -
out="$(env -u XAI_API_KEY printf '' | env -u XAI_API_KEY "$XLII" chat smokepersona 2>&1)"
code=$?
if grep -q "Traceback" <<<"$out"; then
  echo "FAIL [chat: no credentials]: traceback in output"
  echo "$out" | tail -30
  fail=1
else
  echo "ok   [chat: no credentials degrades cleanly]"
fi

# --- CLI surface stays bootable ---------------------------------------------
out="$("$XLII" help 2>&1)"; check_no_traceback "cli: help" "$out" "$?" 0
out="$("$XLII" projects 2>&1)"; check_no_traceback "cli: projects" "$out" "$?" 0

exit $fail
