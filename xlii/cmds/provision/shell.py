"""Config template and shell integration (`xlii config`, `xlii shell-init`)."""

from __future__ import annotations

import os

from xlii.config import GlobalConfig
from xlii.ui import console


def cmd_config(args) -> int:
    path = GlobalConfig.write_template()
    console.print(f"[green]✓[/green] config at {path}")
    console.print("[dim]edit it: paste your management_api_key + add api keys to the keys[] list[/dim]")
    return 0


# Shell wrappers printed by `xlii shell-init`. They run `command xlii` (bypassing
# the function to avoid recursion), pass a temp file via XLII_CWD_FILE, and `cd`
# the *parent* shell to whatever the REPL wrote there on exit. A child can't move
# its parent's cwd directly — this is the standard wrapper trick (cf. zoxide).
_BASH_ZSH_WRAPPER = r'''# xlii shell integration: `cd` inside `xlii code` follows you out on exit.
# Install:  eval "$(xlii shell-init)"   (add to ~/.bashrc or ~/.zshrc)
xlii() {
  local _xlii_f _xlii_rc _xlii_dest
  _xlii_f="$(mktemp -t xlii-cwd.XXXXXX 2>/dev/null)" || _xlii_f=""
  XLII_CWD_FILE="$_xlii_f" command xlii "$@"
  _xlii_rc=$?
  if [ -n "$_xlii_f" ] && [ -s "$_xlii_f" ]; then
    _xlii_dest="$(cat "$_xlii_f")"
    [ -d "$_xlii_dest" ] && cd "$_xlii_dest"
  fi
  [ -n "$_xlii_f" ] && rm -f "$_xlii_f"
  return $_xlii_rc
}'''

_FISH_WRAPPER = r'''# xlii shell integration: `cd` inside `xlii code` follows you out on exit.
# Install:  xlii shell-init | source   (add to ~/.config/fish/config.fish)
function xlii
    set -l _xlii_f (mktemp -t xlii-cwd.XXXXXX 2>/dev/null)
    XLII_CWD_FILE=$_xlii_f command xlii $argv
    set -l _xlii_rc $status
    if test -n "$_xlii_f"; and test -s "$_xlii_f"
        set -l _xlii_dest (cat $_xlii_f)
        test -d "$_xlii_dest"; and cd "$_xlii_dest"
    end
    test -n "$_xlii_f"; and rm -f $_xlii_f
    return $_xlii_rc
end'''


def cmd_shell_init(args) -> int:
    """Print a shell function wrapper that makes `cd` inside `xlii code` persist
    to the parent shell on exit. Opt-in: nothing happens until you install it."""
    shell = getattr(args, "shell", None)
    if shell is None:
        shell = os.path.basename(os.environ.get("SHELL", ""))
    print(_FISH_WRAPPER if "fish" in (shell or "") else _BASH_ZSH_WRAPPER)
    return 0
