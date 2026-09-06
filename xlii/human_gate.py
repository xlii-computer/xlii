"""Foreground-human gate — destroy and other desk-only flows must not run from
tasks, capturing consoles, or forged elevation flags."""

from __future__ import annotations

from typing import Any


class HumanGateError(Exception):
    """Refused: not a genuine foreground human."""


def is_foreground_console(console: Any) -> bool:
    """True when the console can genuinely prompt a foreground human."""
    if console is None:
        return False
    if getattr(console, "xlii_foreground", None) is False:
        return False
    if getattr(console, "record", False) and not callable(
        getattr(console, "request_input", None)
    ):
        return False
    if callable(getattr(console, "request_input", None)):
        return True
    return getattr(console, "xlii_foreground", None) is not False


def require_human(context: dict[str, Any]) -> None:
    """Raise HumanGateError unless foreground. No context flag is proof."""
    if not is_foreground_console(context.get("console")):
        raise HumanGateError("not a foreground human console")


def confirm_typed_phrase(
    console: Any, phrase: str, *, prompt: str | None = None
) -> bool:
    """True only when the user types *phrase* exactly (after strip)."""
    from xlii.console_prompt import request_line

    ask = prompt if prompt is not None else f"Type {phrase!r} to confirm"
    answer = request_line(console, ask, secret=False)
    if answer is None:
        return False
    return answer.strip() == phrase


def verify_fresh_admin(console: Any) -> bool:
    """Fresh masked admin secret — never reads session elevation."""
    from xlii.console_prompt import request_line
    from xlii import vault

    if not vault.admin_secret_is_set():
        return False
    secret = request_line(console, "Admin secret", secret=True)
    if secret is None:
        return False
    return vault.verify_admin_secret(secret)
